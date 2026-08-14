#!/usr/bin/env python3
"""account.py — see which local accounts exist, what each owns, and reset a password.

The app requires an invitation to register, so there is no self-serve way back in
if you forget a local password. This is that way back. It only ever touches the
SQLite file on this machine — it cannot reach a hosted instance.

    python3 account.py                     list accounts and what they own
    python3 account.py --reset a@b.com     set a new password (prompts)
    python3 account.py --reset a@b.com --password 'sixteen-plus-chars'
    python3 account.py --use a@b.com       reset it AND point .env at it

Run it from the repo root.
"""
import argparse
import getpass
import hashlib
import os
import sqlite3
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "api", "lessonlens.db")
MIN_PASSWORD = 16
# reset() prompts when no password is passed; use() needs the value it settled on.
RESOLVED = {}
SALT_ALPHABET = string.ascii_letters + string.digits


def hash_password(password):
    """Produce a hash the app will accept, using only the standard library.

    Werkzeug's default is scrypt, but it verifies pbkdf2:sha256 just as happily,
    and pbkdf2 is available in hashlib. Generating it here means this script has
    no dependencies at all — which matters because the alternative was requiring
    the project virtualenv, and every attempt to locate that reliably (system
    python, .venv/bin/python, re-exec) produced a new way to fail *after* the
    password prompt. A recovery tool must not have a dependency problem.

    Format, matching werkzeug exactly:
        pbkdf2:sha256:<iterations>$<salt>$<hex digest>
    """
    iterations = 600_000
    salt = "".join(SALT_ALPHABET[b % len(SALT_ALPHABET)] for b in os.urandom(16))
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return f"pbkdf2:sha256:{iterations}${salt}${digest.hex()}"


def connect():
    if not os.path.exists(DB):
        sys.exit(f"no database at {DB}\nStart the app once with ./start-local.sh")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def counts_for(conn, user_id):
    def one(sql):
        try:
            return conn.execute(sql, (user_id,)).fetchone()[0]
        except sqlite3.Error:
            return "?"
    return {
        "sessions": one("SELECT COUNT(*) FROM sessions WHERE user_id = ?"),
        "summaries": one("SELECT COUNT(*) FROM lesson_summaries WHERE user_id = ?"),
        "images": one("SELECT COUNT(*) FROM attachments WHERE user_id = ?"),
    }


def list_accounts(conn):
    rows = conn.execute(
        "SELECT id, email, display_name, is_admin, status FROM users ORDER BY id"
    ).fetchall()
    if not rows:
        print("No accounts in this database.")
        return
    print(f"{len(rows)} account(s) in {DB}\n")
    for r in rows:
        c = counts_for(conn, r["id"])
        flags = []
        if r["is_admin"]:
            flags.append("admin")
        if (r["status"] or "") != "active":
            flags.append(f"status={r['status']}")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  id={r['id']}  {r['email']}{suffix}")
        print(f"      {c['sessions']} session(s), {c['summaries']} summary(ies), {c['images']} image(s)")
    print("\nData is scoped per account: logging in as one shows nothing owned by another.")


def reset(conn, email, password, quiet_env_hint=False):
    row = conn.execute("SELECT id, status FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        emails = [r["email"] for r in conn.execute("SELECT email FROM users")]
        sys.exit(f"no account '{email}'. Existing: {', '.join(emails) or '(none)'}")

    if not password:
        password = getpass.getpass(f"New password for {email} ({MIN_PASSWORD}+ chars): ")
    if len(password) < MIN_PASSWORD:
        sys.exit(f"password must be at least {MIN_PASSWORD} characters (the app enforces this)")

    # Reactivate too: a suspended account rejects a correct password, which is
    # indistinguishable from a wrong one at the login screen.
    conn.execute(
        "UPDATE users SET password_hash = ?, status = 'active' WHERE id = ?",
        (hash_password(password), row["id"]),
    )
    conn.commit()
    print(f"  password reset for {email}" + ("  (account re-activated)" if row["status"] != "active" else ""))
    RESOLVED["password"] = password
    print(f"  log in at your local app with {email} and the new password")
    if not quiet_env_hint:
        print("\n  To make the updater and MCP server use this account too:")
        print(f"    python3 account.py --use {email}")


ENV = os.path.join(HERE, ".env")


def rewrite_env(email, password):
    """Point .env at this account, preserving everything else in the file.

    The updater and the MCP server authenticate as whoever .env names. Leaving
    that pointed at a different account than the one holding your lessons means
    the next sync quietly lands in an empty account — the data is not lost, but
    it is split in two, which is worse than either half.
    """
    lines = []
    if os.path.exists(ENV):
        with open(ENV, encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    wanted = {"LESSONLENS_EMAIL": email, "LESSONLENS_PASSWORD": password}
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if key in wanted:
            out.append(f"{key}={wanted[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in seen:
            out.append(f"{key}={value}")

    with open(ENV, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(ENV, 0o600)
    print(f"  .env now points at {email}")


def use(conn, email, password):
    """Make this the account everything uses: reset its password, update .env."""
    reset(conn, email, password, quiet_env_hint=True)
    password = RESOLVED["password"]
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    counts = counts_for(conn, row["id"])
    rewrite_env(email, password)
    print(f"  it holds {counts['sessions']} session(s), {counts['summaries']} summary(ies)")
    # No restart: the server authenticates against the database, so the new
    # password works on the next login attempt. .env only tells the updater and
    # the MCP server who to log in as, and they read it per run. Saying
    # "restart" here sent people round a loop looking for a step that isn't one.


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", metavar="EMAIL", help="set a new password for this account")
    ap.add_argument("--use", metavar="EMAIL",
                    help="set its password AND point .env at it (the updater and MCP server follow)")
    ap.add_argument("--password", help="new password (omit to be prompted)")
    args = ap.parse_args()

    conn = connect()
    try:
        if args.use:
            use(conn, args.use, args.password)
        elif args.reset:
            reset(conn, args.reset, args.password)
        else:
            list_accounts(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""account.py — see which local accounts exist, what each owns, and reset a password.

The app requires an invitation to register, so there is no self-serve way back in
if you forget a local password. This is that way back. It only ever touches the
SQLite file on this machine — it cannot reach a hosted instance.

    python3 account.py                     list accounts and what they own
    python3 account.py --reset a@b.com     set a new password (prompts)
    python3 account.py --reset a@b.com --password 'sixteen-plus-chars'

Run it from the repo root.
"""
import argparse
import getpass
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api", "lessonlens.db")
MIN_PASSWORD = 16


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


def reset(conn, email, password):
    row = conn.execute("SELECT id, status FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        emails = [r["email"] for r in conn.execute("SELECT email FROM users")]
        sys.exit(f"no account '{email}'. Existing: {', '.join(emails) or '(none)'}")

    if not password:
        password = getpass.getpass(f"New password for {email} ({MIN_PASSWORD}+ chars): ")
    if len(password) < MIN_PASSWORD:
        sys.exit(f"password must be at least {MIN_PASSWORD} characters (the app enforces this)")

    sys.path.insert(0, os.path.join(os.path.dirname(DB)))
    from werkzeug.security import generate_password_hash

    # Reactivate too: a suspended account rejects a correct password, which is
    # indistinguishable from a wrong one at the login screen.
    conn.execute(
        "UPDATE users SET password_hash = ?, status = 'active' WHERE id = ?",
        (generate_password_hash(password), row["id"]),
    )
    conn.commit()
    print(f"  password reset for {email}" + ("  (account re-activated)" if row["status"] != "active" else ""))
    print(f"  log in at your local app with {email} and the new password")
    print("\n  If you use this account for the updater, put it in .env too:")
    print(f"    LESSONLENS_EMAIL={email}")
    print( "    LESSONLENS_PASSWORD=<the password you just set>")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", metavar="EMAIL", help="set a new password for this account")
    ap.add_argument("--password", help="new password (omit to be prompted)")
    args = ap.parse_args()

    conn = connect()
    try:
        if args.reset:
            reset(conn, args.reset, args.password)
        else:
            list_accounts(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

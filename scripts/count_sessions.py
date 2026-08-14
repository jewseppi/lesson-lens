#!/usr/bin/env python3
"""Print how many sessions the configured account owns.

Used by update-now.sh to report a before/after count, so a sync that added
nothing is visibly distinct from one that added lessons. Standard library only,
and it reads the database directly rather than the API so it works whether or
not the server happens to be up.
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "api", "lessonlens.db")


def env_email():
    path = os.path.join(ROOT, ".env")
    if os.environ.get("LESSONLENS_EMAIL"):
        return os.environ["LESSONLENS_EMAIL"]
    if not os.path.isfile(path):
        return ""
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("LESSONLENS_EMAIL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main():
    if not os.path.exists(DB):
        print("0")
        return
    email = env_email()
    conn = sqlite3.connect(DB)
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            print("0")
            return
        print(conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (row[0],)
        ).fetchone()[0])
    except sqlite3.Error:
        print("?")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Import a LessonLens backup archive into the running local app.

Used by update-now.sh so that dropping a .zip in Downloads is all it takes to
get summaries in — no menu, no Settings page, no decisions.

    python3 scripts/import_backup.py                  find the newest archive
    python3 scripts/import_backup.py path/to/file.zip import this one
    python3 scripts/import_backup.py --check          report, import nothing

Merge semantics: sessions and summaries that already exist are left alone, so
running this twice imports nothing the second time. The server takes a restore
point before it writes either way.

Standard library only, matching the other helpers in this directory.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Where a downloaded archive plausibly lands. Same spirit as the export-file
# search in line_mac_sync.py — look where a browser or AirDrop would put it.
SEARCH_DIRS = ["~/Downloads", "~/Desktop", "~/Documents"]
# Archives this tool produced. Anything else in Downloads is not ours to open.
NAME_PREFIXES = ("lessonlens", "lesson-lens")


def env(key, default=""):
    """Read a setting from the environment, falling back to the repo .env."""
    if os.environ.get(key):
        return os.environ[key]
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def find_archive():
    """Newest LessonLens .zip across the search directories, or None."""
    found = []
    for raw in SEARCH_DIRS:
        d = Path(raw).expanduser()
        if not d.is_dir():
            continue
        for p in d.glob("*.zip"):
            if p.name.lower().startswith(NAME_PREFIXES):
                try:
                    found.append((p.stat().st_mtime, p))
                except OSError:
                    continue
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]


def login(base, email, password):
    req = urllib.request.Request(
        base + "/api/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def post_archive(base, token, path):
    """POST the zip as multipart/form-data, built by hand to stay stdlib-only."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        base + "/api/backup/import", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", nargs="?", help="path to a backup .zip (default: newest found)")
    ap.add_argument("--check", action="store_true", help="report what would be imported, change nothing")
    args = ap.parse_args()

    path = Path(args.archive).expanduser() if args.archive else find_archive()
    if not path:
        print("  no LessonLens archive found in " + ", ".join(SEARCH_DIRS))
        return 0            # Nothing to do is not a failure.
    if not path.is_file():
        print(f"  no such file: {path}")
        return 1

    print(f"  archive: {path}")
    if args.check:
        return 0

    base = (env("LESSONLENS_LOCAL_URL") or env("LESSONLENS_API_URL")
            or "http://127.0.0.1:5001").rstrip("/")
    email, password = env("LESSONLENS_EMAIL"), env("LESSONLENS_PASSWORD")
    if not email or not password:
        print("  no credentials in .env — run ./start-local.sh first")
        return 1

    try:
        token = login(base, email, password)
    except urllib.error.HTTPError as exc:
        print(f"  could not log in as {email} ({exc.code}) — try: python3 account.py")
        return 1
    except OSError as exc:
        print(f"  the app is not answering at {base} ({exc}) — run ./start-local.sh")
        return 1

    try:
        result = post_archive(base, token, path)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        print(f"  import failed ({exc.code}): {detail}")
        return 1
    except OSError as exc:
        print(f"  import failed: {exc}")
        return 1

    added_sessions = result.get("session_count", 0)
    added_summaries = result.get("summary_count", 0)
    skipped = result.get("skipped_summary_count", 0)
    print(f"  imported {added_summaries} summary(ies) and {added_sessions} session(s)"
          + (f", skipped {skipped} already present" if skipped else ""))
    if not added_summaries and skipped:
        print("  (everything in this archive was already in your app)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

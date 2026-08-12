"""restore_points.py — automatic pre-sync snapshots with rollback.

Every operation that rewrites a user's parsed data (chat sync, backup import,
re-parse) takes a **restore point** first: a full backup archive captured
immediately before the change, kept for a retention window, and restorable from
the Settings page with one click.

The point is blast-radius control. Sync and import are the operations that can
lose data — ``/api/backup/import`` with ``replace_existing`` deletes the user's
learning data outright — so if a bug is introduced in that path, the previous
state is already on disk before the mutation runs, and rolling back is a button
rather than an archaeology project.

Design notes:

* Snapshots are ordinary backup archives (``lessonlens-backup.v2``), so a restore
  point can be downloaded and imported by hand, and rollback reuses the normal
  import path instead of a parallel restore implementation.
* Capture is **best effort and never blocks the operation it protects**. A brand
  new account has nothing to snapshot; a snapshot failure must not turn a
  working sync into a failed one.
* Expiry is enforced on write (each capture prunes) rather than by a scheduler,
  so it works the same on a hosted box and a laptop.

Stdlib only, so these helpers are unit-testable without the Flask stack.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_RETENTION_DAYS = 7
# Age alone doesn't bound disk use: snapshots include images, and a busy day can
# produce many. Keep at most this many per user, newest first.
DEFAULT_MAX_POINTS = 20

# Reasons are stored verbatim and shown in the UI; keep them short and specific.
REASON_SYNC = "pre-sync"
REASON_IMPORT = "pre-backup-import"
REASON_REPARSE = "pre-reparse"
REASON_ROLLBACK = "pre-rollback"
REASON_DELETE_SESSION = "pre-session-delete"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS restore_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    session_count INTEGER DEFAULT 0,
    summary_count INTEGER DEFAULT 0,
    attachment_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


def retention_days() -> int:
    """Retention window in days (override with LESSONLENS_RESTORE_RETENTION_DAYS)."""
    raw = os.environ.get("LESSONLENS_RESTORE_RETENTION_DAYS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return value if value > 0 else DEFAULT_RETENTION_DAYS


def max_points() -> int:
    """Maximum snapshots kept per user (LESSONLENS_RESTORE_MAX_POINTS)."""
    raw = os.environ.get("LESSONLENS_RESTORE_MAX_POINTS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_POINTS
    return value if value > 0 else DEFAULT_MAX_POINTS


def ensure_table(conn) -> None:
    conn.execute(CREATE_TABLE_SQL)


def enforce_max_points(conn, user_id: int, directory: str, limit: int | None = None) -> int:
    """Drop the oldest snapshots beyond the per-user cap. Returns how many went."""
    ensure_table(conn)
    cap = limit if limit is not None else max_points()
    rows = conn.execute(
        "SELECT id, filename FROM restore_points WHERE user_id = ?"
        " ORDER BY datetime(created_at) DESC, id DESC",
        (user_id,),
    ).fetchall()
    removed = 0
    for row in rows[cap:]:
        _unlink(directory, row["filename"])
        conn.execute("DELETE FROM restore_points WHERE id = ?", (row["id"],))
        removed += 1
    return removed


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def safe_filename(name: str) -> str:
    """Reduce a filename to a safe basename (no directories, no traversal)."""
    base = os.path.basename((name or "").replace("\\", "/"))
    cleaned = _SAFE_NAME.sub("_", base).lstrip(".")
    return cleaned or "restore-point.zip"


def create_restore_point(
    conn,
    user_id: int,
    reason: str,
    archive_bytes: bytes,
    directory: str,
    *,
    manifest: dict | None = None,
    now: datetime | None = None,
    days: int | None = None,
) -> dict:
    """Write a snapshot to disk and record it. Returns the created row as a dict."""
    ensure_table(conn)
    created = _now(now)
    window = days if days is not None else retention_days()
    expires = created + timedelta(days=window)

    os.makedirs(directory, exist_ok=True)
    stamp = created.strftime("%Y%m%d-%H%M%S")
    filename = safe_filename(f"{stamp}-u{user_id}-{reason}.zip")
    # Never clobber an existing snapshot (two captures inside one second).
    candidate, counter = filename, 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = safe_filename(f"{stamp}-u{user_id}-{reason}-{counter}.zip")
        counter += 1
    filename = candidate

    with open(os.path.join(directory, filename), "wb") as fh:
        fh.write(archive_bytes)

    manifest = manifest or {}
    cursor = conn.execute(
        """INSERT INTO restore_points
           (user_id, reason, filename, size_bytes, session_count, summary_count,
            attachment_count, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            reason,
            filename,
            len(archive_bytes),
            manifest.get("session_count") or 0,
            manifest.get("summary_count") or 0,
            manifest.get("attachment_count") or 0,
            _iso(created),
            _iso(expires),
        ),
    )
    return {
        "id": cursor.lastrowid,
        "reason": reason,
        "filename": filename,
        "size_bytes": len(archive_bytes),
        "session_count": manifest.get("session_count") or 0,
        "summary_count": manifest.get("summary_count") or 0,
        "attachment_count": manifest.get("attachment_count") or 0,
        "created_at": _iso(created),
        "expires_at": _iso(expires),
    }


def row_to_dict(row, now: datetime | None = None) -> dict:
    current = _now(now)
    expires = _parse_iso(row["expires_at"])
    remaining = None
    if expires:
        remaining = max(0, int((expires - current).total_seconds() // 86400))
    return {
        "id": row["id"],
        "reason": row["reason"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "session_count": row["session_count"],
        "summary_count": row["summary_count"],
        "attachment_count": row["attachment_count"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "expires_in_days": remaining,
        "expired": bool(expires and expires <= current),
    }


def list_restore_points(conn, user_id: int, now: datetime | None = None) -> list[dict]:
    ensure_table(conn)
    rows = conn.execute(
        "SELECT * FROM restore_points WHERE user_id = ? ORDER BY datetime(created_at) DESC, id DESC",
        (user_id,),
    ).fetchall()
    return [row_to_dict(row, now) for row in rows]


def get_restore_point(conn, user_id: int, restore_point_id: int):
    ensure_table(conn)
    return conn.execute(
        "SELECT * FROM restore_points WHERE id = ? AND user_id = ?",
        (restore_point_id, user_id),
    ).fetchone()


def read_restore_point_bytes(row, directory: str) -> bytes:
    path = Path(directory) / safe_filename(row["filename"])
    return path.read_bytes()


def delete_restore_point(conn, user_id: int, restore_point_id: int, directory: str) -> bool:
    row = get_restore_point(conn, user_id, restore_point_id)
    if not row:
        return False
    _unlink(directory, row["filename"])
    conn.execute(
        "DELETE FROM restore_points WHERE id = ? AND user_id = ?",
        (restore_point_id, user_id),
    )
    return True


def _unlink(directory: str, filename: str) -> None:
    try:
        os.remove(os.path.join(directory, safe_filename(filename)))
    except OSError:
        # Already gone, or unreadable — the row still gets removed.
        pass


def prune_restore_points(conn, directory: str, now: datetime | None = None) -> int:
    """Delete expired snapshots (rows and files). Returns how many were removed."""
    ensure_table(conn)
    current = _now(now)
    rows = conn.execute("SELECT * FROM restore_points").fetchall()
    removed = 0
    for row in rows:
        expires = _parse_iso(row["expires_at"])
        if expires and expires <= current:
            _unlink(directory, row["filename"])
            conn.execute("DELETE FROM restore_points WHERE id = ?", (row["id"],))
            removed += 1
    return removed

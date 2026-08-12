"""backup_helpers.py — pure helpers for carrying image attachments in backups.

Split out of ``app.py`` so the attachment export/import logic can be unit tested
against a plain SQLite connection, without importing Flask and the provider SDKs.

Background: backup archives originally carried the chat, parse artifacts, and
summaries — but not attachments. That meant ``/api/backup/sync-remote`` (the
Settings "sync to remote" button) silently dropped every lesson image. These
helpers add attachments to the archive and restore them on the far side.

The awkward part is identity. ``attachments.id`` is AUTOINCREMENT, so ids from
the source instance mean nothing on the destination; and
``session_attachments.session_id`` stores the *integer* ``sessions.id`` (that is
what the upload path and the per-session endpoint both write and read), which is
equally non-portable. So the wire format keys attachments by **sha256** and
sessions by their **session-id string**, and the import remaps both to local ids.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

BACKUP_SCHEMA_VERSION = "lessonlens-backup.v2"
SUPPORTED_BACKUP_SCHEMAS = ("lessonlens-backup.v1", BACKUP_SCHEMA_VERSION)


def normalize_member(name: str) -> str:
    """Normalize a zip member path, rejecting traversal attempts."""
    normalized = name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        raise ValueError("Backup contains an invalid file path")
    return normalized


def load_backup_attachments(conn, user_id):
    """Return (attachment_rows, link_rows) for a user's session-assigned images.

    Loose uploads that were never matched to a session are intentionally not
    exported — they carry no lesson context.
    """
    attachment_rows = conn.execute(
        """SELECT DISTINCT a.* FROM attachments a
           JOIN session_attachments sa ON sa.attachment_id = a.id
           WHERE a.user_id = ?
           ORDER BY a.id ASC""",
        (user_id,),
    ).fetchall()

    # session_attachments.session_id holds the integer sessions.id, but the
    # column is declared TEXT and an older listing query compares it to the
    # session string. Resolve through both so the export always carries a
    # portable session-id string.
    link_rows = conn.execute(
        """SELECT sa.*,
                  a.sha256 AS attachment_sha256,
                  COALESCE(s_int.session_id, s_str.session_id) AS session_key
           FROM session_attachments sa
           JOIN attachments a ON a.id = sa.attachment_id
           LEFT JOIN sessions s_int
                  ON s_int.id = sa.session_id AND s_int.user_id = sa.user_id
           LEFT JOIN sessions s_str
                  ON s_str.session_id = sa.session_id AND s_str.user_id = sa.user_id
           WHERE sa.user_id = ?
           ORDER BY sa.id ASC""",
        (user_id,),
    ).fetchall()
    return attachment_rows, link_rows


def attachment_manifest_entries(attachment_rows, link_rows) -> dict:
    """Build the attachment-related portion of a backup manifest."""
    attachment_rows = attachment_rows or []
    link_rows = link_rows or []
    return {
        "attachment_count": len(attachment_rows),
        "attachments": [
            {
                "sha256": row["sha256"],
                "stored_filename": row["stored_filename"],
                "original_filename": row["original_filename"],
                "mime_type": row["mime_type"],
                "captured_at_utc": row["captured_at_utc"],
                "captured_at_local": row["captured_at_local"],
                "timezone_hint": row["timezone_hint"],
                "metadata_json": row["metadata_json"],
            }
            for row in attachment_rows
        ],
        "session_attachments": [
            {
                # Portable session-id string (e.g. "2026-03-05"), never a row id.
                "session_id": row["session_key"],
                "attachment_sha256": row["attachment_sha256"],
                "match_confidence": row["match_confidence"],
                "match_reason": row["match_reason"],
                "assigned_by": row["assigned_by"],
            }
            for row in link_rows
            if row["session_key"]
        ],
    }


def attachment_archive_members(attachment_rows, attachments_folder):
    """Yield (member_name, blob_bytes) for each attachment blob that exists.

    Blobs are named by sha256 so the import side can match them to manifest rows
    regardless of the source instance's stored filenames.
    """
    for row in attachment_rows or []:
        blob_path = Path(attachments_folder) / row["stored_filename"]
        if blob_path.is_file():
            _, ext = os.path.splitext(row["stored_filename"])
            yield f"attachments/{row['sha256']}{ext.lower()}", blob_path.read_bytes()


def restore_backup_attachments(conn, archive, manifest, user_id, attachments_folder):
    """Restore attachments from a v2 backup archive.

    Returns ``(attachments_created, links_created)``. Safe to run twice:
    attachments dedupe on ``(user_id, sha256)`` and links rely on the table's
    ``UNIQUE(session_id, attachment_id)`` constraint. A v1 archive carries no
    ``attachments`` key and is a no-op.
    """
    attachment_specs = manifest.get("attachments") or []
    link_specs = manifest.get("session_attachments") or []
    if not attachment_specs:
        return 0, 0

    blob_members = {}
    for name in archive.namelist():
        normalized = normalize_member(name)
        if normalized.startswith("attachments/") and not normalized.endswith("/"):
            blob_members[Path(normalized).stem] = name

    os.makedirs(attachments_folder, exist_ok=True)
    sha_to_id: dict[str, int] = {}
    created = 0

    for spec in attachment_specs:
        sha = spec.get("sha256")
        if not sha:
            continue

        existing = conn.execute(
            "SELECT id FROM attachments WHERE user_id = ? AND sha256 = ?",
            (user_id, sha),
        ).fetchone()
        if existing:
            sha_to_id[sha] = existing["id"]
            continue

        member = blob_members.get(sha)
        if not member:
            # Manifest row with no blob — skip rather than create a broken record.
            continue

        _, ext = os.path.splitext(spec.get("stored_filename") or "")
        stored_name = f"{uuid.uuid4()}{ext.lower()}"
        with open(os.path.join(attachments_folder, stored_name), "wb") as fh:
            fh.write(archive.read(member))

        cursor = conn.execute(
            """INSERT INTO attachments
               (user_id, stored_filename, original_filename, mime_type, sha256,
                captured_at_utc, captured_at_local, timezone_hint, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                stored_name,
                spec.get("original_filename") or stored_name,
                spec.get("mime_type") or "application/octet-stream",
                sha,
                spec.get("captured_at_utc"),
                spec.get("captured_at_local"),
                spec.get("timezone_hint"),
                spec.get("metadata_json") or "{}",
            ),
        )
        sha_to_id[sha] = cursor.lastrowid
        created += 1

    linked = 0
    for spec in link_specs:
        attachment_id = sha_to_id.get(spec.get("attachment_sha256"))
        session_id_str = spec.get("session_id")
        if not attachment_id or not session_id_str:
            continue

        session_row = conn.execute(
            "SELECT s.id FROM sessions s"
            " JOIN parse_runs pr ON s.run_id = pr.run_id"
            " WHERE pr.user_id = ? AND s.session_id = ?"
            " ORDER BY s.id DESC LIMIT 1",
            (user_id, session_id_str),
        ).fetchone()
        if not session_row:
            continue

        cursor = conn.execute(
            """INSERT OR IGNORE INTO session_attachments
               (user_id, session_id, attachment_id, match_confidence, match_reason, assigned_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                session_row["id"],
                attachment_id,
                spec.get("match_confidence") or "unmatched",
                spec.get("match_reason"),
                spec.get("assigned_by") or "imported",
            ),
        )
        linked += cursor.rowcount or 0

    return created, linked

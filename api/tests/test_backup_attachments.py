"""Round-trip tests for image attachments in backup archives.

Regression cover for a real gap: backups carried chat + summaries but not
images, so ``/api/backup/sync-remote`` (the Settings "sync to remote" button)
silently dropped every lesson photo.

These drive the pure helpers in ``api/backup_helpers.py`` against a throwaway
SQLite database, so they run without the Flask stack installed.
"""
import io
import json
import os
import sqlite3
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_DIR = os.path.join(ROOT, "api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

import backup_helpers as bh  # noqa: E402

SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, display_name TEXT);
CREATE TABLE parse_runs (run_id TEXT PRIMARY KEY, user_id INTEGER);
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, user_id INTEGER, session_id TEXT, date TEXT
);
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    upload_id INTEGER,
    stored_filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    captured_at_utc TEXT,
    captured_at_local TEXT,
    timezone_hint TEXT,
    metadata_json TEXT DEFAULT '{}',
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE session_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    attachment_id INTEGER NOT NULL,
    match_confidence TEXT NOT NULL DEFAULT 'unmatched',
    match_reason TEXT,
    assigned_by TEXT DEFAULT 'auto',
    assigned_at TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, attachment_id)
);
"""

BLOBS = {"sha-aaa": b"\xff\xd8\xffAAA", "sha-bbb": b"\x89PNG\r\n\x1a\nBBB"}


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _seed(conn, *, with_attachments: bool = True, id_offset: int = 0):
    """User + one session, optionally with two session-assigned attachments.

    ``id_offset`` advances the attachments autoincrement so a destination
    database hands out different ids than the source — the exact condition the
    sha256-based remapping must survive.
    """
    conn.execute("INSERT INTO users (email, display_name) VALUES ('me@example.com', 'Me')")
    user_id = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.execute("INSERT INTO parse_runs (run_id, user_id) VALUES ('run-1', ?)", (user_id,))
    conn.execute(
        "INSERT INTO sessions (run_id, user_id, session_id, date)"
        " VALUES ('run-1', ?, '2026-03-05', '2026-03-05')",
        (user_id,),
    )
    session_pk = conn.execute("SELECT id FROM sessions").fetchone()["id"]

    for i in range(id_offset):
        conn.execute(
            "INSERT INTO attachments (user_id, stored_filename, original_filename, mime_type, sha256)"
            " VALUES (?, ?, ?, 'image/jpeg', ?)",
            (user_id, f"filler{i}", f"filler{i}", f"filler-sha-{i}"),
        )

    if with_attachments:
        for sha, name in (("sha-aaa", "worksheet.jpg"), ("sha-bbb", "board.png")):
            cur = conn.execute(
                """INSERT INTO attachments
                   (user_id, stored_filename, original_filename, mime_type, sha256, captured_at_local)
                   VALUES (?, ?, ?, ?, ?, '2026-03-05T10:00:00')""",
                (user_id, f"{sha}-stored.jpg", name, "image/jpeg", sha),
            )
            # The upload path writes the INTEGER sessions.id into this column.
            conn.execute(
                """INSERT INTO session_attachments
                   (user_id, session_id, attachment_id, match_confidence, match_reason, assigned_by)
                   VALUES (?, ?, ?, 'high', 'within_session_window', 'auto')""",
                (user_id, session_pk, cur.lastrowid),
            )
    conn.commit()
    return user_id


def _manifest_from(conn, user_id, schema=bh.BACKUP_SCHEMA_VERSION):
    attachment_rows, link_rows = bh.load_backup_attachments(conn, user_id)
    manifest = {"schema_version": schema, "summaries": []}
    manifest.update(bh.attachment_manifest_entries(attachment_rows, link_rows))
    return manifest


def _archive_for(manifest, blobs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for sha, data in blobs.items():
            zf.writestr(f"attachments/{sha}.jpg", data)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


# --- export ---------------------------------------------------------------

def test_export_carries_attachments_with_portable_session_id():
    conn = _conn()
    user_id = _seed(conn)

    manifest = _manifest_from(conn, user_id)

    assert manifest["attachment_count"] == 2
    assert {a["sha256"] for a in manifest["attachments"]} == {"sha-aaa", "sha-bbb"}
    # Links must carry the session *string*, not the source instance's row id.
    assert {l["session_id"] for l in manifest["session_attachments"]} == {"2026-03-05"}


def test_export_skips_unassigned_attachments():
    conn = _conn()
    user_id = _seed(conn)
    # A loose upload never matched to a session should not be exported.
    conn.execute(
        "INSERT INTO attachments (user_id, stored_filename, original_filename, mime_type, sha256)"
        " VALUES (?, 'loose', 'loose.jpg', 'image/jpeg', 'sha-loose')",
        (user_id,),
    )
    conn.commit()

    manifest = _manifest_from(conn, user_id)
    assert "sha-loose" not in {a["sha256"] for a in manifest["attachments"]}


def test_archive_members_named_by_sha(tmp_path):
    conn = _conn()
    user_id = _seed(conn)
    folder = tmp_path / "attachments"
    folder.mkdir()
    (folder / "sha-aaa-stored.jpg").write_bytes(BLOBS["sha-aaa"])

    attachment_rows, _ = bh.load_backup_attachments(conn, user_id)
    members = dict(bh.attachment_archive_members(attachment_rows, str(folder)))

    # Only the blob that exists on disk is emitted, keyed by sha256.
    assert list(members) == ["attachments/sha-aaa.jpg"]
    assert members["attachments/sha-aaa.jpg"] == BLOBS["sha-aaa"]


# --- import ---------------------------------------------------------------

def test_import_restores_attachments_and_remaps_ids(tmp_path):
    src = _conn()
    src_user = _seed(src)
    manifest = _manifest_from(src, src_user)
    archive = _archive_for(manifest, BLOBS)

    # Destination hands out different attachment ids than the source.
    dest = _conn()
    dest_user = _seed(dest, with_attachments=False, id_offset=7)
    folder = str(tmp_path / "attachments")

    created, linked = bh.restore_backup_attachments(dest, archive, manifest, dest_user, folder)
    dest.commit()

    assert (created, linked) == (2, 2)
    assert len(list((tmp_path / "attachments").iterdir())) == 2

    dest_session_pk = dest.execute("SELECT id FROM sessions").fetchone()["id"]
    rows = dest.execute(
        "SELECT sa.session_id, a.sha256, a.id AS attachment_id FROM session_attachments sa"
        " JOIN attachments a ON a.id = sa.attachment_id"
    ).fetchall()

    assert {r["sha256"] for r in rows} == {"sha-aaa", "sha-bbb"}
    # Links resolve to the DESTINATION's session row id...
    assert all(int(r["session_id"]) == dest_session_pk for r in rows)
    # ...and the attachment ids are the destination's own, not the source's.
    assert all(r["attachment_id"] > 7 for r in rows)


def test_import_is_idempotent(tmp_path):
    src = _conn()
    manifest = _manifest_from(src, _seed(src))
    folder = str(tmp_path / "attachments")

    dest = _conn()
    dest_user = _seed(dest, with_attachments=False)

    first = bh.restore_backup_attachments(dest, _archive_for(manifest, BLOBS), manifest, dest_user, folder)
    dest.commit()
    second = bh.restore_backup_attachments(dest, _archive_for(manifest, BLOBS), manifest, dest_user, folder)
    dest.commit()

    assert first == (2, 2)
    assert second == (0, 0), "re-importing the same backup must not duplicate anything"
    assert dest.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"] == 2
    assert dest.execute("SELECT COUNT(*) c FROM session_attachments").fetchone()["c"] == 2


def test_v1_archive_without_attachments_is_a_noop(tmp_path):
    manifest = {"schema_version": "lessonlens-backup.v1", "summaries": []}
    dest = _conn()
    dest_user = _seed(dest, with_attachments=False)

    result = bh.restore_backup_attachments(
        dest, _archive_for(manifest, {}), manifest, dest_user, str(tmp_path / "a")
    )
    assert result == (0, 0)
    assert "lessonlens-backup.v1" in bh.SUPPORTED_BACKUP_SCHEMAS


def test_manifest_row_without_blob_is_skipped(tmp_path):
    src = _conn()
    manifest = _manifest_from(src, _seed(src))
    # Only one of the two blobs made it into the archive.
    archive = _archive_for(manifest, {"sha-aaa": BLOBS["sha-aaa"]})

    dest = _conn()
    dest_user = _seed(dest, with_attachments=False)
    created, _ = bh.restore_backup_attachments(
        dest, archive, manifest, dest_user, str(tmp_path / "attachments")
    )
    assert created == 1, "a manifest row with no blob must not create a broken record"


def test_import_skips_sessions_missing_on_destination(tmp_path):
    src = _conn()
    manifest = _manifest_from(src, _seed(src))

    dest = _conn()
    dest_user = _seed(dest, with_attachments=False)
    dest.execute("DELETE FROM sessions")  # destination lacks that session
    dest.commit()

    created, linked = bh.restore_backup_attachments(
        dest, _archive_for(manifest, BLOBS), manifest, dest_user, str(tmp_path / "a")
    )
    assert created == 2
    assert linked == 0, "links to an unknown session must be skipped, not fabricated"


def test_normalize_member_rejects_traversal():
    assert bh.normalize_member("attachments/x.jpg") == "attachments/x.jpg"
    for bad in ("../etc/passwd", "a/../../b", ""):
        try:
            bh.normalize_member(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")

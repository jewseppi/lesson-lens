"""Tests for incremental sync — sessions are merged, never deleted."""

import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import auth_header


def _mock_modules(sessions, stats=None):
    """Build mock parse_line_export & extract_transcript modules."""
    if stats is None:
        stats = {
            "total_sessions": len(sessions),
            "total_messages": sum(s["message_count"] for s in sessions),
            "lesson_content_messages": sum(s["lesson_content_count"] for s in sessions),
        }
    result = {"sessions": sessions, "stats": stats, "warnings": []}
    return {
        "parse_line_export": MagicMock(
            load_config=MagicMock(return_value={}),
            parse_lines=MagicMock(return_value=result),
            write_outputs=MagicMock(),
        ),
        "extract_transcript": MagicMock(
            extract=MagicMock(return_value={"lines": ["l1"], "source": "LINE"}),
        ),
    }


SESSION_A = {
    "session_id": "2025-01-10",
    "date": "2025-01-10",
    "start_time": "10:00",
    "end_time": "11:00",
    "message_count": 5,
    "lesson_content_count": 3,
    "boundary_confidence": "high",
}

SESSION_B = {
    "session_id": "2025-01-11",
    "date": "2025-01-11",
    "start_time": "09:00",
    "end_time": "10:30",
    "message_count": 4,
    "lesson_content_count": 2,
    "boundary_confidence": "medium",
}

SESSION_C = {
    "session_id": "2025-01-12",
    "date": "2025-01-12",
    "start_time": "14:00",
    "end_time": "15:00",
    "message_count": 6,
    "lesson_content_count": 4,
    "boundary_confidence": "high",
}


def _sync(client, token, content, filename="export.txt", sessions=None):
    """Helper to POST /api/sync with mocked parser."""
    if sessions is None:
        sessions = [SESSION_A]
    mods = _mock_modules(sessions)
    with patch.dict("sys.modules", mods):
        return client.post(
            "/api/sync",
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
            headers=auth_header(token),
        )


def _seed_run_with_sessions_json(db, test_dirs, user_id, sessions):
    """Seed a completed run with a sessions.json on disk."""
    run_id = "seed_run_1"
    output_dir = os.path.join(test_dirs["processed"], run_id)
    os.makedirs(output_dir, exist_ok=True)

    sessions_json = {
        "schema_version": "sessions.v1",
        "run_id": run_id,
        "sessions": sessions,
        "stats": {
            "total_sessions": len(sessions),
            "total_messages": sum(s["message_count"] for s in sessions),
            "lesson_content_messages": sum(s["lesson_content_count"] for s in sessions),
        },
    }
    with open(os.path.join(output_dir, "sessions.json"), "w") as f:
        json.dump(sessions_json, f)

    db.execute(
        "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, "seed.txt", "seed.txt", "seed_hash_abc", 100, 10),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Write a dummy upload file so parse can find it
    upload_path = os.path.join(test_dirs["uploads"], "seed.txt")
    with open(upload_path, "w") as f:
        f.write("seed content\n")

    db.execute(
        """INSERT INTO parse_runs
           (run_id, upload_id, user_id, status, session_count, message_count,
            lesson_content_count, output_dir, completed_at)
           VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, datetime('now'))""",
        (run_id, upload_id, user_id, len(sessions),
         sum(s["message_count"] for s in sessions),
         sum(s["lesson_content_count"] for s in sessions),
         output_dir),
    )

    for sess in sessions:
        db.execute(
            """INSERT INTO sessions
               (run_id, user_id, session_id, date, start_time, end_time,
                message_count, lesson_content_count, boundary_confidence, topics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')""",
            (run_id, user_id, sess["session_id"], sess["date"],
             sess["start_time"], sess["end_time"],
             sess["message_count"], sess["lesson_content_count"],
             sess["boundary_confidence"]),
        )
    db.commit()
    return run_id


class TestIncrementalSync:
    def test_first_sync_creates_run(self, client, db, admin_user, admin_token, test_dirs):
        """First sync with no existing run creates a fresh run."""
        r = _sync(client, admin_token, b"first file content\n", sessions=[SESSION_A, SESSION_B])
        assert r.status_code == 201
        body = r.get_json()
        assert body["session_count"] == 2
        assert body["new_session_count"] == 2

    def test_duplicate_file_is_noop(self, client, db, admin_user, admin_token, test_dirs):
        """Re-syncing the exact same file returns existing stats without deleting."""
        content = b"same file content noop test\n"
        r1 = _sync(client, admin_token, content, sessions=[SESSION_A])
        assert r1.status_code == 201

        # Second sync with same content — should be a no-op
        r2 = _sync(client, admin_token, content, sessions=[SESSION_A])
        assert r2.status_code == 200
        body = r2.get_json()
        assert body["duplicate"] is True
        assert body["new_session_count"] == 0
        assert body["session_count"] == 1

    def test_duplicate_preserves_summaries(self, client, db, admin_user, admin_token, test_dirs):
        """Re-syncing the same file does NOT delete existing summaries."""
        content = b"file with summary preservation test\n"
        r1 = _sync(client, admin_token, content, sessions=[SESSION_A])
        assert r1.status_code == 201
        run_id = r1.get_json()["run_id"]

        # Insert a summary for the session
        session_row = db.execute(
            "SELECT id FROM sessions WHERE run_id = ? AND session_id = ?",
            (run_id, SESSION_A["session_id"]),
        ).fetchone()
        db.execute(
            """INSERT INTO lesson_summaries
               (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir)
               VALUES (?, ?, ?, ?, 'openai', 'gpt-4o', '{}', '')""",
            (session_row[0], run_id, SESSION_A["session_id"], admin_user["id"]),
        )
        db.commit()

        # Re-sync same file
        r2 = _sync(client, admin_token, content, sessions=[SESSION_A])
        assert r2.status_code == 200
        assert r2.get_json()["duplicate"] is True

        # Summary still exists
        summary = db.execute(
            "SELECT id FROM lesson_summaries WHERE session_id = ? AND user_id = ?",
            (SESSION_A["session_id"], admin_user["id"]),
        ).fetchone()
        assert summary is not None

    def test_new_file_merges_into_existing_run(self, client, db, admin_user, admin_token, test_dirs):
        """Syncing a different file merges new sessions into the canonical run."""
        run_id = _seed_run_with_sessions_json(
            db, test_dirs, admin_user["id"], [SESSION_A, SESSION_B],
        )

        # Sync a new file that has session C (new) and session A (overlap)
        r = _sync(
            client, admin_token,
            b"different file with new sessions\n",
            sessions=[SESSION_A, SESSION_C],
        )
        assert r.status_code == 201
        body = r.get_json()

        # Only session C should be new
        assert body["new_session_count"] == 1
        # Total should be 3 (A + B from seed + C new)
        assert body["session_count"] == 3
        # Run ID should be the canonical run
        assert body["run_id"] == run_id

    def test_merge_preserves_summaries(self, client, db, admin_user, admin_token, test_dirs):
        """Summaries from existing sessions survive a merge sync."""
        run_id = _seed_run_with_sessions_json(
            db, test_dirs, admin_user["id"], [SESSION_A],
        )

        # Add a summary
        session_row = db.execute(
            "SELECT id FROM sessions WHERE run_id = ? AND session_id = ?",
            (run_id, SESSION_A["session_id"]),
        ).fetchone()
        db.execute(
            """INSERT INTO lesson_summaries
               (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir)
               VALUES (?, ?, ?, ?, 'openai', 'gpt-4o', '{"test": true}', '')""",
            (session_row[0], run_id, SESSION_A["session_id"], admin_user["id"]),
        )
        db.commit()

        # Sync a new file with session B
        r = _sync(
            client, admin_token,
            b"new file merging sessions\n",
            sessions=[SESSION_B],
        )
        assert r.status_code == 201
        assert r.get_json()["new_session_count"] == 1

        # Summary for session A still exists
        summary = db.execute(
            "SELECT id FROM lesson_summaries WHERE session_id = ? AND user_id = ?",
            (SESSION_A["session_id"], admin_user["id"]),
        ).fetchone()
        assert summary is not None

    def test_merge_updates_sessions_json(self, client, db, admin_user, admin_token, test_dirs):
        """Merging writes new sessions into the existing sessions.json file."""
        run_id = _seed_run_with_sessions_json(
            db, test_dirs, admin_user["id"], [SESSION_A],
        )

        r = _sync(
            client, admin_token,
            b"file with session B\n",
            sessions=[SESSION_B],
        )
        assert r.status_code == 201

        # Read the merged sessions.json
        run_row = db.execute("SELECT output_dir FROM parse_runs WHERE run_id = ?", (run_id,)).fetchone()
        sessions_path = os.path.join(run_row[0], "sessions.json")
        with open(sessions_path) as f:
            data = json.load(f)

        session_ids = [s["session_id"] for s in data["sessions"]]
        assert SESSION_A["session_id"] in session_ids
        assert SESSION_B["session_id"] in session_ids
        assert data["stats"]["total_sessions"] == 2

    def test_no_new_sessions_returns_zero(self, client, db, admin_user, admin_token, test_dirs):
        """Syncing a file whose sessions all already exist returns new_session_count=0."""
        _seed_run_with_sessions_json(
            db, test_dirs, admin_user["id"], [SESSION_A, SESSION_B],
        )

        # Sync a file with only existing sessions
        r = _sync(
            client, admin_token,
            b"file with only existing sessions\n",
            sessions=[SESSION_A],
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["new_session_count"] == 0
        assert body["session_count"] == 2

    def test_empty_sessions_skipped(self, client, db, admin_user, admin_token, test_dirs):
        """Sessions with message_count=0 are not inserted."""
        empty_session = {
            "session_id": "2025-01-13",
            "date": "2025-01-13",
            "start_time": "10:00",
            "end_time": "10:01",
            "message_count": 0,
            "lesson_content_count": 0,
            "boundary_confidence": "low",
        }
        r = _sync(client, admin_token, b"file with empty session\n", sessions=[SESSION_A, empty_session])
        assert r.status_code == 201
        body = r.get_json()
        assert body["session_count"] == 1
        assert body["new_session_count"] == 1

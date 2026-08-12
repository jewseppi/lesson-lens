"""Tests for DELETE /api/sessions/<id> and the run-scoping invariant around it.

The delete endpoint's original lookup joined on ``run["id"]`` — the parse_runs
integer primary key — while ``sessions.run_id`` stores the run-id *string*, so it
matched nothing and the endpoint always 404'd. These tests pin the fix, the user
scoping, and the restore point that makes the delete undoable.

They also pin the invariant that makes the obvious "skip duplicate sessions on
re-parse" change wrong: ``list_sessions`` is scoped to the latest run, so a
session that isn't re-inserted under the new run id disappears from the UI.
"""
import json

import pytest

import restore_points


def _seed_run(db, user_id, run_id, sessions, created_offset="+0 seconds", output_dir="/tmp/test"):
    db.execute(
        """INSERT INTO uploads
           (user_id, original_filename, stored_filename, file_hash, file_size, line_count)
           VALUES (?, 'chat.txt', ?, ?, 100, 10)""",
        (user_id, f"{run_id}.txt", f"hash-{run_id}"),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        f"""INSERT INTO parse_runs
            (run_id, upload_id, user_id, status, session_count, message_count,
             output_dir, completed_at, created_at)
            VALUES (?, ?, ?, 'completed', ?, 30, ?,
                    datetime('now'), datetime('now', '{created_offset}'))""",
        (run_id, upload_id, user_id, len(sessions), str(output_dir)),
    )
    for sid, date in sessions:
        db.execute(
            """INSERT INTO sessions
               (run_id, user_id, session_id, date, start_time, end_time,
                message_count, lesson_content_count, boundary_confidence)
               VALUES (?, ?, ?, ?, '10:00', '10:30', 10, 5, 'high')""",
            (run_id, user_id, sid, date),
        )
    db.commit()
    return run_id


class TestDeleteSession:
    def test_delete_removes_the_session(self, client, user_token, db, regular_user):
        _seed_run(db, regular_user["id"], "run_a", [("2026-08-05", "2026-08-05")])
        resp = client.delete(
            "/api/sessions/2026-08-05",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["deleted"] is True
        remaining = db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (regular_user["id"],)
        ).fetchone()[0]
        assert remaining == 0

    def test_lookup_uses_the_run_id_string_not_the_row_id(
        self, client, user_token, db, regular_user
    ):
        """The original bug: sessions.run_id is a string, parse_runs.id is an int.

        Seeded so the two can never be confused — if the endpoint compares
        against the integer primary key again, nothing matches and this 404s.
        """
        _seed_run(db, regular_user["id"], "20260812_151710_1", [("2026-08-05", "2026-08-05")])
        row_id = db.execute(
            "SELECT id FROM parse_runs WHERE run_id = ?", ("20260812_151710_1",)
        ).fetchone()["id"]
        assert str(row_id) != "20260812_151710_1"

        resp = client.delete(
            "/api/sessions/2026-08-05",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200, "regression: lookup is using parse_runs.id again"

    def test_delete_cascades_to_attached_data(
        self, client, user_token, db, regular_user
    ):
        _seed_run(db, regular_user["id"], "run_c", [("2026-08-05", "2026-08-05")])
        uid = regular_user["id"]
        sess_int_id = db.execute(
            "SELECT id FROM sessions WHERE session_id = ?", ("2026-08-05",)
        ).fetchone()["id"]
        db.execute(
            """INSERT INTO attachments
               (user_id, stored_filename, original_filename, mime_type, sha256)
               VALUES (?, 'a.jpg', 'a.jpg', 'image/jpeg', 'sha-a')""",
            (uid,),
        )
        att_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            """INSERT INTO session_attachments
               (user_id, session_id, attachment_id, match_confidence, match_reason, assigned_by)
               VALUES (?, ?, ?, 'high', 'test', 'auto')""",
            (uid, sess_int_id, att_id),
        )
        db.execute(
            """INSERT INTO annotations
               (user_id, session_id, target_type, target_id, annotation_type, content_json)
               VALUES (?, '2026-08-05', 'summary', 'vocab-0', 'note', ?)""",
            (uid, json.dumps({"text": "keep an eye on this"})),
        )
        db.commit()

        resp = client.delete(
            "/api/sessions/2026-08-05",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        assert db.execute(
            "SELECT COUNT(*) FROM session_attachments WHERE user_id = ?", (uid,)
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM annotations WHERE user_id = ?", (uid,)
        ).fetchone()[0] == 0
        # The image itself is not deleted — it may belong to other sessions too.
        assert db.execute(
            "SELECT COUNT(*) FROM attachments WHERE user_id = ?", (uid,)
        ).fetchone()[0] == 1

    def test_delete_captures_a_restore_point(
        self, client, user_token, db, regular_user, tmp_path
    ):
        """Deleting a lesson is destructive and one click away — make it undoable."""
        # A snapshot is built from the run's parse artifacts, so they must exist.
        (tmp_path / "sessions.json").write_text(
            json.dumps({
                "2026-08-05": {
                    "session_id": "2026-08-05", "date": "2026-08-05",
                    "start_time": "10:00", "end_time": "10:30", "messages": [],
                }
            }),
            encoding="utf-8",
        )
        _seed_run(
            db, regular_user["id"], "run_d", [("2026-08-05", "2026-08-05")],
            output_dir=tmp_path,
        )
        client.delete(
            "/api/sessions/2026-08-05",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        reasons = [
            r["reason"]
            for r in db.execute(
                "SELECT reason FROM restore_points WHERE user_id = ?",
                (regular_user["id"],),
            ).fetchall()
        ]
        assert restore_points.REASON_DELETE_SESSION in reasons

    def test_delete_unknown_session_404s(self, client, user_token, db, regular_user):
        _seed_run(db, regular_user["id"], "run_e", [("2026-08-05", "2026-08-05")])
        resp = client.delete(
            "/api/sessions/1999-01-01",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client):
        assert client.delete("/api/sessions/2026-08-05").status_code == 401

    def test_cannot_delete_another_users_session(
        self, client, user_token, db, regular_user, admin_user
    ):
        _seed_run(db, regular_user["id"], "run_mine", [("2026-08-05", "2026-08-05")])
        _seed_run(db, admin_user["id"], "run_theirs", [("2026-08-09", "2026-08-09")])
        resp = client.delete(
            "/api/sessions/2026-08-09",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 404
        assert db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (admin_user["id"],)
        ).fetchone()[0] == 1


class TestRunScopingInvariant:
    def test_sessions_not_carried_into_the_latest_run_are_invisible(
        self, client, user_token, db, regular_user
    ):
        """Why 'skip duplicates on re-parse' is the wrong fix.

        list_sessions scopes to the latest run. A session left under an older
        run id is not merely duplicated — it is gone from the UI. Since LINE
        exports are cumulative, a re-parse that skipped known sessions would
        empty the sessions page.
        """
        uid = regular_user["id"]
        _seed_run(db, uid, "run_old", [("2026-08-05", "2026-08-05")], "-2 hours")
        listed = client.get(
            "/api/sessions", headers={"Authorization": f"Bearer {user_token}"}
        ).get_json()
        assert [s["session_id"] for s in listed] == ["2026-08-05"]

        # A later run that did not re-insert the known session.
        _seed_run(db, uid, "run_new", [], "+1 hour")
        listed = client.get(
            "/api/sessions", headers={"Authorization": f"Bearer {user_token}"}
        ).get_json()
        assert listed == [], "the session is stranded under the old run id"

        # Still on disk — invisible, not deleted. Re-inserting under the new run
        # is what keeps it visible, which is why parse_upload must not skip.
        assert db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (uid,)
        ).fetchone()[0] == 1

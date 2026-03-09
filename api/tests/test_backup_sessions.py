"""
Tests for backup import/export, session listing with data, and summary import.
These tests cover the largest uncovered code blocks to push past 60% coverage.
"""
import io
import json
import os
import zipfile

import pytest

from tests.conftest import auth_header, ADMIN_EMAIL, ADMIN_PASSWORD


def _seed_full_run(db, admin_user, test_dirs):
    """Seed a complete upload + parse_run + sessions + output files."""
    import app as app_module

    user_id = admin_user["id"]

    # Create upload
    upload_dir = test_dirs["uploads"]
    os.makedirs(upload_dir, exist_ok=True)
    stored = "test-stored.txt"
    with open(os.path.join(upload_dir, stored), "w") as f:
        f.write("line1\nline2\nline3\n")

    db.execute(
        "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, 'export.txt', ?, 'hash123', 30, 3)",
        (user_id, stored),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Create output directory with sessions.json
    output_dir = os.path.join(test_dirs["processed"], "test-run")
    os.makedirs(output_dir, exist_ok=True)

    sessions_data = {
        "sessions": [
            {
                "session_id": "s1",
                "date": "2025-01-15",
                "start_time": "09:00",
                "end_time": "10:00",
                "message_count": 5,
                "lesson_content_count": 3,
                "boundary_confidence": "high",
                "topics": ["greetings"],
                "messages": [
                    {"text_raw": "Hello", "speaker_role": "tutor", "speaker_raw": "Teacher", "time": "09:00"},
                    {"text_raw": "Hi there", "speaker_role": "student", "speaker_raw": "Student", "time": "09:01"},
                    {"text_raw": "How are you?", "speaker_role": "tutor", "speaker_raw": "Teacher", "time": "09:02"},
                    {"text_raw": "Good thanks", "speaker_role": "student", "speaker_raw": "Student", "time": "09:03"},
                    {"text_raw": "Great!", "speaker_role": "tutor", "speaker_raw": "Teacher", "time": "09:04"},
                ],
            },
            {
                "session_id": "s2",
                "date": "2025-01-16",
                "start_time": "14:00",
                "end_time": "15:00",
                "message_count": 1,
                "lesson_content_count": 0,
                "boundary_confidence": "low",
                "topics": [],
                "messages": [
                    {"text_raw": "Check https://example.com/lesson", "speaker_role": "tutor"},
                ],
            },
        ]
    }

    with open(os.path.join(output_dir, "sessions.json"), "w") as f:
        json.dump(sessions_data, f)

    # Create parse_run
    db.execute(
        "INSERT INTO parse_runs (run_id, upload_id, user_id, status, session_count, message_count, lesson_content_count, output_dir, completed_at) VALUES ('test-run', ?, ?, 'completed', 2, 6, 3, ?, '2025-01-15T10:00:00')",
        (upload_id, user_id, output_dir),
    )

    # Create session rows
    db.execute(
        "INSERT INTO sessions (run_id, session_id, date, start_time, end_time, message_count, lesson_content_count, boundary_confidence, topics_json) VALUES ('test-run', 's1', '2025-01-15', '09:00', '10:00', 5, 3, 'high', ?)",
        (json.dumps(["greetings"]),),
    )
    db.execute(
        "INSERT INTO sessions (run_id, session_id, date, start_time, end_time, message_count, lesson_content_count, boundary_confidence, topics_json) VALUES ('test-run', 's2', '2025-01-16', '14:00', '15:00', 1, 0, 'low', '[]')"
    )
    db.commit()

    return upload_id, output_dir


class TestSessionsWithData:
    def test_list_sessions(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.get("/api/sessions", headers=auth_header(admin_token))
        assert r.status_code == 200
        sessions = r.get_json()
        # s1 has 5 messages (meets threshold), s2 has 1 message but has a link
        assert len(sessions) >= 1
        s1 = next((s for s in sessions if s["session_id"] == "s1"), None)
        assert s1 is not None
        assert s1["message_count"] == 5
        assert s1["topics"] == ["greetings"]

    def test_get_session_detail(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.get("/api/sessions/s1", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert data["session_id"] == "s1"
        assert "messages" in data

    def test_get_session_not_found(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.get("/api/sessions/nonexistent", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_session_with_links(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.get("/api/sessions", headers=auth_header(admin_token))
        sessions = r.get_json()
        s2 = next((s for s in sessions if s["session_id"] == "s2"), None)
        if s2:
            assert len(s2["shared_links"]) >= 1


class TestBackupExportWithData:
    def test_export_full_backup(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.get("/api/backup/export", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.content_type == "application/zip"

        # Verify zip contents
        zf = zipfile.ZipFile(io.BytesIO(r.data))
        names = zf.namelist()
        assert "manifest.json" in names
        assert "parse/sessions.json" in names

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["schema_version"] == "lessonlens-backup.v1"
        assert manifest["source_user"]["email"] == ADMIN_EMAIL


class TestBackupImport:
    def _build_test_backup(self, sessions=None, summaries=None):
        """Build a minimal valid backup zip in memory."""
        if sessions is None:
            sessions = [
                {
                    "session_id": "imported-s1",
                    "date": "2025-02-01",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "message_count": 4,
                    "lesson_content_count": 2,
                    "boundary_confidence": "medium",
                    "topics": ["imported"],
                    "messages": [],
                }
            ]

        manifest = {
            "schema_version": "lessonlens-backup.v1",
            "exported_at": "2025-02-01T00:00:00",
            "source_user": {"email": "test@test.local", "display_name": "Test"},
            "latest_run": {
                "run_id": "orig-run",
                "completed_at": "2025-02-01T00:00:00",
                "session_count": len(sessions),
                "message_count": sum(s.get("message_count", 0) for s in sessions),
                "lesson_content_count": sum(s.get("lesson_content_count", 0) for s in sessions),
                "upload": {
                    "original_filename": "export.txt",
                    "stored_filename": "stored.txt",
                    "file_size": 100,
                    "line_count": 10,
                    "uploaded_at": "2025-02-01T00:00:00",
                },
            },
            "session_count": len(sessions),
            "summary_count": len(summaries or []),
            "summaries": summaries or [],
        }

        sessions_payload = {"sessions": sessions}

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("parse/sessions.json", json.dumps(sessions_payload))
            zf.writestr("raw-exports/export.txt", "line1\nline2\n")
        buf.seek(0)
        return buf

    def test_import_valid_backup(self, client, admin_token):
        backup = self._build_test_backup()
        r = client.post(
            "/api/backup/import",
            data={
                "file": (backup, "backup.zip"),
                "replace_existing": "true",
            },
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["session_count"] == 1
        assert body["message"] == "Backup imported successfully"

    def test_import_no_file(self, client, admin_token):
        r = client.post(
            "/api/backup/import",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_bad_zip(self, client, admin_token):
        r = client.post(
            "/api/backup/import",
            data={"file": (io.BytesIO(b"not a zip"), "bad.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "zip" in r.get_json()["error"].lower()

    def test_import_wrong_schema(self, client, admin_token):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"schema_version": "wrong"}))
        buf.seek(0)
        r = client.post(
            "/api/backup/import",
            data={"file": (buf, "backup.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_no_sessions(self, client, admin_token):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"schema_version": "lessonlens-backup.v1"}))
            zf.writestr("parse/sessions.json", json.dumps({"sessions": []}))
        buf.seek(0)
        r = client.post(
            "/api/backup/import",
            data={"file": (buf, "backup.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_merge_skips_existing(self, client, admin_token):
        """Importing with replace_existing=false skips sessions already present."""
        # First import
        backup1 = self._build_test_backup()
        r = client.post(
            "/api/backup/import",
            data={"file": (backup1, "backup.zip"), "replace_existing": "true"},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201
        assert r.get_json()["session_count"] == 1

        # Second import (merge) with same session — should skip it
        backup2 = self._build_test_backup()
        r = client.post(
            "/api/backup/import",
            data={"file": (backup2, "backup.zip"), "replace_existing": "false"},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        body = r.get_json()
        assert r.status_code == 200
        assert body["session_count"] == 0
        assert body["skipped_session_count"] == 1

    def test_import_merge_adds_new(self, client, admin_token):
        """Importing with replace_existing=false adds only truly new sessions."""
        # First import
        backup1 = self._build_test_backup()
        r = client.post(
            "/api/backup/import",
            data={"file": (backup1, "backup.zip"), "replace_existing": "true"},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201

        # Second import with a different session
        new_sessions = [
            {
                "session_id": "imported-s2",
                "date": "2025-02-02",
                "start_time": "12:00",
                "end_time": "13:00",
                "message_count": 3,
                "lesson_content_count": 1,
                "boundary_confidence": "medium",
                "topics": [],
                "messages": [],
            }
        ]
        backup2 = self._build_test_backup(sessions=new_sessions)
        r = client.post(
            "/api/backup/import",
            data={"file": (backup2, "backup.zip"), "replace_existing": "false"},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        body = r.get_json()
        assert r.status_code == 201
        assert body["session_count"] == 1
        assert body["skipped_session_count"] == 0

    def test_import_defaults_to_merge(self, client, admin_token):
        """Default replace_existing is false (merge mode)."""
        backup = self._build_test_backup()
        r = client.post(
            "/api/backup/import",
            data={"file": (backup, "backup.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        body = r.get_json()
        assert body["replace_existing"] is False


class TestBackupPreview:
    def _build_test_backup(self, sessions=None):
        if sessions is None:
            sessions = [
                {
                    "session_id": "prev-s1",
                    "date": "2025-03-01",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "message_count": 4,
                    "lesson_content_count": 2,
                    "boundary_confidence": "medium",
                    "topics": [],
                    "messages": [],
                }
            ]
        manifest = {
            "schema_version": "lessonlens-backup.v1",
            "exported_at": "2025-03-01T00:00:00",
            "source_user": {"email": "test@test.local", "display_name": "Test"},
            "latest_run": {
                "run_id": "orig-run",
                "completed_at": "2025-03-01T00:00:00",
                "session_count": len(sessions),
                "message_count": sum(s.get("message_count", 0) for s in sessions),
                "lesson_content_count": sum(s.get("lesson_content_count", 0) for s in sessions),
                "upload": None,
            },
            "session_count": len(sessions),
            "summary_count": 0,
            "summaries": [],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("parse/sessions.json", json.dumps({"sessions": sessions}))
        buf.seek(0)
        return buf

    def test_preview_all_new(self, client, admin_token):
        backup = self._build_test_backup()
        r = client.post(
            "/api/backup/import/preview",
            data={"file": (backup, "backup.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["incoming_session_count"] == 1
        assert body["new_session_count"] == 1
        assert body["skipped_session_count"] == 0

    def test_preview_with_existing(self, client, admin_token):
        # First import to create existing sessions
        backup1 = self._build_test_backup()
        r = client.post(
            "/api/backup/import",
            data={"file": (backup1, "backup.zip"), "replace_existing": "true"},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201

        # Preview with same sessions
        backup2 = self._build_test_backup()
        r = client.post(
            "/api/backup/import/preview",
            data={"file": (backup2, "backup.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["incoming_session_count"] == 1
        assert body["new_session_count"] == 0
        assert body["skipped_session_count"] == 1

    def test_preview_no_file(self, client, admin_token):
        r = client.post(
            "/api/backup/import/preview",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_preview_bad_zip(self, client, admin_token):
        r = client.post(
            "/api/backup/import/preview",
            data={"file": (io.BytesIO(b"junk"), "bad.zip")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400


class TestSyncRemote:
    def test_sync_missing_params(self, client, admin_token):
        r = client.post(
            "/api/backup/sync-remote",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_sync_partial_params(self, client, admin_token):
        r = client.post(
            "/api/backup/sync-remote",
            json={"remote_base_url": "https://example.com"},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400


class TestNormalizeRemoteBaseUrl:
    def test_valid_url(self):
        from app import _normalize_remote_base_url
        result = _normalize_remote_base_url("https://example.com/")
        assert result == "https://example.com"

    def test_trailing_slash_stripped(self):
        from app import _normalize_remote_base_url
        result = _normalize_remote_base_url("https://example.com///")
        assert not result.endswith("/")

    def test_invalid_scheme(self):
        from app import _normalize_remote_base_url
        with pytest.raises(ValueError):
            _normalize_remote_base_url("ftp://example.com")

    def test_empty_url(self):
        from app import _normalize_remote_base_url
        with pytest.raises(ValueError):
            _normalize_remote_base_url("")


class TestSummaryImportWithSession:
    def test_import_summary_lesson_date_mismatch(self, client, db, admin_user, admin_token, test_dirs):
        """Importing a summary with wrong lesson_date returns 400."""
        _seed_full_run(db, admin_user, test_dirs)
        lesson_data = {
            "schema_version": "lesson-data.v1",
            "lesson_date": "wrong-id",
            "title": "Imported Lesson",
        }
        data = {
            "file": (io.BytesIO(json.dumps(lesson_data).encode()), "lesson-data.json"),
        }
        r = client.post(
            "/api/sessions/s1/summary/import",
            data=data,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "lesson_date" in r.get_json()["error"]

    def test_import_summary_bad_schema(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        lesson_data = {"not_valid": True}
        data = {
            "file": (io.BytesIO(json.dumps(lesson_data).encode()), "lesson-data.json"),
        }
        r = client.post(
            "/api/sessions/s1/summary/import",
            data=data,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400


class TestGenerateRouteGuards:
    def test_generate_no_run_data(self, client, admin_token):
        r = client.post(
            "/api/sessions/fake/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_generate_all_no_run_data(self, client, admin_token):
        r = client.post(
            "/api/summaries/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

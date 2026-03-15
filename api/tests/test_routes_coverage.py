"""
Tests for parse_upload, sync_file, generate_summary, generate_all_summaries,
import_summary, sync_backup_remote, and remaining edge-case routes.
Mocks external script imports and HTTP calls to avoid needing real parsers.
"""
import io
import json
import os
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------
def _seed_full_run(db, user, test_dirs):
    """Create a complete upload + parse_run + sessions + sessions.json on disk."""
    user_id = user["id"]

    upload_dir = test_dirs["uploads"]
    os.makedirs(upload_dir, exist_ok=True)
    stored = "parse-test-stored.txt"
    with open(os.path.join(upload_dir, stored), "w") as f:
        f.write("[LINE] Chat\n2025.01.15 Wed\n09:00\tTeacher\tHello\n09:01\tStudent\tHi\n")

    db.execute(
        "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) "
        "VALUES (?, 'export.txt', ?, 'hash-parse-test', 100, 4)",
        (user_id, stored),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    output_dir = os.path.join(test_dirs["processed"], "run-parse")
    os.makedirs(output_dir, exist_ok=True)

    sessions_data = {
        "sessions": [
            {
                "session_id": "2025-01-15",
                "date": "2025-01-15",
                "start_time": "09:00",
                "end_time": "10:00",
                "message_count": 5,
                "lesson_content_count": 3,
                "boundary_confidence": "high",
                "topics": ["greetings"],
                "messages": [
                    {"text_raw": "Hello", "speaker_role": "tutor", "speaker_raw": "Teacher", "time": "09:00"},
                    {"text_raw": "Hi", "speaker_role": "student", "speaker_raw": "Student", "time": "09:01"},
                    {"text_raw": "How?", "speaker_role": "tutor", "speaker_raw": "Teacher", "time": "09:02"},
                    {"text_raw": "Good", "speaker_role": "student", "speaker_raw": "Student", "time": "09:03"},
                    {"text_raw": "Great", "speaker_role": "tutor", "speaker_raw": "Teacher", "time": "09:04"},
                ],
            },
        ],
    }
    with open(os.path.join(output_dir, "sessions.json"), "w") as f:
        json.dump(sessions_data, f)

    db.execute(
        "INSERT INTO parse_runs (run_id, upload_id, user_id, status, session_count, message_count, "
        "lesson_content_count, output_dir, completed_at) "
        "VALUES ('run-parse', ?, ?, 'completed', 1, 5, 3, ?, '2025-01-15T10:00:00')",
        (upload_id, user_id, output_dir),
    )
    db.execute(
        "INSERT INTO sessions (run_id, session_id, date, start_time, end_time, "
        "message_count, lesson_content_count, boundary_confidence, topics_json) "
        "VALUES ('run-parse', '2025-01-15', '2025-01-15', '09:00', '10:00', 5, 3, 'high', ?)",
        (json.dumps(["greetings"]),),
    )
    db.commit()
    return upload_id, output_dir


# ---------------------------------------------------------------------------
# Parse route tests
# ---------------------------------------------------------------------------
class TestParseUpload:
    def _mock_parse_modules(self):
        """Return patchers for parse_line_export and extract_transcript."""
        mock_config = {"session_boundary": {}, "output": {}}
        mock_extract = MagicMock(return_value={
            "lines": ["line1", "line2"],
            "source": "LINE",
        })
        mock_parse = MagicMock(return_value={
            "sessions": [
                {
                    "session_id": "2025-01-15",
                    "date": "2025-01-15",
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "message_count": 5,
                    "lesson_content_count": 3,
                    "boundary_confidence": "high",
                },
            ],
            "stats": {
                "total_sessions": 1,
                "total_messages": 5,
                "lesson_content_messages": 3,
            },
            "warnings": [],
        })
        mock_write = MagicMock()
        mock_load_config = MagicMock(return_value=mock_config)

        return mock_extract, mock_parse, mock_write, mock_load_config

    def test_parse_upload_success(self, client, db, admin_user, admin_token, test_dirs):
        upload_id, _ = _seed_full_run(db, admin_user, test_dirs)
        # Remove existing parse_run so it can be re-parsed
        db.execute("DELETE FROM sessions WHERE run_id = 'run-parse'")
        db.execute("DELETE FROM parse_runs WHERE run_id = 'run-parse'")
        db.commit()

        mock_extract, mock_parse, mock_write, mock_load_config = self._mock_parse_modules()

        with patch.dict("sys.modules", {
            "parse_line_export": MagicMock(
                load_config=mock_load_config,
                parse_lines=mock_parse,
                write_outputs=mock_write,
            ),
            "extract_transcript": MagicMock(extract=mock_extract),
        }):
            r = client.post(
                f"/api/parse/{upload_id}",
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["session_count"] == 1
        assert body["message_count"] == 5
        assert "run_id" in body

    def test_parse_upload_not_found(self, client, admin_token):
        r = client.post("/api/parse/9999", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_parse_upload_already_parsed(self, client, db, admin_user, admin_token, test_dirs):
        upload_id, _ = _seed_full_run(db, admin_user, test_dirs)
        r = client.post(
            f"/api/parse/{upload_id}",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        assert r.get_json()["duplicate"] is True

    def test_parse_upload_force_reparse(self, client, db, admin_user, admin_token, test_dirs):
        upload_id, _ = _seed_full_run(db, admin_user, test_dirs)
        mock_extract, mock_parse, mock_write, mock_load_config = self._mock_parse_modules()

        with patch.dict("sys.modules", {
            "parse_line_export": MagicMock(
                load_config=mock_load_config,
                parse_lines=mock_parse,
                write_outputs=mock_write,
            ),
            "extract_transcript": MagicMock(extract=mock_extract),
        }):
            r = client.post(
                f"/api/parse/{upload_id}?force=true",
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["session_count"] == 1


# ---------------------------------------------------------------------------
# Sync route tests
# ---------------------------------------------------------------------------
class TestSyncFile:
    def _mock_parse_modules(self):
        mock_config = {"session_boundary": {}, "output": {}}
        mock_extract = MagicMock(return_value={
            "lines": ["l1", "l2"],
            "source": "LINE",
        })
        mock_parse = MagicMock(return_value={
            "sessions": [
                {
                    "session_id": "2025-01-20",
                    "date": "2025-01-20",
                    "start_time": "10:00",
                    "end_time": "11:00",
                    "message_count": 3,
                    "lesson_content_count": 2,
                    "boundary_confidence": "medium",
                },
            ],
            "stats": {
                "total_sessions": 1,
                "total_messages": 3,
                "lesson_content_messages": 2,
            },
            "warnings": [],
        })
        mock_write = MagicMock()
        mock_load_config = MagicMock(return_value=mock_config)
        return mock_extract, mock_parse, mock_write, mock_load_config

    def test_sync_success(self, client, db, admin_user, admin_token, test_dirs):
        mock_extract, mock_parse, mock_write, mock_load_config = self._mock_parse_modules()

        with patch.dict("sys.modules", {
            "parse_line_export": MagicMock(
                load_config=mock_load_config,
                parse_lines=mock_parse,
                write_outputs=mock_write,
            ),
            "extract_transcript": MagicMock(extract=mock_extract),
        }):
            r = client.post(
                "/api/sync",
                data={"file": (io.BytesIO(b"chat data\nline2\n"), "export.txt")},
                content_type="multipart/form-data",
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["session_count"] == 1
        assert body["message_count"] == 3

    def test_sync_no_file(self, client, admin_token):
        r = client.post(
            "/api/sync",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_sync_empty_filename(self, client, admin_token):
        r = client.post(
            "/api/sync",
            data={"file": (io.BytesIO(b"data"), "")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_sync_bad_extension(self, client, admin_token):
        r = client.post(
            "/api/sync",
            data={"file": (io.BytesIO(b"data"), "file.pdf")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_sync_duplicate_file(self, client, db, admin_user, admin_token, test_dirs):
        """Two syncs with same content returns 200 no-op (incremental sync)."""
        content = b"unique chat content for dup test\n"

        def _make_modules():
            me, mp, mw, mc = self._mock_parse_modules()
            return {
                "parse_line_export": MagicMock(
                    load_config=mc,
                    parse_lines=mp,
                    write_outputs=mw,
                ),
                "extract_transcript": MagicMock(extract=me),
            }

        with patch.dict("sys.modules", _make_modules()):
            r1 = client.post(
                "/api/sync",
                data={"file": (io.BytesIO(content), "export.txt")},
                content_type="multipart/form-data",
                headers=auth_header(admin_token),
            )
        assert r1.status_code == 201

        with patch.dict("sys.modules", _make_modules()):
            r2 = client.post(
                "/api/sync",
                data={"file": (io.BytesIO(content), "export.txt")},
                content_type="multipart/form-data",
                headers=auth_header(admin_token),
            )
        assert r2.status_code == 200
        assert r2.get_json()["duplicate"] is True


# ---------------------------------------------------------------------------
# Generate summary route tests
# ---------------------------------------------------------------------------
class TestGenerateSummary:
    def test_generate_no_parsed_data(self, client, admin_token):
        r = client.post(
            "/api/sessions/2025-01-15/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_generate_session_not_found(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.post(
            "/api/sessions/nonexistent/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_generate_success(self, client, db, admin_user, admin_token, test_dirs, tmp_path):
        _seed_full_run(db, admin_user, test_dirs)

        lesson_data = {
            "schema_version": "lesson-data.v1",
            "title": "Test Lesson",
            "lesson_date": "2025-01-15",
        }

        # Mock _generate_summary_for_session to avoid real script imports
        with patch("app._generate_summary_for_session") as mock_gen:
            mock_gen.return_value = (lesson_data, "openai", "gpt-4o", "allow", None)
            r = client.post(
                "/api/sessions/2025-01-15/generate",
                json={"provider": "openai", "model": "gpt-4o"},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["title"] == "Test Lesson"

    def test_generate_value_error(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        with patch("app._generate_summary_for_session") as mock_gen:
            mock_gen.side_effect = ValueError("OPENAI_API_KEY not set")
            r = client.post(
                "/api/sessions/2025-01-15/generate",
                json={},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 400
        assert "OPENAI_API_KEY" in r.get_json()["error"]

    def test_generate_unexpected_error(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        with patch("app._generate_summary_for_session") as mock_gen:
            mock_gen.side_effect = RuntimeError("unexpected failure")
            r = client.post(
                "/api/sessions/2025-01-15/generate",
                json={},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 500

    def test_generate_sessions_file_not_found(self, client, db, admin_user, admin_token, test_dirs):
        """When sessions.json is missing, _load_sessions_payload raises FileNotFoundError."""
        _seed_full_run(db, admin_user, test_dirs)
        # Remove sessions.json
        output_dir = os.path.join(test_dirs["processed"], "run-parse")
        os.remove(os.path.join(output_dir, "sessions.json"))

        r = client.post(
            "/api/sessions/2025-01-15/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404
        assert "not found" in r.get_json()["error"].lower()


class TestGenerateAllSummaries:
    def test_no_parsed_data(self, client, admin_token):
        r = client.post(
            "/api/summaries/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_generate_all_success(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        lesson_data = {
            "schema_version": "lesson-data.v1",
            "title": "Lesson 1",
            "lesson_date": "2025-01-15",
        }

        mock_process_session = MagicMock()
        mock_gen_config = {"generation": {"default_provider": "openai", "default_model": "gpt-4o", "temperature": 0.3}}

        with patch("app._load_generator_config") as mock_load_gen, \
             patch("app._validate_provider_credentials", return_value=None), \
             patch("app._generate_summary_for_session") as mock_gen:
            mock_load_gen.return_value = (mock_process_session, mock_gen_config, "openai", "gpt-4o", 0.3)
            mock_gen.return_value = (lesson_data, "openai", "gpt-4o", "allow", None)

            r = client.post(
                "/api/summaries/generate",
                json={"min_lesson_content_count": 1},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 200
        body = r.get_json()
        assert body["generated_count"] == 1
        assert body["failed_count"] == 0

    def test_generate_all_with_limit(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        mock_process_session = MagicMock()

        with patch("app._load_generator_config") as mock_load_gen, \
             patch("app._validate_provider_credentials", return_value=None), \
             patch("app._generate_summary_for_session") as mock_gen:
            mock_load_gen.return_value = (mock_process_session, {}, "openai", "gpt-4o", 0.3)
            mock_gen.return_value = ({"title": "X"}, "openai", "gpt-4o", "allow", None)

            r = client.post(
                "/api/summaries/generate",
                json={"limit": 1, "min_lesson_content_count": 1},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 200
        assert r.get_json()["generated_count"] <= 1

    def test_generate_all_bad_limit(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        mock_process_session = MagicMock()

        with patch("app._load_generator_config") as mock_load_gen, \
             patch("app._validate_provider_credentials", return_value=None):
            mock_load_gen.return_value = (mock_process_session, {}, "openai", "gpt-4o", 0.3)

            r = client.post(
                "/api/summaries/generate",
                json={"limit": "abc", "min_lesson_content_count": 1},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 400

    def test_generate_all_credential_error(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        mock_process_session = MagicMock()

        with patch("app._load_generator_config") as mock_load_gen, \
             patch("app._validate_provider_credentials", return_value="OPENAI_API_KEY not set"):
            mock_load_gen.return_value = (mock_process_session, {}, "openai", "gpt-4o", 0.3)

            r = client.post(
                "/api/summaries/generate",
                json={"min_lesson_content_count": 1},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 400
        assert "OPENAI_API_KEY" in r.get_json()["error"]

    def test_generate_all_sessions_file_missing(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        output_dir = os.path.join(test_dirs["processed"], "run-parse")
        os.remove(os.path.join(output_dir, "sessions.json"))

        r = client.post(
            "/api/summaries/generate",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_generate_all_with_failure(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        mock_process_session = MagicMock()

        with patch("app._load_generator_config") as mock_load_gen, \
             patch("app._validate_provider_credentials", return_value=None), \
             patch("app._generate_summary_for_session") as mock_gen:
            mock_load_gen.return_value = (mock_process_session, {}, "openai", "gpt-4o", 0.3)
            mock_gen.side_effect = RuntimeError("API down")

            r = client.post(
                "/api/summaries/generate",
                json={"min_lesson_content_count": 1},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 200
        body = r.get_json()
        assert body["failed_count"] == 1
        assert body["generated_count"] == 0

    def test_generate_all_overwrite(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        # Insert an existing summary
        session_row = db.execute("SELECT * FROM sessions WHERE session_id = '2025-01-15'").fetchone()
        db.execute(
            "INSERT INTO lesson_summaries (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir) "
            "VALUES (?, 'run-parse', '2025-01-15', ?, 'openai', 'gpt-4o', ?, '/tmp')",
            (session_row["id"], admin_user["id"], json.dumps({"title": "old"})),
        )
        db.commit()

        mock_process_session = MagicMock()

        with patch("app._load_generator_config") as mock_load_gen, \
             patch("app._validate_provider_credentials", return_value=None), \
             patch("app._generate_summary_for_session") as mock_gen:
            mock_load_gen.return_value = (mock_process_session, {}, "openai", "gpt-4o", 0.3)
            mock_gen.return_value = ({"title": "new"}, "openai", "gpt-4o", "allow", None)

            r = client.post(
                "/api/summaries/generate",
                json={"overwrite": True, "min_lesson_content_count": 1},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 200
        body = r.get_json()
        assert body["overwrite"] is True
        assert body["generated_count"] == 1


# ---------------------------------------------------------------------------
# Sync backup remote tests
# ---------------------------------------------------------------------------
class TestSyncBackupRemote:
    def test_sync_remote_success(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        login_response = (200, {"access_token": "remote-token"})
        import_response = (201, {"session_count": 1, "summary_count": 0})

        with patch("app._post_json", return_value=login_response), \
             patch("app._post_multipart", return_value=import_response):
            r = client.post(
                "/api/backup/sync-remote",
                json={
                    "remote_base_url": "https://remote.example.com",
                    "remote_email": "user@remote.com",
                    "remote_password": "password123",
                },
                headers=auth_header(admin_token),
            )

        assert r.status_code == 200
        body = r.get_json()
        assert body["message"] == "Remote sync completed successfully"
        assert body["remote_base_url"] == "https://remote.example.com"

    def test_sync_remote_login_fails(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        with patch("app._post_json", return_value=(401, {"error": "Invalid credentials"})):
            r = client.post(
                "/api/backup/sync-remote",
                json={
                    "remote_base_url": "https://remote.example.com",
                    "remote_email": "user@remote.com",
                    "remote_password": "wrong",
                },
                headers=auth_header(admin_token),
            )

        assert r.status_code == 502

    def test_sync_remote_no_token_returned(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        with patch("app._post_json", return_value=(200, {})):
            r = client.post(
                "/api/backup/sync-remote",
                json={
                    "remote_base_url": "https://remote.example.com",
                    "remote_email": "user@remote.com",
                    "remote_password": "password",
                },
                headers=auth_header(admin_token),
            )

        assert r.status_code == 502
        assert "access token" in r.get_json()["error"].lower()

    def test_sync_remote_import_fails(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        with patch("app._post_json", return_value=(200, {"access_token": "tok"})), \
             patch("app._post_multipart", return_value=(500, {"error": "import failed"})):
            r = client.post(
                "/api/backup/sync-remote",
                json={
                    "remote_base_url": "https://remote.example.com",
                    "remote_email": "user@remote.com",
                    "remote_password": "password",
                },
                headers=auth_header(admin_token),
            )

        assert r.status_code == 502

    def test_sync_remote_bad_url(self, client, admin_token):
        r = client.post(
            "/api/backup/sync-remote",
            json={
                "remote_base_url": "ftp://bad.com",
                "remote_email": "user@remote.com",
                "remote_password": "password",
            },
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Import summary route tests
# ---------------------------------------------------------------------------
class TestImportSummary:
    def test_import_no_parsed_data(self, client, admin_token):
        r = client.post(
            "/api/sessions/s1/summary/import",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_import_session_not_found(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.post(
            "/api/sessions/nonexistent/summary/import",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_import_no_file(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.post(
            "/api/sessions/2025-01-15/summary/import",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_invalid_json(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        r = client.post(
            "/api/sessions/2025-01-15/summary/import",
            data={"file": (io.BytesIO(b"not json"), "lesson.json")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_wrong_schema(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        data = json.dumps({"schema_version": "wrong"}).encode()
        r = client.post(
            "/api/sessions/2025-01-15/summary/import",
            data={"file": (io.BytesIO(data), "lesson.json")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_wrong_session_id(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        data = json.dumps({
            "schema_version": "lesson-data.v1",
            "lesson_date": "2025-01-16",  # doesn't match 2025-01-15
        }).encode()
        r = client.post(
            "/api/sessions/2025-01-15/summary/import",
            data={"file": (io.BytesIO(data), "lesson.json")},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_import_success(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        lesson = {
            "schema_version": "lesson-data.v1",
            "lesson_date": "2025-01-15",
            "title": "Imported Lesson",
        }
        data = json.dumps(lesson).encode()

        mock_install = MagicMock()
        with patch.dict("sys.modules", {
            "install_manual_summary": MagicMock(install_summary_data=mock_install),
        }):
            r = client.post(
                "/api/sessions/2025-01-15/summary/import",
                data={"file": (io.BytesIO(data), "lesson.json")},
                content_type="multipart/form-data",
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["title"] == "Imported Lesson"
        mock_install.assert_called_once()


# ---------------------------------------------------------------------------
# Change password edge case tests
# ---------------------------------------------------------------------------
class TestChangePasswordEdgeCases:
    def test_missing_fields(self, client, admin_token):
        r = client.post(
            "/api/change-password",
            json={"current_password": "x"},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_mismatch_confirmation(self, client, admin_token):
        r = client.post(
            "/api/change-password",
            json={
                "current_password": "TestAdminP@ssword!Long123",
                "new_password": "NewSecureP@ssword!999abc",
                "confirm_password": "Different!",
            },
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "does not match" in r.get_json()["error"]

    def test_same_password(self, client, admin_token):
        r = client.post(
            "/api/change-password",
            json={
                "current_password": "TestAdminP@ssword!Long123",
                "new_password": "TestAdminP@ssword!Long123",
                "confirm_password": "TestAdminP@ssword!Long123",
            },
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "different" in r.get_json()["error"].lower()

    def test_wrong_current_password(self, client, admin_token):
        r = client.post(
            "/api/change-password",
            json={
                "current_password": "WrongPassword123!abcdef",
                "new_password": "NewSecureP@ssword!999abc",
                "confirm_password": "NewSecureP@ssword!999abc",
            },
            headers=auth_header(admin_token),
        )
        assert r.status_code == 401

    def test_weak_new_password(self, client, admin_token):
        r = client.post(
            "/api/change-password",
            json={
                "current_password": "TestAdminP@ssword!Long123",
                "new_password": "short",
                "confirm_password": "short",
            },
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "16 characters" in r.get_json()["error"]

    def test_change_password_success(self, client, admin_token):
        r = client.post(
            "/api/change-password",
            json={
                "current_password": "TestAdminP@ssword!Long123",
                "new_password": "BrandNewSecure!Pass789xyz",
                "confirm_password": "BrandNewSecure!Pass789xyz",
            },
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        assert "successfully" in r.get_json()["message"].lower()


# ---------------------------------------------------------------------------
# Analytics edge case tests
# ---------------------------------------------------------------------------
class TestAnalyticsEdgeCases:
    def test_analytics_summary_non_admin(self, client, user_token):
        r = client.get("/api/analytics/summary", headers=auth_header(user_token))
        assert r.status_code == 403

    def test_analytics_summary_admin(self, client, db, admin_user, admin_token):
        r = client.get("/api/analytics/summary", headers=auth_header(admin_token))
        assert r.status_code == 200
        body = r.get_json()
        assert "total_users" in body
        assert "events_by_type" in body

    def test_track_client_event(self, client, admin_token):
        r = client.post(
            "/api/analytics/event",
            json={"event_type": "quiz_complete", "event_data": {"score": 90}},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201

    def test_track_event_missing_type(self, client, admin_token):
        r = client.post(
            "/api/analytics/event",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Profile route
# ---------------------------------------------------------------------------
class TestProfile:
    def test_profile_success(self, client, admin_token):
        r = client.get("/api/profile", headers=auth_header(admin_token))
        assert r.status_code == 200
        body = r.get_json()
        assert "email" in body
        assert "display_name" in body


# ---------------------------------------------------------------------------
# Get summary route
# ---------------------------------------------------------------------------
class TestGetSummary:
    def test_no_summary(self, client, admin_token):
        r = client.get(
            "/api/sessions/nonexistent/summary",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404

    def test_get_summary_success(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)
        session_row = db.execute("SELECT * FROM sessions WHERE session_id = '2025-01-15'").fetchone()
        db.execute(
            "INSERT INTO lesson_summaries (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir) "
            "VALUES (?, 'run-parse', '2025-01-15', ?, 'openai', 'gpt-4o', ?, '/tmp')",
            (session_row["id"], admin_user["id"], json.dumps({"title": "My Lesson", "schema_version": "lesson-data.v1"})),
        )
        db.commit()

        r = client.get(
            "/api/sessions/2025-01-15/summary",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        assert r.get_json()["title"] == "My Lesson"


# ---------------------------------------------------------------------------
# SPA fallback route
# ---------------------------------------------------------------------------
class TestSPARoute:
    def test_api_404(self, client):
        r = client.get("/api/nonexistent")
        assert r.status_code == 404

    def test_spa_fallback(self, client):
        """SPA route returns either HTML (200) or 404 depending on build."""
        r = client.get("/some-page")
        assert r.status_code in (200, 404)

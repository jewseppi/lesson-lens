"""
Tests for helper functions and additional route coverage.
Covers extract_session_links, session_should_list, validate_provider_credentials,
backup helpers, and additional route edge cases.
"""
import json
import os

import pytest

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# _extract_session_links
# ---------------------------------------------------------------------------
class TestExtractSessionLinks:
    def test_no_messages(self):
        from app import _extract_session_links
        assert _extract_session_links({}) == []
        assert _extract_session_links({"messages": []}) == []

    def test_no_links(self):
        from app import _extract_session_links
        session = {"messages": [{"text_raw": "Hello there"}]}
        assert _extract_session_links(session) == []

    def test_single_link(self):
        from app import _extract_session_links
        session = {"messages": [
            {"text_raw": "Check out https://example.com/page", "speaker_role": "tutor", "speaker_raw": "Tutor", "time": "10:00"},
        ]}
        links = _extract_session_links(session)
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/page"
        assert links[0]["speaker_role"] == "tutor"

    def test_deduplication(self):
        from app import _extract_session_links
        session = {"messages": [
            {"text_raw": "Visit https://example.com"},
            {"text_raw": "Again https://example.com"},
        ]}
        links = _extract_session_links(session)
        assert len(links) == 1

    def test_context_before_after(self):
        from app import _extract_session_links
        session = {"messages": [
            {"text_raw": "Here is context before"},
            {"text_raw": "https://example.com"},
            {"text_raw": "Here is context after"},
        ]}
        links = _extract_session_links(session)
        assert len(links) == 1
        assert links[0]["before_text"] == "Here is context before"
        assert links[0]["after_text"] == "Here is context after"

    def test_non_dict_input(self):
        from app import _extract_session_links
        assert _extract_session_links("not a dict") == []
        assert _extract_session_links(None) == []


# ---------------------------------------------------------------------------
# _session_should_list
# ---------------------------------------------------------------------------
class TestSessionShouldList:
    def test_enough_messages(self):
        from app import _session_should_list
        assert _session_should_list({"message_count": 5, "messages": []}) is True

    def test_too_few_messages_no_links(self):
        from app import _session_should_list
        assert _session_should_list({"message_count": 1, "messages": []}) is False

    def test_few_messages_but_has_links(self):
        from app import _session_should_list
        session = {
            "message_count": 1,
            "messages": [{"text_raw": "https://example.com"}],
        }
        assert _session_should_list(session) is True

    def test_non_dict(self):
        from app import _session_should_list
        assert _session_should_list(None) is False


# ---------------------------------------------------------------------------
# _validate_provider_credentials
# ---------------------------------------------------------------------------
class TestValidateProviderCredentials:
    def test_openai_missing(self, monkeypatch):
        from app import _validate_provider_credentials
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        err = _validate_provider_credentials("openai")
        assert "OPENAI_API_KEY" in err

    def test_openai_present(self, monkeypatch):
        from app import _validate_provider_credentials
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert _validate_provider_credentials("openai") is None

    def test_anthropic_missing(self, monkeypatch):
        from app import _validate_provider_credentials
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        err = _validate_provider_credentials("anthropic")
        assert "ANTHROPIC_API_KEY" in err

    def test_gemini_missing(self, monkeypatch):
        from app import _validate_provider_credentials
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        err = _validate_provider_credentials("gemini")
        assert "GEMINI_API_KEY" in err

    def test_unknown_provider(self):
        from app import _validate_provider_credentials
        assert _validate_provider_credentials("unknown") is None


# ---------------------------------------------------------------------------
# _normalize_backup_member
# ---------------------------------------------------------------------------
class TestNormalizeBackupMember:
    def test_normal_path(self):
        from app import _normalize_backup_member
        assert _normalize_backup_member("parse/sessions.json") == "parse/sessions.json"

    def test_backslash(self):
        from app import _normalize_backup_member
        assert _normalize_backup_member("parse\\sessions.json") == "parse/sessions.json"

    def test_leading_slash(self):
        from app import _normalize_backup_member
        assert _normalize_backup_member("/parse/sessions.json") == "parse/sessions.json"

    def test_traversal_attack(self):
        from app import _normalize_backup_member
        with pytest.raises(ValueError):
            _normalize_backup_member("../etc/passwd")

    def test_traversal_mid_path(self):
        from app import _normalize_backup_member
        with pytest.raises(ValueError):
            _normalize_backup_member("parse/../../etc/passwd")

    def test_empty(self):
        from app import _normalize_backup_member
        with pytest.raises(ValueError):
            _normalize_backup_member("/")


# ---------------------------------------------------------------------------
# _read_backup_json
# ---------------------------------------------------------------------------
class TestReadBackupJson:
    def test_valid_json(self, tmp_path):
        import zipfile
        from app import _read_backup_json

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data.json", json.dumps({"key": "value"}))

        with zipfile.ZipFile(zip_path, "r") as zf:
            result = _read_backup_json(zf, "data.json")
        assert result == {"key": "value"}

    def test_missing_file(self, tmp_path):
        import zipfile
        from app import _read_backup_json

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("other.json", "{}")

        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(ValueError, match="missing"):
                _read_backup_json(zf, "data.json")


# ---------------------------------------------------------------------------
# _load_user and _delete_user_learning_data
# ---------------------------------------------------------------------------
class TestLoadUser:
    def test_load_existing(self, db, admin_user):
        from app import _load_user
        import sqlite3
        import app as app_module
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        user = _load_user(conn, "admin@test.local")
        assert user is not None
        assert user["email"] == "admin@test.local"
        conn.close()

    def test_load_nonexistent(self, db):
        from app import _load_user
        import sqlite3
        import app as app_module
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        user = _load_user(conn, "nobody@test.local")
        assert user is None
        conn.close()


class TestDeleteUserLearningData:
    def test_delete_no_data(self, db, admin_user):
        """Should not error when user has no learning data."""
        from app import _delete_user_learning_data
        import sqlite3
        import app as app_module
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        _delete_user_learning_data(conn, admin_user["id"])
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# _store_lesson_summary
# ---------------------------------------------------------------------------
class TestStoreLessonSummary:
    def test_store_and_retrieve(self, db, admin_user):
        from app import _store_lesson_summary

        # Seed required data
        db.execute(
            "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, 'f.txt', 's.txt', 'h1', 10, 1)",
            (admin_user["id"],),
        )
        db.execute(
            "INSERT INTO parse_runs (run_id, upload_id, user_id, status) VALUES ('run1', 1, ?, 'completed')",
            (admin_user["id"],),
        )
        db.execute(
            "INSERT INTO sessions (run_id, session_id, date, message_count) VALUES ('run1', 'sess1', '2025-01-01', 5)"
        )
        db.commit()

        session_row = db.execute("SELECT * FROM sessions WHERE session_id='sess1'").fetchone()
        run = db.execute("SELECT * FROM parse_runs WHERE run_id='run1'").fetchone()

        _store_lesson_summary(
            db, session_row, run, admin_user["id"],
            "openai", "gpt-4o", {"title": "test"}, "/tmp/out",
        )
        db.commit()

        stored = db.execute("SELECT * FROM lesson_summaries WHERE session_id='sess1'").fetchone()
        assert stored is not None
        assert json.loads(stored["lesson_data_json"])["title"] == "test"


# ---------------------------------------------------------------------------
# Additional route edge cases
# ---------------------------------------------------------------------------
class TestBackupRoutes:
    def test_backup_export_no_data(self, client, admin_token):
        """Export with no parsed data returns error."""
        r = client.get("/api/backup/export", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_backup_import_unauthenticated(self, client):
        r = client.post("/api/backup/import")
        assert r.status_code == 401

    def test_backup_sync_remote_unauthenticated(self, client):
        r = client.post("/api/backup/sync-remote")
        assert r.status_code == 401


class TestParseRoute:
    def test_parse_unauthenticated(self, client):
        r = client.post("/api/parse/1")
        assert r.status_code == 401

    def test_parse_wrong_upload(self, client, admin_token):
        r = client.post("/api/parse/99999", headers=auth_header(admin_token))
        assert r.status_code == 404


class TestSyncRoute:
    def test_sync_unauthenticated(self, client):
        r = client.post("/api/sync")
        assert r.status_code == 401


class TestGenerateRoute:
    def test_generate_unauthenticated(self, client):
        r = client.post("/api/sessions/x/generate")
        assert r.status_code == 401

    def test_generate_all_unauthenticated(self, client):
        r = client.post("/api/summaries/generate")
        assert r.status_code == 401


class TestImportSummaryRoute:
    def test_import_no_file(self, client, admin_token):
        r = client.post(
            "/api/sessions/fake/summary/import",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        # 404 because session doesn't exist, or 400 because no file — either is valid
        assert r.status_code in (400, 404)


class TestInitDb:
    def test_init_db_idempotent(self, test_app):
        """Calling init_db multiple times should not error."""
        from app import init_db
        with test_app.app_context():
            init_db()
            init_db()

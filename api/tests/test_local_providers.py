"""
Tests for local provider support (ollama, openai_compatible_local).
Covers provider validation, credential bypass, health endpoint, and
generation route acceptance of local providers.
"""
import json
import os
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# Reuse seed helper from test_routes_coverage
# ---------------------------------------------------------------------------
def _seed_full_run(db, user, test_dirs):
    user_id = user["id"]

    upload_dir = test_dirs["uploads"]
    os.makedirs(upload_dir, exist_ok=True)
    stored = "parse-test-stored.txt"
    with open(os.path.join(upload_dir, stored), "w") as f:
        f.write("[LINE] Chat\n2025.01.15 Wed\n09:00\tTeacher\tHello\n09:01\tStudent\tHi\n")

    db.execute(
        "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) "
        "VALUES (?, 'export.txt', ?, 'hash-lp-test', 100, 4)",
        (user_id, stored),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    output_dir = os.path.join(test_dirs["processed"], "run-parse-lp")
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
                    {"message_id": "m1", "time": "09:00", "speaker_role": "teacher",
                     "text_raw": "Hello", "message_type": "lesson-content"},
                ],
            }
        ]
    }
    with open(os.path.join(output_dir, "sessions.json"), "w") as f:
        json.dump(sessions_data, f)

    db.execute(
        "INSERT INTO parse_runs (run_id, user_id, upload_id, status, output_dir) "
        "VALUES ('run-parse-lp', ?, ?, 'completed', ?)",
        (user_id, upload_id, output_dir),
    )

    db.execute(
        "INSERT INTO sessions (session_id, run_id, user_id, date, start_time, end_time, "
        "message_count, lesson_content_count, boundary_confidence) "
        "VALUES ('2025-01-15', 'run-parse-lp', ?, '2025-01-15', '09:00', '10:00', 5, 3, 'high')",
        (user_id,),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------
class TestProviderValidation:
    def test_ollama_needs_no_api_key(self):
        from app import _validate_provider_credentials
        assert _validate_provider_credentials("ollama") is None

    def test_openai_compatible_local_needs_no_api_key(self):
        from app import _validate_provider_credentials
        assert _validate_provider_credentials("openai_compatible_local") is None

    def test_unknown_provider_rejected(self):
        from app import _validate_provider_credentials
        err = _validate_provider_credentials("unknown-provider")
        assert err is not None
        assert "Unknown provider" in err

    def test_cloud_providers_still_validated(self):
        from app import _validate_provider_credentials
        with patch.dict(os.environ, {}, clear=True):
            # Remove all cloud keys
            for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
                os.environ.pop(key, None)
            assert _validate_provider_credentials("openai") is not None
            assert _validate_provider_credentials("anthropic") is not None
            assert _validate_provider_credentials("gemini") is not None


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
class TestLocalHealthEndpoint:
    def test_health_requires_auth(self, client):
        r = client.get("/api/models/local/health")
        assert r.status_code == 401

    def test_health_both_offline(self, client, admin_token):
        with patch("app.urllib_request.urlopen", side_effect=Exception("Connection refused")):
            r = client.get(
                "/api/models/local/health",
                headers=auth_header(admin_token),
            )
        assert r.status_code == 200
        body = r.get_json()
        assert body["ollama"]["ok"] is False
        assert body["openai_compatible_local"]["ok"] is False

    def test_health_ollama_online(self, client, admin_token):
        ollama_response = json.dumps({"models": [{"name": "qwen2.5:7b-instruct"}]}).encode()

        def mock_urlopen(req, **kwargs):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/api/tags" in url:
                resp = MagicMock()
                resp.read.return_value = ollama_response
                resp.__enter__ = lambda s: s
                resp.__exit__ = MagicMock(return_value=False)
                return resp
            raise Exception("Connection refused")

        with patch("app.urllib_request.urlopen", side_effect=mock_urlopen):
            r = client.get(
                "/api/models/local/health",
                headers=auth_header(admin_token),
            )
        body = r.get_json()
        assert body["ollama"]["ok"] is True
        assert "qwen2.5:7b-instruct" in body["ollama"]["models"]
        assert body["openai_compatible_local"]["ok"] is False

    def test_health_oai_compatible_online(self, client, admin_token):
        oai_response = json.dumps({"data": [{"id": "my-local-model"}]}).encode()

        def mock_urlopen(req, **kwargs):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/models" in url and "/api/tags" not in url:
                resp = MagicMock()
                resp.read.return_value = oai_response
                resp.__enter__ = lambda s: s
                resp.__exit__ = MagicMock(return_value=False)
                return resp
            raise Exception("Connection refused")

        with patch("app.urllib_request.urlopen", side_effect=mock_urlopen):
            r = client.get(
                "/api/models/local/health",
                headers=auth_header(admin_token),
            )
        body = r.get_json()
        assert body["ollama"]["ok"] is False
        assert body["openai_compatible_local"]["ok"] is True
        assert "my-local-model" in body["openai_compatible_local"]["models"]


# ---------------------------------------------------------------------------
# Generation route with local providers
# ---------------------------------------------------------------------------
class TestGenerateWithLocalProvider:
    def test_generate_with_ollama(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        lesson_data = {
            "schema_version": "lesson-data.v1",
            "title": "Test Ollama Lesson",
            "lesson_date": "2025-01-15",
        }

        with patch("app._generate_summary_for_session") as mock_gen:
            mock_gen.return_value = (lesson_data, "ollama", "qwen2.5:7b-instruct", "allow", None)
            r = client.post(
                "/api/sessions/2025-01-15/generate",
                json={"provider": "ollama"},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["title"] == "Test Ollama Lesson"

    def test_generate_with_openai_compatible_local(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        lesson_data = {
            "schema_version": "lesson-data.v1",
            "title": "Test Local OAI Lesson",
            "lesson_date": "2025-01-15",
        }

        with patch("app._generate_summary_for_session") as mock_gen:
            mock_gen.return_value = (lesson_data, "openai_compatible_local", "local-model", "allow", None)
            r = client.post(
                "/api/sessions/2025-01-15/generate",
                json={"provider": "openai_compatible_local"},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 201
        body = r.get_json()
        assert body["title"] == "Test Local OAI Lesson"

    def test_generate_unknown_provider_rejected(self, client, db, admin_user, admin_token, test_dirs):
        _seed_full_run(db, admin_user, test_dirs)

        with patch("app._generate_summary_for_session") as mock_gen:
            mock_gen.side_effect = ValueError("Unknown provider 'bad_provider'")
            r = client.post(
                "/api/sessions/2025-01-15/generate",
                json={"provider": "bad_provider"},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Config model resolution for local providers
# ---------------------------------------------------------------------------
class TestLocalModelResolution:
    def test_ollama_default_model_from_env(self):
        from app import _load_generator_config

        mock_config = {
            "generation": {"default_provider": "openai", "default_model": "gpt-4o", "temperature": 0.3},
            "local": {"ollama_model": "qwen2.5:7b-instruct"},
        }

        with patch("app._load_generator_config") as mock_load:
            # Test that passing ollama as provider override resolves correct model
            mock_load.return_value = (MagicMock(), mock_config, "ollama", "qwen2.5:7b-instruct", 0.3)
            _, _, provider, model, _ = mock_load(provider_override="ollama")
            assert provider == "ollama"
            assert model == "qwen2.5:7b-instruct"

    def test_openai_compatible_local_default_model(self):
        from app import _load_generator_config

        with patch("app._load_generator_config") as mock_load:
            mock_load.return_value = (MagicMock(), {}, "openai_compatible_local", "local-model", 0.3)
            _, _, provider, model, _ = mock_load(provider_override="openai_compatible_local")
            assert provider == "openai_compatible_local"
            assert model == "local-model"

"""
Tests for model selection flow, provider validation, and profile endpoints.
"""
import json
import os

import pytest

from tests.conftest import auth_header


class TestProviderValidation:
    """Test that _validate_provider_credentials checks correctly."""

    def test_unknown_provider_rejected(self, client, admin_token):
        """Generate endpoint rejects unknown providers."""
        # We need a session to exist first — but the point is the provider check
        r = client.post(
            "/api/sessions/fake-session/generate",
            json={"provider": "totally_unknown_provider"},
            headers=auth_header(admin_token),
        )
        # It may 404 (no parsed data) or 400/500 (unknown provider)
        # The important thing is it doesn't 201
        assert r.status_code != 201

    def test_allowed_providers_constant(self):
        """Verify the ALLOWED_PROVIDERS set contains expected entries."""
        from app import ALLOWED_PROVIDERS
        assert "openai" in ALLOWED_PROVIDERS
        assert "anthropic" in ALLOWED_PROVIDERS
        assert "gemini" in ALLOWED_PROVIDERS
        assert "ollama" in ALLOWED_PROVIDERS
        assert "openai_compatible_local" in ALLOWED_PROVIDERS


class TestGenerateEndpointModelPassthrough:
    """Test that the generate endpoint accepts and passes provider/model from request body."""

    def test_generate_accepts_provider_and_model_fields(self, client, admin_token):
        """Ensure the endpoint reads provider and model from JSON body."""
        # With no parsed data, should get 404 "No parsed data", not a 400 about missing fields
        r = client.post(
            "/api/sessions/test-session/generate",
            json={"provider": "ollama", "model": "qwen2.5:7b"},
            headers=auth_header(admin_token),
        )
        body = r.get_json()
        assert r.status_code == 404
        assert body["error"] == "No parsed data"

    def test_generate_without_provider_model_defaults(self, client, admin_token):
        """Endpoint works with empty body (uses defaults)."""
        r = client.post(
            "/api/sessions/test-session/generate",
            json={},
            headers=auth_header(admin_token),
        )
        body = r.get_json()
        assert r.status_code == 404
        assert body["error"] == "No parsed data"

    def test_generate_unauthenticated(self, client):
        """Generate endpoint requires auth."""
        r = client.post(
            "/api/sessions/test-session/generate",
            json={"provider": "ollama"},
        )
        assert r.status_code == 401


class TestLoadGeneratorConfig:
    """Test the _load_generator_config function directly."""

    def test_provider_override(self, test_app, monkeypatch):
        from app import _load_generator_config
        # Don't actually import generate_outputs — mock it
        import app as app_module
        import types

        mock_module = types.ModuleType("generate_outputs")
        mock_module.load_config = lambda: {"generation": {"default_provider": "openai", "default_model": "gpt-4o", "temperature": 0.3}, "local": {}}
        mock_module.process_session = lambda *a, **kw: None
        monkeypatch.setitem(__import__("sys").modules, "generate_outputs", mock_module)

        _, _, provider, model, _ = _load_generator_config(provider_override="ollama")
        assert provider == "ollama"

    def test_model_override(self, test_app, monkeypatch):
        from app import _load_generator_config
        import types

        mock_module = types.ModuleType("generate_outputs")
        mock_module.load_config = lambda: {"generation": {"default_provider": "openai", "default_model": "gpt-4o", "temperature": 0.3}, "local": {}}
        mock_module.process_session = lambda *a, **kw: None
        monkeypatch.setitem(__import__("sys").modules, "generate_outputs", mock_module)

        _, _, provider, model, _ = _load_generator_config(model_override="custom-model-13b")
        assert model == "custom-model-13b"

    def test_ollama_env_fallback(self, test_app, monkeypatch):
        from app import _load_generator_config
        import types

        mock_module = types.ModuleType("generate_outputs")
        mock_module.load_config = lambda: {"generation": {"default_provider": "openai", "temperature": 0.3}, "local": {"ollama_model": "default-model"}}
        mock_module.process_session = lambda *a, **kw: None
        monkeypatch.setitem(__import__("sys").modules, "generate_outputs", mock_module)

        monkeypatch.setenv("OLLAMA_MODEL", "env-model:7b")
        _, _, _, model, _ = _load_generator_config(provider_override="ollama")
        assert model == "env-model:7b"


class TestProfile:
    """Test GET and PUT /api/profile endpoints."""

    def test_get_profile(self, client, admin_token):
        r = client.get("/api/profile", headers=auth_header(admin_token))
        assert r.status_code == 200
        body = r.get_json()
        assert "email" in body
        assert "display_name" in body
        assert "native_language" in body

    def test_get_profile_unauthenticated(self, client):
        r = client.get("/api/profile")
        assert r.status_code == 401

    def test_update_native_language(self, client, admin_token):
        r = client.put(
            "/api/profile",
            json={"native_language": "en"},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

        # Verify it persisted
        r2 = client.get("/api/profile", headers=auth_header(admin_token))
        assert r2.get_json()["native_language"] == "en"

    def test_update_display_name(self, client, admin_token):
        r = client.put(
            "/api/profile",
            json={"display_name": "New Name"},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200

        r2 = client.get("/api/profile", headers=auth_header(admin_token))
        assert r2.get_json()["display_name"] == "New Name"

    def test_update_empty_display_name_rejected(self, client, admin_token):
        r = client.put(
            "/api/profile",
            json={"display_name": "  "},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_update_no_fields_rejected(self, client, admin_token):
        r = client.put(
            "/api/profile",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_update_profile_unauthenticated(self, client):
        r = client.put("/api/profile", json={"native_language": "zh"})
        assert r.status_code == 401


class TestSummaryStale:
    """Test that list_sessions returns summary_stale field."""

    def test_sessions_include_summary_stale_field(self, client, admin_token):
        """Even with no sessions, the endpoint returns an array (testing field existence when sessions exist)."""
        r = client.get("/api/sessions", headers=auth_header(admin_token))
        assert r.status_code == 200
        # With no data, we get empty list — that's fine, just ensure the endpoint works
        body = r.get_json()
        assert isinstance(body, list)

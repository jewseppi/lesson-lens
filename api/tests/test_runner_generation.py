"""
Tests for GitHub Actions runner generation dispatch, status, and webhook endpoints.
"""
import json
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# Dispatch endpoint
# ---------------------------------------------------------------------------
class TestDispatchRunnerGeneration:
    def test_dispatch_requires_auth(self, client):
        r = client.post("/api/generation/dispatch", json={})
        assert r.status_code == 401

    def test_dispatch_returns_501_when_not_configured(self, client, admin_token, monkeypatch):
        import app as app_module
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        r = client.post(
            "/api/generation/dispatch",
            json={},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 501
        assert "not configured" in r.get_json()["error"]

    def test_dispatch_success(self, client, admin_token, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")

        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.urllib_request.urlopen", return_value=mock_resp):
            r = client.post(
                "/api/generation/dispatch",
                json={},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 202
        body = r.get_json()
        assert "job_id" in body
        assert body["message"] == "Generation dispatched to runner"

    def test_dispatch_github_error(self, client, admin_token, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")

        from urllib.error import HTTPError
        mock_exc = HTTPError(
            url="https://api.github.com/...",
            code=422,
            msg="Unprocessable Entity",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=b'{"message": "Validation Failed"}')),
        )

        with patch("app.urllib_request.urlopen", side_effect=mock_exc):
            r = client.post(
                "/api/generation/dispatch",
                json={},
                headers=auth_header(admin_token),
            )

        assert r.status_code == 502
        assert "GitHub dispatch failed" in r.get_json()["error"]


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------
class TestRunnerGenerationStatus:
    def test_status_requires_auth(self, client):
        r = client.get("/api/generation/status")
        assert r.status_code == 401

    def test_status_no_jobs(self, client, admin_token):
        r = client.get(
            "/api/generation/status",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        assert r.get_json()["status"] == "none"

    def test_status_after_dispatch(self, client, admin_token, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")

        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.urllib_request.urlopen", return_value=mock_resp):
            client.post(
                "/api/generation/dispatch",
                json={},
                headers=auth_header(admin_token),
            )

        r = client.get(
            "/api/generation/status",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "dispatched"
        assert body["job_id"] is not None


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------
class TestRunnerGenerationWebhook:
    def test_webhook_returns_501_when_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("GENERATION_WEBHOOK_TOKEN", raising=False)
        r = client.post(
            "/api/generation/webhook",
            json={"status": "completed"},
        )
        assert r.status_code == 501

    def test_webhook_rejects_invalid_token(self, client, monkeypatch):
        monkeypatch.setenv("GENERATION_WEBHOOK_TOKEN", "correct-token")
        r = client.post(
            "/api/generation/webhook",
            json={"status": "completed"},
            headers={"X-Webhook-Token": "wrong-token"},
        )
        assert r.status_code == 401

    def test_webhook_updates_job(self, client, admin_token, monkeypatch, db):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")
        monkeypatch.setenv("GENERATION_WEBHOOK_TOKEN", "test-webhook-secret")

        # Dispatch a job first
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.urllib_request.urlopen", return_value=mock_resp):
            dispatch_r = client.post(
                "/api/generation/dispatch",
                json={},
                headers=auth_header(admin_token),
            )
        assert dispatch_r.status_code == 202

        # Now send webhook
        r = client.post(
            "/api/generation/webhook",
            json={
                "status": "completed",
                "generated": 3,
                "failed": 1,
                "imported": 3,
                "import_failed": 0,
                "total_missing": 4,
                "run_id": "12345",
            },
            headers={"X-Webhook-Token": "test-webhook-secret"},
        )
        assert r.status_code == 200

        # Check status reflects completion
        status_r = client.get(
            "/api/generation/status",
            headers=auth_header(admin_token),
        )
        body = status_r.get_json()
        assert body["status"] == "completed"
        assert body["completed_at"] is not None
        assert body["result"]["generated"] == 3
        assert body["result"]["failed"] == 1
        assert body["result"]["imported"] == 3

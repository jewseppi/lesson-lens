"""Tests for the hosted MCP server and the shared HTTP client.

The hosted server (``api/mcp_server_hosted.py``) is what lets an authenticated
CLI agent read from and write to a REMOTE LessonLens without a local database or
a provider API key. These tests cover the parts that are easy to get wrong:

* the two payload rules the hosted importer enforces (``schema_version`` and
  ``lesson_date == session_id``) — the whole point of validating client-side is
  to turn an opaque HTTP 400 into an actionable message for the agent;
* that ``store_summary`` POSTs to the import endpoint rather than the
  provider-backed generate endpoint;
* the multipart encoding used for that upload.

``urlopen`` is mocked throughout, so nothing touches the network.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lessonlens_client as llc  # noqa: E402

mcp_hosted = pytest.importorskip(
    "mcp_server_hosted", reason="the `mcp` package is not installed in this environment"
)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def captured(monkeypatch):
    """Capture outgoing requests and stub responses."""
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        url = req.full_url
        if url.endswith("/api/login"):
            return _FakeResp({"access_token": "tok"})
        if url.endswith("/summary/import"):
            return _FakeResp({"ok": True})
        if url.endswith("/api/sessions"):
            return _FakeResp(
                [
                    {"session_id": "2026-03-05", "date": "2026-03-05", "needs_summary": True,
                     "message_count": 40, "lesson_content_count": 20},
                    {"session_id": "2026-03-01", "date": "2026-03-01", "has_summary": True,
                     "needs_summary": False, "message_count": 30, "lesson_content_count": 15},
                ]
            )
        return _FakeResp({})

    monkeypatch.setattr(llc.urllib.request, "urlopen", fake_urlopen)
    # Point the module's config at a configured hosted instance and reset the
    # cached client so each test logs in fresh.
    monkeypatch.setattr(mcp_hosted._CONFIG, "api_url", "https://host.example.com")
    monkeypatch.setattr(mcp_hosted._CONFIG, "email", "me@example.com")
    monkeypatch.setattr(mcp_hosted._CONFIG, "password", "pw")
    monkeypatch.setattr(mcp_hosted, "_CLIENT", None)
    return calls


def _valid_payload(session_id="2026-03-05"):
    return {
        "schema_version": "lesson-data.v1",
        "lesson_id": f"lesson-{session_id}",
        "lesson_date": session_id,
        "title": "Ordering coffee",
        "summary": {"overview": "", "usage_notes": "", "short_recap": ""},
        "key_sentences": [{"zh": "我要一杯咖啡"}],
        "vocabulary": [{"zh": "咖啡"}],
    }


# --- store_summary validation --------------------------------------------

def test_store_summary_rejects_wrong_lesson_date(captured):
    payload = _valid_payload()
    payload["lesson_date"] = "1999-01-01"

    result = mcp_hosted.store_summary("2026-03-05", json.dumps(payload))

    assert "lesson_date must equal the session id" in result
    # Nothing was POSTed — we failed fast instead of eating an HTTP 400.
    assert not any("summary/import" in c.full_url for c in captured)


def test_store_summary_rejects_wrong_schema_version(captured):
    payload = _valid_payload()
    payload["schema_version"] = "lesson-data.v2"

    result = mcp_hosted.store_summary("2026-03-05", json.dumps(payload))

    assert "schema_version must be 'lesson-data.v1'" in result
    assert not any("summary/import" in c.full_url for c in captured)


def test_store_summary_reports_missing_keys(captured):
    payload = _valid_payload()
    del payload["vocabulary"]
    del payload["title"]

    result = mcp_hosted.store_summary("2026-03-05", json.dumps(payload))

    assert "missing required keys" in result
    assert "title" in result and "vocabulary" in result


def test_store_summary_rejects_invalid_json(captured):
    result = mcp_hosted.store_summary("2026-03-05", "{not json")
    assert "not valid JSON" in result


def test_store_summary_posts_to_import_endpoint(captured):
    result = mcp_hosted.store_summary("2026-03-05", json.dumps(_valid_payload()))

    assert "Stored summary for 2026-03-05" in result
    import_calls = [c for c in captured if "summary/import" in c.full_url]
    assert len(import_calls) == 1

    req = import_calls[0]
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("Content-type", "").startswith("multipart/form-data; boundary=")
    body = req.data
    assert b'name="provider"' in body and b"claude-agent" in body
    assert b'name="file"' in body
    assert "咖啡".encode("utf-8") in body

    # It must NOT hit the provider-backed generate endpoint.
    assert not any(c.full_url.endswith("/generate") for c in captured)


def test_store_summary_defaults_model_from_generation_meta(captured):
    payload = _valid_payload()
    payload["generation_meta"] = {"model": "claude-opus-5"}

    result = mcp_hosted.store_summary("2026-03-05", json.dumps(payload))
    assert "claude-agent/claude-opus-5" in result


# --- read tools -----------------------------------------------------------

def test_list_sessions_flags_pending_work(captured):
    out = mcp_hosted.list_sessions()
    assert "2026-03-05" in out
    assert "NEEDS SUMMARY" in out
    assert "1 still need a summary" in out


def test_list_sessions_needs_summary_only(captured):
    out = mcp_hosted.list_sessions(needs_summary_only=True)
    assert "2026-03-05" in out
    assert "2026-03-01" not in out


def test_search_sessions_matches_and_misses(captured):
    assert "2026-03-01" in mcp_hosted.search_sessions("03-01")
    assert "No sessions matched" in mcp_hosted.search_sessions("zzzz")


def test_unconfigured_instance_returns_actionable_error(monkeypatch):
    monkeypatch.setattr(mcp_hosted._CONFIG, "api_url", "")
    monkeypatch.setattr(mcp_hosted._CONFIG, "email", "")
    monkeypatch.setattr(mcp_hosted._CONFIG, "password", "")
    monkeypatch.setattr(mcp_hosted, "_CLIENT", None)

    out = mcp_hosted.list_sessions()
    assert out.startswith("Error:")
    assert "LESSONLENS_API_URL" in out


def test_tool_signatures_survive_the_error_guard():
    """FastMCP derives each tool's input schema from inspect.signature().

    The @_guard decorator must preserve it (via functools.wraps / __wrapped__),
    otherwise every tool is advertised to the agent as taking no arguments and
    store_summary becomes uncallable.
    """
    import inspect

    params = inspect.signature(mcp_hosted.store_summary).parameters
    assert list(params) == ["session_id", "lesson_data_json", "provider", "model"]
    assert params["provider"].default == "claude-agent"

    list_params = inspect.signature(mcp_hosted.list_sessions).parameters
    assert list(list_params) == ["include_archived", "needs_summary_only"]

    for name in ("store_summary", "list_sessions", "get_session"):
        sig = inspect.signature(getattr(mcp_hosted, name))
        assert "args" not in sig.parameters, f"{name} lost its signature through @_guard"


def test_lesson_data_schema_is_valid_skeleton():
    skeleton = json.loads(mcp_hosted.lesson_data_schema())
    assert skeleton["schema_version"] == "lesson-data.v1"
    # The skeleton should name every key the importer requires.
    assert mcp_hosted._REQUIRED_KEYS <= set(skeleton)


# --- shared client --------------------------------------------------------

def test_import_summary_encodes_multipart():
    content_type, body = llc.encode_multipart(
        {"provider": "claude-agent", "model": "m"},
        [("file", "x.json", b'{"a":1}', "application/json")],
    )
    boundary = content_type.split("boundary=")[1]
    assert boundary.encode() in body
    assert b'name="file"; filename="x.json"' in body
    assert b'{"a":1}' in body


def test_client_ensure_login_is_idempotent(monkeypatch):
    logins = []

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/api/login"):
            logins.append(req)
            return _FakeResp({"access_token": "tok"})
        return _FakeResp({})

    monkeypatch.setattr(llc.urllib.request, "urlopen", fake_urlopen)
    client = llc.LessonLensClient("https://host.example.com")
    client.ensure_login("a@b.c", "pw")
    client.ensure_login("a@b.c", "pw")
    assert len(logins) == 1

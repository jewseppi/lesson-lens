"""Tests for the setup preflight (scripts/doctor.py).

The point of doctor is that a broken setup produces an *actionable* message
rather than a stack trace, so these assert on the guidance as much as the status.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import doctor  # noqa: E402
from lessonlens_client import ApiError  # noqa: E402
from lessonlens_config import load_config  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in (
        "LESSONLENS_TARGET", "LESSONLENS_API_URL", "LESSONLENS_EMAIL",
        "LESSONLENS_PASSWORD", "LESSONLENS_AGENT_CMD",
    ):
        monkeypatch.delenv(key, raising=False)


def _cfg(**kw):
    return load_config(env_file=os.path.join(ROOT, "does-not-exist"), **kw)


def _find(report, name):
    return next((r for r in report.rows if r[1] == name), None)


# --- report mechanics -----------------------------------------------------

def test_required_failure_sets_exit_state():
    report = doctor.Report()
    report.add(doctor.OK, "fine")
    assert report.failed is False
    report.add(doctor.FAIL, "broken")
    assert report.failed is True


def test_optional_failure_does_not_fail_the_run():
    report = doctor.Report()
    report.add(doctor.WARN, "optional", required=False)
    report.add(doctor.FAIL, "also optional", required=False)
    assert report.failed is False


# --- config ---------------------------------------------------------------

def test_missing_config_is_actionable():
    report = doctor.Report()
    assert doctor.check_config(report, _cfg(api_url="")) is False
    row = _find(report, "Configuration")
    assert row[0] == doctor.FAIL
    assert ".env" in row[3]


def test_complete_config_passes():
    report = doctor.Report()
    assert doctor.check_config(report, _cfg(api_url="https://h", email="e", password="p")) is True
    assert _find(report, "Configuration")[0] == doctor.OK


# --- connection diagnostics ----------------------------------------------

@pytest.mark.parametrize(
    "error,expected",
    [
        ("POST /api/login failed: HTTP 401 bad", "LESSONLENS_EMAIL"),
        ("POST /api/login failed: [Errno 111] Connection refused", "Nothing is listening"),
        ("POST /api/login failed: Name or service not known", "Cannot resolve"),
    ],
)
def test_login_failures_explain_the_cause(monkeypatch, error, expected):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def login(self, *a):
            raise ApiError(error)

    monkeypatch.setattr(doctor, "LessonLensClient", Boom)
    report = doctor.Report()
    assert doctor.check_connection(report, _cfg(api_url="https://h", email="e", password="p")) is None
    row = _find(report, "Hosted login")
    assert row[0] == doctor.FAIL
    assert expected in row[3]


def test_successful_login_returns_client(monkeypatch):
    class Fine:
        def __init__(self, *a, **k):
            pass

        def login(self, *a):
            pass

    monkeypatch.setattr(doctor, "LessonLensClient", Fine)
    report = doctor.Report()
    assert doctor.check_connection(report, _cfg(api_url="https://h", email="e", password="p")) is not None
    assert _find(report, "Hosted login")[0] == doctor.OK


# --- sessions -------------------------------------------------------------

def test_sessions_reports_pending_count():
    class C:
        def list_sessions(self):
            return [
                {"session_id": "a", "needs_summary": True},
                {"session_id": "b", "needs_summary": False},
            ]

    report = doctor.Report()
    pending = doctor.check_sessions(report, C())
    assert [p["session_id"] for p in pending] == ["a"]
    assert "1 still need a summary" in _find(report, "Sessions")[2]


def test_empty_server_warns_but_does_not_fail():
    class C:
        def list_sessions(self):
            return []

    report = doctor.Report()
    assert doctor.check_sessions(report, C()) == []
    assert _find(report, "Sessions")[0] == doctor.WARN
    assert report.failed is False


# --- agent command --------------------------------------------------------

def test_unset_agent_command_is_a_warning_not_a_failure():
    report = doctor.Report()
    doctor.check_agent_command(report, _cfg(), [], run_probe=False)
    row = _find(report, "Agent command")
    assert row[0] == doctor.WARN
    assert report.failed is False
    assert "prepare-only" in row[2]


def test_missing_binary_fails_with_fix(monkeypatch):
    monkeypatch.setenv("LESSONLENS_AGENT_CMD", "no-such-cli -p {session_id}")
    report = doctor.Report()
    doctor.check_agent_command(report, _cfg(), [], run_probe=False)
    row = _find(report, "Agent command")
    assert row[0] == doctor.FAIL
    assert "not on PATH" in row[2]
    assert report.failed is True


def test_resolvable_binary_passes(monkeypatch):
    monkeypatch.setenv("LESSONLENS_AGENT_CMD", "true {session_id}")
    report = doctor.Report()
    doctor.check_agent_command(report, _cfg(), [], run_probe=False)
    assert _find(report, "Agent command")[0] == doctor.OK


def test_template_without_placeholder_warns(monkeypatch):
    monkeypatch.setenv("LESSONLENS_AGENT_CMD", "true always-the-same")
    report = doctor.Report()
    doctor.check_agent_command(report, _cfg(), [], run_probe=False)
    warns = [r for r in report.rows if r[0] == doctor.WARN and "{session_id}" in r[3]]
    assert warns, "a template that ignores the session should be called out"


def test_probe_runs_the_command(monkeypatch):
    monkeypatch.setenv("LESSONLENS_AGENT_CMD", "true {session_id}")
    report = doctor.Report()
    doctor.check_agent_command(
        report, _cfg(), [{"session_id": "2026-03-05"}], run_probe=True
    )
    assert _find(report, "Agent probe")[0] == doctor.OK


def test_mcp2_is_reported_as_a_pin_problem(monkeypatch):
    """mcp 2.x removed mcp.server.fastmcp, which both servers are built on.

    An unbounded `mcp>=1.0` resolves to 2.x, so this must read as a version pin
    issue rather than a mystery ImportError.
    """
    import importlib.metadata as md

    monkeypatch.setattr(md, "version", lambda name: "2.0.0")
    report = doctor.Report()
    doctor.check_mcp(report, _cfg())
    row = _find(report, "Hosted MCP server")
    assert row[0] == doctor.FAIL
    assert "2.x removed" in row[2]
    assert "mcp>=1.2,<2" in row[3]


def test_dual_mcp_servers_warn_about_shared_tool_names(monkeypatch):
    """Both servers expose store_summary; the wrong one writes to the wrong DB."""
    monkeypatch.setenv("LESSONLENS_USER_EMAIL", "me@example.com")
    report = doctor.Report()
    doctor.check_mcp(report, _cfg())
    row = _find(report, "MCP server ambiguity")
    assert row is not None, "an active local server alongside hosted must be flagged"
    assert row[0] == doctor.WARN
    assert report.failed is False


def test_no_ambiguity_warning_when_only_hosted_configured(monkeypatch):
    monkeypatch.delenv("LESSONLENS_USER_EMAIL", raising=False)
    report = doctor.Report()
    doctor.check_mcp(report, _cfg())
    assert _find(report, "MCP server ambiguity") is None


def test_probe_failure_is_reported(monkeypatch):
    monkeypatch.setenv("LESSONLENS_AGENT_CMD", "false {session_id}")
    report = doctor.Report()
    doctor.check_agent_command(
        report, _cfg(), [{"session_id": "2026-03-05"}], run_probe=True
    )
    row = _find(report, "Agent probe")
    assert row[0] == doctor.FAIL
    assert "non-interactive" in row[3]

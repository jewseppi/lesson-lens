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


# --- LINE-side checks -----------------------------------------------------
# The server can't tell you about these, and they're what actually blocks a
# first `make update`.

def _fake_line_app(tmp_path):
    """A path layout that makes _line_app_present() report LINE as installed."""
    app = tmp_path / "Applications" / "LINE.app"
    app.mkdir(parents=True)
    return [str(app)]


def test_missing_export_lists_what_was_searched(tmp_path):
    report = doctor.Report()
    doctor.check_line_setup(
        report,
        export_dirs=[tmp_path / "nope"],
        image_dirs=[],
        app_paths=_fake_line_app(tmp_path),
    )
    row = _find(report, "LINE chat export")
    # A step you haven't taken yet, not a misconfiguration: on a fresh Mac there
    # is nothing to find until the first export, and calling that a warning made
    # a correct setup look broken.
    assert row[0] == doctor.TODO
    assert "Save chat history" in row[3]
    assert "nope" in row[3], "the searched paths must be shown"
    assert "--export-file" in row[3]
    assert report.failed is False, "a missing export is not a hard failure"


def test_missing_export_says_update_still_does_useful_work(tmp_path):
    report = doctor.Report()
    doctor.check_line_setup(
        report, export_dirs=[], image_dirs=[], app_paths=_fake_line_app(tmp_path)
    )
    row = _find(report, "LINE chat export")
    assert "still works" in row[3]


def test_no_line_app_names_the_real_problem(tmp_path):
    """Running somewhere LINE isn't — the container/server case."""
    report = doctor.Report()
    doctor.check_line_setup(
        report, export_dirs=[tmp_path / "nope"], image_dirs=[], app_paths=[]
    )
    row = _find(report, "LINE desktop app")
    assert row[0] == doctor.FAIL
    assert "not installed" in row[2]
    assert "has to" in row[3] and "Mac" in row[3]
    # Don't ask for an export that cannot exist here.
    assert _find(report, "LINE chat export") is None


def test_no_line_app_does_not_blame_encryption(tmp_path):
    report = doctor.Report()
    doctor.check_line_setup(report, export_dirs=[], image_dirs=[], app_paths=[])
    row = _find(report, "LINE image cache")
    assert "not installed here" in row[2]
    assert "encrypt" not in row[3], "wrong diagnosis when LINE simply isn't here"


def test_export_present_does_not_flag_missing_line_app(tmp_path):
    """An export in hand is proof enough; don't nag about the app bundle."""
    (tmp_path / "[LINE] Chat with Jessie.txt").write_text("2026.03.08 Sunday\n")
    report = doctor.Report()
    doctor.check_line_setup(
        report, export_dirs=[tmp_path], image_dirs=[], app_paths=[]
    )
    assert _find(report, "LINE desktop app") is None
    assert _find(report, "LINE chat export")[0] == doctor.OK


def test_todo_render_reports_setup_correct_with_steps_left(tmp_path, capsys):
    report = doctor.Report()
    doctor.check_line_setup(
        report, export_dirs=[], image_dirs=[], app_paths=_fake_line_app(tmp_path)
    )
    report.render()
    out = capsys.readouterr().out
    assert "Setup is wired up correctly" in out
    assert "LINE chat export" in out
    assert report.failed is False


def test_found_export_is_reported(tmp_path):
    export = tmp_path / "[LINE] Chat with Jessie.txt"
    export.write_text("2026.03.08 Sunday\n")
    report = doctor.Report()
    doctor.check_line_setup(report, export_dirs=[tmp_path], image_dirs=[])
    row = _find(report, "LINE chat export")
    assert row[0] == doctor.OK
    assert "Chat with Jessie" in row[2]


def test_unhinted_export_name_warns_but_is_still_used(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    report = doctor.Report()
    doctor.check_line_setup(report, export_dirs=[tmp_path], image_dirs=[])
    row = _find(report, "LINE chat export")
    assert row[0] == doctor.WARN
    assert "doesn't look like a LINE export" in row[2]


def test_image_cache_counted(tmp_path):
    (tmp_path / "hashed1").write_bytes(b"\xff\xd8\xff" + b"A" * 40)
    (tmp_path / "hashed2").write_bytes(b"\x89PNG\r\n\x1a\n" + b"B" * 40)
    (tmp_path / "notes.txt").write_bytes(b"plain text, not an image")
    report = doctor.Report()
    doctor.check_line_setup(report, export_dirs=[], image_dirs=[tmp_path])
    row = _find(report, "LINE image cache")
    assert row[0] == doctor.OK
    assert "2 image file(s)" in row[2]


def test_no_cache_dir_suggests_the_folder_fallback(tmp_path):
    report = doctor.Report()
    # Pin the app path: this must assert the same thing whether or not the
    # machine running the tests happens to have LINE installed.
    doctor.check_line_setup(
        report, export_dirs=[], image_dirs=[], app_paths=_fake_line_app(tmp_path)
    )
    row = _find(report, "LINE image cache")
    assert row[0] == doctor.TODO
    assert "--images-dir" in row[3]
    assert report.failed is False


def test_cache_dir_with_no_readable_images_explains_encryption(tmp_path):
    (tmp_path / "opaque.blob").write_bytes(b"not an image at all")
    report = doctor.Report()
    doctor.check_line_setup(report, export_dirs=[], image_dirs=[tmp_path])
    row = _find(report, "LINE image cache")
    assert row[0] == doctor.WARN
    assert "encrypted" in row[3]


def test_export_search_covers_sandboxed_line_container():
    """Mac App Store LINE is sandboxed; its Downloads is inside the container."""
    import line_mac_sync as sync

    paths = [str(p) for p in sync.default_export_dirs()]
    assert any("Containers/jp.naver.line.mac/Data/Downloads" in p for p in paths)
    assert any("com~apple~CloudDocs" in p for p in paths), "iCloud Desktop/Documents too"


def test_probe_failure_is_reported(monkeypatch):
    monkeypatch.setenv("LESSONLENS_AGENT_CMD", "false {session_id}")
    report = doctor.Report()
    doctor.check_agent_command(
        report, _cfg(), [{"session_id": "2026-03-05"}], run_probe=True
    )
    row = _find(report, "Agent probe")
    assert row[0] == doctor.FAIL
    assert "non-interactive" in row[3]


# --- local port agreement --------------------------------------------------

def test_app_and_updater_agree_on_the_local_port():
    """The app's default port and the updater's local URL must match.

    When these drifted (app on 5001, LESSONLENS_LOCAL_URL on 5000) `make doctor`
    reported "Connection refused" against a perfectly good local setup — a
    self-inflicted wrong diagnosis, which is the one thing a preflight must not do.
    """
    import app as app_module
    from lessonlens_config import DEFAULT_LOCAL_PORT, DEFAULT_LOCAL_URL

    assert app_module.DEFAULT_LOCAL_PORT == DEFAULT_LOCAL_PORT
    assert DEFAULT_LOCAL_URL.endswith(f":{app_module.DEFAULT_LOCAL_PORT}")


def test_connection_refused_names_the_command_that_fixes_it(monkeypatch):
    from lessonlens_config import Config

    def boom(self, email, password):
        raise doctor.ApiError("POST /api/login failed: Connection refused")

    monkeypatch.setattr(doctor.LessonLensClient, "login", boom)
    report = doctor.Report()
    cfg = Config(
        target="local", api_url="http://127.0.0.1:5001",
        email="me@example.com", password="pw",
    )
    doctor.check_connection(report, cfg)
    row = _find(report, "Hosted login")
    assert row[0] == doctor.FAIL
    assert "make serve" in row[3], "tell the user the command, not just the symptom"
    assert "LESSONLENS_LOCAL_URL" in row[3]


# --- empty env vars must not shadow the .env ------------------------------

def test_empty_env_var_falls_back_to_the_env_file(tmp_path, monkeypatch):
    """MCP client configs declare keys with empty placeholder values.

    Those reach the server process as real (empty) variables. Treating them as
    "already set" blocked the .env fallback, so the hosted MCP server reported
    missing credentials on a correctly configured machine.
    """
    from lessonlens_config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("LESSONLENS_API_URL=https://from-dot-env\n", encoding="utf-8")

    monkeypatch.setenv("LESSONLENS_API_URL", "")
    load_env_file(env_file)
    assert os.environ["LESSONLENS_API_URL"] == "https://from-dot-env"


def test_real_env_var_still_beats_the_env_file(tmp_path, monkeypatch):
    from lessonlens_config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("LESSONLENS_API_URL=https://from-dot-env\n", encoding="utf-8")

    monkeypatch.setenv("LESSONLENS_API_URL", "https://from-shell")
    load_env_file(env_file)
    assert os.environ["LESSONLENS_API_URL"] == "https://from-shell"


def test_mcp_config_ships_no_placeholder_env_blocks():
    """Regression guard for the config that caused the shadowing above."""
    import json

    config = json.loads(
        open(os.path.join(ROOT, ".mcp.json"), encoding="utf-8").read()
    )
    for name, server in config["mcpServers"].items():
        for key, value in (server.get("env") or {}).items():
            assert value, f"{name}.env.{key} is empty and will shadow the .env"


def test_failed_probe_surfaces_what_the_command_printed():
    """A bare exit code threw away the one line that says what to change."""
    class Result:
        returncode = 1
        stdout = ""
        stderr = "--dangerously-skip-permissions cannot be used with root/sudo privileges"

    hint = doctor._probe_failure_hint(Result())
    assert "dangerously-skip-permissions" in hint
    assert "allowedTools" in hint, "and still point at the usual fix"

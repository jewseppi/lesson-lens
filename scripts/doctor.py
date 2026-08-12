"""doctor.py — one command that tells you whether LessonLens is wired up right.

Run this before (or instead of) debugging a failed `make update`:

    make doctor            # check everything
    make doctor-agent      # also run your agent command against a probe session

It checks each link in the chain and, when something is wrong, prints the exact
fix rather than a stack trace:

    1. config resolution (target, URL, credentials)
    2. hosted reachability + login
    3. the sessions endpoint (and how many sessions still need summaries)
    4. restore points (the safety net is actually armed)
    5. the hosted MCP server (importable, configured, tools registered)
    6. the subscription-agent command (parses, binary exists, optionally runs)

Exit code is 0 when everything required passes, 1 otherwise, so it can gate a
scheduled run.

Standard library only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lessonlens_client import ApiError, LessonLensClient  # noqa: E402
from lessonlens_config import load_config  # noqa: E402

OK = "PASS"
WARN = "WARN"
FAIL = "FAIL"


class Report:
    """Collects check results; required failures drive the exit code."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []
        self.failed = False

    def add(self, status: str, name: str, detail: str = "", fix: str = "", required: bool = True) -> None:
        self.rows.append((status, name, detail, fix))
        if status == FAIL and required:
            self.failed = True

    def render(self) -> None:
        print()
        for status, name, detail, fix in self.rows:
            symbol = "x" if status == FAIL else ("!" if status == WARN else "+")
            print(f"[{symbol}] {status:4}  {name}")
            if detail:
                print(f"           {detail}")
            if fix:
                for line in fix.splitlines():
                    print(f"           -> {line}")
        print()
        if self.failed:
            print("Some required checks failed. Fix the items marked FAIL above, then re-run.")
        else:
            print("All required checks passed.")


def check_config(report: Report, cfg):
    problems = cfg.validate()
    if problems:
        report.add(
            FAIL,
            "Configuration",
            "; ".join(problems),
            "Copy .env.example to .env and fill in LESSONLENS_API_URL / "
            "LESSONLENS_EMAIL / LESSONLENS_PASSWORD.",
        )
        return False
    report.add(OK, "Configuration", f"target={cfg.target} url={cfg.api_url}")
    return True


def check_connection(report: Report, cfg):
    client = LessonLensClient(cfg.api_url, timeout=30)
    try:
        client.login(cfg.email, cfg.password)
    except ApiError as exc:
        message = str(exc)
        if "HTTP 401" in message or "HTTP 403" in message:
            fix = "Credentials rejected. Check LESSONLENS_EMAIL / LESSONLENS_PASSWORD."
        elif "Name or service not known" in message or "getaddrinfo" in message:
            fix = f"Cannot resolve {cfg.api_url}. Check the URL and your network."
        elif "Connection refused" in message:
            fix = (
                f"Nothing is listening at {cfg.api_url}. "
                "If target=local, start the app first (see README Deployment)."
            )
        else:
            fix = "Confirm the app is running and reachable at that URL."
        report.add(FAIL, "Hosted login", message[:200], fix)
        return None
    report.add(OK, "Hosted login", f"authenticated as {cfg.email}")
    return client


def check_sessions(report: Report, client):
    try:
        sessions = client.list_sessions()
    except ApiError as exc:
        report.add(FAIL, "Sessions endpoint", str(exc)[:200],
                   "The app is up but /api/sessions failed — check server logs.")
        return []

    if not sessions:
        report.add(
            WARN,
            "Sessions",
            "no sessions on the server yet",
            "Run `make update` once you have a LINE export to sync.",
            required=False,
        )
        return []

    pending = [s for s in sessions if s.get("needs_summary")]
    detail = f"{len(sessions)} session(s); {len(pending)} still need a summary"
    report.add(OK, "Sessions", detail)
    return pending


def check_restore_points(report: Report, client):
    try:
        data = client._request("GET", "/api/restore-points")
    except ApiError as exc:
        report.add(
            WARN,
            "Restore points",
            str(exc)[:160],
            "Safety snapshots may be unavailable — is the server running this branch's code?",
            required=False,
        )
        return
    points = data.get("restore_points", []) if isinstance(data, dict) else []
    retention = data.get("retention_days", "?") if isinstance(data, dict) else "?"
    report.add(
        OK,
        "Restore points",
        f"{len(points)} snapshot(s) held, {retention}-day retention — rollback is armed",
    )


def check_line_setup(report: Report, export_dirs=None, image_dirs=None):
    """Check the Mac side: is there an export to sync, and images to grab?

    This is the half of the chain the server cannot tell you about, and the half
    that actually blocks a first `make update`.
    """
    import line_mac_sync as sync

    export_dirs = export_dirs if export_dirs is not None else sync.default_export_dirs()
    candidates = sync.find_export_candidates(export_dirs)

    if not candidates:
        report.add(
            WARN,
            "LINE chat export",
            "no .txt export found in any candidate directory",
            "In LINE, right-click the chat -> export, then re-run. Searched:\n"
            + "\n".join(f"   {line}" for line in sync.searched_dirs_report(export_dirs))
            + "\nIf it landed elsewhere: make update ARGS=\"--export-file /path/to/export.txt\""
            "\nTo find it: mdfind -name '.txt' -onlyin ~ | head -20",
            required=False,
        )
    else:
        best = candidates[0]
        age_days = max(0, int((time.time() - best["mtime"]) / 86400))
        detail = (
            f"{best['path']} ({best['size'] // 1024} KB, {age_days}d old)"
            f"; {len(candidates)} candidate(s)"
        )
        if not best["name_hinted"]:
            report.add(
                WARN,
                "LINE chat export",
                detail + " — name doesn't look like a LINE export",
                "It will still be used, but confirm it is the right file "
                "(or pass --export-file explicitly).",
                required=False,
            )
        else:
            report.add(OK, "LINE chat export", detail)

    # Images. LINE's text export never contains image bytes, so this is where the
    # automated grab either works or needs the folder fallback.
    dirs = image_dirs if image_dirs is not None else sync.discover_cache_dirs(sync.default_cache_dirs())
    if not dirs:
        report.add(
            WARN,
            "LINE image cache",
            "no LINE media cache directory found",
            "Recent LINE versions encrypt local storage, so this can be expected.\n"
            "Save lesson images to a folder and use:\n"
            "  make update ARGS=\"--images-dir ~/Pictures/line-lessons\"",
            required=False,
        )
        return

    count = 0
    for _ in sync.iter_candidate_images(dirs, 0):
        count += 1
        if count >= 500:  # enough to prove the scan works; don't walk forever
            break
    if count:
        report.add(
            OK,
            "LINE image cache",
            f"{len(dirs)} cache dir(s), {count}{'+' if count >= 500 else ''} image file(s) visible",
        )
    else:
        report.add(
            WARN,
            "LINE image cache",
            f"{len(dirs)} cache dir(s) found but no readable images in them",
            "Likely encrypted storage. Use --images-dir with a folder where you "
            "save images instead.",
            required=False,
        )


def check_mcp(report: Report, cfg):
    try:
        import mcp  # noqa: F401
    except ImportError:
        report.add(
            FAIL,
            "Hosted MCP server",
            "the `mcp` package is not installed",
            "pip install 'mcp>=1.2,<2'   (then point your agent at the "
            "lessonlens-hosted entry in .mcp.json)",
        )
        return

    # mcp 2.x dropped mcp.server.fastmcp, which both servers are built on. Catch
    # the version explicitly so this reads as a pin problem, not a mystery.
    try:
        import importlib.metadata as _md

        version = _md.version("mcp")
    except Exception:
        version = "unknown"
    if version != "unknown" and version.split(".")[0].isdigit() and int(version.split(".")[0]) >= 2:
        report.add(
            FAIL,
            "Hosted MCP server",
            f"mcp {version} is installed, but 2.x removed mcp.server.fastmcp",
            "pip install 'mcp>=1.2,<2'   (api/requirements.txt pins this; an old "
            "environment resolved with `mcp>=1.0` will have picked up 2.x)",
        )
        return

    api_dir = str(Path(_SCRIPTS_DIR).parent / "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    try:
        # Importing the MCP stack can emit unrelated dependency warnings; they
        # would drown out the diagnostics this command exists to show.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import mcp_server_hosted  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on local install
        report.add(
            FAIL,
            "Hosted MCP server",
            f"{type(exc).__name__}: {exc}"[:200],
            "The server could not be imported. Check the `mcp` version provides "
            "mcp.server.fastmcp (mcp>=1.2,<2).",
        )
        return

    report.add(
        OK,
        "Hosted MCP server",
        f"imports cleanly (mcp {version}) — your agent can read/write the hosted app",
    )

    # Both servers expose 9 identically-named tools, store_summary among them. If
    # the local one is also active, an agent can write a summary to the local
    # SQLite instead of the hosted app and nobody notices.
    if os.environ.get("LESSONLENS_USER_EMAIL"):
        report.add(
            WARN,
            "MCP server ambiguity",
            "LESSONLENS_USER_EMAIL is set, so the LOCAL server (lessonlens) is "
            "configured too; it shares 9 tool names with lessonlens-hosted",
            "Prefer the lessonlens-hosted tools. Unset LESSONLENS_USER_EMAIL, or "
            "remove the `lessonlens` entry from .mcp.json, to remove the ambiguity.",
            required=False,
        )


def check_agent_command(report: Report, cfg, pending, run_probe: bool):
    if not cfg.has_agent_cmd:
        report.add(
            WARN,
            "Agent command",
            "LESSONLENS_AGENT_CMD is not set (agent mode is prepare-only)",
            "Optional. Set it to have `make update` generate summaries with your "
            "subscription CLI. See the presets in .env.example, e.g.\n"
            "LESSONLENS_AGENT_CMD='claude -p \"...{session_id}...\"'",
            required=False,
        )
        return

    try:
        argv = cfg.build_agent_command("PROBE-SESSION")
    except ValueError as exc:
        report.add(FAIL, "Agent command", str(exc), "Check LESSONLENS_AGENT_CMD quoting.")
        return

    if "{session_id}" not in cfg.agent_cmd:
        report.add(
            WARN,
            "Agent command",
            "the template has no {session_id} placeholder",
            "Every session would get the same prompt. Add {session_id} to the template.",
            required=False,
        )

    binary = argv[0]
    resolved = shutil.which(binary)
    if not resolved:
        report.add(
            FAIL,
            "Agent command",
            f"'{binary}' is not on PATH",
            "Install it, or use an absolute path in LESSONLENS_AGENT_CMD.",
        )
        return

    report.add(OK, "Agent command", f"{binary} -> {resolved}")

    if not run_probe:
        return

    if not pending:
        report.add(
            WARN,
            "Agent probe",
            "no session needs a summary, so there was nothing to probe",
            required=False,
        )
        return

    target = pending[0].get("session_id")
    probe_argv = cfg.build_agent_command(target)
    print(f"\nRunning agent probe for session {target}:\n  {' '.join(probe_argv)}\n")
    try:
        result = subprocess.run(probe_argv, cwd=str(Path(_SCRIPTS_DIR).parent), timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        report.add(FAIL, "Agent probe", str(exc)[:200], "The command could not be run.")
        return

    if result.returncode == 0:
        report.add(
            OK,
            "Agent probe",
            f"command exited 0 for {target} — verify the summary landed in the app",
        )
    else:
        report.add(
            FAIL,
            "Agent probe",
            f"command exited {result.returncode}",
            "Check the CLI's non-interactive flags; it may be waiting for input.",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose a LessonLens setup end to end."
    )
    parser.add_argument("--target", default=None, help="hosted (default) or local")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument(
        "--check-agent",
        action="store_true",
        help="Actually run LESSONLENS_AGENT_CMD once against a session that needs a summary",
    )
    parser.add_argument(
        "--skip-line",
        action="store_true",
        help="Skip the local LINE export/image checks (useful on a server)",
    )
    args = parser.parse_args(argv)

    cfg = load_config(
        target=args.target, api_url=args.api_url, email=args.email, password=args.password
    )
    report = Report()

    print("LessonLens doctor")
    print("=" * 60)

    if not check_config(report, cfg):
        report.render()
        return 1

    client = check_connection(report, cfg)
    pending = []
    if client is not None:
        pending = check_sessions(report, client)
        check_restore_points(report, client)

    if not args.skip_line:
        check_line_setup(report)
    check_mcp(report, cfg)
    check_agent_command(report, cfg, pending, run_probe=args.check_agent)

    report.render()
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())

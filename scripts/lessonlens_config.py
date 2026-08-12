"""lessonlens_config.py — one place that answers "which LessonLens am I talking to?"

Both the macOS updater (``scripts/line_mac_sync.py``) and the MCP server
(``api/mcp_server.py``) need the same answer: hosted or local, and with what
credentials. This module resolves that from environment variables, falling back
to a repo-root ``.env`` file.

Environment variables
---------------------
    LESSONLENS_TARGET       "hosted" (default) or "local"
    LESSONLENS_API_URL      Hosted base URL, e.g. https://lessons.example.com
    LESSONLENS_EMAIL        Hosted login email
    LESSONLENS_PASSWORD     Hosted login password
    LESSONLENS_DB_PATH      Local SQLite path (local target only)
    LESSONLENS_USER_EMAIL   Local user to operate as (local target only)
    LESSONLENS_AGENT_CMD    Command template for subscription-agent generation.
                            Unset (the default) means "prepare only" — nothing
                            is executed. Supports {session_id} substitution.

Stdlib only, so it imports cleanly in the MCP server's remote mode without the
Flask stack installed.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

TARGET_HOSTED = "hosted"
TARGET_LOCAL = "local"
VALID_TARGETS = (TARGET_HOSTED, TARGET_LOCAL)

# Where a locally-running LessonLens server listens by default.
DEFAULT_LOCAL_URL = "http://127.0.0.1:5000"


def repo_root() -> Path:
    """Repository root (this file lives in <root>/scripts/)."""
    return Path(__file__).resolve().parent.parent


def load_env_file(path: Path | str) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables always win, so a shell export overrides the
    file. Malformed lines and unreadable files are ignored. Accepts a str or a
    Path so callers don't have to care.
    """
    path = Path(path)
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    """Resolved LessonLens connection settings."""

    target: str = TARGET_HOSTED
    api_url: str = ""
    email: str = ""
    password: str = ""
    db_path: str = ""
    user_email: str = ""
    agent_cmd: str = ""
    _problems: list[str] = field(default_factory=list)

    # --- predicates -------------------------------------------------------

    @property
    def is_hosted(self) -> bool:
        return self.target == TARGET_HOSTED

    @property
    def is_local(self) -> bool:
        return self.target == TARGET_LOCAL

    @property
    def has_agent_cmd(self) -> bool:
        """True when a subscription-agent command is configured.

        When False the agent generation mode is 'prepare only' — the updater
        reports which sessions need summaries and executes nothing.
        """
        return bool(self.agent_cmd.strip())

    # --- validation -------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means usable.

        Both targets are reached over HTTP — 'local' just means a LessonLens
        server running on this machine — so the same three settings are needed
        either way. Only the default URL differs (see :func:`load_config`).
        """
        problems = list(self._problems)
        missing = [
            name
            for name, value in (
                ("LESSONLENS_API_URL", self.api_url),
                ("LESSONLENS_EMAIL", self.email),
                ("LESSONLENS_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            where = "Hosted" if self.is_hosted else "Local"
            problems.append(
                f"{where} target needs " + ", ".join(missing)
                + " (set them in the environment or the repo's .env)."
            )
        return problems

    # --- agent command ----------------------------------------------------

    def build_agent_command(self, session_id: str) -> list[str]:
        """Render the configured agent command for one session.

        ``{session_id}`` in the template is substituted. The template is split
        with shlex so it stays a plain argv list — no shell is involved, so an
        odd session id cannot inject additional commands.
        """
        if not self.has_agent_cmd:
            raise ValueError("No LESSONLENS_AGENT_CMD configured")
        parts = shlex.split(self.agent_cmd)
        return [p.replace("{session_id}", session_id) for p in parts]


def load_config(
    *,
    target: str | None = None,
    api_url: str | None = None,
    email: str | None = None,
    password: str | None = None,
    env_file: Path | str | None = None,
) -> Config:
    """Resolve configuration from explicit args, then environment, then .env.

    Explicit arguments (typically CLI flags) always win.
    """
    load_env_file(env_file if env_file is not None else repo_root() / ".env")

    problems: list[str] = []
    raw_target = (target or os.environ.get("LESSONLENS_TARGET") or TARGET_HOSTED).strip().lower()
    if raw_target not in VALID_TARGETS:
        problems.append(
            f"Unknown LESSONLENS_TARGET {raw_target!r}; expected one of {', '.join(VALID_TARGETS)}. "
            f"Falling back to {TARGET_HOSTED}."
        )
        raw_target = TARGET_HOSTED

    resolved_api = (api_url or os.environ.get("LESSONLENS_API_URL") or "").strip().rstrip("/")
    if raw_target == TARGET_LOCAL and not resolved_api:
        # A locally-running LessonLens server is still reached over HTTP.
        resolved_api = (
            os.environ.get("LESSONLENS_LOCAL_URL") or DEFAULT_LOCAL_URL
        ).strip().rstrip("/")

    return Config(
        target=raw_target,
        api_url=resolved_api,
        email=(email or os.environ.get("LESSONLENS_EMAIL") or "").strip(),
        password=password or os.environ.get("LESSONLENS_PASSWORD") or "",
        db_path=(os.environ.get("LESSONLENS_DB_PATH") or "").strip(),
        user_email=(os.environ.get("LESSONLENS_USER_EMAIL") or "").strip(),
        agent_cmd=os.environ.get("LESSONLENS_AGENT_CMD") or "",
        _problems=problems,
    )

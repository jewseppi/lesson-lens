"""LessonLens MCP Server (hosted) — agent tools against a REMOTE instance.

This is the sibling of ``api/mcp_server.py``. Both expose the same tool names;
they differ only in where the data lives:

    mcp_server.py         → local SQLite (imports the Flask app)
    mcp_server_hosted.py  → hosted REST API over HTTP (this file)

Why this exists
---------------
Generating lesson summaries through a provider API costs money per token. If you
already pay for a coding-agent subscription (Claude Code, Copilot, Codex CLI),
the agent itself can read the transcript and author the lesson package — that is
what ``store_summary`` is for. Previously that only worked against a local DB,
so you had to run the app locally and sync up afterwards. This server removes
that step: an authenticated CLI agent reads from and writes to the hosted app
directly, with no local database and no provider API key anywhere.

Configuration (environment variables)
-------------------------------------
    LESSONLENS_API_URL      Hosted base URL (required)
    LESSONLENS_EMAIL        Hosted login email (required)
    LESSONLENS_PASSWORD     Hosted login password (required)
    MCP_TRANSPORT           "stdio" (default) or "sse"

Usage:
    python api/mcp_server_hosted.py

Standard library only (plus ``mcp``) — deliberately no Flask/Pillow import, so
this runs on a machine that only has the agent CLI installed.
"""
# NOTE: deliberately no `from __future__ import annotations`. FastMCP builds each
# tool's input schema by inspecting real annotation objects; stringized
# annotations make it fail with "issubclass() arg 1 must be a class".
# api/mcp_server.py omits it for the same reason.
import functools
import json
import os
import sys
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_SCRIPTS_DIR = os.path.join(_PROJECT_ROOT, "scripts")
for _p in (_SCRIPTS_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lessonlens_client import ApiError, LessonLensClient  # noqa: E402
from lessonlens_config import load_config  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("lessonlens-hosted")

_CONFIG = load_config(target="hosted")
_CLIENT: Optional[LessonLensClient] = None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _client() -> LessonLensClient:
    """Return a logged-in client, or raise ApiError with an actionable message."""
    global _CLIENT
    problems = _CONFIG.validate()
    if problems:
        raise ApiError(" ".join(problems))
    if _CLIENT is None:
        _CLIENT = LessonLensClient(_CONFIG.api_url)
    _CLIENT.ensure_login(_CONFIG.email, _CONFIG.password)
    return _CLIENT


def _guard(fn):
    """Turn transport/API failures into readable tool output instead of tracebacks.

    Uses functools.wraps so ``__wrapped__`` is set: FastMCP builds each tool's
    input schema from ``inspect.signature()``, which follows ``__wrapped__``.
    Without it every tool would be advertised as taking no arguments.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            return f"Error: {exc}"

    return wrapper


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_session_line(s: dict, idx: int) -> str:
    flags = []
    if s.get("needs_summary"):
        flags.append("NEEDS SUMMARY")
    elif s.get("has_summary"):
        flags.append("has summary")
    if s.get("summary_stale"):
        flags.append("STALE")
    if s.get("is_archived"):
        flags.append("archived")
    suffix = f"  [{', '.join(flags)}]" if flags else ""
    return (
        f"{idx}. {s.get('session_id')}  ({s.get('date')} "
        f"{s.get('start_time', '')}-{s.get('end_time', '')})  "
        f"{s.get('message_count', 0)} msgs, "
        f"{s.get('lesson_content_count', 0)} lesson{suffix}"
    )


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@mcp.tool()
@_guard
def list_sessions(include_archived: bool = False, needs_summary_only: bool = False) -> str:
    """List lesson sessions on the hosted LessonLens instance.

    Args:
        include_archived: Include sessions you have archived.
        needs_summary_only: Only sessions that still need a summary — the usual
            starting point when you are generating summaries as the agent.
    """
    sessions = _client().list_sessions()
    if not include_archived:
        sessions = [s for s in sessions if not s.get("is_archived")]
    if needs_summary_only:
        sessions = [s for s in sessions if s.get("needs_summary")]
    if not sessions:
        return "No matching sessions." if needs_summary_only else "No sessions found."

    lines = [_fmt_session_line(s, i + 1) for i, s in enumerate(sessions)]
    pending = sum(1 for s in sessions if s.get("needs_summary"))
    header = f"{len(sessions)} session(s) on {_CONFIG.api_url}"
    if pending:
        header += f" — {pending} still need a summary"
    return header + ":\n" + "\n".join(lines)


@mcp.tool()
@_guard
def get_session(session_id: str, include_messages: bool = True, max_messages: int = 200) -> str:
    """Fetch one session's detail, including its transcript.

    This is the input you summarize: read it, then call store_summary.
    """
    data = _client().get_session(session_id)
    if not data:
        return f"Session '{session_id}' not found."

    out = [
        f"Session {data.get('session_id')} ({data.get('date')})",
        f"  time: {data.get('start_time')}-{data.get('end_time')}",
        f"  messages: {data.get('message_count')} "
        f"(lesson content: {data.get('lesson_content_count')})",
    ]
    if data.get("topics"):
        out.append(f"  topics: {', '.join(data['topics'])}")

    if include_messages:
        messages = data.get("messages", []) or []
        shown = messages[:max_messages]
        out.append(f"\nTranscript ({len(shown)} of {len(messages)} messages):")
        for msg in shown:
            role = msg.get("speaker_role", "unknown")
            text = (msg.get("text_raw") or msg.get("text_normalized") or "").strip()
            out.append(f"  [{msg.get('time', '')}] {role}: {text}")
        if len(messages) > len(shown):
            out.append(f"  ... {len(messages) - len(shown)} more (raise max_messages)")
    return "\n".join(out)


@mcp.tool()
@_guard
def get_session_summary(session_id: str) -> str:
    """Return the stored lesson package for a session, as JSON."""
    data = _client().get_summary(session_id)
    if not data or data.get("error"):
        return f"No summary stored for '{session_id}'."
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
@_guard
def get_retrieval_context(session_id: str) -> str:
    """Prior vocabulary/corrections context for a session.

    Read this before writing a summary so new material stays consistent with
    what earlier lessons already introduced.
    """
    data = _client().get_retrieval_context(session_id)
    if not data:
        return f"No retrieval context available for '{session_id}'."
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
@_guard
def get_session_attachments(session_id: str) -> str:
    """List images attached to a session (worksheets, whiteboard photos)."""
    data = _client().get_session_attachments(session_id)
    items = data.get("attachments", data) if isinstance(data, dict) else data
    if not items:
        return f"No attachments for '{session_id}'."
    return json.dumps(items, ensure_ascii=False, indent=2)


@mcp.tool()
@_guard
def search_sessions(query: str) -> str:
    """Find sessions whose id, date, or topics contain the query string."""
    needle = query.strip().lower()
    if not needle:
        return "Provide a non-empty query."
    matches = [
        s
        for s in _client().list_sessions()
        if needle in str(s.get("session_id", "")).lower()
        or needle in str(s.get("date", "")).lower()
        or any(needle in str(t).lower() for t in s.get("topics", []) or [])
    ]
    if not matches:
        return f"No sessions matched '{query}'."
    return f"{len(matches)} match(es):\n" + "\n".join(
        _fmt_session_line(s, i + 1) for i, s in enumerate(matches)
    )


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

# The hosted import endpoint enforces these; validating here turns a raw HTTP
# 400 into an actionable message for the agent.
_REQUIRED_KEYS = {
    "schema_version",
    "lesson_id",
    "lesson_date",
    "title",
    "summary",
    "key_sentences",
    "vocabulary",
}


@mcp.tool()
@_guard
def store_summary(
    session_id: str,
    lesson_data_json: str,
    provider: str = "claude-agent",
    model: str = "",
) -> str:
    """Store an agent-authored lesson summary on the hosted instance.

    Use this when you (the AI agent) have read the transcript and produced the
    lesson-data.v1 JSON yourself. This is the no-API-key path: nothing is billed
    to a provider, the work is done by your subscription.

    The payload must satisfy the hosted importer's rules:
      * schema_version == "lesson-data.v1"
      * lesson_date == session_id
      * plus lesson_id, title, summary, key_sentences, vocabulary

    Args:
        session_id: Session to attach the summary to.
        lesson_data_json: Full lesson-data.v1 JSON string.
        provider: Metadata label for who generated it. Default "claude-agent".
        model: Metadata label for the model. Optional.
    """
    try:
        lesson_data = json.loads(lesson_data_json)
    except json.JSONDecodeError as exc:
        return f"Error: lesson_data_json is not valid JSON — {exc}"
    if not isinstance(lesson_data, dict):
        return "Error: lesson_data_json must be a JSON object."

    missing = _REQUIRED_KEYS - set(lesson_data)
    if missing:
        return f"Error: missing required keys: {', '.join(sorted(missing))}"

    schema = lesson_data.get("schema_version")
    if schema != "lesson-data.v1":
        return (
            f"Error: schema_version must be 'lesson-data.v1' (got {schema!r}). "
            "The hosted importer rejects anything else."
        )
    lesson_date = lesson_data.get("lesson_date")
    if lesson_date != session_id:
        return (
            f"Error: lesson_date must equal the session id. "
            f"Got lesson_date={lesson_date!r} for session {session_id!r}. "
            "Set lesson_date to the session id and retry."
        )

    use_model = model or lesson_data.get("generation_meta", {}).get("model", "agent")
    _client().import_summary(session_id, lesson_data, provider, use_model)
    return (
        f"Stored summary for {session_id} on {_CONFIG.api_url} via {provider}/{use_model}.\n"
        f"  Vocabulary: {len(lesson_data.get('vocabulary', []))} items\n"
        f"  Key sentences: {len(lesson_data.get('key_sentences', []))}\n"
        "Use get_session_summary to verify."
    )


@mcp.tool()
@_guard
def generate_summary(session_id: str, provider: str = "", model: str = "") -> str:
    """Ask the HOSTED server to generate a summary with a provider API key.

    Prefer store_summary: this path bills a provider per token, and requires the
    server to hold an API key. Use it only when you deliberately want
    provider-backed generation rather than doing the work yourself.
    """
    result = _client().generate(session_id, provider or None, model or None)
    return (
        f"Generated summary for {session_id}: {result.get('title', '(untitled)')}\n"
        f"  Vocabulary: {len(result.get('vocabulary', []))} items"
    )


@mcp.tool()
@_guard
def add_annotation(
    session_id: str,
    annotation_type: str,
    target_type: str = "session",
    target_id: str = "",
    content: str = "",
) -> str:
    """Attach a correction/reclassify/note annotation to a session."""
    payload = {
        "annotation_type": annotation_type,
        "target_type": target_type,
        "target_id": target_id,
        "content": content,
    }
    result = _client().add_annotation(session_id, payload)
    return f"Annotation added to {session_id}: {json.dumps(result, ensure_ascii=False)}"


@mcp.tool()
@_guard
def list_annotations(session_id: str) -> str:
    """List annotations recorded against a session."""
    data = _client().list_annotations(session_id)
    items = data.get("annotations", data) if isinstance(data, dict) else data
    if not items:
        return f"No annotations for '{session_id}'."
    return json.dumps(items, ensure_ascii=False, indent=2)


@mcp.tool()
def lesson_data_schema() -> str:
    """Return the lesson-data.v1 skeleton to fill in when authoring a summary."""
    return json.dumps(
        {
            "schema_version": "lesson-data.v1",
            "lesson_id": "lesson-<session-id>",
            "lesson_date": "<must equal the session id>",
            "title": "",
            "source_session_ids": ["<session-id>"],
            "language_mode": {
                "script": "traditional",
                "pinyin_policy": "every_line",
                "translation_language": "english",
            },
            "summary": {"overview": "", "usage_notes": "", "short_recap": ""},
            "key_sentences": [],
            "vocabulary": [],
            "corrections": [],
            "review": {
                "flashcards": [],
                "fill_blank": [],
                "translation_drills": [],
                "quiz": [],
            },
            "generation_meta": {
                "provider": "claude-agent",
                "model": "agent",
                "prompt_version": "v1-agent",
            },
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    problems = _CONFIG.validate()
    if problems:
        print("LessonLens hosted MCP server is not configured:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        sys.exit(2)
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)

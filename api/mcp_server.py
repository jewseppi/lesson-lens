"""
LessonLens MCP Server — gives AI agents native tools for lesson analysis.

Usage:
    python api/mcp_server.py              # stdio transport (Claude Code, Cursor)
    MCP_TRANSPORT=sse python api/mcp_server.py  # HTTP+SSE transport

Configuration (environment variables):
    LESSONLENS_USER_EMAIL   — Email of the user to operate as (required)
    LESSONLENS_DB_PATH      — Path to SQLite database (default: api/lessonlens.db)
    MCP_TRANSPORT           — "stdio" (default) or "sse"
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# Path setup — ensure api/ and scripts/ are importable
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_SCRIPTS_DIR = os.path.join(_PROJECT_ROOT, "scripts")

for p in [_THIS_DIR, _SCRIPTS_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Override DB_PATH before importing app helpers
import app as _app_module  # noqa: E402

_db_path = os.environ.get(
    "LESSONLENS_DB_PATH",
    os.path.join(_PROJECT_ROOT, "api", "lessonlens.db"),
)
_app_module.DB_PATH = _db_path

from app import (  # noqa: E402
    get_db,
    init_db,
    _load_latest_completed_run,
    _load_sessions_payload,
    _generate_summary_for_session,
    _retrieve_context_for_session,
    build_retrieval_context_block,
    _record_feedback_memory,
    _index_retrieval_items,
    _load_corrections_for_session,
    _load_generator_config,
    _validate_provider_credentials,
    _store_lesson_summary,
    _check_generation_policy,
)

from mcp.server.fastmcp import FastMCP  # noqa: E402

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------
mcp = FastMCP("lessonlens")

USER_EMAIL = os.environ.get("LESSONLENS_USER_EMAIL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_user(conn):
    """Resolve the configured user from LESSONLENS_USER_EMAIL."""
    if not USER_EMAIL:
        return None, "LESSONLENS_USER_EMAIL environment variable not set."
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (USER_EMAIL,)
    ).fetchone()
    if not row:
        return None, f"User '{USER_EMAIL}' not found in the database."
    status = row["status"] if "status" in row.keys() else "active"
    if status != "active":
        return None, f"User account is {status}."
    return row, None


def _require_run(conn, user_id):
    """Load the latest completed parse run, or return an error string."""
    run = _load_latest_completed_run(conn, user_id)
    if not run:
        return None, "No parsed data yet. Upload and parse a chat export first."
    return run, None


def _fmt_session_line(s, idx, has_summary, is_stale):
    """Format one session as a single summary line."""
    date = s.get("date", s.get("session_id", "?"))
    mc = s.get("message_count", 0)
    tc = s.get("teacher_message_count", 0)
    sc = s.get("student_message_count", 0)
    topics_raw = s.get("topics") or s.get("topics_json")
    if isinstance(topics_raw, str):
        try:
            topics_raw = json.loads(topics_raw)
        except Exception:
            topics_raw = []
    topics = ", ".join(topics_raw[:4]) if topics_raw else "—"

    summary_badge = "—"
    if has_summary and is_stale:
        summary_badge = "yes (stale)"
    elif has_summary:
        summary_badge = "yes"

    return f"{idx}. {date} | {mc} msgs (T:{tc} S:{sc}) | Summary: {summary_badge} | Topics: {topics}"


# ---------------------------------------------------------------------------
# Session tools
# ---------------------------------------------------------------------------
@mcp.tool()
def list_sessions(include_archived: bool = False) -> str:
    """List all lesson sessions with dates, message counts, and summary status.

    Use this first to see what sessions are available. Returns session_id values
    you can pass to get_session, get_session_summary, generate_summary, etc.

    Args:
        include_archived: Include archived sessions. Default False.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        # Load session rows from DB
        rows = conn.execute(
            "SELECT * FROM sessions WHERE run_id = ? AND user_id = ? ORDER BY date DESC",
            (run["run_id"], user["id"]),
        ).fetchall()

        # Check which have summaries (and if stale)
        summary_map = {}
        for r in conn.execute(
            "SELECT session_id, created_at FROM lesson_summaries WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall():
            sid = r["session_id"]
            if sid not in summary_map:
                summary_map[sid] = r["created_at"]

        # Load sessions.json for filtering
        try:
            sessions_by_id = _load_sessions_payload(run)
        except FileNotFoundError:
            sessions_by_id = {}

        lines = []
        total_with_summary = 0
        total_stale = 0
        for row in rows:
            sid = row["session_id"]
            is_archived = bool(row["is_archived"]) if "is_archived" in row.keys() else False
            if is_archived and not include_archived:
                continue

            has_summary = sid in summary_map
            is_stale = False
            if has_summary:
                total_with_summary += 1
                # Check for unapplied corrections after summary
                stale_check = conn.execute(
                    """SELECT COUNT(*) as cnt FROM annotations
                       WHERE user_id = ? AND session_id = ? AND status = 'active'
                       AND annotation_type IN ('correction', 'reclassify')
                       AND created_at > ?""",
                    (user["id"], sid, summary_map[sid]),
                ).fetchone()
                if stale_check and stale_check["cnt"] > 0:
                    is_stale = True
                    total_stale += 1

            session_data = sessions_by_id.get(sid, {})
            session_data["date"] = row["date"]
            session_data["session_id"] = sid
            session_data["message_count"] = row["message_count"]
            tc = row["teacher_message_count"] if "teacher_message_count" in row.keys() else 0
            sc = row["student_message_count"] if "student_message_count" in row.keys() else 0
            session_data["teacher_message_count"] = tc
            session_data["student_message_count"] = sc
            topics_json = row["topics_json"] if "topics_json" in row.keys() else "[]"
            session_data["topics_json"] = topics_json

            lines.append(_fmt_session_line(session_data, len(lines) + 1, has_summary, is_stale))

        header = f"Found {len(lines)} sessions ({total_with_summary} with summaries"
        if total_stale:
            header += f", {total_stale} stale"
        header += "):\n"

        if not lines:
            return "No sessions found. Upload and parse a chat export first."
        return header + "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def get_session(session_id: str, include_messages: bool = True, max_messages: int = 100) -> str:
    """Get detailed session data including the lesson conversation transcript.

    Use this after list_sessions to examine a specific lesson. Returns metadata
    and the full message transcript with speaker roles and timestamps.

    Args:
        session_id: The session ID from list_sessions (e.g. "2025-01-15").
        include_messages: Whether to include the message transcript. Default True.
        max_messages: Maximum messages to return if transcript is long. Default 100.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        sessions_by_id = _load_sessions_payload(run)
        session_data = sessions_by_id.get(session_id)
        if not session_data:
            return f"Error: Session '{session_id}' not found."

        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user["id"]),
        ).fetchone()

        date = row["date"] if row else session_data.get("date", "?")
        mc = row["message_count"] if row else len(session_data.get("messages", []))

        out = [
            f"Session: {session_id}",
            f"Date: {date}",
            f"Messages: {mc}",
        ]

        topics_json = row["topics_json"] if row and "topics_json" in row.keys() else "[]"
        try:
            topics = json.loads(topics_json) if isinstance(topics_json, str) else topics_json
        except Exception:
            topics = []
        if topics:
            out.append(f"Topics: {', '.join(topics)}")

        # Attachments
        if row:
            att_rows = conn.execute(
                """SELECT a.original_filename, a.stored_filename, a.mime_type, sa.match_confidence
                   FROM session_attachments sa
                   JOIN attachments a ON sa.attachment_id = a.id
                   WHERE sa.session_id = ? AND sa.user_id = ?
                   ORDER BY a.id""",
                (row["id"], user["id"]),
            ).fetchall()
            if att_rows:
                out.append(f"\nAttachments: {len(att_rows)}")
                for a in att_rows:
                    att_path = os.path.join(_PROJECT_ROOT, "attachments", a["stored_filename"])
                    out.append(f"  - {a['original_filename']} ({a['mime_type']}, {a['match_confidence']}) path: {att_path}")

        if include_messages:
            messages = session_data.get("messages", [])
            out.append(f"\n--- Transcript ({len(messages)} messages) ---")
            for msg in messages[:max_messages]:
                time = msg.get("time", "")
                role = msg.get("speaker_role", "?")
                text = msg.get("text_normalized") or msg.get("text_raw", "")
                mtype = msg.get("message_type", "")
                prefix = f"[{time}] {role.capitalize()}"
                if mtype and mtype not in ("lesson-content", "other"):
                    prefix += f" ({mtype})"
                out.append(f"{prefix}: {text}")
            if len(messages) > max_messages:
                out.append(f"\n... {len(messages) - max_messages} more messages (use max_messages to see all)")

        return "\n".join(out)
    finally:
        conn.close()


@mcp.tool()
def get_session_summary(session_id: str) -> str:
    """Get the AI-generated lesson summary for a session.

    Returns vocabulary, key sentences, corrections, and study materials
    (flashcards, quizzes, drills). Use generate_summary first if no summary exists.

    Args:
        session_id: The session to get the summary for.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        row = conn.execute(
            """SELECT * FROM lesson_summaries
               WHERE user_id = ? AND session_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (user["id"], session_id),
        ).fetchone()
        if not row:
            return f"No summary for session '{session_id}'. Use generate_summary to create one."

        data = json.loads(row["lesson_data_json"])
        out = [
            f"Summary for {session_id}",
            f"Provider: {row['provider']}/{row['model']}",
            f"Generated: {row['created_at']}",
        ]

        # Overview
        summary_section = data.get("summary", {})
        if summary_section.get("overview"):
            out.append(f"\n## Overview\n{summary_section['overview']}")
        if summary_section.get("usage_notes"):
            out.append(f"\n## Usage Notes\n{summary_section['usage_notes']}")

        # Vocabulary
        vocab = data.get("vocabulary", [])
        if vocab:
            out.append(f"\n## Vocabulary ({len(vocab)} items)")
            for v in vocab:
                term = v.get("term_zh") or v.get("term", "")
                pinyin = v.get("pinyin", "")
                en = v.get("en", "")
                pos = v.get("pos_or_type") or v.get("pos", "")
                out.append(f"  - {term} ({pinyin}) [{pos}] — {en}")

        # Key sentences
        sentences = data.get("key_sentences", [])
        if sentences:
            out.append(f"\n## Key Sentences ({len(sentences)})")
            for s in sentences:
                zh = s.get("zh", "")
                pinyin = s.get("pinyin", "")
                en = s.get("en", "")
                out.append(f"  - {zh}\n    {pinyin}\n    {en}")

        # Corrections
        corrections = data.get("corrections", [])
        if corrections:
            out.append(f"\n## Corrections ({len(corrections)})")
            for c in corrections:
                orig = c.get("learner_original") or c.get("student_said", "")
                fix = c.get("teacher_correction") or c.get("correct_form", "")
                reason = c.get("reason") or c.get("explanation", "")
                out.append(f"  - {orig} -> {fix}\n    Reason: {reason}")

        # Study materials
        review = data.get("review", {})
        flashcards = review.get("flashcards", [])
        if flashcards:
            out.append(f"\n## Flashcards ({len(flashcards)})")
            for fc in flashcards[:10]:
                out.append(f"  Q: {fc.get('front', '')}\n  A: {fc.get('back', '')}")

        quizzes = review.get("quiz", [])
        if quizzes:
            out.append(f"\n## Quiz ({len(quizzes)} questions)")
            for q in quizzes[:5]:
                out.append(f"  Q: {q.get('question', '')}")
                for i, opt in enumerate(q.get("options", [])):
                    marker = "*" if i == q.get("correct_index") else " "
                    out.append(f"    {marker} {opt}")

        return "\n".join(out)
    finally:
        conn.close()


@mcp.tool()
def search_sessions(query: str) -> str:
    """Search sessions by topic keyword, date, or content text.

    Searches session topics and dates. Returns matching sessions in the same
    format as list_sessions.

    Args:
        query: Search term — a topic keyword (e.g. "food"), date fragment
               (e.g. "2025-01"), or content text.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        q_lower = query.lower()

        rows = conn.execute(
            "SELECT * FROM sessions WHERE run_id = ? AND user_id = ? ORDER BY date DESC",
            (run["run_id"], user["id"]),
        ).fetchall()

        summary_set = {
            r["session_id"]
            for r in conn.execute(
                "SELECT DISTINCT session_id FROM lesson_summaries WHERE user_id = ?",
                (user["id"],),
            ).fetchall()
        }

        matches = []
        for row in rows:
            sid = row["session_id"]
            date = row["date"] or ""
            topics_json = row["topics_json"] if "topics_json" in row.keys() else "[]"
            try:
                topics = json.loads(topics_json) if isinstance(topics_json, str) else (topics_json or [])
            except Exception:
                topics = []

            topics_text = " ".join(t.lower() for t in topics)
            if q_lower in date.lower() or q_lower in topics_text or q_lower in sid.lower():
                has_summary = sid in summary_set
                d = {"date": date, "session_id": sid, "message_count": row["message_count"],
                     "teacher_message_count": row["teacher_message_count"] if "teacher_message_count" in row.keys() else 0,
                     "student_message_count": row["student_message_count"] if "student_message_count" in row.keys() else 0,
                     "topics_json": topics_json}
                matches.append(_fmt_session_line(d, len(matches) + 1, has_summary, False))

        if not matches:
            return f"No sessions matching '{query}'."
        return f"Found {len(matches)} sessions matching '{query}':\n\n" + "\n".join(matches)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Generation tools
# ---------------------------------------------------------------------------
@mcp.tool()
def generate_summary(session_id: str, provider: str = "", model: str = "") -> str:
    """Generate an AI lesson summary for a session using an LLM.

    Analyzes the lesson transcript and produces vocabulary, grammar points,
    corrections, and study exercises. Takes 10-30 seconds depending on the model.

    Args:
        session_id: The session to summarize.
        provider: LLM provider (openai, anthropic, gemini, ollama). Uses config default if empty.
        model: Model name (e.g. "gpt-4o", "qwen2.5:7b"). Uses config default if empty.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        session_row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user["id"]),
        ).fetchone()
        if not session_row:
            return f"Error: Session '{session_id}' not found."

        sessions_by_id = _load_sessions_payload(run)
        session_data = sessions_by_id.get(session_id)
        if not session_data:
            return f"Error: Session data not found for '{session_id}'."

        try:
            lesson_data, use_provider, use_model, policy_action, policy_msg = (
                _generate_summary_for_session(
                    conn, user, run, session_row, session_data,
                    provider_override=provider or None,
                    model_override=model or None,
                )
            )
        except ValueError as e:
            return f"Error: {e}"

        # Store the summary
        _store_lesson_summary(
            conn, session_row, run, user["id"],
            use_provider, use_model, lesson_data, run["output_dir"],
        )

        # Index retrieval items
        _index_retrieval_items(conn, user["id"], session_id, lesson_data)
        conn.commit()

        vocab_count = len(lesson_data.get("vocabulary", []))
        corr_count = len(lesson_data.get("corrections", []))
        sent_count = len(lesson_data.get("key_sentences", []))

        result = f"Summary generated for {session_id} using {use_provider}/{use_model}.\n"
        result += f"  Vocabulary: {vocab_count} items\n"
        result += f"  Key sentences: {sent_count}\n"
        result += f"  Corrections: {corr_count}\n"
        if policy_action == "warn" and policy_msg:
            result += f"\n  Warning: {policy_msg}"
        result += "\nUse get_session_summary to view the full summary."
        return result
    finally:
        conn.close()


@mcp.tool()
def store_summary(session_id: str, lesson_data_json: str, provider: str = "claude-agent", model: str = "") -> str:
    """Store an agent-generated lesson summary directly into the database.

    Use this when you (the AI agent) have analyzed the session transcript and
    attached images yourself and produced the lesson-data.v1 JSON. This bypasses
    the need for external LLM API keys.

    The lesson_data_json must follow the lesson-data.v1 schema with these top-level keys:
    schema_version, lesson_id, lesson_date, title, source_session_ids, language_mode,
    summary, key_sentences, vocabulary, corrections, review, generation_meta.

    Args:
        session_id: The session to store the summary for.
        lesson_data_json: The full lesson-data.v1 JSON string.
        provider: Provider name for metadata. Default "claude-agent".
        model: Model name for metadata. Default empty (auto-detected).
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        session_row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user["id"]),
        ).fetchone()
        if not session_row:
            return f"Error: Session '{session_id}' not found."

        try:
            lesson_data = json.loads(lesson_data_json)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON — {e}"

        # Validate required keys
        required = {"schema_version", "lesson_id", "lesson_date", "title", "vocabulary", "key_sentences", "summary"}
        missing = required - set(lesson_data.keys())
        if missing:
            return f"Error: Missing required keys: {', '.join(sorted(missing))}"

        use_model = model or lesson_data.get("generation_meta", {}).get("model", "agent")

        _store_lesson_summary(
            conn, session_row, run, user["id"],
            provider, use_model, lesson_data, None,
        )

        # Index retrieval items for future context
        _index_retrieval_items(conn, user["id"], session_id, lesson_data)
        conn.commit()

        vocab_count = len(lesson_data.get("vocabulary", []))
        sent_count = len(lesson_data.get("key_sentences", []))
        return (
            f"Summary stored for {session_id} via {provider}/{use_model}.\n"
            f"  Vocabulary: {vocab_count} items\n"
            f"  Key sentences: {sent_count}\n"
            "Use get_session_summary to view it."
        )
    finally:
        conn.close()


@mcp.tool()
def get_retrieval_context(session_id: str) -> str:
    """Get prior vocabulary and corrections context for a session.

    Shows what the system 'remembers' from previous lessons: recurring vocabulary,
    past corrections, and key sentences. This context is automatically injected
    into the generate_summary prompt for learning continuity.

    Args:
        session_id: The session to get retrieval context for.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        sessions_by_id = _load_sessions_payload(run)
        session_data = sessions_by_id.get(session_id)
        if not session_data:
            return f"Error: Session '{session_id}' not found."

        context = _retrieve_context_for_session(conn, user["id"], session_id, session_data)
        text = build_retrieval_context_block(context)
        if not text:
            return f"No prior context found for session '{session_id}'. This may be the first session or there are no overlapping vocabulary items."

        return f"Retrieval context for {session_id}:\n\n{text}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Annotation tools
# ---------------------------------------------------------------------------
@mcp.tool()
def add_annotation(
    session_id: str,
    target_type: str,
    target_id: str,
    annotation_type: str,
    content: str = "",
    original: str = "",
    corrected: str = "",
    reason: str = "",
) -> str:
    """Add an annotation (correction, note, or flag) to a session item.

    Use 'correction' to fix errors in vocabulary, grammar, or translations.
    Use 'note' to add context or teaching notes. Use 'flag' to mark items
    for later review. Corrections feed back into future summary generation.

    Args:
        session_id: The session containing the item.
        target_type: What you're annotating: 'message', 'vocabulary', 'grammar', 'sentence'.
        target_id: ID of the target (message_id for messages, term for vocabulary).
        annotation_type: Type: 'correction', 'note', 'flag', or 'reclassify'.
        content: Free-text content for notes or flags.
        original: Original value (for corrections).
        corrected: Corrected value (for corrections).
        reason: Explanation for the correction or reclassification.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        if annotation_type not in ("correction", "note", "flag", "reclassify"):
            return "Error: annotation_type must be 'correction', 'note', 'flag', or 'reclassify'."

        # Build content JSON
        if annotation_type == "correction":
            content_json = {"original": original, "corrected": corrected, "reason": reason}
        elif annotation_type == "reclassify":
            content_json = {"original_type": original, "corrected_type": corrected, "reason": reason}
        elif annotation_type == "note":
            content_json = {"text": content}
        else:
            content_json = {"text": content, "reason": reason}

        role = user["role"] if "role" in user.keys() else "student"
        cursor = conn.execute(
            """INSERT INTO annotations
               (user_id, session_id, target_type, target_id,
                annotation_type, content_json, created_by_role)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], session_id, target_type, target_id,
             annotation_type, json.dumps(content_json, ensure_ascii=False), role),
        )

        # Record feedback memory for corrections
        if annotation_type in ("correction", "reclassify"):
            action = "correct" if annotation_type == "correction" else "reclassify"
            _record_feedback_memory(
                conn, user["id"], session_id, action,
                target_type=target_type, target_id=target_id,
                original=original, corrected=corrected, detail=reason,
            )

        conn.commit()
        return f"Annotation created (id={cursor.lastrowid}): {annotation_type} on {target_type} '{target_id}' in session {session_id}."
    finally:
        conn.close()


@mcp.tool()
def list_annotations(session_id: str, target_type: str = "") -> str:
    """List all annotations for a session.

    Shows corrections, notes, and flags added to messages and summary items.

    Args:
        session_id: The session to list annotations for.
        target_type: Optional filter: 'message', 'vocabulary', 'grammar', 'sentence'.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        query = "SELECT * FROM annotations WHERE user_id = ? AND session_id = ?"
        params: list = [user["id"], session_id]
        if target_type:
            query += " AND target_type = ?"
            params.append(target_type)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        if not rows:
            return f"No annotations for session '{session_id}'."

        lines = [f"Annotations for {session_id} ({len(rows)} total):\n"]
        for r in rows:
            content = json.loads(r["content_json"]) if r["content_json"] else {}
            detail = ""
            if r["annotation_type"] == "correction":
                detail = f"'{content.get('original', '')}' -> '{content.get('corrected', '')}'"
            elif r["annotation_type"] == "note":
                detail = content.get("text", "")[:80]
            elif r["annotation_type"] == "reclassify":
                detail = f"{content.get('original_type', '')} -> {content.get('corrected_type', '')}"
            else:
                detail = content.get("text", "")[:80]

            lines.append(
                f"  [{r['id']}] {r['annotation_type']} on {r['target_type']}:{r['target_id']} "
                f"({r['status']}) — {detail}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Review tools
# ---------------------------------------------------------------------------
@mcp.tool()
def run_ai_review(session_id: str, review_type: str = "parse", provider: str = "", model: str = "") -> str:
    """Run an AI review of a session's message parsing or summary quality.

    Parse review checks message classifications (teacher/student, lesson-content
    vs logistics). Summary review checks vocabulary accuracy, missing items, and
    translation quality. Returns findings you can accept or dismiss.

    Takes 10-30 seconds. Requires an LLM API key to be configured.

    Args:
        session_id: The session to review.
        review_type: 'parse' for message classification, 'summary' for summary quality.
        provider: LLM provider override. Uses config default if empty.
        model: Model override. Uses config default if empty.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"
        run, err = _require_run(conn, user["id"])
        if err:
            return f"Error: {err}"

        if review_type not in ("parse", "summary"):
            return "Error: review_type must be 'parse' or 'summary'."

        _, _, use_provider, use_model, temperature = _load_generator_config(
            provider or None, model or None,
        )
        cred_err = _validate_provider_credentials(use_provider)
        if cred_err:
            return f"Error: {cred_err}"

        sessions_by_id = _load_sessions_payload(run)
        session_data = sessions_by_id.get(session_id)
        if not session_data:
            return f"Error: Session '{session_id}' not found."

        # Import review functions
        from ai_review import review_parse, review_summary

        if review_type == "parse":
            findings = review_parse(session_data, use_provider, use_model, temperature)
        else:
            summary_row = conn.execute(
                "SELECT lesson_data_json FROM lesson_summaries WHERE user_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT 1",
                (user["id"], session_id),
            ).fetchone()
            if not summary_row:
                return f"No summary exists for '{session_id}'. Generate one first."
            summary_data = json.loads(summary_row["lesson_data_json"])
            findings = review_summary(session_data, summary_data, use_provider, use_model, temperature)

        # Store review
        findings_json = json.dumps(findings, ensure_ascii=False)
        cursor = conn.execute(
            """INSERT INTO ai_reviews
               (user_id, session_id, review_type, provider, model,
                findings_json, findings_count, accepted_count, dismissed_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'pending')""",
            (user["id"], session_id, review_type, use_provider, use_model,
             findings_json, len(findings)),
        )
        review_id = cursor.lastrowid
        conn.commit()

        if not findings:
            return f"AI review complete for {session_id} ({review_type}): No issues found."

        lines = [f"AI review for {session_id} ({review_type}) — {len(findings)} findings (review_id={review_id}):\n"]
        for i, f in enumerate(findings):
            confidence = f.get("confidence", 0)
            reason = f.get("reason", "")
            if review_type == "parse":
                msg_id = f.get("message_id", "?")
                cur = f.get("current_type", "?")
                sug = f.get("suggested_type", "?")
                lines.append(f"  [{i}] Message {msg_id}: {cur} -> {sug} (confidence: {confidence:.0%})\n       {reason}")
            else:
                section = f.get("section", "?")
                field = f.get("field", "")
                issue = f.get("issue", reason)
                lines.append(f"  [{i}] {section}.{field}: {issue} (confidence: {confidence:.0%})")

        lines.append(f"\nUse accept_finding or dismiss_finding with review_id={review_id} and the finding index.")
        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def list_reviews(session_id: str) -> str:
    """List AI reviews and their findings for a session.

    Shows previous reviews with their findings and current status
    (pending, accepted, dismissed).

    Args:
        session_id: The session to list reviews for.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        rows = conn.execute(
            "SELECT * FROM ai_reviews WHERE user_id = ? AND session_id = ? ORDER BY created_at DESC",
            (user["id"], session_id),
        ).fetchall()

        if not rows:
            return f"No reviews for session '{session_id}'."

        lines = [f"Reviews for {session_id} ({len(rows)} total):\n"]
        for r in rows:
            findings = json.loads(r["findings_json"])
            pending = sum(1 for f in findings if f.get("status") == "pending")
            lines.append(
                f"  Review {r['id']} ({r['review_type']}) — {r['findings_count']} findings, "
                f"{r['accepted_count']} accepted, {r['dismissed_count']} dismissed, "
                f"{pending} pending | {r['provider']}/{r['model']} | {r['created_at']}"
            )
            for i, f in enumerate(findings):
                status_icon = {"pending": "?", "accepted": "+", "dismissed": "x"}.get(f.get("status", ""), "?")
                reason = f.get("reason", "")[:60]
                lines.append(f"    [{i}] ({status_icon}) {reason}")

        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def accept_finding(session_id: str, review_id: int, finding_index: int) -> str:
    """Accept an AI review finding, applying the suggested correction.

    For parse reviews, this updates the message classification in session data.
    For summary reviews, this records the correction for the next re-generation.

    Args:
        session_id: The session the review belongs to.
        review_id: The review ID from list_reviews or run_ai_review.
        finding_index: Zero-based index of the finding to accept.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        row = conn.execute(
            "SELECT * FROM ai_reviews WHERE id = ? AND session_id = ? AND user_id = ?",
            (review_id, session_id, user["id"]),
        ).fetchone()
        if not row:
            return f"Error: Review {review_id} not found for session '{session_id}'."

        findings = json.loads(row["findings_json"])
        if finding_index < 0 or finding_index >= len(findings):
            return f"Error: Finding index {finding_index} out of range (0-{len(findings)-1})."

        finding = findings[finding_index]
        if finding.get("status") != "pending":
            return f"Error: Finding already {finding.get('status')}."

        finding["status"] = "accepted"
        review_type = row["review_type"]

        # For parse reviews, update sessions.json
        if review_type == "parse" and finding.get("suggested_type"):
            run = _load_latest_completed_run(conn, user["id"])
            if run:
                sessions_path = os.path.join(run["output_dir"], "sessions.json")
                if os.path.isfile(sessions_path):
                    with open(sessions_path, "r", encoding="utf-8") as f:
                        sessions_data = json.load(f)
                    msg_id = finding["message_id"]
                    for sess in sessions_data.get("sessions", []):
                        if sess["session_id"] != session_id:
                            continue
                        for msg in sess.get("messages", []):
                            if msg.get("message_id") == msg_id:
                                msg["message_type"] = finding["suggested_type"]
                                if finding.get("suggested_role"):
                                    msg["speaker_role"] = finding["suggested_role"]
                                break
                        break
                    with open(sessions_path, "w", encoding="utf-8") as f:
                        json.dump(sessions_data, f, ensure_ascii=False, indent=2)

                conn.execute(
                    """INSERT INTO feedback_signals
                       (user_id, session_id, signal_type, target_id, original_value, corrected_value)
                       VALUES (?, ?, 'reclassify_message', ?, ?, ?)""",
                    (user["id"], session_id, finding.get("message_id"),
                     finding.get("current_type"), finding.get("suggested_type")),
                )

        _record_feedback_memory(
            conn, user["id"], session_id, "accept_correction",
            target_type=review_type, target_id=finding.get("message_id"),
            original=finding.get("current_type"), corrected=finding.get("suggested_type"),
            detail=finding.get("reason"),
        )

        new_accepted = row["accepted_count"] + 1
        new_status = "completed" if (new_accepted + row["dismissed_count"]) >= row["findings_count"] else "reviewed"
        conn.execute(
            "UPDATE ai_reviews SET findings_json = ?, accepted_count = ?, status = ? WHERE id = ?",
            (json.dumps(findings, ensure_ascii=False), new_accepted, new_status, review_id),
        )
        conn.commit()

        return f"Finding [{finding_index}] accepted. Review status: {new_status}."
    finally:
        conn.close()


@mcp.tool()
def dismiss_finding(session_id: str, review_id: int, finding_index: int) -> str:
    """Dismiss an AI review finding as not applicable.

    Args:
        session_id: The session the review belongs to.
        review_id: The review ID from list_reviews or run_ai_review.
        finding_index: Zero-based index of the finding to dismiss.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        row = conn.execute(
            "SELECT * FROM ai_reviews WHERE id = ? AND session_id = ? AND user_id = ?",
            (review_id, session_id, user["id"]),
        ).fetchone()
        if not row:
            return f"Error: Review {review_id} not found for session '{session_id}'."

        findings = json.loads(row["findings_json"])
        if finding_index < 0 or finding_index >= len(findings):
            return f"Error: Finding index {finding_index} out of range (0-{len(findings)-1})."

        finding = findings[finding_index]
        if finding.get("status") != "pending":
            return f"Error: Finding already {finding.get('status')}."

        finding["status"] = "dismissed"

        new_dismissed = row["dismissed_count"] + 1
        new_status = "completed" if (row["accepted_count"] + new_dismissed) >= row["findings_count"] else "reviewed"
        conn.execute(
            "UPDATE ai_reviews SET findings_json = ?, dismissed_count = ?, status = ? WHERE id = ?",
            (json.dumps(findings, ensure_ascii=False), new_dismissed, new_status, review_id),
        )
        conn.commit()

        return f"Finding [{finding_index}] dismissed. Review status: {new_status}."
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stats tools
# ---------------------------------------------------------------------------
@mcp.tool()
def get_retrieval_stats() -> str:
    """Get an overview of indexed learning items across all sessions.

    Shows counts of vocabulary, corrections, key sentences, and feedback
    patterns in the retrieval index. Useful for understanding what the
    system has learned from your prior lessons.
    """
    conn = get_db()
    try:
        user, err = _get_user(conn)
        if err:
            return f"Error: {err}"

        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_retrieval_items WHERE user_id = ?",
            (user["id"],),
        ).fetchone()["cnt"]

        sessions_indexed = conn.execute(
            "SELECT COUNT(DISTINCT session_id) as cnt FROM user_retrieval_items WHERE user_id = ?",
            (user["id"],),
        ).fetchone()["cnt"]

        by_type = conn.execute(
            "SELECT item_type, COUNT(*) as cnt FROM user_retrieval_items WHERE user_id = ? GROUP BY item_type",
            (user["id"],),
        ).fetchall()

        feedback = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM user_feedback_memory WHERE user_id = ? GROUP BY action",
            (user["id"],),
        ).fetchall()

        out = [
            f"Retrieval Index Stats:",
            f"  Total items: {total}",
            f"  Sessions indexed: {sessions_indexed}",
        ]

        if by_type:
            out.append("  By type:")
            for r in by_type:
                out.append(f"    {r['item_type']}: {r['cnt']}")

        if feedback:
            out.append("  Feedback patterns:")
            for r in feedback:
                out.append(f"    {r['action']}: {r['cnt']}")

        return "\n".join(out)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Resource: lesson-data schema
# ---------------------------------------------------------------------------
LESSON_DATA_SCHEMA = """# LessonLens lesson-data.v1 Schema

Each generated summary follows this structure:

- **schema_version**: "lesson-data.v1"
- **lesson_id**: Unique identifier for the lesson
- **lesson_date**: Date of the lesson (YYYY-MM-DD)
- **title**: Descriptive title

## summary
- overview: 2-3 sentence lesson overview
- usage_notes: Key usage patterns or cultural notes
- short_recap: One-line recap for study sessions

## vocabulary[] (array)
- term_zh: Chinese term
- pinyin: Pinyin pronunciation
- en: English translation
- pos_or_type: Part of speech (noun, verb, adj, phrase, etc.)
- example_zh: Example sentence in Chinese
- example_en: Example sentence translation

## key_sentences[] (array)
- zh: Chinese sentence
- pinyin: Full pinyin
- zhuyin: Zhuyin/Bopomofo (optional)
- en: English translation
- source_refs: Message IDs from the transcript
- context_note: When/how to use this sentence

## corrections[] (array)
- learner_original: What the student said
- teacher_correction: What the teacher corrected it to
- reason: Why the correction was made

## review (study materials)
- flashcards[]: {front, back} pairs
- fill_blank[]: {sentence (with ___), answer}
- translation_drills[]: {source_text, target_text}
- quiz[]: {question, options[], correct_index}
"""


@mcp.resource("lessonlens://schema/lesson-data")
def lesson_data_schema() -> str:
    """The lesson-data.v1 JSON schema used by LessonLens summaries."""
    return LESSON_DATA_SCHEMA


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run()

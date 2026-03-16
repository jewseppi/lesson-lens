"""
ai_review.py — AI-powered review of parser output and generated summaries.

Uses the same LLM provider abstraction as generate_outputs.py to check
message classifications and summary accuracy.
"""
import json
import sys

from generate_outputs import PROVIDERS, load_config, load_prompt


def _parse_llm_json_array(raw: str) -> list:
    """Parse a JSON array from LLM response, stripping code fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines[1:] if not line.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse review JSON: {e}", file=sys.stderr)
        print(f"Raw response (first 500 chars): {text[:500]}", file=sys.stderr)
        return []


def _build_review_transcript(session: dict) -> str:
    """Build a transcript with classifications visible for the reviewer."""
    lines = []
    lines.append(f"## Session: {session['date']} ({session.get('start_time', '')}–{session.get('end_time', '')})")
    lines.append(f"Session ID: {session['session_id']}")
    lines.append("")
    lines.append("Messages:")
    lines.append("")

    for msg in session.get("messages", []):
        mid = msg.get("message_id", "")
        time_str = msg.get("time", "")
        role = msg.get("speaker_role", "unknown")
        raw_speaker = msg.get("speaker_raw", "")
        mtype = msg.get("message_type", "")
        lang = msg.get("language_hint", "")
        text = msg.get("text_raw", "")
        tags = msg.get("tags", [])

        tag_str = f" tags={tags}" if tags else ""
        lines.append(
            f"[{mid}] {time_str} | role={role} speaker=\"{raw_speaker}\" "
            f"| type={mtype} lang={lang}{tag_str}"
        )
        lines.append(f"  {text}")
        lines.append("")

    return "\n".join(lines)


def _format_feedback_context(feedback_signals: list) -> str:
    """Format feedback signals as context for the reviewer."""
    if not feedback_signals:
        return ""

    lines = ["\n## Confirmed User Feedback\n"]
    lines.append("The following actions were taken by the user and represent ground truth:\n")

    for sig in feedback_signals:
        stype = sig.get("signal_type", "")
        sid = sig.get("session_id", "")
        target = sig.get("target_id", "")
        orig = sig.get("original_value", "")
        corrected = sig.get("corrected_value", "")

        if stype == "archive":
            lines.append(f"- Session {sid} was manually archived (user confirmed it's not a lesson)")
        elif stype == "unarchive":
            lines.append(f"- Session {sid} was manually unarchived (user confirmed it IS a lesson)")
        elif stype == "reclassify_message" and target:
            lines.append(f"- Message {target}: reclassified from '{orig}' to '{corrected}'")
        else:
            lines.append(f"- {stype}: {orig} → {corrected}")

    return "\n".join(lines)


def review_parse(
    session: dict,
    provider: str,
    model: str,
    temperature: float = 0.3,
    feedback_signals: list | None = None,
) -> list:
    """
    Ask an LLM to review message classifications for a session.

    Returns a list of findings, each with:
      message_id, current_type, suggested_type, confidence, reason
    """
    prompt = load_prompt("parse-reviewer")
    transcript = _build_review_transcript(session)

    user_content = transcript
    if feedback_signals:
        user_content += _format_feedback_context(feedback_signals)

    call_fn = PROVIDERS.get(provider)
    if not call_fn:
        print(f"Error: unknown provider '{provider}'", file=sys.stderr)
        return []

    raw = call_fn(prompt, user_content, model, temperature)
    findings = _parse_llm_json_array(raw)

    # Normalize and validate findings
    valid = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        if not f.get("message_id"):
            continue
        finding = {
            "message_id": f["message_id"],
            "current_type": f.get("current_type", ""),
            "suggested_type": f.get("suggested_type", ""),
            "current_role": f.get("current_role"),
            "suggested_role": f.get("suggested_role"),
            "confidence": min(1.0, max(0.0, float(f.get("confidence", 0.5)))),
            "reason": f.get("reason", ""),
            "status": "pending",
        }
        valid.append(finding)

    return valid


def review_summary(
    lesson_data: dict,
    session: dict,
    provider: str,
    model: str,
    temperature: float = 0.3,
) -> list:
    """
    Ask an LLM to review a generated summary for accuracy.

    Returns a list of findings, each with:
      section, item_id, field, issue, suggestion, confidence
    """
    prompt = load_prompt("summary-reviewer")

    # Build user content with both the summary and original transcript
    parts = []
    parts.append("## Generated Lesson Data\n")
    parts.append(json.dumps(lesson_data, ensure_ascii=False, indent=2))
    parts.append("\n\n## Original Transcript\n")

    for msg in session.get("messages", []):
        mid = msg.get("message_id", "")
        role = msg.get("speaker_role", "unknown")
        text = msg.get("text_raw", "")
        mtype = msg.get("message_type", "")
        if mtype in ("media-reference", "call-system"):
            continue
        label = "Teacher" if role == "teacher" else ("Student" if role == "student" else "?")
        parts.append(f"[{mid}] {label}: {text}")

    user_content = "\n".join(parts)

    call_fn = PROVIDERS.get(provider)
    if not call_fn:
        print(f"Error: unknown provider '{provider}'", file=sys.stderr)
        return []

    raw = call_fn(prompt, user_content, model, temperature)
    findings = _parse_llm_json_array(raw)

    # Normalize and validate findings
    valid = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        finding = {
            "section": f.get("section", ""),
            "item_id": f.get("item_id"),
            "field": f.get("field", ""),
            "current_value": f.get("current_value", ""),
            "suggested_value": f.get("suggested_value", ""),
            "issue": f.get("issue", ""),
            "confidence": min(1.0, max(0.0, float(f.get("confidence", 0.5)))),
            "status": "pending",
        }
        valid.append(finding)

    return valid

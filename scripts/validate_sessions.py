"""
validate_sessions.py — Validate sessions.json against the JSON schema
and run cross-file integrity checks.
"""
import argparse
import json
import os
import sys

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_structure(data: dict) -> list[str]:
    """Basic structural validation (no jsonschema dependency needed)."""
    errors = []

    # Required top-level keys
    required = ["schema_version", "run_id", "source", "parser_version",
                "generated_at", "sessions", "stats", "warnings"]
    for key in required:
        if key not in data:
            errors.append(f"Missing required top-level key: {key}")

    if data.get("schema_version") != "sessions.v1":
        errors.append(f"Unexpected schema_version: {data.get('schema_version')}")

    # Source checks
    source = data.get("source", {})
    for key in ["file_name", "file_hash_sha256", "encoding", "timezone", "line_count"]:
        if key not in source:
            errors.append(f"Missing source.{key}")

    # Session checks
    sessions = data.get("sessions", [])
    if not isinstance(sessions, list):
        errors.append("sessions is not an array")
        return errors

    seen_session_ids = set()
    seen_message_ids = set()
    total_msgs_in_sessions = 0

    for i, sess in enumerate(sessions):
        prefix = f"sessions[{i}]"

        for key in ["session_id", "date", "start_time", "end_time",
                     "message_count", "lesson_content_count",
                     "boundary_confidence", "messages"]:
            if key not in sess:
                errors.append(f"{prefix}: missing {key}")

        sid = sess.get("session_id", "")
        if sid in seen_session_ids:
            errors.append(f"{prefix}: duplicate session_id '{sid}'")
        seen_session_ids.add(sid)

        messages = sess.get("messages", [])
        if not isinstance(messages, list):
            errors.append(f"{prefix}: messages is not an array")
            continue

        # message_count consistency
        if sess.get("message_count") != len(messages):
            errors.append(
                f"{prefix}: message_count={sess.get('message_count')} "
                f"but messages has {len(messages)} items"
            )

        total_msgs_in_sessions += len(messages)

        # lesson_content_count consistency
        actual_content = sum(1 for m in messages if m.get("message_type") == "lesson-content")
        if sess.get("lesson_content_count") != actual_content:
            errors.append(
                f"{prefix}: lesson_content_count={sess.get('lesson_content_count')} "
                f"but actual count is {actual_content}"
            )

        # Per-message checks
        for j, msg in enumerate(messages):
            msg_prefix = f"{prefix}.messages[{j}]"
            mid = msg.get("message_id", "")

            if mid in seen_message_ids:
                errors.append(f"{msg_prefix}: duplicate message_id '{mid}'")
            seen_message_ids.add(mid)

            # Required message fields
            for key in ["message_id", "line_start", "line_end", "time",
                        "speaker_role", "message_type", "text_raw"]:
                if key not in msg:
                    errors.append(f"{msg_prefix}: missing {key}")

            # Enum checks
            valid_roles = {"teacher", "student", "unknown"}
            if msg.get("speaker_role") not in valid_roles:
                errors.append(f"{msg_prefix}: invalid speaker_role '{msg.get('speaker_role')}'")

            valid_types = {"lesson-content", "logistics", "media-reference",
                           "call-system", "link", "other"}
            if msg.get("message_type") not in valid_types:
                errors.append(f"{msg_prefix}: invalid message_type '{msg.get('message_type')}'")

            # line_start <= line_end
            ls = msg.get("line_start", 0)
            le = msg.get("line_end", 0)
            if ls > le:
                errors.append(f"{msg_prefix}: line_start ({ls}) > line_end ({le})")

    # Stats consistency
    stats = data.get("stats", {})
    if stats.get("total_sessions") != len(sessions):
        errors.append(
            f"stats.total_sessions={stats.get('total_sessions')} "
            f"but actual sessions count is {len(sessions)}"
        )
    if stats.get("total_messages") != total_msgs_in_sessions:
        errors.append(
            f"stats.total_messages={stats.get('total_messages')} "
            f"but sum of session messages is {total_msgs_in_sessions}"
        )

    return errors


def validate_jsonl_consistency(sessions_data: dict, jsonl_path: str) -> list[str]:
    """Cross-check sessions.json against normalized_messages.jsonl."""
    errors = []

    if not os.path.isfile(jsonl_path):
        errors.append(f"JSONL file not found: {jsonl_path}")
        return errors

    jsonl_ids = set()
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                jsonl_ids.add(obj.get("message_id"))
            except json.JSONDecodeError:
                errors.append(f"JSONL line {line_num}: invalid JSON")

    # All session message IDs should exist in JSONL
    session_ids = set()
    for sess in sessions_data.get("sessions", []):
        for msg in sess.get("messages", []):
            session_ids.add(msg.get("message_id"))

    missing_in_jsonl = session_ids - jsonl_ids
    extra_in_jsonl = jsonl_ids - session_ids

    if missing_in_jsonl:
        errors.append(f"{len(missing_in_jsonl)} message IDs in sessions.json but missing from JSONL")
    if extra_in_jsonl:
        errors.append(f"{len(extra_in_jsonl)} message IDs in JSONL but missing from sessions.json")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate sessions.json")
    parser.add_argument("--input", required=True, help="Path to sessions.json")
    parser.add_argument("--jsonl", default=None, help="Path to normalized_messages.jsonl for cross-check")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    data = load_json(args.input)
    errors = validate_structure(data)

    # Auto-detect JSONL path if not provided
    jsonl_path = args.jsonl
    if not jsonl_path:
        dir_of_input = os.path.dirname(args.input)
        candidate = os.path.join(dir_of_input, "normalized_messages.jsonl")
        if os.path.isfile(candidate):
            jsonl_path = candidate

    if jsonl_path:
        errors += validate_jsonl_consistency(data, jsonl_path)

    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):\n")
        for err in errors:
            print(f"  ✗ {err}")
        sys.exit(1)
    else:
        stats = data.get("stats", {})
        print("VALIDATION PASSED ✓")
        print(f"  Sessions:  {stats.get('total_sessions', '?')}")
        print(f"  Messages:  {stats.get('total_messages', '?')}")
        print(f"  Lesson:    {stats.get('lesson_content_messages', '?')}")
        print(f"  Warnings:  {len(data.get('warnings', []))}")
        if jsonl_path:
            print(f"  JSONL cross-check: passed")


if __name__ == "__main__":
    main()

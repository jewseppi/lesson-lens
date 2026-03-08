"""
parse_line_export.py — State-machine parser for LINE/chat exports.

Reads extracted transcript lines and produces:
  - normalized_messages.jsonl  (one JSON object per message)
  - sessions.json              (sessions schema v1)
  - parse_report.json          (stats + warnings)
  - diagnostics.txt            (first N anomalies)
"""
import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime

import yaml
from datetime import timezone

from extract_transcript import extract

# ---------------------------------------------------------------------------
# Regex patterns (from Parsing Spec v1)
# ---------------------------------------------------------------------------
RE_DATE_HEADER = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s(\d{4}-\d{2}-\d{2})$")
RE_MESSAGE_START = re.compile(r"^(\d{2}:\d{2})\t([^\t]+)\t(.*)$")
RE_BLANK = re.compile(r"^\s*$")

# Language / content detection
RE_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
RE_PINYIN_NUMERIC = re.compile(r"\b[a-z]+[1-5](?:[a-z]+[1-5])*\b", re.IGNORECASE)
RE_PINYIN_DIACRITIC = re.compile(r"[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]")
RE_ZHUYIN = re.compile(r"[\u3100-\u312f\u31a0-\u31bf]")
RE_URL = re.compile(r"https?://\S+")
RE_BILINGUAL_PAIR = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf]+)\s*[/\-]\s*([a-zA-Z][\w\s]*)",
)

MEDIA_PLACEHOLDERS = {"[Photo]", "[File]", "[Sticker]", "[Contact]"}
CALL_SYSTEM_PHRASE = "called you. You can make and receive calls"

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(config_path: str | None = None) -> dict:
    default = os.path.join(os.path.dirname(__file__), "..", "config", "pipeline.yaml")
    path = config_path or default
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_message(text: str, config: dict) -> tuple[str, str, list[str]]:
    """Return (message_type, language_hint, tags)."""
    text_stripped = text.strip()
    tags = []

    # Media reference
    if text_stripped in MEDIA_PLACEHOLDERS:
        return "media-reference", "other", ["media"]

    # Call system
    if CALL_SYSTEM_PHRASE in text:
        return "call-system", "en", ["system"]

    # Detect language features
    has_cjk = bool(RE_CJK.search(text))
    has_pinyin_num = bool(RE_PINYIN_NUMERIC.search(text))
    has_pinyin_dia = bool(RE_PINYIN_DIACRITIC.search(text))
    has_zhuyin = bool(RE_ZHUYIN.search(text))
    has_url = bool(RE_URL.search(text))
    has_bilingual = bool(RE_BILINGUAL_PAIR.search(text))

    if has_pinyin_num:
        tags.append("pinyin-numeric")
    if has_pinyin_dia:
        tags.append("pinyin-diacritic")
    if has_zhuyin:
        tags.append("zhuyin")
    if has_bilingual:
        tags.append("bilingual-pair")

    # URL-only or URL-bearing
    if has_url:
        tags.append("link")
        if not has_cjk and not has_pinyin_num:
            return "link", "en", tags

    # Lesson content detection
    if has_cjk or has_pinyin_num or has_pinyin_dia or has_zhuyin or has_bilingual:
        lang = "mixed" if (has_cjk and bool(re.search(r"[a-zA-Z]{3,}", text))) else (
            "zh" if has_cjk else "pinyin"
        )
        return "lesson-content", lang, tags

    # Logistics detection (English-only, scheduling patterns)
    logistics_patterns = [
        r"\b(late|min late|minutes? late|couple min|few min)\b",
        r"\b(reschedule|cancel|class today|class time|class on|unavailable)\b",
        r"\b(zoom|google meet|link|password)\b",
        r"\b(got it|no problem|will wait|see you|sounds good)\b",
        r"\b(good morning|hello|hi\b)",
        r"\b(sorry|slept in|making coffee)\b",
        r"\b(ok(ay)?|thank(s| you)|you'?re welcome|please|enjoy)\b",
        r"\b(sick|feel|feeling|headache|tired)\b",
        r"\b(tomorrow|yesterday|today|schedule|meeting|next class)\b",
        r"\b(classroom|room|face to face|online|parking|bike)\b",
        r"\b(practice|mean ?time|holiday|new year|vacation)\b",
        r"\b(check|checked|send|message|email)\b",
        r"\b(meet|same|last time)\b",
    ]
    for pat in logistics_patterns:
        if re.search(pat, text, re.IGNORECASE):
            return "logistics", "en", tags

    # If has URL + CJK, still lesson content
    if has_url and has_cjk:
        return "lesson-content", "mixed", tags

    # Short English-only messages without CJK — likely logistics/chit-chat
    if not has_cjk and len(text_stripped) < 80:
        return "logistics", "en", tags

    return "other", "en", tags


def resolve_speaker_role(speaker_raw: str, config: dict) -> str:
    teachers = config.get("speakers", {}).get("teacher_aliases", [])
    students = config.get("speakers", {}).get("student_aliases", [])
    if speaker_raw in teachers:
        return "teacher"
    if speaker_raw in students:
        return "student"
    return "unknown"


# ---------------------------------------------------------------------------
# State-machine parser
# ---------------------------------------------------------------------------

def parse_lines(lines: list[str], source_meta: dict, config: dict) -> dict:
    """Parse list of lines into messages grouped by session."""

    messages = []
    warnings = []
    diagnostics = []
    preamble_lines = []

    state = "PREAMBLE"
    current_date = None
    current_msg = None
    msg_counter = 0

    def finalize_message():
        nonlocal current_msg
        if current_msg is None:
            return
        text_raw = current_msg["_text_parts"]
        joined = "\n".join(text_raw)
        msg_type, lang_hint, tags = classify_message(joined, config)
        current_msg["text_raw"] = joined
        current_msg["text_normalized"] = joined.strip()
        current_msg["message_type"] = msg_type
        current_msg["language_hint"] = lang_hint
        current_msg["tags"] = tags
        current_msg["confidence"] = "high"
        del current_msg["_text_parts"]
        messages.append(current_msg)
        current_msg = None

    for line_num_0, line in enumerate(lines):
        line_num = line_num_0 + 1  # 1-indexed

        # Check date header
        m_date = RE_DATE_HEADER.match(line)
        if m_date:
            finalize_message()
            current_date = m_date.group(2)
            if state == "PREAMBLE":
                state = "IN_DAY_BLOCK"
            else:
                state = "IN_DAY_BLOCK"
            continue

        # Check message start
        m_msg = RE_MESSAGE_START.match(line)
        if m_msg and current_date:
            finalize_message()
            msg_counter += 1
            current_msg = {
                "message_id": f"msg-{msg_counter:04d}",
                "line_start": line_num,
                "line_end": line_num,
                "date": current_date,
                "time": m_msg.group(1),
                "speaker_raw": m_msg.group(2),
                "speaker_role": resolve_speaker_role(m_msg.group(2), config),
                "_text_parts": [m_msg.group(3)],
            }
            state = "IN_MESSAGE"
            continue

        # Blank line
        if RE_BLANK.match(line):
            continue

        # Preamble
        if state == "PREAMBLE":
            preamble_lines.append(line)
            continue

        # Continuation line
        if state == "IN_MESSAGE" and current_msg:
            current_msg["_text_parts"].append(line)
            current_msg["line_end"] = line_num
            continue

        # Unknown pattern
        diagnostics.append(f"L{line_num}: unexpected line in state={state}: {line[:120]}")
        if len(diagnostics) <= 50:
            warnings.append({
                "code": "UNKNOWN_LINE_PATTERN",
                "severity": "warn",
                "message": f"Unexpected line at L{line_num}",
                "line_refs": [line_num],
                "session_id": current_date or "",
            })

    # Finalize last message
    finalize_message()

    # --- Build sessions from messages grouped by date ---
    sessions = _build_sessions(messages, config, warnings)

    # --- Stats ---
    stats = _compute_stats(messages, sessions)

    # --- Check for low-content days ---
    min_content = config.get("parser", {}).get("min_lesson_content_messages", 3)
    for sess in sessions:
        if sess["lesson_content_count"] < min_content:
            sess["parse_flags"] = sess.get("parse_flags", []) + ["low-content-day"]
            warnings.append({
                "code": "LOW_CONTENT_DAY",
                "severity": "info",
                "message": f"Session {sess['session_id']} has only {sess['lesson_content_count']} lesson-content messages",
                "line_refs": [],
                "session_id": sess["session_id"],
            })

    # --- Check unknown speaker ---
    unknown_speakers = {m["speaker_raw"] for m in messages if m["speaker_role"] == "unknown"}
    for sp in unknown_speakers:
        warnings.append({
            "code": "MISSING_SPEAKER_ALIAS",
            "severity": "warn",
            "message": f"Speaker '{sp}' not in teacher/student aliases",
            "line_refs": [],
            "session_id": "",
        })

    return {
        "messages": messages,
        "sessions": sessions,
        "stats": stats,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "preamble": preamble_lines,
    }


def _build_sessions(messages: list[dict], config: dict, warnings: list) -> list:
    """Group messages into sessions by date, with intra-day gap splitting."""
    gap_minutes = config.get("parser", {}).get("lesson_gap_minutes", 90)

    by_date: dict[str, list[dict]] = {}
    for msg in messages:
        by_date.setdefault(msg["date"], []).append(msg)

    sessions = []
    for date, msgs in sorted(by_date.items()):
        # Sort by time
        msgs.sort(key=lambda m: m["time"])

        # Split by time gap
        groups = []
        current_group = [msgs[0]]
        for i in range(1, len(msgs)):
            prev_time = _parse_time(msgs[i - 1]["time"])
            curr_time = _parse_time(msgs[i]["time"])
            if curr_time and prev_time:
                delta = (curr_time - prev_time).total_seconds() / 60
                if delta > gap_minutes:
                    groups.append(current_group)
                    current_group = []
                    warnings.append({
                        "code": "TIME_GAP_SPLIT",
                        "severity": "info",
                        "message": f"Split session on {date} at {msgs[i]['time']} (gap: {delta:.0f}min)",
                        "line_refs": [msgs[i]["line_start"]],
                        "session_id": date,
                    })
            current_group.append(msgs[i])
        groups.append(current_group)

        for idx, group in enumerate(groups):
            session_id = date if len(groups) == 1 else f"{date}-{idx + 1}"
            content_count = sum(1 for m in group if m["message_type"] == "lesson-content")
            logistics_count = sum(1 for m in group if m["message_type"] == "logistics")
            media_count = sum(1 for m in group if m["message_type"] == "media-reference")
            links_count = sum(1 for m in group if m["message_type"] == "link")

            # Boundary confidence
            if content_count >= 5:
                confidence = "high"
            elif content_count >= 2:
                confidence = "medium"
            else:
                confidence = "low"

            session_messages = []
            for m in group:
                session_messages.append({
                    "message_id": m["message_id"],
                    "line_start": m["line_start"],
                    "line_end": m["line_end"],
                    "time": m["time"],
                    "speaker_role": m["speaker_role"],
                    "speaker_raw": m["speaker_raw"],
                    "message_type": m["message_type"],
                    "text_raw": m["text_raw"],
                    "text_normalized": m["text_normalized"],
                    "language_hint": m["language_hint"],
                    "tags": m["tags"],
                    "confidence": m["confidence"],
                })

            sessions.append({
                "session_id": session_id,
                "date": date,
                "start_time": group[0]["time"],
                "end_time": group[-1]["time"],
                "message_count": len(group),
                "lesson_content_count": content_count,
                "boundary_confidence": confidence,
                "logistics_count": logistics_count,
                "media_count": media_count,
                "links_count": links_count,
                "messages": session_messages,
            })

    return sessions


def _parse_time(time_str: str):
    try:
        return datetime.strptime(time_str, "%H:%M")
    except ValueError:
        return None


def _compute_stats(messages: list[dict], sessions: list[dict]) -> dict:
    stats = {
        "total_messages": len(messages),
        "total_sessions": len(sessions),
        "lesson_content_messages": sum(1 for m in messages if m["message_type"] == "lesson-content"),
        "logistics_messages": sum(1 for m in messages if m["message_type"] == "logistics"),
        "media_messages": sum(1 for m in messages if m["message_type"] == "media-reference"),
        "unknown_pattern_count": sum(1 for m in messages if m["message_type"] == "other"),
        "pinyin_numeric_count": sum(1 for m in messages if "pinyin-numeric" in m.get("tags", [])),
        "pinyin_diacritic_count": sum(1 for m in messages if "pinyin-diacritic" in m.get("tags", [])),
        "zhuyin_count": sum(1 for m in messages if "zhuyin" in m.get("tags", [])),
        "bilingual_pair_count": sum(1 for m in messages if "bilingual-pair" in m.get("tags", [])),
    }
    return stats


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_outputs(parse_result: dict, source_meta: dict, config: dict,
                  run_id: str, output_dir: str):
    """Write all four parser artifacts to output_dir."""
    os.makedirs(output_dir, exist_ok=True)

    # 1. normalized_messages.jsonl
    jsonl_path = os.path.join(output_dir, "normalized_messages.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for msg in parse_result["messages"]:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    # 2. sessions.json
    sessions_obj = {
        "schema_version": "sessions.v1",
        "run_id": run_id,
        "parser_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "file_name": source_meta["file_name"],
            "file_hash_sha256": source_meta["file_hash_sha256"],
            "encoding": source_meta["encoding"],
            "timezone": config.get("parser", {}).get("timezone", "Asia/Taipei"),
            "line_count": source_meta["line_count"],
        },
        "config_snapshot": config,
        "sessions": parse_result["sessions"],
        "stats": parse_result["stats"],
        "warnings": parse_result["warnings"],
    }
    # Add preamble info if available
    if parse_result.get("preamble"):
        sessions_obj["source"]["saved_on_text"] = " | ".join(parse_result["preamble"])

    sessions_path = os.path.join(output_dir, "sessions.json")
    with open(sessions_path, "w", encoding="utf-8") as f:
        json.dump(sessions_obj, f, ensure_ascii=False, indent=2)

    # 3. parse_report.json
    report = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_file": source_meta["file_name"],
        "stats": parse_result["stats"],
        "warning_count": len(parse_result["warnings"]),
        "warnings": parse_result["warnings"],
    }
    report_path = os.path.join(output_dir, "parse_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 4. diagnostics.txt
    diag_path = os.path.join(output_dir, "diagnostics.txt")
    with open(diag_path, "w", encoding="utf-8") as f:
        if parse_result["diagnostics"]:
            for line in parse_result["diagnostics"][:50]:
                f.write(line + "\n")
        else:
            f.write("No anomalies detected.\n")

    return {
        "jsonl": jsonl_path,
        "sessions": sessions_path,
        "report": report_path,
        "diagnostics": diag_path,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Parse LINE chat export into structured sessions")
    parser.add_argument("--input", required=True, help="Path to chat export file")
    parser.add_argument("--run-id", default=None, help="Run identifier (default: auto-generated)")
    parser.add_argument("--config", default=None, help="Path to pipeline.yaml config")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    run_id = args.run_id or datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base_dir = os.path.join(os.path.dirname(__file__), "..", "processed")
    output_dir = args.output_dir or os.path.join(base_dir, run_id)

    # Extract
    print(f"Extracting: {args.input}")
    source_meta = extract(args.input)
    lines = source_meta.pop("lines")

    # Parse
    print(f"Parsing {source_meta['line_count']} lines...")
    result = parse_lines(lines, source_meta, config)

    # Write
    paths = write_outputs(result, source_meta, config, run_id, output_dir)

    # Summary
    stats = result["stats"]
    print(f"\n{'='*50}")
    print(f"Run ID:           {run_id}")
    print(f"Sessions found:   {stats['total_sessions']}")
    print(f"Total messages:   {stats['total_messages']}")
    print(f"Lesson content:   {stats['lesson_content_messages']}")
    print(f"Logistics:        {stats['logistics_messages']}")
    print(f"Media refs:       {stats['media_messages']}")
    print(f"Unknown patterns: {stats['unknown_pattern_count']}")
    print(f"Warnings:         {len(result['warnings'])}")
    print(f"{'='*50}")
    print(f"Output: {output_dir}")
    for name, path in paths.items():
        print(f"  {name}: {os.path.basename(path)}")


if __name__ == "__main__":
    main()

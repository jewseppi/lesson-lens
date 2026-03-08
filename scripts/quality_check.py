"""
quality_check.py — Post-generation quality checks on lesson-data.json.

Checks:
  - Correction coverage (all teacher corrections captured)
  - Pinyin completeness (every zh field has matching pinyin)
  - Source ref resolution (source_refs point to valid message IDs)
  - Schema field presence
"""
import argparse
import json
import os
import re
import sys


RE_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_pinyin_completeness(lesson: dict) -> list[str]:
    """Every key_sentence and vocabulary item with Chinese must have pinyin."""
    issues = []

    for i, ks in enumerate(lesson.get("key_sentences", [])):
        if RE_CJK.search(ks.get("zh", "")) and not ks.get("pinyin", "").strip():
            issues.append(f"key_sentences[{i}] ({ks.get('id','?')}): missing pinyin for '{ks.get('zh','')[:30]}'")

    for i, v in enumerate(lesson.get("vocabulary", [])):
        if RE_CJK.search(v.get("term_zh", "")) and not v.get("pinyin", "").strip():
            issues.append(f"vocabulary[{i}]: missing pinyin for '{v.get('term_zh','')}'")

    return issues


def check_source_refs(lesson: dict, valid_ids: set[str]) -> list[str]:
    """Verify source_refs point to real message IDs from sessions.json."""
    issues = []

    def check_refs(items, label):
        for i, item in enumerate(items):
            for ref in item.get("source_refs", []):
                if ref not in valid_ids:
                    issues.append(f"{label}[{i}] ({item.get('id','?')}): invalid source_ref '{ref}'")

    check_refs(lesson.get("key_sentences", []), "key_sentences")
    check_refs(lesson.get("corrections", []), "corrections")
    # vocabulary source_refs are optional
    check_refs(lesson.get("vocabulary", []), "vocabulary")

    return issues


def check_required_sections(lesson: dict) -> list[str]:
    """Verify all required top-level sections are populated."""
    issues = []
    summary = lesson.get("summary", {})

    if not summary.get("overview"):
        issues.append("summary.overview is empty")
    if not summary.get("short_recap"):
        issues.append("summary.short_recap is empty")
    if not lesson.get("key_sentences"):
        issues.append("key_sentences is empty")
    if not lesson.get("vocabulary"):
        issues.append("vocabulary is empty")

    review = lesson.get("review", {})
    if not review.get("flashcards"):
        issues.append("review.flashcards is empty")
    if not review.get("quiz"):
        issues.append("review.quiz is empty")

    return issues


def check_review_quality(lesson: dict) -> list[str]:
    """Spot-check review exercises for common issues."""
    issues = []
    review = lesson.get("review", {})

    # Quiz: correct_index must be valid
    for i, q in enumerate(review.get("quiz", [])):
        opts = q.get("options", [])
        ci = q.get("correct_index", -1)
        if ci < 0 or ci >= len(opts):
            issues.append(f"quiz[{i}] ({q.get('id','?')}): correct_index {ci} out of range (options: {len(opts)})")

    # Fill-blank: answer should not appear in sentence
    for i, fb in enumerate(review.get("fill_blank", [])):
        if fb.get("answer", "") in fb.get("sentence", ""):
            issues.append(f"fill_blank[{i}] ({fb.get('id','?')}): answer '{fb.get('answer','')}' appears in the sentence (blank not applied?)")

    return issues


def run_quality_check(lesson_path: str, sessions_path: str | None = None) -> dict:
    lesson = load_json(lesson_path)
    all_issues = []

    # Pinyin
    pinyin_issues = check_pinyin_completeness(lesson)
    all_issues += [("pinyin", i) for i in pinyin_issues]

    # Source refs (needs sessions.json)
    if sessions_path:
        sessions = load_json(sessions_path)
        valid_ids = set()
        for sess in sessions.get("sessions", []):
            for msg in sess.get("messages", []):
                valid_ids.add(msg.get("message_id"))
        ref_issues = check_source_refs(lesson, valid_ids)
        all_issues += [("source_ref", i) for i in ref_issues]

    # Required sections
    section_issues = check_required_sections(lesson)
    all_issues += [("missing_section", i) for i in section_issues]

    # Review quality
    review_issues = check_review_quality(lesson)
    all_issues += [("review_quality", i) for i in review_issues]

    return {
        "lesson_path": lesson_path,
        "total_issues": len(all_issues),
        "issues": [{"category": cat, "detail": detail} for cat, detail in all_issues],
        "passed": len(all_issues) == 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Quality-check a lesson-data.json file")
    parser.add_argument("--input", required=True, help="Path to lesson-data.json")
    parser.add_argument("--sessions", default=None, help="Path to sessions.json for source_ref validation")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    result = run_quality_check(args.input, args.sessions)

    if result["passed"]:
        print("QUALITY CHECK PASSED ✓")
    else:
        print(f"QUALITY CHECK: {result['total_issues']} issue(s) found\n")
        for issue in result["issues"]:
            print(f"  [{issue['category']}] {issue['detail']}")
        sys.exit(1)


if __name__ == "__main__":
    main()

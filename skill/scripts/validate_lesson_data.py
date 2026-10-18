#!/usr/bin/env python3
"""
validate_lesson_data.py — Validate a generated lesson-data.v2 study pack.

Runs two layers of checks against a study-pack JSON file:

  1. Schema validation (structure + required fields) using the bundled
     lesson-data.v2 JSON schema.
  2. Fidelity lints — the rules that make a study pack trustworthy rather
     than merely well-formed. These are the checks a buyer actually cares
     about: that every item is traceable to the transcript, that the
     reading/romanization policy was honoured, and that review items are
     internally consistent.

Usage:
    python validate_lesson_data.py path/to/lesson-data.json
    python validate_lesson_data.py path/to/lesson-data.json --schema path/to/schema.json

Exit code is 0 only when schema validation passes AND there are no fidelity
errors, so this can gate an automated run. Warnings do not fail the run.
"""
import argparse
import json
import os
import sys

try:
    from jsonschema import Draft7Validator
except ImportError:
    print("error: jsonschema is not installed. `pip install jsonschema`", file=sys.stderr)
    sys.exit(2)

DEFAULT_SCHEMA = os.path.join(
    os.path.dirname(__file__), "..", "references", "lesson-data.schema.json"
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def schema_errors(data, schema):
    validator = Draft7Validator(schema)
    out = []
    for e in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        out.append(f"{loc}: {e.message}")
    return out


def fidelity_lints(data):
    """Return (errors, warnings). Errors fail the run; warnings are advisory."""
    errors, warnings = [], []

    profile = data.get("language_profile", {})
    policy = profile.get("reading_policy", "none")
    reading_system = (profile.get("reading_system") or "none").lower()
    reading_expected = policy == "every_line" and reading_system != "none"

    # Every key sentence must be traceable to the transcript.
    for i, ks in enumerate(data.get("key_sentences", [])):
        loc = f"key_sentences[{i}] ({ks.get('id', '?')})"
        if not ks.get("source_refs"):
            errors.append(f"{loc}: empty source_refs — every item must cite the message(s) it came from")
        if reading_expected and not (ks.get("reading") or "").strip():
            errors.append(f"{loc}: reading_policy is 'every_line' but 'reading' is empty")

    # Vocabulary: reading expected on each term under every_line; examples should be traceable.
    for i, v in enumerate(data.get("vocabulary", [])):
        loc = f"vocabulary[{i}] ({v.get('term', '?')})"
        if reading_expected and not (v.get("reading") or "").strip():
            warnings.append(f"{loc}: reading_policy is 'every_line' but term 'reading' is empty")
        if "source_refs" in v and not v["source_refs"]:
            warnings.append(f"{loc}: source_refs present but empty")

    # Corrections must carry both sides and a source ref, or they are not verifiable.
    for i, c in enumerate(data.get("corrections", [])):
        loc = f"corrections[{i}] ({c.get('id', '?')})"
        if not (c.get("learner_original") or "").strip():
            errors.append(f"{loc}: learner_original is empty")
        if not (c.get("teacher_correction") or "").strip():
            errors.append(f"{loc}: teacher_correction is empty")
        if not c.get("source_refs"):
            errors.append(f"{loc}: empty source_refs")

    review = data.get("review", {})

    # Quiz: correct_index must point at a real option.
    for i, q in enumerate(review.get("quiz", [])):
        loc = f"review.quiz[{i}] ({q.get('id', '?')})"
        opts = q.get("options", [])
        idx = q.get("correct_index")
        if isinstance(idx, int) and not (0 <= idx < len(opts)):
            errors.append(f"{loc}: correct_index {idx} out of range for {len(opts)} options")

    # Fill-in-the-blank: the blank should actually be removed from the sentence.
    for i, fb in enumerate(review.get("fill_blank", [])):
        loc = f"review.fill_blank[{i}] ({fb.get('id', '?')})"
        if "____" not in (fb.get("sentence") or "") and "___" not in (fb.get("sentence") or ""):
            warnings.append(f"{loc}: sentence has no blank marker ('____')")

    # Review should not be empty across the board — that is a silent failure.
    if not any(review.get(k) for k in ("flashcards", "fill_blank", "translation_drills", "quiz")):
        warnings.append("review: all review arrays are empty")

    # Stable, unique IDs across every item (the UI/SRS tracks progress by ID).
    seen, dupes = set(), set()
    for coll in ("key_sentences", "corrections"):
        for item in data.get(coll, []):
            _id = item.get("id")
            if _id in seen:
                dupes.add(_id)
            seen.add(_id)
    for coll in ("flashcards", "fill_blank", "translation_drills", "quiz"):
        for item in review.get(coll, []):
            _id = item.get("id")
            if _id in seen:
                dupes.add(_id)
            seen.add(_id)
    if dupes:
        errors.append(f"duplicate item id(s): {', '.join(sorted(str(d) for d in dupes))}")

    return errors, warnings


def main():
    ap = argparse.ArgumentParser(description="Validate a lesson-data.v2 study pack")
    ap.add_argument("input", help="Path to the study-pack JSON file")
    ap.add_argument("--schema", default=None, help="Path to the lesson-data schema")
    args = ap.parse_args()

    schema_path = args.schema or DEFAULT_SCHEMA
    data = load_json(args.input)
    schema = load_json(schema_path)

    s_errors = schema_errors(data, schema)
    f_errors, f_warnings = ([], [])
    if not s_errors:
        # Fidelity lints assume a well-formed document; skip if the shape is wrong.
        f_errors, f_warnings = fidelity_lints(data)

    print(f"== Validating {os.path.basename(args.input)} ==")
    if s_errors:
        print(f"\nSCHEMA: {len(s_errors)} error(s)")
        for e in s_errors:
            print(f"  ✗ {e}")
    else:
        print("\nSCHEMA: ok")

    if f_errors:
        print(f"\nFIDELITY: {len(f_errors)} error(s)")
        for e in f_errors:
            print(f"  ✗ {e}")
    if f_warnings:
        print(f"\nFIDELITY: {len(f_warnings)} warning(s)")
        for w in f_warnings:
            print(f"  ⚠ {w}")
    if not s_errors and not f_errors and not f_warnings:
        print("FIDELITY: ok")

    failed = bool(s_errors or f_errors)
    print(f"\nRESULT: {'FAIL' if failed else 'PASS'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

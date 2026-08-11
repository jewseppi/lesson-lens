"""run_all.py — full local pipeline: parse → validate → generate → quality-check.

Chains the individual pipeline scripts against a single run id, wiring their
output paths together so you don't have to. Each stage is invoked as a
subprocess, so this orchestrator stays decoupled from the scripts' internals.

Layout produced:
    processed/<run-id>/sessions.json                      (parse)
    processed/<run-id>/normalized_messages.jsonl          (parse)
    summaries/<run-id>/<session-id>/lesson-data.json      (generate)

Usage:
    python scripts/run_all.py --input raw-exports/DOC-20260307-WA0006 --run-id 2026-03-07_01
    python scripts/run_all.py --input export.txt --session 2024-01-16 --provider anthropic
    python scripts/run_all.py --input export.txt --skip-generate   # parse + validate only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def _run(step: str, cmd: list[str]) -> None:
    print(f"\n=== {step} ===")
    print("$ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{step} failed (exit {result.returncode})")


def _script(name: str) -> str:
    return os.path.join(SCRIPTS, name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full LessonLens pipeline: parse → validate → generate → quality-check"
    )
    parser.add_argument("--input", required=True, help="Path to the chat export file")
    parser.add_argument("--run-id", default=None, help="Run identifier (default: auto-generated)")
    parser.add_argument("--config", default=None, help="Path to pipeline.yaml config")
    parser.add_argument("--session", default=None, help="Only generate/check this session id (default: all)")
    parser.add_argument("--provider", default=None, help="Generation provider (openai, anthropic, gemini, ...)")
    parser.add_argument("--model", default=None, help="Generation model override")
    parser.add_argument("--skip-generate", action="store_true", help="Stop after validate (no LLM calls)")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to generation (no LLM calls)")
    args = parser.parse_args()

    run_id = args.run_id or datetime.now().strftime("%Y-%m-%d_%H%M%S")
    py = sys.executable

    processed_dir = os.path.join(ROOT, "processed", run_id)
    sessions_json = os.path.join(processed_dir, "sessions.json")
    jsonl = os.path.join(processed_dir, "normalized_messages.jsonl")

    # 1) parse
    parse_cmd = [py, _script("parse_line_export.py"), "--input", args.input, "--run-id", run_id]
    if args.config:
        parse_cmd += ["--config", args.config]
    _run("parse", parse_cmd)

    # 2) validate
    validate_cmd = [py, _script("validate_sessions.py"), "--input", sessions_json]
    if os.path.isfile(jsonl):
        validate_cmd += ["--jsonl", jsonl]
    _run("validate", validate_cmd)

    if args.skip_generate:
        print("\nStopped after validate (--skip-generate).")
        return

    # 3) generate
    gen_cmd = [py, _script("generate_outputs.py"), "--sessions", sessions_json, "--run-id", run_id]
    if args.session:
        gen_cmd += ["--session-id", args.session]
    if args.provider:
        gen_cmd += ["--provider", args.provider]
    if args.model:
        gen_cmd += ["--model", args.model]
    if args.config:
        gen_cmd += ["--config", args.config]
    if args.dry_run:
        gen_cmd += ["--dry-run"]
    _run("generate", gen_cmd)

    if args.dry_run:
        print("\nDry run — skipping quality-check.")
        return

    # 4) quality-check each generated lesson package
    summaries_base = os.path.join(ROOT, "summaries", run_id)
    session_ids: list[str] = []
    try:
        with open(sessions_json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        session_ids = [s["session_id"] for s in data.get("sessions", [])]
    except (OSError, json.JSONDecodeError, KeyError):
        session_ids = []
    if args.session:
        session_ids = [sid for sid in session_ids if sid == args.session]

    checked = 0
    for sid in session_ids:
        lesson_json = os.path.join(summaries_base, sid, "lesson-data.json")
        if not os.path.isfile(lesson_json):
            continue
        _run(
            f"quality-check {sid}",
            [py, _script("quality_check.py"), "--input", lesson_json, "--sessions", sessions_json],
        )
        checked += 1

    print(f"\n=== pipeline complete (run-id {run_id}) — {checked} session(s) checked ===")


if __name__ == "__main__":
    main()

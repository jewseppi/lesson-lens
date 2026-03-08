"""Repo-local bridge for syncing a LINE export and generating study materials.

Usage:
    python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider openai
    python scripts/agent_bridge.py --file /absolute/path/to/export.txt --session-id 2026-03-05 --provider anthropic
    python scripts/agent_bridge.py --file /absolute/path/to/export.txt --provider gemini
    python scripts/agent_bridge.py --file /absolute/path/to/export.txt --manual-agent
    python scripts/agent_bridge.py --install-lesson-json /absolute/path/to/lesson-data.json --session-id 2026-03-05

This script uses the Flask app test client directly, so it does not require the
development server to be running.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.app import DB_PATH, app  # noqa: E402
from scripts.generate_outputs import build_transcript_text, load_config  # noqa: E402
from scripts.install_manual_summary import main as install_manual_summary_main  # noqa: E402


def ensure_admin_user(email: str, password: str, display_name: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, is_admin) VALUES (?, ?, ?, 1)",
            (email, generate_password_hash(password), display_name),
        )
        conn.commit()
    finally:
        conn.close()


def login(client, email: str, password: str) -> str:
    response = client.post(
        "/api/login",
        json={"email": email, "password": password},
    )
    if response.status_code != 200:
        raise SystemExit(f"Login failed: {response.get_json()}")
    return response.get_json()["access_token"]


def sync_file(client, token: str, file_path: str) -> dict:
    with open(file_path, "rb") as handle:
        response = client.post(
            "/api/sync",
            headers={"Authorization": f"Bearer {token}"},
            data={"file": (handle, os.path.basename(file_path))},
            content_type="multipart/form-data",
        )
    if response.status_code not in (200, 201):
        raise SystemExit(f"Sync failed: {response.get_json()}")
    return response.get_json()


def choose_session(client, token: str, session_id: str | None) -> str:
    response = client.get(
        "/api/sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code != 200:
        raise SystemExit(f"Could not load sessions: {response.get_json()}")

    sessions = response.get_json()
    if not sessions:
        raise SystemExit("No sessions available after sync.")

    if session_id:
        match = next((item for item in sessions if item["session_id"] == session_id), None)
        if not match:
            raise SystemExit(f"Session not found: {session_id}")
        return match["session_id"]

    return sessions[0]["session_id"]


def generate_summary(client, token: str, session_id: str, provider: str, model: str | None) -> tuple[int, dict]:
    payload: dict[str, str] = {"provider": provider}
    if model:
        payload["model"] = model

    response = client.post(
        f"/api/sessions/{session_id}/generate",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    return response.status_code, response.get_json()


def fetch_session_detail(client, token: str, session_id: str) -> dict:
    response = client.get(
        f"/api/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code != 200:
        raise SystemExit(f"Could not load session detail: {response.get_json()}")
    return response.get_json()


def manual_output_dir(session_id: str) -> Path:
    run_id = datetime.now(timezone.utc).strftime("manual-agent-%Y%m%d-%H%M%S")
    return Path(ROOT) / "summaries" / run_id / session_id


def lesson_template(session: dict) -> dict:
    config = load_config()
    return {
        "schema_version": "lesson-data.v1",
        "lesson_id": f"lesson-{session['session_id']}",
        "lesson_date": session["date"],
        "title": "",
        "source_session_ids": [session["session_id"]],
        "language_mode": {
            "script": config.get("language", {}).get("script", "traditional"),
            "pinyin_policy": config.get("language", {}).get("pinyin_policy", "every_line"),
            "translation_language": config.get("language", {}).get("translation_language", "english"),
        },
        "summary": {
            "overview": "",
            "usage_notes": "",
            "short_recap": "",
        },
        "key_sentences": [],
        "vocabulary": [],
        "corrections": [],
        "review": {
            "flashcards": [],
            "fill_blank": [],
            "translation_drills": [],
            "quiz": [],
        },
        "assets": {
            "markdown_path": "summary.md",
            "html_path": "summary.html",
            "flashcards_csv_path": "flashcards.csv",
        },
        "generation_meta": {
            "provider": "copilot-agent",
            "model": "GPT-5.4",
            "prompt_version": "v1-manual-agent",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": "manual-agent",
            "temperature": 0,
            "post_edit_notes": "Fill this payload manually from the parsed transcript, then install it into SQLite.",
        },
    }


def prepare_manual_bundle(client, token: str, session_id: str) -> Path:
    session = fetch_session_detail(client, token, session_id)
    out_dir = manual_output_dir(session_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "source-session.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "transcript.txt").write_text(
        build_transcript_text(session),
        encoding="utf-8",
    )
    (out_dir / "lesson-data.template.json").write_text(
        json.dumps(lesson_template(session), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_dir


def install_manual_lesson(lesson_json_path: str, session_id: str) -> None:
    argv = sys.argv[:]
    try:
        sys.argv = [
            "install_manual_summary.py",
            "--lesson-json",
            lesson_json_path,
            "--session-id",
            session_id,
        ]
        install_manual_summary_main()
    finally:
        sys.argv = argv


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync a LINE export and generate lesson summary assets")
    parser.add_argument("--file", help="Absolute path to the LINE export text file")
    parser.add_argument("--install-lesson-json", default=None, help="Install a manually-authored lesson-data.json into the app")
    parser.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default="openai")
    parser.add_argument("--model", default=None, help="Optional model override")
    parser.add_argument("--session-id", default=None, help="Generate a specific session instead of latest")
    parser.add_argument("--sync-only", action="store_true", help="Sync and parse only, skip summary generation")
    parser.add_argument("--manual-agent", action="store_true", help="Prepare a manual-agent work bundle instead of calling a provider")
    parser.add_argument("--email", default="admin@lessonlens.local")
    parser.add_argument("--password", default="adminpassword1")
    parser.add_argument("--display-name", default="Admin")
    args = parser.parse_args()

    if args.install_lesson_json:
        if not args.session_id:
            raise SystemExit("--session-id is required with --install-lesson-json")
        install_manual_lesson(os.path.abspath(args.install_lesson_json), args.session_id)
        return

    if not args.file:
        raise SystemExit("--file is required unless --install-lesson-json is used")

    file_path = os.path.abspath(args.file)
    if not os.path.isfile(file_path):
        raise SystemExit(f"File not found: {file_path}")

    ensure_admin_user(args.email, args.password, args.display_name)

    with app.test_client() as client:
        token = login(client, args.email, args.password)
        sync_result = sync_file(client, token, file_path)
        print(json.dumps({"sync": sync_result}, ensure_ascii=False, indent=2))

        if args.sync_only:
            return

        target_session_id = choose_session(client, token, args.session_id)
        print(json.dumps({"selected_session": target_session_id}, ensure_ascii=False, indent=2))

        if args.manual_agent:
            out_dir = prepare_manual_bundle(client, token, target_session_id)
            print(
                json.dumps(
                    {
                        "selected_session": target_session_id,
                        "manual_bundle_dir": str(out_dir),
                        "files": {
                            "source_session": str(out_dir / "source-session.json"),
                            "transcript": str(out_dir / "transcript.txt"),
                            "lesson_template": str(out_dir / "lesson-data.template.json"),
                        },
                        "next_step": f"Author lesson-data.json in {out_dir} and install it with --install-lesson-json",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        status_code, generate_result = generate_summary(
            client, token, target_session_id, args.provider, args.model
        )
        if status_code not in (200, 201):
            raise SystemExit(
                json.dumps(
                    {
                        "selected_session": target_session_id,
                        "generation_error": generate_result,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        print(
            json.dumps(
                {
                    "selected_session": target_session_id,
                    "title": generate_result.get("title"),
                    "lesson_date": generate_result.get("lesson_date"),
                    "key_sentences": len(generate_result.get("key_sentences", [])),
                    "vocabulary": len(generate_result.get("vocabulary", [])),
                    "corrections": len(generate_result.get("corrections", [])),
                    "review_assets": list(generate_result.get("review", {}).keys()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
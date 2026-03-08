"""Install a manually-authored lesson-data.json into generated assets and SQLite."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_outputs import generate_csv, generate_html, generate_markdown


def install_summary_data(
    lesson: dict,
    lesson_path: Path,
    session_id: str,
    provider: str = "copilot-agent",
    model: str = "GPT-5.4",
    run_id: str | None = None,
    user_id: int | None = None,
) -> None:
    root = lesson_path.parents[3]
    out_dir = lesson_path.parent

    generate_markdown(lesson, str(out_dir / "summary.md"))
    generate_html(lesson, str(out_dir / "summary.html"))
    generate_csv(lesson, str(out_dir / "flashcards.csv"))

    db_path = root / "api" / "lessonlens.db"
    conn = sqlite3.connect(db_path)
    try:
        conditions = ["s.session_id = ?"]
        params: list[object] = [session_id]
        if run_id is not None:
            conditions.append("s.run_id = ?")
            params.append(run_id)
        if user_id is not None:
            conditions.append("pr.user_id = ?")
            params.append(user_id)

        row = conn.execute(
            f"""
            select s.id, s.run_id, pr.user_id
            from sessions s
            join parse_runs pr on s.run_id = pr.run_id
            where {' and '.join(conditions)}
            order by s.id desc
            limit 1
            """,
            tuple(params),
        ).fetchone()
        if row is None:
            raise SystemExit(f"session row not found for {session_id}")

        conn.execute(
            "delete from lesson_summaries where session_id = ? and run_id = ?",
            (session_id, row[1]),
        )
        conn.execute(
            """
            insert into lesson_summaries
            (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row[0],
                row[1],
                session_id,
                row[2],
                provider,
                model,
                json.dumps(lesson, ensure_ascii=False),
                str(out_dir),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def install_summary_file(lesson_json_path: str, session_id: str, provider: str = "copilot-agent", model: str = "GPT-5.4") -> None:
    lesson_path = Path(lesson_json_path).resolve()
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    install_summary_data(lesson, lesson_path, session_id, provider=provider, model=model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install manual summary into app storage")
    parser.add_argument("--lesson-json", required=True, help="Path to lesson-data.json")
    parser.add_argument("--session-id", required=True, help="Session id, e.g. 2026-03-05")
    args = parser.parse_args()

    install_summary_file(args.lesson_json, args.session_id)

    print(f"installed summary for {args.session_id}")


if __name__ == "__main__":
    main()
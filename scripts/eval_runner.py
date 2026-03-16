#!/usr/bin/env python3
"""
Evaluation runner for LessonLens summary quality.

Runs a model against baseline sessions and computes metrics:
- schema_valid: Does the output conform to lesson-data schema?
- content_coverage: Are key sections present and non-empty?
- pedagogical_structure: Are exercises, flashcards, drills present?
- hallucination_proxy: Do vocab/sentences reference actual session content?
- latency: Time to generate (seconds)

Usage:
    python scripts/eval_runner.py --provider ollama --model qwen2.5:7b
    python scripts/eval_runner.py --provider openai --model gpt-4o --sessions 5
"""
import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "api"))

from generate_outputs import load_config, process_session, PROVIDERS


# ---------------------------------------------------------------------------
# Metric computations
# ---------------------------------------------------------------------------

def score_schema_valid(lesson_data: dict) -> tuple[float, dict]:
    """Check that the output has the expected top-level fields."""
    required = ["title", "summary", "key_sentences", "vocabulary", "corrections", "review"]
    present = [f for f in required if f in lesson_data]
    score = len(present) / len(required)
    return score, {"present": present, "missing": [f for f in required if f not in present]}


def score_content_coverage(lesson_data: dict) -> tuple[float, dict]:
    """Check that key content sections are populated (non-empty)."""
    checks = {
        "title": bool(lesson_data.get("title")),
        "overview": bool((lesson_data.get("summary") or {}).get("overview")),
        "key_sentences": len(lesson_data.get("key_sentences", [])) > 0,
        "vocabulary": len(lesson_data.get("vocabulary", [])) > 0,
    }
    score = sum(checks.values()) / len(checks)
    return score, checks


def score_pedagogical_structure(lesson_data: dict) -> tuple[float, dict]:
    """Check that study/review assets are present."""
    review = lesson_data.get("review", {})
    checks = {
        "flashcards": len(review.get("flashcards", [])) > 0,
        "fill_blank": len(review.get("fill_blank", [])) > 0,
        "translation_drills": len(review.get("translation_drills", [])) > 0,
        "quiz": len(review.get("quiz", [])) > 0,
    }
    score = sum(checks.values()) / len(checks)
    return score, checks


def score_hallucination_proxy(lesson_data: dict, session_data: dict) -> tuple[float, dict]:
    """
    Rough hallucination check: do vocabulary terms appear somewhere in the
    original session messages?
    """
    # Build a text corpus from the session messages
    messages = session_data.get("messages", [])
    corpus = " ".join(m.get("text_raw", "") + " " + m.get("text_normalized", "") for m in messages).lower()

    vocab = lesson_data.get("vocabulary", [])
    if not vocab:
        return 1.0, {"checked": 0, "found": 0}

    found = 0
    for v in vocab:
        term = (v.get("term_zh") or "").lower()
        if term and term in corpus:
            found += 1

    score = found / len(vocab) if vocab else 1.0
    return score, {"checked": len(vocab), "found": found}


def compute_metrics(lesson_data: dict, session_data: dict, latency: float) -> list[dict]:
    """Compute all metrics for a single session evaluation."""
    metrics = []

    score, meta = score_schema_valid(lesson_data)
    metrics.append({"metric_name": "schema_valid", "metric_value": score, "metric_meta_json": json.dumps(meta)})

    score, meta = score_content_coverage(lesson_data)
    metrics.append({"metric_name": "content_coverage", "metric_value": score, "metric_meta_json": json.dumps(meta)})

    score, meta = score_pedagogical_structure(lesson_data)
    metrics.append({"metric_name": "pedagogical_structure", "metric_value": score, "metric_meta_json": json.dumps(meta)})

    score, meta = score_hallucination_proxy(lesson_data, session_data)
    metrics.append({"metric_name": "hallucination_proxy", "metric_value": score, "metric_meta_json": json.dumps(meta)})

    metrics.append({"metric_name": "latency", "metric_value": latency, "metric_meta_json": "{}"})

    return metrics


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_eval(provider: str, model: str, max_sessions: int = 0,
             language: str = "zh", dataset_name: str = "default",
             db_path: str | None = None):
    """
    Run evaluation: generate summaries for baseline sessions and score them.
    Returns (run_id, summary_dict, per_session_scores).
    """
    import sqlite3

    if db_path is None:
        db_path = os.path.join(ROOT_DIR, "api", "lessonlens.db")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get latest completed parse run
    run_row = conn.execute(
        "SELECT * FROM parse_runs WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()
    if not run_row:
        print("No completed parse runs found.", file=sys.stderr)
        conn.close()
        return None

    # Load sessions payload
    sessions_file = os.path.join(run_row["output_dir"], "sessions.json")
    if not os.path.exists(sessions_file):
        print(f"Sessions file not found: {sessions_file}", file=sys.stderr)
        conn.close()
        return None

    with open(sessions_file, "r", encoding="utf-8") as f:
        all_sessions = json.load(f)

    if isinstance(all_sessions, list):
        sessions_by_id = {s["session_id"]: s for s in all_sessions}
    else:
        sessions_by_id = all_sessions

    # Get session rows with enough lesson content
    session_rows = conn.execute(
        """SELECT * FROM sessions WHERE run_id = ? AND lesson_content_count >= 3
           ORDER BY date DESC""",
        (run_row["run_id"],),
    ).fetchall()

    if max_sessions > 0:
        session_rows = session_rows[:max_sessions]

    if not session_rows:
        print("No eligible sessions found.", file=sys.stderr)
        conn.close()
        return None

    # Create eval run record
    conn.execute(
        """INSERT INTO model_eval_runs (provider, model, language, dataset_name, session_count, status)
           VALUES (?, ?, ?, ?, ?, 'running')""",
        (provider, model, language, dataset_name, len(session_rows)),
    )
    eval_run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    config = load_config()
    gen_defaults = config.get("generation", {})
    temperature = gen_defaults.get("temperature", 0.3)

    call_llm = PROVIDERS.get(provider)
    if not call_llm:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        conn.execute("UPDATE model_eval_runs SET status = 'failed', error_message = ? WHERE id = ?",
                      (f"Unknown provider: {provider}", eval_run_id))
        conn.commit()
        conn.close()
        return None

    all_metrics = []
    failures = 0
    total_latency = 0.0

    for i, sr in enumerate(session_rows):
        sid = sr["session_id"]
        session_data = sessions_by_id.get(sid)
        if not session_data:
            print(f"  [{i+1}/{len(session_rows)}] {sid}: session data not found, skipping")
            failures += 1
            continue

        print(f"  [{i+1}/{len(session_rows)}] {sid}: generating...")
        run_id_gen = f"eval_{eval_run_id}_{sid}"
        output_base = os.path.join(ROOT_DIR, "summaries", f"eval_{eval_run_id}")

        t0 = time.time()
        try:
            result = process_session(
                session_data, config, provider, model, temperature,
                run_id_gen, output_base,
            )
            latency = time.time() - t0
            total_latency += latency

            lesson_json_path = os.path.join(result["output_dir"], "lesson-data.json")
            with open(lesson_json_path, "r", encoding="utf-8") as f:
                lesson_data = json.load(f)

            metrics = compute_metrics(lesson_data, session_data, latency)
            for m in metrics:
                conn.execute(
                    """INSERT INTO model_eval_scores (eval_run_id, session_id, metric_name, metric_value, metric_meta_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (eval_run_id, sid, m["metric_name"], m["metric_value"], m["metric_meta_json"]),
                )
                all_metrics.append({**m, "session_id": sid})

            print(f"    ✓ {latency:.1f}s")
        except Exception as e:
            latency = time.time() - t0
            total_latency += latency
            failures += 1
            print(f"    ✗ failed ({latency:.1f}s): {e}")
            # Record failure as 0 scores
            for mname in ["schema_valid", "content_coverage", "pedagogical_structure", "hallucination_proxy"]:
                conn.execute(
                    """INSERT INTO model_eval_scores (eval_run_id, session_id, metric_name, metric_value, metric_meta_json)
                       VALUES (?, ?, ?, 0, ?)""",
                    (eval_run_id, sid, mname, json.dumps({"error": str(e)})),
                )
            conn.execute(
                """INSERT INTO model_eval_scores (eval_run_id, session_id, metric_name, metric_value, metric_meta_json)
                   VALUES (?, ?, 'latency', ?, '{}')""",
                (eval_run_id, sid, latency),
            )

        conn.commit()

    # Compute aggregate summary
    successful = len(session_rows) - failures
    summary = {
        "sessions_total": len(session_rows),
        "sessions_successful": successful,
        "sessions_failed": failures,
        "avg_latency": round(total_latency / len(session_rows), 2) if session_rows else 0,
    }

    # Average per-metric
    metric_names = ["schema_valid", "content_coverage", "pedagogical_structure", "hallucination_proxy"]
    for mn in metric_names:
        rows = conn.execute(
            "SELECT AVG(metric_value) as avg_val FROM model_eval_scores WHERE eval_run_id = ? AND metric_name = ?",
            (eval_run_id, mn),
        ).fetchone()
        summary[f"avg_{mn}"] = round(rows["avg_val"], 4) if rows["avg_val"] is not None else 0

    conn.execute(
        "UPDATE model_eval_runs SET status = 'completed', completed_at = datetime('now'), summary_json = ? WHERE id = ?",
        (json.dumps(summary), eval_run_id),
    )
    conn.commit()
    conn.close()

    print(f"\nEval run {eval_run_id} complete: {successful}/{len(session_rows)} sessions")
    print(f"Summary: {json.dumps(summary, indent=2)}")

    return eval_run_id, summary, all_metrics


def main():
    parser = argparse.ArgumentParser(description="Run model evaluation")
    parser.add_argument("--provider", required=True, help="LLM provider (openai, anthropic, ollama, etc.)")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--sessions", type=int, default=0, help="Max sessions to evaluate (0=all)")
    parser.add_argument("--language", default="zh", help="Target language code")
    parser.add_argument("--dataset", default="default", help="Dataset name tag")
    parser.add_argument("--db", default=None, help="Database path (default: api/lessonlens.db)")

    args = parser.parse_args()

    result = run_eval(
        provider=args.provider,
        model=args.model,
        max_sessions=args.sessions,
        language=args.language,
        dataset_name=args.dataset,
        db_path=args.db,
    )

    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()

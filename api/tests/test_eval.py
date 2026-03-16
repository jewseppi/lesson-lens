"""
Tests for Phase 2 Evaluation Harness: eval tables, API endpoints, and metric computation.
"""
import json

import pytest

from tests.conftest import auth_header


class TestEvalTables:
    """Verify eval tables exist after init_db."""

    def test_model_eval_runs_table_exists(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_eval_runs'"
        ).fetchall()
        assert len(rows) == 1

    def test_model_eval_scores_table_exists(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_eval_scores'"
        ).fetchall()
        assert len(rows) == 1

    def test_eval_runs_columns(self, db):
        info = db.execute("PRAGMA table_info(model_eval_runs)").fetchall()
        columns = {row["name"] for row in info}
        assert "provider" in columns
        assert "model" in columns
        assert "language" in columns
        assert "dataset_name" in columns
        assert "status" in columns
        assert "summary_json" in columns
        assert "session_count" in columns

    def test_eval_scores_columns(self, db):
        info = db.execute("PRAGMA table_info(model_eval_scores)").fetchall()
        columns = {row["name"] for row in info}
        assert "eval_run_id" in columns
        assert "session_id" in columns
        assert "metric_name" in columns
        assert "metric_value" in columns
        assert "metric_meta_json" in columns


class TestListEvalRuns:
    """Test GET /api/eval/runs."""

    def test_list_empty(self, client, admin_token):
        r = client.get("/api/eval/runs", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_list_unauthenticated(self, client):
        r = client.get("/api/eval/runs")
        assert r.status_code == 401

    def test_list_non_admin(self, client, user_token):
        r = client.get("/api/eval/runs", headers=auth_header(user_token))
        assert r.status_code == 403

    def test_list_after_create(self, client, admin_token):
        # Create a run
        client.post(
            "/api/eval/runs",
            json={"provider": "ollama", "model": "test:7b"},
            headers=auth_header(admin_token),
        )
        r = client.get("/api/eval/runs", headers=auth_header(admin_token))
        assert r.status_code == 200
        runs = r.get_json()
        assert len(runs) == 1
        assert runs[0]["provider"] == "ollama"
        assert runs[0]["model"] == "test:7b"
        assert runs[0]["status"] == "pending"


class TestCreateEvalRun:
    """Test POST /api/eval/runs."""

    def test_create_run(self, client, admin_token):
        r = client.post(
            "/api/eval/runs",
            json={"provider": "openai", "model": "gpt-4o", "language": "zh"},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["id"] > 0
        assert body["status"] == "pending"

    def test_create_run_missing_fields(self, client, admin_token):
        r = client.post(
            "/api/eval/runs",
            json={"provider": "openai"},
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_create_run_non_admin(self, client, user_token):
        r = client.post(
            "/api/eval/runs",
            json={"provider": "openai", "model": "gpt-4o"},
            headers=auth_header(user_token),
        )
        assert r.status_code == 403


class TestGetEvalRun:
    """Test GET /api/eval/runs/<id>."""

    def test_get_run_not_found(self, client, admin_token):
        r = client.get("/api/eval/runs/9999", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_get_run_with_scores(self, client, admin_token, db):
        # Insert a run with scores directly
        db.execute(
            """INSERT INTO model_eval_runs (provider, model, language, dataset_name, session_count, status, summary_json)
               VALUES ('ollama', 'qwen:7b', 'zh', 'test', 1, 'completed', '{}')"""
        )
        run_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            """INSERT INTO model_eval_scores (eval_run_id, session_id, metric_name, metric_value, metric_meta_json)
               VALUES (?, 'sess-1', 'schema_valid', 0.85, '{}')""",
            (run_id,),
        )
        db.execute(
            """INSERT INTO model_eval_scores (eval_run_id, session_id, metric_name, metric_value, metric_meta_json)
               VALUES (?, 'sess-1', 'content_coverage', 0.75, '{}')""",
            (run_id,),
        )
        db.commit()

        r = client.get(f"/api/eval/runs/{run_id}", headers=auth_header(admin_token))
        assert r.status_code == 200
        body = r.get_json()
        assert body["provider"] == "ollama"
        assert body["model"] == "qwen:7b"
        assert "scores_by_session" in body
        assert "sess-1" in body["scores_by_session"]
        assert body["scores_by_session"]["sess-1"]["schema_valid"]["value"] == 0.85

    def test_get_run_non_admin(self, client, user_token):
        r = client.get("/api/eval/runs/1", headers=auth_header(user_token))
        assert r.status_code == 403


class TestEvalScorecard:
    """Test GET /api/eval/scorecard."""

    def test_scorecard_empty(self, client, admin_token):
        r = client.get("/api/eval/scorecard", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_scorecard_with_data(self, client, admin_token, db):
        # Insert completed run with scores
        db.execute(
            """INSERT INTO model_eval_runs (provider, model, language, dataset_name, session_count, status, summary_json)
               VALUES ('ollama', 'qwen:7b', 'zh', 'test', 2, 'completed', '{}')"""
        )
        run_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for sid in ["sess-1", "sess-2"]:
            for metric, val in [("schema_valid", 0.9), ("content_coverage", 0.8)]:
                db.execute(
                    "INSERT INTO model_eval_scores (eval_run_id, session_id, metric_name, metric_value) VALUES (?, ?, ?, ?)",
                    (run_id, sid, metric, val),
                )
        db.commit()

        r = client.get("/api/eval/scorecard", headers=auth_header(admin_token))
        assert r.status_code == 200
        entries = r.get_json()
        assert len(entries) == 1
        assert entries[0]["provider"] == "ollama"
        assert entries[0]["model"] == "qwen:7b"
        assert entries[0]["metrics"]["schema_valid"] == 0.9
        assert entries[0]["metrics"]["content_coverage"] == 0.8

    def test_scorecard_non_admin(self, client, user_token):
        r = client.get("/api/eval/scorecard", headers=auth_header(user_token))
        assert r.status_code == 403


class TestEvalMetrics:
    """Test the metric computation functions from eval_runner.py."""

    def test_schema_valid_all_present(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        from eval_runner import score_schema_valid

        lesson = {
            "title": "Test", "summary": {"overview": "x"}, "key_sentences": [{"zh": "a"}],
            "vocabulary": [{"term_zh": "b"}], "corrections": [], "review": {"flashcards": []},
        }
        score, meta = score_schema_valid(lesson)
        assert score == 1.0
        assert len(meta["missing"]) == 0

    def test_schema_valid_missing_fields(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        from eval_runner import score_schema_valid

        lesson = {"title": "Test"}
        score, meta = score_schema_valid(lesson)
        assert score < 1.0
        assert len(meta["missing"]) > 0

    def test_content_coverage(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        from eval_runner import score_content_coverage

        lesson = {
            "title": "Test", "summary": {"overview": "something"},
            "key_sentences": [{"zh": "a"}], "vocabulary": [{"term_zh": "b"}],
        }
        score, meta = score_content_coverage(lesson)
        assert score == 1.0

    def test_hallucination_proxy(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        from eval_runner import score_hallucination_proxy

        lesson = {"vocabulary": [{"term_zh": "你好"}, {"term_zh": "再見"}]}
        session = {"messages": [{"text_raw": "你好世界", "text_normalized": "你好世界"}]}
        score, meta = score_hallucination_proxy(lesson, session)
        # "你好" found, "再見" not found
        assert score == 0.5
        assert meta["found"] == 1
        assert meta["checked"] == 2

    def test_pedagogical_structure(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        from eval_runner import score_pedagogical_structure

        lesson = {"review": {
            "flashcards": [{"front": "a", "back": "b"}],
            "fill_blank": [{"sentence": "x"}],
            "translation_drills": [],
            "quiz": [{"question": "q"}],
        }}
        score, _ = score_pedagogical_structure(lesson)
        assert score == 0.75  # 3 out of 4 present

"""
Tests for Phase 4: Retrieval Index, Feedback Memory, and Context Injection.
"""
import json
import os

import pytest

from tests.conftest import auth_header


class TestRetrievalTables:
    """Verify retrieval tables exist after init_db."""

    def test_retrieval_items_table(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_retrieval_items'"
        ).fetchall()
        assert len(rows) == 1

    def test_feedback_memory_table(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_feedback_memory'"
        ).fetchall()
        assert len(rows) == 1

    def test_retrieval_columns(self, db):
        info = db.execute("PRAGMA table_info(user_retrieval_items)").fetchall()
        columns = {row["name"] for row in info}
        for col in ["user_id", "session_id", "item_type", "item_key", "item_data_json", "source"]:
            assert col in columns, f"Missing column: {col}"

    def test_feedback_memory_columns(self, db):
        info = db.execute("PRAGMA table_info(user_feedback_memory)").fetchall()
        columns = {row["name"] for row in info}
        for col in ["user_id", "session_id", "action", "target_type", "target_id",
                     "original_json", "corrected_json", "detail"]:
            assert col in columns, f"Missing column: {col}"


def _ensure_user(db, user_id=1):
    """Ensure a test user exists."""
    from werkzeug.security import generate_password_hash
    existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO users (id, email, password_hash, display_name, is_admin, status) VALUES (?, ?, ?, ?, 1, 'active')",
            (user_id, f"test{user_id}@test.local", generate_password_hash("Test1234!", method="scrypt"), "Test"),
        )
        db.commit()
    return user_id


class TestIndexRetrievalItems:
    """Test _index_retrieval_items function."""

    def test_indexes_vocab(self, db):
        from app import _index_retrieval_items
        uid = _ensure_user(db)

        lesson_data = {
            "vocabulary": [
                {"term": "你好", "pinyin": "nǐ hǎo", "meaning": "hello", "pos": "phrase"},
                {"term": "谢谢", "pinyin": "xiè xiè", "meaning": "thank you", "pos": "verb"},
            ],
            "key_sentences": [],
            "corrections": [],
        }
        count = _index_retrieval_items(db, uid, "2025-01-15", lesson_data)
        assert count == 2

        rows = db.execute(
            "SELECT * FROM user_retrieval_items WHERE user_id = 1 AND item_type = 'vocab'"
        ).fetchall()
        assert len(rows) == 2
        terms = {r["item_key"] for r in rows}
        assert "你好" in terms
        assert "谢谢" in terms

    def test_indexes_key_sentences(self, db):
        from app import _index_retrieval_items
        _ensure_user(db)

        lesson_data = {
            "vocabulary": [],
            "key_sentences": [
                {"zh": "我很喜欢学中文", "pinyin": "wǒ hěn xǐhuān xué zhōngwén", "en": "I love learning Chinese"},
            ],
            "corrections": [],
        }
        count = _index_retrieval_items(db, 1, "2025-01-16", lesson_data)
        assert count == 1

        rows = db.execute(
            "SELECT * FROM user_retrieval_items WHERE user_id = 1 AND item_type = 'key_sentence'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["item_key"] == "我很喜欢学中文"

    def test_indexes_corrections(self, db):
        from app import _index_retrieval_items
        _ensure_user(db)

        lesson_data = {
            "vocabulary": [],
            "key_sentences": [],
            "corrections": [
                {"student_said": "我是好", "correct_form": "我很好", "explanation": "Use 很 not 是 before adjectives"},
            ],
        }
        count = _index_retrieval_items(db, 1, "2025-01-17", lesson_data)
        assert count == 1

        rows = db.execute(
            "SELECT * FROM user_retrieval_items WHERE user_id = 1 AND item_type = 'correction'"
        ).fetchall()
        assert len(rows) == 1
        data = json.loads(rows[0]["item_data_json"])
        assert data["correct_form"] == "我很好"

    def test_regeneration_clears_old(self, db):
        from app import _index_retrieval_items
        _ensure_user(db)

        lesson_data_v1 = {"vocabulary": [{"term": "旧词", "pinyin": "jiù cí", "meaning": "old word"}], "key_sentences": [], "corrections": []}
        lesson_data_v2 = {"vocabulary": [{"term": "新词", "pinyin": "xīn cí", "meaning": "new word"}], "key_sentences": [], "corrections": []}

        _index_retrieval_items(db, 1, "2025-01-18", lesson_data_v1)
        _index_retrieval_items(db, 1, "2025-01-18", lesson_data_v2)

        rows = db.execute(
            "SELECT * FROM user_retrieval_items WHERE user_id = 1 AND session_id = '2025-01-18'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["item_key"] == "新词"


class TestRetrieveContext:
    """Test _retrieve_context_for_session function."""

    def test_retrieve_matching_vocab(self, db):
        from app import _index_retrieval_items, _retrieve_context_for_session
        _ensure_user(db)

        # Index vocab from a prior session
        _index_retrieval_items(db, 1, "2025-01-10", {
            "vocabulary": [
                {"term": "你好", "pinyin": "nǐ hǎo", "meaning": "hello"},
                {"term": "再见", "pinyin": "zài jiàn", "meaning": "goodbye"},
            ],
            "key_sentences": [],
            "corrections": [],
        })

        # Current session contains 你好
        session_data = {
            "messages": [
                {"message_id": "msg-001", "text_normalized": "老师说你好吗"},
            ],
        }

        context = _retrieve_context_for_session(db, 1, "2025-01-15", session_data)
        assert len(context["prior_vocab"]) > 0
        terms = [v["term"] for v in context["prior_vocab"]]
        assert "你好" in terms

    def test_no_self_retrieval(self, db):
        from app import _index_retrieval_items, _retrieve_context_for_session
        _ensure_user(db)

        # Index vocab for session "2025-01-15"
        _index_retrieval_items(db, 1, "2025-01-15", {
            "vocabulary": [{"term": "你好", "pinyin": "nǐ hǎo", "meaning": "hello"}],
            "key_sentences": [],
            "corrections": [],
        })

        # Retrieve for the same session — should NOT include its own vocab
        session_data = {"messages": [{"text_normalized": "你好吗"}]}
        context = _retrieve_context_for_session(db, 1, "2025-01-15", session_data)
        assert len(context["prior_vocab"]) == 0

    def test_retrieve_feedback_patterns(self, db):
        from app import _record_feedback_memory, _retrieve_context_for_session
        _ensure_user(db)

        _record_feedback_memory(
            db, 1, "2025-01-10", "correct", "vocab",
            target_id="v-1", original="eat", corrected="have (a meal)",
            detail="More precise translation",
        )
        db.commit()

        session_data = {"messages": [{"text_normalized": "吃饭了吗"}]}
        context = _retrieve_context_for_session(db, 1, "2025-01-15", session_data)
        assert len(context["feedback_patterns"]) > 0


class TestBuildRetrievalContextBlock:
    """Test the text block builder."""

    def test_empty_context(self):
        from app import build_retrieval_context_block
        assert build_retrieval_context_block({}) == ""
        assert build_retrieval_context_block({"prior_vocab": [], "prior_corrections": []}) == ""

    def test_vocab_block(self):
        from app import build_retrieval_context_block
        context = {
            "prior_vocab": [
                {"term": "你好", "pinyin": "nǐ hǎo", "meaning": "hello", "from_session": "2025-01-10"},
            ],
        }
        text = build_retrieval_context_block(context)
        assert "Prior Vocabulary" in text
        assert "你好" in text
        assert "nǐ hǎo" in text

    def test_corrections_block(self):
        from app import build_retrieval_context_block
        context = {
            "prior_corrections": [
                {"student_said": "我是好", "correct_form": "我很好", "explanation": "adjective rule"},
            ],
        }
        text = build_retrieval_context_block(context)
        assert "Common Student Errors" in text
        assert "我是好" in text


class TestFeedbackMemoryRecording:
    """Test that annotations and AI review acceptances record feedback memory."""

    def test_annotation_correction_records_memory(self, client, admin_token, db):
        """Creating a correction annotation should record feedback memory."""
        from tests.conftest import ADMIN_EMAIL
        user = db.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
        db.execute(
            "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) "
            "VALUES (?, 'test.txt', 'test.txt', 'hash-ret-test', 100, 4)",
            (user["id"],),
        )
        upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO parse_runs (run_id, user_id, upload_id, status, output_dir) VALUES ('r1', ?, ?, 'completed', '/tmp')",
            (user["id"], upload_id),
        )
        db.execute(
            "INSERT INTO sessions (run_id, user_id, session_id, date) VALUES ('r1', ?, '2025-01-15', '2025-01-15')",
            (user["id"],),
        )
        db.commit()

        r = client.post("/api/sessions/2025-01-15/annotations", json={
            "target_type": "vocabulary",
            "target_id": "vocab-1",
            "annotation_type": "correction",
            "content": {
                "field": "en",
                "original": "eat",
                "corrected": "have a meal",
                "reason": "More contextual translation",
            },
        }, headers=auth_header(admin_token))
        assert r.status_code == 201

        # Check feedback memory was recorded
        rows = db.execute(
            "SELECT * FROM user_feedback_memory WHERE user_id = ?",
            (user["id"],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "correct"
        assert rows[0]["target_type"] == "vocabulary"

    def test_annotation_note_no_memory(self, client, admin_token, db):
        """Creating a note annotation should NOT record feedback memory."""
        from tests.conftest import ADMIN_EMAIL
        user = db.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
        # Use session from previous test or create fresh
        existing_run = db.execute("SELECT run_id FROM parse_runs WHERE user_id = ?", (user["id"],)).fetchone()
        if not existing_run:
            db.execute(
                "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) "
                "VALUES (?, 'test.txt', 'test.txt', 'hash-ret-note', 100, 4)",
                (user["id"],),
            )
            uid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO parse_runs (run_id, user_id, upload_id, status, output_dir) VALUES ('r2', ?, ?, 'completed', '/tmp')",
                (user["id"], uid),
            )
            db.execute(
                "INSERT INTO sessions (run_id, user_id, session_id, date) VALUES ('r2', ?, '2025-01-15', '2025-01-15')",
                (user["id"],),
            )
            db.commit()

        r = client.post("/api/sessions/2025-01-15/annotations", json={
            "target_type": "message",
            "target_id": "msg-001",
            "annotation_type": "note",
            "content": {"text": "Good example sentence"},
        }, headers=auth_header(admin_token))
        assert r.status_code == 201

        rows = db.execute(
            "SELECT * FROM user_feedback_memory WHERE user_id = ?",
            (user["id"],),
        ).fetchall()
        assert len(rows) == 0


class TestRetrievalStatsEndpoint:
    def test_stats_empty(self, client, admin_token):
        r = client.get("/api/retrieval/stats", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert data["total_items"] == 0
        assert data["sessions_indexed"] == 0

    def test_stats_with_data(self, client, admin_token, db):
        from tests.conftest import ADMIN_EMAIL
        user = db.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()

        db.execute(
            "INSERT INTO user_retrieval_items (user_id, session_id, item_type, item_key, item_data_json) VALUES (?, '2025-01-15', 'vocab', '你好', '{}')",
            (user["id"],),
        )
        db.execute(
            "INSERT INTO user_retrieval_items (user_id, session_id, item_type, item_key, item_data_json) VALUES (?, '2025-01-15', 'vocab', '谢谢', '{}')",
            (user["id"],),
        )
        db.execute(
            "INSERT INTO user_feedback_memory (user_id, session_id, action, target_type) VALUES (?, '2025-01-15', 'correct', 'vocab')",
            (user["id"],),
        )
        db.commit()

        r = client.get("/api/retrieval/stats", headers=auth_header(admin_token))
        data = r.get_json()
        assert data["total_items"] == 2
        assert data["sessions_indexed"] == 1
        assert data["items_by_type"]["vocab"] == 2
        assert data["feedback_by_action"]["correct"] == 1

    def test_unauthenticated(self, client):
        assert client.get("/api/retrieval/stats").status_code == 401


class TestRetrievalContextEndpoint:
    def test_no_data(self, client, admin_token):
        """With no run data, returns empty context."""
        r = client.get("/api/sessions/2025-01-15/retrieval-context", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert data["text"] == "" or data["context"] == {}

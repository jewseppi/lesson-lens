"""
Tests for the LessonLens MCP server tools.

Calls tool functions directly (they're plain Python functions) using
the same temp-DB approach as the rest of the test suite.
"""
import json
import os
import sys

import pytest

# Ensure api/ is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "scripts"))

from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
TEST_EMAIL = "mcp-test@test.local"


@pytest.fixture()
def mcp_env(tmp_path, monkeypatch):
    """Set up an isolated DB and configure the MCP server module."""
    db_path = str(tmp_path / "mcp_test.db")

    import app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_path)

    # Import mcp_server AFTER patching DB_PATH (it imports app at module level
    # but we already patched it)
    import mcp_server
    monkeypatch.setattr(mcp_server, "USER_EMAIL", TEST_EMAIL)
    # Also ensure mcp_server._app_module uses the right DB_PATH
    monkeypatch.setattr(mcp_server._app_module, "DB_PATH", db_path)

    from app import init_db, get_db

    # Create Flask app context for init_db (it uses app.app_context internally)
    from app import app
    app.config["TESTING"] = True
    with app.app_context():
        init_db()

    # Seed a test user
    conn = get_db()
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin, status) VALUES (?, ?, ?, 0, 'active')",
        (TEST_EMAIL, generate_password_hash("TestMCP!LongPassword123", method="scrypt"), "MCP Test"),
    )
    conn.commit()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", (TEST_EMAIL,)).fetchone()["id"]
    conn.close()

    return {"db_path": db_path, "user_id": user_id, "mcp": mcp_server}


@pytest.fixture()
def seeded_env(mcp_env, tmp_path):
    """Seed a parse run with sessions and summaries."""
    from app import get_db

    conn = get_db()
    user_id = mcp_env["user_id"]

    # Create output dir with sessions.json
    output_dir = str(tmp_path / "processed" / "run1")
    os.makedirs(output_dir, exist_ok=True)

    sessions_data = {
        "sessions": [
            {
                "session_id": "2025-01-15",
                "date": "2025-01-15",
                "start_time": "14:00",
                "end_time": "15:00",
                "message_count": 10,
                "lesson_content_count": 8,
                "messages": [
                    {"message_id": "msg-001", "time": "14:01", "speaker_role": "teacher",
                     "message_type": "lesson-content", "text_raw": "你好", "text_normalized": "你好"},
                    {"message_id": "msg-002", "time": "14:02", "speaker_role": "student",
                     "message_type": "lesson-content", "text_raw": "你好老师", "text_normalized": "你好老师"},
                    {"message_id": "msg-003", "time": "14:03", "speaker_role": "teacher",
                     "message_type": "lesson-content", "text_raw": "今天我们学习食物", "text_normalized": "今天我们学习食物"},
                ],
            },
            {
                "session_id": "2025-01-13",
                "date": "2025-01-13",
                "start_time": "10:00",
                "end_time": "11:00",
                "message_count": 5,
                "lesson_content_count": 3,
                "messages": [
                    {"message_id": "msg-010", "time": "10:01", "speaker_role": "teacher",
                     "message_type": "lesson-content", "text_raw": "早安", "text_normalized": "早安"},
                ],
            },
        ]
    }
    with open(os.path.join(output_dir, "sessions.json"), "w") as f:
        json.dump(sessions_data, f)

    # Upload record
    conn.execute(
        "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) "
        "VALUES (?, 'test.txt', 'test.txt', 'hash-mcp', 100, 10)",
        (user_id,),
    )
    upload_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Parse run
    conn.execute(
        "INSERT INTO parse_runs (run_id, user_id, upload_id, status, output_dir, session_count) "
        "VALUES ('mcp-run-1', ?, ?, 'completed', ?, 2)",
        (user_id, upload_id, output_dir),
    )

    # Session rows
    conn.execute(
        "INSERT INTO sessions (run_id, user_id, session_id, date, start_time, end_time, message_count, "
        "lesson_content_count, teacher_message_count, student_message_count, is_archived, boundary_confidence, topics_json) "
        "VALUES ('mcp-run-1', ?, '2025-01-15', '2025-01-15', '14:00', '15:00', 10, 8, 5, 5, 0, 'high', ?)",
        (user_id, json.dumps(["greetings", "food vocabulary"])),
    )
    conn.execute(
        "INSERT INTO sessions (run_id, user_id, session_id, date, start_time, end_time, message_count, "
        "lesson_content_count, teacher_message_count, student_message_count, is_archived, boundary_confidence, topics_json) "
        "VALUES ('mcp-run-1', ?, '2025-01-13', '2025-01-13', '10:00', '11:00', 5, 3, 3, 2, 0, 'high', ?)",
        (user_id, json.dumps(["morning greetings"])),
    )

    # Summary for first session
    lesson_data = {
        "schema_version": "lesson-data.v1",
        "lesson_id": "2025-01-15",
        "lesson_date": "2025-01-15",
        "title": "Food Vocabulary Lesson",
        "summary": {"overview": "Learned food vocabulary.", "usage_notes": "Use 吃 for eating.", "short_recap": "Food words."},
        "vocabulary": [
            {"term_zh": "你好", "pinyin": "nǐ hǎo", "en": "hello", "pos_or_type": "phrase", "example_zh": "你好吗", "example_en": "How are you"},
            {"term_zh": "食物", "pinyin": "shí wù", "en": "food", "pos_or_type": "noun", "example_zh": "我喜欢食物", "example_en": "I like food"},
        ],
        "key_sentences": [
            {"zh": "今天我们学习食物", "pinyin": "jīntiān wǒmen xuéxí shíwù", "en": "Today we study food"},
        ],
        "corrections": [
            {"learner_original": "我吃好", "teacher_correction": "我吃得好", "reason": "Need 得 complement"},
        ],
        "review": {
            "flashcards": [{"front": "你好", "back": "hello"}],
            "fill_blank": [],
            "translation_drills": [],
            "quiz": [{"question": "What does 食物 mean?", "options": ["food", "drink", "money", "time"], "correct_index": 0}],
        },
    }
    session_db_id = conn.execute("SELECT id FROM sessions WHERE session_id = '2025-01-15' AND user_id = ?", (user_id,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO lesson_summaries (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir) "
        "VALUES (?, 'mcp-run-1', '2025-01-15', ?, 'openai', 'gpt-4o', ?, ?)",
        (session_db_id, user_id, json.dumps(lesson_data, ensure_ascii=False), output_dir),
    )

    conn.commit()
    conn.close()

    mcp_env["output_dir"] = output_dir
    mcp_env["lesson_data"] = lesson_data
    return mcp_env


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------
class TestUserResolution:
    def test_valid_user(self, mcp_env):
        from app import get_db
        conn = get_db()
        try:
            user, err = mcp_env["mcp"]._get_user(conn)
            assert err is None
            assert user["email"] == TEST_EMAIL
        finally:
            conn.close()

    def test_missing_email(self, mcp_env, monkeypatch):
        monkeypatch.setattr(mcp_env["mcp"], "USER_EMAIL", "")
        from app import get_db
        conn = get_db()
        try:
            user, err = mcp_env["mcp"]._get_user(conn)
            assert user is None
            assert "not set" in err
        finally:
            conn.close()

    def test_unknown_email(self, mcp_env, monkeypatch):
        monkeypatch.setattr(mcp_env["mcp"], "USER_EMAIL", "nobody@test.local")
        from app import get_db
        conn = get_db()
        try:
            user, err = mcp_env["mcp"]._get_user(conn)
            assert user is None
            assert "not found" in err
        finally:
            conn.close()

    def test_suspended_user(self, mcp_env):
        from app import get_db
        conn = get_db()
        conn.execute("UPDATE users SET status = 'suspended' WHERE email = ?", (TEST_EMAIL,))
        conn.commit()
        try:
            user, err = mcp_env["mcp"]._get_user(conn)
            assert user is None
            assert "suspended" in err
        finally:
            conn.execute("UPDATE users SET status = 'active' WHERE email = ?", (TEST_EMAIL,))
            conn.commit()
            conn.close()


# ---------------------------------------------------------------------------
# Session tool tests
# ---------------------------------------------------------------------------
class TestListSessions:
    def test_no_parse_run(self, mcp_env):
        result = mcp_env["mcp"].list_sessions()
        assert "Error" in result
        assert "No parsed data" in result

    def test_list_with_data(self, seeded_env):
        result = seeded_env["mcp"].list_sessions()
        assert "Found 2 sessions" in result
        assert "2025-01-15" in result
        assert "2025-01-13" in result
        assert "1 with summaries" in result

    def test_list_excludes_archived(self, seeded_env):
        from app import get_db
        conn = get_db()
        conn.execute("UPDATE sessions SET is_archived = 1 WHERE session_id = '2025-01-13'")
        conn.commit()
        conn.close()

        result = seeded_env["mcp"].list_sessions(include_archived=False)
        assert "Found 1 sessions" in result
        assert "2025-01-13" not in result

    def test_list_includes_archived(self, seeded_env):
        from app import get_db
        conn = get_db()
        conn.execute("UPDATE sessions SET is_archived = 1 WHERE session_id = '2025-01-13'")
        conn.commit()
        conn.close()

        result = seeded_env["mcp"].list_sessions(include_archived=True)
        assert "Found 2 sessions" in result


class TestGetSession:
    def test_get_existing(self, seeded_env):
        result = seeded_env["mcp"].get_session("2025-01-15")
        assert "Session: 2025-01-15" in result
        assert "Transcript" in result
        assert "Teacher" in result
        assert "你好" in result

    def test_get_nonexistent(self, seeded_env):
        result = seeded_env["mcp"].get_session("2099-01-01")
        assert "Error" in result
        assert "not found" in result

    def test_get_without_messages(self, seeded_env):
        result = seeded_env["mcp"].get_session("2025-01-15", include_messages=False)
        assert "Session: 2025-01-15" in result
        assert "Transcript" not in result

    def test_max_messages(self, seeded_env):
        result = seeded_env["mcp"].get_session("2025-01-15", max_messages=1)
        assert "Transcript" in result
        assert "more messages" in result


class TestGetSessionSummary:
    def test_get_existing_summary(self, seeded_env):
        result = seeded_env["mcp"].get_session_summary("2025-01-15")
        assert "Summary for 2025-01-15" in result
        assert "Vocabulary" in result
        assert "你好" in result
        assert "食物" in result
        assert "Key Sentences" in result
        assert "Corrections" in result
        assert "Flashcards" in result
        assert "Quiz" in result

    def test_no_summary(self, seeded_env):
        result = seeded_env["mcp"].get_session_summary("2025-01-13")
        assert "No summary" in result
        assert "generate_summary" in result


class TestSearchSessions:
    def test_search_by_topic(self, seeded_env):
        result = seeded_env["mcp"].search_sessions("food")
        assert "Found 1 sessions" in result
        assert "2025-01-15" in result

    def test_search_by_date(self, seeded_env):
        result = seeded_env["mcp"].search_sessions("2025-01")
        assert "Found 2 sessions" in result

    def test_search_no_match(self, seeded_env):
        result = seeded_env["mcp"].search_sessions("quantum physics")
        assert "No sessions matching" in result


# ---------------------------------------------------------------------------
# Retrieval tools
# ---------------------------------------------------------------------------
class TestGetRetrievalContext:
    def test_no_prior_context(self, seeded_env):
        result = seeded_env["mcp"].get_retrieval_context("2025-01-15")
        assert "No prior context" in result or "Retrieval context" in result

    def test_with_indexed_data(self, seeded_env):
        """Index vocab for session 1, then retrieve context for session 2."""
        from app import get_db, _index_retrieval_items
        conn = get_db()
        _index_retrieval_items(conn, seeded_env["user_id"], "2025-01-13", {
            "vocabulary": [{"term": "你好", "pinyin": "nǐ hǎo", "meaning": "hello"}],
            "key_sentences": [],
            "corrections": [],
        })
        conn.commit()
        conn.close()

        result = seeded_env["mcp"].get_retrieval_context("2025-01-15")
        # Session 2025-01-15 transcript contains 你好, and 你好 is indexed from 2025-01-13
        assert "Retrieval context" in result or "Prior Vocabulary" in result


class TestGetRetrievalStats:
    def test_empty_stats(self, seeded_env):
        result = seeded_env["mcp"].get_retrieval_stats()
        assert "Total items: 0" in result

    def test_stats_with_data(self, seeded_env):
        from app import get_db, _index_retrieval_items
        conn = get_db()
        _index_retrieval_items(conn, seeded_env["user_id"], "2025-01-15", {
            "vocabulary": [{"term": "你好", "pinyin": "nǐ hǎo", "meaning": "hello"}],
            "key_sentences": [],
            "corrections": [],
        })
        conn.commit()
        conn.close()

        result = seeded_env["mcp"].get_retrieval_stats()
        assert "Total items: 1" in result
        assert "Sessions indexed: 1" in result


# ---------------------------------------------------------------------------
# Annotation tools
# ---------------------------------------------------------------------------
class TestAddAnnotation:
    def test_add_note(self, seeded_env):
        result = seeded_env["mcp"].add_annotation(
            session_id="2025-01-15",
            target_type="message",
            target_id="msg-001",
            annotation_type="note",
            content="Great opening sentence",
        )
        assert "Annotation created" in result
        assert "note" in result

    def test_add_correction(self, seeded_env):
        result = seeded_env["mcp"].add_annotation(
            session_id="2025-01-15",
            target_type="vocabulary",
            target_id="食物",
            annotation_type="correction",
            original="food",
            corrected="cuisine/food",
            reason="More nuanced translation",
        )
        assert "Annotation created" in result
        assert "correction" in result

        # Check feedback memory was recorded
        from app import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM user_feedback_memory WHERE user_id = ?",
            (seeded_env["user_id"],),
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["action"] == "correct"

    def test_invalid_type(self, seeded_env):
        result = seeded_env["mcp"].add_annotation(
            session_id="2025-01-15",
            target_type="message",
            target_id="msg-001",
            annotation_type="invalid",
        )
        assert "Error" in result


class TestListAnnotations:
    def test_empty(self, seeded_env):
        result = seeded_env["mcp"].list_annotations("2025-01-15")
        assert "No annotations" in result

    def test_with_annotations(self, seeded_env):
        seeded_env["mcp"].add_annotation(
            session_id="2025-01-15",
            target_type="message",
            target_id="msg-001",
            annotation_type="note",
            content="Test note",
        )
        result = seeded_env["mcp"].list_annotations("2025-01-15")
        assert "1 total" in result
        assert "note" in result
        assert "msg-001" in result


# ---------------------------------------------------------------------------
# Review tools
# ---------------------------------------------------------------------------
class TestListReviews:
    def test_no_reviews(self, seeded_env):
        result = seeded_env["mcp"].list_reviews("2025-01-15")
        assert "No reviews" in result

    def test_with_review(self, seeded_env):
        """Create a review manually and verify list_reviews finds it."""
        from app import get_db
        conn = get_db()
        findings = [
            {"message_id": "msg-001", "current_type": "lesson-content",
             "suggested_type": "logistics", "confidence": 0.8,
             "reason": "This looks like logistics", "status": "pending"},
        ]
        conn.execute(
            "INSERT INTO ai_reviews (user_id, session_id, review_type, provider, model, "
            "findings_json, findings_count, accepted_count, dismissed_count, status) "
            "VALUES (?, '2025-01-15', 'parse', 'openai', 'gpt-4o', ?, 1, 0, 0, 'pending')",
            (seeded_env["user_id"], json.dumps(findings)),
        )
        conn.commit()
        conn.close()

        result = seeded_env["mcp"].list_reviews("2025-01-15")
        assert "1 total" in result
        assert "parse" in result
        assert "logistics" in result


class TestAcceptDismissFinding:
    def _create_review(self, user_id):
        from app import get_db
        conn = get_db()
        findings = [
            {"message_id": "msg-001", "current_type": "lesson-content",
             "suggested_type": "logistics", "confidence": 0.8,
             "reason": "Looks like logistics", "status": "pending"},
            {"message_id": "msg-002", "current_type": "lesson-content",
             "suggested_type": "other", "confidence": 0.6,
             "reason": "Generic message", "status": "pending"},
        ]
        conn.execute(
            "INSERT INTO ai_reviews (user_id, session_id, review_type, provider, model, "
            "findings_json, findings_count, accepted_count, dismissed_count, status) "
            "VALUES (?, '2025-01-15', 'parse', 'openai', 'gpt-4o', ?, 2, 0, 0, 'pending')",
            (user_id, json.dumps(findings)),
        )
        conn.commit()
        review_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return review_id

    def test_accept(self, seeded_env):
        review_id = self._create_review(seeded_env["user_id"])
        result = seeded_env["mcp"].accept_finding("2025-01-15", review_id, 0)
        assert "accepted" in result.lower()

    def test_dismiss(self, seeded_env):
        review_id = self._create_review(seeded_env["user_id"])
        result = seeded_env["mcp"].dismiss_finding("2025-01-15", review_id, 1)
        assert "dismissed" in result.lower()

    def test_out_of_range(self, seeded_env):
        review_id = self._create_review(seeded_env["user_id"])
        result = seeded_env["mcp"].accept_finding("2025-01-15", review_id, 99)
        assert "Error" in result
        assert "out of range" in result

    def test_already_accepted(self, seeded_env):
        review_id = self._create_review(seeded_env["user_id"])
        seeded_env["mcp"].accept_finding("2025-01-15", review_id, 0)
        result = seeded_env["mcp"].accept_finding("2025-01-15", review_id, 0)
        assert "Error" in result
        assert "already" in result

    def test_review_not_found(self, seeded_env):
        result = seeded_env["mcp"].accept_finding("2025-01-15", 99999, 0)
        assert "Error" in result
        assert "not found" in result

    def test_completes_review(self, seeded_env):
        """Accepting all findings should complete the review."""
        review_id = self._create_review(seeded_env["user_id"])
        seeded_env["mcp"].accept_finding("2025-01-15", review_id, 0)
        result = seeded_env["mcp"].dismiss_finding("2025-01-15", review_id, 1)
        assert "completed" in result.lower()


# ---------------------------------------------------------------------------
# Resource test
# ---------------------------------------------------------------------------
class TestResource:
    def test_schema_resource(self, mcp_env):
        text = mcp_env["mcp"].lesson_data_schema()
        assert "lesson-data.v1" in text
        assert "vocabulary" in text
        assert "corrections" in text
        assert "flashcards" in text

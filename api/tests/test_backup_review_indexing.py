"""Restoring a backup must also rebuild the Daily Review queue.

Three paths write a summary — generation, the per-session summary import, and
restoring a backup — and all three have to leave the account in the same state.
The backup path did not index retrieval items, so a restored account showed its
lessons and summaries but an empty Daily Review: indistinguishable, from the
outside, from the import having silently failed.
"""
import io
import json
import os
import sys
import zipfile

import pytest

# app.py imports install_manual_summary lazily from scripts/, so the path is only
# set up once that import runs. The fixture below needs it at collection time.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _lesson_data(session_id):
    """A minimal lesson-data.v1 payload with one card of each indexed type."""
    return {
        "schema_version": "lesson-data.v1",
        "lesson_id": session_id,
        "lesson_date": session_id,
        "source_session_ids": [session_id],
        "title": "Restored lesson",
        "language_mode": {"script": "traditional", "pinyin_policy": "numbered",
                          "translation_language": "en"},
        "summary": {"overview": "o", "short_recap": "r", "teacher_focus": "t",
                    "student_focus": "s", "usage_notes": "u"},
        "vocabulary": [{
            "term_zh": "冷氣", "pinyin": "leng3qi4", "en": "air conditioning",
            "pos_or_type": "noun", "example_zh": "哪裡都有冷氣。",
            "example_pinyin": "na3li3 dou1 you3 leng3qi4",
            "example_en": "There's AC everywhere.", "difficulty": "easy",
            "source_refs": [session_id],
        }],
        "key_sentences": [{
            "id": "ks-1", "zh": "又濕又熱", "pinyin": "you4 shi1 you4 re4",
            "en": "Both humid and hot.", "context_note": "n", "confidence": "high",
            "source_refs": [session_id],
        }],
        "grammar_patterns": [],
        "corrections": [],
        "review": {"flashcards": [], "quiz": [], "fill_blank": [], "translation_drills": []},
        "confidence_flags": [],
        "assets": {"markdown_path": "", "html_path": "", "flashcards_csv_path": "",
                   "anki_csv_path": "", "image_refs": []},
        "generation_meta": {"provider": "claude-agent", "model": "test",
                            "prompt_version": "v1", "temperature": "0",
                            "run_id": "t", "generated_at": "2026-01-01T00:00:00Z",
                            "post_edit_notes": ""},
    }


def _make_archive(session_id="2026-01-01"):
    """Build the smallest backup zip the import endpoint will accept."""
    buf = io.BytesIO()
    manifest = {
        "schema_version": "lessonlens-backup.v2",
        "exported_at": "2026-01-01T00:00:00+00:00",
        "source_user": "someone@example.com",
        "session_count": 1,
        "summary_count": 1,
        "attachment_count": 0,
        "attachments": [],
        "session_attachments": [],
        "latest_run": {
            "run_id": "run-restore-1",
            "source_filename": "chat.txt",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        "summaries": [{"session_id": session_id, "provider": "claude-agent", "model": "test"}],
    }
    sessions_payload = {
        "schema_version": "lesson-sessions.v1",
        "run_id": "run-restore-1",
        "parser_version": "test",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "source": {"filename": "chat.txt"},
        "stats": {},
        "warnings": [],
        "sessions": [{
            "session_id": session_id, "date": session_id, "start_time": "10:00",
            "end_time": "11:00", "message_count": 2, "student_message_count": 1,
            "teacher_message_count": 1, "lesson_content_count": 2,
            "logistics_count": 0, "media_count": 0, "links_count": 0,
            "boundary_confidence": "high", "messages": [],
        }],
    }
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("parse/sessions.json", json.dumps(sessions_payload))
        z.writestr("parse/parse_report.json", json.dumps({"sessions": 1}))
        z.writestr("parse/diagnostics.txt", "ok")
        z.writestr("parse/normalized_messages.jsonl", "")
        z.writestr("raw-exports/chat.txt", "chat")
        z.writestr(f"summaries/{session_id}.json", json.dumps(_lesson_data(session_id)))
    buf.seek(0)
    return buf


@pytest.fixture
def stub_install(monkeypatch):
    """Neutralise install_summary_data — it opens its own connection to the real
    DB_PATH, which the temp-database fixture doesn't cover, and it is not what
    these tests are about. Same pattern as test_retrieval_indexing.py.
    """
    import install_manual_summary

    monkeypatch.setattr(install_manual_summary, "install_summary_data", lambda *a, **k: None)


def _import(client, token, archive):
    return client.post(
        "/api/backup/import",
        data={"file": (archive, "backup.zip")},
        headers={"Authorization": f"Bearer {token}"},
        content_type="multipart/form-data",
    )


def test_restoring_a_backup_populates_the_review_queue(client, user_token, db, stub_install):
    """The regression: summaries arrived, Daily Review stayed empty."""
    before = db.execute("SELECT COUNT(*) FROM user_retrieval_items").fetchone()[0]

    resp = _import(client, user_token, _make_archive())
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["summary_count"] == 1

    rows = db.execute(
        "SELECT item_type, item_key FROM user_retrieval_items"
    ).fetchall()
    assert len(rows) > before, "restoring a backup indexed no review cards"
    types = {r["item_type"] for r in rows}
    assert "vocab" in types
    assert "key_sentence" in types


def test_restored_cards_are_served_by_the_review_queue(client, user_token, stub_install):
    """End to end: after a restore, the queue actually hands back cards."""
    _import(client, user_token, _make_archive())

    resp = client.get("/api/review/queue",
                      headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_items"] > 0, "review queue empty after restoring a backup"
    assert len(body["items"]) > 0
    answers = [
        i["data"].get("zh") or i["data"].get("term_zh") for i in body["items"]
    ]
    assert any(a in {"又濕又熱", "冷氣"} for a in answers)


def test_reimporting_the_same_backup_does_not_duplicate_cards(client, user_token, db, stub_install):
    """Re-running a restore is a normal thing to do; it must stay idempotent."""
    _import(client, user_token, _make_archive())
    after_first = db.execute(
        "SELECT COUNT(*) FROM user_retrieval_items"
    ).fetchone()[0]

    _import(client, user_token, _make_archive())
    after_second = db.execute(
        "SELECT COUNT(*) FROM user_retrieval_items"
    ).fetchone()[0]

    assert after_second == after_first, "re-importing duplicated review cards"

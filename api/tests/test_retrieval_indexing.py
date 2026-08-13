"""Retrieval indexing must work for agent-authored lessons and real schema names.

Three bugs lived here at once, and together they meant a subscription-agent
workflow produced an empty review queue:

1. ``/summary/import`` never indexed at all, so every agent-written lesson
   contributed nothing — and that path exists precisely so generation can run
   without a provider API key.
2. Vocabulary was read as ``term``; lesson-data.v1 calls it ``term_zh``.
3. Corrections were read as ``student_said``; the schema calls it
   ``learner_original``.

So of the three item types, only key sentences were ever indexed, and only on the
generate path. Since Daily Review and prior-context injection both read this
table, the effect was a review queue that stayed empty on real data.
"""
import json
import os
import sys

import pytest

# The import route adds scripts/ to sys.path at call time; do it up front so the
# fixture below can reach install_manual_summary to stub it.
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"
)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


SCHEMA_LESSON = {
    "schema_version": "lesson-data.v1",
    "lesson_id": "2026-08-12",
    "lesson_date": "2026-08-12",
    "title": "Rain and licences",
    "source_session_ids": ["2026-08-12"],
    "language_mode": {"script": "traditional", "pinyin_policy": "numbered",
                      "translation_language": "en"},
    "summary": {"overview": "o", "usage_notes": "u", "short_recap": "r"},
    "key_sentences": [
        {"id": "ks-1", "zh": "雨停了", "pinyin": "yu3 ting2 le",
         "en": "The rain stopped.", "source_refs": ["x"]},
    ],
    "vocabulary": [
        {"term_zh": "教師證", "pinyin": "jiao4shi1zheng4", "en": "teaching certificate",
         "pos_or_type": "noun", "example_zh": "她有教師證。",
         "example_en": "She has a teaching certificate."},
    ],
    "corrections": [
        {"id": "c-1", "learner_original": "我昨天去了公園了",
         "teacher_correction": "我昨天去公園了", "reason": "了 appears once",
         "source_refs": ["x"]},
    ],
    "review": {"flashcards": [], "fill_blank": [], "translation_drills": [], "quiz": []},
    "assets": {"markdown_path": "m", "html_path": "h", "flashcards_csv_path": "f"},
    "generation_meta": {"provider": "claude-agent", "model": "claude-opus-5",
                        "prompt_version": "v1", "generated_at": "2026-08-12T00:00:00Z",
                        "run_id": "r"},
}


def _index(db, user_id, lesson, session_id="2026-08-12"):
    import app as app_module

    app_module._index_retrieval_items(db, user_id, session_id, lesson)
    db.commit()
    return {
        row["item_type"]: json.loads(row["item_data_json"])
        for row in db.execute(
            "SELECT item_type, item_data_json FROM user_retrieval_items WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    }


class TestSchemaFieldNames:
    def test_vocabulary_is_indexed_from_term_zh(self, db, regular_user):
        by_type = _index(db, regular_user["id"], SCHEMA_LESSON)
        assert "vocab" in by_type, "lesson-data.v1 vocabulary must be indexed"
        assert by_type["vocab"]["term_zh"] == "教師證"
        assert by_type["vocab"]["en"] == "teaching certificate"

    def test_corrections_are_indexed_from_learner_original(self, db, regular_user):
        by_type = _index(db, regular_user["id"], SCHEMA_LESSON)
        assert "correction" in by_type, "the highest-value review type must be indexed"
        assert by_type["correction"]["learner_original"] == "我昨天去了公園了"
        assert by_type["correction"]["teacher_correction"] == "我昨天去公園了"

    def test_key_sentences_still_indexed(self, db, regular_user):
        by_type = _index(db, regular_user["id"], SCHEMA_LESSON)
        assert by_type["key_sentence"]["zh"] == "雨停了"

    def test_all_three_types_are_present(self, db, regular_user):
        by_type = _index(db, regular_user["id"], SCHEMA_LESSON)
        assert set(by_type) == {"vocab", "key_sentence", "correction"}

    def test_legacy_field_names_still_index(self, db, regular_user):
        """Lessons stored before the schema settled must still re-index."""
        legacy = dict(SCHEMA_LESSON)
        legacy["vocabulary"] = [
            {"term": "城市", "pinyin": "cheng2shi4", "meaning": "city",
             "pos": "noun", "example_sentence": "這個城市很大。"},
        ]
        legacy["corrections"] = [
            {"student_said": "我是很好", "correct_form": "我很好",
             "explanation": "no 是 before an adjective"},
        ]
        by_type = _index(db, regular_user["id"], legacy)
        assert by_type["vocab"]["term_zh"] == "城市"
        assert by_type["vocab"]["en"] == "city"
        assert by_type["correction"]["learner_original"] == "我是很好"
        assert by_type["correction"]["teacher_correction"] == "我很好"

    def test_reindexing_replaces_rather_than_duplicates(self, db, regular_user):
        _index(db, regular_user["id"], SCHEMA_LESSON)
        _index(db, regular_user["id"], SCHEMA_LESSON)
        count = db.execute(
            "SELECT COUNT(*) FROM user_retrieval_items WHERE user_id = ?",
            (regular_user["id"],),
        ).fetchone()[0]
        assert count == 3, "regeneration must not accumulate duplicates"


@pytest.fixture
def stub_install(monkeypatch):
    """Neutralise install_summary_data for route tests.

    It opens its own connection to the real DB_PATH, which the temp-database
    fixture doesn't cover — and it is not what these tests are about. The
    behaviour under test is that the route indexes retrieval items at all.
    """
    import install_manual_summary

    monkeypatch.setattr(
        install_manual_summary, "install_summary_data",
        lambda *a, **k: None,
    )


class TestImportPathIndexes:
    def _seed_run(self, db, user_id, session_id="2026-08-12"):
        db.execute(
            """INSERT INTO uploads
               (user_id, original_filename, stored_filename, file_hash, file_size, line_count)
               VALUES (?, 'c.txt', 'c.txt', 'h1', 10, 5)""",
            (user_id,),
        )
        upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            """INSERT INTO parse_runs
               (run_id, upload_id, user_id, status, session_count, message_count,
                output_dir, completed_at)
               VALUES ('run_imp', ?, ?, 'completed', 1, 10, '/tmp/test', datetime('now'))""",
            (upload_id, user_id),
        )
        db.execute(
            """INSERT INTO sessions
               (run_id, user_id, session_id, date, start_time, end_time,
                message_count, lesson_content_count, boundary_confidence)
               VALUES ('run_imp', ?, ?, '2026-08-12', '10:00', '11:00', 10, 8, 'high')""",
            (user_id, session_id),
        )
        db.commit()

    def test_agent_import_populates_the_review_corpus(
        self, client, user_token, db, regular_user, stub_install
    ):
        """The no-API-key path must feed Daily Review like generation does."""
        self._seed_run(db, regular_user["id"])
        payload = json.dumps(SCHEMA_LESSON, ensure_ascii=False).encode("utf-8")

        resp = client.post(
            "/api/sessions/2026-08-12/summary/import",
            headers={"Authorization": f"Bearer {user_token}"},
            data={
                "provider": "claude-agent",
                "model": "claude-opus-5",
                "file": (__import__("io").BytesIO(payload), "lesson-data.json"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201, resp.get_json()

        rows = db.execute(
            "SELECT item_type FROM user_retrieval_items WHERE user_id = ?",
            (regular_user["id"],),
        ).fetchall()
        assert rows, "an imported lesson contributed nothing to review"
        assert {r["item_type"] for r in rows} == {"vocab", "key_sentence", "correction"}

    def test_imported_lesson_shows_up_in_the_review_queue(
        self, client, user_token, db, regular_user, stub_install
    ):
        self._seed_run(db, regular_user["id"])
        payload = json.dumps(SCHEMA_LESSON, ensure_ascii=False).encode("utf-8")
        client.post(
            "/api/sessions/2026-08-12/summary/import",
            headers={"Authorization": f"Bearer {user_token}"},
            data={
                "provider": "claude-agent",
                "model": "claude-opus-5",
                "file": (__import__("io").BytesIO(payload), "lesson-data.json"),
            },
            content_type="multipart/form-data",
        )
        body = client.get(
            "/api/review/queue", headers={"Authorization": f"Bearer {user_token}"}
        ).get_json()
        assert body["total_items"] == 3
        # Corrections lead: the learner's own mistake is the best thing to re-see.
        assert body["items"][0]["item_type"] == "correction"

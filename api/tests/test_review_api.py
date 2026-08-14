"""Tests for the Daily Review endpoints.

The corpus is not duplicated: review joins user_retrieval_items, which is
already populated for every generated lesson. So these seed retrieval items and
assert the review layer picks them up with no migration or import step.
"""
import json

import pytest

import review_scheduler as rs


def _seed_items(db, user_id, items):
    """items: [(session_id, item_type, item_key, data_dict)]"""
    db.executemany(
        """INSERT INTO user_retrieval_items
           (user_id, session_id, item_type, item_key, item_data_json, source)
           VALUES (?, ?, ?, ?, ?, 'generation')""",
        [
            (user_id, sid, itype, key, json.dumps(data, ensure_ascii=False))
            for sid, itype, key, data in items
        ],
    )
    db.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


CORPUS = [
    ("2026-08-05", "correction", "wrong-one", {"student_said": "wrong-one", "correct_form": "right one"}),
    ("2026-08-05", "key_sentence", "sentence-one", {"zh": "sentence-one", "en": "Sentence one"}),
    ("2026-08-05", "vocab", "term-one", {"term_zh": "term-one", "en": "term one"}),
]


class TestQueue:
    def test_existing_lessons_are_reviewable_with_no_backfill(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert body["total_items"] == 3
        assert body["due_count"] == 3, "never-reviewed items are due today"

    def test_queue_is_ordered_by_value(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert [i["item_type"] for i in body["items"]] == [
            "correction", "key_sentence", "vocab",
        ]

    def test_a_term_taught_twice_is_one_review_item(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], [
            ("2026-08-05", "vocab", "repeat", {"term_zh": "repeat", "en": "first teaching"}),
            ("2026-08-12", "vocab", "repeat", {"term_zh": "repeat", "en": "second teaching"}),
        ])
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert body["total_items"] == 1
        # And it carries the newest teaching of that term.
        assert body["items"][0]["data"]["en"] == "second teaching"

    def test_time_box_bounds_the_queue(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], [
            # Needs a meaning: an item with no answer side is not served at all.
            ("2026-08-05", "vocab", f"term-{i}", {"term_zh": f"term-{i}", "en": f"meaning {i}"})
            for i in range(40)
        ])
        five = client.get("/api/review/queue?minutes=5", headers=_auth(user_token)).get_json()
        assert five["count"] == rs.DEFAULT_DAILY_TARGET

        one = client.get("/api/review/queue?minutes=1", headers=_auth(user_token)).get_json()
        assert one["count"] < five["count"], "a shorter box must ask for less"

    def test_empty_corpus_is_an_empty_queue_not_an_error(self, client, user_token):
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert body["items"] == []
        assert body["total_items"] == 0

    def test_queue_requires_auth(self, client):
        assert client.get("/api/review/queue").status_code == 401

    def test_another_users_items_are_not_reviewable(
        self, client, user_token, db, regular_user, admin_user
    ):
        _seed_items(db, admin_user["id"], CORPUS)
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert body["total_items"] == 0


class TestGrade:
    def test_good_schedules_it_forward_and_drops_it_from_the_queue(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        for _ in range(3):
            client.post(
                "/api/review/grade", headers=_auth(user_token),
                json={"item_key": "term-one", "grade": "good"},
            )
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert "term-one" not in [i["item_key"] for i in body["items"] if i["due_at"] is None]
        assert body["due_count"] == 2

    def test_intervals_grow_across_calls(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        seen = []
        for _ in range(3):
            resp = client.post(
                "/api/review/grade", headers=_auth(user_token),
                json={"item_key": "term-one", "grade": "good"},
            )
            seen.append(resp.get_json()["interval_days"])
        assert seen == sorted(seen) and seen[-1] > seen[0]

    def test_again_brings_it_straight_back(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        client.post("/api/review/grade", headers=_auth(user_token),
                    json={"item_key": "term-one", "grade": "good"})
        resp = client.post("/api/review/grade", headers=_auth(user_token),
                           json={"item_key": "term-one", "grade": "again"})
        body = resp.get_json()
        assert body["interval_days"] == 1.0
        assert body["streak"] == 0

    def test_grading_is_idempotent_per_row(self, client, user_token, db, regular_user):
        """Repeated grades update one row rather than accumulating duplicates."""
        _seed_items(db, regular_user["id"], CORPUS)
        for _ in range(4):
            client.post("/api/review/grade", headers=_auth(user_token),
                        json={"item_key": "term-one", "grade": "good"})
        count = db.execute(
            "SELECT COUNT(*) FROM review_schedule WHERE user_id = ? AND item_key = ?",
            (regular_user["id"], "term-one"),
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.parametrize("payload", [
        {"grade": "good"},
        {"item_key": "term-one"},
        {"item_key": "term-one", "grade": "sort-of"},
    ])
    def test_bad_payloads_are_rejected(self, client, user_token, db, regular_user, payload):
        _seed_items(db, regular_user["id"], CORPUS)
        resp = client.post("/api/review/grade", headers=_auth(user_token), json=payload)
        assert resp.status_code == 400

    def test_unknown_item_404s(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        resp = client.post("/api/review/grade", headers=_auth(user_token),
                           json={"item_key": "nope", "grade": "good"})
        assert resp.status_code == 404

    def test_cannot_grade_another_users_item(
        self, client, user_token, db, regular_user, admin_user
    ):
        _seed_items(db, admin_user["id"], CORPUS)
        resp = client.post("/api/review/grade", headers=_auth(user_token),
                           json={"item_key": "term-one", "grade": "good"})
        assert resp.status_code == 404


class TestCompleteAndStats:
    def test_completing_starts_the_streak(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        body = client.post("/api/review/complete", headers=_auth(user_token)).get_json()
        assert body["streak"] == 1
        assert body["target_increased"] is False

    def test_completing_twice_in_one_day_does_not_double_count(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        client.post("/api/review/complete", headers=_auth(user_token))
        body = client.post("/api/review/complete", headers=_auth(user_token)).get_json()
        assert body["streak"] == 1

    def test_three_day_streak_raises_the_target(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        # Two days already banked, yesterday being the most recent.
        db.execute(
            """INSERT INTO review_stats (user_id, daily_target, streak, last_completed_on)
               VALUES (?, ?, 2, date('now', '-1 day'))""",
            (regular_user["id"], rs.DEFAULT_DAILY_TARGET),
        )
        db.commit()
        body = client.post("/api/review/complete", headers=_auth(user_token)).get_json()
        assert body["streak"] == 3
        assert body["target_increased"] is True
        assert body["daily_target"] == rs.DEFAULT_DAILY_TARGET + rs.RAMP_STEP

    def test_stats_report_what_the_dashboard_card_needs(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        body = client.get("/api/review/stats", headers=_auth(user_token)).get_json()
        assert body["due_count"] == 3
        assert body["new_count"] == 3
        assert body["total_items"] == 3
        assert body["streak"] == 0
        assert body["completed_today"] is False

    def test_stats_track_completion_today(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        client.post("/api/review/complete", headers=_auth(user_token))
        body = client.get("/api/review/stats", headers=_auth(user_token)).get_json()
        assert body["completed_today"] is True

    def test_stats_requires_auth(self, client):
        assert client.get("/api/review/stats").status_code == 401


class TestSettings:
    def test_target_can_be_overridden(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        body = client.post("/api/review/settings", headers=_auth(user_token),
                           json={"daily_target": 12}).get_json()
        assert body["daily_target"] == 12

    def test_target_is_clamped_to_something_sane(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        high = client.post("/api/review/settings", headers=_auth(user_token),
                           json={"daily_target": 9999}).get_json()
        assert high["daily_target"] == rs.MAX_DAILY_TARGET
        low = client.post("/api/review/settings", headers=_auth(user_token),
                          json={"daily_target": 0}).get_json()
        assert low["daily_target"] == 1

    def test_non_numeric_target_is_rejected(self, client, user_token, db, regular_user):
        _seed_items(db, regular_user["id"], CORPUS)
        resp = client.post("/api/review/settings", headers=_auth(user_token),
                           json={"daily_target": "lots"})
        assert resp.status_code == 400

    def test_suspending_an_item_removes_it_from_review(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], CORPUS)
        client.post("/api/review/settings", headers=_auth(user_token),
                    json={"suspend_item_key": "term-one"})
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert "term-one" not in [i["item_key"] for i in body["items"]]
        assert body["total_items"] == 2


class TestUnusableItemsAreSkipped:
    """Older summaries carry a Chinese sentence with no translation and a pinyin
    of "n/a". Serving those produces a card whose reveal is a dash — nothing to
    recall, and no way to tell whether you got it right."""

    def test_sentence_without_translation_is_not_served(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], [
            ("2026-04-22", "key_sentence", "因為對我來說，味道有一點重",
             {"zh": "因為對我來說，味道有一點重", "pinyin": "n/a", "en": ""}),
            ("2026-04-22", "key_sentence", "雨停了",
             {"zh": "雨停了", "pinyin": "yu3 ting2 le", "en": "The rain stopped."}),
        ])
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        keys = [i["item_key"] for i in body["items"]]
        assert "雨停了" in keys
        assert "因為對我來說，味道有一點重" not in keys, "a card with no answer must not be served"
        assert body["total_items"] == 1, "and must not count toward the daily target"

    @pytest.mark.parametrize("placeholder", ["", "n/a", "N/A", "-", "—", "none", "null"])
    def test_placeholder_translations_count_as_missing(
        self, client, user_token, db, regular_user, placeholder
    ):
        _seed_items(db, regular_user["id"], [
            ("2026-04-22", "key_sentence", f"sentence-{placeholder or 'blank'}",
             {"zh": "有一點重", "pinyin": "n/a", "en": placeholder}),
        ])
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert body["total_items"] == 0

    def test_vocab_needs_a_meaning_or_an_example(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], [
            ("2026-04-22", "vocab", "bare", {"term_zh": "重", "pinyin": "zhong4", "en": "n/a"}),
            ("2026-04-22", "vocab", "with-example",
             {"term_zh": "味道", "pinyin": "wei4dao4", "en": "", "example_zh": "味道有一點重"}),
            ("2026-04-22", "vocab", "with-meaning", {"term_zh": "城市", "en": "city"}),
        ])
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        keys = {i["item_key"] for i in body["items"]}
        assert keys == {"with-example", "with-meaning"}

    def test_correction_needs_the_corrected_form(
        self, client, user_token, db, regular_user
    ):
        _seed_items(db, regular_user["id"], [
            ("2026-04-22", "correction", "no-fix",
             {"learner_original": "我是很好", "teacher_correction": ""}),
            ("2026-04-22", "correction", "has-fix",
             {"learner_original": "我是很好", "teacher_correction": "我很好"}),
        ])
        body = client.get("/api/review/queue", headers=_auth(user_token)).get_json()
        assert [i["item_key"] for i in body["items"]] == ["has-fix"]

    def test_stats_agree_with_the_queue(self, client, user_token, db, regular_user):
        """A due count that includes unservable cards is a target you can't hit."""
        _seed_items(db, regular_user["id"], [
            ("2026-04-22", "key_sentence", "broken", {"zh": "重", "pinyin": "n/a", "en": ""}),
            ("2026-04-22", "key_sentence", "fine", {"zh": "雨停了", "en": "It stopped."}),
        ])
        stats = client.get("/api/review/stats", headers=_auth(user_token)).get_json()
        assert stats["total_items"] == 1
        assert stats["due_count"] == 1

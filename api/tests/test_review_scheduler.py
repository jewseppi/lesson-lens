"""Unit tests for the Daily Review spacing, ordering, and ramp rules.

Pure functions over plain dicts, so these run without Flask, a database, or a
clock — every time-dependent case passes an explicit ``now``.
"""
from datetime import datetime, timedelta, timezone

import pytest

import review_scheduler as rs


NOW = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)


# --- spacing ---------------------------------------------------------------

class TestScheduleAfterGrade:
    def test_new_item_graded_good_comes_back_tomorrow(self):
        result = rs.schedule_after_grade({}, rs.GRADE_GOOD, now=NOW)
        assert result["interval_days"] == 1.0
        assert result["streak"] == 1
        assert result["due_at"].startswith("2026-08-13")

    def test_intervals_grow_with_repetition(self):
        state = {}
        intervals = []
        for _ in range(4):
            state = rs.schedule_after_grade(state, rs.GRADE_GOOD, now=NOW)
            intervals.append(state["interval_days"])
        assert intervals[0] == 1.0
        assert intervals == sorted(intervals), "each correct answer must push it further out"
        assert intervals[-1] > 5, f"growth too slow: {intervals}"

    def test_again_resets_to_tomorrow_and_counts_a_lapse(self):
        state = {"interval_days": 20, "ease": 2.5, "streak": 4, "lapses": 0}
        result = rs.schedule_after_grade(state, rs.GRADE_AGAIN, now=NOW)
        assert result["interval_days"] == 1.0
        assert result["streak"] == 0
        assert result["lapses"] == 1
        assert result["due_at"].startswith("2026-08-13")

    def test_again_lowers_ease_so_it_grows_slower_next_time(self):
        state = {"interval_days": 10, "ease": 2.5, "streak": 3}
        after = rs.schedule_after_grade(state, rs.GRADE_AGAIN, now=NOW)
        assert after["ease"] < 2.5

    def test_ease_is_clamped_at_both_ends(self):
        state = {"ease": rs.MIN_EASE, "interval_days": 1, "streak": 0}
        for _ in range(10):
            state = rs.schedule_after_grade(state, rs.GRADE_AGAIN, now=NOW)
        assert state["ease"] >= rs.MIN_EASE

        state = {"ease": rs.MAX_EASE, "interval_days": 1, "streak": 1}
        for _ in range(10):
            state = rs.schedule_after_grade(state, rs.GRADE_GOOD, now=NOW)
        assert state["ease"] <= rs.MAX_EASE

    def test_a_lapsed_item_is_not_buried(self):
        """After a lapse it must return quickly, while the confusion is fresh."""
        state = {"interval_days": 60, "ease": 2.5, "streak": 8}
        after = rs.schedule_after_grade(state, rs.GRADE_AGAIN, now=NOW)
        due = datetime.fromisoformat(after["due_at"])
        assert (due - NOW) <= timedelta(days=1)


# --- time boxing -----------------------------------------------------------

class TestBudget:
    def test_five_minutes_is_bounded_by_the_daily_target(self):
        # 5 min / 15s = 20 cards, but a target of 5 keeps the habit sustainable.
        assert rs.budget_to_item_count(5, daily_target=5) == 5

    def test_a_bigger_target_uses_more_of_the_time_box(self):
        assert rs.budget_to_item_count(5, daily_target=30) == 20

    def test_short_box_truncates_below_the_target(self):
        assert rs.budget_to_item_count(1, daily_target=30) == 4

    def test_no_minutes_falls_back_to_the_target(self):
        assert rs.budget_to_item_count(None, daily_target=7) == 7
        assert rs.budget_to_item_count("", daily_target=7) == 7

    def test_garbage_and_zero_never_yield_an_empty_session(self):
        assert rs.budget_to_item_count("abc", daily_target=5) == 5
        assert rs.budget_to_item_count(0, daily_target=5) == 5
        assert rs.budget_to_item_count(-3, daily_target=5) == 5
        assert rs.budget_to_item_count(0.01, daily_target=5) >= 1


# --- ordering --------------------------------------------------------------

class TestOrdering:
    def test_corrections_come_before_sentences_before_vocab(self):
        items = [
            {"item_key": "v", "item_type": "vocab", "due_at": None},
            {"item_key": "k", "item_type": "key_sentence", "due_at": None},
            {"item_key": "c", "item_type": "correction", "due_at": None},
        ]
        assert [i["item_key"] for i in rs.order_queue(items, now=NOW)] == ["c", "k", "v"]

    def test_due_items_outrank_new_which_outrank_not_yet_due(self):
        items = [
            {"item_key": "future", "item_type": "correction",
             "due_at": (NOW + timedelta(days=3)).isoformat()},
            {"item_key": "new", "item_type": "vocab", "due_at": None},
            {"item_key": "overdue", "item_type": "vocab",
             "due_at": (NOW - timedelta(days=2)).isoformat()},
        ]
        order = [i["item_key"] for i in rs.order_queue(items, now=NOW)]
        assert order == ["overdue", "new", "future"]

    def test_most_overdue_first_within_the_due_bucket(self):
        items = [
            {"item_key": "a", "item_type": "vocab",
             "due_at": (NOW - timedelta(days=1)).isoformat()},
            {"item_key": "b", "item_type": "vocab",
             "due_at": (NOW - timedelta(days=9)).isoformat()},
        ]
        assert [i["item_key"] for i in rs.order_queue(items, now=NOW)] == ["b", "a"]

    def test_a_backlog_does_not_starve_new_material(self):
        items = [{"item_key": f"old{i}", "item_type": "vocab",
                  "due_at": (NOW - timedelta(days=30)).isoformat()} for i in range(50)]
        items.append({"item_key": "fresh", "item_type": "vocab", "due_at": None})
        order = [i["item_key"] for i in rs.order_queue(items, now=NOW)]
        assert "fresh" in order[:51]

    def test_ordering_is_stable_and_total(self):
        items = [
            {"item_key": "b", "item_type": "vocab", "due_at": None},
            {"item_key": "a", "item_type": "vocab", "due_at": None},
        ]
        assert [i["item_key"] for i in rs.order_queue(items, now=NOW)] == ["a", "b"]

    def test_unparseable_due_dates_do_not_explode(self):
        items = [{"item_key": "junk", "item_type": "vocab", "due_at": "not-a-date"}]
        assert len(rs.order_queue(items, now=NOW)) == 1


class TestIsDue:
    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    def test_missing_or_broken_timestamps_surface_the_item(self, value):
        # Better to re-show an item than to bury it forever behind bad data.
        assert rs.is_due(value, now=NOW) is True

    def test_past_is_due_future_is_not(self):
        assert rs.is_due((NOW - timedelta(seconds=1)).isoformat(), now=NOW) is True
        assert rs.is_due((NOW + timedelta(days=1)).isoformat(), now=NOW) is False

    def test_naive_timestamps_are_treated_as_utc(self):
        assert rs.is_due("2026-08-11T09:00:00", now=NOW) is True


# --- the ramp --------------------------------------------------------------

class TestRamp:
    def test_target_holds_until_the_streak_earns_a_step(self):
        assert rs.next_daily_target(5, 1) == 5
        assert rs.next_daily_target(5, 2) == 5
        assert rs.next_daily_target(5, rs.RAMP_AFTER_DAYS) == 5 + rs.RAMP_STEP

    def test_target_is_capped(self):
        assert rs.next_daily_target(rs.MAX_DAILY_TARGET, rs.RAMP_AFTER_DAYS) == rs.MAX_DAILY_TARGET

    def test_consecutive_days_extend_the_streak(self):
        stats = {"streak": 3, "daily_target": 5, "last_completed_on": "2026-08-11"}
        out = rs.update_streak(stats, now=NOW)
        assert out["streak"] == 4
        assert out["last_completed_on"] == "2026-08-12"

    def test_a_gap_restarts_the_streak_at_one(self):
        stats = {"streak": 9, "daily_target": 11, "last_completed_on": "2026-08-01"}
        out = rs.update_streak(stats, now=NOW)
        assert out["streak"] == 1

    def test_completing_twice_in_a_day_is_a_no_op(self):
        stats = {"streak": 4, "daily_target": 7, "last_completed_on": "2026-08-12"}
        out = rs.update_streak(stats, now=NOW)
        assert out["streak"] == 4
        assert out["daily_target"] == 7

    def test_first_ever_review_starts_the_streak(self):
        out = rs.update_streak({}, now=NOW)
        assert out["streak"] == 1
        assert out["last_completed_on"] == "2026-08-12"

    def test_three_day_streak_raises_the_target(self):
        stats = {"streak": 2, "daily_target": 5, "last_completed_on": "2026-08-11"}
        out = rs.update_streak(stats, now=NOW)
        assert out["streak"] == 3
        assert out["daily_target"] == 7, "the ramp the user asked for"

    def test_a_full_ramp_walks_up_and_stops_at_the_cap(self):
        stats = {}
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for _ in range(120):
            stats = rs.update_streak(stats, now=day)
            day += timedelta(days=1)
        assert stats["daily_target"] == rs.MAX_DAILY_TARGET


class TestDecay:
    def test_a_single_missed_day_is_forgiven(self):
        stats = {"daily_target": 15, "last_completed_on": "2026-08-11"}
        assert rs.decayed_target(stats, now=NOW) == 15

    def test_two_missed_days_ease_the_target_down(self):
        stats = {"daily_target": 15, "last_completed_on": "2026-08-10"}
        assert rs.decayed_target(stats, now=NOW) == 15 - rs.RAMP_STEP

    def test_a_long_absence_never_falls_below_the_starting_target(self):
        stats = {"daily_target": 25, "last_completed_on": "2025-01-01"}
        assert rs.decayed_target(stats, now=NOW) == rs.DEFAULT_DAILY_TARGET

    def test_never_reviewed_keeps_the_default(self):
        assert rs.decayed_target({}, now=NOW) == rs.DEFAULT_DAILY_TARGET

    def test_decay_is_gentler_than_the_climb(self):
        """Coming back after a week shouldn't feel like starting over."""
        stats = {"daily_target": 21, "last_completed_on": "2026-08-05"}
        assert rs.decayed_target(stats, now=NOW) > rs.DEFAULT_DAILY_TARGET

"""review_scheduler.py — spacing, ordering, and the daily ramp for Daily Review.

Why this exists
---------------
The app already produces excellent study material, and it went unused. The
blocker was never content, it was *starting friction*: reviewing meant choosing
a session, then a mode, then facing a whole deck. Three decisions and an
unbounded pile, which is a non-starter with ten minutes before class.

So the queue is opinionated on the learner's behalf:

* **Time-boxed, not deck-boxed.** You pick five or ten minutes; the queue fills
  that budget and ending it is a success state, not an abandonment.
* **Ordered by value, not by lesson.** Your own corrections first — they are
  personally yours and the highest-yield thing to re-see — then whole sentences,
  then vocabulary.
* **Two grades.** ``again`` / ``good``. Four buttons is a decision, and
  decisions are what killed usage.
* **A target that earns its growth.** Volume ramps only while the habit holds,
  and decays gently rather than off a cliff after a busy week.

Pure functions over plain dicts, no Flask and no DB handle, so the rules are
testable without standing up the app.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

__all__ = [
    "GRADE_AGAIN",
    "GRADE_GOOD",
    "ITEM_TYPE_ORDER",
    "SECONDS_PER_ITEM",
    "DEFAULT_DAILY_TARGET",
    "MAX_DAILY_TARGET",
    "RAMP_AFTER_DAYS",
    "RAMP_STEP",
    "schedule_after_grade",
    "budget_to_item_count",
    "order_queue",
    "next_daily_target",
    "update_streak",
    "today_iso",
    "is_due",
    "decayed_target",
]

GRADE_AGAIN = "again"
GRADE_GOOD = "good"

# Corrections are the learner's own mistakes, so they earn the front of the
# queue: highest value per second of attention. Vocabulary is the long tail.
ITEM_TYPE_ORDER = {"correction": 0, "key_sentence": 1, "vocab": 2}

# Rough pace of a two-button review on a phone, used to turn "5 minutes" into a
# number of cards. Deliberately generous: overshooting the budget is worse than
# finishing early, because finishing is what builds the habit.
SECONDS_PER_ITEM = 15

DEFAULT_DAILY_TARGET = 5
MAX_DAILY_TARGET = 30
RAMP_AFTER_DAYS = 3
RAMP_STEP = 2
# One skipped day is a busy day, not a broken habit. Two in a row is a signal
# that the current volume isn't sustainable, so ease off instead of pretending.
DECAY_AFTER_MISSED_DAYS = 2

MIN_EASE = 1.3
MAX_EASE = 2.8
EASE_PENALTY = 0.2
EASE_BONUS = 0.05
FIRST_INTERVAL_DAYS = 1.0


def today_iso(now=None) -> str:
    """Today's date as YYYY-MM-DD, in UTC."""
    now = now or datetime.now(timezone.utc)
    return now.date().isoformat()


def schedule_after_grade(current, grade, now=None) -> dict:
    """Return the updated scheduling state for one item after a grade.

    ``current`` is the existing row (or ``{}`` for an item never seen before).
    Missing keys take new-item defaults, so callers don't have to pre-seed rows.
    """
    now = now or datetime.now(timezone.utc)
    interval = float(current.get("interval_days") or FIRST_INTERVAL_DAYS)
    ease = float(current.get("ease") or 2.5)
    streak = int(current.get("streak") or 0)
    lapses = int(current.get("lapses") or 0)

    if grade == GRADE_AGAIN:
        # Back to tomorrow, and make future intervals grow more slowly. The item
        # is not buried: it returns while the confusion is still fresh.
        interval = FIRST_INTERVAL_DAYS
        ease = max(MIN_EASE, ease - EASE_PENALTY)
        streak = 0
        lapses += 1
    else:
        # First correct answer is worth a day; after that, grow by ease.
        interval = FIRST_INTERVAL_DAYS if streak == 0 else interval * ease
        ease = min(MAX_EASE, ease + EASE_BONUS)
        streak += 1

    return {
        "interval_days": round(interval, 4),
        "ease": round(ease, 4),
        "streak": streak,
        "lapses": lapses,
        "due_at": (now + timedelta(days=interval)).isoformat(),
        "last_reviewed_at": now.isoformat(),
    }


def budget_to_item_count(minutes, daily_target, seconds_per_item=SECONDS_PER_ITEM) -> int:
    """How many items fit in a time box.

    Bounded by the daily target so a generous time box doesn't undo the ramp —
    the point is a sustainable habit, not a marathon that burns you out on day
    one. Always at least 1, so a review is never an empty screen.
    """
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        minutes = 0.0
    if minutes <= 0:
        return max(1, int(daily_target))
    fits = int((minutes * 60) // max(1, seconds_per_item))
    return max(1, min(fits, int(daily_target)))


def is_due(due_at, now=None) -> bool:
    """Is this item ready to be seen?

    A never-reviewed item (``due_at`` empty) counts as due — new material is
    what you owe yourself today just as much as a lapsed card. An unparseable
    value also counts as due, so a bad timestamp surfaces the item instead of
    silently burying it forever.
    """
    now = now or datetime.now(timezone.utc)
    if not due_at:
        return True
    parsed = _parse_dt(due_at)
    if parsed is None:
        return True
    return parsed <= now


def order_queue(items, now=None) -> list:
    """Order candidate items: most overdue first, then by type value.

    ``items`` are dicts with ``item_type`` and optionally ``due_at`` (absent or
    None means never reviewed). New items sort after genuinely due ones but
    ahead of items that aren't due yet, so a backlog never starves new material.
    """
    now = now or datetime.now(timezone.utc)

    def sort_key(item):
        due_raw = item.get("due_at")
        due = _parse_dt(due_raw)
        if due is None:
            # Never seen. Rank between "due" and "not yet due".
            bucket, overdue = 1, 0.0
        else:
            overdue = (now - due).total_seconds()
            bucket = 0 if overdue >= 0 else 2
        type_rank = ITEM_TYPE_ORDER.get(item.get("item_type"), 99)
        # Negative overdue → most overdue first within the due bucket.
        return (bucket, type_rank, -overdue, str(item.get("item_key", "")))

    return sorted(items, key=sort_key)


def next_daily_target(current_target, completed_streak) -> int:
    """Grow the target only once the habit has actually held."""
    target = int(current_target or DEFAULT_DAILY_TARGET)
    if completed_streak and completed_streak % RAMP_AFTER_DAYS == 0:
        target += RAMP_STEP
    return max(1, min(target, MAX_DAILY_TARGET))


def update_streak(stats, now=None) -> dict:
    """Record a completed review for today and return the new stats.

    Completing twice in one day is a no-op for the streak — the ramp measures
    days of habit, not sessions.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    last_raw = stats.get("last_completed_on")
    last = _parse_date(last_raw)
    streak = int(stats.get("streak") or 0)
    target = int(stats.get("daily_target") or DEFAULT_DAILY_TARGET)

    if last == today:
        return {"streak": streak, "daily_target": target, "last_completed_on": today.isoformat()}

    if last is not None and (today - last).days == 1:
        streak += 1
    else:
        streak = 1

    return {
        "streak": streak,
        "daily_target": next_daily_target(target, streak),
        "last_completed_on": today.isoformat(),
    }


def decayed_target(stats, now=None) -> int:
    """The target to use today, eased down after a run of missed days."""
    now = now or datetime.now(timezone.utc)
    target = int(stats.get("daily_target") or DEFAULT_DAILY_TARGET)
    last = _parse_date(stats.get("last_completed_on"))
    if last is None:
        return target
    missed = (now.date() - last).days
    if missed >= DECAY_AFTER_MISSED_DAYS:
        # One step back per full "decay window" missed, never below the default.
        steps = missed // DECAY_AFTER_MISSED_DAYS
        target -= RAMP_STEP * steps
    return max(DEFAULT_DAILY_TARGET, min(target, MAX_DAILY_TARGET))


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = _parse_dt(value)
    return parsed.date() if parsed else None

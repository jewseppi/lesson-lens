"""Tests for automatic pre-sync restore points.

The safety net exists so a bug introduced in a sync/import path cannot cost data:
a snapshot is taken before the mutation, kept for a retention window, and can be
rolled back from the UI. These cover the retention/pruning/lookup logic against a
real SQLite database, with no Flask needed.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_DIR = os.path.join(ROOT, "api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

import restore_points as rp  # noqa: E402

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT)")
    c.execute("INSERT INTO users (email) VALUES ('me@example.com')")
    rp.ensure_table(c)
    return c


def _make(conn, directory, *, user_id=1, reason=rp.REASON_SYNC, now=NOW, days=None, data=b"PK-zip"):
    return rp.create_restore_point(
        conn, user_id, reason, data, str(directory),
        manifest={"session_count": 3, "summary_count": 2, "attachment_count": 5},
        now=now, days=days,
    )


# --- creation -------------------------------------------------------------

def test_create_writes_file_and_row(conn, tmp_path):
    created = _make(conn, tmp_path)
    conn.commit()

    assert (tmp_path / created["filename"]).read_bytes() == b"PK-zip"
    assert created["size_bytes"] == len(b"PK-zip")
    assert created["session_count"] == 3
    assert created["attachment_count"] == 5

    rows = rp.list_restore_points(conn, 1, now=NOW)
    assert len(rows) == 1
    assert rows[0]["reason"] == rp.REASON_SYNC


def test_default_retention_is_seven_days(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("LESSONLENS_RESTORE_RETENTION_DAYS", raising=False)
    created = _make(conn, tmp_path)

    expires = datetime.fromisoformat(created["expires_at"])
    assert (expires - NOW).days == 7
    assert rp.retention_days() == 7


def test_retention_is_configurable(monkeypatch):
    monkeypatch.setenv("LESSONLENS_RESTORE_RETENTION_DAYS", "14")
    assert rp.retention_days() == 14
    # Garbage and non-positive values fall back to the safe default.
    monkeypatch.setenv("LESSONLENS_RESTORE_RETENTION_DAYS", "nonsense")
    assert rp.retention_days() == rp.DEFAULT_RETENTION_DAYS
    monkeypatch.setenv("LESSONLENS_RESTORE_RETENTION_DAYS", "0")
    assert rp.retention_days() == rp.DEFAULT_RETENTION_DAYS


def test_concurrent_captures_do_not_clobber(conn, tmp_path):
    a = _make(conn, tmp_path, data=b"first")
    b = _make(conn, tmp_path, data=b"second")
    conn.commit()

    assert a["filename"] != b["filename"], "same-second captures must not overwrite"
    assert (tmp_path / a["filename"]).read_bytes() == b"first"
    assert (tmp_path / b["filename"]).read_bytes() == b"second"


# --- expiry / pruning -----------------------------------------------------

def test_expired_points_are_pruned(conn, tmp_path):
    old = _make(conn, tmp_path, now=NOW - timedelta(days=10))
    fresh = _make(conn, tmp_path, now=NOW - timedelta(days=1))
    conn.commit()

    removed = rp.prune_restore_points(conn, str(tmp_path), now=NOW)
    conn.commit()

    assert removed == 1
    assert not (tmp_path / old["filename"]).exists(), "expired snapshot file must be deleted"
    assert (tmp_path / fresh["filename"]).exists()

    remaining = rp.list_restore_points(conn, 1, now=NOW)
    assert [r["id"] for r in remaining] == [fresh["id"]]


def test_point_expiring_exactly_now_is_pruned(conn, tmp_path):
    _make(conn, tmp_path, now=NOW - timedelta(days=7))
    conn.commit()
    assert rp.prune_restore_points(conn, str(tmp_path), now=NOW) == 1


def test_prune_survives_missing_file(conn, tmp_path):
    created = _make(conn, tmp_path, now=NOW - timedelta(days=10))
    conn.commit()
    os.remove(tmp_path / created["filename"])  # file vanished out from under us

    assert rp.prune_restore_points(conn, str(tmp_path), now=NOW) == 1
    assert rp.list_restore_points(conn, 1, now=NOW) == []


def test_reported_expiry_countdown(conn, tmp_path):
    _make(conn, tmp_path, now=NOW - timedelta(days=2))
    conn.commit()

    row = rp.list_restore_points(conn, 1, now=NOW)[0]
    assert row["expires_in_days"] == 5  # created 2 days ago, 7-day window
    assert row["expired"] is False


# --- lookup / isolation / deletion ----------------------------------------

def test_points_are_scoped_per_user(conn, tmp_path):
    conn.execute("INSERT INTO users (email) VALUES ('other@example.com')")
    _make(conn, tmp_path, user_id=1)
    _make(conn, tmp_path, user_id=2)
    conn.commit()

    assert len(rp.list_restore_points(conn, 1, now=NOW)) == 1
    assert len(rp.list_restore_points(conn, 2, now=NOW)) == 1
    # One user cannot fetch another's snapshot.
    other = rp.list_restore_points(conn, 2, now=NOW)[0]
    assert rp.get_restore_point(conn, 1, other["id"]) is None


def test_delete_removes_row_and_file(conn, tmp_path):
    created = _make(conn, tmp_path)
    conn.commit()

    assert rp.delete_restore_point(conn, 1, created["id"], str(tmp_path)) is True
    conn.commit()
    assert not (tmp_path / created["filename"]).exists()
    assert rp.list_restore_points(conn, 1, now=NOW) == []
    # Deleting again (or someone else's) is a clean False, not an exception.
    assert rp.delete_restore_point(conn, 1, created["id"], str(tmp_path)) is False


def test_read_bytes_round_trip(conn, tmp_path):
    created = _make(conn, tmp_path, data=b"ARCHIVE-BYTES")
    conn.commit()
    row = rp.get_restore_point(conn, 1, created["id"])
    assert rp.read_restore_point_bytes(row, str(tmp_path)) == b"ARCHIVE-BYTES"


def test_newest_first_ordering(conn, tmp_path):
    older = _make(conn, tmp_path, now=NOW - timedelta(days=3))
    newer = _make(conn, tmp_path, now=NOW - timedelta(days=1))
    conn.commit()
    ids = [r["id"] for r in rp.list_restore_points(conn, 1, now=NOW)]
    assert ids == [newer["id"], older["id"]]


# --- path safety ----------------------------------------------------------

def test_safe_filename_strips_traversal():
    assert rp.safe_filename("../../etc/passwd") == "passwd"
    assert rp.safe_filename("a/b/c.zip") == "c.zip"
    assert rp.safe_filename("") == "restore-point.zip"
    assert rp.safe_filename("..") == "restore-point.zip"
    assert "/" not in rp.safe_filename("evil/../../x.zip")


def test_read_bytes_cannot_escape_directory(conn, tmp_path):
    """A tampered filename column must not read outside the snapshot folder."""
    secret = tmp_path.parent / "secret.txt"
    secret.write_bytes(b"top secret")
    created = _make(conn, tmp_path)
    conn.execute(
        "UPDATE restore_points SET filename = ? WHERE id = ?",
        ("../secret.txt", created["id"]),
    )
    conn.commit()

    row = rp.get_restore_point(conn, 1, created["id"])
    with pytest.raises(OSError):
        rp.read_restore_point_bytes(row, str(tmp_path))


def test_reason_constants_are_distinct():
    reasons = {rp.REASON_SYNC, rp.REASON_IMPORT, rp.REASON_REPARSE, rp.REASON_ROLLBACK}
    assert len(reasons) == 4


# --- count cap ------------------------------------------------------------
# Age alone does not bound disk use: snapshots now contain images, and the
# scheduled updater can produce one per run.

def test_max_points_cap_drops_oldest(conn, tmp_path):
    created = [
        _make(conn, tmp_path, now=NOW - timedelta(hours=n), data=f"snap{n}".encode())
        for n in range(6)
    ]
    conn.commit()

    removed = rp.enforce_max_points(conn, 1, str(tmp_path), limit=3)
    conn.commit()

    assert removed == 3
    remaining = rp.list_restore_points(conn, 1, now=NOW)
    assert len(remaining) == 3
    # created[0] is newest (NOW - 0h); the three oldest must be gone.
    assert {r["id"] for r in remaining} == {c["id"] for c in created[:3]}
    for gone in created[3:]:
        assert not (tmp_path / gone["filename"]).exists()


def test_max_points_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("LESSONLENS_RESTORE_MAX_POINTS", "5")
    assert rp.max_points() == 5
    monkeypatch.setenv("LESSONLENS_RESTORE_MAX_POINTS", "junk")
    assert rp.max_points() == rp.DEFAULT_MAX_POINTS
    monkeypatch.setenv("LESSONLENS_RESTORE_MAX_POINTS", "-1")
    assert rp.max_points() == rp.DEFAULT_MAX_POINTS


def test_max_points_cap_is_per_user(conn, tmp_path):
    conn.execute("INSERT INTO users (email) VALUES ('other@example.com')")
    for n in range(4):
        _make(conn, tmp_path, user_id=1, now=NOW - timedelta(hours=n))
    for n in range(4):
        _make(conn, tmp_path, user_id=2, now=NOW - timedelta(hours=n))
    conn.commit()

    rp.enforce_max_points(conn, 1, str(tmp_path), limit=2)
    conn.commit()

    assert len(rp.list_restore_points(conn, 1, now=NOW)) == 2
    assert len(rp.list_restore_points(conn, 2, now=NOW)) == 4, "other users must be untouched"


def test_cap_is_a_noop_under_the_limit(conn, tmp_path):
    _make(conn, tmp_path)
    conn.commit()
    assert rp.enforce_max_points(conn, 1, str(tmp_path), limit=10) == 0
    assert len(rp.list_restore_points(conn, 1, now=NOW)) == 1

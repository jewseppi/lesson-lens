"""Tests for the one-command updater (update-now.sh, scripts/count_sessions.py).

These are the two pieces a user runs when everything else has already gone
wrong, so they are tested for *graceful degradation* as much as for the happy
path: a missing database, a damaged schema, or an unknown flag must produce a
readable line and a sane exit code, never a traceback or — worst of all — a
silent fall-through into syncing data the user only asked to inspect.

update-now.sh is exercised with a stub start-local.sh, so nothing here starts a
server, touches a real database, or reaches the network.
"""
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPDATE_NOW = os.path.join(ROOT, "update-now.sh")
COUNT_SESSIONS = os.path.join(ROOT, "scripts", "count_sessions.py")


# --- scripts/count_sessions.py --------------------------------------------

def _run_count(cwd, email="a@b.com"):
    env = dict(os.environ, LESSONLENS_EMAIL=email)
    proc = subprocess.run(
        [sys.executable, os.path.join(cwd, "scripts", "count_sessions.py")],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout.strip()


@pytest.fixture
def fake_repo(tmp_path):
    """A directory shaped like the repo, with count_sessions.py copied in.

    The script locates the database relative to its own path, so it has to be
    tested from a real copy rather than by importing it.
    """
    (tmp_path / "api").mkdir()
    (tmp_path / "scripts").mkdir()
    shutil.copy(COUNT_SESSIONS, tmp_path / "scripts" / "count_sessions.py")
    return tmp_path


def _make_db(repo, users=(), sessions=()):
    conn = sqlite3.connect(repo / "api" / "lessonlens.db")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)", users)
    conn.executemany("INSERT INTO sessions (user_id) VALUES (?)", [(u,) for u in sessions])
    conn.commit()
    conn.close()


def test_count_is_zero_when_no_database_exists(fake_repo):
    """Before the first run there is no database — that is not an error."""
    assert _run_count(fake_repo) == (0, "0")


def test_count_is_zero_for_an_unknown_account(fake_repo):
    _make_db(fake_repo, users=[(1, "someone@else.com")], sessions=[1, 1])
    assert _run_count(fake_repo, email="a@b.com") == (0, "0")


def test_count_reports_only_the_configured_account(fake_repo):
    """Data is scoped per user, so the count must be too."""
    _make_db(fake_repo, users=[(1, "a@b.com"), (2, "other@b.com")],
             sessions=[1, 1, 1, 2, 2])
    assert _run_count(fake_repo, email="a@b.com") == (0, "3")


def test_count_degrades_to_a_question_mark_on_a_damaged_schema(fake_repo):
    """A '?' in the before/after line beats a traceback over a working sync."""
    conn = sqlite3.connect(fake_repo / "api" / "lessonlens.db")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'a@b.com')")
    conn.commit()
    conn.close()
    code, out = _run_count(fake_repo)
    assert (code, out) == (0, "?")


def test_count_reads_email_from_dotenv_when_unset(fake_repo):
    _make_db(fake_repo, users=[(1, "from-env-file@b.com")], sessions=[1, 1])
    (fake_repo / ".env").write_text(
        "LESSONLENS_TARGET=local\nLESSONLENS_EMAIL=from-env-file@b.com\n", encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k != "LESSONLENS_EMAIL"}
    proc = subprocess.run(
        [sys.executable, str(fake_repo / "scripts" / "count_sessions.py")],
        cwd=fake_repo, env=env, capture_output=True, text=True,
    )
    assert (proc.returncode, proc.stdout.strip()) == (0, "2")


# --- update-now.sh flag handling ------------------------------------------

@pytest.fixture
def stub_repo(tmp_path):
    """update-now.sh with a start-local.sh that only records how it was called.

    Everything after phase 1 needs a .env, a virtualenv and a server, so these
    tests stop at the hand-off: what matters is *whether* phase 2 and 3 are
    reached at all, and with which arguments.
    """
    shutil.copy(UPDATE_NOW, tmp_path / "update-now.sh")
    os.chmod(tmp_path / "update-now.sh", 0o755)
    stub = tmp_path / "start-local.sh"
    stub.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$@" > "$(dirname "$0")/called-with.txt"\n'
        'echo "stub start-local ran"\n'
        # No .env is written, so the real script's `. ./.env` fails right after
        # phase 1 — which is exactly the boundary these tests care about.
        'exit 0\n',
        encoding="utf-8",
    )
    os.chmod(stub, 0o755)
    return tmp_path


def _run_update(repo, *args):
    return subprocess.run(
        ["bash", str(repo / "update-now.sh"), *args],
        cwd=repo, capture_output=True, text=True,
    )


@pytest.mark.parametrize("flag", ["--status", "--stop", "--logs"])
def test_control_flags_hand_over_and_never_sync(stub_repo, flag):
    """The bug this guards: these flags exit 0 inside start-local.sh, and an
    earlier version of the wrapper carried straight on into syncing data the
    user had only asked to look at."""
    proc = _run_update(stub_repo, flag)
    assert proc.returncode == 0
    assert (stub_repo / "called-with.txt").read_text().strip() == flag
    combined = proc.stdout + proc.stderr
    assert "2/3" not in combined
    assert "3/3" not in combined


def test_help_exits_without_running_anything(stub_repo):
    proc = _run_update(stub_repo, "--help")
    assert proc.returncode == 0
    assert not (stub_repo / "called-with.txt").exists()
    assert "./update-now.sh" in proc.stdout


def test_unknown_flag_is_rejected_before_any_work(stub_repo):
    proc = _run_update(stub_repo, "--nonsense")
    assert proc.returncode == 1
    assert "unknown option" in proc.stdout + proc.stderr
    assert not (stub_repo / "called-with.txt").exists()


def test_import_without_a_path_is_rejected(stub_repo):
    proc = _run_update(stub_repo, "--import")
    assert proc.returncode == 1
    assert "needs a path" in proc.stdout + proc.stderr


def test_no_git_and_import_are_forwarded_to_start_local(stub_repo):
    proc = _run_update(stub_repo, "--no-git", "--import", "/tmp/backup.zip")
    forwarded = (stub_repo / "called-with.txt").read_text().split()
    assert forwarded == ["--no-git", "--import", "/tmp/backup.zip"]
    # Phase 1 was reached; it then fails on the missing .env the stub never wrote.
    assert "1/3" in proc.stdout


def test_a_failing_start_local_stops_the_run(stub_repo):
    """If the app did not start there is nothing to sync into, and continuing
    would produce a confusing connection error three phases later."""
    (stub_repo / "start-local.sh").write_text(
        '#!/usr/bin/env bash\necho "boom" >&2\nexit 1\n', encoding="utf-8"
    )
    os.chmod(stub_repo / "start-local.sh", 0o755)
    proc = _run_update(stub_repo)
    assert proc.returncode != 0
    assert "3/3" not in proc.stdout


# --- the shipped script itself --------------------------------------------

def test_update_now_is_executable_and_parses():
    assert os.access(UPDATE_NOW, os.X_OK), "update-now.sh must be executable"
    proc = subprocess.run(["bash", "-n", UPDATE_NOW], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_update_now_never_passes_unvetted_arguments_through():
    """Guards the regression directly: "$@" must not reach start-local.sh."""
    body = open(UPDATE_NOW, encoding="utf-8").read()
    assert './start-local.sh "$@"' not in body

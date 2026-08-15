"""Tests for scripts/import_backup.py — the auto-import step in update-now.sh.

This runs unattended as part of one command, so the things that matter are that
it picks the right file, refuses to touch files that aren't ours, and treats
"nothing to import" as success rather than an error that would abort the run.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import import_backup  # noqa: E402


@pytest.fixture
def search_dirs(tmp_path, monkeypatch):
    """Point the archive search at a temp directory instead of the real ~/Downloads."""
    d = tmp_path / "Downloads"
    d.mkdir()
    monkeypatch.setattr(import_backup, "SEARCH_DIRS", [str(d)])
    return d


def _zip(path, mtime=None):
    path.write_bytes(b"PK\x03\x04fake")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


# --- picking an archive ---------------------------------------------------

def test_finds_nothing_in_an_empty_directory(search_dirs):
    assert import_backup.find_archive() is None


def test_finds_a_lessonlens_archive(search_dirs):
    z = _zip(search_dirs / "lessonlens-summaries-2026-08-15.zip")
    assert import_backup.find_archive() == z


def test_accepts_both_naming_styles(search_dirs):
    _zip(search_dirs / "lesson-lens-backup.zip")
    assert import_backup.find_archive() is not None


def test_ignores_zips_that_are_not_ours(search_dirs):
    """Downloads is full of other people's files; opening them is not our business."""
    _zip(search_dirs / "tax-return-2025.zip")
    _zip(search_dirs / "photos.zip")
    assert import_backup.find_archive() is None


def test_picks_the_newest_when_several_exist(search_dirs):
    _zip(search_dirs / "lessonlens-old.zip", mtime=1_000_000)
    new = _zip(search_dirs / "lessonlens-new.zip", mtime=2_000_000)
    assert import_backup.find_archive() == new


def test_missing_search_directories_are_skipped(tmp_path, monkeypatch):
    """A Mac without ~/Documents must not crash the run."""
    monkeypatch.setattr(import_backup, "SEARCH_DIRS",
                        [str(tmp_path / "nope"), str(tmp_path / "also-nope")])
    assert import_backup.find_archive() is None


# --- exit codes -----------------------------------------------------------

def test_no_archive_is_success_not_failure(search_dirs, monkeypatch, capsys):
    """update-now.sh runs this every time. Having no archive is the normal case
    and must not abort the rest of the update."""
    monkeypatch.setattr(sys, "argv", ["import_backup.py"])
    assert import_backup.main() == 0
    assert "no LessonLens archive found" in capsys.readouterr().out


def test_an_explicit_missing_path_is_an_error(tmp_path, monkeypatch, capsys):
    """Naming a file that isn't there is a mistake worth reporting."""
    monkeypatch.setattr(sys, "argv", ["import_backup.py", str(tmp_path / "gone.zip")])
    assert import_backup.main() == 1
    assert "no such file" in capsys.readouterr().out


def test_check_mode_reports_without_importing(search_dirs, monkeypatch, capsys):
    z = _zip(search_dirs / "lessonlens-summaries.zip")

    def explode(*a, **k):                       # pragma: no cover - must not run
        raise AssertionError("--check must not contact the server")
    monkeypatch.setattr(import_backup, "login", explode)
    monkeypatch.setattr(import_backup, "post_archive", explode)
    monkeypatch.setattr(sys, "argv", ["import_backup.py", "--check"])

    assert import_backup.main() == 0
    assert z.name in capsys.readouterr().out


def test_missing_credentials_are_reported_not_raised(search_dirs, monkeypatch, capsys):
    _zip(search_dirs / "lessonlens-summaries.zip")
    monkeypatch.setattr(import_backup, "env", lambda key, default="": "")
    monkeypatch.setattr(sys, "argv", ["import_backup.py"])

    assert import_backup.main() == 1
    assert "start-local.sh" in capsys.readouterr().out


def test_a_server_that_is_not_running_gives_a_readable_message(search_dirs, monkeypatch, capsys):
    _zip(search_dirs / "lessonlens-summaries.zip")
    monkeypatch.setattr(import_backup, "env",
                        lambda key, default="": {"LESSONLENS_EMAIL": "a@b.com",
                                                 "LESSONLENS_PASSWORD": "x" * 16}.get(key, default))

    def refused(*a, **k):
        raise OSError("Connection refused")
    monkeypatch.setattr(import_backup, "login", refused)
    monkeypatch.setattr(sys, "argv", ["import_backup.py"])

    assert import_backup.main() == 1
    out = capsys.readouterr().out
    assert "not answering" in out and "start-local.sh" in out


# --- reporting ------------------------------------------------------------

def test_reports_counts_from_the_server_response(search_dirs, monkeypatch, capsys):
    _zip(search_dirs / "lessonlens-summaries.zip")
    monkeypatch.setattr(import_backup, "env",
                        lambda key, default="": {"LESSONLENS_EMAIL": "a@b.com",
                                                 "LESSONLENS_PASSWORD": "x" * 16}.get(key, default))
    monkeypatch.setattr(import_backup, "login", lambda *a, **k: "tok")
    monkeypatch.setattr(import_backup, "post_archive",
                        lambda *a, **k: {"session_count": 83, "summary_count": 31,
                                         "skipped_summary_count": 0})
    monkeypatch.setattr(sys, "argv", ["import_backup.py"])

    assert import_backup.main() == 0
    out = capsys.readouterr().out
    assert "31 summary(ies)" in out and "83 session(s)" in out


def test_a_second_run_says_nothing_was_new(search_dirs, monkeypatch, capsys):
    """Merge semantics make re-running harmless; the output should say so plainly
    rather than looking like a silent no-op."""
    _zip(search_dirs / "lessonlens-summaries.zip")
    monkeypatch.setattr(import_backup, "env",
                        lambda key, default="": {"LESSONLENS_EMAIL": "a@b.com",
                                                 "LESSONLENS_PASSWORD": "x" * 16}.get(key, default))
    monkeypatch.setattr(import_backup, "login", lambda *a, **k: "tok")
    monkeypatch.setattr(import_backup, "post_archive",
                        lambda *a, **k: {"session_count": 0, "summary_count": 0,
                                         "skipped_summary_count": 31})
    monkeypatch.setattr(sys, "argv", ["import_backup.py"])

    assert import_backup.main() == 0
    assert "already in your app" in capsys.readouterr().out


# --- the wiring in update-now.sh -----------------------------------------

def test_update_now_runs_the_import_before_the_chat_sync():
    """Order matters: a chat sync that adds sessions should see the summaries
    that came from the archive, and the before/after count must span both."""
    body = (ROOT / "update-now.sh").read_text(encoding="utf-8")
    assert "scripts/import_backup.py" in body
    assert body.index("scripts/import_backup.py") < body.index("scripts/line_mac_sync.py")
    # before= is captured ahead of the import, or the count under-reports.
    assert body.index('before="$(count_sessions)"') < body.index("scripts/import_backup.py")


def test_a_failed_import_does_not_abort_the_update():
    """The archive is optional. A bad one must not cost you the chat sync."""
    body = (ROOT / "update-now.sh").read_text(encoding="utf-8")
    line = next(l for l in body.splitlines() if "scripts/import_backup.py" in l and "$PY" in l)
    assert "||" in line, "import_backup.py must not run under bare set -e"

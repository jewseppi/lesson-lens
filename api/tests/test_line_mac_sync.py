"""Tests for the macOS one-touch updater (scripts/line_mac_sync.py).

These exercise the pure, network-free helpers (image sniffing, export
discovery, incremental state, multipart encoding, dedupe) plus the client with
urlopen mocked out.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import line_mac_sync as m  # noqa: E402


# --- magic-byte image detection -------------------------------------------

@pytest.mark.parametrize(
    "header,expected",
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"GIF87a" + b"\x00" * 8, "gif"),
        (b"GIF89a" + b"\x00" * 8, "gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "webp"),
        (b"BM" + b"\x00" * 12, "bmp"),
        (b"\x00\x00\x00\x18ftypheic", "heic"),
        (b"\x00\x00\x00\x18ftypmif1", "heic"),
        (b"this is plain text!!", None),
        (b"short", None),
    ],
)
def test_sniff_image_type(header, expected):
    assert m.sniff_image_type(header) == expected


def test_is_image_file(tmp_path):
    img = tmp_path / "hashed_no_extension"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"junk" * 10)
    txt = tmp_path / "note.txt"
    txt.write_bytes(b"hello world")
    assert m.is_image_file(img) is True
    assert m.is_image_file(txt) is False
    assert m.is_image_file(tmp_path / "missing") is False


# --- export discovery ------------------------------------------------------

def test_find_latest_export_prefers_hinted(tmp_path):
    old = tmp_path / "random.txt"
    old.write_text("x")
    os.utime(old, (1000, 1000))
    hinted = tmp_path / "[LINE] Chat with Jessie.txt"
    hinted.write_text("y")
    os.utime(hinted, (900, 900))  # older, but name-hinted wins
    assert m.find_latest_export([tmp_path]) == hinted


def test_find_latest_export_falls_back_to_newest_txt(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("a")
    os.utime(a, (1000, 1000))
    b = tmp_path / "b.txt"
    b.write_text("b")
    os.utime(b, (2000, 2000))
    assert m.find_latest_export([tmp_path]) == b


def test_find_latest_export_explicit(tmp_path):
    f = tmp_path / "explicit.txt"
    f.write_text("x")
    assert m.find_latest_export([tmp_path], explicit=f) == f
    assert m.find_latest_export([tmp_path], explicit=tmp_path / "nope.txt") is None


def test_find_latest_export_none(tmp_path):
    assert m.find_latest_export([tmp_path]) is None
    assert m.find_latest_export([tmp_path / "does-not-exist"]) is None


# --- image scanning + dedupe ----------------------------------------------

def test_iter_candidate_images_respects_mtime(tmp_path):
    old = tmp_path / "old_img"
    old.write_bytes(b"\xff\xd8\xffOLD")
    os.utime(old, (1000, 1000))
    new = tmp_path / "new_img"
    new.write_bytes(b"\x89PNG\r\n\x1a\nNEW")
    os.utime(new, (5000, 5000))
    not_img = tmp_path / "note"
    not_img.write_bytes(b"just text here")
    os.utime(not_img, (5000, 5000))

    all_found = set(m.iter_candidate_images([tmp_path], min_mtime=0))
    assert old in all_found and new in all_found and not_img not in all_found

    recent = set(m.iter_candidate_images([tmp_path], min_mtime=2000))
    assert new in recent and old not in recent


def test_select_new_images_dedupes(tmp_path):
    p1 = tmp_path / "a"
    p1.write_bytes(b"\xff\xd8\xffSAME")
    p2 = tmp_path / "b"
    p2.write_bytes(b"\xff\xd8\xffSAME")  # identical content
    p3 = tmp_path / "c"
    p3.write_bytes(b"\x89PNG\r\n\x1a\nOTHER")

    sel = m.select_new_images([p1, p2, p3], set())
    assert len(sel) == 2  # p1/p2 collapse to one
    shas = {s for _, s in sel}
    # already-synced shas are skipped
    assert m.select_new_images([p1, p2, p3], shas) == []


# --- incremental state -----------------------------------------------------

def test_state_roundtrip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = m.load_state(path)  # missing file → defaults
    assert state["synced_image_sha256"] == []
    assert state["image_mtime_watermark"] == 0.0
    state["synced_image_sha256"].append("abc")
    state["image_mtime_watermark"] = 1234.5
    m.save_state(path, state)
    reloaded = m.load_state(path)
    assert reloaded["synced_image_sha256"] == ["abc"]
    assert reloaded["image_mtime_watermark"] == 1234.5


def test_load_state_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json")
    state = m.load_state(path)  # should not raise
    assert state["synced_image_sha256"] == []


# --- multipart encoder -----------------------------------------------------

def test_encode_multipart_shape():
    ct, body = m.encode_multipart(
        {"provider": "anthropic"},
        [("images", "a.jpg", b"\xff\xd8\xffDATA", "image/jpeg")],
    )
    assert ct.startswith("multipart/form-data; boundary=")
    boundary = ct.split("boundary=")[1]
    assert boundary.encode() in body
    assert b'name="provider"' in body
    assert b"anthropic" in body
    assert b'name="images"; filename="a.jpg"' in body
    assert b"Content-Type: image/jpeg" in body
    assert b"\xff\xd8\xffDATA" in body


# --- .env loader -----------------------------------------------------------

def test_load_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        'LESSONLENS_API_URL="https://example.com"\n'
        "LESSONLENS_EMAIL=me@example.com\n"
        "\n"
        "IGNORED_NO_EQUALS\n"
    )
    monkeypatch.delenv("LESSONLENS_API_URL", raising=False)
    monkeypatch.delenv("LESSONLENS_EMAIL", raising=False)
    m.load_env_file(env)
    assert os.environ["LESSONLENS_API_URL"] == "https://example.com"
    assert os.environ["LESSONLENS_EMAIL"] == "me@example.com"


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LESSONLENS_EMAIL=fromfile@example.com\n")
    monkeypatch.setenv("LESSONLENS_EMAIL", "fromshell@example.com")
    m.load_env_file(env)
    assert os.environ["LESSONLENS_EMAIL"] == "fromshell@example.com"


# --- client with urlopen mocked -------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_login_and_generate(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        if req.full_url.endswith("/api/login"):
            return _FakeResp({"access_token": "tok123"})
        if req.full_url.endswith("/generate"):
            return _FakeResp({"title": "Lesson", "vocabulary": [1, 2, 3]})
        return _FakeResp({})

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)

    client = m.LessonLensClient("https://example.com/")
    client.login("me@example.com", "pw")
    assert client.token == "tok123"

    result = client.generate("2026-03-05", "anthropic", "claude-opus-5")
    assert result["title"] == "Lesson"
    # auth header carried on the authenticated call
    gen_req = calls[-1]
    assert gen_req.get_header("Authorization") == "Bearer tok123"


def test_client_login_missing_token(monkeypatch):
    monkeypatch.setattr(
        m.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp({"error": "bad"})
    )
    client = m.LessonLensClient("https://example.com")
    with pytest.raises(m.ApiError):
        client.login("me@example.com", "pw")


def test_push_uses_remote_credentials_not_local(tmp_path, monkeypatch, capsys):
    """--push must authenticate to the HOSTED instance with its own credentials.

    The local instance and the hosted one can have different logins; reusing the
    local ones silently pushed to the wrong account (or failed auth).
    """
    sent = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def login(self, email, password):
            sent["local_login"] = (email, password)

        def list_sessions(self):
            return []

        def sync_remote(self, url, email, password, replace_existing=False):
            sent["remote"] = (url, email, password)
            return {"session_count": 1}

    monkeypatch.setattr(m, "LessonLensClient", FakeClient)
    monkeypatch.setenv("LESSONLENS_TARGET", "local")
    monkeypatch.setenv("LESSONLENS_EMAIL", "local@example.com")
    monkeypatch.setenv("LESSONLENS_PASSWORD", "local-pw")
    monkeypatch.setenv("LESSONLENS_REMOTE_EMAIL", "hosted@example.com")
    monkeypatch.setenv("LESSONLENS_REMOTE_PASSWORD", "hosted-pw")
    monkeypatch.setenv("LESSONLENS_REMOTE_URL", "https://hosted.example.com")

    args = m.build_arg_parser().parse_args([
        "--target", "local", "--push", "--sync-only",
        "--skip-export", "--skip-images",
        "--state-file", str(tmp_path / "state.json"),
    ])
    assert m.run(args) == 0

    assert sent["local_login"] == ("local@example.com", "local-pw")
    assert sent["remote"] == ("https://hosted.example.com", "hosted@example.com", "hosted-pw")


def test_push_falls_back_to_local_credentials(tmp_path, monkeypatch):
    """When no remote-specific credentials are set, reuse the local ones."""
    sent = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def login(self, email, password):
            pass

        def list_sessions(self):
            return []

        def sync_remote(self, url, email, password, replace_existing=False):
            sent["remote"] = (url, email, password)
            return {}

    monkeypatch.setattr(m, "LessonLensClient", FakeClient)
    for key in ("LESSONLENS_REMOTE_EMAIL", "LESSONLENS_REMOTE_PASSWORD", "LESSONLENS_REMOTE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LESSONLENS_TARGET", "local")
    monkeypatch.setenv("LESSONLENS_EMAIL", "same@example.com")
    monkeypatch.setenv("LESSONLENS_PASSWORD", "same-pw")
    monkeypatch.setenv("LESSONLENS_API_URL", "https://hosted.example.com")

    args = m.build_arg_parser().parse_args([
        "--target", "local", "--push", "--sync-only",
        "--skip-export", "--skip-images",
        "--state-file", str(tmp_path / "state.json"),
    ])
    assert m.run(args) == 0
    assert sent["remote"] == ("https://hosted.example.com", "same@example.com", "same-pw")


def test_run_dry_run(tmp_path, capsys):
    # An export in a searched dir + one image dir; --dry-run makes no network calls.
    export_dir = tmp_path / "dl"
    export_dir.mkdir()
    (export_dir / "[LINE] Chat.txt").write_text("2026.03.08 Sunday\n")
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    (img_dir / "photo").write_bytes(b"\xff\xd8\xffPHOTO")

    args = m.build_arg_parser().parse_args(
        [
            "--dry-run",
            "--export-dir",
            str(export_dir),
            "--images-dir",
            str(img_dir),
            "--state-file",
            str(tmp_path / "state.json"),
        ]
    )
    rc = m.run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run" in out
    assert "Would sync export" in out
    assert "Would upload image" in out


# --- capture time travels with the bytes -----------------------------------

def test_source_timestamp_for_is_naive_local(tmp_path):
    """Lesson windows are naive local times, so the stamp must be too."""
    import datetime as _dt

    path = tmp_path / "photo"
    path.write_bytes(b"\xff\xd8\xffPHOTO")
    os.utime(path, (1_754_388_390, 1_754_388_390))

    stamp = m.source_timestamp_for(path)
    assert stamp == _dt.datetime.fromtimestamp(1_754_388_390).isoformat()
    assert "+" not in stamp and not stamp.endswith("Z")


def test_source_timestamp_for_missing_file(tmp_path):
    assert m.source_timestamp_for(tmp_path / "gone") is None


def test_upload_images_sends_source_timestamps(tmp_path, monkeypatch):
    """The server's copy is stamped 'now', so the original mtime must be sent."""
    captured = {}

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/api/login"):
            return _FakeResp({"access_token": "tok"})
        captured["body"] = req.data
        captured["content_type"] = req.get_header("Content-type")
        return _FakeResp({"attachments": [], "uploaded": 0})

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)

    first = tmp_path / "hashed_a"
    first.write_bytes(b"\xff\xd8\xffAAA")
    os.utime(first, (1_754_388_390, 1_754_388_390))
    second = tmp_path / "hashed_b"
    second.write_bytes(b"\x89PNG\r\n\x1a\nBBB")
    os.utime(second, (1_755_000_000, 1_755_000_000))

    client = m.LessonLensClient("https://example.com")
    client.login("me@example.com", "pw")
    client.upload_images([first, second], name_hint=m.upload_name_for)

    body = captured["body"].decode("utf-8", "replace")
    assert 'name="source_timestamps"' in body
    payload = body.split('name="source_timestamps"')[1]
    stamps = json.loads(payload.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert stamps == [
        m.source_timestamp_for(first),
        m.source_timestamp_for(second),
    ]
    # Order must line up with the files, which the server indexes positionally.
    assert body.index('filename="hashed_a.jpg"') < body.index('filename="hashed_b.png"')


def test_upload_name_for_gives_extensionless_files_a_real_extension(tmp_path):
    path = tmp_path / "0f3a9c1b2d"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert m.upload_name_for(path, path.read_bytes()) == ("0f3a9c1b2d.png", "image/png")

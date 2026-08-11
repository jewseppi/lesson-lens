"""line_mac_sync.py — one-touch LINE → LessonLens updater for macOS.

The goal is a single command you run from your Mac after exporting a LINE chat:
it finds the newest chat export, scans LINE's local media cache for images you
haven't synced yet, pushes both to your **hosted** LessonLens server, and (by
default) generates the lesson package for the newest session.

Why this exists
---------------
LINE's text export contains only ``[Photo]`` / ``Photos`` placeholders — never
the actual image files — so historically you had to save every image by hand
and upload them one at a time. This script automates the image grab: it reads
LINE's on-disk media cache (files are stored with hashed names and often no
extension, so we sniff magic bytes rather than trust the filename), dedupes by
SHA-256, and uploads only what's new. The server then matches each image to a
session by EXIF/timestamp, exactly as the manual upload flow does.

The one manual step that remains
--------------------------------
macOS LINE has no scriptable "export chat" hook, so you still trigger the export
from LINE's UI once (right-click the chat → export). Everything after that is
this one command. If reading LINE's encrypted cache doesn't yield usable images
on your machine, point ``--images-dir`` at a folder where you save images and
the rest of the flow is unchanged.

Configuration
-------------
Targets the hosted app via environment variables (same names the CI workflows
use), which can also live in a repo-root ``.env`` file:

    LESSONLENS_API_URL   e.g. https://lessonlens.example.com
    LESSONLENS_EMAIL     your login email
    LESSONLENS_PASSWORD  your login password

Usage
-----
    python scripts/line_mac_sync.py                 # find export, sync chat + new images, generate latest
    python scripts/line_mac_sync.py --sync-only      # skip generation
    python scripts/line_mac_sync.py --dry-run        # show what would be synced, touch nothing
    python scripts/line_mac_sync.py --export-file /path/to/chat.txt
    python scripts/line_mac_sync.py --images-dir ~/Pictures/line-lessons
    python scripts/line_mac_sync.py --generate-all   # generate every session missing a summary

This script uses only the Python standard library so it runs on a stock macOS
Python 3 with no ``pip install``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Image detection — LINE caches media with hashed names and often no extension,
# so identify images by magic bytes instead of trusting the filename.
# ---------------------------------------------------------------------------

def sniff_image_type(header: bytes) -> str | None:
    """Return a short image type ('jpeg'|'png'|'gif'|'webp'|'heic'|'bmp') from
    the leading bytes of a file, or None if it does not look like an image.

    Each format is matched against its own minimum signature length so short
    buffers still classify correctly; the multi-byte container formats (WEBP,
    HEIC) simply need enough bytes to inspect their brand box.
    """
    if header[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    # BMP's 2-byte magic is weak, so require a plausible full header length to
    # avoid matching text that happens to start with "BM".
    if len(header) >= 14 and header[:2] == b"BM":
        return "bmp"
    if len(header) >= 12:
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "webp"
        # HEIC/HEIF: ISO-BMFF 'ftyp' box with a heic/heif/mif1 brand
        if header[4:8] == b"ftyp" and header[8:12] in (
            b"heic",
            b"heix",
            b"heif",
            b"mif1",
            b"hevc",
            b"hevx",
        ):
            return "heic"
    return None


def is_image_file(path: Path, _cache: dict[str, bool] | None = None) -> bool:
    try:
        with open(path, "rb") as fh:
            return sniff_image_type(fh.read(32)) is not None
    except OSError:
        return False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Default macOS locations (all overridable via CLI flags). These are candidates
# only — the script uses the ones that actually exist on the machine.
# ---------------------------------------------------------------------------

def default_export_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
    ]


def default_cache_dirs() -> list[Path]:
    home = Path.home()
    return [
        # Mac App Store (sandboxed) LINE
        home
        / "Library/Containers/jp.naver.line.mac/Data/Library/Application Support/LINE",
        # Non-sandboxed install
        home / "Library/Application Support/LINE",
    ]


# Filename hints that a .txt is a LINE chat export rather than an unrelated file.
EXPORT_NAME_HINTS = ("line", "chat", "talk", "聊天", "對話", "会話")


def find_latest_export(
    export_dirs: list[Path], explicit: Path | None = None
) -> Path | None:
    """Pick the newest LINE chat-export .txt across export_dirs.

    Prefers files whose name looks like a LINE export; falls back to the newest
    .txt if none match. Returns None when nothing is found.
    """
    if explicit is not None:
        return explicit if explicit.is_file() else None

    txts: list[Path] = []
    for d in export_dirs:
        if not d.is_dir():
            continue
        try:
            txts.extend(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
        except OSError:
            continue
    if not txts:
        return None

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    hinted = [p for p in txts if any(h in p.name.lower() for h in EXPORT_NAME_HINTS)]
    pool = hinted or txts
    return max(pool, key=mtime)


def discover_cache_dirs(candidates: list[Path]) -> list[Path]:
    return [d for d in candidates if d.is_dir()]


def iter_candidate_images(dirs: list[Path], min_mtime: float = 0.0):
    """Yield paths under dirs that look like images and were modified at or after
    min_mtime. Walks recursively; skips unreadable entries."""
    for root in dirs:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < min_mtime:
                    continue
                if is_image_file(p):
                    yield p


def select_new_images(paths, synced_shas: set[str]):
    """Return [(path, sha256)] for images whose content hasn't been synced yet.
    Dedupes within the batch as well as against synced_shas."""
    selected: list[tuple[Path, str]] = []
    seen: set[str] = set(synced_shas)
    for p in paths:
        try:
            sha = file_sha256(p)
        except OSError:
            continue
        if sha in seen:
            continue
        seen.add(sha)
        selected.append((p, sha))
    return selected


# ---------------------------------------------------------------------------
# Incremental state — remembers which image contents were already synced and a
# per-run mtime watermark so repeat runs stay fast and idempotent.
# ---------------------------------------------------------------------------

def default_state_file() -> Path:
    return Path.home() / ".lessonlens" / "mac_sync_state.json"


def load_state(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("synced_image_sha256", [])
    data.setdefault("image_mtime_watermark", 0.0)
    data.setdefault("last_export_sha256", "")
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Minimal multipart/form-data encoder (stdlib only).
# ---------------------------------------------------------------------------

def encode_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]):
    """Return (content_type, body_bytes).

    fields: {name: value}
    files:  [(field_name, filename, content, mime_type)]
    """
    boundary = f"----lessonlens{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(("--" + boundary).encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    for field, filename, content, mime in files:
        parts.append(("--" + boundary).encode())
        parts.append(
            (
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"'
            ).encode("utf-8")
        )
        parts.append(f"Content-Type: {mime}".encode())
        parts.append(b"")
        parts.append(content)
    parts.append(("--" + boundary + "--").encode())
    parts.append(b"")
    body = crlf.join(parts)
    return f"multipart/form-data; boundary={boundary}", body


# ---------------------------------------------------------------------------
# Hosted API client (stdlib urllib).
# ---------------------------------------------------------------------------

class ApiError(RuntimeError):
    pass


class LessonLensClient:
    def __init__(self, base_url: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def _request(self, method: str, path: str, *, headers=None, data=None):
        url = self.base_url + path
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise ApiError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {path} failed: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}

    def login(self, email: str, password: str) -> None:
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        result = self._request(
            "POST", "/api/login", headers={"Content-Type": "application/json"}, data=body
        )
        token = result.get("access_token")
        if not token:
            raise ApiError(f"Login did not return a token: {result}")
        self.token = token

    def sync_export(self, path: Path) -> dict:
        content = path.read_bytes()
        content_type, body = encode_multipart(
            {}, [("file", path.name, content, "text/plain")]
        )
        return self._request(
            "POST", "/api/sync", headers={"Content-Type": content_type}, data=body
        )

    def upload_images(self, image_paths: list[Path]) -> dict:
        files = []
        for p in image_paths:
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            files.append(("images", p.name, p.read_bytes(), mime))
        content_type, body = encode_multipart({}, files)
        return self._request(
            "POST",
            "/api/attachments/upload",
            headers={"Content-Type": content_type},
            data=body,
        )

    def list_sessions(self) -> list[dict]:
        result = self._request("GET", "/api/sessions")
        return result if isinstance(result, list) else result.get("sessions", [])

    def generate(self, session_id: str, provider: str | None, model: str | None) -> dict:
        payload: dict[str, str] = {}
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        body = json.dumps(payload).encode("utf-8")
        return self._request(
            "POST",
            f"/api/sessions/{session_id}/generate",
            headers={"Content-Type": "application/json"},
            data=body,
        )

    def generate_all_missing(self, provider: str | None, model: str | None) -> dict:
        payload: dict[str, str] = {}
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        body = json.dumps(payload).encode("utf-8")
        return self._request(
            "POST",
            "/api/summaries/generate",
            headers={"Content-Type": "application/json"},
            data=body,
        )


# ---------------------------------------------------------------------------
# .env loading (no third-party dependency).
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-touch LINE → LessonLens updater for macOS (targets the hosted app)."
    )
    parser.add_argument("--api-url", default=None, help="Hosted API base URL (default: $LESSONLENS_API_URL)")
    parser.add_argument("--email", default=None, help="Login email (default: $LESSONLENS_EMAIL)")
    parser.add_argument("--password", default=None, help="Login password (default: $LESSONLENS_PASSWORD)")
    parser.add_argument("--export-file", default=None, help="Explicit path to a LINE chat export .txt")
    parser.add_argument(
        "--export-dir",
        action="append",
        default=None,
        help="Directory to search for the newest export (repeatable). Defaults to Downloads/Desktop/Documents.",
    )
    parser.add_argument(
        "--images-dir",
        action="append",
        default=None,
        help="Directory to scan for images instead of LINE's cache (repeatable). Most reliable if the cache is encrypted.",
    )
    parser.add_argument("--skip-images", action="store_true", help="Do not sync images this run")
    parser.add_argument("--skip-export", action="store_true", help="Do not sync a chat export this run")
    parser.add_argument("--sync-only", action="store_true", help="Sync only; skip summary generation")
    parser.add_argument("--generate-all", action="store_true", help="Generate every session missing a summary (not just the latest)")
    parser.add_argument("--provider", default=None, help="Generation provider override (e.g. anthropic)")
    parser.add_argument("--model", default=None, help="Generation model override (e.g. claude-opus-5)")
    parser.add_argument("--state-file", default=None, help="Path to the incremental sync state file")
    parser.add_argument("--full-scan", action="store_true", help="Ignore the mtime watermark and rescan all images")
    parser.add_argument("--batch-size", type=int, default=20, help="Images per upload request (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be synced; make no network calls")
    return parser


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(args: argparse.Namespace) -> int:
    load_env_file(repo_root() / ".env")

    api_url = args.api_url or os.environ.get("LESSONLENS_API_URL")
    email = args.email or os.environ.get("LESSONLENS_EMAIL")
    password = args.password or os.environ.get("LESSONLENS_PASSWORD")

    state_file = Path(args.state_file) if args.state_file else default_state_file()
    state = load_state(state_file)

    # --- Resolve the chat export ---
    export_path: Path | None = None
    if not args.skip_export:
        export_dirs = [Path(d).expanduser() for d in args.export_dir] if args.export_dir else default_export_dirs()
        explicit = Path(args.export_file).expanduser() if args.export_file else None
        export_path = find_latest_export(export_dirs, explicit)
        if export_path:
            _log(f"Chat export: {export_path}")
        else:
            _log("Chat export: none found (skipping chat sync). Use --export-file to point at one.")

    # --- Discover new images ---
    new_images: list[tuple[Path, str]] = []
    if not args.skip_images:
        if args.images_dir:
            image_dirs = discover_cache_dirs([Path(d).expanduser() for d in args.images_dir])
        else:
            image_dirs = discover_cache_dirs(default_cache_dirs())
        if not image_dirs:
            _log("Images: no LINE cache found. Use --images-dir to point at a folder of saved images.")
        else:
            watermark = 0.0 if args.full_scan else float(state.get("image_mtime_watermark", 0.0))
            synced = set(state.get("synced_image_sha256", []))
            candidates = list(iter_candidate_images(image_dirs, watermark))
            new_images = select_new_images(candidates, synced)
            _log(
                f"Images: scanned {len(image_dirs)} dir(s), {len(candidates)} candidate file(s), "
                f"{len(new_images)} new."
            )

    if args.dry_run:
        _log("\n--- dry run: no changes made ---")
        if export_path:
            _log(f"Would sync export: {export_path}")
        for p, _sha in new_images[:50]:
            _log(f"Would upload image: {p}")
        if len(new_images) > 50:
            _log(f"... and {len(new_images) - 50} more images")
        return 0

    if not api_url or not email or not password:
        _log(
            "ERROR: hosted API not configured. Set LESSONLENS_API_URL, LESSONLENS_EMAIL, "
            "LESSONLENS_PASSWORD (env or repo .env), or pass --api-url/--email/--password."
        )
        return 2

    client = LessonLensClient(api_url)
    try:
        client.login(email, password)
    except ApiError as exc:
        _log(f"ERROR: {exc}")
        return 2

    summary: dict[str, object] = {}

    # --- Sync chat export ---
    if export_path:
        try:
            sync_result = client.sync_export(export_path)
            summary["chat"] = {
                "file": export_path.name,
                "new_sessions": sync_result.get("new_session_count", sync_result.get("session_count")),
                "duplicate": sync_result.get("duplicate", False),
            }
            _log(f"Synced chat: {summary['chat']}")
        except ApiError as exc:
            _log(f"ERROR syncing chat: {exc}")
            return 1

    # --- Upload images ---
    uploaded = 0
    matched = 0
    if new_images:
        newly_synced_shas: list[str] = []
        max_mtime = float(state.get("image_mtime_watermark", 0.0))
        for start in range(0, len(new_images), max(1, args.batch_size)):
            batch = new_images[start : start + max(1, args.batch_size)]
            try:
                result = client.upload_images([p for p, _ in batch])
            except ApiError as exc:
                _log(f"ERROR uploading images (batch at {start}): {exc}")
                break
            for entry in result.get("attachments", []):
                if entry.get("status") in ("created", "duplicate"):
                    uploaded += 1
                match = entry.get("match") or {}
                if match.get("session_id"):
                    matched += 1
            # Record shas + advance watermark only for the batch we uploaded.
            for p, sha in batch:
                newly_synced_shas.append(sha)
                try:
                    max_mtime = max(max_mtime, p.stat().st_mtime)
                except OSError:
                    pass
        state["synced_image_sha256"] = sorted(
            set(state.get("synced_image_sha256", [])) | set(newly_synced_shas)
        )
        state["image_mtime_watermark"] = max_mtime
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state_file, state)
        summary["images"] = {"uploaded": uploaded, "auto_matched": matched}
        _log(f"Uploaded images: {summary['images']}")

    # --- Generate ---
    if not args.sync_only:
        try:
            if args.generate_all:
                gen = client.generate_all_missing(args.provider, args.model)
                summary["generation"] = gen
                _log(f"Generation (all missing): {gen}")
            else:
                sessions = client.list_sessions()
                if sessions:
                    target = sessions[0].get("session_id")
                    gen = client.generate(target, args.provider, args.model)
                    summary["generation"] = {
                        "session_id": target,
                        "title": gen.get("title"),
                        "vocabulary": len(gen.get("vocabulary", [])),
                    }
                    _log(f"Generated latest session: {summary['generation']}")
                else:
                    _log("Generation: no sessions available.")
        except ApiError as exc:
            _log(f"WARNING: generation failed (sync is complete): {exc}")

    _log("\n=== update complete ===")
    _log(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

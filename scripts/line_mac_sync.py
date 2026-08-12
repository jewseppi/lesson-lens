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
from LINE's UI once: open the lesson chat → chat menu (top-right) → Save chat
history. Everything after that is this one command.

This script has to run on the Mac itself. LINE only talks to the machine it runs
on, so no amount of credentialing lets a hosted server or container reach it —
which is why the flow is Mac → hosted, and never the other way. If reading
LINE's encrypted cache doesn't yield usable images on your machine, point
``--images-dir`` at a folder where you save images and the rest is unchanged.

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
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# The HTTP client and config resolution are shared with the hosted MCP server
# (api/mcp_server_hosted.py) so there is one implementation of each. They are
# re-exported here because this module is also the CLI entry point.
from lessonlens_client import (  # noqa: E402,F401
    ApiError,
    LessonLensClient,
    encode_multipart,
    source_timestamp_for,
)
from lessonlens_config import (  # noqa: E402,F401
    TARGET_HOSTED,
    TARGET_LOCAL,
    Config,
    load_config,
    load_env_file,
    repo_root,
)

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


_MIME_FOR_KIND = {
    "jpeg": ("jpg", "image/jpeg"),
    "png": ("png", "image/png"),
    "gif": ("gif", "image/gif"),
    "webp": ("webp", "image/webp"),
    "heic": ("heic", "image/heic"),
    "bmp": ("bmp", "image/bmp"),
}


def upload_name_for(path: Path, data: bytes) -> tuple[str, str]:
    """Filename + MIME to upload a cached image under.

    LINE stores media with hashed, extension-less names. Sending those verbatim
    gets them rejected as an unsupported format, so derive both from the actual
    magic bytes and keep the original name as the stem for traceability.
    """
    kind = sniff_image_type(data[:32])
    ext, mime = _MIME_FOR_KIND.get(kind or "", ("jpg", "image/jpeg"))
    stem = Path(path).stem or "line-image"
    return f"{stem}.{ext}", mime


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
    """Places a LINE chat export plausibly lands on macOS.

    The container paths matter: LINE from the Mac App Store is sandboxed, so its
    "Downloads"/"Desktop" are redirected inside the container rather than the
    real home folder. iCloud Desktop/Documents matter for anyone with Desktop &
    Documents syncing turned on.
    """
    home = Path.home()
    container = home / "Library/Containers/jp.naver.line.mac/Data"
    icloud = home / "Library/Mobile Documents/com~apple~CloudDocs"
    return [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        # Sandboxed LINE writes here instead of the real ~/Downloads.
        container / "Downloads",
        container / "Desktop",
        container / "Documents",
        icloud / "Desktop",
        icloud / "Documents",
    ]


def default_cache_dirs() -> list[Path]:
    home = Path.home()
    container = home / "Library/Containers/jp.naver.line.mac/Data"
    return [
        # Mac App Store (sandboxed) LINE
        container / "Library/Application Support/LINE",
        container / "Library/Caches",
        # Non-sandboxed install
        home / "Library/Application Support/LINE",
        home / "Library/Caches/jp.naver.line.mac",
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


def find_export_candidates(export_dirs: list[Path], limit: int = 10) -> list[dict]:
    """Every .txt that could be a LINE export, newest first, with why it ranked.

    Used by `make doctor` so a missing export is a report of what was searched
    rather than a bare "none found".
    """
    found: list[dict] = []
    for directory in export_dirs:
        if not directory.is_dir():
            continue
        try:
            entries = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
        except OSError:
            continue
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            hinted = any(h in path.name.lower() for h in EXPORT_NAME_HINTS)
            found.append({
                "path": path,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "name_hinted": hinted,
            })
    # Name-hinted files first (that is how find_latest_export chooses), then newest.
    found.sort(key=lambda item: (item["name_hinted"], item["mtime"]), reverse=True)
    return found[:limit]


def searched_dirs_report(export_dirs: list[Path]) -> list[str]:
    """Human-readable list of which candidate directories exist."""
    return [
        f"{'exists' if d.is_dir() else 'missing'}: {d}"
        for d in export_dirs
    ]


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
# HTTP client, multipart encoding, and .env loading now live in the shared
# modules imported at the top of this file (lessonlens_client / lessonlens_config)
# so the hosted MCP server reuses exactly the same implementations.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Generation modes
# ---------------------------------------------------------------------------

def resolve_generate_mode(requested: str | None, cfg: Config) -> str:
    """Decide who generates summaries: 'agent', 'provider', or 'none'.

    Defaults to the subscription agent when one is configured, because that path
    costs nothing per token; otherwise falls back to provider-backed generation.
    """
    if requested:
        return requested
    return "agent" if cfg.has_agent_cmd else "provider"


def run_agent_generation(client, cfg: Config, max_sessions: int = 10) -> dict:
    """Generate summaries using the configured subscription-agent CLI.

    With no LESSONLENS_AGENT_CMD configured this is deliberately *prepare only*:
    it reports which sessions still need a summary and runs nothing. That keeps
    a misconfigured command from fanning out across the whole backlog, and lets
    you drive the agent interactively (via the lessonlens-hosted MCP server)
    instead if you prefer.
    """
    sessions = client.list_sessions()
    pending = [
        s.get("session_id")
        for s in sessions
        if s.get("needs_summary") and s.get("session_id")
    ]

    if not pending:
        _log("Generation (agent): every session already has a summary.")
        return {"mode": "agent", "pending": 0, "ran": 0}

    if not cfg.has_agent_cmd:
        listed = ", ".join(pending[:10]) + ("..." if len(pending) > 10 else "")
        _log(
            f"Generation (agent): {len(pending)} session(s) need a summary: {listed}\n"
            "  No LESSONLENS_AGENT_CMD configured, so nothing was run.\n"
            "  Either set it (e.g. LESSONLENS_AGENT_CMD='claude -p \"...{session_id}...\"'),\n"
            "  or ask your agent to summarize them via the lessonlens-hosted MCP server."
        )
        return {"mode": "agent", "pending": len(pending), "ran": 0, "sessions": pending}

    targets = pending[: max(0, max_sessions)]
    if len(pending) > len(targets):
        _log(
            f"Generation (agent): {len(pending)} pending, running the first "
            f"{len(targets)} (raise --max-sessions for more)."
        )

    ran, failed = 0, 0
    for session_id in targets:
        cmd = cfg.build_agent_command(session_id)
        _log(f"  agent → {session_id}: {' '.join(cmd)}")
        try:
            # No shell: cmd is an argv list, so a session id can't inject commands.
            result = subprocess.run(cmd, cwd=str(repo_root()), timeout=1800)
            if result.returncode == 0:
                ran += 1
            else:
                failed += 1
                _log(f"  agent command exited {result.returncode} for {session_id}")
        except (OSError, subprocess.SubprocessError) as exc:
            failed += 1
            _log(f"  agent command failed for {session_id}: {exc}")

    return {
        "mode": "agent",
        "pending": len(pending),
        "ran": ran,
        "failed": failed,
        "sessions": targets,
    }


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
    parser.add_argument(
        "--generate-with",
        choices=("agent", "provider", "none"),
        default=None,
        help=(
            "Who generates summaries. 'agent' uses your subscription CLI via "
            "$LESSONLENS_AGENT_CMD (no API key, no per-token cost); 'provider' calls the "
            "server's configured LLM provider; 'none' skips generation. "
            "Default: agent when $LESSONLENS_AGENT_CMD is set, else provider."
        ),
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=10,
        help="Cap how many sessions the agent command is run for in one pass (default: 10)",
    )
    parser.add_argument("--provider", default=None, help="Generation provider override (e.g. anthropic)")
    parser.add_argument("--model", default=None, help="Generation model override (e.g. claude-opus-5)")
    parser.add_argument("--state-file", default=None, help="Path to the incremental sync state file")
    parser.add_argument("--full-scan", action="store_true", help="Ignore the mtime watermark and rescan all images")
    parser.add_argument("--batch-size", type=int, default=20, help="Images per upload request (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be synced; make no network calls")
    parser.add_argument(
        "--target",
        choices=(TARGET_HOSTED, TARGET_LOCAL),
        default=None,
        help="Which instance to sync into (default: $LESSONLENS_TARGET, else hosted)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help=(
            "After syncing into a LOCAL instance, push it up to the hosted one via "
            "/api/backup/sync-remote. Only meaningful with --target local."
        ),
    )
    parser.add_argument(
        "--remote-url",
        default=None,
        help="Hosted URL to push to with --push (default: $LESSONLENS_REMOTE_URL, else $LESSONLENS_API_URL)",
    )
    parser.add_argument(
        "--remote-email",
        default=None,
        help=(
            "Login for the HOSTED instance when pushing (default: $LESSONLENS_REMOTE_EMAIL, "
            "else $LESSONLENS_EMAIL). Set this when local and hosted logins differ."
        ),
    )
    parser.add_argument(
        "--remote-password",
        default=None,
        help="Password for the HOSTED instance when pushing (default: $LESSONLENS_REMOTE_PASSWORD, else $LESSONLENS_PASSWORD)",
    )
    return parser


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(args: argparse.Namespace) -> int:
    cfg = load_config(
        target=args.target,
        api_url=args.api_url,
        email=args.email,
        password=args.password,
    )

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
            _log(
                "Chat export: none found (skipping chat sync). Searched:\n  "
                + "\n  ".join(searched_dirs_report(export_dirs))
                + "\nIn LINE for Mac: open the lesson chat -> chat menu (top-right)"
                "\n-> Save chat history -> save the .txt to Downloads or Desktop."
                "\nAlready have one elsewhere? --export-file /path/to/export.txt"
                "\nTo locate it: mdfind -name '.txt' -onlyin ~ | head -20"
            )

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

    problems = cfg.validate()
    if problems:
        _log("ERROR: LessonLens is not configured for this run:")
        for problem in problems:
            _log(f"  - {problem}")
        return 2

    _log(f"Target: {cfg.target} ({cfg.api_url})")
    client = LessonLensClient(cfg.api_url)
    try:
        client.login(cfg.email, cfg.password)
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
                result = client.upload_images(
                    [p for p, _ in batch], name_hint=upload_name_for
                )
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

    # --- Re-match orphaned images ---
    # LINE caches a photo the moment it arrives, but the chat export that
    # explains it is taken later. Images uploaded in that gap had no session to
    # match; now that this run may have added some, give them another chance.
    chat_summary = summary.get("chat") or {}
    if isinstance(chat_summary, dict) and chat_summary.get("new_sessions"):
        try:
            rematch = client.rematch_attachments()
            if rematch.get("matched"):
                summary["rematched"] = rematch["matched"]
                _log(
                    f"Re-matched {rematch['matched']} previously unmatched image(s) "
                    f"of {rematch.get('candidates', 0)} candidate(s)."
                )
        except ApiError as exc:
            # Older servers won't have the endpoint; never fail the run over it.
            _log(f"NOTE: re-match skipped ({exc}).")

    # --- Generate ---
    if not args.sync_only:
        mode = resolve_generate_mode(args.generate_with, cfg)
        try:
            if mode == "none":
                _log("Generation: skipped (--generate-with none).")
            elif mode == "agent":
                summary["generation"] = run_agent_generation(
                    client, cfg, max_sessions=args.max_sessions
                )
            elif args.generate_all:
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

    # --- Push local -> hosted (Mode B fallback) ---
    if args.push:
        if cfg.is_hosted:
            _log(
                "WARNING: --push is only meaningful with --target local "
                "(you are already syncing straight to hosted); skipping."
            )
        else:
            # The hosted instance may well have different credentials than the
            # local one, so resolve them separately and only fall back to the
            # local login when no remote-specific values are given.
            remote_url = (
                args.remote_url
                or os.environ.get("LESSONLENS_REMOTE_URL")
                or os.environ.get("LESSONLENS_API_URL", "")
            )
            remote_email = (
                args.remote_email
                or os.environ.get("LESSONLENS_REMOTE_EMAIL")
                or cfg.email
            )
            remote_password = (
                args.remote_password
                or os.environ.get("LESSONLENS_REMOTE_PASSWORD")
                or cfg.password
            )
            if not (remote_url and remote_email and remote_password):
                _log(
                    "WARNING: --push needs a hosted URL, email and password "
                    "(--remote-url/--remote-email/--remote-password or "
                    "LESSONLENS_REMOTE_*); skipping."
                )
            else:
                try:
                    pushed = client.sync_remote(remote_url, remote_email, remote_password)
                    summary["push"] = pushed
                    _log(f"Pushed local -> hosted ({remote_url}): {pushed}")
                except ApiError as exc:
                    _log(f"WARNING: push to hosted failed: {exc}")

    _log("\n=== update complete ===")
    _log(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

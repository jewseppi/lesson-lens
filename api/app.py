"""
Language Lesson Summarizer — Flask API

Provides REST endpoints for:
  - Auth (JWT, invite-only registration)
  - File upload (LINE chat exports)
  - Parsing / session management
  - Lesson summaries & study assets
  - Analytics events
"""
import hashlib
import io
import json
import mimetypes
import os
import re
import sqlite3
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    get_jwt_identity,
    jwt_required,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


def load_local_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = ROOT_DIR / "web" / "dist"

app = Flask(__name__, static_folder=None)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", app.config["SECRET_KEY"])
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
app.config["UPLOAD_FOLDER"] = str(ROOT_DIR / "raw-exports")
app.config["PROCESSED_FOLDER"] = str(ROOT_DIR / "processed")
app.config["SUMMARIES_FOLDER"] = str(ROOT_DIR / "summaries")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

jwt = JWTManager(app)

CORS(app, origins=[
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    re.compile(r"https://([a-z0-9-]+\.)*pages\.dev"),
])


@app.errorhandler(500)
def handle_500(exc):
    return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(413)
def handle_413(exc):
    return jsonify({"error": "File too large"}), 413

DB_PATH = str(ROOT_DIR / "api" / "lessonlens.db")

# ---------------------------------------------------------------------------
# Preview mode — read-only enforcement for Cloudflare Pages preview builds
# ---------------------------------------------------------------------------
_PREVIEW_SAFE = {"/api/login", "/api/logout", "/api/refresh"}


@app.before_request
def enforce_preview_mode():
    if request.headers.get("X-Preview-Mode") == "true":
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.path not in _PREVIEW_SAFE:
            return jsonify({"error": "This action is not available in preview mode"}), 403


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# ---------------------------------------------------------------------------
# Rate limiter (from xlsvc pattern)
# ---------------------------------------------------------------------------
_rate_counts = defaultdict(list)


def rate_limit(max_requests=10, window_seconds=60):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if app.config.get("TESTING"):
                return f(*args, **kwargs)
            ip = request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr)
            key = f"{ip}:{f.__name__}"
            now = time.time()
            _rate_counts[key] = [t for t in _rate_counts[key] if now - t < window_seconds]
            if len(_rate_counts[key]) >= max_requests:
                return jsonify({"error": "Rate limit exceeded"}), 429
            _rate_counts[key].append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _load_latest_completed_run(conn, user_id):
    return conn.execute(
        "SELECT * FROM parse_runs WHERE user_id = ? AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def _load_sessions_payload(run):
    sessions_path = os.path.join(run["output_dir"], "sessions.json")
    if not os.path.isfile(sessions_path):
        raise FileNotFoundError("Sessions file not found")

    with open(sessions_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {
        session["session_id"]: session
        for session in data.get("sessions", [])
    }


def _extract_session_links(session):
    links = []
    seen = set()
    messages = session.get("messages", []) if isinstance(session, dict) else []
    for index, message in enumerate(messages):
        text = (message.get("text_raw") or "").strip()
        if not text:
            continue
        for match in re.findall(r"https?://\S+", text):
            url = match.rstrip(').,!?]}>"\'')
            if not url or url in seen:
                continue
            seen.add(url)

            before_text = None
            after_text = None
            for previous in reversed(messages[:index]):
                candidate = (previous.get("text_raw") or "").strip()
                if candidate and candidate != text:
                    before_text = candidate
                    break
            for following in messages[index + 1:]:
                candidate = (following.get("text_raw") or "").strip()
                if candidate and candidate != text:
                    after_text = candidate
                    break

            links.append({
                "url": url,
                "label": text if text != url else None,
                "speaker_role": message.get("speaker_role"),
                "speaker_raw": message.get("speaker_raw"),
                "time": message.get("time"),
                "before_text": before_text,
                "after_text": after_text,
            })
    return links


def _session_should_list(session):
    message_count = session.get("message_count", 0) if isinstance(session, dict) else 0
    links = _extract_session_links(session)
    return message_count >= 3 or bool(links)


def _load_generator_config(provider_override=None, model_override=None):
    import sys as _sys

    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)

    from generate_outputs import load_config as load_gen_config, process_session

    gen_config = load_gen_config()
    gen_defaults = gen_config.get("generation", {})
    local_defaults = gen_config.get("local", {})
    use_provider = provider_override or gen_defaults.get("default_provider", "openai")

    # Resolve model: explicit override > env var > config default
    if model_override:
        use_model = model_override
    elif use_provider == "ollama":
        use_model = os.environ.get("OLLAMA_MODEL") or local_defaults.get("ollama_model", "qwen2.5:7b-instruct")
    elif use_provider == "openai_compatible_local":
        use_model = os.environ.get("LOCAL_OAI_MODEL") or local_defaults.get("openai_compatible_local_model", "local-model")
    else:
        use_model = gen_defaults.get("default_model", "gpt-4o")

    temperature = gen_defaults.get("temperature", 0.3)

    return process_session, gen_config, use_provider, use_model, temperature


ALLOWED_PROVIDERS = {"openai", "anthropic", "gemini", "ollama", "openai_compatible_local"}


def _validate_provider_credentials(provider_name):
    if provider_name not in ALLOWED_PROVIDERS:
        return f"Unknown provider '{provider_name}'. Supported: {', '.join(sorted(ALLOWED_PROVIDERS))}"
    if provider_name == "openai" and not os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY not set. Export it before starting the server."
    if provider_name == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY not set. Export it before starting the server."
    if provider_name == "gemini" and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return "GEMINI_API_KEY or GOOGLE_API_KEY not set. Export it before starting the server."
    # Local providers (ollama, openai_compatible_local) need no API key.
    return None


def _store_lesson_summary(conn, session_row, run, user_id, provider_name, model_name, lesson_data, output_dir):
    conn.execute(
        """INSERT INTO lesson_summaries
           (session_db_id, run_id, session_id, user_id, provider, model, lesson_data_json, output_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_row["id"],
            run["run_id"],
            session_row["session_id"],
            user_id,
            provider_name,
            model_name,
            json.dumps(lesson_data, ensure_ascii=False),
            output_dir,
        ),
    )


def _generate_summary_for_session(conn, user, run, session_row, session_data, provider_override=None, model_override=None):
    process_session, gen_config, use_provider, use_model, temperature = _load_generator_config(
        provider_override,
        model_override,
    )

    credential_error = _validate_provider_credentials(use_provider)
    if credential_error:
        raise ValueError(credential_error)

    # --- Policy check ---
    policy_action, policy_msg = _check_generation_policy(conn, use_provider, use_model)
    if policy_action == "block":
        raise ValueError(f"Policy blocked: {policy_msg}")
    # "warn" is returned alongside the result for the frontend to display

    session_id = session_row["session_id"]

    # --- Load corrections from feedback signals and annotations ---
    corrections = _load_corrections_for_session(conn, user["id"], session_id)

    # --- Retrieve prior context for prompt injection (Phase 4) ---
    retrieval_context = _retrieve_context_for_session(
        conn, user["id"], session_id, session_data,
    )
    retrieval_block = build_retrieval_context_block(retrieval_context)

    gen_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_base = os.path.join(
        os.path.dirname(__file__), "..", "summaries", gen_run_id
    )

    result = process_session(
        session_data,
        gen_config,
        use_provider,
        use_model,
        temperature,
        gen_run_id,
        output_base,
        corrections=corrections if corrections else None,
        retrieval_context=retrieval_block if retrieval_block else None,
    )

    lesson_json_path = os.path.join(result["output_dir"], "lesson-data.json")
    with open(lesson_json_path, "r", encoding="utf-8") as f:
        lesson_data = json.load(f)

    _store_lesson_summary(
        conn,
        session_row,
        run,
        user["id"],
        use_provider,
        use_model,
        lesson_data,
        result["output_dir"],
    )

    # --- Index retrieval items from this generation (Phase 4) ---
    _index_retrieval_items(conn, user["id"], session_id, lesson_data)

    # Mark annotations as applied after successful generation
    if corrections:
        conn.execute(
            """UPDATE annotations SET status = 'applied', updated_at = datetime('now')
               WHERE session_id = ? AND user_id = ? AND status = 'active'""",
            (session_id, user["id"]),
        )

    return lesson_data, use_provider, use_model, policy_action, policy_msg


def _load_corrections_for_session(conn, user_id, session_id):
    """Load all accepted corrections for a session from feedback signals, AI reviews, and annotations."""
    corrections = []

    # 1. Accepted AI review parse findings (reclassifications)
    review_rows = conn.execute(
        """SELECT findings_json FROM ai_reviews
           WHERE session_id = ? AND user_id = ? AND accepted_count > 0""",
        (session_id, user_id),
    ).fetchall()
    for row in review_rows:
        findings = json.loads(row["findings_json"])
        for f in findings:
            if f.get("status") != "accepted":
                continue
            if f.get("suggested_type"):
                corrections.append({
                    "type": "reclassify_message",
                    "message_id": f.get("message_id"),
                    "original": f.get("current_type"),
                    "corrected": f.get("suggested_type"),
                    "detail": f.get("reason", ""),
                })

    # 2. Feedback signals (manual reclassifications)
    signal_rows = conn.execute(
        """SELECT * FROM feedback_signals
           WHERE session_id = ? AND user_id = ? AND signal_type = 'reclassify_message'""",
        (session_id, user_id),
    ).fetchall()
    for sig in signal_rows:
        corrections.append({
            "type": "reclassify_message",
            "message_id": sig["target_id"],
            "original": sig["original_value"],
            "corrected": sig["corrected_value"],
        })

    # 3. Active annotations (corrections, notes)
    ann_rows = conn.execute(
        """SELECT * FROM annotations
           WHERE session_id = ? AND status = 'active'""",
        (session_id,),
    ).fetchall()
    for ann in ann_rows:
        content = json.loads(ann["content_json"])
        atype = ann["annotation_type"]
        if atype == "correction":
            field = content.get("field", "")
            ctype = "pinyin" if field == "pinyin" else ("translation" if field == "en" else "annotation")
            corrections.append({
                "type": ctype,
                "item_id": ann["target_id"],
                "original": content.get("original", ""),
                "corrected": content.get("corrected", ""),
                "detail": content.get("reason", ""),
            })
        elif atype == "reclassify":
            corrections.append({
                "type": "reclassify_message",
                "message_id": ann["target_id"],
                "original": content.get("original_type", ""),
                "corrected": content.get("corrected_type", ""),
            })
        elif atype == "note" and content.get("text"):
            corrections.append({
                "type": "annotation",
                "message_id": ann["target_id"],
                "detail": f"User note on {ann['target_id']}: {content['text']}",
            })

    return corrections


# ---------------------------------------------------------------------------
# Phase 4: Retrieval Index + Feedback Memory
# ---------------------------------------------------------------------------

def _index_retrieval_items(conn, user_id, session_id, lesson_data):
    """Extract vocab, key sentences, and corrections from a generated summary
    and store them in the retrieval index for future context injection."""
    # Clear old items for this session (regeneration)
    conn.execute(
        "DELETE FROM user_retrieval_items WHERE user_id = ? AND session_id = ?",
        (user_id, session_id),
    )

    items = []

    # Vocabulary
    for v in lesson_data.get("vocabulary", []):
        term = v.get("term", "")
        if not term:
            continue
        items.append((
            user_id, session_id, "vocab", term,
            json.dumps({
                "term": term,
                "pinyin": v.get("pinyin", ""),
                "meaning": v.get("meaning", ""),
                "pos": v.get("pos", ""),
                "example": v.get("example_sentence", ""),
            }, ensure_ascii=False),
            "generation",
        ))

    # Key sentences
    for ks in lesson_data.get("key_sentences", []):
        zh = ks.get("zh", "")
        if not zh:
            continue
        items.append((
            user_id, session_id, "key_sentence", zh,
            json.dumps({
                "zh": zh,
                "pinyin": ks.get("pinyin", ""),
                "en": ks.get("en", ""),
                "context_note": ks.get("context_note", ""),
            }, ensure_ascii=False),
            "generation",
        ))

    # Corrections from summary (teacher corrections captured by LLM)
    for c in lesson_data.get("corrections", []):
        wrong = c.get("student_said", c.get("wrong", ""))
        if not wrong:
            continue
        items.append((
            user_id, session_id, "correction", wrong,
            json.dumps({
                "student_said": wrong,
                "correct_form": c.get("correct_form", c.get("corrected", "")),
                "explanation": c.get("explanation", c.get("detail", "")),
            }, ensure_ascii=False),
            "generation",
        ))

    if items:
        conn.executemany(
            """INSERT INTO user_retrieval_items
               (user_id, session_id, item_type, item_key, item_data_json, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            items,
        )

    return len(items)


def _record_feedback_memory(conn, user_id, session_id, action, target_type,
                            target_id=None, original=None, corrected=None, detail=None):
    """Record a user feedback action for retrieval memory."""
    conn.execute(
        """INSERT INTO user_feedback_memory
           (user_id, session_id, action, target_type, target_id,
            original_json, corrected_json, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, session_id, action, target_type, target_id,
            json.dumps(original, ensure_ascii=False) if original else None,
            json.dumps(corrected, ensure_ascii=False) if corrected else None,
            detail,
        ),
    )


def _retrieve_context_for_session(conn, user_id, session_id, session_data, max_items=20):
    """Build retrieval context from prior sessions for prompt injection.

    Strategy:
    1. Find vocab terms from the current session transcript that appear in prior summaries.
    2. Pull recent corrections the user has made (from feedback_memory).
    3. Pull recent key sentences from adjacent sessions for continuity.
    Returns a structured dict with sections for the prompt builder.
    """
    # Extract Chinese characters from current transcript for matching
    current_terms = set()
    for msg in session_data.get("messages", []):
        text = msg.get("text_normalized", msg.get("text_raw", ""))
        current_terms.update(_extract_cjk_tokens(text))

    context = {
        "prior_vocab": [],
        "prior_corrections": [],
        "prior_sentences": [],
        "feedback_patterns": [],
    }

    if not current_terms:
        return context

    # 1. Find matching vocab from other sessions
    vocab_rows = conn.execute(
        """SELECT DISTINCT item_key, item_data_json, session_id
           FROM user_retrieval_items
           WHERE user_id = ? AND item_type = 'vocab' AND session_id != ?
           ORDER BY created_at DESC
           LIMIT 200""",
        (user_id, session_id),
    ).fetchall()

    for row in vocab_rows:
        term = row["item_key"]
        if term in current_terms or any(term in t for t in current_terms):
            data = json.loads(row["item_data_json"])
            data["from_session"] = row["session_id"]
            context["prior_vocab"].append(data)
            if len(context["prior_vocab"]) >= max_items // 3:
                break

    # 2. Recent user corrections from feedback memory
    correction_rows = conn.execute(
        """SELECT corrected_json, detail, target_type, action, session_id
           FROM user_feedback_memory
           WHERE user_id = ? AND action IN ('correct', 'reclassify', 'accept_correction')
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, max_items // 3),
    ).fetchall()

    for row in correction_rows:
        context["feedback_patterns"].append({
            "action": row["action"],
            "type": row["target_type"],
            "corrected": json.loads(row["corrected_json"]) if row["corrected_json"] else None,
            "detail": row["detail"],
            "session_id": row["session_id"],
        })

    # 3. Key sentences from recent adjacent sessions (for continuity)
    sentence_rows = conn.execute(
        """SELECT item_key, item_data_json, session_id
           FROM user_retrieval_items
           WHERE user_id = ? AND item_type = 'key_sentence' AND session_id != ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, session_id, max_items // 3),
    ).fetchall()

    for row in sentence_rows:
        data = json.loads(row["item_data_json"])
        data["from_session"] = row["session_id"]
        context["prior_sentences"].append(data)

    # 4. Correction patterns from prior generations
    corr_rows = conn.execute(
        """SELECT item_key, item_data_json
           FROM user_retrieval_items
           WHERE user_id = ? AND item_type = 'correction' AND session_id != ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, session_id, max_items // 3),
    ).fetchall()

    for row in corr_rows:
        data = json.loads(row["item_data_json"])
        context["prior_corrections"].append(data)

    return context


def _extract_cjk_tokens(text):
    """Extract CJK character sequences from text as simple tokens."""
    import re
    return set(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]{2,}', text))


def build_retrieval_context_block(context):
    """Format retrieval context as a text block for prompt injection."""
    sections = []

    if context.get("prior_vocab"):
        lines = ["## Prior Vocabulary (from earlier lessons)",
                 "The student has previously studied these terms. Reuse consistent translations:"]
        for v in context["prior_vocab"]:
            lines.append(f"- {v['term']} ({v.get('pinyin', '')}) = {v.get('meaning', '')} [{v.get('from_session', '')}]")
        sections.append("\n".join(lines))

    if context.get("prior_corrections"):
        lines = ["## Common Student Errors",
                 "The student has made these errors before. Watch for similar patterns:"]
        for c in context["prior_corrections"]:
            lines.append(f"- Said \"{c.get('student_said', '')}\" → should be \"{c.get('correct_form', '')}\" ({c.get('explanation', '')})")
        sections.append("\n".join(lines))

    if context.get("feedback_patterns"):
        lines = ["## User Correction History",
                 "The user has made these corrections to previous summaries:"]
        for f in context["feedback_patterns"]:
            detail = f.get("detail") or json.dumps(f.get("corrected"), ensure_ascii=False) or ""
            lines.append(f"- {f['action']} ({f['type']}): {detail}")
        sections.append("\n".join(lines))

    if context.get("prior_sentences"):
        lines = ["## Recent Key Sentences (from adjacent lessons)",
                 "For continuity, here are sentences from recent lessons:"]
        for s in context["prior_sentences"][:5]:  # limit to 5
            lines.append(f"- {s.get('zh', '')} ({s.get('pinyin', '')}) = {s.get('en', '')}")
        sections.append("\n".join(lines))

    if not sections:
        return ""

    return "\n\n".join(sections) + "\n"


def _table_has_column(conn, table, column):
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c["name"] == column for c in cols)


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            is_admin INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            last_login_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_size INTEGER,
            line_count INTEGER,
            uploaded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS parse_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            upload_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            session_count INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            lesson_content_count INTEGER DEFAULT 0,
            output_dir TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (upload_id) REFERENCES uploads(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            user_id INTEGER,
            session_id TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            message_count INTEGER DEFAULT 0,
            lesson_content_count INTEGER DEFAULT 0,
            teacher_message_count INTEGER DEFAULT 0,
            student_message_count INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            boundary_confidence TEXT,
            topics_json TEXT DEFAULT '[]',
            FOREIGN KEY (run_id) REFERENCES parse_runs(run_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS lesson_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_db_id INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            provider TEXT,
            model TEXT,
            lesson_data_json TEXT,
            output_dir TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (session_db_id) REFERENCES sessions(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_type TEXT NOT NULL,
            event_data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS invitation_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS signup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            display_name TEXT,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            reviewed_by INTEGER,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (reviewed_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            actor_id INTEGER,
            event_type TEXT NOT NULL,
            detail_json TEXT DEFAULT '{}',
            ip_address TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            upload_id INTEGER,
            stored_filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            captured_at_utc TEXT,
            captured_at_local TEXT,
            timezone_hint TEXT,
            metadata_json TEXT DEFAULT '{}',
            ingested_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (upload_id) REFERENCES uploads(id)
        );

        CREATE TABLE IF NOT EXISTS session_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            attachment_id INTEGER NOT NULL,
            match_confidence TEXT NOT NULL DEFAULT 'unmatched',
            match_reason TEXT,
            assigned_by TEXT DEFAULT 'auto',
            assigned_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (attachment_id) REFERENCES attachments(id),
            UNIQUE(session_id, attachment_id)
        );

        CREATE TABLE IF NOT EXISTS generation_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'dispatched',
            session_id_filter TEXT,
            github_run_id TEXT,
            dispatched_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            result_json TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_section TEXT,
            annotation_type TEXT NOT NULL,
            content_json TEXT NOT NULL DEFAULT '{}',
            status TEXT DEFAULT 'active',
            created_by_role TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id);

        CREATE TABLE IF NOT EXISTS feedback_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            target_id TEXT,
            original_value TEXT,
            corrected_value TEXT,
            context_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback_signals(session_id);

        CREATE TABLE IF NOT EXISTS ai_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            review_type TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            findings_json TEXT NOT NULL DEFAULT '[]',
            findings_count INTEGER DEFAULT 0,
            accepted_count INTEGER DEFAULT 0,
            dismissed_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS model_eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            language TEXT DEFAULT 'zh',
            dataset_name TEXT DEFAULT 'default',
            session_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            summary_json TEXT DEFAULT '{}',
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            error_message TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS model_eval_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_run_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            metric_meta_json TEXT DEFAULT '{}',
            FOREIGN KEY (eval_run_id) REFERENCES model_eval_runs(id)
        );
        CREATE INDEX IF NOT EXISTS idx_eval_scores_run ON model_eval_scores(eval_run_id);

        CREATE TABLE IF NOT EXISTS model_language_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_pattern TEXT NOT NULL DEFAULT '*',
            enabled INTEGER DEFAULT 1,
            min_score REAL DEFAULT 0.0,
            warning_threshold REAL DEFAULT 0.6,
            block_threshold REAL DEFAULT 0.3,
            fallback_provider TEXT,
            fallback_model TEXT,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (created_by) REFERENCES users(id),
            UNIQUE(language, provider, model_pattern)
        );

        CREATE TABLE IF NOT EXISTS user_retrieval_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_data_json TEXT NOT NULL DEFAULT '{}',
            source TEXT DEFAULT 'generation',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_retrieval_user ON user_retrieval_items(user_id, item_type);
        CREATE INDEX IF NOT EXISTS idx_retrieval_key ON user_retrieval_items(user_id, item_key);

        CREATE TABLE IF NOT EXISTS user_feedback_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            original_json TEXT,
            corrected_json TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_memory_user ON user_feedback_memory(user_id, target_type);

        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_by INTEGER,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS fine_tune_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            base_model TEXT NOT NULL,
            adapter_name TEXT,
            training_records INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            config_json TEXT DEFAULT '{}',
            metrics_json TEXT DEFAULT '{}',
            output_path TEXT,
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            error_message TEXT,
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS fine_tune_training_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fine_tune_run_id INTEGER,
            user_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            system_prompt TEXT,
            user_content TEXT NOT NULL,
            assistant_content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (fine_tune_run_id) REFERENCES fine_tune_runs(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    # Migrations for existing databases
    if not _table_has_column(conn, "users", "status"):
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    if not _table_has_column(conn, "users", "last_login_at"):
        conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
    if not _table_has_column(conn, "sessions", "user_id"):
        conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        # Backfill user_id from parse_runs
        conn.execute("""
            UPDATE sessions SET user_id = (
                SELECT pr.user_id FROM parse_runs pr WHERE pr.run_id = sessions.run_id
            ) WHERE user_id IS NULL
        """)
    if not _table_has_column(conn, "users", "role"):
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'student'")
    if not _table_has_column(conn, "sessions", "teacher_message_count"):
        conn.execute("ALTER TABLE sessions ADD COLUMN teacher_message_count INTEGER DEFAULT 0")
    if not _table_has_column(conn, "sessions", "student_message_count"):
        conn.execute("ALTER TABLE sessions ADD COLUMN student_message_count INTEGER DEFAULT 0")
    if not _table_has_column(conn, "sessions", "is_archived"):
        conn.execute("ALTER TABLE sessions ADD COLUMN is_archived INTEGER DEFAULT 0")
    if not _table_has_column(conn, "users", "native_language"):
        conn.execute("ALTER TABLE users ADD COLUMN native_language TEXT")

    conn.commit()
    conn.close()


COMMON_WEAK_PASSWORDS = {
    "password",
    "password123",
    "123456789",
    "1234567890",
    "qwertyuiop",
    "adminpassword1",
    "letmein",
}


def validate_password_strength(password, email="", display_name=""):
    errors = []
    if not password:
        return ["Password is required"]
    if len(password) < 16:
        errors.append("Password must be at least 16 characters")
    if len(password) > 256:
        errors.append("Password must be 256 characters or fewer")

    lowered = password.casefold()
    if lowered in COMMON_WEAK_PASSWORDS:
        errors.append("Password is too common; use a password manager-generated password or a long unique passphrase")
    if password.isdigit():
        errors.append("Password cannot be only numbers")
    if len(set(password)) < 4:
        errors.append("Password is too repetitive")

    personal_tokens = set()
    for source in (email, display_name):
        for token in re.split(r"[^a-z0-9]+", source.casefold()):
            if len(token) >= 3:
                personal_tokens.add(token)
    for token in sorted(personal_tokens):
        if token in lowered:
            errors.append("Password cannot contain your email or display name")
            break

    return errors


def _password_hash(password):
    return generate_password_hash(password, method="scrypt")


def _load_user(conn, email):
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def _delete_user_learning_data(conn, user_id):
    """Delete DB records for a user's learning data.

    IMPORTANT: This only removes database rows.  The on-disk processed
    artifacts (sessions.json, etc.) in the ``processed/`` directory are
    intentionally preserved so data can be recovered if needed.
    """
    run_ids = [row["run_id"] for row in conn.execute(
        "SELECT run_id FROM parse_runs WHERE user_id = ?",
        (user_id,),
    ).fetchall()]

    conn.execute("DELETE FROM lesson_summaries WHERE user_id = ?", (user_id,))
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        conn.execute(f"DELETE FROM sessions WHERE run_id IN ({placeholders})", tuple(run_ids))
    conn.execute("DELETE FROM parse_runs WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM uploads WHERE user_id = ?", (user_id,))


def _normalize_backup_member(name):
    normalized = name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        raise ValueError("Backup contains an invalid file path")
    return normalized


def _read_backup_json(zip_file, name):
    try:
        return json.loads(zip_file.read(name).decode("utf-8"))
    except KeyError as exc:
        raise ValueError(f"Backup is missing {name}") from exc
    except Exception as exc:
        raise ValueError(f"Backup file {name} must be valid UTF-8 JSON") from exc


def _write_backup_member(destination_root, member_name, data):
    relative = member_name.split("/", 1)[1]
    destination = Path(destination_root) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def _build_backup_manifest(user, run, upload, sessions_payload, summaries):
    return {
        "schema_version": "lessonlens-backup.v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_user": {
            "email": user["email"],
            "display_name": user["display_name"],
        },
        "latest_run": {
            "run_id": run["run_id"],
            "completed_at": run["completed_at"],
            "session_count": run["session_count"],
            "message_count": run["message_count"],
            "lesson_content_count": run["lesson_content_count"],
            "upload": {
                "original_filename": upload["original_filename"] if upload else None,
                "stored_filename": upload["stored_filename"] if upload else None,
                "file_size": upload["file_size"] if upload else None,
                "line_count": upload["line_count"] if upload else None,
                "uploaded_at": upload["uploaded_at"] if upload else None,
            },
        },
        "session_count": len(sessions_payload.get("sessions", [])),
        "summary_count": len(summaries),
        "summaries": [
            {
                "session_id": row["session_id"],
                "provider": row["provider"],
                "model": row["model"],
                "created_at": row["created_at"],
            }
            for row in summaries
        ],
    }


def _build_backup_archive(conn, user):
    run = _load_latest_completed_run(conn, user["id"])
    if not run:
        raise ValueError("No parsed data to export")

    output_dir = Path(run["output_dir"])
    sessions_path = output_dir / "sessions.json"
    if not sessions_path.is_file():
        raise ValueError("Sessions artifact not found for latest run")

    sessions_payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (run["upload_id"],)).fetchone()
    summaries = conn.execute(
        "SELECT session_id, provider, model, lesson_data_json, created_at FROM lesson_summaries WHERE user_id = ? AND run_id = ? ORDER BY session_id ASC",
        (user["id"], run["run_id"]),
    ).fetchall()

    manifest = _build_backup_manifest(user, run, upload, sessions_payload, summaries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        for artifact_name in ("sessions.json", "parse_report.json", "diagnostics.txt", "normalized_messages.jsonl"):
            artifact_path = output_dir / artifact_name
            if artifact_path.is_file():
                archive.writestr(f"parse/{artifact_name}", artifact_path.read_bytes())

        if upload:
            raw_path = Path(app.config["UPLOAD_FOLDER"]) / upload["stored_filename"]
            if raw_path.is_file():
                backup_name = secure_filename(upload["original_filename"] or "line-export.txt") or "line-export.txt"
                archive.writestr(f"raw-exports/{backup_name}", raw_path.read_bytes())

        for summary in summaries:
            archive.writestr(
                f"summaries/{summary['session_id']}.json",
                json.dumps(json.loads(summary["lesson_data_json"]), ensure_ascii=False, indent=2),
            )

    buffer.seek(0)
    filename = f"lessonlens-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
    return buffer.getvalue(), filename, manifest


def _normalize_remote_base_url(url):
    cleaned = (url or "").strip().rstrip("/")
    parsed = urllib_parse.urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Remote URL must include http:// or https:// and a host")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Remote sync requires HTTPS unless you are targeting localhost")
    return cleaned


def _post_json(url, payload, headers=None, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            return response.getcode(), json.loads(response.read().decode("utf-8") or "{}")
    except urllib_error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(payload or "{}")
        except Exception:
            return exc.code, {"error": payload or f"Remote request failed with status {exc.code}"}


def _encode_multipart_form(fields, files):
    boundary = f"lessonlens-{uuid.uuid4().hex}"
    chunks = []

    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])

    for file_spec in files:
        mime_type = file_spec.get("content_type") or mimetypes.guess_type(file_spec["filename"])[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_spec["field_name"]}"; '
                f'filename="{file_spec["filename"]}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            file_spec["data"],
            b"\r\n",
        ])

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def _post_multipart(url, fields, files, headers=None, timeout=120):
    body, boundary = _encode_multipart_form(fields, files)
    req = urllib_request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            return response.getcode(), json.loads(response.read().decode("utf-8") or "{}")
    except urllib_error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(payload or "{}")
        except Exception:
            return exc.code, {"error": payload or f"Remote request failed with status {exc.code}"}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.route("/api/register", methods=["POST"])
@rate_limit(max_requests=3, window_seconds=300)
def register():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    display_name = data.get("display_name", "")
    invitation_token = data.get("invitation_token", "")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400
    password_errors = validate_password_strength(password, email=email, display_name=display_name)
    if password_errors:
        return jsonify({"error": password_errors[0], "errors": password_errors}), 400

    conn = get_db()
    try:
        # Validate invitation
        invite = conn.execute(
            "SELECT * FROM invitation_tokens WHERE token = ? AND email = ? AND used_at IS NULL",
            (invitation_token, email),
        ).fetchone()
        if not invite:
            return jsonify({"error": "Valid invitation required"}), 403
        if invite["expires_at"] < datetime.now(timezone.utc).isoformat():
            return jsonify({"error": "Invitation expired"}), 403

        # Check duplicate
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "Email already registered"}), 409

        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, status) VALUES (?, ?, ?, 'active')",
            (email, _password_hash(password), display_name),
        )
        conn.execute(
            "UPDATE invitation_tokens SET used_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), invite["id"]),
        )
        conn.commit()
        return jsonify({"message": "Registration successful"}), 201
    finally:
        conn.close()


@app.route("/api/login", methods=["POST"])
@rate_limit(max_requests=30, window_seconds=300)
def login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            status = user["status"] if "status" in user.keys() else "active"
            if status != "active":
                _log_security_event(conn, "login_blocked", user_id=user["id"], detail={"status": status})
                conn.commit()
                return jsonify({"error": "Account is not active", "status": status}), 403
            token = create_access_token(identity=email)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user["id"]))
            _track_event(conn, user["id"], "login", {})
            _log_security_event(conn, "login_success", user_id=user["id"])
            conn.commit()
            return jsonify({"access_token": token, "user": {
                "email": user["email"],
                "display_name": user["display_name"],
                "is_admin": bool(user["is_admin"]),
            }}), 200
        _log_security_event(conn, "login_failed", detail={"email": email})
        conn.commit()
        return jsonify({"error": "Invalid credentials"}), 401
    finally:
        conn.close()


@app.route("/api/change-password", methods=["POST"])
@jwt_required()
@rate_limit(max_requests=5, window_seconds=900)
def change_password():
    email = get_jwt_identity()
    data = request.get_json() or {}
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")
    confirm_password = data.get("confirm_password", "")

    if not current_password or not new_password or not confirm_password:
        return jsonify({"error": "Current password, new password, and confirmation are required"}), 400
    if new_password != confirm_password:
        return jsonify({"error": "New password confirmation does not match"}), 400
    if current_password == new_password:
        return jsonify({"error": "New password must be different from the current password"}), 400

    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not check_password_hash(user["password_hash"], current_password):
            return jsonify({"error": "Current password is incorrect"}), 401

        password_errors = validate_password_strength(
            new_password,
            email=user["email"],
            display_name=user["display_name"] or "",
        )
        if password_errors:
            return jsonify({"error": password_errors[0], "errors": password_errors}), 400

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_password_hash(new_password), user["id"]),
        )
        conn.commit()

        _track_event(conn, user["id"], "change_password", {})
        return jsonify({
            "message": "Password changed successfully",
            "password_requirements": "Use a password manager-generated password or a unique passphrase of at least 16 characters.",
        })
    finally:
        conn.close()


@app.route("/api/profile", methods=["GET"])
@jwt_required()
def profile():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        return jsonify({
            "email": user["email"],
            "display_name": user["display_name"],
            "is_admin": bool(user["is_admin"]),
            "status": user["status"] if "status" in user.keys() else "active",
            "native_language": user["native_language"] if "native_language" in user.keys() else None,
        })
    finally:
        conn.close()


@app.route("/api/profile", methods=["PUT"])
@jwt_required()
def update_profile():
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        updates = []
        params = []
        if "display_name" in data:
            dn = (data["display_name"] or "").strip()
            if not dn:
                return jsonify({"error": "display_name cannot be empty"}), 400
            updates.append("display_name = ?")
            params.append(dn)
        if "native_language" in data:
            updates.append("native_language = ?")
            params.append(data["native_language"])

        if not updates:
            return jsonify({"error": "No fields to update"}), 400

        params.append(user["id"])
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/backup/export", methods=["GET"])
@jwt_required()
def export_backup():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = _load_user(conn, email)
        if not user:
            return jsonify({"error": "User not found"}), 404
        backup_bytes, filename, _manifest = _build_backup_archive(conn, user)
        return send_file(
            io.BytesIO(backup_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    finally:
        conn.close()


@app.route("/api/backup/sync-remote", methods=["POST"])
@jwt_required()
def sync_backup_remote():
    email = get_jwt_identity()
    data = request.get_json() or {}

    remote_base_url = data.get("remote_base_url", "")
    remote_email = (data.get("remote_email", "") or "").strip().lower()
    remote_password = data.get("remote_password", "") or ""
    replace_existing = bool(data.get("replace_existing", False))

    if not remote_base_url or not remote_email or not remote_password:
        return jsonify({"error": "Remote URL, email, and password are required"}), 400

    try:
        remote_base = _normalize_remote_base_url(remote_base_url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    conn = get_db()
    try:
        user = _load_user(conn, email)
        if not user:
            return jsonify({"error": "User not found"}), 404

        backup_bytes, filename, manifest = _build_backup_archive(conn, user)

        login_status, login_payload = _post_json(
            f"{remote_base}/api/login",
            {"email": remote_email, "password": remote_password},
        )
        if login_status >= 400:
            return jsonify({
                "error": login_payload.get("error") or "Remote login failed",
                "remote_status": login_status,
            }), 502

        remote_token = login_payload.get("access_token")
        if not remote_token:
            return jsonify({"error": "Remote login did not return an access token"}), 502

        import_status, import_payload = _post_multipart(
            f"{remote_base}/api/backup/import",
            {"replace_existing": "true" if replace_existing else "false"},
            [{
                "field_name": "file",
                "filename": filename,
                "data": backup_bytes,
                "content_type": "application/zip",
            }],
            headers={"Authorization": f"Bearer {remote_token}"},
        )
        if import_status >= 400:
            return jsonify({
                "error": import_payload.get("error") or "Remote backup import failed",
                "remote_status": import_status,
            }), 502

        _track_event(conn, user["id"], "sync_remote_backup", {
            "remote_base_url": remote_base,
            "replace_existing": replace_existing,
            "summary_count": len(manifest.get("summaries", [])),
            "session_count": manifest.get("latest_run", {}).get("session_count"),
        })

        return jsonify({
            "message": "Remote sync completed successfully",
            "remote_base_url": remote_base,
            "session_count": import_payload.get("session_count", manifest.get("latest_run", {}).get("session_count", 0)),
            "summary_count": import_payload.get("summary_count", len(manifest.get("summaries", []))),
            "replace_existing": replace_existing,
        }), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


def _get_existing_session_ids(conn, user_id):
    """Return a set of session_id strings the user already has."""
    rows = conn.execute(
        "SELECT DISTINCT s.session_id FROM sessions s"
        " JOIN parse_runs pr ON s.run_id = pr.run_id"
        " WHERE pr.user_id = ?",
        (user_id,),
    ).fetchall()
    return {row["session_id"] for row in rows}


def _get_existing_summary_session_ids(conn, user_id):
    """Return a set of session_id strings that already have a summary."""
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM lesson_summaries WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return {row["session_id"] for row in rows}


def _validate_backup_zip(raw_zip):
    """Open and validate a backup zip, returning (archive, manifest, sessions_payload, sessions, parse_members, summary_payloads, raw_export_member)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_zip))
    except zipfile.BadZipFile:
        return None, "Backup file must be a valid .zip archive"

    try:
        manifest = _read_backup_json(archive, "manifest.json")
    except ValueError as exc:
        archive.close()
        return None, str(exc)

    if manifest.get("schema_version") != "lessonlens-backup.v1":
        archive.close()
        return None, "Unsupported backup schema"

    try:
        sessions_payload = _read_backup_json(archive, "parse/sessions.json")
    except ValueError as exc:
        archive.close()
        return None, str(exc)

    sessions = sessions_payload.get("sessions") or []
    if not isinstance(sessions, list) or not sessions:
        archive.close()
        return None, "Backup does not contain any sessions"

    parse_members = []
    summary_payloads = {}
    raw_export_member = None
    for name in archive.namelist():
        normalized = _normalize_backup_member(name)
        if normalized.startswith("parse/") and not normalized.endswith("/"):
            parse_members.append(normalized)
        elif normalized.startswith("summaries/") and normalized.endswith(".json"):
            sid = Path(normalized).stem
            summary_payloads[sid] = json.loads(archive.read(name).decode("utf-8"))
        elif normalized.startswith("raw-exports/") and not normalized.endswith("/") and raw_export_member is None:
            raw_export_member = normalized

    return (archive, manifest, sessions_payload, sessions, parse_members, summary_payloads, raw_export_member), None


@app.route("/api/backup/import/preview", methods=["POST"])
@jwt_required()
def preview_backup_import():
    """Analyze a backup zip against existing data and return what would be imported."""
    email = get_jwt_identity()

    if "file" not in request.files:
        return jsonify({"error": "No backup file provided"}), 400
    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"error": "No backup file selected"}), 400

    raw_zip = uploaded.read()
    result = _validate_backup_zip(raw_zip)
    parsed, error = result
    if error:
        return jsonify({"error": error}), 400

    archive, manifest, _sessions_payload, sessions, _parse_members, summary_payloads, _raw_export_member = parsed

    archive.close()

    conn = get_db()
    try:
        user = _load_user(conn, email)
        if not user:
            return jsonify({"error": "User not found"}), 404

        existing_session_ids = _get_existing_session_ids(conn, user["id"])
        existing_summary_ids = _get_existing_summary_session_ids(conn, user["id"])

        incoming_sessions = [s for s in sessions if s.get("message_count", 0) > 0]
        incoming_session_ids = {s["session_id"] for s in incoming_sessions}
        new_session_ids = incoming_session_ids - existing_session_ids
        skipped_session_ids = incoming_session_ids & existing_session_ids

        incoming_summary_ids = set(summary_payloads.keys())
        new_summary_ids = incoming_summary_ids - existing_summary_ids
        skipped_summary_ids = incoming_summary_ids & existing_summary_ids

        return jsonify({
            "incoming_session_count": len(incoming_sessions),
            "incoming_summary_count": len(summary_payloads),
            "new_session_count": len(new_session_ids),
            "new_summary_count": len(new_summary_ids),
            "skipped_session_count": len(skipped_session_ids),
            "skipped_summary_count": len(skipped_summary_ids),
            "existing_session_count": len(existing_session_ids),
            "existing_summary_count": len(existing_summary_ids),
            "new_session_ids": sorted(new_session_ids),
            "skipped_session_ids": sorted(skipped_session_ids),
        }), 200
    finally:
        conn.close()


@app.route("/api/backup/import", methods=["POST"])
@jwt_required()
def import_backup():
    email = get_jwt_identity()
    replace_existing = request.form.get("replace_existing", "false").lower() not in {"0", "false", "no"}

    if "file" not in request.files:
        return jsonify({"error": "No backup file provided"}), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"error": "No backup file selected"}), 400

    raw_zip = uploaded.read()
    result = _validate_backup_zip(raw_zip)
    parsed, error = result
    if error:
        return jsonify({"error": error}), 400

    archive, manifest, sessions_payload, sessions, parse_members, summary_payloads, raw_export_member = parsed

    conn = get_db()
    try:
        user = _load_user(conn, email)
        if not user:
            archive.close()
            return jsonify({"error": "User not found"}), 404

        with archive:
            if replace_existing:
                _delete_user_learning_data(conn, user["id"])
                conn.commit()
                existing_session_ids = set()
                existing_summary_ids = set()
            else:
                existing_session_ids = _get_existing_session_ids(conn, user["id"])
                existing_summary_ids = _get_existing_summary_session_ids(conn, user["id"])

            # Filter sessions to only truly new ones in merge mode
            candidate_sessions = [s for s in sessions if s.get("message_count", 0) > 0]
            if replace_existing:
                new_sessions = candidate_sessions
            else:
                new_sessions = [s for s in candidate_sessions if s["session_id"] not in existing_session_ids]

            skipped_session_count = len(candidate_sessions) - len(new_sessions)

            if not new_sessions and not replace_existing:
                # Check if there are at least new summaries to import
                new_summary_ids = set(summary_payloads.keys()) - existing_summary_ids
                if not new_summary_ids:
                    return jsonify({
                        "message": "Nothing new to import — all sessions and summaries already exist.",
                        "session_count": 0,
                        "summary_count": 0,
                        "skipped_session_count": skipped_session_count,
                        "skipped_summary_count": len(summary_payloads),
                        "replace_existing": False,
                    }), 200

            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            os.makedirs(app.config["PROCESSED_FOLDER"], exist_ok=True)
            os.makedirs(app.config["SUMMARIES_FOLDER"], exist_ok=True)

            if raw_export_member:
                raw_export_bytes = archive.read(raw_export_member)
                original_filename = Path(raw_export_member).name
            else:
                raw_export_bytes = b""
                original_filename = "imported-backup.txt"

            stored_filename = f"{uuid.uuid4()}.txt"
            upload_path = Path(app.config["UPLOAD_FOLDER"]) / stored_filename
            upload_path.write_bytes(raw_export_bytes)
            file_hash = hashlib.sha256(raw_export_bytes).hexdigest()
            line_count = len(raw_export_bytes.decode("utf-8", errors="replace").splitlines())
            file_size = len(raw_export_bytes)

            upload_row = conn.execute(
                "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], original_filename, stored_filename, file_hash, file_size, line_count),
            )
            upload_id = upload_row.lastrowid

            import_run_id = datetime.now(timezone.utc).strftime("imported-%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}"
            import_output_dir = Path(app.config["PROCESSED_FOLDER"]) / import_run_id
            import_output_dir.mkdir(parents=True, exist_ok=True)

            for member_name in parse_members:
                _write_backup_member(import_output_dir, member_name, archive.read(member_name))

            if not (import_output_dir / "sessions.json").is_file():
                (import_output_dir / "sessions.json").write_text(
                    json.dumps(sessions_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            session_count = 0
            total_messages = 0
            total_lesson_messages = 0
            for session in new_sessions:
                session_count += 1
                total_messages += session.get("message_count", 0)
                total_lesson_messages += session.get("lesson_content_count", 0)

            completed_at = manifest.get("latest_run", {}).get("completed_at") or datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO parse_runs
                   (run_id, upload_id, user_id, status, session_count, message_count,
                    lesson_content_count, output_dir, completed_at)
                   VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
                (
                    import_run_id,
                    upload_id,
                    user["id"],
                    session_count,
                    total_messages,
                    total_lesson_messages,
                    str(import_output_dir),
                    completed_at,
                ),
            )

            for session in new_sessions:
                conn.execute(
                    """INSERT INTO sessions
                       (run_id, user_id, session_id, date, start_time, end_time,
                        message_count, lesson_content_count, teacher_message_count, student_message_count,
                        boundary_confidence, topics_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        import_run_id,
                        user["id"],
                        session["session_id"],
                        session["date"],
                        session.get("start_time"),
                        session.get("end_time"),
                        session.get("message_count", 0),
                        session.get("lesson_content_count", 0),
                        session.get("teacher_message_count", 0),
                        session.get("student_message_count", 0),
                        session.get("boundary_confidence", "medium"),
                        json.dumps(session.get("topics", []), ensure_ascii=False),
                    ),
                )
            conn.commit()

            import sys as _sys
            scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
            if scripts_dir not in _sys.path:
                _sys.path.insert(0, scripts_dir)
            from install_manual_summary import install_summary_data

            summary_metadata = {
                item["session_id"]: item
                for item in manifest.get("summaries", [])
                if item.get("session_id")
            }

            imported_summaries = 0
            skipped_summary_count = 0
            for session_id, lesson_data in summary_payloads.items():
                if lesson_data.get("schema_version") != "lesson-data.v1":
                    continue
                # In merge mode, skip summaries for sessions that already have one
                if not replace_existing and session_id in existing_summary_ids:
                    skipped_summary_count += 1
                    continue
                # For new sessions: check they were actually inserted in this run
                # For existing sessions without summaries: find any matching session
                session_row = conn.execute(
                    "SELECT 1 FROM sessions WHERE run_id = ? AND session_id = ?",
                    (import_run_id, session_id),
                ).fetchone()
                if not session_row and not replace_existing:
                    # Session already existed from a prior run — attach summary to that run
                    session_row = conn.execute(
                        "SELECT s.run_id FROM sessions s"
                        " JOIN parse_runs pr ON s.run_id = pr.run_id"
                        " WHERE pr.user_id = ? AND s.session_id = ?"
                        " LIMIT 1",
                        (user["id"], session_id),
                    ).fetchone()
                    if not session_row:
                        continue
                    target_run_id = session_row["run_id"]
                else:
                    if not session_row:
                        continue
                    target_run_id = import_run_id

                summary_dir = Path(app.config["SUMMARIES_FOLDER"]) / target_run_id / session_id
                summary_dir.mkdir(parents=True, exist_ok=True)
                lesson_path = summary_dir / "lesson-data.json"
                lesson_path.write_text(json.dumps(lesson_data, ensure_ascii=False, indent=2), encoding="utf-8")

                meta = summary_metadata.get(session_id, {})
                install_summary_data(
                    lesson_data,
                    lesson_path,
                    session_id,
                    provider=meta.get("provider") or "imported-backup",
                    model=meta.get("model") or "external-agent",
                    run_id=target_run_id,
                    user_id=user["id"],
                )
                imported_summaries += 1

            _track_event(conn, user["id"], "import_backup", {
                "session_count": session_count,
                "summary_count": imported_summaries,
                "skipped_session_count": skipped_session_count,
                "skipped_summary_count": skipped_summary_count,
                "replace_existing": replace_existing,
            })

            return jsonify({
                "message": "Backup imported successfully",
                "session_count": session_count,
                "summary_count": imported_summaries,
                "skipped_session_count": skipped_session_count,
                "skipped_summary_count": skipped_summary_count,
                "replace_existing": replace_existing,
            }), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Upload routes
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS = {".txt", ""}  # .txt and extensionless


def compute_file_hash(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@app.route("/api/upload", methods=["POST"])
@jwt_required()
def upload_file():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        # Validate extension
        _, ext = os.path.splitext(file.filename)
        if ext.lower() not in ALLOWED_EXTENSIONS:
            return jsonify({"error": "Only .txt files or extensionless files allowed"}), 400

        # Save with UUID name
        original_name = secure_filename(file.filename) or "unnamed-export"
        stored_name = f"{uuid.uuid4()}.txt"
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)
        file.save(filepath)

        # Hash-based dedup
        file_hash = compute_file_hash(filepath)
        existing = conn.execute(
            "SELECT id FROM uploads WHERE user_id = ? AND file_hash = ?",
            (user["id"], file_hash),
        ).fetchone()
        if existing:
            os.remove(filepath)
            return jsonify({"upload_id": existing["id"], "duplicate": True}), 200

        # Count lines
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)

        file_size = os.path.getsize(filepath)
        cursor = conn.execute(
            "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], original_name, stored_name, file_hash, file_size, line_count),
        )
        conn.commit()
        upload_id = cursor.lastrowid

        _track_event(conn, user["id"], "upload", {"upload_id": upload_id, "lines": line_count})

        return jsonify({
            "upload_id": upload_id,
            "filename": original_name,
            "line_count": line_count,
            "file_size": file_size,
            "duplicate": False,
        }), 201
    finally:
        conn.close()


@app.route("/api/uploads", methods=["GET"])
@jwt_required()
def list_uploads():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404
        rows = conn.execute(
            "SELECT id, original_filename, file_size, line_count, uploaded_at FROM uploads WHERE user_id = ? ORDER BY uploaded_at DESC",
            (user["id"],),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parse routes
# ---------------------------------------------------------------------------
@app.route("/api/parse/<int:upload_id>", methods=["POST"])
@jwt_required()
def parse_upload(upload_id):
    """Parse an uploaded file into sessions."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        upload = conn.execute(
            "SELECT * FROM uploads WHERE id = ? AND user_id = ?",
            (upload_id, user["id"]),
        ).fetchone()
        if not upload:
            return jsonify({"error": "Upload not found"}), 404

        # Import the parser
        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from parse_line_export import load_config, parse_lines, write_outputs
        from extract_transcript import extract

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{upload_id}"

        force = request.args.get("force", "").lower() in ("1", "true")

        # Check for incremental: skip already-parsed hashes
        existing_run = conn.execute(
            "SELECT run_id FROM parse_runs WHERE upload_id = ? AND status = 'completed'",
            (upload_id,),
        ).fetchone()
        if existing_run and not force:
            return jsonify({
                "message": "Already parsed",
                "run_id": existing_run["run_id"],
                "duplicate": True,
            }), 200

        # Mark old run as superseded (not deleted) so data is recoverable
        if existing_run and force:
            conn.execute(
                "UPDATE parse_runs SET status = 'superseded' WHERE run_id = ?",
                (existing_run["run_id"],),
            )
            conn.commit()

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], upload["stored_filename"])
        config = load_config()
        source_meta = extract(filepath)
        lines = source_meta.pop("lines")

        result = parse_lines(lines, source_meta, config)
        output_dir = os.path.join(app.config["PROCESSED_FOLDER"], run_id)
        write_outputs(result, source_meta, config, run_id, output_dir)

        stats = result["stats"]

        # Insert parse_run record
        conn.execute(
            """INSERT INTO parse_runs
               (run_id, upload_id, user_id, status, session_count, message_count,
                lesson_content_count, output_dir, completed_at)
               VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
            (run_id, upload_id, user["id"], stats["total_sessions"],
             stats["total_messages"], stats["lesson_content_messages"],
             output_dir, datetime.now(timezone.utc).isoformat()),
        )

        # Insert session records (skip empty sessions)
        inserted_sessions = 0
        for sess in result["sessions"]:
            if sess["message_count"] == 0:
                continue
            conn.execute(
                """INSERT INTO sessions
                   (run_id, user_id, session_id, date, start_time, end_time,
                    message_count, lesson_content_count, teacher_message_count, student_message_count,
                    boundary_confidence, topics_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, user["id"], sess["session_id"], sess["date"],
                 sess["start_time"], sess["end_time"],
                 sess["message_count"], sess["lesson_content_count"],
                 sess.get("teacher_message_count", 0), sess.get("student_message_count", 0),
                 sess["boundary_confidence"], json.dumps([])),
            )
            inserted_sessions += 1

        # Auto-archive sessions with no teacher messages
        conn.execute(
            """UPDATE sessions SET is_archived = 1
               WHERE run_id = ? AND user_id = ? AND teacher_message_count = 0""",
            (run_id, user["id"]),
        )
        conn.commit()

        _track_event(conn, user["id"], "parse", {
            "run_id": run_id, "sessions": stats["total_sessions"],
            "messages": stats["total_messages"],
        })

        return jsonify({
            "run_id": run_id,
            "session_count": inserted_sessions,
            "message_count": stats["total_messages"],
            "lesson_content_count": stats["lesson_content_messages"],
            "warnings": len(result["warnings"]),
        }), 201
    finally:
        conn.close()


def _merge_sessions_json(existing_path, new_sessions):
    """Merge new parsed sessions into an existing sessions.json file.

    Adds sessions whose session_id doesn't already exist.  Returns
    (merged_session_list, new_session_ids_set).
    """
    with open(existing_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    existing_sessions = data.get("sessions", [])
    existing_ids = {s["session_id"] for s in existing_sessions}

    added = []
    for sess in new_sessions:
        if sess["session_id"] not in existing_ids:
            added.append(sess)

    if added:
        data["sessions"] = existing_sessions + added
        old_stats = data.get("stats", {})
        data["stats"] = {
            "total_sessions": old_stats.get("total_sessions", 0) + len(added),
            "total_messages": old_stats.get("total_messages", 0)
            + sum(s.get("message_count", 0) for s in added),
            "lesson_content_messages": old_stats.get("lesson_content_messages", 0)
            + sum(s.get("lesson_content_count", 0) for s in added),
        }
        with open(existing_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    new_ids = {s["session_id"] for s in added}
    return data["sessions"], new_ids


@app.route("/api/sync", methods=["POST"])
@jwt_required()
def sync_file():
    """Upload + parse in one step.  Incremental: merges new sessions into
    the user's existing canonical run so that previously generated summaries
    are never deleted."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        _, ext = os.path.splitext(file.filename)
        if ext.lower() not in ALLOWED_EXTENSIONS:
            return jsonify({"error": "Only .txt files allowed"}), 400

        original_name = secure_filename(file.filename) or "unnamed-export"
        stored_name = f"{uuid.uuid4()}.txt"
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)
        file.save(filepath)

        file_hash = compute_file_hash(filepath)
        existing = conn.execute(
            "SELECT id FROM uploads WHERE user_id = ? AND file_hash = ?",
            (user["id"], file_hash),
        ).fetchone()

        is_duplicate_file = existing is not None
        if is_duplicate_file:
            os.remove(filepath)
            upload_id = existing["id"]

            # If this exact file was already parsed, return the existing
            # run's stats as a no-op instead of deleting anything.
            dup_run = conn.execute(
                "SELECT run_id FROM parse_runs WHERE upload_id = ? AND status = 'completed'",
                (upload_id,),
            ).fetchone()
            if dup_run:
                total_sessions = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM sessions WHERE run_id = ? AND user_id = ?",
                    (dup_run["run_id"], user["id"]),
                ).fetchone()["cnt"]
                return jsonify({
                    "run_id": dup_run["run_id"],
                    "session_count": total_sessions,
                    "new_session_count": 0,
                    "message_count": 0,
                    "lesson_content_count": 0,
                    "warnings": 0,
                    "duplicate": True,
                }), 200
        else:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
            file_size = os.path.getsize(filepath)
            cursor = conn.execute(
                "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], original_name, stored_name, file_hash, file_size, line_count),
            )
            conn.commit()
            upload_id = cursor.lastrowid

        # Parse the new file
        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from parse_line_export import load_config, parse_lines, write_outputs
        from extract_transcript import extract

        upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        filepath_parse = os.path.join(app.config["UPLOAD_FOLDER"], upload["stored_filename"])
        config = load_config()
        source_meta = extract(filepath_parse)
        lines = source_meta.pop("lines")
        result = parse_lines(lines, source_meta, config)

        # Check if user has an existing canonical run to merge into
        canonical_run = _load_latest_completed_run(conn, user["id"])

        if canonical_run:
            # Merge new sessions into the existing run's sessions.json
            existing_sessions_path = os.path.join(canonical_run["output_dir"], "sessions.json")
            if os.path.isfile(existing_sessions_path):
                _all_sessions, new_ids = _merge_sessions_json(
                    existing_sessions_path, result["sessions"],
                )
                run_id = canonical_run["run_id"]

                # Also write the new parse artifacts to a sub-directory for reference
                temp_output = os.path.join(app.config["PROCESSED_FOLDER"], f"merge_{upload_id}")
                write_outputs(result, source_meta, config, f"merge_{upload_id}", temp_output)

                # Insert only genuinely new sessions into DB
                inserted = 0
                for sess in result["sessions"]:
                    if sess["session_id"] not in new_ids:
                        continue
                    if sess["message_count"] == 0:
                        continue
                    conn.execute(
                        """INSERT INTO sessions
                           (run_id, user_id, session_id, date, start_time, end_time,
                            message_count, lesson_content_count, teacher_message_count, student_message_count,
                            boundary_confidence, topics_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, user["id"], sess["session_id"], sess["date"],
                         sess["start_time"], sess["end_time"],
                         sess["message_count"], sess["lesson_content_count"],
                         sess.get("teacher_message_count", 0), sess.get("student_message_count", 0),
                         sess["boundary_confidence"], json.dumps([])),
                    )
                    inserted += 1

                # Update canonical run stats
                if inserted:
                    conn.execute(
                        """UPDATE parse_runs SET
                              session_count = session_count + ?,
                              message_count = message_count + ?,
                              lesson_content_count = lesson_content_count + ?
                           WHERE run_id = ?""",
                        (inserted,
                         sum(s["message_count"] for s in result["sessions"] if s["session_id"] in new_ids),
                         sum(s["lesson_content_count"] for s in result["sessions"] if s["session_id"] in new_ids),
                         run_id),
                    )
                conn.commit()

                total_sessions = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM sessions WHERE run_id = ? AND user_id = ?",
                    (run_id, user["id"]),
                ).fetchone()["cnt"]

                _track_event(conn, user["id"], "sync", {
                    "run_id": run_id, "new_sessions": inserted,
                    "total_sessions": total_sessions, "merged": True,
                })

                return jsonify({
                    "run_id": run_id,
                    "session_count": total_sessions,
                    "new_session_count": inserted,
                    "message_count": result["stats"]["total_messages"],
                    "lesson_content_count": result["stats"]["lesson_content_messages"],
                    "warnings": len(result["warnings"]),
                }), 201

        # No existing run — create a fresh one (first-time sync)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{upload_id}"
        output_dir = os.path.join(app.config["PROCESSED_FOLDER"], run_id)
        write_outputs(result, source_meta, config, run_id, output_dir)

        stats = result["stats"]
        conn.execute(
            """INSERT INTO parse_runs
               (run_id, upload_id, user_id, status, session_count, message_count,
                lesson_content_count, output_dir, completed_at)
               VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
            (run_id, upload_id, user["id"], stats["total_sessions"],
             stats["total_messages"], stats["lesson_content_messages"],
             output_dir, datetime.now(timezone.utc).isoformat()),
        )

        inserted = 0
        for sess in result["sessions"]:
            if sess["message_count"] == 0:
                continue
            conn.execute(
                """INSERT INTO sessions
                   (run_id, user_id, session_id, date, start_time, end_time,
                    message_count, lesson_content_count, teacher_message_count, student_message_count,
                    boundary_confidence, topics_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, user["id"], sess["session_id"], sess["date"],
                 sess["start_time"], sess["end_time"],
                 sess["message_count"], sess["lesson_content_count"],
                 sess.get("teacher_message_count", 0), sess.get("student_message_count", 0),
                 sess["boundary_confidence"], json.dumps([])),
            )
            inserted += 1
        conn.commit()

        _track_event(conn, user["id"], "sync", {
            "run_id": run_id, "sessions": inserted,
            "messages": stats["total_messages"],
        })

        return jsonify({
            "run_id": run_id,
            "session_count": inserted,
            "new_session_count": inserted,
            "message_count": stats["total_messages"],
            "lesson_content_count": stats["lesson_content_messages"],
            "warnings": len(result["warnings"]),
        }), 201
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sessions routes
# ---------------------------------------------------------------------------
@app.route("/api/sessions", methods=["GET"])
@jwt_required()
def list_sessions():
    """List all parsed sessions, optionally filtered by date or topic."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        # Get latest run for this user
        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify([])

        try:
            sessions_by_id = _load_sessions_payload(run)
        except FileNotFoundError:
            sessions_by_id = {}

        rows = conn.execute(
            """SELECT s.*,
                      EXISTS(
                          SELECT 1
                          FROM lesson_summaries ls
                          WHERE ls.session_id = s.session_id
                            AND ls.run_id = s.run_id
                      ) AS has_summary
               FROM sessions s
               WHERE s.run_id = ? AND s.message_count > 0
               ORDER BY s.date DESC, s.start_time DESC""",
            (run["run_id"],),
        ).fetchall()

        # Pre-load stale session IDs (sessions needing re-summarization)
        stale_set = set()
        # 1. Active correction/reclassify annotations
        stale_rows = conn.execute(
            """SELECT DISTINCT session_id FROM annotations
               WHERE user_id = ? AND status = 'active'
                 AND annotation_type IN ('correction', 'reclassify')""",
            (user["id"],),
        ).fetchall()
        for sr in stale_rows:
            stale_set.add(sr["session_id"])
        # 2. Accepted AI review findings newer than latest summary
        stale_rows2 = conn.execute(
            """SELECT DISTINCT ar.session_id FROM ai_reviews ar
               WHERE ar.user_id = ? AND ar.accepted_count > 0
                 AND ar.created_at > COALESCE(
                     (SELECT MAX(ls.created_at) FROM lesson_summaries ls
                      WHERE ls.session_id = ar.session_id AND ls.user_id = ar.user_id),
                     '1970-01-01')""",
            (user["id"],),
        ).fetchall()
        for sr in stale_rows2:
            stale_set.add(sr["session_id"])

        # Determine min content threshold for "summarizable"
        min_lc = 3

        sessions = []
        for r in rows:
            session_payload = sessions_by_id.get(r["session_id"], {})
            shared_links = _extract_session_links(session_payload)
            if session_payload and not (session_payload.get("message_count", 0) >= 3 or shared_links):
                continue
            has_summary = bool(r["has_summary"])
            summarizable = r["lesson_content_count"] >= min_lc
            is_archived = bool(r["is_archived"]) if "is_archived" in r.keys() else False
            sessions.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "date": r["date"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "message_count": r["message_count"],
                "lesson_content_count": r["lesson_content_count"],
                "teacher_message_count": r["teacher_message_count"] if "teacher_message_count" in r.keys() else 0,
                "student_message_count": r["student_message_count"] if "student_message_count" in r.keys() else 0,
                "is_archived": is_archived,
                "boundary_confidence": r["boundary_confidence"],
                "topics": json.loads(r["topics_json"] or "[]"),
                "has_summary": has_summary,
                "needs_summary": summarizable and not has_summary,
                "summary_stale": has_summary and r["session_id"] in stale_set,
                "shared_links": shared_links,
            })
        return jsonify(sessions)
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>", methods=["GET"])
@jwt_required()
def get_session(session_id):
    """Get full session data including messages."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"error": "No parsed data"}), 404

        try:
            sessions_by_id = _load_sessions_payload(run)
        except FileNotFoundError:
            return jsonify({"error": "Sessions file not found"}), 404

        session = sessions_by_id.get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        session["shared_links"] = _extract_session_links(session)

        return jsonify(session)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Summary routes
# ---------------------------------------------------------------------------
@app.route("/api/sessions/<session_id>/summary", methods=["GET"])
@jwt_required()
def get_summary(session_id):
    """Get lesson summary for a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        summary = conn.execute(
            "SELECT * FROM lesson_summaries WHERE session_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id, user["id"]),
        ).fetchone()
        if not summary:
            return jsonify({"error": "No summary yet"}), 404

        lesson_data = json.loads(summary["lesson_data_json"])
        _track_event(conn, user["id"], "view_summary", {"session_id": session_id})
        return jsonify(lesson_data)
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/summary/import", methods=["POST"])
@jwt_required()
def import_summary(session_id):
    """Import an externally authored lesson-data.json for a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"error": "No parsed data"}), 404

        session_row = conn.execute(
            "SELECT * FROM sessions WHERE run_id = ? AND session_id = ?",
            (run["run_id"], session_id),
        ).fetchone()
        if not session_row:
            return jsonify({"error": "Session not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        uploaded = request.files["file"]
        if not uploaded.filename:
            return jsonify({"error": "No file selected"}), 400

        raw_bytes = uploaded.read()
        try:
            lesson_data = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return jsonify({"error": "Uploaded file must be valid UTF-8 JSON"}), 400

        if lesson_data.get("schema_version") != "lesson-data.v1":
            return jsonify({"error": "Uploaded JSON must match lesson-data.v1"}), 400
        if lesson_data.get("lesson_date") != session_id:
            return jsonify({"error": f"lesson_date must match session_id ({session_id})"}), 400

        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from install_manual_summary import install_summary_data

        import_run_id = datetime.now(timezone.utc).strftime("imported-%Y%m%d-%H%M%S")
        out_dir = os.path.join(os.path.dirname(__file__), "..", "summaries", import_run_id, session_id)
        os.makedirs(out_dir, exist_ok=True)
        lesson_path = Path(os.path.join(out_dir, "lesson-data.json"))
        lesson_path.write_text(json.dumps(lesson_data, ensure_ascii=False, indent=2), encoding="utf-8")

        provider = request.form.get("provider", "uploaded-summary")
        model = request.form.get("model", "external-agent")
        install_summary_data(
            lesson_data,
            lesson_path,
            session_id,
            provider=provider,
            model=model,
            run_id=run["run_id"],
            user_id=user["id"],
        )

        _track_event(conn, user["id"], "import_summary", {
            "session_id": session_id,
            "provider": provider,
            "model": model,
        })
        return jsonify(lesson_data), 201
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/generate", methods=["POST"])
@jwt_required()
def generate_summary(session_id):
    """Generate a lesson summary via LLM for a session."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    provider = data.get("provider")  # optional override
    model = data.get("model")  # optional override

    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"error": "No parsed data"}), 404

        session_row = conn.execute(
            "SELECT * FROM sessions WHERE run_id = ? AND session_id = ?",
            (run["run_id"], session_id),
        ).fetchone()
        if not session_row:
            return jsonify({"error": "Session not found"}), 404

        try:
            sessions_by_id = _load_sessions_payload(run)
        except FileNotFoundError:
            return jsonify({"error": "Sessions file not found"}), 404

        session_data = sessions_by_id.get(session_id)
        if not session_data:
            return jsonify({"error": "Session data not found"}), 404

        lesson_data, use_provider, use_model, p_action, p_msg = _generate_summary_for_session(
            conn,
            user,
            run,
            session_row,
            session_data,
            provider_override=provider,
            model_override=model,
        )
        conn.commit()

        _track_event(conn, user["id"], "generate_summary", {
            "session_id": session_id, "provider": use_provider, "model": use_model,
        })

        response = lesson_data
        if p_action == "warn" and p_msg:
            response = dict(lesson_data)
            response["_policy_warning"] = p_msg

        return jsonify(response), 201

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/summaries/generate", methods=["POST"])
@jwt_required()
def generate_all_summaries():
    """Generate summaries for all missing parsed lesson sessions."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    provider = data.get("provider")
    model = data.get("model")
    overwrite = bool(data.get("overwrite", False))
    limit = data.get("limit")
    min_lesson_content_count = data.get("min_lesson_content_count", 3)

    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"error": "No parsed data"}), 404

        try:
            sessions_by_id = _load_sessions_payload(run)
        except FileNotFoundError:
            return jsonify({"error": "Sessions file not found"}), 404

        rows = conn.execute(
            """SELECT s.*
               FROM sessions s
               WHERE s.run_id = ?
                 AND s.message_count > 0
                 AND s.lesson_content_count >= ?
                 AND (
                     ? = 1 OR NOT EXISTS (
                         SELECT 1
                         FROM lesson_summaries ls
                         WHERE ls.session_id = s.session_id
                           AND ls.run_id = s.run_id
                     )
                 )
               ORDER BY s.date ASC, s.start_time ASC""",
            (run["run_id"], min_lesson_content_count, 1 if overwrite else 0),
        ).fetchall()

        if limit is not None:
            try:
                limit = max(1, int(limit))
            except (TypeError, ValueError):
                return jsonify({"error": "limit must be a positive integer"}), 400
            rows = rows[:limit]

        process_session, gen_config, use_provider, use_model, temperature = _load_generator_config(
            provider,
            model,
        )
        del process_session, gen_config, temperature

        credential_error = _validate_provider_credentials(use_provider)
        if credential_error:
            return jsonify({"error": credential_error}), 400

        generated = []
        skipped = []
        failures = []

        for session_row in rows:
            session_data = sessions_by_id.get(session_row["session_id"])
            if not session_data:
                failures.append({
                    "session_id": session_row["session_id"],
                    "error": "Session data not found in sessions.json",
                })
                continue

            try:
                lesson_data, final_provider, final_model, _, _ = _generate_summary_for_session(
                    conn,
                    user,
                    run,
                    session_row,
                    session_data,
                    provider_override=provider,
                    model_override=model,
                )
                conn.commit()
                generated.append({
                    "session_id": session_row["session_id"],
                    "title": lesson_data.get("title"),
                    "provider": final_provider,
                    "model": final_model,
                })
            except Exception as exc:
                conn.rollback()
                failures.append({
                    "session_id": session_row["session_id"],
                    "error": str(exc),
                })

        if not overwrite:
            summarized_rows = conn.execute(
                """SELECT s.session_id
                   FROM sessions s
                   WHERE s.run_id = ?
                     AND s.message_count > 0
                     AND s.lesson_content_count >= ?
                     AND EXISTS (
                         SELECT 1
                         FROM lesson_summaries ls
                         WHERE ls.session_id = s.session_id
                           AND ls.run_id = s.run_id
                     )
                   ORDER BY s.date ASC, s.start_time ASC""",
                (run["run_id"], min_lesson_content_count),
            ).fetchall()
            generated_ids = {item["session_id"] for item in generated}
            skipped = [
                row["session_id"]
                for row in summarized_rows
                if row["session_id"] not in generated_ids
            ]

        _track_event(conn, user["id"], "bulk_generate_summaries", {
            "provider": provider or use_provider,
            "model": model or use_model,
            "generated_count": len(generated),
            "failed_count": len(failures),
            "overwrite": overwrite,
            "min_lesson_content_count": min_lesson_content_count,
        })

        return jsonify({
            "provider": provider or use_provider,
            "model": model or use_model,
            "overwrite": overwrite,
            "min_lesson_content_count": min_lesson_content_count,
            "generated_count": len(generated),
            "failed_count": len(failures),
            "skipped_existing_count": len(skipped),
            "generated": generated,
            "failures": failures,
        }), 200
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evaluation Harness routes (Phase 2)
# ---------------------------------------------------------------------------

@app.route("/api/eval/runs", methods=["GET"])
@jwt_required()
def list_eval_runs():
    """List all evaluation runs."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        rows = conn.execute(
            """SELECT * FROM model_eval_runs ORDER BY started_at DESC"""
        ).fetchall()

        runs = []
        for r in rows:
            runs.append({
                "id": r["id"],
                "provider": r["provider"],
                "model": r["model"],
                "language": r["language"],
                "dataset_name": r["dataset_name"],
                "session_count": r["session_count"],
                "status": r["status"],
                "summary": json.loads(r["summary_json"] or "{}"),
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "error_message": r["error_message"],
            })
        return jsonify(runs)
    finally:
        conn.close()


@app.route("/api/eval/runs", methods=["POST"])
@jwt_required()
def start_eval_run():
    """Start a new evaluation run (async-style: creates the record, actual running is via CLI)."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        provider = data.get("provider")
        model = data.get("model")
        if not provider or not model:
            return jsonify({"error": "provider and model are required"}), 400

        language = data.get("language", "zh")
        dataset_name = data.get("dataset_name", "default")
        max_sessions = data.get("max_sessions", 0)

        conn.execute(
            """INSERT INTO model_eval_runs (user_id, provider, model, language, dataset_name, session_count, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (user["id"], provider, model, language, dataset_name, max_sessions),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        _track_event(conn, user["id"], "eval_run_created", {
            "eval_run_id": run_id, "provider": provider, "model": model,
        })

        return jsonify({"id": run_id, "status": "pending"}), 201
    finally:
        conn.close()


@app.route("/api/eval/runs/<int:run_id>", methods=["GET"])
@jwt_required()
def get_eval_run(run_id):
    """Get details of an evaluation run including per-session scores."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        run_row = conn.execute("SELECT * FROM model_eval_runs WHERE id = ?", (run_id,)).fetchone()
        if not run_row:
            return jsonify({"error": "Eval run not found"}), 404

        scores = conn.execute(
            "SELECT * FROM model_eval_scores WHERE eval_run_id = ? ORDER BY session_id, metric_name",
            (run_id,),
        ).fetchall()

        # Group scores by session
        by_session = {}
        for s in scores:
            sid = s["session_id"]
            if sid not in by_session:
                by_session[sid] = {}
            by_session[sid][s["metric_name"]] = {
                "value": s["metric_value"],
                "meta": json.loads(s["metric_meta_json"] or "{}"),
            }

        return jsonify({
            "id": run_row["id"],
            "provider": run_row["provider"],
            "model": run_row["model"],
            "language": run_row["language"],
            "dataset_name": run_row["dataset_name"],
            "session_count": run_row["session_count"],
            "status": run_row["status"],
            "summary": json.loads(run_row["summary_json"] or "{}"),
            "started_at": run_row["started_at"],
            "completed_at": run_row["completed_at"],
            "error_message": run_row["error_message"],
            "scores_by_session": by_session,
        })
    finally:
        conn.close()


@app.route("/api/eval/scorecard", methods=["GET"])
@jwt_required()
def eval_scorecard():
    """Get aggregate scorecard: avg metrics grouped by provider+model."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        rows = conn.execute(
            """SELECT r.provider, r.model, r.language,
                      s.metric_name,
                      AVG(s.metric_value) as avg_value,
                      COUNT(DISTINCT s.session_id) as session_count,
                      COUNT(DISTINCT r.id) as run_count
               FROM model_eval_runs r
               JOIN model_eval_scores s ON s.eval_run_id = r.id
               WHERE r.status = 'completed'
               GROUP BY r.provider, r.model, r.language, s.metric_name
               ORDER BY r.provider, r.model, s.metric_name"""
        ).fetchall()

        # Pivot into a model-centric structure
        models = {}
        for r in rows:
            key = f"{r['provider']}/{r['model']}"
            if key not in models:
                models[key] = {
                    "provider": r["provider"],
                    "model": r["model"],
                    "language": r["language"],
                    "metrics": {},
                    "session_count": r["session_count"],
                    "run_count": r["run_count"],
                }
            models[key]["metrics"][r["metric_name"]] = round(r["avg_value"], 4)

        return jsonify(list(models.values()))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Policy Gating (Phase 3)
# ---------------------------------------------------------------------------

def _check_generation_policy(conn, provider, model, language="zh"):
    """
    Check if a provider/model/language combination is allowed.
    Returns (action, message) where action is 'allow', 'warn', or 'block'.
    """
    # Find matching policies (exact model match or wildcard)
    policies = conn.execute(
        """SELECT * FROM model_language_policies
           WHERE language = ? AND provider = ? AND enabled = 1
           ORDER BY
             CASE WHEN model_pattern = '*' THEN 1 ELSE 0 END,
             model_pattern""",
        (language, provider),
    ).fetchall()

    if not policies:
        return "allow", None  # No policy = allow

    import fnmatch
    for p in policies:
        pattern = p["model_pattern"]
        if pattern == "*" or fnmatch.fnmatch(model.lower(), pattern.lower()):
            # Look up average scores for this provider/model/language
            score_row = conn.execute(
                """SELECT AVG(s.metric_value) as avg_score
                   FROM model_eval_scores s
                   JOIN model_eval_runs r ON r.id = s.eval_run_id
                   WHERE r.provider = ? AND r.model = ? AND r.language = ?
                     AND r.status = 'completed'
                     AND s.metric_name IN ('schema_valid', 'content_coverage', 'pedagogical_structure', 'hallucination_proxy')""",
                (provider, model, language),
            ).fetchone()

            avg_score = score_row["avg_score"] if score_row and score_row["avg_score"] is not None else None

            if avg_score is None:
                # No eval data — check if policy requires minimum score
                if p["min_score"] and p["min_score"] > 0:
                    return "warn", (
                        f"No evaluation data for {provider}/{model} ({language}). "
                        f"Quality is unverified. Consider running an eval first."
                    )
                return "allow", None

            if avg_score < p["block_threshold"]:
                fallback_msg = ""
                if p["fallback_provider"] and p["fallback_model"]:
                    fallback_msg = f" Try {p['fallback_provider']}/{p['fallback_model']} instead."
                return "block", (
                    f"{provider}/{model} scored {avg_score:.0%} for {language} "
                    f"(below {p['block_threshold']:.0%} threshold).{fallback_msg}"
                )

            if avg_score < p["warning_threshold"]:
                return "warn", (
                    f"{provider}/{model} scored {avg_score:.0%} for {language} "
                    f"(below {p['warning_threshold']:.0%} recommended threshold). "
                    f"Results may be lower quality."
                )

            return "allow", None

    return "allow", None


@app.route("/api/policies", methods=["GET"])
@jwt_required()
def list_policies():
    """List all model-language policies."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        rows = conn.execute("SELECT * FROM model_language_policies ORDER BY language, provider, model_pattern").fetchall()
        policies = []
        for r in rows:
            policies.append({
                "id": r["id"],
                "language": r["language"],
                "provider": r["provider"],
                "model_pattern": r["model_pattern"],
                "enabled": bool(r["enabled"]),
                "min_score": r["min_score"],
                "warning_threshold": r["warning_threshold"],
                "block_threshold": r["block_threshold"],
                "fallback_provider": r["fallback_provider"],
                "fallback_model": r["fallback_model"],
                "notes": r["notes"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return jsonify(policies)
    finally:
        conn.close()


@app.route("/api/policies", methods=["POST"])
@jwt_required()
def create_policy():
    """Create a new model-language policy."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        language = data.get("language")
        provider = data.get("provider")
        if not language or not provider:
            return jsonify({"error": "language and provider are required"}), 400

        model_pattern = data.get("model_pattern", "*")
        try:
            conn.execute(
                """INSERT INTO model_language_policies
                   (language, provider, model_pattern, enabled, min_score,
                    warning_threshold, block_threshold, fallback_provider, fallback_model, notes, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    language, provider, model_pattern,
                    1 if data.get("enabled", True) else 0,
                    data.get("min_score", 0.0),
                    data.get("warning_threshold", 0.6),
                    data.get("block_threshold", 0.3),
                    data.get("fallback_provider"),
                    data.get("fallback_model"),
                    data.get("notes"),
                    user["id"],
                ),
            )
            policy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            return jsonify({"id": policy_id}), 201
        except Exception as e:
            if "UNIQUE" in str(e):
                return jsonify({"error": "Policy already exists for this language/provider/model combination"}), 409
            raise
    finally:
        conn.close()


@app.route("/api/policies/<int:policy_id>", methods=["PUT"])
@jwt_required()
def update_policy(policy_id):
    """Update an existing policy."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        existing = conn.execute("SELECT id FROM model_language_policies WHERE id = ?", (policy_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Policy not found"}), 404

        updates = []
        params = []
        for field in ["enabled", "min_score", "warning_threshold", "block_threshold",
                       "fallback_provider", "fallback_model", "notes"]:
            if field in data:
                val = data[field]
                if field == "enabled":
                    val = 1 if val else 0
                updates.append(f"{field} = ?")
                params.append(val)

        if not updates:
            return jsonify({"error": "No fields to update"}), 400

        updates.append("updated_at = datetime('now')")
        params.append(policy_id)
        conn.execute(f"UPDATE model_language_policies SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/policies/<int:policy_id>", methods=["DELETE"])
@jwt_required()
def delete_policy(policy_id):
    """Delete a policy."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        existing = conn.execute("SELECT id FROM model_language_policies WHERE id = ?", (policy_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Policy not found"}), 404

        conn.execute("DELETE FROM model_language_policies WHERE id = ?", (policy_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/policies/check", methods=["POST"])
@jwt_required()
def check_policy():
    """Check if a provider/model/language combo is allowed by policy."""
    data = request.get_json() or {}
    provider = data.get("provider")
    model = data.get("model")
    language = data.get("language", "zh")

    if not provider or not model:
        return jsonify({"error": "provider and model required"}), 400

    conn = get_db()
    try:
        action, message = _check_generation_policy(conn, provider, model, language)
        return jsonify({"action": action, "message": message})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Retrieval Context routes (Phase 4)
# ---------------------------------------------------------------------------

@app.route("/api/sessions/<session_id>/retrieval-context", methods=["GET"])
@jwt_required()
def get_retrieval_context(session_id):
    """Preview the retrieval context that would be injected for a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        # Load session data
        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"context": {}, "text": ""})

        sessions_path = os.path.join(run["output_dir"], "sessions.json")
        if not os.path.isfile(sessions_path):
            return jsonify({"context": {}, "text": ""})

        with open(sessions_path, "r", encoding="utf-8") as f:
            sessions_data = json.load(f)

        session_data = None
        for s in sessions_data.get("sessions", []):
            if s["session_id"] == session_id:
                session_data = s
                break

        if not session_data:
            return jsonify({"context": {}, "text": ""})

        context = _retrieve_context_for_session(conn, user["id"], session_id, session_data)
        text = build_retrieval_context_block(context)

        return jsonify({
            "context": context,
            "text": text,
            "stats": {
                "prior_vocab": len(context.get("prior_vocab", [])),
                "prior_corrections": len(context.get("prior_corrections", [])),
                "prior_sentences": len(context.get("prior_sentences", [])),
                "feedback_patterns": len(context.get("feedback_patterns", [])),
            },
        })
    finally:
        conn.close()


@app.route("/api/retrieval/stats", methods=["GET"])
@jwt_required()
def retrieval_stats():
    """Get retrieval index stats for the current user."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        items = conn.execute(
            """SELECT item_type, COUNT(*) as count
               FROM user_retrieval_items
               WHERE user_id = ?
               GROUP BY item_type""",
            (user["id"],),
        ).fetchall()

        feedback = conn.execute(
            """SELECT action, COUNT(*) as count
               FROM user_feedback_memory
               WHERE user_id = ?
               GROUP BY action""",
            (user["id"],),
        ).fetchall()

        sessions_indexed = conn.execute(
            """SELECT COUNT(DISTINCT session_id) as count
               FROM user_retrieval_items WHERE user_id = ?""",
            (user["id"],),
        ).fetchone()

        return jsonify({
            "items_by_type": {row["item_type"]: row["count"] for row in items},
            "feedback_by_action": {row["action"]: row["count"] for row in feedback},
            "sessions_indexed": sessions_indexed["count"] if sessions_indexed else 0,
            "total_items": sum(row["count"] for row in items),
            "total_feedback": sum(row["count"] for row in feedback),
        })
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fine-Tuning (Phase 5) - behind admin flag
# ---------------------------------------------------------------------------

def _is_feature_enabled(conn, feature_key, default=False):
    """Check if a feature flag is enabled in admin_settings."""
    row = conn.execute(
        "SELECT value FROM admin_settings WHERE key = ?", (feature_key,)
    ).fetchone()
    if not row:
        return default
    return row["value"].lower() in ("true", "1", "yes", "enabled")


@app.route("/api/admin/settings", methods=["GET"])
@jwt_required()
def get_admin_settings():
    """Get all admin settings."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        rows = conn.execute("SELECT key, value, updated_at FROM admin_settings").fetchall()
        return jsonify({r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows})
    finally:
        conn.close()


@app.route("/api/admin/settings", methods=["PUT"])
@jwt_required()
def update_admin_settings():
    """Update admin settings. Body: { key: value, ... }"""
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        for key, value in data.items():
            conn.execute(
                """INSERT INTO admin_settings (key, value, updated_by, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value = ?, updated_by = ?, updated_at = datetime('now')""",
                (key, str(value), user["id"], str(value), user["id"]),
            )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/fine-tune/export", methods=["POST"])
@jwt_required()
def export_training_data():
    """Export de-identified training records from user summaries.
    Body: { sessions?: list[str], include_retrieval_context?: bool }
    Returns: { records: list, count: int }
    """
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        if not _is_feature_enabled(conn, "fine_tuning_enabled"):
            return jsonify({"error": "Fine-tuning is not enabled. Enable it in Admin Settings."}), 403

        data = request.get_json() or {}
        session_filter = data.get("sessions")  # optional list of session IDs
        include_retrieval = data.get("include_retrieval_context", False)

        # Load all completed summaries
        query = """SELECT ls.session_id, ls.lesson_data_json, ls.provider, ls.model, ls.user_id
                   FROM lesson_summaries ls
                   WHERE ls.lesson_data_json IS NOT NULL"""
        params = []
        if session_filter:
            placeholders = ",".join("?" for _ in session_filter)
            query += f" AND ls.session_id IN ({placeholders})"
            params.extend(session_filter)

        summaries = conn.execute(query, params).fetchall()

        records = []
        for row in summaries:
            lesson_data = json.loads(row["lesson_data_json"])
            session_id = row["session_id"]
            summary_user_id = row["user_id"]

            # Load the source transcript
            run = conn.execute(
                """SELECT pr.output_dir FROM parse_runs pr
                   WHERE pr.user_id = ? AND pr.status = 'completed'
                   ORDER BY pr.completed_at DESC LIMIT 1""",
                (summary_user_id,),
            ).fetchone()

            if not run:
                continue

            sessions_path = os.path.join(run["output_dir"], "sessions.json")
            if not os.path.isfile(sessions_path):
                continue

            with open(sessions_path, "r", encoding="utf-8") as f:
                sessions_data = json.load(f)

            session_data = None
            for s in sessions_data.get("sessions", []):
                if s["session_id"] == session_id:
                    session_data = s
                    break

            if not session_data:
                continue

            # Build de-identified transcript
            from scripts.generate_outputs import build_transcript_text, load_prompt
            transcript = build_transcript_text(session_data)

            # De-identify: replace speaker names with generic labels
            transcript = transcript.replace(
                session_data.get("messages", [{}])[0].get("speaker_raw", ""),
                "Teacher" if session_data.get("messages", [{}])[0].get("speaker_role") == "teacher" else "Student",
            )

            # Build the expected output (lesson_data minus metadata)
            output = {k: v for k, v in lesson_data.items()
                      if k not in ("generation_meta", "assets", "lesson_id", "source_session_ids")}

            system_prompt = load_prompt("master-summarizer")

            user_content = transcript
            if include_retrieval:
                context = _retrieve_context_for_session(conn, summary_user_id, session_id, session_data)
                context_block = build_retrieval_context_block(context)
                if context_block:
                    user_content = context_block + "\n" + transcript

            records.append({
                "session_id": session_id,
                "system": system_prompt,
                "user": user_content,
                "assistant": json.dumps(output, ensure_ascii=False),
            })

        return jsonify({"records": records, "count": len(records)})
    finally:
        conn.close()


@app.route("/api/fine-tune/export/jsonl", methods=["POST"])
@jwt_required()
def export_training_jsonl():
    """Export training data as JSONL format (compatible with most fine-tuning frameworks).
    Body: same as /api/fine-tune/export
    Returns: JSONL text file download.
    """
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403
        if not _is_feature_enabled(conn, "fine_tuning_enabled"):
            return jsonify({"error": "Fine-tuning is not enabled"}), 403

        # Reuse the export logic
        from flask import Response

        data = request.get_json() or {}
        with app.test_request_context(json=data):
            # Call export_training_data internally
            pass

        # Re-fetch to avoid nested context issues
        session_filter = data.get("sessions")
        query = """SELECT ls.session_id, ls.lesson_data_json, ls.user_id
                   FROM lesson_summaries ls WHERE ls.lesson_data_json IS NOT NULL"""
        params = []
        if session_filter:
            placeholders = ",".join("?" for _ in session_filter)
            query += f" AND ls.session_id IN ({placeholders})"
            params.extend(session_filter)

        summaries = conn.execute(query, params).fetchall()

        from scripts.generate_outputs import build_transcript_text, load_prompt
        system_prompt = load_prompt("master-summarizer")
        lines = []

        for row in summaries:
            lesson_data = json.loads(row["lesson_data_json"])
            session_id = row["session_id"]
            summary_user_id = row["user_id"]

            run = conn.execute(
                """SELECT pr.output_dir FROM parse_runs pr
                   WHERE pr.user_id = ? AND pr.status = 'completed'
                   ORDER BY pr.completed_at DESC LIMIT 1""",
                (summary_user_id,),
            ).fetchone()
            if not run:
                continue

            sessions_path = os.path.join(run["output_dir"], "sessions.json")
            if not os.path.isfile(sessions_path):
                continue

            with open(sessions_path, "r", encoding="utf-8") as f:
                sessions_data = json.load(f)

            session_data = None
            for s in sessions_data.get("sessions", []):
                if s["session_id"] == session_id:
                    session_data = s
                    break
            if not session_data:
                continue

            transcript = build_transcript_text(session_data)
            output = {k: v for k, v in lesson_data.items()
                      if k not in ("generation_meta", "assets", "lesson_id", "source_session_ids")}

            record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": transcript},
                    {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
                ]
            }
            lines.append(json.dumps(record, ensure_ascii=False))

        return Response(
            "\n".join(lines),
            mimetype="application/jsonl",
            headers={"Content-Disposition": "attachment; filename=training-data.jsonl"},
        )
    finally:
        conn.close()


@app.route("/api/fine-tune/runs", methods=["GET"])
@jwt_required()
def list_fine_tune_runs():
    """List fine-tune runs."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        rows = conn.execute(
            "SELECT * FROM fine_tune_runs ORDER BY started_at DESC"
        ).fetchall()

        runs = []
        for r in rows:
            runs.append({
                "id": r["id"],
                "base_model": r["base_model"],
                "adapter_name": r["adapter_name"],
                "training_records": r["training_records"],
                "status": r["status"],
                "config": json.loads(r["config_json"]) if r["config_json"] else {},
                "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else {},
                "output_path": r["output_path"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "error_message": r["error_message"],
            })
        return jsonify(runs)
    finally:
        conn.close()


@app.route("/api/fine-tune/runs", methods=["POST"])
@jwt_required()
def create_fine_tune_run():
    """Create a fine-tune run record. Actual training is done via CLI script.
    Body: { base_model, adapter_name?, config?: { epochs, lr, lora_rank, ... } }
    """
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403
        if not _is_feature_enabled(conn, "fine_tuning_enabled"):
            return jsonify({"error": "Fine-tuning is not enabled"}), 403

        base_model = data.get("base_model")
        if not base_model:
            return jsonify({"error": "base_model is required"}), 400

        adapter_name = data.get("adapter_name", f"lessonlens-{base_model.replace(':', '-')}")
        config = data.get("config", {
            "epochs": 3,
            "learning_rate": 2e-4,
            "lora_rank": 16,
            "lora_alpha": 32,
            "batch_size": 4,
        })

        cursor = conn.execute(
            """INSERT INTO fine_tune_runs
               (created_by, base_model, adapter_name, config_json)
               VALUES (?, ?, ?, ?)""",
            (user["id"], base_model, adapter_name, json.dumps(config)),
        )
        conn.commit()
        return jsonify({"id": cursor.lastrowid}), 201
    finally:
        conn.close()


@app.route("/api/fine-tune/runs/<int:run_id>", methods=["PUT"])
@jwt_required()
def update_fine_tune_run(run_id):
    """Update a fine-tune run status/metrics (used by training script)."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        if not user["is_admin"]:
            return jsonify({"error": "Admin only"}), 403

        existing = conn.execute("SELECT id FROM fine_tune_runs WHERE id = ?", (run_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Run not found"}), 404

        updates = []
        params = []
        for field in ["status", "output_path", "adapter_name", "error_message", "training_records"]:
            if field in data:
                updates.append(f"{field} = ?")
                params.append(data[field])
        if "metrics" in data:
            updates.append("metrics_json = ?")
            params.append(json.dumps(data["metrics"]))
        if data.get("status") == "completed":
            updates.append("completed_at = datetime('now')")

        if updates:
            params.append(run_id)
            conn.execute(f"UPDATE fine_tune_runs SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

        return jsonify({"ok": True})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Attachment routes (Phase 0A)
# ---------------------------------------------------------------------------
ATTACHMENTS_FOLDER = str(ROOT_DIR / "attachments")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp"}


@app.route("/api/attachments/upload", methods=["POST"])
@jwt_required()
def upload_attachments():
    """Upload one or more images, extract EXIF, auto-match to sessions."""
    from image_helpers import extract_exif_datetime, match_image_to_sessions, is_image_file

    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        files = request.files.getlist("images")
        if not files:
            return jsonify({"error": "No images provided"}), 400

        os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)

        # Load sessions for auto-matching
        run = _load_latest_completed_run(conn, user["id"])
        sessions = []
        if run:
            rows = conn.execute(
                "SELECT session_id, date, start_time, end_time FROM sessions WHERE user_id = ? AND run_id = ?",
                (user["id"], run["run_id"]),
            ).fetchall()
            sessions = [dict(r) for r in rows]

        results = []
        for file in files:
            if not file.filename:
                continue

            _, ext = os.path.splitext(file.filename)
            if ext.lower() not in IMAGE_EXTENSIONS:
                results.append({"filename": file.filename, "error": "unsupported_format"})
                continue

            original_name = secure_filename(file.filename) or "unnamed-image"
            stored_name = f"{uuid.uuid4()}{ext.lower()}"
            filepath = os.path.join(ATTACHMENTS_FOLDER, stored_name)
            file.save(filepath)

            file_hash = compute_file_hash(filepath)

            # Check for duplicate
            existing = conn.execute(
                "SELECT id FROM attachments WHERE user_id = ? AND sha256 = ?",
                (user["id"], file_hash),
            ).fetchone()
            if existing:
                os.remove(filepath)
                results.append({
                    "filename": file.filename,
                    "attachment_id": existing["id"],
                    "status": "duplicate",
                })
                continue

            # Extract EXIF metadata
            exif = extract_exif_datetime(filepath)

            mime_type = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"

            cursor = conn.execute(
                """INSERT INTO attachments
                   (user_id, stored_filename, original_filename, mime_type, sha256,
                    captured_at_utc, captured_at_local, timezone_hint, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user["id"], stored_name, original_name, mime_type, file_hash,
                 exif["captured_at_utc"], exif["captured_at_local"],
                 exif["timezone_hint"], json.dumps(exif["metadata_json"])),
            )
            attachment_id = cursor.lastrowid

            # Auto-match to session
            match = match_image_to_sessions(exif["captured_at_local"], sessions)
            session_attachment_id = None
            if match["session_id"]:
                sa_cursor = conn.execute(
                    """INSERT OR IGNORE INTO session_attachments
                       (user_id, session_id, attachment_id, match_confidence, match_reason, assigned_by)
                       VALUES (?, ?, ?, ?, ?, 'auto')""",
                    (user["id"], match["session_id"], attachment_id,
                     match["confidence"], match["reason"]),
                )
                if sa_cursor.rowcount:
                    session_attachment_id = sa_cursor.lastrowid

            conn.commit()

            results.append({
                "filename": file.filename,
                "attachment_id": attachment_id,
                "status": "created",
                "timestamp_source": exif["source"],
                "captured_at_local": exif["captured_at_local"],
                "match": {
                    "session_id": match["session_id"],
                    "confidence": match["confidence"],
                    "reason": match["reason"],
                    "session_attachment_id": session_attachment_id,
                },
            })

        _track_event(conn, user["id"], "upload_images", {"count": len(results)})

        return jsonify({"attachments": results}), 201
    finally:
        conn.close()


@app.route("/api/attachments", methods=["GET"])
@jwt_required()
def list_attachments():
    """List all attachments for the current user, with optional filter."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        filter_type = request.args.get("filter")  # 'unmatched', 'low', 'all'

        if filter_type in ("unmatched", "low"):
            # Get attachments not matched or with low confidence
            if filter_type == "unmatched":
                rows = conn.execute(
                    """SELECT a.* FROM attachments a
                       LEFT JOIN session_attachments sa ON a.id = sa.attachment_id AND sa.user_id = a.user_id
                       WHERE a.user_id = ? AND sa.id IS NULL
                       ORDER BY a.ingested_at DESC""",
                    (user["id"],),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT a.* FROM attachments a
                       LEFT JOIN session_attachments sa ON a.id = sa.attachment_id AND sa.user_id = a.user_id
                       WHERE a.user_id = ? AND (sa.id IS NULL OR sa.match_confidence = 'low')
                       ORDER BY a.ingested_at DESC""",
                    (user["id"],),
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM attachments WHERE user_id = ? ORDER BY ingested_at DESC",
                (user["id"],),
            ).fetchall()

        attachments = []
        for r in rows:
            # Get session assignments
            assignments = conn.execute(
                """SELECT sa.*, s.date, s.start_time, s.end_time
                   FROM session_attachments sa
                   JOIN sessions s ON sa.session_id = s.session_id AND sa.user_id = s.user_id
                   WHERE sa.attachment_id = ? AND sa.user_id = ?""",
                (r["id"], user["id"]),
            ).fetchall()

            attachments.append({
                "id": r["id"],
                "original_filename": r["original_filename"],
                "mime_type": r["mime_type"],
                "captured_at_local": r["captured_at_local"],
                "captured_at_utc": r["captured_at_utc"],
                "ingested_at": r["ingested_at"],
                "sessions": [{
                    "session_id": a["session_id"],
                    "confidence": a["match_confidence"],
                    "reason": a["match_reason"],
                    "assigned_by": a["assigned_by"],
                    "date": a["date"],
                } for a in assignments],
            })

        return jsonify({"attachments": attachments})
    finally:
        conn.close()


@app.route("/api/attachments/<int:attachment_id>/image", methods=["GET"])
@jwt_required()
def serve_attachment_image(attachment_id):
    """Serve an attachment image file (user-scoped)."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        att = conn.execute(
            "SELECT * FROM attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user["id"]),
        ).fetchone()
        if not att:
            return jsonify({"error": "Attachment not found"}), 404

        return send_from_directory(
            ATTACHMENTS_FOLDER, att["stored_filename"],
            mimetype=att["mime_type"],
        )
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/attachments", methods=["GET"])
@jwt_required()
def get_session_attachments(session_id):
    """Get all attachments for a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        rows = conn.execute(
            """SELECT sa.id as sa_id, sa.match_confidence, sa.match_reason, sa.assigned_by, sa.assigned_at,
                      a.id as attachment_id, a.original_filename, a.mime_type,
                      a.captured_at_local, a.captured_at_utc, a.stored_filename
               FROM session_attachments sa
               JOIN attachments a ON sa.attachment_id = a.id
               WHERE sa.session_id = ? AND sa.user_id = ?
               ORDER BY a.captured_at_local ASC""",
            (session_id, user["id"]),
        ).fetchall()

        attachments = [{
            "session_attachment_id": r["sa_id"],
            "attachment_id": r["attachment_id"],
            "original_filename": r["original_filename"],
            "mime_type": r["mime_type"],
            "captured_at_local": r["captured_at_local"],
            "match_confidence": r["match_confidence"],
            "match_reason": r["match_reason"],
            "assigned_by": r["assigned_by"],
        } for r in rows]

        return jsonify({"attachments": attachments})
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/attachments/assign", methods=["POST"])
@jwt_required()
def assign_attachment(session_id):
    """Manually assign an attachment to a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        data = request.get_json() or {}
        attachment_id = data.get("attachment_id")
        if not attachment_id:
            return jsonify({"error": "attachment_id required"}), 400

        # Verify attachment belongs to user
        att = conn.execute(
            "SELECT id FROM attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user["id"]),
        ).fetchone()
        if not att:
            return jsonify({"error": "Attachment not found"}), 404

        # Verify session belongs to user
        sess = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user["id"]),
        ).fetchone()
        if not sess:
            return jsonify({"error": "Session not found"}), 404

        try:
            cursor = conn.execute(
                """INSERT INTO session_attachments
                   (user_id, session_id, attachment_id, match_confidence, match_reason, assigned_by)
                   VALUES (?, ?, ?, 'high', 'manual_assignment', 'manual')""",
                (user["id"], session_id, attachment_id),
            )
            conn.commit()
            return jsonify({"session_attachment_id": cursor.lastrowid}), 201
        except sqlite3.IntegrityError:
            return jsonify({"error": "Already assigned"}), 409
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/attachments/<int:attachment_id>", methods=["DELETE"])
@jwt_required()
def unassign_attachment(session_id, attachment_id):
    """Remove an attachment from a session (unassign)."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        result = conn.execute(
            "DELETE FROM session_attachments WHERE session_id = ? AND attachment_id = ? AND user_id = ?",
            (session_id, attachment_id, user["id"]),
        )
        if result.rowcount == 0:
            return jsonify({"error": "Assignment not found"}), 404

        conn.commit()
        return jsonify({"status": "removed"}), 200
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
@app.route("/api/sessions/<session_id>/annotations", methods=["GET"])
@jwt_required()
def list_annotations(session_id):
    """List annotations for a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        target_type = request.args.get("target_type")
        query = "SELECT * FROM annotations WHERE session_id = ? AND user_id = ? AND status != 'dismissed'"
        params = [session_id, user["id"]]
        if target_type:
            query += " AND target_type = ?"
            params.append(target_type)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
        return jsonify([{
            "id": r["id"],
            "session_id": r["session_id"],
            "target_type": r["target_type"],
            "target_id": r["target_id"],
            "target_section": r["target_section"],
            "annotation_type": r["annotation_type"],
            "content": json.loads(r["content_json"]),
            "status": r["status"],
            "created_by_role": r["created_by_role"],
            "created_at": r["created_at"],
        } for r in rows])
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/annotations", methods=["POST"])
@jwt_required()
def create_annotation(session_id):
    """Create an annotation on a message or summary item."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        required = ("target_type", "target_id", "annotation_type")
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        role = user["role"] if "role" in user.keys() else "student"
        content = data.get("content", {})
        cursor = conn.execute(
            """INSERT INTO annotations
               (user_id, session_id, target_type, target_id, target_section,
                annotation_type, content_json, created_by_role)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], session_id, data["target_type"], data["target_id"],
             data.get("target_section"), data["annotation_type"],
             json.dumps(content, ensure_ascii=False), role),
        )
        # Record to feedback memory for retrieval (Phase 4)
        ann_type = data["annotation_type"]
        if ann_type in ("correction", "reclassify"):
            action = "correct" if ann_type == "correction" else "reclassify"
            _record_feedback_memory(
                conn, user["id"], session_id, action,
                target_type=data["target_type"],
                target_id=data["target_id"],
                original=content.get("original") or content.get("original_type"),
                corrected=content.get("corrected") or content.get("corrected_type"),
                detail=content.get("reason"),
            )

        conn.commit()
        return jsonify({"id": cursor.lastrowid, "status": "created"}), 201
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/annotations/<int:annotation_id>", methods=["PUT"])
@jwt_required()
def update_annotation(session_id, annotation_id):
    """Update an annotation."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        existing = conn.execute(
            "SELECT * FROM annotations WHERE id = ? AND user_id = ? AND session_id = ?",
            (annotation_id, user["id"], session_id),
        ).fetchone()
        if not existing:
            return jsonify({"error": "Annotation not found"}), 404

        updates = []
        params = []
        if "content" in data:
            updates.append("content_json = ?")
            params.append(json.dumps(data["content"], ensure_ascii=False))
        if "status" in data:
            updates.append("status = ?")
            params.append(data["status"])
        if "annotation_type" in data:
            updates.append("annotation_type = ?")
            params.append(data["annotation_type"])
        if not updates:
            return jsonify({"error": "No fields to update"}), 400

        updates.append("updated_at = datetime('now')")
        params.extend([annotation_id, user["id"]])
        conn.execute(
            f"UPDATE annotations SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            params,
        )
        conn.commit()
        return jsonify({"status": "updated"})
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/annotations/<int:annotation_id>", methods=["DELETE"])
@jwt_required()
def delete_annotation(session_id, annotation_id):
    """Soft-delete an annotation (set status to dismissed)."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err
        result = conn.execute(
            "UPDATE annotations SET status = 'dismissed', updated_at = datetime('now') WHERE id = ? AND user_id = ? AND session_id = ?",
            (annotation_id, user["id"], session_id),
        )
        if result.rowcount == 0:
            return jsonify({"error": "Annotation not found"}), 404
        conn.commit()
        return jsonify({"status": "dismissed"})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Re-parse
# ---------------------------------------------------------------------------
@app.route("/api/reparse", methods=["POST"])
@jwt_required()
def reparse_sessions():
    """Re-parse all sessions using the current parser code.

    Re-runs parse_lines() on stored upload files, updates sessions.json
    and session metadata without deleting summaries or annotations.
    """
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        canonical_run = _load_latest_completed_run(conn, user["id"])
        if not canonical_run:
            return jsonify({"error": "No existing parse run"}), 404

        # Find the most recent upload file
        upload = conn.execute(
            """SELECT u.* FROM uploads u
               JOIN parse_runs pr ON pr.upload_id = u.id
               WHERE pr.user_id = ? AND pr.status = 'completed'
               ORDER BY u.id DESC LIMIT 1""",
            (user["id"],),
        ).fetchone()
        if not upload:
            return jsonify({"error": "No upload file found"}), 404

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], upload["stored_filename"])
        if not os.path.isfile(filepath):
            return jsonify({"error": "Upload file missing from disk"}), 404

        # Import parser
        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from parse_line_export import load_config, parse_lines, write_outputs
        from extract_transcript import extract

        config = load_config()
        source_meta = extract(filepath)
        lines = source_meta.pop("lines")
        result = parse_lines(lines, source_meta, config)

        # Apply feedback overrides: accepted reclassifications survive re-parse
        reclass_rows = conn.execute(
            """SELECT target_id, corrected_value FROM feedback_signals
               WHERE user_id = ? AND signal_type = 'reclassify_message'""",
            (user["id"],),
        ).fetchall()
        overrides = {r["target_id"]: r["corrected_value"] for r in reclass_rows if r["target_id"]}
        user_overrides_applied = 0
        if overrides:
            for sess in result["sessions"]:
                for msg in sess.get("messages", []):
                    mid = msg.get("message_id", "")
                    if mid in overrides and msg.get("message_type") != overrides[mid]:
                        msg["message_type"] = overrides[mid]
                        if "user-corrected" not in msg.get("tags", []):
                            msg.setdefault("tags", []).append("user-corrected")
                        user_overrides_applied += 1
                # Recompute session stats after overrides
                messages = sess.get("messages", [])
                sess["lesson_content_count"] = sum(1 for m in messages if m["message_type"] == "lesson-content")
                sess["message_count"] = len(messages)
            # Recompute global stats
            all_msgs = [m for s in result["sessions"] for m in s.get("messages", [])]
            result["stats"]["lesson_content_messages"] = sum(1 for m in all_msgs if m["message_type"] == "lesson-content")

        # Archive old sessions.json
        sessions_path = os.path.join(canonical_run["output_dir"], "sessions.json")
        if os.path.isfile(sessions_path):
            archive_dir = os.path.join(canonical_run["output_dir"], "_previous")
            os.makedirs(archive_dir, exist_ok=True)
            archive_name = f"sessions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            import shutil
            shutil.copy2(sessions_path, os.path.join(archive_dir, archive_name))

        # Write new sessions.json (and other artifacts)
        write_outputs(result, source_meta, config, canonical_run["run_id"], canonical_run["output_dir"])

        # Update session metadata in DB
        new_sessions_by_id = {s["session_id"]: s for s in result["sessions"] if s["message_count"] > 0}
        existing_sessions = conn.execute(
            "SELECT id, session_id FROM sessions WHERE run_id = ? AND user_id = ?",
            (canonical_run["run_id"], user["id"]),
        ).fetchall()
        existing_ids = {r["session_id"] for r in existing_sessions}

        updated = 0
        inserted = 0
        for sid, sess in new_sessions_by_id.items():
            if sid in existing_ids:
                conn.execute(
                    """UPDATE sessions SET
                          message_count = ?, lesson_content_count = ?,
                          teacher_message_count = ?, student_message_count = ?,
                          start_time = ?, end_time = ?, boundary_confidence = ?
                       WHERE run_id = ? AND session_id = ? AND user_id = ?""",
                    (sess["message_count"], sess["lesson_content_count"],
                     sess.get("teacher_message_count", 0), sess.get("student_message_count", 0),
                     sess["start_time"], sess["end_time"], sess["boundary_confidence"],
                     canonical_run["run_id"], sid, user["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO sessions
                       (run_id, user_id, session_id, date, start_time, end_time,
                        message_count, lesson_content_count, teacher_message_count, student_message_count,
                        boundary_confidence, topics_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (canonical_run["run_id"], user["id"], sid, sess["date"],
                     sess["start_time"], sess["end_time"],
                     sess["message_count"], sess["lesson_content_count"],
                     sess.get("teacher_message_count", 0), sess.get("student_message_count", 0),
                     sess["boundary_confidence"], json.dumps([])),
                )
                inserted += 1

        # Update run stats
        stats = result["stats"]
        conn.execute(
            """UPDATE parse_runs SET
                  session_count = ?, message_count = ?, lesson_content_count = ?
               WHERE run_id = ?""",
            (stats["total_sessions"], stats["total_messages"],
             stats["lesson_content_messages"], canonical_run["run_id"]),
        )
        # Auto-archive sessions with no teacher messages (not lesson-related)
        auto_archived = conn.execute(
            """UPDATE sessions SET is_archived = 1
               WHERE run_id = ? AND user_id = ? AND teacher_message_count = 0
                 AND is_archived = 0""",
            (canonical_run["run_id"], user["id"]),
        ).rowcount
        # Unarchive sessions that now have teacher messages (parser improved)
        auto_unarchived = conn.execute(
            """UPDATE sessions SET is_archived = 0
               WHERE run_id = ? AND user_id = ? AND teacher_message_count > 0
                 AND is_archived = 1""",
            (canonical_run["run_id"], user["id"]),
        ).rowcount

        conn.commit()

        _track_event(conn, user["id"], "reparse", {
            "run_id": canonical_run["run_id"],
            "updated": updated,
            "inserted": inserted,
            "total_sessions": stats["total_sessions"],
            "auto_archived": auto_archived,
            "auto_unarchived": auto_unarchived,
            "user_overrides_applied": user_overrides_applied,
        })

        return jsonify({
            "run_id": canonical_run["run_id"],
            "total_sessions": stats["total_sessions"],
            "updated_sessions": updated,
            "new_sessions": inserted,
            "total_messages": stats["total_messages"],
            "lesson_content_count": stats["lesson_content_messages"],
            "auto_archived": auto_archived,
            "auto_unarchived": auto_unarchived,
            "user_overrides_applied": user_overrides_applied,
        })
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/archive", methods=["POST"])
@jwt_required()
def toggle_archive(session_id):
    """Archive or unarchive a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"error": "No parse run"}), 404

        row = conn.execute(
            "SELECT is_archived FROM sessions WHERE run_id = ? AND session_id = ? AND user_id = ?",
            (run["run_id"], session_id, user["id"]),
        ).fetchone()
        if not row:
            return jsonify({"error": "Session not found"}), 404

        new_val = 0 if row["is_archived"] else 1
        conn.execute(
            "UPDATE sessions SET is_archived = ? WHERE run_id = ? AND session_id = ? AND user_id = ?",
            (new_val, run["run_id"], session_id, user["id"]),
        )
        # Record feedback signal for manual archive/unarchive
        signal_type = "archive" if new_val else "unarchive"
        conn.execute(
            """INSERT INTO feedback_signals
               (user_id, session_id, signal_type, original_value, corrected_value)
               VALUES (?, ?, ?, ?, ?)""",
            (user["id"], session_id, signal_type,
             "active" if new_val else "archived",
             "archived" if new_val else "active"),
        )
        conn.commit()
        return jsonify({"session_id": session_id, "is_archived": bool(new_val)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AI Review
# ---------------------------------------------------------------------------

@app.route("/api/sessions/<session_id>/review", methods=["POST"])
@jwt_required()
def trigger_review(session_id):
    """Trigger an AI review of a session's parse or summary."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        run = _load_latest_completed_run(conn, user["id"])
        if not run:
            return jsonify({"error": "No parse run found"}), 404

        body = request.get_json(silent=True) or {}
        review_type = body.get("review_type", "parse")
        if review_type not in ("parse", "summary"):
            return jsonify({"error": "review_type must be 'parse' or 'summary'"}), 400

        # Load provider config
        _, _, use_provider, use_model, temperature = _load_generator_config(
            body.get("provider"), body.get("model"),
        )
        credential_error = _validate_provider_credentials(use_provider)
        if credential_error:
            return jsonify({"error": credential_error}), 400

        # Load session data
        sessions_by_id = _load_sessions_payload(run)
        session_data = sessions_by_id.get(session_id)
        if not session_data:
            return jsonify({"error": "Session not found in sessions.json"}), 404

        # Import review functions
        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from ai_review import review_parse, review_summary

        if review_type == "parse":
            # Load feedback signals for this session
            feedback_rows = conn.execute(
                "SELECT * FROM feedback_signals WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
            feedback = [dict(r) for r in feedback_rows]

            findings = review_parse(
                session_data,
                provider=use_provider,
                model=use_model,
                temperature=temperature,
                feedback_signals=feedback,
            )
        else:
            # Load existing summary for this session
            summary_row = conn.execute(
                """SELECT lesson_data_json FROM lesson_summaries
                   WHERE session_id = ? AND user_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id, user["id"]),
            ).fetchone()
            if not summary_row:
                return jsonify({"error": "No summary found for this session. Generate one first."}), 404

            lesson_data = json.loads(summary_row["lesson_data_json"])
            findings = review_summary(
                lesson_data,
                session_data,
                provider=use_provider,
                model=use_model,
                temperature=temperature,
            )

        # Store review
        findings_json = json.dumps(findings, ensure_ascii=False)
        conn.execute(
            """INSERT INTO ai_reviews
               (user_id, session_id, review_type, provider, model, findings_json, findings_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (user["id"], session_id, review_type, use_provider, use_model,
             findings_json, len(findings)),
        )
        review_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        return jsonify({
            "id": review_id,
            "session_id": session_id,
            "review_type": review_type,
            "provider": use_provider,
            "model": use_model,
            "findings": findings,
            "findings_count": len(findings),
            "status": "pending",
        })
    except FileNotFoundError:
        return jsonify({"error": "Sessions file not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/reviews", methods=["GET"])
@jwt_required()
def list_reviews(session_id):
    """List AI reviews for a session."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        rows = conn.execute(
            """SELECT id, session_id, review_type, provider, model,
                      findings_json, findings_count, accepted_count, dismissed_count,
                      status, created_at
               FROM ai_reviews
               WHERE session_id = ? AND user_id = ?
               ORDER BY created_at DESC""",
            (session_id, user["id"]),
        ).fetchall()

        reviews = []
        for r in rows:
            findings = json.loads(r["findings_json"])
            reviews.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "review_type": r["review_type"],
                "provider": r["provider"],
                "model": r["model"],
                "findings": findings,
                "findings_count": r["findings_count"],
                "accepted_count": r["accepted_count"],
                "dismissed_count": r["dismissed_count"],
                "status": r["status"],
                "created_at": r["created_at"],
            })

        return jsonify(reviews)
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/reviews/<int:review_id>/findings/<int:finding_idx>/accept", methods=["POST"])
@jwt_required()
def accept_finding(session_id, review_id, finding_idx):
    """Accept a review finding — applies the suggested change."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        row = conn.execute(
            "SELECT * FROM ai_reviews WHERE id = ? AND session_id = ? AND user_id = ?",
            (review_id, session_id, user["id"]),
        ).fetchone()
        if not row:
            return jsonify({"error": "Review not found"}), 404

        findings = json.loads(row["findings_json"])
        if finding_idx < 0 or finding_idx >= len(findings):
            return jsonify({"error": "Finding index out of range"}), 400

        finding = findings[finding_idx]
        if finding.get("status") != "pending":
            return jsonify({"error": f"Finding already {finding.get('status')}"}), 400

        # Mark finding as accepted
        finding["status"] = "accepted"

        review_type = row["review_type"]
        if review_type == "parse" and finding.get("suggested_type"):
            # Update message classification in sessions.json
            run = _load_latest_completed_run(conn, user["id"])
            if run:
                sessions_path = os.path.join(run["output_dir"], "sessions.json")
                if os.path.isfile(sessions_path):
                    with open(sessions_path, "r", encoding="utf-8") as f:
                        sessions_data = json.load(f)

                    msg_id = finding["message_id"]
                    updated = False
                    for sess in sessions_data.get("sessions", []):
                        if sess["session_id"] != session_id:
                            continue
                        for msg in sess.get("messages", []):
                            if msg.get("message_id") == msg_id:
                                msg["message_type"] = finding["suggested_type"]
                                if finding.get("suggested_role"):
                                    msg["speaker_role"] = finding["suggested_role"]
                                updated = True
                                break
                        break

                    if updated:
                        with open(sessions_path, "w", encoding="utf-8") as f:
                            json.dump(sessions_data, f, ensure_ascii=False, indent=2)

            # Record feedback signal
            conn.execute(
                """INSERT INTO feedback_signals
                   (user_id, session_id, signal_type, target_id, original_value, corrected_value)
                   VALUES (?, ?, 'reclassify_message', ?, ?, ?)""",
                (user["id"], session_id, finding.get("message_id"),
                 finding.get("current_type"), finding.get("suggested_type")),
            )

        # Record to feedback memory for retrieval (Phase 4)
        _record_feedback_memory(
            conn, user["id"], session_id, "accept_correction",
            target_type=review_type,
            target_id=finding.get("message_id"),
            original=finding.get("current_type"),
            corrected=finding.get("suggested_type"),
            detail=finding.get("reason"),
        )

        # Update review record
        new_accepted = row["accepted_count"] + 1
        new_status = "completed" if (new_accepted + row["dismissed_count"]) >= row["findings_count"] else "reviewed"
        conn.execute(
            """UPDATE ai_reviews SET findings_json = ?, accepted_count = ?, status = ?
               WHERE id = ?""",
            (json.dumps(findings, ensure_ascii=False), new_accepted, new_status, review_id),
        )
        conn.commit()

        return jsonify({"finding": finding, "review_status": new_status})
    finally:
        conn.close()


@app.route("/api/sessions/<session_id>/reviews/<int:review_id>/findings/<int:finding_idx>/dismiss", methods=["POST"])
@jwt_required()
def dismiss_finding(session_id, review_id, finding_idx):
    """Dismiss a review finding — marks it as incorrect."""
    email = get_jwt_identity()
    conn = get_db()
    try:
        user, err = _require_active_user(conn, email)
        if err:
            return err

        row = conn.execute(
            "SELECT * FROM ai_reviews WHERE id = ? AND session_id = ? AND user_id = ?",
            (review_id, session_id, user["id"]),
        ).fetchone()
        if not row:
            return jsonify({"error": "Review not found"}), 404

        findings = json.loads(row["findings_json"])
        if finding_idx < 0 or finding_idx >= len(findings):
            return jsonify({"error": "Finding index out of range"}), 400

        finding = findings[finding_idx]
        if finding.get("status") != "pending":
            return jsonify({"error": f"Finding already {finding.get('status')}"}), 400

        finding["status"] = "dismissed"

        new_dismissed = row["dismissed_count"] + 1
        new_status = "completed" if (row["accepted_count"] + new_dismissed) >= row["findings_count"] else "reviewed"
        conn.execute(
            """UPDATE ai_reviews SET findings_json = ?, dismissed_count = ?, status = ?
               WHERE id = ?""",
            (json.dumps(findings, ensure_ascii=False), new_dismissed, new_status, review_id),
        )
        conn.commit()

        return jsonify({"finding": finding, "review_status": new_status})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def _track_event(conn, user_id, event_type, event_data):
    conn.execute(
        "INSERT INTO analytics_events (user_id, event_type, event_data_json) VALUES (?, ?, ?)",
        (user_id, event_type, json.dumps(event_data)),
    )
    conn.commit()


def _log_security_event(conn, event_type, user_id=None, actor_id=None, detail=None):
    ip = request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr) if request else None
    conn.execute(
        "INSERT INTO security_events (user_id, actor_id, event_type, detail_json, ip_address) VALUES (?, ?, ?, ?, ?)",
        (user_id, actor_id, event_type, json.dumps(detail or {}), ip),
    )


def _require_active_user(conn, email):
    """Load user and verify their account status is 'active'. Returns (user, error_response)."""
    user = _load_user(conn, email)
    if not user:
        return None, (jsonify({"error": "User not found"}), 404)
    status = user["status"] if "status" in user.keys() else "active"
    if status != "active":
        _log_security_event(conn, "blocked_inactive", user_id=user["id"], detail={"status": status})
        conn.commit()
        return None, (jsonify({"error": "Account is not active", "status": status}), 403)
    return user, None


@app.route("/api/analytics/summary", methods=["GET"])
@jwt_required()
def analytics_summary():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not user["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        events = conn.execute(
            "SELECT event_type, COUNT(*) as count FROM analytics_events GROUP BY event_type"
        ).fetchall()
        daily = conn.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as count FROM analytics_events GROUP BY DATE(created_at) ORDER BY day DESC LIMIT 30"
        ).fetchall()
        user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]

        return jsonify({
            "total_users": user_count,
            "events_by_type": {r["event_type"]: r["count"] for r in events},
            "daily_activity": [{"date": r["day"], "count": r["count"]} for r in daily],
        })
    finally:
        conn.close()


@app.route("/api/analytics/event", methods=["POST"])
@jwt_required()
def track_client_event():
    """Track a frontend event (quiz completion, flashcard flip, etc.)."""
    email = get_jwt_identity()
    data = request.get_json() or {}
    event_type = data.get("event_type")
    if not event_type:
        return jsonify({"error": "event_type required"}), 400

    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        _track_event(conn, user["id"], event_type, data.get("event_data", {}))
        return jsonify({"ok": True}), 201
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SPA routes (production)
# ---------------------------------------------------------------------------
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404

    if WEB_DIST_DIR.is_dir():
        target = WEB_DIST_DIR / path
        if path and target.is_file():
            return send_from_directory(str(WEB_DIST_DIR), path)
        index_path = WEB_DIST_DIR / "index.html"
        if index_path.is_file():
            return send_from_directory(str(WEB_DIST_DIR), "index.html")

    return jsonify({
        "error": "Frontend build not found",
        "hint": "Build the web app with `npm run build` in web/ before deploying.",
    }), 404


# ---------------------------------------------------------------------------
# Signup Requests (public)
# ---------------------------------------------------------------------------
@app.route("/api/signup-requests", methods=["POST"])
@rate_limit(max_requests=3, window_seconds=600)
def create_signup_request():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    display_name = data.get("display_name", "").strip()
    reason = data.get("reason", "").strip()

    if not email:
        return jsonify({"error": "Email is required"}), 400
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Invalid email format"}), 400

    conn = get_db()
    try:
        # Reject if already registered
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "Email already registered"}), 409

        # Reject if a pending request already exists
        pending = conn.execute(
            "SELECT id FROM signup_requests WHERE email = ? AND status = 'pending'",
            (email,),
        ).fetchone()
        if pending:
            return jsonify({"error": "A request for this email is already pending"}), 409

        conn.execute(
            "INSERT INTO signup_requests (email, display_name, reason) VALUES (?, ?, ?)",
            (email, display_name, reason),
        )
        _log_security_event(conn, "signup_request_created", detail={"email": email})
        conn.commit()
        return jsonify({"message": "Access request submitted. You will be notified by email when reviewed."}), 201
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Admin: Signup Request Management
# ---------------------------------------------------------------------------
@app.route("/api/admin/signup-requests", methods=["GET"])
@jwt_required()
def list_signup_requests():
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        status_filter = request.args.get("status", "pending")
        rows = conn.execute(
            "SELECT * FROM signup_requests WHERE status = ? ORDER BY created_at DESC",
            (status_filter,),
        ).fetchall()
        return jsonify([{
            "id": r["id"],
            "email": r["email"],
            "display_name": r["display_name"],
            "reason": r["reason"],
            "status": r["status"],
            "reviewed_by": r["reviewed_by"],
            "reviewed_at": r["reviewed_at"],
            "created_at": r["created_at"],
        } for r in rows])
    finally:
        conn.close()


@app.route("/api/admin/signup-requests/<int:req_id>/approve", methods=["POST"])
@jwt_required()
def approve_signup_request(req_id):
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        req = conn.execute("SELECT * FROM signup_requests WHERE id = ?", (req_id,)).fetchone()
        if not req:
            return jsonify({"error": "Request not found"}), 404
        if req["status"] != "pending":
            return jsonify({"error": f"Request already {req['status']}"}), 409

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE signup_requests SET status = 'approved', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (admin["id"], now, req_id),
        )

        # Create invitation token for the approved user
        token = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute(
            "INSERT INTO invitation_tokens (email, token, expires_at, created_by) VALUES (?, ?, ?, ?)",
            (req["email"], token, expires, admin["id"]),
        )
        _log_security_event(conn, "signup_request_approved", user_id=admin["id"], detail={"request_id": req_id, "email": req["email"]})
        conn.commit()
        return jsonify({"message": "Request approved", "invitation_token": token, "email": req["email"], "expires_at": expires}), 200
    finally:
        conn.close()


@app.route("/api/admin/signup-requests/<int:req_id>/deny", methods=["POST"])
@jwt_required()
def deny_signup_request(req_id):
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        req = conn.execute("SELECT * FROM signup_requests WHERE id = ?", (req_id,)).fetchone()
        if not req:
            return jsonify({"error": "Request not found"}), 404
        if req["status"] != "pending":
            return jsonify({"error": f"Request already {req['status']}"}), 409

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE signup_requests SET status = 'denied', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (admin["id"], now, req_id),
        )
        _log_security_event(conn, "signup_request_denied", user_id=admin["id"], detail={"request_id": req_id, "email": req["email"]})
        conn.commit()
        return jsonify({"message": "Request denied"}), 200
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Admin: User Management
# ---------------------------------------------------------------------------
@app.route("/api/admin/users", methods=["GET"])
@jwt_required()
def list_users():
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        rows = conn.execute(
            "SELECT id, email, display_name, is_admin, status, role, last_login_at, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([{
            "id": r["id"],
            "email": r["email"],
            "display_name": r["display_name"],
            "is_admin": bool(r["is_admin"]),
            "status": r["status"] or "active",
            "role": r["role"] or "student",
            "last_login_at": r["last_login_at"],
            "created_at": r["created_at"],
        } for r in rows])
    finally:
        conn.close()


@app.route("/api/admin/users/<int:user_id>/suspend", methods=["POST"])
@jwt_required()
def suspend_user(user_id):
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return jsonify({"error": "User not found"}), 404
        if target["id"] == admin["id"]:
            return jsonify({"error": "Cannot suspend yourself"}), 400
        if (target["status"] or "active") == "suspended":
            return jsonify({"error": "User is already suspended"}), 409

        conn.execute("UPDATE users SET status = 'suspended' WHERE id = ?", (user_id,))
        _log_security_event(conn, "user_suspended", user_id=user_id, actor_id=admin["id"])
        conn.commit()
        return jsonify({"message": "User suspended"}), 200
    finally:
        conn.close()


@app.route("/api/admin/users/<int:user_id>/reactivate", methods=["POST"])
@jwt_required()
def reactivate_user(user_id):
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return jsonify({"error": "User not found"}), 404
        if (target["status"] or "active") == "active":
            return jsonify({"error": "User is already active"}), 409

        conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (user_id,))
        _log_security_event(conn, "user_reactivated", user_id=user_id, actor_id=admin["id"])
        conn.commit()
        return jsonify({"message": "User reactivated"}), 200
    finally:
        conn.close()


@app.route("/api/admin/users/<int:user_id>/role", methods=["POST"])
@jwt_required()
def set_user_role(user_id):
    email = get_jwt_identity()
    conn = get_db()
    try:
        admin = _load_user(conn, email)
        if not admin or not admin["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        data = request.get_json() or {}
        role = data.get("role", "").strip().lower()
        if role not in ("student", "teacher"):
            return jsonify({"error": "Role must be 'student' or 'teacher'"}), 400

        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return jsonify({"error": "User not found"}), 404

        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        _log_security_event(conn, "user_role_changed", user_id=user_id, actor_id=admin["id"],
                            details=f"role={role}")
        conn.commit()
        return jsonify({"message": f"Role set to {role}"}), 200
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Admin: Invitations
# ---------------------------------------------------------------------------
@app.route("/api/admin/invitations", methods=["POST"])
@jwt_required()
def create_invitation():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not user["is_admin"]:
            return jsonify({"error": "Admin required"}), 403

        data = request.get_json() or {}
        invite_email = data.get("email", "").strip().lower()
        if not invite_email:
            return jsonify({"error": "Email required"}), 400

        token = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute(
            "INSERT INTO invitation_tokens (email, token, expires_at, created_by) VALUES (?, ?, ?, ?)",
            (invite_email, token, expires, user["id"]),
        )
        conn.commit()
        return jsonify({"token": token, "email": invite_email, "expires_at": expires}), 201
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Local model health
# ---------------------------------------------------------------------------
@app.route("/api/models/local/health", methods=["GET"])
@jwt_required()
def local_model_health():
    results = {}

    # --- Ollama ---
    ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        req = urllib_request.Request(f"{ollama_base}/api/tags", method="GET")
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        model_names = [m.get("name", "") for m in data.get("models", [])]
        results["ollama"] = {"ok": True, "base_url": ollama_base, "models": model_names}
    except Exception as exc:
        results["ollama"] = {"ok": False, "base_url": ollama_base, "error": str(exc)}

    # --- OpenAI-compatible local ---
    local_oai_base = os.environ.get("LOCAL_OAI_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    try:
        req = urllib_request.Request(f"{local_oai_base}/models", method="GET")
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        model_ids = [m.get("id", "") for m in data.get("data", [])]
        results["openai_compatible_local"] = {"ok": True, "base_url": local_oai_base, "models": model_ids}
    except Exception as exc:
        results["openai_compatible_local"] = {"ok": False, "base_url": local_oai_base, "error": str(exc)}

    return jsonify(results)


# ---------------------------------------------------------------------------
# Runner generation (GitHub Actions dispatch)
# ---------------------------------------------------------------------------
@app.route("/api/generation/dispatch", methods=["POST"])
@jwt_required()
def dispatch_runner_generation():
    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo = os.environ.get("GITHUB_REPO")
    if not github_token or not github_repo:
        return jsonify({"error": "Runner generation not configured (GITHUB_TOKEN / GITHUB_REPO missing)"}), 501

    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        data = request.get_json() or {}
        session_id_filter = data.get("session_id", "")

        # Trigger GitHub Actions workflow_dispatch
        dispatch_url = f"https://api.github.com/repos/{github_repo}/actions/workflows/generate-local.yml/dispatches"
        dispatch_body = json.dumps({
            "ref": "main",
            "inputs": {"session_id": session_id_filter or ""},
        }).encode()
        req = urllib_request.Request(
            dispatch_url,
            data=dispatch_body,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return jsonify({"error": f"GitHub dispatch failed ({exc.code}): {body}"}), 502

        # Record the job
        conn.execute(
            "INSERT INTO generation_jobs (user_id, status, session_id_filter) VALUES (?, 'dispatched', ?)",
            (user["id"], session_id_filter or None),
        )
        conn.commit()
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        return jsonify({"job_id": job_id, "message": "Generation dispatched to runner"}), 202
    finally:
        conn.close()


@app.route("/api/generation/status", methods=["GET"])
@jwt_required()
def runner_generation_status():
    email = get_jwt_identity()
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404

        row = conn.execute(
            "SELECT * FROM generation_jobs WHERE user_id = ? ORDER BY dispatched_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()

        if not row:
            return jsonify({"status": "none"})

        result = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except (json.JSONDecodeError, TypeError):
                result = None

        return jsonify({
            "job_id": row["id"],
            "status": row["status"],
            "session_id_filter": row["session_id_filter"],
            "dispatched_at": row["dispatched_at"],
            "completed_at": row["completed_at"],
            "result": result,
        })
    finally:
        conn.close()


@app.route("/api/generation/webhook", methods=["POST"])
def runner_generation_webhook():
    expected_token = os.environ.get("GENERATION_WEBHOOK_TOKEN")
    if not expected_token:
        return jsonify({"error": "Webhook not configured"}), 501

    provided_token = request.headers.get("X-Webhook-Token", "")
    if provided_token != expected_token:
        return jsonify({"error": "Invalid webhook token"}), 401

    data = request.get_json() or {}
    status = data.get("status", "completed")
    result_json = json.dumps({
        "generated": data.get("generated", 0),
        "failed": data.get("failed", 0),
        "imported": data.get("imported", 0),
        "import_failed": data.get("import_failed", 0),
        "total_missing": data.get("total_missing", 0),
        "run_id": data.get("run_id"),
    })

    conn = get_db()
    try:
        # Update the latest dispatched/running job
        conn.execute(
            """UPDATE generation_jobs
               SET status = ?, completed_at = datetime('now'), result_json = ?
               WHERE id = (
                   SELECT id FROM generation_jobs
                   WHERE status IN ('dispatched', 'running')
                   ORDER BY dispatched_at DESC LIMIT 1
               )""",
            (status, result_json),
        )
        conn.commit()
        return jsonify({"message": "Job updated"})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# Init + run
# ---------------------------------------------------------------------------
with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True, port=5001)

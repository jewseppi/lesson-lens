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
])

DB_PATH = str(ROOT_DIR / "api" / "lessonlens.db")

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
            now = time.time()
            _rate_counts[ip] = [t for t in _rate_counts[ip] if now - t < window_seconds]
            if len(_rate_counts[ip]) >= max_requests:
                return jsonify({"error": "Rate limit exceeded"}), 429
            _rate_counts[ip].append(now)
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
    use_provider = provider_override or gen_defaults.get("default_provider", "openai")
    use_model = model_override or gen_defaults.get("default_model", "gpt-4o")
    temperature = gen_defaults.get("temperature", 0.3)

    return process_session, gen_config, use_provider, use_model, temperature


def _validate_provider_credentials(provider_name):
    if provider_name == "openai" and not os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY not set. Export it before starting the server."
    if provider_name == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY not set. Export it before starting the server."
    if provider_name == "gemini" and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return "GEMINI_API_KEY or GOOGLE_API_KEY not set. Export it before starting the server."
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

    return lesson_data, use_provider, use_model


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            is_admin INTEGER DEFAULT 0,
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
            session_id TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            message_count INTEGER DEFAULT 0,
            lesson_content_count INTEGER DEFAULT 0,
            boundary_confidence TEXT,
            topics_json TEXT DEFAULT '[]',
            FOREIGN KEY (run_id) REFERENCES parse_runs(run_id)
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
    """)
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
            "INSERT INTO users (email, password_hash, display_name) VALUES (?, ?, ?)",
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
            token = create_access_token(identity=email)
            _track_event(conn, user["id"], "login", {})
            return jsonify({"access_token": token, "user": {
                "email": user["email"],
                "display_name": user["display_name"],
                "is_admin": bool(user["is_admin"]),
            }}), 200
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
        user = _load_user(conn, email)
        if not user:
            return jsonify({"error": "User not found"}), 404
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
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404
        return jsonify({
            "email": user["email"],
            "display_name": user["display_name"],
            "is_admin": bool(user["is_admin"]),
        })
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
    replace_existing = bool(data.get("replace_existing", True))

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


@app.route("/api/backup/import", methods=["POST"])
@jwt_required()
def import_backup():
    email = get_jwt_identity()
    replace_existing = request.form.get("replace_existing", "true").lower() not in {"0", "false", "no"}

    if "file" not in request.files:
        return jsonify({"error": "No backup file provided"}), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"error": "No backup file selected"}), 400

    raw_zip = uploaded.read()
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_zip))
    except zipfile.BadZipFile:
        return jsonify({"error": "Backup file must be a valid .zip archive"}), 400

    conn = get_db()
    try:
        user = _load_user(conn, email)
        if not user:
            return jsonify({"error": "User not found"}), 404

        with archive:
            manifest = _read_backup_json(archive, "manifest.json")
            if manifest.get("schema_version") != "lessonlens-backup.v1":
                return jsonify({"error": "Unsupported backup schema"}), 400

            sessions_payload = _read_backup_json(archive, "parse/sessions.json")
            sessions = sessions_payload.get("sessions") or []
            if not isinstance(sessions, list) or not sessions:
                return jsonify({"error": "Backup does not contain any sessions"}), 400

            parse_members = []
            summary_payloads = {}
            raw_export_member = None
            for name in archive.namelist():
                normalized = _normalize_backup_member(name)
                if normalized.startswith("parse/") and not normalized.endswith("/"):
                    parse_members.append(normalized)
                elif normalized.startswith("summaries/") and normalized.endswith(".json"):
                    session_id = Path(normalized).stem
                    summary_payloads[session_id] = json.loads(archive.read(name).decode("utf-8"))
                elif normalized.startswith("raw-exports/") and not normalized.endswith("/") and raw_export_member is None:
                    raw_export_member = normalized

            if replace_existing:
                _delete_user_learning_data(conn, user["id"])
                conn.commit()

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

            import_run_id = datetime.now(timezone.utc).strftime("imported-%Y%m%d-%H%M%S")
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
            for session in sessions:
                if session.get("message_count", 0) == 0:
                    continue
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

            for session in sessions:
                if session.get("message_count", 0) == 0:
                    continue
                conn.execute(
                    """INSERT INTO sessions
                       (run_id, session_id, date, start_time, end_time,
                        message_count, lesson_content_count, boundary_confidence, topics_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        import_run_id,
                        session["session_id"],
                        session["date"],
                        session.get("start_time"),
                        session.get("end_time"),
                        session.get("message_count", 0),
                        session.get("lesson_content_count", 0),
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
            for session_id, lesson_data in summary_payloads.items():
                if lesson_data.get("schema_version") != "lesson-data.v1":
                    continue
                if not conn.execute(
                    "SELECT 1 FROM sessions WHERE run_id = ? AND session_id = ?",
                    (import_run_id, session_id),
                ).fetchone():
                    continue

                summary_dir = Path(app.config["SUMMARIES_FOLDER"]) / import_run_id / session_id
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
                    run_id=import_run_id,
                    user_id=user["id"],
                )
                imported_summaries += 1

            _track_event(conn, user["id"], "import_backup", {
                "session_count": session_count,
                "summary_count": imported_summaries,
                "replace_existing": replace_existing,
            })

            return jsonify({
                "message": "Backup imported successfully",
                "session_count": session_count,
                "summary_count": imported_summaries,
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

        # Clear old data if re-parsing
        if existing_run and force:
            old_run_id = existing_run["run_id"]
            conn.execute("DELETE FROM lesson_summaries WHERE run_id = ?", (old_run_id,))
            conn.execute("DELETE FROM sessions WHERE run_id = ?", (old_run_id,))
            conn.execute("DELETE FROM parse_runs WHERE run_id = ?", (old_run_id,))
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
                   (run_id, session_id, date, start_time, end_time,
                    message_count, lesson_content_count, boundary_confidence, topics_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, sess["session_id"], sess["date"],
                 sess["start_time"], sess["end_time"],
                 sess["message_count"], sess["lesson_content_count"],
                 sess["boundary_confidence"], json.dumps([])),
            )
            inserted_sessions += 1
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


@app.route("/api/sync", methods=["POST"])
@jwt_required()
def sync_file():
    """Upload + parse in one step. If already uploaded, re-parse."""
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

        if existing:
            os.remove(filepath)
            upload_id = existing["id"]
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

        # Now parse (force if already parsed)
        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from parse_line_export import load_config, parse_lines, write_outputs
        from extract_transcript import extract

        # Clear old parse data for this upload
        old_run = conn.execute(
            "SELECT run_id FROM parse_runs WHERE upload_id = ? AND status = 'completed'",
            (upload_id,),
        ).fetchone()
        if old_run:
            old_run_id = old_run["run_id"]
            conn.execute("DELETE FROM lesson_summaries WHERE run_id = ?", (old_run_id,))
            conn.execute("DELETE FROM sessions WHERE run_id = ?", (old_run_id,))
            conn.execute("DELETE FROM parse_runs WHERE run_id = ?", (old_run_id,))
            conn.commit()

        upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        filepath_parse = os.path.join(app.config["UPLOAD_FOLDER"], upload["stored_filename"])
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{upload_id}"
        config = load_config()
        source_meta = extract(filepath_parse)
        lines = source_meta.pop("lines")
        result = parse_lines(lines, source_meta, config)
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
                   (run_id, session_id, date, start_time, end_time,
                    message_count, lesson_content_count, boundary_confidence, topics_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, sess["session_id"], sess["date"],
                 sess["start_time"], sess["end_time"],
                 sess["message_count"], sess["lesson_content_count"],
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

        sessions = []
        for r in rows:
            session_payload = sessions_by_id.get(r["session_id"], {})
            shared_links = _extract_session_links(session_payload)
            if session_payload and not (session_payload.get("message_count", 0) >= 3 or shared_links):
                continue
            sessions.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "date": r["date"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "message_count": r["message_count"],
                "lesson_content_count": r["lesson_content_count"],
                "boundary_confidence": r["boundary_confidence"],
                "topics": json.loads(r["topics_json"] or "[]"),
                "has_summary": bool(r["has_summary"]),
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

        lesson_data, use_provider, use_model = _generate_summary_for_session(
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

        return jsonify(lesson_data), 201

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
                lesson_data, final_provider, final_model = _generate_summary_for_session(
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
# Analytics
# ---------------------------------------------------------------------------
def _track_event(conn, user_id, event_type, event_data):
    conn.execute(
        "INSERT INTO analytics_events (user_id, event_type, event_data_json) VALUES (?, ?, ?)",
        (user_id, event_type, json.dumps(event_data)),
    )
    conn.commit()


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

"""
Pytest fixtures for LessonLens API tests.
Provides isolated test database, Flask test client, and auth helpers.
"""
import os
import sys
import tempfile
import shutil

import pytest

# Ensure the api directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, init_db, get_db, DB_PATH as _orig_db_path  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


# ---------------------------------------------------------------------------
# Temporary directories fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def test_dirs():
    base = tempfile.mkdtemp(prefix="lessonlens-test-")
    dirs = {
        "uploads": os.path.join(base, "raw-exports"),
        "processed": os.path.join(base, "processed"),
        "summaries": os.path.join(base, "summaries"),
        "attachments": os.path.join(base, "attachments"),
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    yield dirs
    shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# App + DB fixture (function-scoped for isolation)
# ---------------------------------------------------------------------------
@pytest.fixture()
def test_app(tmp_path, test_dirs, monkeypatch):
    """Configures the Flask app with an isolated temp DB for each test."""
    db_path = str(tmp_path / "test.db")

    # Patch the module-level DB_PATH so get_db() uses the temp DB
    import app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_path)

    app.config["TESTING"] = True
    app.config["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only"
    app.config["UPLOAD_FOLDER"] = test_dirs["uploads"]
    app.config["PROCESSED_FOLDER"] = test_dirs["processed"]
    app.config["SUMMARIES_FOLDER"] = test_dirs["summaries"]

    monkeypatch.setattr(app_module, "ATTACHMENTS_FOLDER", test_dirs["attachments"])

    with app.app_context():
        init_db()

    yield app


@pytest.fixture()
def client(test_app):
    """Flask test client."""
    return test_app.test_client()


@pytest.fixture()
def db(test_app):
    """Direct DB connection for test data setup/assertions."""
    import app as app_module
    import sqlite3
    conn = sqlite3.connect(app_module.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Auth helper fixtures
# ---------------------------------------------------------------------------
ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "TestAdminP@ssword!Long123"
USER_EMAIL = "user@test.local"
USER_PASSWORD = "TestUserP@ssword!Long456"


def _seed_user(db, email, password, is_admin=0, status="active"):
    db.execute(
        "INSERT INTO users (email, password_hash, display_name, is_admin, status) VALUES (?, ?, ?, ?, ?)",
        (email, generate_password_hash(password, method="scrypt"), email.split("@")[0], is_admin, status),
    )
    db.commit()
    return db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


@pytest.fixture()
def admin_user(db):
    return _seed_user(db, ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=1)


@pytest.fixture()
def regular_user(db):
    return _seed_user(db, USER_EMAIL, USER_PASSWORD, is_admin=0)


def _login(client, email, password):
    resp = client.post("/api/login", json={"email": email, "password": password})
    return resp.get_json()["access_token"]


@pytest.fixture()
def admin_token(client, admin_user):
    return _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture()
def user_token(client, regular_user):
    return _login(client, USER_EMAIL, USER_PASSWORD)


def auth_header(token):
    """Return Authorization header dict for a JWT token."""
    return {"Authorization": f"Bearer {token}"}


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}

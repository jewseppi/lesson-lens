"""
Phase 0 multi-user tests: signup requests, account status enforcement,
admin user management, cross-user data isolation, and security audit logging.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    USER_EMAIL,
    USER_PASSWORD,
    _seed_user,
    auth_header,
)

SUSPENDED_EMAIL = "suspended@test.local"
SUSPENDED_PASSWORD = "SuspendedUserP@ssword!Long789"


# ---------------------------------------------------------------------------
# Signup Requests
# ---------------------------------------------------------------------------
class TestSignupRequests:
    def test_create_signup_request(self, client):
        r = client.post("/api/signup-requests", json={
            "email": "new@example.com",
            "display_name": "New User",
            "reason": "I want to track my lessons",
        })
        assert r.status_code == 201
        assert "submitted" in r.get_json()["message"].lower()

    def test_create_signup_request_missing_email(self, client):
        r = client.post("/api/signup-requests", json={"display_name": "No Email"})
        assert r.status_code == 400

    def test_create_signup_request_invalid_email(self, client):
        r = client.post("/api/signup-requests", json={"email": "not-an-email"})
        assert r.status_code == 400

    def test_create_signup_request_duplicate_pending(self, client):
        email = "dup@example.com"
        client.post("/api/signup-requests", json={"email": email})
        r = client.post("/api/signup-requests", json={"email": email})
        assert r.status_code == 409

    def test_create_signup_request_already_registered(self, client, admin_user):
        r = client.post("/api/signup-requests", json={"email": ADMIN_EMAIL})
        assert r.status_code == 409

    def test_list_signup_requests_admin(self, client, admin_token):
        # Create a request first
        client.post("/api/signup-requests", json={"email": "list@example.com"})
        r = client.get("/api/admin/signup-requests", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert any(req["email"] == "list@example.com" for req in data)

    def test_list_signup_requests_non_admin(self, client, user_token):
        r = client.get("/api/admin/signup-requests", headers=auth_header(user_token))
        assert r.status_code == 403

    def test_approve_signup_request(self, client, db, admin_token):
        client.post("/api/signup-requests", json={"email": "approve@example.com"})
        req = db.execute("SELECT id FROM signup_requests WHERE email = 'approve@example.com'").fetchone()

        r = client.post(f"/api/admin/signup-requests/{req['id']}/approve", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert "invitation_token" in data
        assert data["email"] == "approve@example.com"

        # Verify invitation was created
        invite = db.execute("SELECT * FROM invitation_tokens WHERE email = 'approve@example.com'").fetchone()
        assert invite is not None

        # Verify request status updated
        updated = db.execute("SELECT * FROM signup_requests WHERE id = ?", (req["id"],)).fetchone()
        assert updated["status"] == "approved"

    def test_deny_signup_request(self, client, db, admin_token):
        client.post("/api/signup-requests", json={"email": "deny@example.com"})
        req = db.execute("SELECT id FROM signup_requests WHERE email = 'deny@example.com'").fetchone()

        r = client.post(f"/api/admin/signup-requests/{req['id']}/deny", headers=auth_header(admin_token))
        assert r.status_code == 200

        updated = db.execute("SELECT * FROM signup_requests WHERE id = ?", (req["id"],)).fetchone()
        assert updated["status"] == "denied"

    def test_approve_nonexistent_request(self, client, admin_token):
        r = client.post("/api/admin/signup-requests/99999/approve", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_approve_already_approved(self, client, db, admin_token):
        client.post("/api/signup-requests", json={"email": "double@example.com"})
        req = db.execute("SELECT id FROM signup_requests WHERE email = 'double@example.com'").fetchone()
        client.post(f"/api/admin/signup-requests/{req['id']}/approve", headers=auth_header(admin_token))

        r = client.post(f"/api/admin/signup-requests/{req['id']}/approve", headers=auth_header(admin_token))
        assert r.status_code == 409

    def test_approve_non_admin(self, client, db, user_token):
        r = client.post("/api/admin/signup-requests/1/approve", headers=auth_header(user_token))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Account Status Enforcement
# ---------------------------------------------------------------------------
class TestAccountStatus:
    def test_suspended_user_cannot_login(self, client, db):
        _seed_user(db, SUSPENDED_EMAIL, SUSPENDED_PASSWORD, status="suspended")
        r = client.post("/api/login", json={"email": SUSPENDED_EMAIL, "password": SUSPENDED_PASSWORD})
        assert r.status_code == 403
        assert "not active" in r.get_json()["error"].lower()

    def test_disabled_user_cannot_login(self, client, db):
        email = "disabled@test.local"
        _seed_user(db, email, "DisabledUserP@ssword!Long789", status="disabled")
        r = client.post("/api/login", json={"email": email, "password": "DisabledUserP@ssword!Long789"})
        assert r.status_code == 403

    def test_suspended_user_profile_blocked(self, client, db):
        _seed_user(db, SUSPENDED_EMAIL, SUSPENDED_PASSWORD, status="active")
        # Login while active
        r = client.post("/api/login", json={"email": SUSPENDED_EMAIL, "password": SUSPENDED_PASSWORD})
        token = r.get_json()["access_token"]

        # Suspend the user directly
        db.execute("UPDATE users SET status = 'suspended' WHERE email = ?", (SUSPENDED_EMAIL,))
        db.commit()

        # Profile should now be blocked
        r = client.get("/api/profile", headers=auth_header(token))
        assert r.status_code == 403

    def test_active_user_login_sets_last_login(self, client, db, admin_user):
        r = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        user = db.execute("SELECT last_login_at FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
        assert user["last_login_at"] is not None

    def test_login_returns_status_on_block(self, client, db):
        email = "statuscheck@test.local"
        _seed_user(db, email, "StatusCheckP@ssword!Long789", status="suspended")
        r = client.post("/api/login", json={"email": email, "password": "StatusCheckP@ssword!Long789"})
        assert r.status_code == 403
        assert r.get_json()["status"] == "suspended"


# ---------------------------------------------------------------------------
# Admin: User Management
# ---------------------------------------------------------------------------
class TestAdminUserManagement:
    def test_list_users(self, client, admin_token):
        r = client.get("/api/admin/users", headers=auth_header(admin_token))
        assert r.status_code == 200
        users = r.get_json()
        assert isinstance(users, list)
        assert len(users) >= 1
        assert "status" in users[0]

    def test_list_users_non_admin(self, client, user_token):
        r = client.get("/api/admin/users", headers=auth_header(user_token))
        assert r.status_code == 403

    def test_suspend_user(self, client, db, admin_token, regular_user):
        r = client.post(f"/api/admin/users/{regular_user['id']}/suspend", headers=auth_header(admin_token))
        assert r.status_code == 200
        user = db.execute("SELECT status FROM users WHERE id = ?", (regular_user["id"],)).fetchone()
        assert user["status"] == "suspended"

    def test_suspend_self_blocked(self, client, db, admin_token, admin_user):
        r = client.post(f"/api/admin/users/{admin_user['id']}/suspend", headers=auth_header(admin_token))
        assert r.status_code == 400

    def test_suspend_already_suspended(self, client, db, admin_token, regular_user):
        db.execute("UPDATE users SET status = 'suspended' WHERE id = ?", (regular_user["id"],))
        db.commit()
        r = client.post(f"/api/admin/users/{regular_user['id']}/suspend", headers=auth_header(admin_token))
        assert r.status_code == 409

    def test_suspend_nonexistent_user(self, client, admin_token):
        r = client.post("/api/admin/users/99999/suspend", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_reactivate_user(self, client, db, admin_token, regular_user):
        db.execute("UPDATE users SET status = 'suspended' WHERE id = ?", (regular_user["id"],))
        db.commit()
        r = client.post(f"/api/admin/users/{regular_user['id']}/reactivate", headers=auth_header(admin_token))
        assert r.status_code == 200
        user = db.execute("SELECT status FROM users WHERE id = ?", (regular_user["id"],)).fetchone()
        assert user["status"] == "active"

    def test_reactivate_already_active(self, client, db, admin_token, regular_user):
        r = client.post(f"/api/admin/users/{regular_user['id']}/reactivate", headers=auth_header(admin_token))
        assert r.status_code == 409

    def test_reactivate_non_admin(self, client, user_token):
        r = client.post("/api/admin/users/1/reactivate", headers=auth_header(user_token))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Cross-User Data Isolation
# ---------------------------------------------------------------------------
class TestDataIsolation:
    """Verify that one user cannot access another user's data."""

    def _upload_for_user(self, client, token, filename="chat.txt", content=b"[LINE] Chat export\n2024/01/15 10:00\tTeacher\tHello\n"):
        """Helper to upload a file as a given user."""
        from io import BytesIO
        data = {"file": (BytesIO(content), filename)}
        return client.post("/api/upload", data=data, headers=auth_header(token),
                           content_type="multipart/form-data")

    def test_user_cannot_see_other_uploads(self, client, db, admin_token, user_token):
        # Admin uploads a file
        self._upload_for_user(client, admin_token, "admin_chat.txt")

        # Regular user lists uploads — should not see admin's
        r = client.get("/api/uploads", headers=auth_header(user_token))
        assert r.status_code == 200
        uploads = r.get_json()
        assert not any(u["original_filename"] == "admin_chat.txt" for u in uploads)

    def test_sessions_scoped_to_user(self, client, admin_token, user_token):
        # Each user lists sessions — should be independent
        r1 = client.get("/api/sessions", headers=auth_header(admin_token))
        r2 = client.get("/api/sessions", headers=auth_header(user_token))
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Sessions should only belong to their respective users (empty if no uploads)


# ---------------------------------------------------------------------------
# Security Event Logging
# ---------------------------------------------------------------------------
class TestSecurityEvents:
    def test_login_success_logged(self, client, db, admin_user):
        client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        events = db.execute(
            "SELECT * FROM security_events WHERE event_type = 'login_success'"
        ).fetchall()
        assert len(events) >= 1

    def test_login_failure_logged(self, client, db, admin_user):
        client.post("/api/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        events = db.execute(
            "SELECT * FROM security_events WHERE event_type = 'login_failed'"
        ).fetchall()
        assert len(events) >= 1

    def test_suspended_login_logged(self, client, db):
        _seed_user(db, "blocked@test.local", "BlockedUserP@ssword!Long789", status="suspended")
        client.post("/api/login", json={"email": "blocked@test.local", "password": "BlockedUserP@ssword!Long789"})
        events = db.execute(
            "SELECT * FROM security_events WHERE event_type = 'login_blocked'"
        ).fetchall()
        assert len(events) >= 1

    def test_suspend_action_logged(self, client, db, admin_token, regular_user):
        client.post(f"/api/admin/users/{regular_user['id']}/suspend", headers=auth_header(admin_token))
        events = db.execute(
            "SELECT * FROM security_events WHERE event_type = 'user_suspended'"
        ).fetchall()
        assert len(events) >= 1
        detail = json.loads(events[0]["detail_json"]) if events[0]["detail_json"] != "{}" else {}
        assert events[0]["user_id"] == regular_user["id"]

    def test_signup_request_logged(self, client, db):
        client.post("/api/signup-requests", json={"email": "logged@example.com"})
        events = db.execute(
            "SELECT * FROM security_events WHERE event_type = 'signup_request_created'"
        ).fetchall()
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# Profile includes status
# ---------------------------------------------------------------------------
class TestProfileStatus:
    def test_profile_includes_status(self, client, admin_token):
        r = client.get("/api/profile", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert "status" in data
        assert data["status"] == "active"

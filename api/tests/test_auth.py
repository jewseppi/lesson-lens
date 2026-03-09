"""
Baseline tests for LessonLens auth and access endpoints.
Covers login, registration, profile, change-password, invitation, and admin guards.
"""
import pytest
from datetime import datetime, timedelta, timezone

from tests.conftest import (
    ADMIN_EMAIL, ADMIN_PASSWORD,
    USER_EMAIL, USER_PASSWORD,
    auth_header,
)


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.get_json()
        assert data["status"] == "ok"
        assert "timestamp" in data


class TestLogin:
    def test_login_success(self, client, admin_user):
        r = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.get_json()
        assert "access_token" in data
        assert data["user"]["email"] == ADMIN_EMAIL
        assert data["user"]["is_admin"] is True

    def test_login_wrong_password(self, client, admin_user):
        r = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_login_nonexistent_user(self, client):
        r = client.post("/api/login", json={"email": "nobody@test.local", "password": "anything"})
        assert r.status_code == 401

    def test_login_missing_fields(self, client):
        r = client.post("/api/login", json={})
        assert r.status_code == 401


class TestProfile:
    def test_profile_authenticated(self, client, admin_token):
        r = client.get("/api/profile", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert data["email"] == ADMIN_EMAIL
        assert "is_admin" in data

    def test_profile_unauthenticated(self, client):
        r = client.get("/api/profile")
        assert r.status_code == 401


class TestChangePassword:
    def test_change_password_success(self, client, admin_token):
        new_pw = "BrandNewLongPassword!789"
        r = client.post("/api/change-password", json={
            "current_password": ADMIN_PASSWORD,
            "new_password": new_pw,
            "confirm_password": new_pw,
        }, headers=auth_header(admin_token))
        assert r.status_code == 200
        # Verify new password works
        r2 = client.post("/api/login", json={"email": ADMIN_EMAIL, "password": new_pw})
        assert r2.status_code == 200

    def test_change_password_wrong_current(self, client, admin_token):
        r = client.post("/api/change-password", json={
            "current_password": "wrongcurrent",
            "new_password": "BrandNewLongPassword!789",
            "confirm_password": "BrandNewLongPassword!789",
        }, headers=auth_header(admin_token))
        assert r.status_code == 401

    def test_change_password_mismatch(self, client, admin_token):
        r = client.post("/api/change-password", json={
            "current_password": ADMIN_PASSWORD,
            "new_password": "BrandNewLongPassword!789",
            "confirm_password": "DifferentPassword!789xyz",
        }, headers=auth_header(admin_token))
        assert r.status_code == 400

    def test_change_password_too_weak(self, client, admin_token):
        r = client.post("/api/change-password", json={
            "current_password": ADMIN_PASSWORD,
            "new_password": "short",
            "confirm_password": "short",
        }, headers=auth_header(admin_token))
        assert r.status_code == 400

    def test_change_password_same_as_current(self, client, admin_token):
        r = client.post("/api/change-password", json={
            "current_password": ADMIN_PASSWORD,
            "new_password": ADMIN_PASSWORD,
            "confirm_password": ADMIN_PASSWORD,
        }, headers=auth_header(admin_token))
        assert r.status_code == 400

    def test_change_password_unauthenticated(self, client):
        r = client.post("/api/change-password", json={
            "current_password": "x",
            "new_password": "y",
            "confirm_password": "y",
        })
        assert r.status_code == 401


class TestRegistration:
    def _create_invite(self, db, email, created_by=1):
        import uuid
        token = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        db.execute(
            "INSERT INTO invitation_tokens (email, token, expires_at, created_by) VALUES (?, ?, ?, ?)",
            (email, token, expires, created_by),
        )
        db.commit()
        return token

    def test_register_with_valid_invite(self, client, db, admin_user):
        email = "newuser@test.local"
        token = self._create_invite(db, email, admin_user["id"])
        r = client.post("/api/register", json={
            "email": email,
            "password": "ValidLongSecurePassword!1",
            "display_name": "New User",
            "invitation_token": token,
        })
        assert r.status_code == 201

    def test_register_duplicate_email(self, client, db, admin_user):
        token = self._create_invite(db, ADMIN_EMAIL, admin_user["id"])
        r = client.post("/api/register", json={
            "email": ADMIN_EMAIL,
            "password": "ValidLongSecurePassword!2",
            "invitation_token": token,
        })
        assert r.status_code == 409

    def test_register_invalid_invite(self, client):
        r = client.post("/api/register", json={
            "email": "anyone@test.local",
            "password": "ValidLongSecurePassword!3",
            "invitation_token": "bogus-token",
        })
        assert r.status_code == 403

    def test_register_expired_invite(self, client, db, admin_user):
        email = "expired@test.local"
        import uuid
        token = str(uuid.uuid4())
        expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        db.execute(
            "INSERT INTO invitation_tokens (email, token, expires_at, created_by) VALUES (?, ?, ?, ?)",
            (email, token, expired, admin_user["id"]),
        )
        db.commit()
        r = client.post("/api/register", json={
            "email": email,
            "password": "ValidLongSecurePassword!4",
            "invitation_token": token,
        })
        assert r.status_code == 403

    def test_register_weak_password(self, client, db, admin_user):
        email = "weakpw@test.local"
        token = self._create_invite(db, email, admin_user["id"])
        r = client.post("/api/register", json={
            "email": email,
            "password": "short",
            "invitation_token": token,
        })
        assert r.status_code == 400

    def test_register_missing_fields(self, client):
        r = client.post("/api/register", json={})
        assert r.status_code == 400


class TestInvitationAdmin:
    def test_create_invitation_as_admin(self, client, admin_token):
        r = client.post("/api/admin/invitations", json={
            "email": "invited@test.local",
        }, headers=auth_header(admin_token))
        assert r.status_code == 201
        data = r.get_json()
        assert "token" in data
        assert data["email"] == "invited@test.local"

    def test_create_invitation_as_non_admin(self, client, user_token):
        r = client.post("/api/admin/invitations", json={
            "email": "invited2@test.local",
        }, headers=auth_header(user_token))
        assert r.status_code == 403

    def test_create_invitation_unauthenticated(self, client):
        r = client.post("/api/admin/invitations", json={"email": "x@x.com"})
        assert r.status_code == 401


class TestAnalytics:
    def test_track_event(self, client, admin_token):
        r = client.post("/api/analytics/event", json={
            "event_type": "test_event",
            "event_data": {"key": "value"},
        }, headers=auth_header(admin_token))
        assert r.status_code == 201

    def test_analytics_summary_admin(self, client, admin_token):
        # Seed an event first
        client.post("/api/analytics/event", json={
            "event_type": "test_summary",
        }, headers=auth_header(admin_token))
        r = client.get("/api/analytics/summary", headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert "events_by_type" in data
        assert "total_users" in data

    def test_analytics_summary_non_admin(self, client, user_token):
        r = client.get("/api/analytics/summary", headers=auth_header(user_token))
        assert r.status_code == 403

    def test_analytics_unauthenticated(self, client):
        r = client.get("/api/analytics/summary")
        assert r.status_code == 401


class TestPasswordValidation:
    def test_too_short(self):
        from app import validate_password_strength
        errs = validate_password_strength("short")
        assert any("16" in e for e in errs)

    def test_common_password(self):
        from app import validate_password_strength
        errs = validate_password_strength("adminpassword1")
        assert any("common" in e.lower() for e in errs)

    def test_all_digits(self):
        from app import validate_password_strength
        errs = validate_password_strength("1234567890123456")
        assert any("number" in e.lower() for e in errs)

    def test_contains_email(self):
        from app import validate_password_strength
        errs = validate_password_strength("longpasswordadminuser12", email="adminuser@test.com")
        assert any("email" in e.lower() or "name" in e.lower() for e in errs)

    def test_valid_password(self):
        from app import validate_password_strength
        errs = validate_password_strength("Str0ng!Unique#Pass9876")
        assert errs == []


class TestSecurityHeaders:
    def test_security_headers_present(self, client):
        r = client.get("/api/health")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"

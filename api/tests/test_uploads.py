"""
Tests for upload, uploads list, sessions, and summary endpoints.
"""
import io
import json
import os

import pytest

from tests.conftest import auth_header


class TestUpload:
    def test_upload_txt(self, client, admin_token, test_app):
        data = {"file": (io.BytesIO(b"line1\nline2\nline3\n"), "export.txt")}
        r = client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["duplicate"] is False
        assert body["line_count"] == 3
        assert body["upload_id"] > 0

    def test_upload_extensionless(self, client, admin_token):
        data = {"file": (io.BytesIO(b"some data\n"), "chatexport")}
        r = client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 201

    def test_upload_bad_extension(self, client, admin_token):
        data = {"file": (io.BytesIO(b"data"), "file.csv")}
        r = client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "txt" in r.get_json()["error"].lower()

    def test_upload_no_file(self, client, admin_token):
        r = client.post(
            "/api/upload",
            data={},
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 400

    def test_upload_duplicate(self, client, admin_token):
        content = b"identical content for dedup test\n"
        data1 = {"file": (io.BytesIO(content), "first.txt")}
        r1 = client.post(
            "/api/upload",
            data=data1,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r1.status_code == 201

        data2 = {"file": (io.BytesIO(content), "second.txt")}
        r2 = client.post(
            "/api/upload",
            data=data2,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        assert r2.status_code == 200
        assert r2.get_json()["duplicate"] is True
        assert r2.get_json()["upload_id"] == r1.get_json()["upload_id"]

    def test_upload_unauthenticated(self, client):
        data = {"file": (io.BytesIO(b"data"), "file.txt")}
        r = client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert r.status_code == 401


class TestListUploads:
    def test_list_empty(self, client, admin_token):
        r = client.get("/api/uploads", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_list_after_upload(self, client, admin_token):
        data = {"file": (io.BytesIO(b"hello\n"), "test.txt")}
        client.post(
            "/api/upload",
            data=data,
            content_type="multipart/form-data",
            headers=auth_header(admin_token),
        )
        r = client.get("/api/uploads", headers=auth_header(admin_token))
        assert r.status_code == 200
        uploads = r.get_json()
        assert len(uploads) == 1
        assert uploads[0]["original_filename"] == "test.txt"

    def test_list_unauthenticated(self, client):
        r = client.get("/api/uploads")
        assert r.status_code == 401


class TestSessions:
    def test_list_sessions_no_data(self, client, admin_token):
        r = client.get("/api/sessions", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_get_session_no_data(self, client, admin_token):
        r = client.get("/api/sessions/fake-id", headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_sessions_unauthenticated(self, client):
        r = client.get("/api/sessions")
        assert r.status_code == 401


class TestSummary:
    def test_get_summary_not_found(self, client, admin_token):
        r = client.get(
            "/api/sessions/nonexistent/summary",
            headers=auth_header(admin_token),
        )
        assert r.status_code == 404
        assert "No summary" in r.get_json()["error"]

    def test_summary_unauthenticated(self, client):
        r = client.get("/api/sessions/x/summary")
        assert r.status_code == 401

    def test_get_summary_exists(self, client, db, admin_user, admin_token):
        """Seed a summary directly and verify retrieval."""
        # Seed upload first (FK requirement)
        db.execute(
            "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, 'test.txt', 'stored.txt', 'abc123', 100, 10)",
            (admin_user["id"],),
        )
        upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Seed parse_run and session
        db.execute(
            "INSERT INTO parse_runs (run_id, upload_id, user_id, status) VALUES (?, ?, ?, 'completed')",
            ("test-run", upload_id, admin_user["id"]),
        )
        db.execute(
            "INSERT INTO sessions (run_id, session_id, date, message_count) VALUES ('test-run', 'sess-1', '2025-01-01', 5)"
        )
        sess_row = db.execute("SELECT id FROM sessions WHERE session_id = 'sess-1'").fetchone()
        lesson_data = {"title": "Test Lesson", "vocabulary": []}
        db.execute(
            "INSERT INTO lesson_summaries (session_db_id, run_id, session_id, user_id, lesson_data_json) VALUES (?, 'test-run', 'sess-1', ?, ?)",
            (sess_row["id"], admin_user["id"], json.dumps(lesson_data)),
        )
        db.commit()

        r = client.get("/api/sessions/sess-1/summary", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json()["title"] == "Test Lesson"


class TestImportSummary:
    def test_import_unauthenticated(self, client):
        r = client.post("/api/sessions/x/summary/import")
        assert r.status_code == 401


class TestComputeFileHash:
    def test_hash_consistency(self, tmp_path):
        from app import compute_file_hash
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        h1 = compute_file_hash(str(f))
        h2 = compute_file_hash(str(f))
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_different_content(self, tmp_path):
        from app import compute_file_hash
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert compute_file_hash(str(f1)) != compute_file_hash(str(f2))


class TestBackupExport:
    def test_export_unauthenticated(self, client):
        r = client.get("/api/backup/export")
        assert r.status_code == 401


class TestRateLimit:
    def test_rate_limit_bypassed_in_testing(self, client, admin_user):
        """In TESTING mode, rate limits should be bypassed."""
        # Login multiple times quickly — should not be blocked
        for _ in range(35):
            r = client.post("/api/login", json={
                "email": "admin@test.local",
                "password": "TestAdminP@ssword!Long123",
            })
            assert r.status_code == 200


class TestSPA:
    def test_spa_serves_index_or_404(self, client):
        """SPA root either serves index.html (if built) or 404."""
        r = client.get("/")
        assert r.status_code in (200, 404)

    def test_unknown_api_route(self, client):
        """Non-existent /api/ paths should return 404."""
        r = client.get("/api/nonexistent")
        assert r.status_code == 404

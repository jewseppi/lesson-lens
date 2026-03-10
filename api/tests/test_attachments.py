"""
Tests for Phase 0A — Image Context Ingestion.
Covers: EXIF extraction, session matching, attachment upload/list/assign/unassign endpoints.
"""
import io
import json
import os
import struct
import tempfile

import pytest
from PIL import Image

from tests.conftest import USER_EMAIL, USER_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_test_image(width=100, height=100, fmt="JPEG", exif=None):
    """Create a test image in memory, optionally with EXIF data."""
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    if exif and fmt == "JPEG":
        from PIL.ExifTags import Base as ExifBase
        import piexif
    # For simplicity, save without piexif — we'll test EXIF via file-based approach
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf


def _make_jpeg_with_exif_datetime(dt_str, offset_str=None):
    """Create minimal JPEG with EXIF DateTimeOriginal."""
    img = Image.new("RGB", (10, 10), color="blue")
    from PIL import Image as PILImage

    # Build minimal EXIF using Pillow's built-in capabilities
    exif_data = img.getexif()
    # Tag 36867 = DateTimeOriginal
    exif_data[36867] = dt_str
    # Tag 306 = DateTime
    exif_data[306] = dt_str
    if offset_str:
        # Tag 36881 = OffsetTimeOriginal
        exif_data[36881] = offset_str

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif_data.tobytes())
    buf.seek(0)
    return buf


def _seed_sessions(db, user_id, run_id="test_run_001"):
    """Insert a dummy upload, parse_run, and some sessions for matching tests."""
    # Need a dummy upload since parse_runs.upload_id is NOT NULL
    db.execute(
        "INSERT INTO uploads (user_id, original_filename, stored_filename, file_hash, file_size, line_count) VALUES (?, 'test.txt', 'test_stored.txt', 'fakehash', 100, 10)",
        (user_id,),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.execute(
        """INSERT INTO parse_runs
           (run_id, upload_id, user_id, status, session_count, message_count, output_dir, completed_at)
           VALUES (?, ?, ?, 'completed', 3, 30, '/tmp/test', datetime('now'))""",
        (run_id, upload_id, user_id),
    )
    sessions = [
        ("sess_1", "2024-01-15", "09:00:00", "10:30:00", 10),
        ("sess_2", "2024-01-15", "14:00:00", "15:30:00", 12),
        ("sess_3", "2024-01-16", "10:00:00", "11:00:00", 8),
    ]
    for sid, date, start, end, mc in sessions:
        db.execute(
            """INSERT INTO sessions
               (run_id, user_id, session_id, date, start_time, end_time, message_count, lesson_content_count, boundary_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'high')""",
            (run_id, user_id, sid, date, start, end, mc),
        )
    db.commit()


# ---------------------------------------------------------------------------
# image_helpers unit tests
# ---------------------------------------------------------------------------
class TestExifExtraction:
    def test_extract_from_jpeg_with_exif(self, tmp_path):
        """EXIF DateTimeOriginal is extracted correctly."""
        from image_helpers import extract_exif_datetime

        img_buf = _make_jpeg_with_exif_datetime("2024:01:15 09:30:00")
        filepath = str(tmp_path / "test.jpg")
        with open(filepath, "wb") as f:
            f.write(img_buf.read())

        result = extract_exif_datetime(filepath)
        assert result["source"] == "exif"
        assert result["captured_at_local"] == "2024-01-15T09:30:00"

    def test_extract_with_timezone_offset(self, tmp_path):
        """EXIF with OffsetTimeOriginal produces UTC time."""
        from image_helpers import extract_exif_datetime

        img_buf = _make_jpeg_with_exif_datetime("2024:01:15 18:30:00", "+09:00")
        filepath = str(tmp_path / "test_tz.jpg")
        with open(filepath, "wb") as f:
            f.write(img_buf.read())

        result = extract_exif_datetime(filepath)
        assert result["source"] == "exif"
        assert result["timezone_hint"] == "+09:00"
        assert result["captured_at_utc"] is not None
        assert "09:30:00" in result["captured_at_utc"]

    def test_extract_from_filename_pattern(self, tmp_path):
        """Filename timestamp patterns are detected as fallback."""
        from image_helpers import extract_exif_datetime

        # Create image with no EXIF
        img = Image.new("RGB", (10, 10), "green")
        filepath = str(tmp_path / "IMG_20240115_143022.png")
        img.save(filepath, format="PNG")

        result = extract_exif_datetime(filepath)
        assert result["source"] == "filename"
        assert result["captured_at_local"] == "2024-01-15T14:30:22"

    def test_extract_dash_filename_pattern(self, tmp_path):
        """Dash-separated filename patterns work."""
        from image_helpers import extract_exif_datetime

        img = Image.new("RGB", (10, 10), "green")
        filepath = str(tmp_path / "2024-01-16-10-15-30.jpg")
        img.save(filepath, format="JPEG")

        result = extract_exif_datetime(filepath)
        assert result["source"] == "filename"
        assert result["captured_at_local"] == "2024-01-16T10:15:30"

    def test_extract_mtime_fallback(self, tmp_path):
        """Falls back to filesystem mtime when no EXIF or filename pattern."""
        from image_helpers import extract_exif_datetime

        img = Image.new("RGB", (10, 10), "green")
        filepath = str(tmp_path / "random_name.png")
        img.save(filepath, format="PNG")

        result = extract_exif_datetime(filepath)
        assert result["source"] == "mtime"
        assert result["captured_at_local"] is not None
        assert result["captured_at_utc"] is not None

    def test_is_image_file(self):
        from image_helpers import is_image_file
        assert is_image_file("photo.jpg")
        assert is_image_file("PHOTO.JPEG")
        assert is_image_file("screen.png")
        assert is_image_file("image.heic")
        assert not is_image_file("document.txt")
        assert not is_image_file("archive.zip")
        assert not is_image_file("data.csv")


class TestSessionMatching:
    def test_high_confidence_within_window(self):
        from image_helpers import match_image_to_sessions
        sessions = [
            {"session_id": "s1", "date": "2024-01-15", "start_time": "09:00:00", "end_time": "10:30:00"},
        ]
        result = match_image_to_sessions("2024-01-15T09:45:00", sessions)
        assert result["confidence"] == "high"
        assert result["session_id"] == "s1"

    def test_medium_confidence_within_margin(self):
        from image_helpers import match_image_to_sessions
        sessions = [
            {"session_id": "s1", "date": "2024-01-15", "start_time": "09:00:00", "end_time": "10:30:00"},
        ]
        # 1 hour before session start — within 2h margin
        result = match_image_to_sessions("2024-01-15T08:00:00", sessions)
        assert result["confidence"] == "medium"
        assert result["session_id"] == "s1"

    def test_low_confidence_same_date(self):
        from image_helpers import match_image_to_sessions
        sessions = [
            {"session_id": "s1", "date": "2024-01-15", "start_time": "09:00:00", "end_time": "10:30:00"},
        ]
        # Same date but way outside margin
        result = match_image_to_sessions("2024-01-15T23:00:00", sessions)
        assert result["confidence"] == "low"
        assert result["session_id"] == "s1"

    def test_unmatched_different_date(self):
        from image_helpers import match_image_to_sessions
        sessions = [
            {"session_id": "s1", "date": "2024-01-15", "start_time": "09:00:00", "end_time": "10:30:00"},
        ]
        result = match_image_to_sessions("2024-02-20T12:00:00", sessions)
        assert result["confidence"] == "unmatched"

    def test_unmatched_no_timestamp(self):
        from image_helpers import match_image_to_sessions
        result = match_image_to_sessions(None, [])
        assert result["confidence"] == "unmatched"
        assert result["reason"] == "no_timestamp"

    def test_picks_best_match(self):
        from image_helpers import match_image_to_sessions
        sessions = [
            {"session_id": "s1", "date": "2024-01-15", "start_time": "09:00:00", "end_time": "10:30:00"},
            {"session_id": "s2", "date": "2024-01-15", "start_time": "14:00:00", "end_time": "15:30:00"},
        ]
        # Within s2's window
        result = match_image_to_sessions("2024-01-15T14:30:00", sessions)
        assert result["confidence"] == "high"
        assert result["session_id"] == "s2"

    def test_custom_margin(self):
        from image_helpers import match_image_to_sessions
        sessions = [
            {"session_id": "s1", "date": "2024-01-15", "start_time": "09:00:00", "end_time": "10:30:00"},
        ]
        # 3 hours before — outside 2h margin but inside 4h margin
        result = match_image_to_sessions("2024-01-15T06:00:00", sessions, margin_hours=4)
        assert result["confidence"] == "medium"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------
class TestUploadAttachments:
    def test_upload_single_image(self, client, user_token, db, regular_user):
        """Upload a single image and verify attachment creation."""
        _seed_sessions(db, regular_user["id"])
        img = _make_test_image()
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "photo.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert len(data["attachments"]) == 1
        att = data["attachments"][0]
        assert att["status"] == "created"
        assert att["attachment_id"] is not None

    def test_upload_multiple_images(self, client, user_token, db, regular_user):
        """Upload multiple images at once."""
        _seed_sessions(db, regular_user["id"])
        img1 = _make_test_image()
        img2 = _make_test_image(width=50, height=50)
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": [(img1, "photo1.jpg"), (img2, "photo2.jpg")]},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert len(data["attachments"]) == 2

    def test_upload_unsupported_format(self, client, user_token, db, regular_user):
        """Non-image files are rejected."""
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (io.BytesIO(b"hello"), "document.txt")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["attachments"][0]["error"] == "unsupported_format"

    def test_upload_duplicate_detection(self, client, user_token, db, regular_user):
        """Same image uploaded twice results in duplicate status."""
        _seed_sessions(db, regular_user["id"])
        img_data = _make_test_image().read()

        # First upload
        resp1 = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (io.BytesIO(img_data), "photo.jpg")},
            content_type="multipart/form-data",
        )
        assert resp1.status_code == 201

        # Second upload (same content)
        resp2 = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (io.BytesIO(img_data), "photo_copy.jpg")},
            content_type="multipart/form-data",
        )
        assert resp2.status_code == 201
        assert resp2.get_json()["attachments"][0]["status"] == "duplicate"

    def test_upload_no_images(self, client, user_token):
        """Empty upload returns error."""
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_requires_auth(self, client):
        """Unauthenticated upload is rejected."""
        resp = client.post("/api/attachments/upload")
        assert resp.status_code == 401

    def test_upload_with_exif_auto_matches(self, client, user_token, db, regular_user):
        """Image with EXIF timestamp auto-matches to correct session."""
        _seed_sessions(db, regular_user["id"])

        # Create image with timestamp matching sess_1 (2024-01-15 09:00-10:30)
        img = _make_jpeg_with_exif_datetime("2024:01:15 09:30:00")

        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "lesson_photo.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        att = resp.get_json()["attachments"][0]
        assert att["match"]["confidence"] == "high"
        assert att["match"]["session_id"] == "sess_1"


class TestListAttachments:
    def test_list_all(self, client, user_token, db, regular_user):
        """List all user attachments."""
        _seed_sessions(db, regular_user["id"])
        img = _make_test_image()
        client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "photo.jpg")},
            content_type="multipart/form-data",
        )

        resp = client.get(
            "/api/attachments",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["attachments"]) == 1

    def test_list_unmatched(self, client, user_token, db, regular_user):
        """Filter unmatched attachments."""
        # Upload without any sessions (no auto-match possible)
        img = _make_test_image()
        client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "orphan.jpg")},
            content_type="multipart/form-data",
        )

        resp = client.get(
            "/api/attachments?filter=unmatched",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["attachments"]) >= 1

    def test_list_requires_auth(self, client):
        resp = client.get("/api/attachments")
        assert resp.status_code == 401


class TestServeAttachmentImage:
    def test_serve_own_image(self, client, user_token, db, regular_user):
        """User can fetch their own uploaded image."""
        img_data = _make_test_image().read()
        client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (io.BytesIO(img_data), "photo.jpg")},
            content_type="multipart/form-data",
        )

        # Get the attachment id
        resp = client.get(
            "/api/attachments",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        att_id = resp.get_json()["attachments"][0]["id"]

        # Serve the image
        resp = client.get(
            f"/api/attachments/{att_id}/image",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        assert "image" in resp.content_type

    def test_cannot_serve_other_users_image(self, client, db, user_token, admin_token, regular_user, admin_user):
        """User cannot access another user's attachment."""
        # Upload as regular user
        img = _make_test_image()
        client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "private.jpg")},
            content_type="multipart/form-data",
        )
        resp = client.get(
            "/api/attachments",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        att_id = resp.get_json()["attachments"][0]["id"]

        # Try to access as admin (different user)
        resp = client.get(
            f"/api/attachments/{att_id}/image",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_serve_nonexistent(self, client, user_token):
        resp = client.get(
            "/api/attachments/99999/image",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 404


class TestSessionAttachments:
    def test_get_session_attachments(self, client, user_token, db, regular_user):
        """Get attachments linked to a session."""
        _seed_sessions(db, regular_user["id"])
        img = _make_jpeg_with_exif_datetime("2024:01:15 09:30:00")
        client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "lesson.jpg")},
            content_type="multipart/form-data",
        )

        resp = client.get(
            "/api/sessions/sess_1/attachments",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["attachments"]) >= 1
        assert data["attachments"][0]["match_confidence"] == "high"

    def test_empty_session_attachments(self, client, user_token, db, regular_user):
        """Session with no attachments returns empty list."""
        _seed_sessions(db, regular_user["id"])
        resp = client.get(
            "/api/sessions/sess_1/attachments",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["attachments"] == []


class TestAssignAttachment:
    def test_manual_assign(self, client, user_token, db, regular_user):
        """Manually assign an unmatched attachment to a session."""
        _seed_sessions(db, regular_user["id"])
        img = _make_test_image()
        upload_resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "photo.jpg")},
            content_type="multipart/form-data",
        )
        att_id = upload_resp.get_json()["attachments"][0]["attachment_id"]

        resp = client.post(
            "/api/sessions/sess_2/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"attachment_id": att_id},
        )
        assert resp.status_code == 201
        assert resp.get_json()["session_attachment_id"] is not None

    def test_assign_duplicate(self, client, user_token, db, regular_user):
        """Assigning same attachment to same session twice fails."""
        _seed_sessions(db, regular_user["id"])
        img = _make_test_image()
        upload_resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "photo.jpg")},
            content_type="multipart/form-data",
        )
        att_id = upload_resp.get_json()["attachments"][0]["attachment_id"]

        # First assign
        client.post(
            "/api/sessions/sess_2/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"attachment_id": att_id},
        )
        # Second assign
        resp = client.post(
            "/api/sessions/sess_2/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"attachment_id": att_id},
        )
        assert resp.status_code == 409

    def test_assign_missing_attachment(self, client, user_token, db, regular_user):
        _seed_sessions(db, regular_user["id"])
        resp = client.post(
            "/api/sessions/sess_1/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"attachment_id": 99999},
        )
        assert resp.status_code == 404

    def test_assign_missing_session(self, client, user_token, db, regular_user):
        img = _make_test_image()
        upload_resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "photo.jpg")},
            content_type="multipart/form-data",
        )
        att_id = upload_resp.get_json()["attachments"][0]["attachment_id"]

        resp = client.post(
            "/api/sessions/nonexistent/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"attachment_id": att_id},
        )
        assert resp.status_code == 404

    def test_assign_no_body(self, client, user_token, db, regular_user):
        _seed_sessions(db, regular_user["id"])
        resp = client.post(
            "/api/sessions/sess_1/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={},
        )
        assert resp.status_code == 400


class TestUnassignAttachment:
    def test_unassign(self, client, user_token, db, regular_user):
        """Remove an attachment from a session."""
        _seed_sessions(db, regular_user["id"])
        img = _make_test_image()
        upload_resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "photo.jpg")},
            content_type="multipart/form-data",
        )
        att_id = upload_resp.get_json()["attachments"][0]["attachment_id"]

        # Assign first
        client.post(
            "/api/sessions/sess_2/attachments/assign",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"attachment_id": att_id},
        )

        # Then unassign
        resp = client.delete(
            f"/api/sessions/sess_2/attachments/{att_id}",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200

        # Verify removed
        resp = client.get(
            "/api/sessions/sess_2/attachments",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert len(resp.get_json()["attachments"]) == 0

    def test_unassign_nonexistent(self, client, user_token, db, regular_user):
        _seed_sessions(db, regular_user["id"])
        resp = client.delete(
            "/api/sessions/sess_1/attachments/99999",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 404


class TestAttachmentUserScoping:
    def test_user_isolation(self, client, db, user_token, admin_token, regular_user, admin_user):
        """User A's attachments are invisible to User B."""
        # Upload as regular user
        img = _make_test_image()
        client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (img, "user_photo.jpg")},
            content_type="multipart/form-data",
        )

        # Admin lists their attachments — should be empty
        resp = client.get(
            "/api/attachments",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["attachments"]) == 0

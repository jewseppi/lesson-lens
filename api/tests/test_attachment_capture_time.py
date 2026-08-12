"""Tests for capture-time preservation, magic-byte sniffing, and re-matching.

An upload transmits bytes only, so the server's copy of an image is always
stamped "just now". LINE strips EXIF from received photos and stores media under
hashed, extension-less names — so without the fixes covered here, every image
the macOS updater finds is either rejected outright or lands with a useless
capture time and never matches a lesson.
"""
import io
import json
import os

from PIL import Image

from image_helpers import extract_exif_datetime, sniff_image_extension


def _png_bytes(color="green"):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color).save(buf, format="JPEG")
    return buf.getvalue()


def _seed_run(db, user_id, run_id="captime_run", sessions=None):
    """Seed an upload + completed run + sessions, mirroring test_attachments."""
    db.execute(
        """INSERT INTO uploads
           (user_id, original_filename, stored_filename, file_hash, file_size, line_count)
           VALUES (?, 'chat.txt', ?, ?, 100, 10)""",
        (user_id, f"{run_id}.txt", f"hash-{run_id}"),
    )
    upload_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """INSERT INTO parse_runs
           (run_id, upload_id, user_id, status, session_count, message_count,
            output_dir, completed_at)
           VALUES (?, ?, ?, 'completed', ?, 30, '/tmp/test', datetime('now'))""",
        (run_id, upload_id, user_id, len(sessions or [])),
    )
    for sid, date, start, end in sessions or []:
        db.execute(
            """INSERT INTO sessions
               (run_id, user_id, session_id, date, start_time, end_time,
                message_count, lesson_content_count, boundary_confidence)
               VALUES (?, ?, ?, ?, ?, ?, 10, 0, 'high')""",
            (run_id, user_id, sid, date, start, end),
        )
    db.commit()
    return run_id


# ---------------------------------------------------------------------------
# Magic-byte sniffing
# ---------------------------------------------------------------------------
class TestSniffImageExtension:
    def test_detects_jpeg(self):
        assert sniff_image_extension(_jpeg_bytes()[:32]) == ".jpg"

    def test_detects_png(self):
        assert sniff_image_extension(_png_bytes()[:32]) == ".png"

    def test_detects_gif(self):
        assert sniff_image_extension(b"GIF89a" + b"\x00" * 20) == ".gif"

    def test_detects_bmp(self):
        assert sniff_image_extension(b"BM" + b"\x00" * 20) == ".bmp"

    def test_detects_webp(self):
        header = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
        assert sniff_image_extension(header) == ".webp"

    def test_detects_heic(self):
        header = b"\x00\x00\x00\x18" + b"ftyp" + b"heic" + b"\x00" * 8
        assert sniff_image_extension(header) == ".heic"

    def test_rejects_text(self):
        assert sniff_image_extension(b"hello world, not an image") is None

    def test_handles_empty_and_none(self):
        assert sniff_image_extension(b"") is None
        assert sniff_image_extension(None) is None


# ---------------------------------------------------------------------------
# extract_exif_datetime: client-supplied source timestamp
# ---------------------------------------------------------------------------
class TestSourceTimestamp:
    def test_client_timestamp_used_when_no_exif(self, tmp_path):
        path = str(tmp_path / "hashed_no_ext")
        with open(path, "wb") as fh:
            fh.write(_png_bytes())

        result = extract_exif_datetime(path, source_timestamp="2026-08-05T10:06:30")
        assert result["source"] == "source_mtime"
        assert result["captured_at_local"] == "2026-08-05T10:06:30"

    def test_exif_wins_over_client_timestamp(self, tmp_path):
        img = Image.new("RGB", (10, 10), "blue")
        exif = img.getexif()
        exif[36867] = "2024:01:15 09:30:00"
        path = str(tmp_path / "with_exif.jpg")
        img.save(path, format="JPEG", exif=exif.tobytes())

        result = extract_exif_datetime(path, source_timestamp="2026-08-05T10:06:30")
        assert result["source"] == "exif"
        assert result["captured_at_local"] == "2024-01-15T09:30:00"

    def test_filename_pattern_wins_over_client_timestamp(self, tmp_path):
        path = str(tmp_path / "IMG_20240115_143022.png")
        with open(path, "wb") as fh:
            fh.write(_png_bytes())

        result = extract_exif_datetime(path, source_timestamp="2026-08-05T10:06:30")
        assert result["source"] == "filename"

    def test_aware_client_timestamp_is_converted_to_naive_local(self, tmp_path):
        path = str(tmp_path / "aware")
        with open(path, "wb") as fh:
            fh.write(_png_bytes())

        result = extract_exif_datetime(
            path, source_timestamp="2026-08-05T10:06:30+00:00"
        )
        assert result["source"] == "source_mtime"
        # Session windows are naive local times, so the stored value must be too.
        assert "+" not in result["captured_at_local"]
        assert result["captured_at_utc"] == "2026-08-05T10:06:30+00:00"

    def test_garbage_client_timestamp_falls_through_to_mtime(self, tmp_path):
        path = str(tmp_path / "garbage")
        with open(path, "wb") as fh:
            fh.write(_png_bytes())

        result = extract_exif_datetime(path, source_timestamp="not-a-date")
        assert result["source"] == "mtime"
        assert result["captured_at_local"] is not None

    def test_mtime_fallback_local_is_not_utc(self, tmp_path):
        """captured_at_local must be local wall-clock, not a UTC instant.

        Sessions are stored as naive local times, so stamping UTC here silently
        shifts every image out of its lesson for anyone not living on UTC.
        """
        path = str(tmp_path / "plain")
        with open(path, "wb") as fh:
            fh.write(_png_bytes())
        os.utime(path, (1_754_388_390, 1_754_388_390))

        result = extract_exif_datetime(path)
        assert result["source"] == "mtime"
        from datetime import datetime

        expected = datetime.fromtimestamp(1_754_388_390).isoformat()
        assert result["captured_at_local"] == expected


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------
class TestUploadCaptureTime:
    def test_extensionless_upload_is_sniffed_and_matched(
        self, client, user_token, db, regular_user
    ):
        """The real LINE case: hashed name, no extension, no EXIF."""
        _seed_run(
            db, regular_user["id"],
            sessions=[("2026-08-05", "2026-08-05", "10:00", "10:20")],
        )
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={
                "images": (io.BytesIO(_jpeg_bytes()), "abc123def456"),
                "source_timestamps": json.dumps(["2026-08-05T10:06:30"]),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        att = resp.get_json()["attachments"][0]
        assert att["status"] == "created"
        assert att["timestamp_source"] == "source_mtime"
        assert att["captured_at_local"] == "2026-08-05T10:06:30"
        assert att["match"]["session_id"] == "2026-08-05"
        assert att["match"]["confidence"] == "high"

    def test_timestamps_line_up_with_files_in_order(
        self, client, user_token, db, regular_user
    ):
        _seed_run(
            db, regular_user["id"],
            sessions=[
                ("2026-08-05", "2026-08-05", "10:00", "10:20"),
                ("2026-08-12", "2026-08-12", "10:00", "10:15"),
            ],
        )
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={
                "images": [
                    (io.BytesIO(_jpeg_bytes("red")), "first"),
                    (io.BytesIO(_png_bytes("green")), "second"),
                ],
                "source_timestamps": json.dumps(
                    ["2026-08-05T10:05:00", "2026-08-12T10:05:00"]
                ),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        atts = resp.get_json()["attachments"]
        assert [a["match"]["session_id"] for a in atts] == ["2026-08-05", "2026-08-12"]

    def test_malformed_timestamps_field_is_ignored(
        self, client, user_token, db, regular_user
    ):
        """A bad field must not break the upload — the image still lands."""
        _seed_run(db, regular_user["id"], sessions=[])
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={
                "images": (io.BytesIO(_jpeg_bytes()), "photo.jpg"),
                "source_timestamps": "{not json",
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        assert resp.get_json()["attachments"][0]["status"] == "created"

    def test_missing_timestamps_field_still_works(
        self, client, user_token, db, regular_user
    ):
        _seed_run(db, regular_user["id"], sessions=[])
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (io.BytesIO(_jpeg_bytes()), "photo.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        assert resp.get_json()["attachments"][0]["status"] == "created"

    def test_sniffed_upload_gets_a_real_mime_type(
        self, client, user_token, db, regular_user
    ):
        _seed_run(db, regular_user["id"], sessions=[])
        resp = client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {user_token}"},
            data={"images": (io.BytesIO(_png_bytes()), "no_extension_here")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        attachment_id = resp.get_json()["attachments"][0]["attachment_id"]
        row = db.execute(
            "SELECT mime_type, stored_filename FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        assert row["mime_type"] == "image/png"
        assert row["stored_filename"].endswith(".png")


# ---------------------------------------------------------------------------
# Re-match endpoint
# ---------------------------------------------------------------------------
class TestRematchAttachments:
    def _upload(self, client, token, name, timestamp, data=None):
        return client.post(
            "/api/attachments/upload",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "images": (io.BytesIO(data or _jpeg_bytes()), name),
                "source_timestamps": json.dumps([timestamp]),
            },
            content_type="multipart/form-data",
        )

    def test_orphan_matches_once_its_lesson_arrives(
        self, client, user_token, db, regular_user
    ):
        """The ordering that actually happens: photo cached, export taken later."""
        _seed_run(
            db, regular_user["id"], run_id="run_one",
            sessions=[("2026-08-05", "2026-08-05", "10:00", "10:20")],
        )
        resp = self._upload(
            client, user_token, "orphan", "2026-08-12T10:06:20",
            data=_png_bytes("blue"),
        )
        att = resp.get_json()["attachments"][0]
        assert att["match"]["session_id"] is None

        # The later export lands, bringing the lesson that explains the photo.
        _seed_run(
            db, regular_user["id"], run_id="run_two",
            sessions=[
                ("2026-08-05", "2026-08-05", "10:00", "10:20"),
                ("2026-08-12", "2026-08-12", "10:00", "10:15"),
            ],
        )
        resp = client.post(
            "/api/attachments/rematch",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["matched"] == 1
        assert body["matches"][0]["session_id"] == "2026-08-12"
        assert body["matches"][0]["confidence"] == "high"

    def test_rematch_is_idempotent(self, client, user_token, db, regular_user):
        _seed_run(
            db, regular_user["id"],
            sessions=[("2026-08-05", "2026-08-05", "10:00", "10:20")],
        )
        self._upload(client, user_token, "in_window", "2026-08-05T10:06:30")
        for _ in range(2):
            resp = client.post(
                "/api/attachments/rematch",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert resp.status_code == 200
            # Already linked at upload time, so there is nothing left to do.
            assert resp.get_json()["matched"] == 0

    def test_rematch_leaves_genuine_orphans_alone(
        self, client, user_token, db, regular_user
    ):
        _seed_run(
            db, regular_user["id"],
            sessions=[("2026-08-05", "2026-08-05", "10:00", "10:20")],
        )
        self._upload(client, user_token, "far_away", "2019-01-01T03:00:00")
        resp = client.post(
            "/api/attachments/rematch",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["candidates"] == 1
        assert body["matched"] == 0

    def test_rematch_with_no_run_is_a_noop(self, client, user_token):
        resp = client.post(
            "/api/attachments/rematch",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"matched": 0, "candidates": 0, "matches": []}

    def test_rematch_requires_auth(self, client):
        assert client.post("/api/attachments/rematch").status_code == 401

    def test_rematch_does_not_touch_another_users_orphans(
        self, client, user_token, admin_token, db, regular_user, admin_user
    ):
        _seed_run(
            db, regular_user["id"], run_id="user_run",
            sessions=[("2026-08-05", "2026-08-05", "10:00", "10:20")],
        )
        _seed_run(
            db, admin_user["id"], run_id="admin_run",
            sessions=[("2026-08-05", "2026-08-05", "10:00", "10:20")],
        )
        # Admin owns an orphan; the regular user's re-match must not see it.
        self._upload(
            client, admin_token, "admin_orphan", "2026-08-05T10:06:30",
            data=_png_bytes("purple"),
        )
        db.execute("DELETE FROM session_attachments")
        db.commit()

        resp = client.post(
            "/api/attachments/rematch",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["candidates"] == 0

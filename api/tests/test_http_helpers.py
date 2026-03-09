"""Tests for HTTP helper functions: _post_json, _encode_multipart_form, _post_multipart."""
import json
from unittest.mock import patch, MagicMock

import pytest

from app import _post_json, _encode_multipart_form, _post_multipart, _normalize_remote_base_url


class TestPostJson:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.urllib_request.urlopen", return_value=mock_resp):
            status, body = _post_json("https://example.com/api", {"key": "val"})

        assert status == 200
        assert body == {"ok": True}

    def test_http_error_json_body(self):
        from urllib.error import HTTPError
        import io

        error_body = json.dumps({"error": "bad request"}).encode()
        exc = HTTPError("https://example.com", 400, "Bad Request", {}, io.BytesIO(error_body))

        with patch("app.urllib_request.urlopen", side_effect=exc):
            status, body = _post_json("https://example.com/api", {})

        assert status == 400
        assert body["error"] == "bad request"

    def test_http_error_non_json_body(self):
        from urllib.error import HTTPError
        import io

        exc = HTTPError("https://example.com", 500, "Server Error", {}, io.BytesIO(b"plain text error"))

        with patch("app.urllib_request.urlopen", side_effect=exc):
            status, body = _post_json("https://example.com/api", {})

        assert status == 500
        assert "plain text error" in body["error"]

    def test_http_error_empty_body(self):
        from urllib.error import HTTPError
        import io

        exc = HTTPError("https://example.com", 502, "Gateway", {}, io.BytesIO(b""))

        with patch("app.urllib_request.urlopen", side_effect=exc):
            status, body = _post_json("https://example.com/api", {})

        assert status == 502

    def test_custom_headers(self):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("app.urllib_request.urlopen", return_value=mock_resp) as mock_open:
            _post_json("https://example.com/api", {}, headers={"Authorization": "Bearer tok"})

        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer tok"
        assert req.get_header("Content-type") == "application/json"


class TestEncodeMultipartForm:
    def test_fields_only(self):
        body, boundary = _encode_multipart_form({"name": "test", "value": "123"}, [])
        assert boundary.startswith("lessonlens-")
        assert b"name=\"name\"" in body
        assert b"test" in body
        assert b"name=\"value\"" in body
        assert b"123" in body
        assert body.endswith(f"--{boundary}--\r\n".encode())

    def test_files_only(self):
        files = [{
            "field_name": "file",
            "filename": "test.zip",
            "data": b"zipdata",
            "content_type": "application/zip",
        }]
        body, boundary = _encode_multipart_form({}, files)
        assert b'name="file"' in body
        assert b'filename="test.zip"' in body
        assert b"Content-Type: application/zip" in body
        assert b"zipdata" in body

    def test_mixed_fields_and_files(self):
        files = [{
            "field_name": "attachment",
            "filename": "data.txt",
            "data": b"hello",
        }]
        body, boundary = _encode_multipart_form({"key": "val"}, files)
        assert b'name="key"' in body
        assert b'name="attachment"' in body
        assert b"hello" in body

    def test_file_without_content_type_uses_guess(self):
        files = [{
            "field_name": "f",
            "filename": "doc.pdf",
            "data": b"pdfdata",
        }]
        body, _ = _encode_multipart_form({}, files)
        assert b"Content-Type: application/pdf" in body


class TestPostMultipart:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 201
        mock_resp.read.return_value = json.dumps({"imported": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        files = [{
            "field_name": "file",
            "filename": "backup.zip",
            "data": b"zipbytes",
            "content_type": "application/zip",
        }]

        with patch("app.urllib_request.urlopen", return_value=mock_resp):
            status, body = _post_multipart(
                "https://example.com/api/import",
                {"replace": "true"},
                files,
                headers={"Authorization": "Bearer t"},
            )

        assert status == 201
        assert body["imported"] is True

    def test_http_error(self):
        from urllib.error import HTTPError
        import io

        exc = HTTPError("https://example.com", 403, "Forbidden", {}, io.BytesIO(b'{"error":"denied"}'))

        with patch("app.urllib_request.urlopen", side_effect=exc):
            status, body = _post_multipart("https://example.com", {}, [])

        assert status == 403
        assert body["error"] == "denied"

    def test_http_error_non_json(self):
        from urllib.error import HTTPError
        import io

        exc = HTTPError("https://example.com", 500, "Error", {}, io.BytesIO(b"server down"))

        with patch("app.urllib_request.urlopen", side_effect=exc):
            status, body = _post_multipart("https://example.com", {}, [])

        assert status == 500
        assert "server down" in body["error"]


class TestNormalizeRemoteBaseUrlExtended:
    def test_localhost_http_allowed(self):
        result = _normalize_remote_base_url("http://localhost:5000/")
        assert result == "http://localhost:5000"

    def test_127_http_allowed(self):
        result = _normalize_remote_base_url("http://127.0.0.1:3000")
        assert result == "http://127.0.0.1:3000"

    def test_non_localhost_http_rejected(self):
        with pytest.raises(ValueError, match="HTTPS"):
            _normalize_remote_base_url("http://example.com")

    def test_no_host(self):
        with pytest.raises(ValueError):
            _normalize_remote_base_url("https://")

    def test_whitespace_stripped(self):
        result = _normalize_remote_base_url("  https://example.com  ")
        assert result == "https://example.com"

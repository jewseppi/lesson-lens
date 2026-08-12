"""lessonlens_client.py — stdlib HTTP client for a hosted LessonLens instance.

Shared by the macOS updater (``scripts/line_mac_sync.py``) and the hosted MCP
server (``api/mcp_server_hosted.py``) so there is exactly one implementation of
login, multipart encoding, and the REST surface.

Deliberately standard-library only: the hosted MCP server must run on a machine
that has never installed Flask, Pillow, or a provider SDK.
"""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

__all__ = [
    "ApiError",
    "LessonLensClient",
    "encode_multipart",
    "source_timestamp_for",
]


def source_timestamp_for(path: Path) -> str | None:
    """Return a file's mtime as a naive *local* ISO string, or None.

    Naive local, not UTC: lesson windows are local wall-clock times, so an
    aware/UTC stamp would shift every photo out of its lesson for anyone not
    living on UTC.
    """
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat()
    except OSError:
        return None


class ApiError(RuntimeError):
    """Any non-2xx response or transport failure from the hosted API."""


def encode_multipart(
    fields: dict[str, str], files: list[tuple[str, str, bytes, str]]
) -> tuple[str, bytes]:
    """Return (content_type, body_bytes) for a multipart/form-data request.

    fields: {name: value}
    files:  [(field_name, filename, content, mime_type)]
    """
    boundary = f"----lessonlens{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(("--" + boundary).encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    for field, filename, content, mime in files:
        parts.append(("--" + boundary).encode())
        parts.append(
            (
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"'
            ).encode("utf-8")
        )
        parts.append(f"Content-Type: {mime}".encode())
        parts.append(b"")
        parts.append(content)
    parts.append(("--" + boundary + "--").encode())
    parts.append(b"")
    return f"multipart/form-data; boundary={boundary}", crlf.join(parts)


class LessonLensClient:
    """Thin REST client. Call :meth:`login` once, then any endpoint method."""

    def __init__(self, base_url: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    # --- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, *, headers=None, data=None):
        url = self.base_url + path
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise ApiError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {path} failed: {exc.reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}

    def _post_json(self, path: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        return self._request(
            "POST", path, headers={"Content-Type": "application/json"}, data=body
        )

    # --- auth -------------------------------------------------------------

    def login(self, email: str, password: str) -> None:
        result = self._post_json("/api/login", {"email": email, "password": password})
        token = result.get("access_token")
        if not token:
            raise ApiError(f"Login did not return a token: {result}")
        self.token = token

    def ensure_login(self, email: str, password: str) -> None:
        """Log in only if we don't already hold a token."""
        if not self.token:
            self.login(email, password)

    # --- sessions ---------------------------------------------------------

    def list_sessions(self) -> list[dict]:
        result = self._request("GET", "/api/sessions")
        return result if isinstance(result, list) else result.get("sessions", [])

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/api/sessions/{session_id}")

    def get_summary(self, session_id: str) -> dict:
        return self._request("GET", f"/api/sessions/{session_id}/summary")

    def get_retrieval_context(self, session_id: str) -> dict:
        return self._request("GET", f"/api/sessions/{session_id}/retrieval-context")

    def get_session_attachments(self, session_id: str) -> dict:
        return self._request("GET", f"/api/sessions/{session_id}/attachments")

    # --- writes -----------------------------------------------------------

    def import_summary(
        self, session_id: str, lesson_data: dict, provider: str, model: str
    ) -> dict:
        """Store an externally authored lesson package (the agent write path).

        Mirrors ``POST /api/sessions/<id>/summary/import``, which expects the
        lesson JSON as an uploaded file plus provider/model form fields.
        """
        payload = json.dumps(lesson_data, ensure_ascii=False, indent=2).encode("utf-8")
        content_type, body = encode_multipart(
            {"provider": provider, "model": model},
            [("file", f"{session_id}-lesson-data.json", payload, "application/json")],
        )
        return self._request(
            "POST",
            f"/api/sessions/{session_id}/summary/import",
            headers={"Content-Type": content_type},
            data=body,
        )

    def generate(self, session_id: str, provider: str | None, model: str | None) -> dict:
        payload: dict[str, str] = {}
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        return self._post_json(f"/api/sessions/{session_id}/generate", payload)

    def generate_all_missing(self, provider: str | None, model: str | None) -> dict:
        payload: dict[str, str] = {}
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        return self._post_json("/api/summaries/generate", payload)

    def add_annotation(self, session_id: str, payload: dict) -> dict:
        return self._post_json(f"/api/sessions/{session_id}/annotations", payload)

    def list_annotations(self, session_id: str) -> dict:
        return self._request("GET", f"/api/sessions/{session_id}/annotations")

    # --- sync / upload ----------------------------------------------------

    def sync_export(self, path: Path) -> dict:
        content = path.read_bytes()
        content_type, body = encode_multipart(
            {}, [("file", path.name, content, "text/plain")]
        )
        return self._request(
            "POST", "/api/sync", headers={"Content-Type": content_type}, data=body
        )

    def upload_images(self, image_paths, name_hint=None) -> dict:
        """Upload images.

        ``name_hint(path, data) -> (filename, mime)`` lets the caller supply a
        filename with a real extension. LINE caches media with hashed,
        extension-less names, and sending those verbatim gets them rejected.

        Each file's modification time travels alongside it as
        ``source_timestamps``. Only bytes cross the wire, so the server's copy
        is always stamped "now" — and LINE strips EXIF from received photos, so
        without this the server has no capture time to match against a lesson.
        """
        files = []
        timestamps: list[str | None] = []
        for p in image_paths:
            p = Path(p)
            data = p.read_bytes()
            if name_hint:
                filename, mime = name_hint(p, data)
            else:
                filename = p.name
                mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            files.append(("images", filename, data, mime))
            timestamps.append(source_timestamp_for(p))
        content_type, body = encode_multipart(
            {"source_timestamps": json.dumps(timestamps)}, files
        )
        return self._request(
            "POST",
            "/api/attachments/upload",
            headers={"Content-Type": content_type},
            data=body,
        )

    def rematch_attachments(self) -> dict:
        """Retry auto-matching for attachments not yet linked to a session."""
        return self._post_json("/api/attachments/rematch", {})

    def sync_remote(
        self,
        remote_base_url: str,
        remote_email: str,
        remote_password: str,
        replace_existing: bool = False,
    ) -> dict:
        """Ask a *local* instance to push its data up to a hosted one."""
        return self._post_json(
            "/api/backup/sync-remote",
            {
                "remote_base_url": remote_base_url,
                "remote_email": remote_email,
                "remote_password": remote_password,
                "replace_existing": replace_existing,
            },
        )

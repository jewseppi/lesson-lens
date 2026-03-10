"""
Image processing helpers for Phase 0A — EXIF extraction and session matching.
"""
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

from PIL import Image
from PIL.ExifTags import TAGS

# Supported image MIME types
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp"}


def is_image_file(filename):
    """Check if a filename has a supported image extension."""
    _, ext = os.path.splitext(filename)
    return ext.lower() in IMAGE_EXTENSIONS


def compute_file_hash(filepath):
    """SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_exif_datetime(filepath):
    """
    Extract capture timestamp from image EXIF data.

    Returns dict with:
      - captured_at_local: ISO string of local capture time (or None)
      - timezone_hint: EXIF timezone offset if available (or None)
      - captured_at_utc: ISO string of UTC capture time (or None)
      - metadata_json: dict of selected EXIF fields
      - source: 'exif', 'filename', 'mtime', or None
    """
    result = {
        "captured_at_local": None,
        "captured_at_utc": None,
        "timezone_hint": None,
        "metadata_json": {},
        "source": None,
    }

    # Try EXIF first
    try:
        img = Image.open(filepath)
        exif_data = img._getexif()
        if exif_data:
            decoded = {}
            for tag_id, value in exif_data.items():
                tag_name = TAGS.get(tag_id, str(tag_id))
                # Only keep string/numeric EXIF values
                if isinstance(value, (str, int, float)):
                    decoded[tag_name] = value

            result["metadata_json"] = decoded

            # DateTimeOriginal (tag 36867) is most reliable
            dt_str = decoded.get("DateTimeOriginal") or decoded.get("DateTime")
            if dt_str and isinstance(dt_str, str):
                try:
                    dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                    result["captured_at_local"] = dt.isoformat()
                    result["source"] = "exif"

                    # Check for timezone offset in OffsetTimeOriginal
                    tz_offset = decoded.get("OffsetTimeOriginal") or decoded.get("OffsetTime")
                    if tz_offset and isinstance(tz_offset, str):
                        result["timezone_hint"] = tz_offset
                        try:
                            # Parse offset like "+09:00" or "-05:00"
                            match = re.match(r"([+-])(\d{2}):(\d{2})", tz_offset)
                            if match:
                                sign = 1 if match.group(1) == "+" else -1
                                hours = int(match.group(2))
                                minutes = int(match.group(3))
                                offset = timedelta(hours=hours, minutes=minutes) * sign
                                dt_utc = dt - offset
                                result["captured_at_utc"] = dt_utc.replace(
                                    tzinfo=timezone.utc
                                ).isoformat()
                        except (ValueError, AttributeError):
                            pass
                except ValueError:
                    pass
        img.close()
    except Exception:
        pass

    # Fallback: try filename timestamp patterns
    if not result["captured_at_local"]:
        basename = os.path.basename(filepath)
        dt = _parse_filename_timestamp(basename)
        if dt:
            result["captured_at_local"] = dt.isoformat()
            result["source"] = "filename"

    # Fallback: filesystem mtime
    if not result["captured_at_local"]:
        try:
            mtime = os.path.getmtime(filepath)
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            result["captured_at_local"] = dt.isoformat()
            result["captured_at_utc"] = dt.isoformat()
            result["source"] = "mtime"
        except OSError:
            pass

    return result


def _parse_filename_timestamp(filename):
    """
    Try to extract a datetime from common filename patterns.
    Returns datetime or None.

    Patterns matched:
      - IMG_20231225_143022.jpg
      - 2023-12-25_14-30-22.png
      - 20231225143022.jpg
      - Screenshot_2023-12-25-14-30-22.png
    """
    patterns = [
        # IMG_20231225_143022 or Screenshot_20231225_143022
        r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})",
        # 2023-12-25_14-30-22 or 2023-12-25-14-30-22
        r"(\d{4})-(\d{2})-(\d{2})[_-](\d{2})-(\d{2})-(\d{2})",
        # 20231225143022 (14 consecutive digits)
        r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, filename)
        if m:
            try:
                return datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    int(m.group(4)), int(m.group(5)), int(m.group(6)),
                )
            except ValueError:
                continue
    return None


def match_image_to_sessions(captured_at_local, sessions, margin_hours=2):
    """
    Match an image timestamp to sessions based on confidence bands.

    Args:
        captured_at_local: ISO datetime string of captured time (local)
        sessions: list of dicts with 'session_id', 'date', 'start_time', 'end_time'
        margin_hours: hours of margin for 'medium' confidence

    Returns:
        dict with 'session_id', 'confidence' ('high'|'medium'|'low'|'unmatched'),
        and 'reason' string
    """
    if not captured_at_local:
        return {"session_id": None, "confidence": "unmatched", "reason": "no_timestamp"}

    try:
        img_dt = datetime.fromisoformat(captured_at_local)
    except (ValueError, TypeError):
        return {"session_id": None, "confidence": "unmatched", "reason": "invalid_timestamp"}

    # Strip timezone info for comparison (session times are naive/local)
    if img_dt.tzinfo is not None:
        img_dt = img_dt.replace(tzinfo=None)

    img_date = img_dt.date()
    margin = timedelta(hours=margin_hours)

    best_match = None
    best_confidence = "unmatched"
    best_reason = "no_matching_session"

    for sess in sessions:
        try:
            sess_date = datetime.strptime(sess["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue

        sess_id = sess["session_id"]

        # Build session time window
        try:
            start_str = f"{sess['date']}T{sess['start_time']}"
            end_str = f"{sess['date']}T{sess['end_time']}"
            sess_start = datetime.fromisoformat(start_str)
            sess_end = datetime.fromisoformat(end_str)
        except (ValueError, KeyError):
            # If no times, only match by date
            if img_date == sess_date:
                if best_confidence in ("unmatched",):
                    best_match = sess_id
                    best_confidence = "low"
                    best_reason = "same_date_no_session_times"
            continue

        # High confidence: within session window
        if sess_start <= img_dt <= sess_end:
            return {"session_id": sess_id, "confidence": "high", "reason": "within_session_window"}

        # Medium confidence: within margin
        if (sess_start - margin) <= img_dt <= (sess_end + margin):
            if best_confidence in ("unmatched", "low"):
                best_match = sess_id
                best_confidence = "medium"
                best_reason = "within_margin"

        # Low confidence: same date
        elif img_date == sess_date:
            if best_confidence == "unmatched":
                best_match = sess_id
                best_confidence = "low"
                best_reason = "same_date"

    return {"session_id": best_match, "confidence": best_confidence, "reason": best_reason}

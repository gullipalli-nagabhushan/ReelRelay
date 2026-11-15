"""Small formatting / filename helpers shared across the app."""

from __future__ import annotations

import re
from urllib.parse import quote

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def human_size(num_bytes: int | float | None) -> str:
    """Format a byte count as a compact human readable string."""
    if not num_bytes or num_bytes <= 0:
        return "—"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}" if size < 10 else f"{size:.0f} {unit}"
        size /= 1024
    return "—"


def human_duration(seconds: int | float | None) -> str:
    """Format seconds as m:ss or h:mm:ss."""
    if not seconds or seconds < 0:
        return "Unknown"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def compact_number(value: int | float | None) -> str:
    """1234567 -> 1.2M."""
    if value is None:
        return "—"
    number = int(value)
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(number)


def sanitize_filename(name: str, max_length: int = 140) -> str:
    """Make a value safe for Windows, macOS and Linux downloads."""
    cleaned = _INVALID_FILENAME.sub("", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    cleaned = cleaned[:max_length].rstrip(" .")
    if not cleaned:
        cleaned = "download"
    stem = cleaned.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def clean_text(value: str | None, max_length: int = 300) -> str:
    """Trim user supplied or scraped text and remove control characters."""
    if not value:
        return ""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length].strip()


def content_disposition(filename: str) -> str:
    """RFC 6266 header with an ASCII fallback and UTF-8 support."""
    fallback = (
        filename.encode("ascii", "ignore").decode("ascii").replace('"', "'").strip()
        or "download"
    )
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )

"""URL parsing, formatting and filename helpers."""

from __future__ import annotations

import pytest

from core.utils import (
    clean_text,
    compact_number,
    content_disposition,
    human_duration,
    human_size,
    sanitize_filename,
)
from core.ytdlp import extract_video_id, is_valid_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ],
)
def test_accepts_youtube_urls(url: str) -> None:
    assert is_valid_url(url) is True
    assert extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=too-short",
        "https://youtu.be/",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "javascript:alert(1)",
        "dQw4w9WgXc",
    ],
)
def test_rejects_non_video_urls(url: str) -> None:
    assert is_valid_url(url) is False
    assert extract_video_id(url) is None


def test_human_size() -> None:
    assert human_size(None) == "—"
    assert human_size(0) == "—"
    assert human_size(1023) == "1023 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


def test_human_duration() -> None:
    assert human_duration(None) == "Unknown"
    assert human_duration(0) == "Unknown"
    assert human_duration(65) == "1:05"
    assert human_duration(3661) == "1:01:01"


def test_compact_number() -> None:
    assert compact_number(None) == "—"
    assert compact_number(999) == "999"
    assert compact_number(1500) == "1.5K"
    assert compact_number(1_600_000) == "1.6M"
    assert compact_number(2_500_000_000) == "2.5B"


def test_sanitize_filename() -> None:
    assert sanitize_filename('a/b:c*?"<>|d') == "abcd"
    assert sanitize_filename("  ...  ") == "download"
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("x" * 300, max_length=20) == "x" * 20


def test_clean_text_strips_controls() -> None:
    assert clean_text("  hello\n\tworld  ") == "hello world"
    assert clean_text(None) == ""
    assert clean_text("abcdef", max_length=3) == "abc"


def test_content_disposition_is_rfc6266() -> None:
    header = content_disposition("Café del Mar.mp3")
    assert header.startswith("attachment; ")
    assert 'filename="Caf del Mar.mp3"' in header
    assert "filename*=UTF-8''Caf%C3%A9%20del%20Mar.mp3" in header

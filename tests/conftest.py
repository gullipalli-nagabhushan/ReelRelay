"""Shared fixtures for the offline unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture()
def sample_info() -> dict:
    """A small but realistic yt-dlp payload (no network required)."""
    return {
        "id": "dQw4w9WgXcQ",
        "_video_id": "dQw4w9WgXcQ",
        "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
        "channel": "Rick Astley",
        "uploader": "Rick Astley",
        "duration": 213,
        "view_count": 1_600_000_000,
        "like_count": 17_000_000,
        "upload_date": "20091025",
        "release_year": 1987,
        "categories": ["Music"],
        "description": (
            "The official video for Never Gonna Give You Up.\n"
            "Composer: Stock Aitken Waterman\n"
            "Lyrics: Stock Aitken Waterman\n"
        ),
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        "thumbnails": [
            {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", "width": 1280, "height": 720},
            {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.webp", "width": 1280, "height": 720},
        ],
        "formats": [
            {
                "format_id": "313",
                "ext": "webm",
                "protocol": "https",
                "vcodec": "vp9",
                "acodec": "none",
                "width": 3840,
                "height": 2160,
                "fps": 30,
                "tbr": 15000,
                "filesize": 400_000_000,
                "url": "https://media.example/313.webm",
            },
            {
                "format_id": "399",
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "av01.0.08M.08",
                "acodec": "none",
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "tbr": 2500,
                "filesize": 60_000_000,
                "url": "https://media.example/399.mp4",
            },
            {
                "format_id": "137",
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "tbr": 4000,
                "filesize": 80_000_000,
                "url": "https://media.example/137.mp4",
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "protocol": "https",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 128,
                "filesize": 3_400_000,
                "url": "https://media.example/140.m4a",
            },
            {
                "format_id": "251",
                "ext": "webm",
                "protocol": "https",
                "vcodec": "none",
                "acodec": "opus",
                "abr": 96,
                "filesize": 2_600_000,
                "url": "https://media.example/251.webm",
            },
            {
                "format_id": "18",
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "width": 640,
                "height": 360,
                "fps": 30,
                "tbr": 700,
                "filesize": 18_000_000,
                "url": "https://media.example/18.mp4",
            },
        ],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 test",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }


@pytest.fixture()
def progressive_info() -> dict:
    """An Instagram-style portrait link: one file, audio inside the video."""
    return {
        "id": "CxYz123",
        "_video_id": "CxYz123",
        "_platform": "instagram",
        "_source_url": "https://www.instagram.com/reel/CxYz123/",
        "title": "Golden hour by the sea",
        "channel": "creator",
        "uploader": "creator",
        "duration": 30,
        "view_count": 0,
        "like_count": 0,
        "webpage_url": "https://www.instagram.com/reel/CxYz123/",
        "thumbnail": "https://cdn.example/thumb.jpg",
        "thumbnails": [
            {"url": "https://cdn.example/thumb.jpg", "width": 1080, "height": 1920},
        ],
        "formats": [
            {
                "format_id": "hd",
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "avc1.4d401f",
                "acodec": "mp4a.40.2",
                "width": 1080,
                "height": 1920,
                "fps": 30,
                "tbr": 2800,
                "filesize": 10_500_000,
                "url": "https://media.example/reel-hd.mp4",
            },
            {
                "format_id": "sd",
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "avc1.4d401e",
                "acodec": "mp4a.40.2",
                "width": 720,
                "height": 1280,
                "fps": 30,
                "tbr": 1400,
                "filesize": 5_250_000,
                "url": "https://media.example/reel-sd.mp4",
            },
        ],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 test",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

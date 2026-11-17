"""Cover-art retrieval.

The video thumbnail is fetched through the local app (so the user's browser
never needs to talk to Google) and normalised to JPEG, which is what every
tag reader understands.  The image is kept in memory; nothing is written to
disk unless ffmpeg explicitly needs a file input (FLAC cover embedding), in
which case a short-lived temp file is created and deleted immediately.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

import httpx

from . import config

logger = logging.getLogger(__name__)

_MAX_COVER_BYTES = 12 * 1024 * 1024


def cover_candidates(info: dict[str, Any]) -> list[str]:
    """High-resolution JPEG candidates first, then whatever yt-dlp found."""
    video_id = info.get("_video_id") or info.get("id") or ""
    urls: list[str] = []
    if video_id and str(info.get("_platform") or "youtube").lower() == "youtube":
        urls.extend(
            [
                f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
                f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            ]
        )

    thumbnails = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    thumbnails.sort(key=lambda t: (t.get("width") or 0) * (t.get("height") or 0), reverse=True)
    for thumb in thumbnails:
        url = thumb["url"]
        if url not in urls:
            urls.append(url)
    if info.get("thumbnail") and info["thumbnail"] not in urls:
        urls.append(info["thumbnail"])
    return urls


def _to_jpeg(data: bytes) -> bytes | None:
    """Convert any image bytes to JPEG using ffmpeg (already required for audio)."""
    if data[:2] == b"\xff\xd8":
        return data
    if not config.FFMPEG:
        return None
    try:
        result = subprocess.run(
            [
                config.FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-c:v",
                "mjpeg",
                "-q:v",
                "2",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Cover conversion failed")
        return None
    if result.returncode != 0 or not result.stdout:
        logger.warning("Cover conversion failed: %s", result.stderr[-300:])
        return None
    return result.stdout


def fetch_cover(info: dict[str, Any]) -> bytes | None:
    """Download the best available cover art as JPEG bytes."""
    platform = str(info.get("_platform") or "youtube").lower()
    referer = {
        "youtube": config.YOUTUBE_REFERER,
        "instagram": "https://www.instagram.com/",
        "facebook": "https://www.facebook.com/",
    }.get(platform, config.YOUTUBE_REFERER)
    headers = {
        "User-Agent": config.DEFAULT_USER_AGENT,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0), follow_redirects=True) as client:
            for url in cover_candidates(info):
                try:
                    response = client.get(url, headers=headers)
                except httpx.HTTPError as exc:
                    logger.debug("Cover fetch failed for %s: %s", url, exc)
                    continue
                if response.status_code != 200:
                    continue
                data = response.content
                if len(data) < 2048 or len(data) > _MAX_COVER_BYTES:
                    continue
                content_type = response.headers.get("content-type", "").lower()
                if not content_type.startswith("image/") and not data[:4].startswith(b"\x89PNG"):
                    continue
                jpeg = _to_jpeg(data)
                if jpeg:
                    return jpeg
    except Exception:  # noqa: BLE001 - cover art is optional
        logger.exception("Unexpected error while fetching cover art")
    return None

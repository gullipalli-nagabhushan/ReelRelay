"""Runtime configuration.

Everything can be tuned with environment variables so the app can be deployed
without touching the code. Sensible defaults are used for local development.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import logging
import os
import shutil
import tempfile
from pathlib import Path

try:  # optional: load a local .env file when python-dotenv is present
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the app works without it
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

logger = logging.getLogger(__name__)

APP_NAME = "ReelRelay"
APP_VERSION = "3.0.0"
APP_TAGLINE = "YouTube · Instagram · Facebook, straight to your device"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


_NETSCAPE_MARKERS = ("# Netscape HTTP Cookie File", "# HTTP Cookie File")


def _materialise_cookies(text: str) -> str | None:
    """Write cookie *content* to a private temp file that yt-dlp can read.

    Serverless hosts (Vercel, Lambda, Cloud Run) cannot ship a cookie file and
    only allow writes under the system temp directory, so the contents are
    supplied through an environment variable instead.
    """
    text = text.strip()
    if not text:
        return None
    if not any(marker in text for marker in _NETSCAPE_MARKERS):
        logger.warning(
            "Cookies were supplied but do not look like a Netscape cookies file; "
            "yt-dlp may ignore them."
        )
    target = Path(tempfile.gettempdir()) / f"reelrelay-cookies-{os.getpid()}.txt"
    try:
        target.write_text(text + "\n", encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover - Windows without POSIX modes
            pass
    except OSError:
        logger.exception("Could not write the cookies file supplied via environment")
        return None
    atexit.register(lambda: target.unlink(missing_ok=True))
    return str(target)


def _cookies_from_env() -> str | None:
    """Resolve the yt-dlp cookie file from the environment.

    ``YTDLP_COOKIES_FILE`` wins because it points at a real path. Otherwise the
    raw Netscape text (``YTDLP_COOKIES``) or its base64 form
    (``YTDLP_COOKIES_B64``) is written to a temp file, which is what serverless
    platforms need.
    """
    explicit = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if explicit:
        return explicit

    raw = os.getenv("YTDLP_COOKIES")
    if raw:
        return _materialise_cookies(raw)

    encoded = os.getenv("YTDLP_COOKIES_B64")
    if encoded:
        try:
            decoded = base64.b64decode("".join(encoded.split()), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            logger.warning("YTDLP_COOKIES_B64 is not valid base64 text; cookies were ignored")
            return None
        return _materialise_cookies(decoded)

    return None


# Highest video resolution offered (YouTube can serve 4320p / 8K).
MAX_VIDEO_HEIGHT = max(144, min(_env_int("MAX_VIDEO_HEIGHT", 2160), 4320))
# How many downloads may stream at the same time.
MAX_CONCURRENT_DOWNLOADS = max(1, _env_int("MAX_CONCURRENT_DOWNLOADS", 4))

# yt-dlp metadata cache (avoids re-extracting between preview and download).
INFO_CACHE_TTL = max(30, _env_int("INFO_CACHE_TTL", 600))
INFO_CACHE_SIZE = max(8, _env_int("INFO_CACHE_SIZE", 64))

# Optional yt-dlp extras (age-restricted videos, geo blocks, proxies).
COOKIES_FILE = _cookies_from_env()
PROXY = os.getenv("YTDLP_PROXY") or None

# System binaries. ffmpeg is required for 4K merging and audio conversion.
FFMPEG = os.getenv("FFMPEG_BINARY") or shutil.which("ffmpeg")
FFPROBE = os.getenv("FFPROBE_BINARY") or shutil.which("ffprobe")
FFMPEG_OK = bool(FFMPEG)

# Fallback user agent, only used when a format does not expose its own headers.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Used to build a friendly legal / yt-dlp referer header for streaming.
YOUTUBE_REFERER = "https://www.youtube.com/"


def summary() -> dict:
    """Small diagnostic payload used by /health and the startup log."""
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "ffmpeg": FFMPEG or None,
        "ffmpeg_available": FFMPEG_OK,
        "max_height": MAX_VIDEO_HEIGHT,
        "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
        "cookies_configured": bool(COOKIES_FILE),
        "proxy_configured": bool(PROXY),
    }

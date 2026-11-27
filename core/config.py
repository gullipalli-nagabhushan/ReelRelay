"""Runtime configuration.

Everything can be tuned with environment variables so the app can run without
touching the code. Sensible defaults are used for local use.
"""

from __future__ import annotations

import logging
import os
import shutil

try:  # optional: load a local .env file when python-dotenv is present
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the app works without it
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

logger = logging.getLogger(__name__)

APP_NAME = "ReelRelay"
APP_VERSION = "3.4.0"
APP_TAGLINE = "YouTube · Instagram · Facebook, straight to your device"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _cookies_from_env() -> str | None:
    """Resolve the yt-dlp cookie file path from the environment.

    ReelRelay runs on the user's own machine, so cookies are always a real file
    that the user creates and controls. Only ``YTDLP_COOKIES_FILE`` is read.
    """
    explicit = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if not explicit:
        return None
    if not os.path.isfile(explicit):
        logger.warning(
            "YTDLP_COOKIES_FILE points at %s, but that file does not exist; "
            "cookies will be ignored.",
            explicit,
        )
        return None
    return explicit


# Highest video resolution offered (YouTube can serve 4320p / 8K).
MAX_VIDEO_HEIGHT = max(144, min(_env_int("MAX_VIDEO_HEIGHT", 4320), 4320))
# How many downloads may stream at the same time.
MAX_CONCURRENT_DOWNLOADS = max(1, _env_int("MAX_CONCURRENT_DOWNLOADS", 4))

# yt-dlp metadata cache (avoids re-extracting between preview and download).
INFO_CACHE_TTL = max(30, _env_int("INFO_CACHE_TTL", 600))
INFO_CACHE_SIZE = max(8, _env_int("INFO_CACHE_SIZE", 64))

# Parallel-range accelerator. CDNs throttle a single connection, so 4K/8K
# streams are fetched over several connections into a bounded memory window
# and handed to ffmpeg through a localhost URL. Nothing is written to disk.
PARALLEL_ENABLED = _env_bool("PARALLEL_DOWNLOADS", True)
PARALLEL_CONNECTIONS = max(1, min(_env_int("PARALLEL_CONNECTIONS", 12), 32))
PARALLEL_SEGMENT_MB = max(1, min(_env_int("PARALLEL_SEGMENT_MB", 4), 32))
PARALLEL_WINDOW_MB = max(8, min(_env_int("PARALLEL_WINDOW_MB", 32), 512))
# Hard cap shared by every active accelerator in the process.
PARALLEL_MAX_CONNECTIONS = max(1, min(_env_int("PARALLEL_MAX_CONNECTIONS", 32), 128))

# Optional yt-dlp extras (age-restricted videos, geo blocks, proxies).
COOKIES_FILE = _cookies_from_env()
PROXY = os.getenv("YTDLP_PROXY") or None

# System binaries. ffmpeg is required for 4K/8K merging and audio conversion.
FFMPEG = os.getenv("FFMPEG_BINARY") or shutil.which("ffmpeg")
FFPROBE = os.getenv("FFPROBE_BINARY") or shutil.which("ffprobe")
FFMPEG_OK = bool(FFMPEG)

# Lyrics: use a lyrics block in the video description, and optionally ask
# LRCLIB when the description has none. Only the artist, title, album and
# duration are sent; no account or API key is involved.
LYRICS_LOOKUP = _env_bool("REELRELAY_LYRICS_LOOKUP", True)
# Prefer timed LRC text where a format has a timed lyrics field. MP3 keeps a
# plain USLT plus timed SYLT/TXXX:LYRICS; FLAC and M4A store the LRC text in
# their main lyrics field. Set this to false to store plain text only.
LYRICS_SYNCED = _env_bool("REELRELAY_LYRICS_SYNCED", True)

# Optional AI lyrics through Groq. This is opt-in per download: the audio tab
# has a checkbox, and nothing is sent to Groq unless the user ticks it.
# The key is read on the server only and is never exposed to the browser.
GROQ_API_KEY = (
    os.getenv("GROQ_API_KEY") or os.getenv("REELRELAY_GROQ_API_KEY") or ""
).strip()
GROQ_BASE_URL = (
    os.getenv("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
).rstrip("/")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL") or "whisper-large-v3"
GROQ_TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL") or "openai/gpt-oss-20b"
AI_LYRICS_ENABLED = _env_bool("REELRELAY_AI_LYRICS", True)
# Groq's speech endpoint accepts up to 25 MB and 10 minutes. ReelRelay refuses
# longer audio instead of silently truncating a song.
GROQ_MAX_AUDIO_SECONDS = max(30, min(_env_int("GROQ_MAX_AUDIO_SECONDS", 600), 600))
GROQ_MAX_AUDIO_BYTES = 25 * 1024 * 1024
GROQ_TIMEOUT = max(30, min(_env_int("GROQ_TIMEOUT", 180), 600))
GROQ_AUDIO_TIMEOUT = max(30, min(_env_int("GROQ_AUDIO_TIMEOUT", 240), 900))
# Include negative results ("no lyrics") so a song is not transcribed again
# and again during the same server session.
AI_LYRICS_CACHE_TTL = max(300, _env_int("AI_LYRICS_CACHE_TTL", 21600))
AI_LYRICS_CACHE_SIZE = max(16, _env_int("AI_LYRICS_CACHE_SIZE", 256))

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
        "parallel_downloads": PARALLEL_ENABLED,
        "parallel_connections": PARALLEL_CONNECTIONS,
        "parallel_max_connections": PARALLEL_MAX_CONNECTIONS,
        "cookies_configured": bool(COOKIES_FILE),
        "proxy_configured": bool(PROXY),
        "lyrics_lookup": LYRICS_LOOKUP,
        "ai_lyrics_configured": bool(AI_LYRICS_ENABLED and GROQ_API_KEY),
        "ai_lyrics_models": {
            "transcription": GROQ_WHISPER_MODEL,
            "text": GROQ_TEXT_MODEL,
        },
        "ai_lyrics_max_seconds": GROQ_MAX_AUDIO_SECONDS,
    }

"""Multi-platform extraction, metadata normalisation and format discovery.

`yt-dlp` is the only component that talks to YouTube, Instagram or Facebook.
Everything else in the app consumes the plain dictionaries produced here, which
keeps the download pipeline easy to reason about (and easy to unit test).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import yt_dlp

from . import config
from .utils import clean_text, compact_number, human_duration, human_size

logger = logging.getLogger(__name__)

PLATFORM_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
}
SUPPORTED_PLATFORMS = tuple(PLATFORM_LABELS)


class ExtractionError(Exception):
    """The URL could not be turned into media metadata."""


class UnavailableError(Exception):
    """A requested quality/profile is not available for this video."""


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #

_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "www.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}
_INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}
_FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
    "mbasic.facebook.com",
    "fb.watch",
    "www.fb.watch",
}
_SHORT_PATH_PREFIXES = ("/embed/", "/v/", "/shorts/", "/live/")
_INSTAGRAM_PREFIXES = ("/p/", "/reel/", "/reels/", "/tv/", "/stories/")
_FACEBOOK_VIDEO_PATHS = (
    "/watch",
    "/video.php",
    "/videos/",
    "/reel/",
    "/reels/",
    "/share/",
    "/stories/",
)
_TRACKING_PARAMS = {
    "igsh",
    "igshid",
    "fbclid",
    "mibextid",
    "ref",
    "refsrc",
    "__cft__",
    "__tn__",
    "_rdr",
    "extid",
    "si",
    "feature",
    "app",
    "utm_source",
    "utm_medium",
    "utm_campaign",
}


@dataclass(frozen=True)
class Source:
    """A validated, normalised media link."""

    platform: str
    key: str
    url: str
    video_id: str | None = None

    @property
    def label(self) -> str:
        return platform_label(self.platform)


def platform_label(platform: str | None) -> str:
    return PLATFORM_LABELS.get(str(platform or "").lower(), "this platform")


def _with_scheme(raw_url: str) -> str:
    raw = str(raw_url or "").strip()
    if raw and not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        raw = "https://" + raw
    return raw


def _parse(raw_url: str | None):
    raw = _with_scheme(raw_url) if raw_url else ""
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return parsed


def extract_video_id(raw_url: str | None) -> str | None:
    """Return the 11 character YouTube id, or None when this is not YouTube."""
    if not raw_url:
        return None

    raw = str(raw_url).strip()
    if not raw:
        return None

    # Allow users to paste just the id.
    if _BARE_ID.match(raw):
        return raw

    parsed = _parse(raw)
    if parsed is None:
        return None

    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None

    candidate: str | None = None
    path = parsed.path or "/"

    if host.endswith("youtu.be"):
        candidate = path.strip("/").split("/")[0]
    elif path in ("/watch", "/watch/"):
        values = parse_qs(parsed.query).get("v") or []
        candidate = values[0] if values else None
    elif path.startswith(_SHORT_PATH_PREFIXES):
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            candidate = parts[1]
    elif path.startswith("/playlist"):
        return None  # playlists are intentionally unsupported
    else:
        # e.g. /watch/VIDEOID or /attribution_link?...
        values = parse_qs(parsed.query).get("v") or []
        if values:
            candidate = values[0]
        else:
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]

    if candidate and _VIDEO_ID.match(candidate):
        return candidate
    return None


def detect_platform(raw_url: str | None) -> str | None:
    """Return ``youtube``/``instagram``/``facebook`` for a supported link."""
    if not raw_url:
        return None
    raw = str(raw_url).strip()
    if _BARE_ID.match(raw):
        return "youtube"

    parsed = _parse(raw)
    if parsed is None:
        return None

    host = (parsed.hostname or "").lower()
    path = (parsed.path or "/").lower()

    if host in _YOUTUBE_HOSTS and extract_video_id(raw):
        return "youtube"

    if host in _INSTAGRAM_HOSTS:
        if any(path.startswith(prefix) for prefix in _INSTAGRAM_PREFIXES):
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2:
                return "instagram"
        return None

    if host in _FACEBOOK_HOSTS:
        if host.endswith("fb.watch"):
            return "facebook" if path.strip("/") else None
        if any(hint in path for hint in _FACEBOOK_VIDEO_PATHS):
            return "facebook"
        if parse_qs(parsed.query).get("v"):
            return "facebook"
        return None

    return None


def _share_url(parsed) -> str:
    """Drop tracking parameters while keeping the parameters that identify media."""
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), "")
    )


def resolve_source(raw_url: str | None) -> Source:
    """Validate a link and return a canonical form plus a cache key."""
    platform = detect_platform(raw_url)
    if platform is None:
        raise ExtractionError(
            "That link is not supported. Paste a YouTube, Instagram or Facebook video link."
        )

    if platform == "youtube":
        video_id = extract_video_id(raw_url)
        assert video_id is not None
        return Source(
            platform="youtube",
            key=f"youtube:{video_id}",
            url=f"https://www.youtube.com/watch?v={video_id}",
            video_id=video_id,
        )

    parsed = _parse(raw_url)
    assert parsed is not None
    url = _share_url(parsed)
    return Source(platform=platform, key=f"{platform}:{url}", url=url)


def is_supported_url(raw_url: str | None) -> bool:
    return detect_platform(raw_url) is not None


def is_valid_url(raw_url: str | None) -> bool:
    """Backwards-compatible name used by the routes and tests."""
    return is_supported_url(raw_url)


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# --------------------------------------------------------------------------- #
# Extraction cache (single-flight per video)
# --------------------------------------------------------------------------- #


class _InfoCache:
    def __init__(self, ttl: int, max_size: int) -> None:
        self.ttl = ttl
        self.max_size = max_size
        self._data: dict[str, tuple[float, dict]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            stored_at, value = entry
            if time.time() - stored_at > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            if len(self._data) >= self.max_size:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time(), value)

    def begin(self, key: str) -> bool:
        """Return True when the caller is responsible for extracting.

        If another request is already extracting the same video we wait for it
        (so the source is hit once), then return True as a safe fallback.
        """
        with self._lock:
            event = self._inflight.get(key)
            if event is None:
                self._inflight[key] = threading.Event()
                return True
        event.wait(timeout=120)
        with self._lock:
            if self._inflight.get(key) is event:
                self._inflight.pop(key, None)
        return True

    def finish(self, key: str) -> None:
        with self._lock:
            event = self._inflight.pop(key, None)
        if event:
            event.set()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_info_cache = _InfoCache(config.INFO_CACHE_TTL, config.INFO_CACHE_SIZE)


def _ydl_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "no_color": True,
        "nocheckcertificate": False,
        "socket_timeout": 30,
        "extractor_retries": 3,
        "geo_bypass": True,
        "windowsfilenames": True,
        "ignoreerrors": False,
    }
    if config.COOKIES_FILE:
        options["cookiefile"] = config.COOKIES_FILE
    if config.PROXY:
        options["proxy"] = config.PROXY
    return options


_FRIENDLY_ERRORS = (
    ("private", "This post is private, so the platform will not let us read it."),
    ("login required", "This platform is asking for a sign-in. Add a cookies file (YTDLP_COOKIES_FILE) to access it."),
    ("sign in", "This platform is asking for a sign-in. Add a cookies file (YTDLP_COOKIES_FILE) to access it."),
    ("rate-limit", "The platform is rate-limiting the server. Wait a minute and try again."),
    ("age", "This media is age-restricted. Add a cookies file (YTDLP_COOKIES_FILE) to access it."),
    ("members", "This media is for members only."),
    ("not available in your country", "This media is blocked in the server's region."),
    ("unavailable", "This media is unavailable, removed, or region-locked."),
    ("unsupported url", "That link is not a supported video page."),
    ("live event", "Live streams are not supported — try again after the stream ends."),
    ("ffmpeg", "ffmpeg is required for this operation but was not found on the server."),
)


def _friendly_error(message: str, platform: str | None = None) -> str:
    lowered = message.lower()
    for needle, friendly in _FRIENDLY_ERRORS:
        if needle in lowered:
            return friendly
    return clean_text(message, 400) or f"{platform_label(platform)} did not return any media information."


def get_info(raw_url: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Extract raw yt-dlp information for one video on a supported platform."""
    source = resolve_source(raw_url)

    if use_cache:
        cached = _info_cache.get(source.key)
        if cached is not None:
            return cached

    if use_cache and not _info_cache.begin(source.key):
        cached = _info_cache.get(source.key)
        if cached is not None:
            return cached

    try:
        with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
            info = ydl.extract_info(source.url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("yt-dlp failed for %s: %s", source.url, exc)
        raise ExtractionError(_friendly_error(str(exc), source.platform)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a readable message
        logger.exception("Unexpected extraction failure for %s", source.url)
        raise ExtractionError(
            f"Could not reach {source.label}. Please try again."
        ) from exc
    finally:
        if use_cache:
            _info_cache.finish(source.key)

    if not info:
        raise ExtractionError(f"{source.label} did not return any information for this link.")

    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            raise ExtractionError("This link does not contain a playable video.")
        info = entries[0]

    if info.get("live_status") in ("is_live", "is_upcoming"):
        raise ExtractionError("Live streams are not supported. Try again once the stream is published.")

    if not info.get("formats"):
        raise ExtractionError("No downloadable formats were found for this link.")

    info["_video_id"] = source.video_id or str(info.get("id") or "")
    info["_platform"] = source.platform
    info["_source_url"] = source.url
    if use_cache:
        _info_cache.put(source.key, info)
    return info


# --------------------------------------------------------------------------- #
# Metadata normalisation
# --------------------------------------------------------------------------- #

_BRACKET_GROUP = re.compile(r"[\(\[\{]([^\)\]\}]{0,80})[\)\]\}]")
_NOISE_WORDS = re.compile(
    r"official|lyrics?\b|\baudio\b|\bvideo\b|visuali[sz]er|\bm/v\b|\bmv\b|"
    r"remaster|\bhd\b|\bhq\b|\b4k\b|\b8k\b|explicit|clean|color.?coded|"
    r"behind the scenes|performance",
    re.IGNORECASE,
)
_COMPOSER_LINE = re.compile(
    r"^\s*(?:composer(?:s)?|songwriter(?:s)?|written\s+by|music\s+(?:and\s+lyrics\s+)?by|"
    r"lyrics?\s+by|produced\s+by)\s*[:\-–]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_title_noise(title: str) -> str:
    """Remove (Official Video), [4K Remaster], etc. without touching real words."""

    def replace(match: re.Match[str]) -> str:
        return "" if _NOISE_WORDS.search(match.group(1)) else match.group(0)

    previous = None
    current = title
    while previous != current:
        previous = current
        current = _BRACKET_GROUP.sub(replace, current)
    current = re.sub(r"\s{2,}", " ", current)
    current = re.sub(r"\s*[-–—]\s*$", "", current)
    return current.strip(" .-–—")


def _derive_artist_and_song(info: dict[str, Any]) -> tuple[str, str]:
    uploader = clean_text(info.get("uploader") or info.get("channel") or "", 120)
    artist = clean_text(info.get("artist") or info.get("creator") or "", 120)
    topic_channel = uploader.lower().endswith("- topic")
    if topic_channel:
        uploader = uploader[: -len("- topic")].strip()

    raw_title = clean_text(info.get("title") or "Unknown Title", 240)
    song = clean_text(info.get("track") or "", 240)
    guessed_artist = ""

    base = _strip_title_noise(raw_title) or raw_title
    if not song:
        if " - " in base and (topic_channel or not artist):
            left, right = base.split(" - ", 1)
            guessed_artist = left.strip()
            song = right.strip()
        else:
            song = base

    if artist and song.lower().startswith(artist.lower() + " - "):
        song = song[len(artist) + 3 :].strip()

    if not artist:
        artist = guessed_artist or (uploader if topic_channel else "")

    if not artist:
        artist = uploader

    return (artist or "Unknown Artist"), (song or base or raw_title)


def _derive_composer(info: dict[str, Any], artist: str) -> tuple[str, str]:
    """Return (composer, source). Most sources rarely expose composer, so fall back
    to a composer/songwriter credit found in the description."""
    direct = clean_text(info.get("composer") or "", 160)
    if direct:
        return direct, "youtube"

    description = info.get("description") or ""
    for match in _COMPOSER_LINE.finditer(description[:6000]):
        candidate = clean_text(match.group(1), 160)
        candidate = re.split(r"[|•·;]", candidate)[0].strip(" .,-–—")
        if 2 <= len(candidate) <= 120 and candidate.lower() != artist.lower():
            return candidate, "description"

    return "", "none"


def normalize_metadata(info: dict[str, Any]) -> dict[str, Any]:
    """Turn a raw yt-dlp dict into the values used by templates and tags."""
    platform = str(info.get("_platform") or "youtube").lower()
    artist, song_title = _derive_artist_and_song(info)
    if platform != "youtube":
        creator = clean_text(
            info.get("uploader")
            or info.get("channel")
            or info.get("creator")
            or "Unknown creator",
            120,
        )
        artist = creator
        song_title = clean_text(info.get("title") or "Untitled", 240)

    composer, composer_source = _derive_composer(info, artist)

    categories = [str(c) for c in (info.get("categories") or []) if c]
    genre = clean_text(info.get("genre") or "", 80)
    if not genre:
        # "Music" or "Film & Animation" are still useful tag values.
        genre = categories[0] if categories else ""

    year = ""
    release_year = info.get("release_year")
    if release_year:
        year = str(release_year)
    elif info.get("upload_date") and str(info["upload_date"])[:4].isdigit():
        year = str(info["upload_date"])[:4]

    duration = int(info.get("duration") or 0)
    video_id = info.get("_video_id") or info.get("id") or ""

    thumbnails = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    best_thumb = max(
        thumbnails,
        key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
        default=None,
    )
    thumbnail = (best_thumb or {}).get("url") or info.get("thumbnail") or ""
    # Browsers render webp fine, but prefer jpg for metadata/social previews.
    jpg_thumb = next(
        (t["url"] for t in reversed(thumbnails) if t["url"].lower().split("?")[0].endswith((".jpg", ".jpeg"))),
        "",
    )

    webpage_url = (
        info.get("webpage_url")
        or info.get("_source_url")
        or (canonical_url(video_id) if platform == "youtube" else "")
    )
    source_url = info.get("_source_url") or webpage_url
    description = clean_text(info.get("description") or "", 2000)

    is_music = bool(
        info.get("artist")
        or info.get("track")
        or info.get("album")
        or "music" in [c.lower() for c in categories]
    )
    channel_label = {
        "youtube": "Channel",
        "instagram": "Creator",
        "facebook": "Page",
    }.get(platform, "Creator")

    return {
        "video_id": video_id,
        "platform": platform,
        "platform_label": platform_label(platform),
        "is_youtube": platform == "youtube",
        "channel_label": channel_label,
        "url": webpage_url,
        "input_url": source_url,
        "title": clean_text(info.get("title") or "Unknown Title", 240),
        "song_title": song_title,
        "artist": artist,
        "album": clean_text(info.get("album") or "", 160),
        "album_artist": clean_text(info.get("album_artist") or artist, 160),
        "composer": composer,
        "composer_source": composer_source,
        "genre": genre,
        "year": year,
        "track_number": str(info.get("track_number") or ""),
        "duration": duration,
        "duration_formatted": human_duration(duration),
        "channel": clean_text(
            info.get("channel") or info.get("uploader") or "Unknown creator", 120
        ),
        "channel_url": info.get("channel_url") or info.get("uploader_url") or "",
        "view_count": int(info.get("view_count") or 0),
        "view_count_formatted": compact_number(info.get("view_count")),
        "like_count": int(info.get("like_count") or 0),
        "like_count_formatted": compact_number(info.get("like_count")),
        "upload_date": str(info.get("upload_date") or ""),
        "upload_date_formatted": _format_upload_date(str(info.get("upload_date") or "")),
        "thumbnail": thumbnail,
        "thumbnail_jpg": jpg_thumb,
        "description": description,
        "categories": categories,
        "is_music": is_music,
        "webpage_url": webpage_url,
    }


def _format_upload_date(raw: str) -> str:
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


# --------------------------------------------------------------------------- #
# Format catalog
# --------------------------------------------------------------------------- #

_HDR_MARKERS = ("hdr", "hlg", "pq")


def _usable_format(fmt: dict[str, Any]) -> bool:
    if not fmt.get("url"):
        return False
    if fmt.get("has_drm"):
        return False
    if (fmt.get("protocol") or "").startswith("mhtml"):
        return False
    note = (fmt.get("format_note") or "").lower()
    if "storyboard" in note or "drm" in note:
        return False
    return True


def _is_video(fmt: dict[str, Any]) -> bool:
    return _usable_format(fmt) and fmt.get("vcodec") not in (None, "none") and bool(fmt.get("height"))


def _display_height(fmt: dict[str, Any]) -> int:
    """Portrait reels are 1080×1920 — people call that 1080p, not 1920p."""
    height = int(fmt.get("height") or 0)
    width = int(fmt.get("width") or 0)
    if height and width:
        return min(height, width)
    return height


def _is_vertical(fmt: dict[str, Any]) -> bool:
    width = int(fmt.get("width") or 0)
    height = int(fmt.get("height") or 0)
    return bool(width and height and height > width)


def _is_direct(fmt: dict[str, Any]) -> bool:
    return str(fmt.get("protocol") or "").startswith(("http", "https"))


def _is_audio(fmt: dict[str, Any]) -> bool:
    return (
        _usable_format(fmt)
        and fmt.get("vcodec") in (None, "none")
        and fmt.get("acodec") not in (None, "none")
        and (fmt.get("abr") or fmt.get("tbr"))
    )


def _codec_family(fmt: dict[str, Any]) -> str:
    codec = (fmt.get("vcodec") or "").lower()
    if codec.startswith(("avc", "h264")):
        return "h264"
    if codec.startswith(("av01", "av1")):
        return "av1"
    if codec.startswith(("vp9", "vp09", "vp8")):
        return "vp9"
    if codec.startswith(("hev", "hvc", "h265")):
        return "h265"
    return "other"


def _candidate_rank(fmt: dict[str, Any], family: str) -> tuple:
    """Lower is better: direct HTTP first, then a sensible container, then bitrate."""
    direct = 0 if str(fmt.get("protocol") or "").startswith(("http", "https")) else 1
    ext = str(fmt.get("ext") or "")
    if family == "vp9":
        container = 0 if ext == "webm" else 1
    else:
        container = 0 if ext in ("mp4", "m4a") else 1
    return (direct, container, -(fmt.get("tbr") or 0))


_FAMILY_INFO = {
    "h264": ("H.264", "mp4", 0),
    "h265": ("H.265", "mp4", 1),
    "av1": ("AV1", "mp4", 2),
    "vp9": ("VP9", "webm", 3),
}


def _is_hdr(fmt: dict[str, Any]) -> bool:
    text = " ".join(
        str(fmt.get(key) or "") for key in ("format_note", "dynamic_range", "format")
    ).lower()
    return any(marker in text for marker in _HDR_MARKERS)


def _estimate_size(fmt: dict[str, Any], duration: int) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return int(size)
    tbr = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if tbr and duration:
        return int(float(tbr) * 1000 / 8 * duration)
    return None


def _best_audio(info: dict[str, Any], prefer_ext: str | None = None) -> dict[str, Any] | None:
    candidates = [fmt for fmt in (info.get("formats") or []) if _is_audio(fmt)]
    if not candidates:
        return None
    if prefer_ext:
        preferred = [fmt for fmt in candidates if fmt.get("ext") == prefer_ext]
        if preferred:
            candidates = preferred
    return max(candidates, key=lambda f: (f.get("abr") or f.get("tbr") or 0))


def _best_progressive_video(info: dict[str, Any]) -> dict[str, Any] | None:
    """Best single-file video that already contains audio (Instagram/Facebook)."""
    candidates = [
        fmt
        for fmt in (info.get("formats") or [])
        if _is_video(fmt) and fmt.get("acodec") not in (None, "none")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda f: (_display_height(f), f.get("tbr") or f.get("vbr") or 0),
    )


def _audio_for_container(info: dict[str, Any], container: str) -> dict[str, Any] | None:
    preferred = "m4a" if container == "mp4" else "webm"
    audio = _best_audio(info, preferred)
    if audio is None:
        audio = _best_audio(info)
    return audio


def _quality_name(height: int) -> str:
    return {
        4320: "8K",
        2160: "4K",
        1440: "2K",
        1080: "Full HD",
        720: "HD",
        480: "SD",
        360: "SD",
        240: "Low",
        144: "Low",
    }.get(height, f"{height}p")


def _size_label(size: int | None) -> str:
    return f"~{human_size(size)}" if size else "size varies"


def build_video_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the video qualities that can actually be delivered."""
    duration = int(info.get("duration") or 0)
    usable = [
        fmt
        for fmt in (info.get("formats") or [])
        if _is_video(fmt) and 0 < _display_height(fmt) <= config.MAX_VIDEO_HEIGHT
    ]
    if not usable:
        return []

    by_height: dict[int, list[dict[str, Any]]] = {}
    for fmt in usable:
        by_height.setdefault(_display_height(fmt), []).append(fmt)

    options: list[dict[str, Any]] = []

    for height in sorted(by_height, reverse=True):
        group = by_height[height]
        vertical = any(_is_vertical(fmt) for fmt in group)
        candidates: dict[str, dict[str, Any]] = {}

        if config.FFMPEG_OK:
            for fmt in group:
                if fmt.get("acodec") not in (None, "none"):
                    continue
                family = _codec_family(fmt)
                if family == "other":
                    continue
                current = candidates.get(family)
                if current is None or _candidate_rank(fmt, family) < _candidate_rank(current, family):
                    candidates[family] = fmt

        made_for_height = 0
        for family in ("h264", "h265", "av1", "vp9"):
            fmt = candidates.get(family)
            if not fmt or made_for_height >= 3:
                continue
            label, container, _ = _FAMILY_INFO[family]
            if family == "vp9" and fmt.get("ext") != "webm":
                container = "mp4"
            audio = _audio_for_container(info, container)
            if audio is None:
                continue
            fps = round(float(fmt.get("fps") or 0))
            video_size = _estimate_size(fmt, duration) or 0
            audio_size = _estimate_size(audio, duration) or 0
            estimated = int((video_size + audio_size) * 1.02) if video_size else None
            hdr = _is_hdr(fmt)
            badges = [_quality_name(height), label, container.upper()]
            if vertical:
                badges.append("Portrait")
            if fps >= 50:
                badges.append(f"{fps} fps")
            if hdr:
                badges.append("HDR")
            options.append(
                {
                    "id": f"v{height}-{family}-{container}",
                    "kind": "video",
                    "height": height,
                    "quality": f"{height}p",
                    "tier": _quality_name(height),
                    "fps": fps,
                    "container": container,
                    "codec": label,
                    "codec_family": family,
                    "format_id": str(fmt.get("format_id") or ""),
                    "audio_format_id": str(audio.get("format_id") or ""),
                    "needs_merge": True,
                    "has_audio": False,
                    "direct": True,
                    "vertical": vertical,
                    "hdr": hdr,
                    "badges": badges,
                    "estimated_size": estimated,
                    "size_label": _size_label(estimated),
                    "recommended": False,
                    "note": f"{label} video + {str(audio.get('acodec') or 'audio').split('.')[0].upper()} audio",
                }
            )
            made_for_height += 1

        # Progressive (video+audio in one file) is the usual shape of an
        # Instagram or Facebook link. It is also the fallback when ffmpeg is
        # missing, because no muxing is needed.
        if made_for_height == 0:
            progressive = [
                fmt
                for fmt in group
                if fmt.get("acodec") not in (None, "none")
                and (config.FFMPEG_OK or _is_direct(fmt))
            ]
            if progressive:
                fmt = max(progressive, key=lambda f: (f.get("tbr") or 0))
                fps = round(float(fmt.get("fps") or 0))
                size = _estimate_size(fmt, duration)
                ext = str(fmt.get("ext") or "mp4")
                container = "mp4" if ext in ("mp4", "m4a") else ext
                family = _codec_family(fmt)
                label = _FAMILY_INFO.get(family, ("Video", container, 0))[0]
                badges = [_quality_name(height), label, container.upper(), "Ready to play"]
                if vertical:
                    badges.append("Portrait")
                if fps >= 50:
                    badges.append(f"{fps} fps")
                options.append(
                    {
                        "id": f"v{height}-progressive-{container}",
                        "kind": "video",
                        "height": height,
                        "quality": f"{height}p",
                        "tier": _quality_name(height),
                        "fps": fps,
                        "container": container,
                        "codec": label,
                        "codec_family": family,
                        "format_id": str(fmt.get("format_id") or ""),
                        "audio_format_id": "",
                        "needs_merge": False,
                        "has_audio": True,
                        "direct": _is_direct(fmt),
                        "vertical": vertical,
                        "hdr": _is_hdr(fmt),
                        "badges": badges,
                        "estimated_size": size,
                        "size_label": _size_label(size),
                        "recommended": False,
                        "note": "Single file with audio · ready to play",
                    }
                )

    # Mark one recommendation: 1080p H.264 MP4 when possible, else best ≤1080p.
    if options:
        def score(option: dict[str, Any]) -> tuple:
            height = option["height"]
            return (
                0 if height == 1080 else 1 if height < 1080 else 2,
                0 if option["container"] == "mp4" else 1,
                0 if option.get("codec_family") == "h264" else 1,
                -height,
            )

        best = sorted(options, key=score)[0]
        best["recommended"] = True

    return options


# Audio profiles that require ffmpeg. Order is the order shown in the UI.
AUDIO_PROFILES: dict[str, dict[str, Any]] = {
    "mp3_320": {
        "label": "MP3",
        "detail": "320 kbps",
        "ext": "mp3",
        "bitrate": 320,
        "tags": True,
        "cover": True,
        "lossless": False,
        "badges": ["Cover art", "Artist & composer", "Plays everywhere"],
        "description": "Highest quality MP3 with full ID3 tags and the video cover art.",
    },
    "mp3_192": {
        "label": "MP3",
        "detail": "192 kbps",
        "ext": "mp3",
        "bitrate": 192,
        "tags": True,
        "cover": True,
        "lossless": False,
        "badges": ["Cover art", "Artist & composer", "Smaller file"],
        "description": "Balanced MP3 for phones and older players.",
    },
    "m4a_256": {
        "label": "M4A",
        "detail": "AAC 256 kbps",
        "ext": "m4a",
        "bitrate": 256,
        "tags": True,
        "cover": False,
        "lossless": False,
        "badges": ["AAC audio", "Artist & composer", "Apple friendly"],
        "description": "AAC in an MP4 container — ideal for iPhone, iPad and macOS.",
    },
    "flac": {
        "label": "FLAC",
        "detail": "Lossless",
        "ext": "flac",
        "bitrate": 0,
        "tags": True,
        "cover": True,
        "lossless": True,
        "badges": ["Lossless", "Cover art", "Archival"],
        "description": "Lossless FLAC with cover art and full tags. Large, but pristine.",
    },
    "wav": {
        "label": "WAV",
        "detail": "PCM 16-bit",
        "ext": "wav",
        "bitrate": 1411,
        "tags": True,
        "cover": False,
        "lossless": True,
        "badges": ["Uncompressed", "Editing ready"],
        "description": (
            "Uncompressed WAV for editing. Basic title/artist/album/year tags are included; "
            "WAV has no cover-art or composer slot."
        ),
    },
}

_AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
}


def build_audio_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Return audio options, including the untouched original when one exists.

    Instagram and Facebook usually expose a single progressive video file with
    no standalone audio stream. In that case converted formats are still offered
    and ffmpeg extracts the audio track, but "original stream" is hidden because
    it would really be a video download.
    """
    duration = int(info.get("duration") or 0)
    options: list[dict[str, Any]] = []

    source = _best_audio(info)
    progressive = None if source else _best_progressive_video(info)

    if source:
        ext = str(source.get("ext") or "m4a")
        abr = int(round(float(source.get("abr") or source.get("tbr") or 0)))
        size = _estimate_size(source, duration)
        options.append(
            {
                "id": f"a-original-{source.get('format_id')}",
                "kind": "audio",
                "profile": "original",
                "format_id": str(source.get("format_id") or ""),
                "label": "Original stream",
                "detail": f"{ext.upper()} · {abr or '?'} kbps",
                "ext": ext,
                "bitrate": abr,
                "estimated_size": size,
                "size_label": _size_label(size),
                "tags": False,
                "cover": False,
                "lossless": False,
                "badges": ["No re-encoding", "Fastest"],
                "description": (
                    "Downloads the exact audio stream the platform serves, with zero "
                    "quality loss and no embedded tags."
                ),
                "recommended": False,
            }
        )

    if config.FFMPEG_OK and (source or progressive):
        for key, profile in AUDIO_PROFILES.items():
            bitrate = profile["bitrate"]
            if profile["lossless"]:
                if key == "wav":
                    estimated = int(duration * 44100 * 2 * 2) if duration else None
                else:
                    estimated = int(duration * 200_000) if duration else None
            else:
                estimated = int(duration * bitrate * 1000 / 8) if duration and bitrate else None
            description = profile["description"]
            if progressive:
                description = "Extracted from the video, then " + description[0].lower() + description[1:]
            options.append(
                {
                    "id": f"a-{key}",
                    "kind": "audio",
                    "profile": key,
                    "format_id": "",
                    "label": profile["label"],
                    "detail": profile["detail"],
                    "ext": profile["ext"],
                    "bitrate": bitrate,
                    "estimated_size": estimated,
                    "size_label": _size_label(estimated),
                    "tags": profile["tags"],
                    "cover": profile["cover"],
                    "lossless": profile["lossless"],
                    "badges": profile["badges"],
                    "description": description,
                    "recommended": key == "mp3_320",
                    "from_video": bool(progressive),
                }
            )

    return options


def build_view(info: dict[str, Any]) -> dict[str, Any]:
    """Everything the preview page and JSON API need."""
    metadata = normalize_metadata(info)
    video_options = build_video_options(info)
    audio_options = build_audio_options(info)
    source = _best_audio(info)
    progressive = None if source else _best_progressive_video(info)

    max_height = max((option["height"] for option in video_options), default=0)
    has_hdr = any(option.get("hdr") for option in video_options)
    has_60fps = any(option.get("fps", 0) >= 50 for option in video_options)
    vertical = any(option.get("vertical") for option in video_options)

    if source:
        audio_source = (
            f"{str(source.get('ext') or '').upper()} · "
            f"{int(round(float(source.get('abr') or 0)))} kbps"
        )
    elif progressive:
        audio_source = "Extracted from video"
    else:
        audio_source = "—"

    metadata.update(
        {
            "video_options": video_options,
            "audio_options": audio_options,
            "max_height": max_height,
            "max_quality": _quality_name(max_height) if max_height else "—",
            "has_4k": max_height >= 2160,
            "has_hdr": has_hdr,
            "has_60fps": has_60fps,
            "vertical": vertical,
            "has_original_audio": bool(source),
            "audio_source": audio_source,
            "ffmpeg_ok": config.FFMPEG_OK,
            "tags": {
                "title": metadata["song_title"],
                "artist": metadata["artist"],
                "album": metadata["album"],
                "composer": metadata["composer"],
                "year": metadata["year"],
                "genre": metadata["genre"],
                "track": metadata["track_number"],
            },
        }
    )
    return metadata


def find_video_format(info: dict[str, Any], option_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve an option id from the preview page into (video, audio) formats."""
    options = build_video_options(info)
    option = next((item for item in options if item["id"] == option_id), None)
    if option is None:
        raise UnavailableError(
            "That video quality is no longer available. Refresh the preview and pick a quality again."
        )

    formats = {str(fmt.get("format_id")): fmt for fmt in (info.get("formats") or [])}
    video = formats.get(option["format_id"])
    if video is None:
        raise UnavailableError("The available streams changed. Refresh the preview and try again.")

    audio = None
    if option["needs_merge"]:
        audio = formats.get(option["audio_format_id"])
        if audio is None:
            audio = _audio_for_container(info, option["container"])
        if audio is None:
            raise UnavailableError("No matching audio stream was found for this quality.")
    return video, audio


def find_audio_format(
    info: dict[str, Any],
    format_id: str | None,
    prefer_abr: bool = True,
    allow_progressive: bool = False,
) -> dict[str, Any]:
    """Find an audio source by id, falling back to the best available stream.

    ``allow_progressive`` lets converted-audio downloads use a video file whose
    audio track ffmpeg can extract (common on Instagram and Facebook).
    """
    formats = [fmt for fmt in (info.get("formats") or []) if _is_audio(fmt)]
    if format_id:
        match = next((fmt for fmt in formats if str(fmt.get("format_id")) == str(format_id)), None)
        if match is not None:
            return match
    source = _best_audio(info) if prefer_abr else None
    if source is not None:
        return source
    if allow_progressive:
        progressive = _best_progressive_video(info)
        if progressive is not None:
            return progressive
    raise UnavailableError("This link does not expose a usable audio stream.")


def audio_mime(ext: str) -> str:
    return _AUDIO_MIME.get(ext.lower(), "application/octet-stream")


def clear_cache() -> None:
    _info_cache.clear()

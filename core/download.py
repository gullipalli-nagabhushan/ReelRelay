"""Download planning and execution.

A *plan* describes exactly what will be sent to the browser (filename, mime
type, stream URLs, ffmpeg arguments, tags).  Building a plan is cheap and
side-effect free, which lets ``/api/prepare`` validate a click before the
browser starts fetching.  Executing a plan returns a streaming iterator — the
server never writes the media to disk.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config, cover, id3, lyrics
from .accelerator import open_accelerator
from .streaming import DownloadError, FFmpegStream, PreparedStream, RelayStream
from .utils import clean_lyrics, clean_text, human_size, sanitize_filename
from .ytdlp import (
    AUDIO_PROFILES,
    UnavailableError,
    audio_mime,
    build_audio_options,
    build_video_options,
    find_audio_format,
    find_video_format,
    normalize_metadata,
    pick_preview_option,
    platform_label,
)

logger = logging.getLogger(__name__)

_VIDEO_MIME = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "mov": "video/quicktime",
}

_PLATFORM_ORIGINS = {
    "youtube": "https://www.youtube.com/",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
}

_TAG_KEYS = (
    "title",
    "artist",
    "album",
    "composer",
    "genre",
    "year",
    "track",
    "disc",
    "copyright",
    "publisher",
    "isrc",
)


@dataclass
class DownloadPlan:
    kind: str
    mode: str  # "relay" | "video" | "audio"
    filename: str
    media_type: str
    label: str
    quality: str
    size_label: str
    platform: str = "youtube"
    notes: list[str] = field(default_factory=list)
    content_length: int | None = None
    # ffmpeg output has no exact length up front. This is a best-effort size
    # used only for the progress bar/ETA, never for Content-Length.
    estimated_total: int | None = None

    # Streaming details
    stream_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    container: str = ""
    profile: str | None = None
    video_format: dict[str, Any] | None = None
    audio_format: dict[str, Any] | None = None
    copy_audio_from_video: bool = False

    # Tagging
    tags: dict[str, str] = field(default_factory=dict)
    embed_tags: bool = False
    embed_cover: bool = False
    cover_urls: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "media_type": self.media_type,
            "label": self.label,
            "quality": self.quality,
            "kind": self.kind,
            "size_label": self.size_label,
            "mode": self.mode,
            "notes": self.notes,
            "estimated_total": self.estimated_total,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _stream_headers(fmt: dict[str, Any], platform: str | None = None) -> dict[str, str]:
    source = {str(k).lower(): str(v) for k, v in (fmt.get("http_headers") or {}).items()}
    origin = _PLATFORM_ORIGINS.get(str(platform or "youtube").lower(), _PLATFORM_ORIGINS["youtube"])
    headers = {
        "User-Agent": source.get("user-agent") or config.DEFAULT_USER_AGENT,
        "Referer": source.get("referer") or origin,
        "Origin": origin.rstrip("/"),
        "Accept-Language": source.get("accept-language") or "en-US,en;q=0.9",
    }
    cookie = source.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _is_manifest(fmt: dict[str, Any]) -> bool:
    """Manifests must stay on the origin so relative segment URLs resolve."""
    protocol = str(fmt.get("protocol") or "").lower()
    if any(token in protocol for token in ("m3u8", "dash", "ism")):
        return True
    if fmt.get("manifest_url") or fmt.get("fragments"):
        return True
    path = str(fmt.get("url") or "").split("?", 1)[0].lower()
    return path.endswith((".m3u8", ".mpd", ".ism"))


def _input_url(
    fmt: dict[str, Any],
    platform: str | None,
    *,
    cleanups: list[Callable[[], None]] | None,
    content_type: str,
    label: str,
) -> str:
    """Return the URL ffmpeg should read, accelerating it when possible."""
    url = str(fmt.get("url") or "")
    if cleanups is None or not url or _is_manifest(fmt):
        return url
    try:
        accelerator = open_accelerator(
            url,
            _stream_headers(fmt, platform),
            content_type=content_type,
            label=label,
        )
    except Exception:  # noqa: BLE001 - acceleration is best-effort
        logger.exception("Download accelerator failed for %s; using the direct URL", label)
        return url
    if accelerator is None:
        return url
    cleanups.append(accelerator.close)
    return accelerator.url


def _ffmpeg_input(
    fmt: dict[str, Any],
    platform: str | None = None,
    *,
    cleanups: list[Callable[[], None]] | None = None,
    content_type: str = "video/mp4",
    label: str = "media",
) -> list[str]:
    headers = _stream_headers(fmt, platform)
    header_lines = [
        f"Referer: {headers['Referer']}\r\n",
        f"Origin: {headers['Origin']}\r\n",
        f"Accept-Language: {headers['Accept-Language']}\r\n",
    ]
    if headers.get("Cookie"):
        header_lines.append(f"Cookie: {headers['Cookie']}\r\n")
    return [
        "-user_agent",
        headers["User-Agent"],
        "-headers",
        "".join(header_lines),
        "-rw_timeout",
        "30000000",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_at_eof",
        "1",
        "-reconnect_on_network_error",
        "1",
        "-reconnect_max_retries",
        "8",
        "-reconnect_delay_max",
        "5",
        "-i",
        _input_url(
            fmt,
            platform,
            cleanups=cleanups,
            content_type=content_type,
            label=label,
        ),
    ]


def _ffmpeg_base() -> list[str]:
    if not config.FFMPEG:
        raise UnavailableError(
            "ffmpeg is not installed on this computer, so this format cannot be generated."
        )
    return [
        config.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
    ]


def _metadata_args(tags: dict[str, str], *, with_lyrics: bool = True) -> list[str]:
    pairs = [
        ("title", tags.get("title", "")),
        ("artist", tags.get("artist", "")),
        ("album_artist", tags.get("album_artist", "") or tags.get("artist", "")),
        ("album", tags.get("album", "")),
        ("composer", tags.get("composer", "")),
        ("date", tags.get("date", "") or tags.get("year", "")),
        ("genre", tags.get("genre", "")),
        ("track", tags.get("track", "")),
        ("disc", tags.get("disc", "")),
        ("comment", tags.get("comment", "")),
        ("copyright", tags.get("copyright", "")),
        ("isrc", tags.get("isrc", "")),
        ("publisher", tags.get("publisher", "")),
        ("encoder", "ReelRelay"),
    ]
    if with_lyrics:
        pairs.append(("lyrics", tags.get("lyrics", "")))

    args: list[str] = ["-map_metadata", "-1"]
    for key, value in pairs:
        # Lyrics keep their line breaks; everything else is single-line text.
        value = clean_lyrics(value) if key == "lyrics" else clean_text(value, 240)
        if value:
            args.extend(["-metadata", f"{key}={value}"])
    return args


def _normalise_tags(info: dict[str, Any], overrides: dict[str, str] | None) -> dict[str, str]:
    # Raw yt-dlp data has no derived artist/song split, so normalise it here.
    meta = normalize_metadata(info) if "song_title" not in info else info

    track = str(meta.get("track_number") or "")
    track_total = str(meta.get("track_total") or "")
    if track and track_total and "/" not in track:
        track = f"{track}/{track_total}"

    disc = str(meta.get("disc_number") or "")
    disc_total = str(meta.get("disc_total") or "")
    if disc and disc_total and "/" not in disc:
        disc = f"{disc}/{disc_total}"

    source_year = str(meta.get("year") or "")

    defaults = {
        "title": meta.get("song_title") or meta.get("title") or "",
        "artist": meta.get("artist") or meta.get("channel") or "",
        "album_artist": meta.get("album_artist") or meta.get("artist") or "",
        "album": meta.get("album") or "",
        "composer": meta.get("composer") or "",
        "genre": meta.get("genre") or "",
        "year": meta.get("year") or "",
        "date": meta.get("release_date") or meta.get("year") or "",
        "track": track,
        "disc": disc,
        "comment": f"Source: {meta.get('webpage_url') or meta.get('url') or ''}",
        "copyright": meta.get("copyright") or "",
        "isrc": meta.get("isrc") or "",
        "publisher": meta.get("publisher") or "",
    }
    for key in _TAG_KEYS:
        value = (overrides or {}).get(key)
        if value is not None and str(value).strip():
            defaults[key] = clean_text(str(value), 200)
    if "album_artist" in defaults and not defaults["album_artist"]:
        defaults["album_artist"] = defaults["artist"]
    # Keep the full release date unless the user actually changed the year.
    year_override = clean_text(str((overrides or {}).get("year") or ""), 200)
    if year_override and year_override != source_year:
        defaults["date"] = year_override
    return defaults


def _base_name(info: dict[str, Any], tags: dict[str, str]) -> str:
    artist = clean_text(tags.get("artist") or info.get("artist") or "", 90)
    title = clean_text(tags.get("title") or info.get("song_title") or info.get("title") or "", 120)
    categories = [str(category).lower() for category in (info.get("categories") or [])]
    is_music = bool(info.get("artist") or info.get("track") or "music" in categories)
    if is_music and artist and artist.lower() not in title.lower():
        return sanitize_filename(f"{artist} - {title}", 150)
    return sanitize_filename(title or artist or "download", 150)


def _size_from_formats(video: dict[str, Any] | None, audio: dict[str, Any] | None, duration: int) -> int | None:
    total = 0
    for fmt in (video, audio):
        if not fmt:
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if not size:
            tbr = fmt.get("tbr") or fmt.get("abr")
            if tbr and duration:
                size = int(float(tbr) * 1000 / 8 * duration)
        if not size:
            return None
        total += int(size)
    return total or None


# --------------------------------------------------------------------------- #
# Plan builders
# --------------------------------------------------------------------------- #


def plan_video(
    info: dict[str, Any],
    *,
    option_id: str | None,
    height: int | None,
    container: str | None,
    tags_override: dict[str, str] | None,
) -> DownloadPlan:
    options = build_video_options(info)
    option = next((item for item in options if item["id"] == option_id), None)

    if option is None and height:
        # Fall back to a similar option if the page is a little stale.
        wanted_container = (container or "mp4").lower()
        option = next(
            (
                item
                for item in options
                if int(item["height"]) == int(height) and item["container"] == wanted_container
            ),
            None,
        )
    if option is None and height:
        option = next((item for item in options if int(item["height"]) == int(height)), None)
    if option is None:
        raise UnavailableError(
            "That video quality is not available. Refresh the preview and choose a quality again."
        )

    video_fmt, audio_fmt = find_video_format(info, option["id"])
    duration = int(info.get("duration") or 0)
    platform = str(info.get("_platform") or "youtube").lower()
    tags = _normalise_tags(info, tags_override)
    base = _base_name(info, tags)
    quality_suffix = option["quality"]
    container = option["container"]

    notes: list[str] = []
    if option["needs_merge"]:
        notes.append("Video and audio are muxed live while streaming — the first bytes take a few seconds.")

    if not option["needs_merge"] and not option.get("direct", True):
        # HLS / DASH manifests cannot be relayed byte-for-byte, so ffmpeg
        # remuxes the single stream (audio included) while it delivers it.
        ext = str(video_fmt.get("ext") or container)
        filename = f"{base} [{quality_suffix}].{ext}"
        size = _size_from_formats(video_fmt, None, duration)
        notes.append("This link uses a segmented stream, so ffmpeg remuxes it live.")
        return DownloadPlan(
            kind="video",
            mode="video",
            filename=sanitize_filename(filename, 180),
            media_type=_VIDEO_MIME.get(ext, "video/mp4"),
            label=option["codec"],
            quality=quality_suffix,
            size_label=option["size_label"],
            platform=platform,
            notes=notes,
            content_length=None,
            estimated_total=size,
            container=ext,
            video_format=video_fmt,
            headers=_stream_headers(video_fmt, platform),
            copy_audio_from_video=True,
        )

    if not option["needs_merge"]:
        ext = str(video_fmt.get("ext") or container)
        filename = f"{base} [{quality_suffix}].{ext}"
        size = _size_from_formats(video_fmt, None, duration)
        return DownloadPlan(
            kind="video",
            mode="relay",
            filename=sanitize_filename(filename, 180),
            media_type=_VIDEO_MIME.get(ext, "application/octet-stream"),
            label=option["codec"],
            quality=quality_suffix,
            size_label=option["size_label"],
            platform=platform,
            notes=notes,
            content_length=size,
            estimated_total=size,
            stream_url=str(video_fmt["url"]),
            headers=_stream_headers(video_fmt, platform),
            container=ext,
            video_format=video_fmt,
        )

    if audio_fmt is None:
        raise UnavailableError("This quality has no matching audio stream.")

    filename = f"{base} [{quality_suffix}].{container}"
    size = _size_from_formats(video_fmt, audio_fmt, duration)
    return DownloadPlan(
        kind="video",
        mode="video",
        filename=sanitize_filename(filename, 180),
        media_type=_VIDEO_MIME.get(container, "video/mp4"),
        label=option["codec"],
        quality=quality_suffix,
        size_label=human_size(size) if size else option["size_label"],
        platform=platform,
        notes=notes,
        content_length=None,  # live muxing: length is not known up front
        estimated_total=size,
        container=container,
        video_format=video_fmt,
        audio_format=audio_fmt,
        headers=_stream_headers(video_fmt, platform),
    )


def plan_preview(info: dict[str, Any]) -> DownloadPlan | None:
    """Build a small, browser-playable plan for the inline preview player.

    Returns ``None`` when the link exposes no rendition a ``<video>`` element
    can play, so the page can fall back to a thumbnail instead of a broken
    player.
    """
    option = pick_preview_option(info)
    if option is None:
        return None
    try:
        plan = plan_video(
            info,
            option_id=option["id"],
            height=None,
            container=None,
            tags_override=None,
        )
    except UnavailableError:
        return None
    if plan.media_type not in ("video/mp4", "video/webm", "video/x-matroska", "video/quicktime"):
        return None
    plan.notes.append("Preview stream — small and optimised for instant playback.")
    return plan


def plan_audio(
    info: dict[str, Any],
    *,
    profile: str,
    format_id: str | None,
    tags_override: dict[str, str] | None,
    embed_tags: bool,
    embed_cover: bool,
) -> DownloadPlan:
    duration = int(info.get("duration") or 0)
    platform = str(info.get("_platform") or "youtube").lower()
    tags = _normalise_tags(info, tags_override)
    base = _base_name(info, tags)
    cover_urls = cover.cover_candidates(info)

    if profile == "original":
        source = find_audio_format(info, format_id, allow_progressive=False)
        ext = str(source.get("ext") or "m4a")
        size = source.get("filesize") or source.get("filesize_approx")
        if not size and duration:
            tbr = source.get("abr") or source.get("tbr")
            size = int(float(tbr) * 1000 / 8 * duration) if tbr else None
        return DownloadPlan(
            kind="audio",
            mode="relay",
            filename=sanitize_filename(f"{base}.{ext}", 180),
            media_type=audio_mime(ext),
            label="Original audio",
            quality=f"{int(round(float(source.get('abr') or 0)))} kbps",
            size_label=human_size(size),
            platform=platform,
            notes=[f"Sent byte-for-byte as {platform_label(platform)} encodes it — no re-encoding, no embedded tags."],
            content_length=int(size) if size else None,
            estimated_total=int(size) if size else None,
            stream_url=str(source["url"]),
            headers=_stream_headers(source, platform),
            container=ext,
            profile="original",
            audio_format=source,
            cover_urls=cover_urls,
        )

    definition = AUDIO_PROFILES.get(profile)
    if definition is None:
        raise UnavailableError("Unknown audio format. Refresh the preview and pick a format again.")
    if not config.FFMPEG_OK:
        raise UnavailableError("ffmpeg is required for converted audio (MP3, FLAC, M4A, WAV).")

    source = find_audio_format(info, None, allow_progressive=True)
    from_video = source.get("vcodec") not in (None, "none")
    ext = definition["ext"]
    bitrate = definition["bitrate"]
    estimated = int(duration * bitrate * 1000 / 8) if duration and bitrate else None
    if profile == "wav":
        estimated = int(duration * 44100 * 2 * 2) if duration else None
    elif profile == "flac":
        estimated = int(duration * 200_000) if duration else None

    tags_supported = bool(definition["tags"])
    cover_supported = bool(definition["cover"])
    notes: list[str] = []

    # WAV has no lyrics field, so asking for them there would only cost a
    # request. MP3, FLAC and M4A get USLT / LYRICS / ©lyr respectively.
    if embed_tags and tags_supported and definition["ext"] != "wav":
        try:
            found_lyrics = lyrics.lookup(info, tags, duration)
        except Exception:  # noqa: BLE001 - lyrics are optional
            logger.exception("Lyrics lookup failed; continuing without lyrics")
            found_lyrics = ""
        if found_lyrics:
            tags["lyrics"] = found_lyrics
            notes.append("Lyrics are embedded.")

    if from_video:
        notes.append("This link has no standalone audio stream, so the audio track is extracted from the video.")
    if profile == "mp3_320" or profile == "mp3_192":
        notes.append("Cover art and tags are injected into the stream as it is sent.")
    if profile == "m4a_256":
        notes.append("Tags are embedded; cover art is not supported in streamed M4A — use MP3 or FLAC for artwork.")
    if profile == "wav":
        notes.append("WAV supports basic title/artist/album/date/genre/track/copyright tags only — no cover art, lyrics or composer.")
    if definition["lossless"]:
        notes.append("Lossless output — expect a large file.")

    return DownloadPlan(
        kind="audio",
        mode="audio",
        filename=sanitize_filename(f"{base}.{ext}", 180),
        media_type=audio_mime(ext),
        label=f"{definition['label']} {definition['detail']}",
        quality=definition["detail"],
        size_label=human_size(estimated) if estimated else "size varies",
        platform=platform,
        notes=notes,
        content_length=None,
        estimated_total=estimated,
        container=ext,
        profile=profile,
        audio_format=source,
        tags=tags,
        embed_tags=bool(embed_tags and tags_supported),
        embed_cover=bool(embed_cover and cover_supported),
        cover_urls=cover_urls,
    )


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _video_command(
    plan: DownloadPlan,
    cleanups: list[Callable[[], None]] | None = None,
) -> list[str]:
    assert plan.video_format is not None
    command = _ffmpeg_base()
    command += _ffmpeg_input(
        plan.video_format,
        plan.platform,
        cleanups=cleanups,
        content_type=plan.media_type,
        label="video",
    )
    if plan.audio_format is not None:
        audio_ext = str(plan.audio_format.get("ext") or "m4a")
        command += _ffmpeg_input(
            plan.audio_format,
            plan.platform,
            cleanups=cleanups,
            content_type=audio_mime(audio_ext),
            label="audio",
        )
        command += ["-map", "0:v:0", "-map", "1:a:0"]
    elif plan.copy_audio_from_video:
        command += ["-map", "0:v:0", "-map", "0:a:0?"]
    else:
        command += ["-map", "0:v:0"]
    command += ["-c", "copy", "-map_metadata", "-1", "-max_muxing_queue_size", "4096"]
    if plan.container == "mp4":
        command += [
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
        ]
    elif plan.container == "webm":
        command += ["-f", "webm"]
    else:
        command += ["-f", "matroska"]
    command += ["pipe:1"]
    return command


def _audio_command(
    plan: DownloadPlan,
    cover_path: str | None,
    cleanups: list[Callable[[], None]] | None = None,
) -> list[str]:
    assert plan.audio_format is not None
    command = _ffmpeg_base()
    command += _ffmpeg_input(
        plan.audio_format,
        plan.platform,
        cleanups=cleanups,
        content_type=plan.media_type,
        label="audio",
    )
    if cover_path:
        command += ["-i", cover_path, "-map", "0:a:0", "-map", "1:v:0", "-c:v", "copy"]
        command += ["-disposition:v", "attached_pic"]
    else:
        command += ["-map", "0:a:0"]

    profile = plan.profile
    if profile in ("mp3_320", "mp3_192"):
        bitrate = "320k" if profile == "mp3_320" else "192k"
        # Tags are added by the Python ID3 writer so they survive a pipe.
        command += [
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-id3v2_version",
            "0",
            "-write_id3v1",
            "0",
            "-map_metadata",
            "-1",
        ]
        command += ["-f", "mp3", "pipe:1"]
        return command

    if profile == "flac":
        command += ["-c:a", "flac", "-compression_level", "5"]
        command += _metadata_args(plan.tags)
        command += ["-f", "flac", "pipe:1"]
        return command

    if profile == "m4a_256":
        command += [
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
        ]
        command += _metadata_args(plan.tags)
        command += ["-f", "mp4", "pipe:1"]
        return command

    if profile == "wav":
        command += ["-c:a", "pcm_s16le"]
        command += _metadata_args(plan.tags, with_lyrics=False)
        command += ["-f", "wav", "pipe:1"]
        return command

    raise UnavailableError("This audio format is not supported.")


class _TempCover:
    """Tiny helper that guarantees the cover file is deleted."""

    def __init__(self, data: bytes) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="reelrelay-cover-", suffix=".jpg", delete=False)
        try:
            handle.write(data)
            handle.flush()
        finally:
            handle.close()
        self.path = handle.name

    def cleanup(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _combine_cleanups(
    cleanups: list[Callable[[], None]],
) -> Callable[[], None] | None:
    """Run every cleanup, newest first, never letting one failure stop the rest."""
    if not cleanups:
        return None

    def run() -> None:
        for cleanup in reversed(cleanups):
            try:
                cleanup()
            except Exception:  # noqa: BLE001
                logger.warning("Cleanup step failed", exc_info=True)

    return run


def prepare_stream(plan: DownloadPlan) -> PreparedStream:
    """Open the underlying stream. Blocking; call from a worker thread."""
    if plan.mode == "relay":
        if not plan.stream_url:
            raise DownloadError("No media URL was resolved for this download.")
        return RelayStream(plan.stream_url, plan.headers).open()

    if plan.mode == "video":
        cleanups: list[Callable[[], None]] = []
        try:
            command = _video_command(plan, cleanups)
        except Exception:
            cleanup = _combine_cleanups(cleanups)
            if cleanup is not None:
                cleanup()
            raise
        stream = FFmpegStream(
            command,
            label=f"video {plan.quality}",
            cleanup=_combine_cleanups(cleanups),
        )
        return stream.open()

    if plan.mode == "audio":
        temp_cover: _TempCover | None = None
        prefix = b""
        cover_bytes: bytes | None = None

        if plan.embed_cover and plan.mode == "audio":
            cover_bytes = _fetch_plan_cover(plan)

        if cover_bytes and plan.profile in ("mp3_320", "mp3_192"):
            if plan.embed_tags:
                prefix = id3.build_tag(plan.tags, cover_bytes)
        elif cover_bytes and plan.profile == "flac":
            temp_cover = _TempCover(cover_bytes)

        if plan.profile in ("mp3_320", "mp3_192") and plan.embed_tags and not prefix:
            prefix = id3.build_tag(plan.tags, None)

        cleanups: list[Callable[[], None]] = []
        if temp_cover is not None:
            cleanups.append(temp_cover.cleanup)
        try:
            command = _audio_command(
                plan,
                temp_cover.path if temp_cover else None,
                cleanups,
            )
        except Exception:
            cleanup = _combine_cleanups(cleanups)
            if cleanup is not None:
                cleanup()
            raise
        stream = FFmpegStream(
            command,
            prefix=prefix,
            label=f"audio {plan.profile}",
            cleanup=_combine_cleanups(cleanups),
        )
        return stream.open()

    raise DownloadError("Unsupported download mode.")


def _fetch_plan_cover(plan: DownloadPlan) -> bytes | None:
    """Fetch cover art for a plan without needing the full info dict."""
    fake_info = {
        "_video_id": "",
        "_platform": plan.platform,
        "thumbnails": [],
        "thumbnail": "",
    }
    # cover.cover_candidates accepts pre-built URLs through the thumbnail list.
    fake_info["thumbnails"] = [{"url": url, "width": 1280, "height": 720} for url in plan.cover_urls]
    try:
        return cover.fetch_cover(fake_info)
    except Exception:  # noqa: BLE001 - album art is optional
        logger.exception("Cover art retrieval failed; continuing without it")
        return None


def build_plan(
    info: dict[str, Any],
    *,
    kind: str,
    option_id: str | None = None,
    height: int | None = None,
    container: str | None = None,
    profile: str | None = None,
    format_id: str | None = None,
    tags: dict[str, str] | None = None,
    embed_tags: bool = True,
    embed_cover: bool = True,
) -> DownloadPlan:
    if kind == "video":
        return plan_video(
            info,
            option_id=option_id,
            height=height,
            container=container,
            tags_override=tags,
        )
    if kind == "audio":
        if not profile:
            profile = "mp3_320"
        return plan_audio(
            info,
            profile=profile,
            format_id=format_id,
            tags_override=tags,
            embed_tags=embed_tags,
            embed_cover=embed_cover,
        )
    raise UnavailableError("Unknown download type.")

"""Optional Groq-powered lyrics and tag assistance for audio downloads.

This feature is deliberately opt-in. ReelRelay first tries the free sources:
a lyrics block in the video description and, when enabled, LRCLIB. Only when
the user ticks an AI option does it call Groq:

* ``whisper-large-v3`` transcribes a short audio sample and returns timed
  segments, which become LRC lines for synchronized players.
* ``openai/gpt-oss-20b`` interprets the description (verbatim lyrics plus
  explicitly stated tags), formats the transcript, and can suggest a small
  number of missing tags.

The text model is never allowed to invent words. A formatted transcript is
accepted only when it preserves the words Whisper recognized; otherwise the
raw transcript is kept. Raw and formatted transcripts are returned separately.

Results, including "no lyrics", are cached in memory for the current server
session. Re-clicking the checkbox, opening the confirmation dialog and starting
the final download therefore reuse one Groq request instead of paying for the
same song repeatedly.

The API key stays on the server. Nothing is sent to Groq for video downloads,
and audio longer than ``GROQ_MAX_AUDIO_SECONDS`` (10 minutes by default) is
refused instead of being silently truncated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from . import config, lyrics
from .utils import clean_lyrics

logger = logging.getLogger(__name__)

_MAX_LYRICS = 15_000
_MIN_LYRICS = 20
_MAX_DESCRIPTION = 12_000
_ERROR_TTL = 60.0
_SEGMENT_CHUNK = 80
_TAG_FIELDS = (
    "album",
    "year",
    "composer",
    "genre",
    "track",
    "disc",
    "copyright",
    "publisher",
    "isrc",
)


class GroqError(Exception):
    """A Groq request failed in a way the UI should explain."""


# --------------------------------------------------------------------------- #
# Small in-memory caches
# --------------------------------------------------------------------------- #


class _Cache:
    def __init__(self, ttl: float, size: int) -> None:
        self._ttl = ttl
        self._size = size
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, lyrics.Result]] = {}

    def get(self, key: str) -> lyrics.Result | None:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return result

    def put(self, key: str, result: lyrics.Result) -> None:
        ttl = _ERROR_TTL if result.retryable else self._ttl
        now = time.time()
        with self._lock:
            self._entries[key] = (now + ttl, result)
            self._prune_locked(now)
            while len(self._entries) > self._size:
                oldest = min(self._entries, key=lambda item: self._entries[item][0])
                self._entries.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _prune_locked(self, now: float) -> None:
        expired = [key for key, (expires, _) in self._entries.items() if expires <= now]
        for key in expired:
            self._entries.pop(key, None)


_CACHE = _Cache(config.AI_LYRICS_CACHE_TTL, config.AI_LYRICS_CACHE_SIZE)
# Description interpretation is shared by the lyrics and tag paths, so both
# options cost one chat request when they are used together.
_DESC_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DESC_LOCK = threading.Lock()
# Groq free-tier limits are per account, not per song. Serialising extraction
# keeps several open tabs from firing the same request at once.
_EXTRACT_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Public status helpers
# --------------------------------------------------------------------------- #


def availability(info: dict[str, Any]) -> dict[str, Any]:
    """Describe whether the AI options can be used for this media."""
    duration = int(info.get("duration") or 0)
    max_seconds = config.GROQ_MAX_AUDIO_SECONDS
    configured = bool(config.AI_LYRICS_ENABLED and config.GROQ_API_KEY)
    ffmpeg_ok = bool(config.FFMPEG_OK)
    unknown_duration = duration <= 0
    too_long = duration >= max_seconds
    lyrics_enabled = configured and ffmpeg_ok and not too_long and not unknown_duration
    tags_enabled = configured and bool(str(info.get("description") or "").strip())

    if not config.AI_LYRICS_ENABLED:
        note = "AI assistance is turned off in this ReelRelay configuration."
    elif not config.GROQ_API_KEY:
        note = "Add GROQ_API_KEY to .env and restart to enable AI lyrics and tags."
    elif not ffmpeg_ok:
        note = "ffmpeg is required to prepare audio for AI lyrics; AI tags can still work."
    elif unknown_duration:
        note = "AI lyrics need a known duration so a safe audio sample can be prepared."
    elif too_long:
        minutes = max(1, max_seconds // 60)
        note = f"AI lyrics are only available for audio under {minutes} minutes."
    elif not tags_enabled:
        note = "This video has no description, so AI tags have nothing to read."
    else:
        minutes = max(1, max_seconds // 60)
        note = (
            f"Transcribes up to {minutes} minutes with Groq Whisper and formats the "
            "text without inventing missing words. AI tags only fill missing "
            "fields from facts stated in the description."
        )

    return {
        "enabled": lyrics_enabled,
        "lyrics_enabled": lyrics_enabled,
        "tags_enabled": tags_enabled,
        "configured": configured,
        "ffmpeg_ok": ffmpeg_ok,
        "too_long": too_long,
        "unknown_duration": unknown_duration,
        "duration": duration,
        "max_seconds": max_seconds,
        "note": note,
        "transcription_model": config.GROQ_WHISPER_MODEL,
        "text_model": config.GROQ_TEXT_MODEL,
    }


def cache_key(info: dict[str, Any], purpose: str = "lyrics") -> str:
    platform = str(info.get("_platform") or "youtube").lower()
    video_id = str(info.get("_video_id") or info.get("id") or "")
    url = str(info.get("_source_url") or info.get("webpage_url") or "")
    duration = int(info.get("duration") or 0)
    title = str(info.get("title") or "")[:160]
    raw = f"{purpose}|{platform}|{video_id or url}|{duration}|{title}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def payload(result: lyrics.Result, *, cached: bool = False) -> dict[str, Any]:
    """Turn a result into JSON for the browser (and for plan summaries)."""
    label = result.label or lyrics.describe(
        result.source, text=result.plain
    ).get("label", "")
    return {
        "status": result.source,
        "available": result.available,
        "synchronized": result.synchronized,
        "embedded": False,
        "characters": len(result.plain),
        "label": label,
        "language": result.language,
        "warnings": list(result.warnings),
        "cached": bool(cached),
        "text": result.plain,
        "synced": result.synced,
        "raw_transcript": result.raw,
        "formatted_transcript": result.formatted,
        "tags": result.tags,
        "description": result.description,
    }


def peek(info: dict[str, Any]) -> lyrics.Result | None:
    """Return a cached lyrics result without starting work."""
    return _CACHE.get(cache_key(info, "lyrics"))


def clear_cache() -> None:
    """Forget all cached AI results (tests and configuration changes)."""
    _CACHE.clear()
    with _DESC_LOCK:
        _DESC_CACHE.clear()


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def resolve(
    info: dict[str, Any],
    tags: dict[str, str] | None = None,
    duration: int | None = None,
    *,
    use_ai: bool = True,
) -> tuple[lyrics.Result, bool]:
    """Return ``(result, cached)`` for one media item.

    Free lyrics are resolved before the lock. The remaining work is serialised
    so repeated clicks and parallel format cards share a single Groq request.
    ``use_ai=False`` returns the free result and never calls Groq.
    """
    tags = tags or {}
    if duration is None:
        duration = int(info.get("duration") or 0)
    key = cache_key(info, "lyrics")

    cached = _CACHE.get(key)
    if cached is not None:
        return cached, True

    free = lyrics.probe_result(info, tags, duration)
    if free.available:
        _CACHE.put(key, free)
        return free, False
    if not use_ai:
        # Do not cache a free "no lyrics" outcome when AI was not requested:
        # the user may tick the AI box next and expect the transcription path.
        return free, False

    with _EXTRACT_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached, True
        result = _extract(info, tags, duration)
        _CACHE.put(key, result)
        return result, False


def _extract(
    info: dict[str, Any],
    tags: dict[str, str],
    duration: int,
) -> lyrics.Result:
    status = availability(info)
    if not status["lyrics_enabled"]:
        source = (
            lyrics.SOURCE_AI_TOO_LONG
            if status["too_long"]
            else lyrics.SOURCE_AI_UNAVAILABLE
        )
        return lyrics.Result(source=source, label=status["note"])

    warnings: list[str] = []
    description_data: dict[str, Any] = {}
    description = str(info.get("description") or "").strip()
    if description:
        try:
            description_data = _description_data(info)
        except GroqError as exc:
            warnings.append(str(exc))
        else:
            warnings.extend(description_data.get("warnings") or [])
            candidate = _verbatim_lyrics(
                description_data.get("lyrics"), description
            )
            if candidate:
                label = lyrics.describe(
                    lyrics.SOURCE_AI_DESCRIPTION, text=candidate
                )["label"]
                return lyrics.Result(
                    source=lyrics.SOURCE_AI_DESCRIPTION,
                    plain=candidate,
                    formatted=candidate,
                    language=str(description_data.get("language") or ""),
                    warnings=tuple(warnings),
                    label=label,
                    tags=description_data.get("tags", {}),
                    description=description_data,
                )

    try:
        raw, language, segments = _transcribe(info, duration)
    except GroqError as exc:
        logger.info("AI lyrics transcription failed: %s", exc)
        return lyrics.Result(
            source=lyrics.SOURCE_AI_ERROR,
            label=str(exc),
            warnings=tuple(warnings),
            tags=description_data.get("tags", {}),
            description=description_data,
            retryable=True,
        )

    raw = clean_lyrics(raw, _MAX_LYRICS)
    if not _looks_like_vocals(raw):
        return lyrics.Result(
            source=lyrics.SOURCE_AI_NO_VOCALS,
            raw=raw,
            language=language,
            label=lyrics.describe(lyrics.SOURCE_AI_NO_VOCALS)["label"],
            warnings=tuple(warnings),
            tags=description_data.get("tags", {}),
            description=description_data,
        )

    try:
        plain, synced, has_lyrics, language, extra = _format_segments(
            segments, description_data=description_data, language=language
        )
    except GroqError as exc:
        warnings.append(str(exc))
        plain = "\n".join(
            segment["text"] for segment in segments if segment["text"].strip()
        )
        synced = _lrc_from_segments(segments, [segment["text"] for segment in segments])
        has_lyrics = True
    else:
        warnings.extend(extra)

    plain = clean_lyrics(plain, _MAX_LYRICS)
    synced = _clean_lrc(synced)
    if not has_lyrics or len(plain) < _MIN_LYRICS:
        return lyrics.Result(
            source=lyrics.SOURCE_AI_NO_VOCALS,
            raw=raw,
            formatted=plain,
            language=language,
            label=lyrics.describe(lyrics.SOURCE_AI_NO_VOCALS)["label"],
            warnings=tuple(warnings),
            tags=description_data.get("tags", {}),
            description=description_data,
        )

    if not _preserves_words(raw, plain):
        warnings.append(
            "The formatted transcript changed the recognized words, so the "
            "raw Whisper transcript was kept instead."
        )
        plain = raw
        synced = _lrc_from_segments(
            segments, [segment["text"] for segment in segments]
        )

    label = lyrics.describe(lyrics.SOURCE_AI_AUDIO, text=plain)["label"]
    if synced:
        label += " Timed LRC lines are included."
    return lyrics.Result(
        source=lyrics.SOURCE_AI_AUDIO,
        plain=plain,
        synced=synced,
        raw=raw,
        formatted=plain,
        language=language,
        label=label,
        warnings=tuple(warnings),
        tags=description_data.get("tags", {}),
        description=description_data,
    )


# --------------------------------------------------------------------------- #
# Description interpretation (GPT OSS 20B)
# --------------------------------------------------------------------------- #


def _description_data(info: dict[str, Any]) -> dict[str, Any]:
    """Interpret the description once per media item, with a short cache."""
    key = cache_key(info, "description")
    now = time.time()
    with _DESC_LOCK:
        entry = _DESC_CACHE.get(key)
        if entry and entry[0] > now:
            return entry[1]

    data = _interpret_description(
        str(info.get("description") or ""),
        title=str(info.get("title") or ""),
        artist=str(info.get("artist") or ""),
    )
    with _DESC_LOCK:
        _DESC_CACHE[key] = (now + config.AI_LYRICS_CACHE_TTL, data)
        while len(_DESC_CACHE) > config.AI_LYRICS_CACHE_SIZE:
            oldest = min(_DESC_CACHE, key=lambda item: _DESC_CACHE[item][0])
            _DESC_CACHE.pop(oldest, None)
    return data


def _interpret_description(description: str, *, title: str, artist: str) -> dict[str, Any]:
    """Extract only facts and verbatim lyrics from a written description."""
    system = (
        "You read a video description for a music download. Return one JSON "
        "object with exactly these keys: language, genre, mood, artist, title, "
        "album, year, composer, track, disc, copyright, publisher, isrc, "
        "has_lyrics, lyrics, inferred_tags, warnings. "
        "Copy a value only when the description states it; never guess artist, "
        "title, album, year, composer, track, disc, copyright, publisher or "
        "ISRC. Use an empty string for unknown values. "
        "genre and mood may be inferred; list those field names in inferred_tags. "
        "Set has_lyrics to true only when the description contains the actual "
        "lyric text, not a credit line such as 'Lyrics: Stock Aitken Waterman'. "
        "When has_lyrics is true, copy the complete lyric block verbatim into "
        "the lyrics field, preserving every word and line break. "
        "Never invent, complete, translate, paraphrase or guess lyrics. "
        "warnings is an array of short strings; use [] when there are none."
    )
    user = (
        f"Video title: {title}\n"
        f"Channel / artist: {artist}\n\n"
        f"Description:\n{description[:_MAX_DESCRIPTION]}"
    )
    content = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )
    data = _json_object(content)

    values = {field: _clip(data.get(field), 200) for field in _TAG_FIELDS}
    values["year"] = _year(values["year"])
    values["isrc"] = _isrc(values["isrc"])
    inferred = {
        item
        for item in _string_list(data.get("inferred_tags"), limit=len(_TAG_FIELDS))
        if item in _TAG_FIELDS
    }
    tags = _supported_tags(values, description, inferred)

    return {
        "language": _clip(data.get("language"), 40),
        "genre": values["genre"],
        "mood": _clip(data.get("mood"), 80),
        "artist": _clip(data.get("artist"), 200),
        "title": _clip(data.get("title"), 200),
        "has_lyrics": bool(data.get("has_lyrics")),
        "lyrics": str(data.get("lyrics") or ""),
        "tags": tags,
        "inferred_tags": sorted(inferred),
        "warnings": _string_list(data.get("warnings")),
    }


def _supported_tags(
    values: dict[str, str], description: str, inferred: set[str]
) -> dict[str, str]:
    """Keep a tag only when the description actually supports it.

    Text fields must appear in the description. A year must appear as a
    four-digit number, track/disc must follow their label, and ISRC must be a
    valid-looking code. ``genre`` is the one field that may be inferred, and
    only when the model listed it in ``inferred_tags``.
    """
    supported: dict[str, str] = {}
    lowered = description.casefold()
    normalized = _match_text(description)
    for field, value in values.items():
        if not value:
            continue
        if field == "isrc":
            if value.casefold() not in lowered:
                continue
        elif field == "year":
            if not re.search(rf"\b{re.escape(value)}\b", description):
                continue
        elif field in ("track", "disc"):
            if not re.search(
                rf"\b{field}\b[^\n]{{0,16}}\b{re.escape(value)}\b", lowered
            ):
                continue
        elif field == "genre" and field in inferred:
            pass
        elif _match_text(value) not in normalized:
            continue
        supported[field] = value
    return supported


def enrich_tags(
    info: dict[str, Any], existing: dict[str, str]
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[str]]:
    """Suggest missing tags from the description.

    Returns ``(values, details, warnings)``. Only empty fields are suggested,
    and every value is either a fact stated in the description or an explicitly
    marked genre inference. This function never raises; a failed description
    call simply returns no suggestions.
    """
    status = availability(info)
    if not status["configured"] or not status["tags_enabled"]:
        return {}, {}, []

    try:
        # Groq limits are per account, and a transcription can run for minutes.
        # Sharing the extraction lock keeps tag suggestions from racing it.
        with _EXTRACT_LOCK:
            data = _description_data(info)
    except GroqError as exc:
        return {}, {}, [str(exc)]

    inferred = set(data.get("inferred_tags") or [])
    values: dict[str, str] = {}
    details: dict[str, dict[str, Any]] = {}
    for field, value in (data.get("tags") or {}).items():
        if existing.get(field):
            continue
        values[field] = value
        details[field] = {
            "value": value,
            "inferred": field in inferred,
            "source": "description",
        }
    return values, details, list(data.get("warnings") or [])


def _verbatim_lyrics(candidate: Any, description: str) -> str:
    """Accept an AI lyrics block only when its lines exist in the description."""
    text = clean_lyrics(str(candidate or ""), _MAX_LYRICS)
    if len(text) < 40:
        return ""
    source = _match_text(description)
    lines = [line for line in text.splitlines() if len(_match_text(line)) >= 8]
    if len(lines) < 2:
        return ""
    hits = sum(1 for line in lines if _match_text(line) in source)
    if hits != len(lines):
        return ""
    return text


def _match_text(value: str) -> str:
    text = value.casefold().replace("’", "'")
    text = re.sub(r"[^\w']+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _year(value: str) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", value)
    return match.group(0) if match else "" if value else ""


def _isrc(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return compact if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", compact) else ""


# --------------------------------------------------------------------------- #
# Audio transcription (Whisper Large v3)
# --------------------------------------------------------------------------- #


def _transcribe(
    info: dict[str, Any], duration: int
) -> tuple[str, str, list[dict[str, Any]]]:
    with _audio_sample(info, duration) as sample:
        size = os.path.getsize(sample)
        if size > config.GROQ_MAX_AUDIO_BYTES:
            raise GroqError(
                "The audio sample is larger than Groq's 25 MB upload limit."
            )
        with open(sample, "rb") as handle:
            data = handle.read()

    response = _request(
        "POST",
        "/audio/transcriptions",
        files={"file": (os.path.basename(sample), data, "audio/mpeg")},
        data={
            "model": config.GROQ_WHISPER_MODEL,
            "response_format": "verbose_json",
            "temperature": "0",
        },
        timeout=config.GROQ_TIMEOUT,
    )
    try:
        body = response.json()
    except ValueError as exc:
        raise GroqError("Groq returned an invalid transcription response.") from exc
    if not isinstance(body, dict):
        raise GroqError("Groq returned an invalid transcription response.")

    text = str(body.get("text") or "").strip()
    language = str(body.get("language") or "").strip()
    segments: list[dict[str, Any]] = []
    for item in body.get("segments") or []:
        if not isinstance(item, dict):
            continue
        segment_text = str(item.get("text") or "").strip()
        if not segment_text:
            continue
        try:
            start = float(item.get("start") or 0)
        except (TypeError, ValueError):
            start = 0.0
        segments.append({"start": max(0.0, start), "text": segment_text})
    if not segments and text:
        segments = [{"start": 0.0, "text": text}]
    if not text:
        text = " ".join(segment["text"] for segment in segments)
    return text, language, segments


@contextmanager
def _audio_sample(info: dict[str, Any], duration: int) -> Iterator[str]:
    """Write a short mono MP3 sample to a temporary directory."""
    if not config.FFMPEG:
        raise GroqError("ffmpeg is required to prepare audio for AI lyrics.")

    # Imported lazily: download.py imports this module, and ytdlp imports it
    # for availability metadata. Keeping the import local avoids a circular
    # import at start-up.
    from .download import _ffmpeg_base, _ffmpeg_input
    from .ytdlp import UnavailableError, find_audio_format

    try:
        source = find_audio_format(info, None, allow_progressive=True)
    except UnavailableError as exc:
        raise GroqError("This link did not expose an audio stream to transcribe.") from exc

    platform = str(info.get("_platform") or "youtube").lower()
    tempdir = tempfile.mkdtemp(prefix="reelrelay-ai-lyrics-")
    output = os.path.join(tempdir, "sample.mp3")
    cleanups: list[Any] = []
    seconds = max(1, min(duration or config.GROQ_MAX_AUDIO_SECONDS, config.GROQ_MAX_AUDIO_SECONDS))
    try:
        try:
            command = _ffmpeg_base() + _ffmpeg_input(
                source,
                platform,
                cleanups=cleanups,
                content_type="audio/mpeg",
                label="AI lyrics sample",
            )
        except Exception as exc:  # noqa: BLE001 - wrap into a friendly error
            raise GroqError("Could not prepare the audio sample with ffmpeg.") from exc
        command += [
            "-t",
            str(seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            "-f",
            "mp3",
            output,
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=config.GROQ_AUDIO_TIMEOUT,
                creationflags=creationflags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GroqError("Preparing the audio sample took too long.") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            logger.info("AI audio sample failed: %s", detail[-1] if detail else "")
            raise GroqError("Could not extract an audio sample for transcription.")
        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise GroqError("The extracted audio sample was empty.")
        yield output
    finally:
        for cleanup in reversed(cleanups):
            try:
                cleanup()
            except Exception:  # noqa: BLE001 - best effort
                pass
        shutil.rmtree(tempdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Transcript formatting (GPT OSS 20B)
# --------------------------------------------------------------------------- #


def _format_segments(
    segments: list[dict[str, Any]],
    *,
    description_data: dict[str, Any] | None = None,
    language: str = "",
) -> tuple[str, str, bool, str, list[str]]:
    """Return ``(plain, synced, has_lyrics, language, warnings)``.

    Whisper segments already carry timings. The text model is asked to add
    punctuation and capitalization to each numbered segment without merging,
    splitting or dropping lines. Any segment whose words changed is replaced
    with the raw Whisper text, so the LRC timings always describe the words
    that are actually written.
    """
    context = description_data or {}
    warnings: list[str] = []
    texts: list[str] = []
    has_lyrics = False
    detected_language = language

    hints = []
    if language:
        hints.append(f"Whisper language: {language}")
    if context.get("title"):
        hints.append(f"Song title hint: {context['title']}")
    if context.get("artist"):
        hints.append(f"Artist hint: {context['artist']}")
    prefix = "\n".join(hints)

    system = (
        "You format numbered transcript segments from a song. Return one JSON "
        "object with exactly these keys: segments, has_lyrics, language, "
        "warnings. "
        "segments must be an array of objects {\"index\": integer, \"text\": "
        "string}, one for every input index, in the same order. "
        "Improve punctuation and capitalization only. Keep every recognized "
        "word and never add, complete, merge, split, reorder, translate, "
        "paraphrase or guess lyrics. If a word is unclear, keep the original "
        "word or mark it [unclear]. If the transcript is speech, noise, an "
        "instrumental, or has no sung words, set has_lyrics to false and use "
        "empty text for every segment. warnings is an array of short strings. "
        "Return only the JSON object."
    )

    for start_index in range(0, len(segments), _SEGMENT_CHUNK):
        chunk = segments[start_index : start_index + _SEGMENT_CHUNK]
        numbered = "\n".join(
            f"{start_index + offset}\t{segment['text']}"
            for offset, segment in enumerate(chunk)
        )
        user = (prefix + "\n\n" if prefix else "") + "Segments:\n" + numbered
        try:
            content = _chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            data = _json_object(content)
            returned = data.get("segments")
            formatted_map: dict[int, str] = {}
            if isinstance(returned, list):
                for item in returned:
                    if not isinstance(item, dict):
                        continue
                    try:
                        index = int(item.get("index"))
                    except (TypeError, ValueError):
                        continue
                    formatted_map[index] = str(item.get("text") or "")
            expected = set(range(start_index, start_index + len(chunk)))
            if set(formatted_map) != expected:
                raise GroqError("The formatter did not return the same segments.")
            if data.get("has_lyrics"):
                has_lyrics = True
            detected_language = str(data.get("language") or detected_language).strip()[:40]
            warnings.extend(_string_list(data.get("warnings")))
        except GroqError as exc:
            warnings.append(
                "The formatter response could not be used for one part of the "
                "transcript; raw Whisper text was kept there."
            )
            logger.info("Transcript formatting failed: %s", exc)
            formatted_map = {
                start_index + offset: segment["text"]
                for offset, segment in enumerate(chunk)
            }
            has_lyrics = has_lyrics or _looks_like_vocals(
                " ".join(segment["text"] for segment in chunk)
            )

        for offset, segment in enumerate(chunk):
            index = start_index + offset
            candidate = clean_lyrics(formatted_map.get(index, segment["text"]))
            if not _preserves_words(segment["text"], candidate, per_line=True):
                candidate = segment["text"]
            texts.append(candidate)

    plain = clean_lyrics("\n".join(text for text in texts if text.strip()), _MAX_LYRICS)
    synced = _lrc_from_segments(segments, texts)
    if not has_lyrics and plain:
        has_lyrics = _looks_like_vocals(plain)
    return plain, synced, has_lyrics, detected_language, warnings


def _lrc_from_segments(
    segments: list[dict[str, Any]], texts: list[str]
) -> str:
    lines = []
    for segment, text in zip(segments, texts):
        cleaned = clean_lyrics(text)
        if not cleaned:
            continue
        lines.append(f"{_lrc_timestamp(segment['start'])}{cleaned}")
    return "\n".join(lines)


def _lrc_timestamp(seconds: float) -> str:
    total_centiseconds = max(0, int(round(float(seconds) * 100)))
    minutes, remainder = divmod(total_centiseconds, 6000)
    return f"[{minutes:02d}:{remainder / 100:05.2f}]"


def _clean_lrc(value: str) -> str:
    return "\n".join(
        line.rstrip()
        for line in str(value or "").replace("\r\n", "\n").split("\n")
        if re.match(r"^\[\d{1,3}:\d{2}(?:[.,]\d{1,3})?\]", line)
    )


def _looks_like_vocals(text: str) -> bool:
    if len(text) < _MIN_LYRICS:
        return False
    words = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    return len(words) >= 4


def _word_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[\[\(][^\]\)]{0,40}[\]\)]", " ", text)
    return re.findall(r"[^\W_]+(?:'[^\W_]+)?", cleaned.casefold(), flags=re.UNICODE)


def _preserves_words(raw: str, formatted: str, *, per_line: bool = False) -> bool:
    """Reject a formatter response that invented or dropped text.

    Per line the token sequence must be identical: formatting may change
    punctuation and capitalization, but not add, drop or reorder a word.
    """
    raw_tokens = _word_tokens(raw)
    formatted_tokens = _word_tokens(formatted)
    if per_line:
        return raw_tokens == formatted_tokens
    if not raw_tokens:
        return not formatted_tokens
    minimum = max(4, int(len(raw_tokens) * 0.5))
    if len(formatted_tokens) < minimum:
        return False
    extra = set(formatted_tokens) - set(raw_tokens) - {"unclear"}
    allowed = max(3, int(len(set(formatted_tokens)) * 0.08))
    return len(extra) <= allowed


# --------------------------------------------------------------------------- #
# Groq HTTP helpers
# --------------------------------------------------------------------------- #


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    url = f"{config.GROQ_BASE_URL}{path}"
    timeout = kwargs.pop("timeout", config.GROQ_TIMEOUT)
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Accept": "application/json",
        **kwargs.pop("headers", {}),
    }
    last_error = "Groq request failed."
    for attempt in range(3):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout, connect=15.0),
                follow_redirects=True,
            ) as client:
                response = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            last_error = "Could not reach Groq. Check your connection and try again."
            logger.info("Groq request failed: %s", exc)
            if attempt < 2:
                time.sleep(_backoff(attempt))
                continue
            raise GroqError(last_error) from exc

        if response.status_code in (408, 429, 500, 502, 503, 504):
            last_error = "Groq is busy. Try again in a moment."
            if attempt < 2:
                time.sleep(_backoff(attempt, response))
                continue
        if response.status_code == 401:
            raise GroqError("Groq rejected the API key. Check GROQ_API_KEY.")
        if response.status_code == 413:
            raise GroqError("The audio sample is larger than Groq's upload limit.")
        if response.status_code == 400:
            raise GroqError("Groq rejected the request (HTTP 400).")
        if response.status_code >= 400:
            raise GroqError(f"Groq returned HTTP {response.status_code}.")
        return response

    raise GroqError(last_error)


def _backoff(attempt: int, response: httpx.Response | None = None) -> float:
    retry_after = 0.0
    if response is not None:
        try:
            retry_after = float(response.headers.get("retry-after") or 0)
        except (TypeError, ValueError):
            retry_after = 0.0
    return min(20.0, max(retry_after, 1.5 * (2**attempt))) + random.uniform(0, 0.5)


def _chat(messages: list[dict[str, str]]) -> str:
    body: dict[str, Any] = {
        "model": config.GROQ_TEXT_MODEL,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        response = _request("POST", "/chat/completions", json=body)
    except GroqError as exc:
        # Some models reject response_format. Retry once in plain mode; the
        # prompts already ask for JSON, and the parser still validates it.
        if "HTTP 400" not in str(exc):
            raise
        body.pop("response_format", None)
        response = _request("POST", "/chat/completions", json=body)
    try:
        data = response.json()
    except ValueError as exc:
        raise GroqError("Groq returned an invalid response.") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError("Groq returned an empty response.") from exc
    if not isinstance(content, str) or not content.strip():
        raise GroqError("Groq returned an empty response.")
    return content.strip()


def _json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise GroqError("Groq returned text instead of JSON.")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise GroqError("Groq returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise GroqError("Groq returned invalid JSON.")
    return data


def _string_list(value: Any, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:240] for item in value if str(item).strip()][:limit]

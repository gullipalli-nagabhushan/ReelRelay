"""Lyrics lookup for audio downloads.

Two free sources are tried, in this order:

1. A lyrics block in the video description. Many music uploads put the full
   text there, so it travels with the source and costs no extra request.
2. LRCLIB, but only when the description has no block and the link looks like
   music. The best record is chosen by title, artist and duration instead of
   trusting the first search hit.

Both plain and timed (LRC) text are kept when the source provides timestamps.
``Result.plain`` is what most players show; ``Result.synced`` is the LRC text
used for the synchronised tags (MP3 ``SYLT``/``TXXX``, FLAC ``LYRICS`` and the
MP4 lyrics atom). A source without timestamps still produces plain lyrics.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

import httpx

from . import config
from .utils import clean_lyrics

logger = logging.getLogger(__name__)

MAX_LYRICS = 15_000
SEARCH_URL = "https://lrclib.net/api/search"

# Sources returned by ``probe``. The UI uses these instead of guessing why a
# download has no lyrics.
SOURCE_DESCRIPTION = "description"
SOURCE_LRCLIB = "lrclib"
SOURCE_NONE = "none"
SOURCE_DISABLED = "disabled"
SOURCE_NOT_MUSIC = "not-music"
SOURCE_UNKNOWN = "unknown"
SOURCE_NOT_EMBEDDED = "not-embedded"
SOURCE_UNSUPPORTED = "unsupported"
SOURCE_VIDEO = "video"
SOURCE_ERROR = "error"
# AI lyrics (Groq). The source is kept separate so the UI can explain that a
# transcript is a best-effort result rather than an official lyrics file.
SOURCE_AI_AUDIO = "ai-audio"
SOURCE_AI_DESCRIPTION = "ai-description"
SOURCE_AI_TOO_LONG = "ai-too-long"
SOURCE_AI_UNAVAILABLE = "ai-unavailable"
SOURCE_AI_NO_VOCALS = "ai-no-vocals"
SOURCE_AI_ERROR = "ai-error"

_LABELS = {
    SOURCE_DESCRIPTION: "Lyrics found in the video description.",
    SOURCE_LRCLIB: "Lyrics matched on LRCLIB.",
    SOURCE_NONE: "No lyrics found for this track.",
    SOURCE_DISABLED: "Lyrics lookup is turned off.",
    SOURCE_NOT_MUSIC: "Lyrics are only looked up for music videos.",
    SOURCE_UNKNOWN: "Lyrics were not looked up because the artist or title is unknown.",
    SOURCE_NOT_EMBEDDED: "No tags or lyrics will be added.",
    SOURCE_UNSUPPORTED: "This format cannot store lyrics.",
    SOURCE_VIDEO: "Video downloads do not embed lyrics.",
    SOURCE_ERROR: "Lyrics lookup failed; the download will continue without lyrics.",
    SOURCE_AI_AUDIO: "Lyrics transcribed from the audio with Groq Whisper.",
    SOURCE_AI_DESCRIPTION: "Lyrics extracted from the description with Groq.",
    SOURCE_AI_TOO_LONG: "AI lyrics are only available for audio under 10 minutes.",
    SOURCE_AI_UNAVAILABLE: "AI lyrics need a Groq API key and ffmpeg.",
    SOURCE_AI_NO_VOCALS: "No recognizable lyrics were found in the audio.",
    SOURCE_AI_ERROR: "AI lyrics extraction failed; the download will continue without lyrics.",
}

# Sources whose text can be written into a file's lyrics field.
_EMBEDDABLE = {
    SOURCE_DESCRIPTION,
    SOURCE_LRCLIB,
    SOURCE_AI_AUDIO,
    SOURCE_AI_DESCRIPTION,
}


@dataclass(frozen=True)
class Result:
    """One lyrics lookup outcome.

    ``plain`` is a human-readable block with no timestamps. ``synced`` is LRC
    text (``[mm:ss.xx]line``) and is empty when the source had no timing data.
    Raw and formatted AI transcripts are kept separate so the two can be
    inspected independently.
    """

    source: str
    plain: str = ""
    synced: str = ""
    language: str = ""
    warnings: tuple[str, ...] = ()
    raw: str = ""
    formatted: str = ""
    label: str = ""
    tags: dict[str, Any] = field(default_factory=dict)
    description: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    @property
    def available(self) -> bool:
        return bool(self.plain)

    @property
    def synchronized(self) -> bool:
        return bool(self.synced)

# A heading is a line that is only "Lyrics", "Official Lyrics", "Lyrics:".
_HEADING = re.compile(
    r"^\s*(?:official\s+)?(?:song\s+)?lyrics?\s*[-–—:：]?\s*$", re.IGNORECASE
)
_SECTION = re.compile(
    r"^\s*\[(?:verse|chorus|bridge|intro|outro|hook|pre-?chorus|refrain|interlude)\b[^\]]*\]\s*$",
    re.IGNORECASE,
)
# Credits almost always follow the lyrics block.
_CREDIT = re.compile(
    r"^\s*(?:written\s+by|produced\s+by|composed\s+by|composer|songwriters?|"
    r"lyrics?\s+by|directed\s+by|starring|label|record\s+label|publisher|"
    r"credits?)\b",
    re.IGNORECASE,
)
# Promo lines only stop the block when they look like a CTA, so a lyric such as
# "Listen to the rhythm" is not mistaken for "Listen on Spotify:".
_PROMO = re.compile(
    r"^\s*(?:follow|subscribe|listen|stream|download|connect|shop|merch)\b",
    re.IGNORECASE,
)
_MARK = re.compile(r"^\s*(?:©|℗|#)")
_STAMP = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.,]\d{1,3})?\]\s*")
_LRC_META = re.compile(
    r"^\s*\[(?:ar|ti|al|by|offset|re|ve|length|au):[^\]]*\]\s*$", re.IGNORECASE
)
_URL_ONLY = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.IGNORECASE)
_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_NOISE = re.compile(
    r"\b(instrumental|karaoke|tribute|cover|remix|reprise|acoustic|live|"
    r"sped\s+up|slowed|nightcore)\b",
    re.IGNORECASE,
)

_USER_AGENT = (
    f"{config.APP_NAME}/{config.APP_VERSION} "
    "(https://github.com/gullipalli-nagabhushan/ReelRelay)"
)


def _heading(line: str) -> bool:
    """True for a line that is only a lyrics heading, ignoring emoji."""
    cleaned = line.strip().strip("\ufe0f🎤🎵🎶🎧🎙🎸 ")
    return bool(_HEADING.match(cleaned))


def _plain_from_synced(synced: str) -> str:
    """Strip LRC timestamps and metadata lines, keeping one line per lyric."""
    lines = [
        _STAMP.sub("", line).rstrip()
        for line in synced.splitlines()
        if not _LRC_META.match(line)
    ]
    return "\n".join(lines).strip()


def _has_timestamps(text: str) -> bool:
    return any(_STAMP.match(line) for line in str(text or "").splitlines())


def _clean_lrc(synced: str) -> str:
    """Keep timecoded lines and drop LRC metadata such as ``[ar:]``."""
    lines = []
    for line in str(synced or "").replace("\r\n", "\n").split("\n"):
        if _LRC_META.match(line):
            continue
        if _STAMP.match(line):
            lines.append(line.rstrip())
    return clean_lyrics("\n".join(lines), MAX_LYRICS)


def _should_stop(line: str) -> bool:
    """True for a credit, link or call-to-action that ends a lyrics block."""
    if _CREDIT.match(line) or _MARK.match(line) or _URL_ONLY.match(line):
        return True
    if _PROMO.match(line):
        lowered = line.casefold()
        return any(marker in lowered for marker in (":", "：", "@", "http", "www."))
    return False


def from_description(info: dict[str, Any]) -> str:
    """Return the lyrics block in a video description, or ``""``.

    The parser is deliberately conservative. It accepts a block only after an
    explicit "Lyrics" heading, or starting at an explicit ``[Verse]`` /
    ``[Chorus]`` marker. This avoids embedding a description's promo text or
    credits as if it were a song.
    """
    description = str(info.get("description") or "")
    if not description:
        return ""

    lines = [
        _STAMP.sub("", line.rstrip())
        for line in description.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]

    start: int | None = None
    for index, line in enumerate(lines):
        if _heading(line):
            start = index + 1
            break
    if start is None:
        for index, line in enumerate(lines):
            if _SECTION.match(line):
                start = index
                break
    if start is None:
        return ""

    block: list[str] = []
    blank_run = 0
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if block and blank_run <= 2:
                block.append("")
            continue
        blank_run = 0
        if _should_stop(stripped):
            break
        block.append(line.rstrip())
        if len(block) >= 140:
            break

    while block and not block[-1].strip():
        block.pop()

    text = "\n".join(block).strip()
    plain_lines = [
        line for line in text.splitlines() if line.strip() and not _SECTION.match(line)
    ]
    if len(plain_lines) < 3 or len(text) < 80:
        return ""
    return clean_lyrics(text, MAX_LYRICS)


def _norm(value: str) -> str:
    """Casefold, de-accent and reduce to words, keeping non-Latin scripts."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ").replace("’", "'")
    text = re.sub(r"[^\w']+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _core(value: str) -> str:
    """Title without parenthesised qualifiers: "(Official Video)", "(From ...)"."""
    return _norm(_BRACKETS.sub(" ", str(value)))


def _title_score(query: str, candidate: str, artist: str) -> float:
    left = _core(query)
    right = _core(candidate)
    if not left or not right:
        return 0.0
    # LRCLIB often stores "Artist - Title" in trackName.
    prefix = _norm(artist)
    if prefix and right.startswith(prefix + " "):
        right = right[len(prefix) :].strip()
    if left == right:
        return 1.0
    score = SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        score = max(score, 0.92)
    return score


def _artist_score(query: str, candidate: str) -> float:
    left = _norm(query)
    right = _norm(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.95
    ignored = {"feat", "ft", "and", "the", "with"}
    left_words = set(left.split()) - ignored
    right_words = set(right.split()) - ignored
    if not left_words:
        return 0.0
    overlap = len(left_words & right_words) / len(left_words)
    ratio = SequenceMatcher(None, left, right).ratio()
    return max(overlap, ratio * 0.9)


def _duration_score(query: int, candidate: Any) -> float:
    try:
        other = int(float(candidate))
    except (TypeError, ValueError):
        return 0.4
    if query <= 0 or other <= 0:
        return 0.4
    difference = abs(query - other)
    if difference <= 4:
        return 1.0
    if difference <= 10:
        return 0.7
    if difference <= 25:
        return 0.4
    return 0.0


def _pick_result(
    records: list[dict[str, Any]], artist: str, title: str, duration: int
) -> Result | None:
    """Choose the best LRCLIB record, rejecting instrumentals and wrong songs."""
    best: Result | None = None
    best_score = -1.0
    wanted_noise = bool(_NOISE.search(title))

    for record in records:
        if not isinstance(record, dict) or record.get("instrumental"):
            continue
        track = str(record.get("trackName") or "")
        if _NOISE.search(track) and not wanted_noise:
            continue
        if duration and record.get("duration"):
            try:
                if abs(duration - int(float(record["duration"]))) > 45:
                    continue
            except (TypeError, ValueError):
                pass

        title_score = _title_score(title, track, artist)
        if title_score < 0.72:
            continue
        artist_score = _artist_score(artist, str(record.get("artistName") or ""))
        if artist_score < 0.45:
            continue

        synced = _clean_lrc(str(record.get("syncedLyrics") or ""))
        plain = str(record.get("plainLyrics") or "")
        if not plain:
            plain = _plain_from_synced(synced) if synced else ""
        plain = clean_lyrics(plain, MAX_LYRICS)
        if len(plain) < 20:
            continue
        if not _has_timestamps(synced):
            synced = ""

        score = (
            title_score * 3
            + artist_score * 2
            + _duration_score(duration, record.get("duration"))
        )
        if score > best_score:
            best_score = score
            best = Result(source=SOURCE_LRCLIB, plain=plain, synced=synced)

    return best


def _pick(records: list[dict[str, Any]], artist: str, title: str, duration: int) -> str:
    """Backward-compatible plain-text wrapper around :func:`_pick_result`."""
    result = _pick_result(records, artist, title, duration)
    return result.plain if result else ""


def _search(params: dict[str, str]) -> list[dict[str, Any]] | None:
    """One LRCLIB search.

    ``None`` means the lookup could not be completed (network error, rate
    limit), while an empty list means LRCLIB genuinely had no match. The
    distinction stops the caller from immediately retrying a rate-limited or
    unreachable server with a second query.
    """
    try:
        with httpx.Client(
            timeout=httpx.Timeout(4.0, connect=3.0),
            follow_redirects=True,
        ) as client:
            response = client.get(
                SEARCH_URL,
                params=params,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.info("Lyrics lookup skipped: %s", exc)
        return None

    if response.status_code == 429:
        logger.info("Lyrics lookup was rate limited; continuing without lyrics")
        return None
    if response.status_code != 200:
        logger.info("Lyrics lookup returned HTTP %s", response.status_code)
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None
    return [record for record in payload if isinstance(record, dict)]


@lru_cache(maxsize=256)
def from_lrclib_result(
    artist: str, title: str, album: str, duration: int
) -> Result | None:
    """Look up LRCLIB and return the best record with plain and LRC text."""
    structured = {"track_name": title, "artist_name": artist}
    if album:
        structured["album_name"] = album
    records = _search(structured)
    if records is None:
        return None
    if not records:
        records = _search({"q": f"{artist} {title}"}) or []
    return _pick_result(records, artist, title, duration)


def from_lrclib(artist: str, title: str, album: str, duration: int) -> str:
    """Backward-compatible plain-text wrapper around :func:`from_lrclib_result`."""
    result = from_lrclib_result(artist, title, album, duration)
    return result.plain if result else ""


def _is_music(info: dict[str, Any], tags: dict[str, str]) -> bool:
    categories = {str(category).lower() for category in info.get("categories") or []}
    return bool(
        info.get("artist")
        or info.get("artists")
        or info.get("album_artists")
        or info.get("track")
        or "music" in categories
        or tags.get("genre", "").lower() == "music"
    )


def lookup(info: dict[str, Any], tags: dict[str, str], duration: int) -> str:
    """Lyrics for a download: description first, then LRCLIB when enabled."""
    return probe_result(info, tags, duration).plain


def probe_result(
    info: dict[str, Any], tags: dict[str, str], duration: int
) -> Result:
    """Return a :class:`Result` with plain and, when available, LRC lyrics.

    The ``source`` field explains what happened. An empty ``plain`` is paired
    with a reason such as ``disabled``, ``not-music``, ``unknown`` or ``none``,
    which lets the download confirmation tell the user whether lyrics exist
    instead of silently embedding nothing.
    """
    text = from_description(info)
    if text:
        logger.info("Lyrics taken from the description (%d characters)", len(text))
        return Result(source=SOURCE_DESCRIPTION, plain=text)

    if not config.LYRICS_LOOKUP:
        return Result(source=SOURCE_DISABLED)
    if not _is_music(info, tags):
        return Result(source=SOURCE_NOT_MUSIC)

    artist = tags.get("artist", "").strip()
    title = tags.get("title", "").strip()
    if not artist or not title:
        return Result(source=SOURCE_UNKNOWN)
    if artist.casefold() in {"unknown artist", "unknown creator"}:
        return Result(source=SOURCE_UNKNOWN)
    if title.casefold() in {"unknown title", "untitled"}:
        return Result(source=SOURCE_UNKNOWN)

    result = from_lrclib_result(
        artist, title, tags.get("album", ""), max(0, int(duration))
    )
    if result is not None and result.available:
        logger.info(
            "Lyrics matched on LRCLIB (%d characters%s)",
            len(result.plain),
            ", synchronized" if result.synced else "",
        )
        return result
    return Result(source=SOURCE_NONE)


def probe(
    info: dict[str, Any], tags: dict[str, str], duration: int
) -> tuple[str, str]:
    """Backward-compatible ``(plain, source)`` wrapper around :func:`probe_result`."""
    result = probe_result(info, tags, duration)
    return result.plain, result.source


def describe(
    source: str,
    *,
    text: str = "",
    embedded: bool | None = None,
    detail: str = "",
    synchronized: bool = False,
) -> dict[str, Any]:
    """Turn a lookup result into a small, UI-friendly status object.

    ``text`` is only used for length and availability; the lyrics themselves
    never go into the confirmation response. ``embedded`` is ``None`` when the
    caller only wants to show availability (the format cards), ``True`` when the
    lyrics will be written into the file, and ``False`` when the chosen format
    cannot store them. ``synchronized`` says whether timed LRC lines exist.
    """
    available = bool(text)
    label = detail or _LABELS.get(source, "Lyrics status is unknown.")
    if available and source in _EMBEDDABLE:
        if not detail:
            label = _LABELS[source]
        if embedded is True:
            label += " They will be embedded in this file."
        elif embedded is False:
            label += " This file cannot store them."
    return {
        "status": source,
        "available": available,
        "embedded": bool(available and embedded is True),
        "synchronized": bool(available and synchronized),
        "characters": len(text),
        "label": label,
    }


def clear_cache() -> None:
    """Forget cached lookups (used by tests and after a config change)."""
    from_lrclib_result.cache_clear()

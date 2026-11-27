"""Minimal, dependency-free ID3v2.3 writer.

ffmpeg cannot write an ID3 tag to a *non-seekable* pipe (which is exactly what
we use so nothing is written to disk).  We therefore ask ffmpeg for the
raw MP3 frames and prepend a standards-compliant ID3v2.3 tag ourselves.  That
tag carries the title, artist, album, composer, year, genre, track, comment,
copyright, ISRC, lyrics and — most importantly — cover art as an APIC frame.
"""

from __future__ import annotations

import re
import struct

from .utils import clean_lyrics

_UTF16 = 0x01
_LRC_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.,](\d{1,3}))?\]")


def _syncsafe(value: int) -> bytes:
    """ID3 tag size is stored as four 7-bit bytes."""
    if value < 0 or value > 0x0FFFFFFF:
        raise ValueError("ID3 tag is too large")
    return bytes(
        (
            (value >> 21) & 0x7F,
            (value >> 14) & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        )
    )


def _frame(frame_id: str, payload: bytes) -> bytes:
    return frame_id.encode("ascii") + struct.pack(">I", len(payload)) + b"\x00\x00" + payload


def _utf16(value: str) -> bytes:
    return b"\xff\xfe" + str(value).encode("utf-16-le")


def _text_frame(frame_id: str, value: str) -> bytes:
    text = str(value).strip()
    if not text:
        return b""
    return _frame(frame_id, bytes((_UTF16,)) + _utf16(text))


def _described_frame(frame_id: str, value: str, language: str) -> bytes:
    """COMM/USLT layout: encoding, language, description, text.

    The description is empty. In UTF-16 every string starts with a byte-order
    mark, so the empty description is BOM plus its null terminator.
    """
    text = str(value).strip()
    if not text:
        return b""
    payload = (
        bytes((_UTF16,))
        + language.encode("ascii", "ignore")[:3].ljust(3, b" ")
        + _utf16("")
        + b"\x00\x00"
        + _utf16(text)
    )
    return _frame(frame_id, payload)


def parse_lrc(synced: str) -> list[tuple[int, str]]:
    """Turn LRC text into sorted ``(milliseconds, text)`` pairs.

    A line may carry several timestamps (repeated choruses), so every pair is
    emitted. Timestamps are clamped to 32 bits, which is what SYLT stores.
    """
    entries: list[tuple[int, str]] = []
    for line in str(synced or "").replace("\r\n", "\n").split("\n"):
        stamps = list(_LRC_STAMP.finditer(line))
        if not stamps:
            continue
        text = _LRC_STAMP.sub("", line).strip()
        if not text:
            continue
        for match in stamps:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            fraction = (match.group(3) or "0")[:3]
            millis = int(fraction.ljust(3, "0"))
            total = minutes * 60_000 + seconds * 1_000 + millis
            entries.append((min(total, 0xFFFFFFFF), text))
    entries.sort(key=lambda entry: entry[0])
    # Drop exact duplicates created by overlapping timestamps.
    unique: list[tuple[int, str]] = []
    for entry in entries:
        if not unique or unique[-1] != entry:
            unique.append(entry)
    return unique


def _sylt_frame(synced: str, language: str = "eng") -> bytes:
    """Build an ID3v2.3 SYLT frame from LRC text.

    Layout: encoding, language, timestamp format (2 = milliseconds), content
    type (1 = lyrics), descriptor, then repeated (text, 32-bit timestamp).
    """
    entries = parse_lrc(synced)
    if len(entries) < 2:
        return b""
    payload = (
        bytes((_UTF16,))
        + language.encode("ascii", "ignore")[:3].ljust(3, b" ")
        + bytes((0x02, 0x01))
        + _utf16("")
        + b"\x00\x00"
    )
    for millis, text in entries:
        payload += _utf16(text) + b"\x00\x00" + struct.pack(">I", millis)
    return _frame("SYLT", payload)


def _txxx_frame(description: str, value: str) -> bytes:
    """A TXXX frame; foobar2000's lyrics plugins read ``LYRICS`` from here."""
    text = str(value).strip()
    if not text:
        return b""
    payload = (
        bytes((_UTF16,))
        + _utf16(str(description))
        + b"\x00\x00"
        + _utf16(text)
        + b"\x00\x00"
    )
    return _frame("TXXX", payload)


def _apic_frame(image: bytes, mime: str = "image/jpeg") -> bytes:
    if not image:
        return b""
    payload = (
        bytes((0x00,))            # ISO-8859-1 description
        + mime.encode("latin-1", "ignore")
        + b"\x00"
        + bytes((0x03,))          # picture type 3 = front cover
        + b"\x00"                 # empty description
        + image
    )
    return _frame("APIC", payload)


def build_tag(
    tags: dict[str, str],
    cover: bytes | None = None,
    *,
    encoder: str = "ReelRelay",
    synced_lyrics: str = "",
    prefer_synced: bool = True,
) -> bytes:
    """Build an ID3v2.3 tag (without padding) from a tag mapping.

    ``USLT`` always holds the plain text, so players without timestamp support
    show clean lyrics. When ``synced_lyrics`` holds LRC text it is also written
    as a proper ``SYLT`` frame (Poweramp, MusicBee) and as ``TXXX:LYRICS``
    (foobar2000 lyrics plugins). ``prefer_synced=False`` skips both timed
    frames.
    """
    date = str(tags.get("date") or "")
    year = str(tags.get("year") or (date[:4] if len(date) >= 4 else ""))[:4]

    frames = b"".join(
        (
            _text_frame("TIT2", tags.get("title", "")),
            _text_frame("TPE1", tags.get("artist", "")),
            _text_frame("TPE2", tags.get("album_artist", "") or tags.get("artist", "")),
            _text_frame("TCOM", tags.get("composer", "")),
            _text_frame("TALB", tags.get("album", "")),
            _text_frame("TYER", year),
            _text_frame("TCON", tags.get("genre", "")),
            _text_frame("TRCK", tags.get("track", "")),
            _text_frame("TPOS", tags.get("disc", "")),
            _text_frame("TCOP", tags.get("copyright", "")),
            _text_frame("TSRC", tags.get("isrc", "")),
            _text_frame("TPUB", tags.get("publisher", "")),
        )
    )

    # ID3v2.3 has no single ISO date frame; TDAT carries DDMM next to TYER.
    if len(date) >= 10 and date[4] == "-" and date[7] == "-":
        day, month = date[8:10], date[5:7]
        if day.isdigit() and month.isdigit():
            frames += _text_frame("TDAT", day + month)
    elif len(date) == 8 and date.isdigit():
        frames += _text_frame("TDAT", date[6:8] + date[4:6])

    frames += _described_frame("COMM", tags.get("comment", ""), "eng")

    if cover:
        frames += _apic_frame(cover)

    plain = clean_lyrics(tags.get("lyrics", ""))
    synced = clean_lyrics(synced_lyrics) if prefer_synced else ""
    frames += _described_frame("USLT", plain, "eng")
    if synced:
        frames += _sylt_frame(synced)
        frames += _txxx_frame("LYRICS", synced)

    if not frames:
        return b""

    frames += _text_frame("TSSE", encoder)
    return b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames

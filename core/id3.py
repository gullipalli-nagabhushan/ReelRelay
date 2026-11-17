"""Minimal, dependency-free ID3v2.3 writer.

ffmpeg cannot write an ID3 tag to a *non-seekable* pipe (which is exactly what
we use so nothing is written to disk).  We therefore ask ffmpeg for the
raw MP3 frames and prepend a standards-compliant ID3v2.3 tag ourselves.  That
tag carries the title, artist, album, composer, year, genre and — most
importantly — the cover art as an APIC frame.
"""

from __future__ import annotations

import struct

_UTF16 = 0x01


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


def _text_frame(frame_id: str, value: str) -> bytes:
    text = str(value).strip()
    if not text:
        return b""
    encoded = text.encode("utf-16-le")
    return _frame(frame_id, bytes((_UTF16,)) + b"\xff\xfe" + encoded)


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


def build_tag(tags: dict[str, str], cover: bytes | None = None, *, encoder: str = "ReelRelay") -> bytes:
    """Build an ID3v2.3 tag (without padding) from a tag mapping."""
    frames = b"".join(
        (
            _text_frame("TIT2", tags.get("title", "")),
            _text_frame("TPE1", tags.get("artist", "")),
            _text_frame("TPE2", tags.get("album_artist", "") or tags.get("artist", "")),
            _text_frame("TCOM", tags.get("composer", "")),
            _text_frame("TALB", tags.get("album", "")),
            _text_frame("TYER", tags.get("year", "")[:4]),
            _text_frame("TCON", tags.get("genre", "")),
            _text_frame("TRCK", tags.get("track", "")),
            _text_frame("TPOS", tags.get("disc", "")),
        )
    )

    if cover:
        frames += _apic_frame(cover)

    if not frames:
        return b""

    frames += _text_frame("TSSE", encoder)
    return b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames

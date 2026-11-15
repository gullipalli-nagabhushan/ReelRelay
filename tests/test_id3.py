"""ID3v2.3 writer used for streamed MP3 downloads."""

from __future__ import annotations

from core.id3 import _syncsafe, build_tag

TAGS = {
    "title": "Café del Mar",
    "artist": "Rick Astley",
    "album_artist": "Rick Astley",
    "album": "Whenever You Need Somebody",
    "composer": "Stock Aitken Waterman",
    "genre": "Pop",
    "year": "1987",
    "track": "1",
}
COVER = b"\xff\xd8\xff\xe0" + b"fake-jpeg" * 32


def test_empty_tag_returns_nothing() -> None:
    assert build_tag({}, None) == b""


def test_header_and_size_are_syncsafe() -> None:
    tag = build_tag(TAGS, COVER)

    assert tag[:3] == b"ID3"
    assert tag[3:5] == b"\x03\x00"
    assert tag[5] == 0x00
    declared = (tag[6] << 21) | (tag[7] << 14) | (tag[8] << 7) | tag[9]
    assert declared == len(tag) - 10
    assert _syncsafe(declared) == tag[6:10]


def test_writes_text_frames_in_utf16() -> None:
    tag = build_tag(TAGS, None)

    for frame in (b"TIT2", b"TPE1", b"TPE2", b"TCOM", b"TALB", b"TYER", b"TCON", b"TRCK"):
        assert frame in tag

    assert "Café del Mar".encode("utf-16-le") in tag
    assert "Stock Aitken Waterman".encode("utf-16-le") in tag


def test_writes_front_cover_apic_frame() -> None:
    tag = build_tag(TAGS, COVER)

    assert b"APIC" in tag
    assert b"image/jpeg" in tag
    assert COVER in tag


def test_omits_cover_when_not_supplied() -> None:
    assert b"APIC" not in build_tag(TAGS, None)


def test_skips_blank_fields() -> None:
    tag = build_tag({"title": "Only a title"}, None)

    assert b"TIT2" in tag
    assert b"TPE1" not in tag
    assert b"TCOM" not in tag

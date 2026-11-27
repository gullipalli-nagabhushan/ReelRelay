"""ID3v2.3 writer used for streamed MP3 downloads."""

from __future__ import annotations

from core.id3 import _syncsafe, build_tag, parse_lrc

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


def test_writes_extra_identifiers() -> None:
    tag = build_tag(
        {
            "title": "Song",
            "copyright": "© 2026 ReelRelay Records",
            "publisher": "ReelRelay Records",
            "isrc": "USABC1234567",
            "track": "3/12",
            "disc": "1/2",
        },
        None,
    )

    for frame in (b"TCOP", b"TPUB", b"TSRC", b"TRCK", b"TPOS"):
        assert frame in tag
    assert "USABC1234567".encode("utf-16-le") in tag
    assert "3/12".encode("utf-16-le") in tag


def test_writes_a_comment_frame() -> None:
    tag = build_tag({"comment": "Source: https://youtu.be/dQw4w9WgXcQ"}, None)

    assert b"COMM" in tag
    assert "Source: https://youtu.be/dQw4w9WgXcQ".encode("utf-16-le") in tag


def test_writes_uslt_lyrics_with_line_breaks() -> None:
    text = "We're no strangers to love\nYou know the rules and so do I"
    tag = build_tag({"lyrics": text}, None)

    assert b"USLT" in tag
    index = tag.index(b"USLT")
    size = int.from_bytes(tag[index + 4 : index + 8], "big")
    payload = tag[index + 10 : index + 10 + size]

    assert payload[0] == 0x01
    assert payload[1:4] == b"eng"
    assert payload[4:6] == b"\xff\xfe"  # empty descriptor: BOM
    assert payload[6:8] == b"\x00\x00"
    assert text.encode("utf-16-le") in payload


def test_writes_full_date_as_tdat() -> None:
    tag = build_tag({"title": "Song", "date": "1987-11-16"}, None)

    assert b"TYER" in tag
    assert b"TDAT" in tag
    assert "1987".encode("utf-16-le") in tag
    assert "1611".encode("utf-16-le") in tag


def test_parse_lrc_reads_timestamps_and_repeated_lines() -> None:
    entries = parse_lrc(
        "[ar:Rick Astley]\n"
        "[00:01.50]First line\n"
        "[00:10.00][00:30.00]Repeated line\n"
    )

    assert entries == [
        (1500, "First line"),
        (10000, "Repeated line"),
        (30000, "Repeated line"),
    ]


def test_synced_lyrics_write_uslt_sylt_and_txxx() -> None:
    plain = "First line\nRepeated line"
    synced = "[00:01.50]First line\n[00:10.00]Repeated line"

    tag = build_tag({"lyrics": plain}, None, synced_lyrics=synced)

    assert b"USLT" in tag
    assert b"SYLT" in tag
    assert b"TXXX" in tag
    # USLT stays plain for players without timestamp support.
    uslt = tag.index(b"USLT")
    uslt_size = int.from_bytes(tag[uslt + 4 : uslt + 8], "big")
    uslt_payload = tag[uslt + 10 : uslt + 10 + uslt_size]
    assert "[00:01.50]".encode("utf-16-le") not in uslt_payload
    assert "First line".encode("utf-16-le") in uslt_payload
    # Timed lines live in SYLT and TXXX:LYRICS.
    assert "[00:01.50]First line".encode("utf-16-le") in tag
    assert "LYRICS".encode("utf-16-le") in tag

    sylt = tag.index(b"SYLT")
    size = int.from_bytes(tag[sylt + 4 : sylt + 8], "big")
    payload = tag[sylt + 10 : sylt + 10 + size]
    assert payload[0] == 0x01
    assert payload[1:4] == b"eng"
    assert payload[4] == 0x02  # milliseconds
    assert payload[5] == 0x01  # lyrics


def test_synced_lyrics_can_be_turned_off() -> None:
    synced = "[00:01.50]First line\n[00:10.00]Repeated line"

    tag = build_tag(
        {"lyrics": "First line\nRepeated line"},
        None,
        synced_lyrics=synced,
        prefer_synced=False,
    )

    assert b"USLT" in tag
    assert b"SYLT" not in tag
    assert b"TXXX" not in tag
    assert b"[00:01.50]" not in tag
    assert "First line".encode("utf-16-le") in tag


def test_one_timed_line_is_not_enough_for_sylt() -> None:
    tag = build_tag({"lyrics": "Only line"}, None, synced_lyrics="[00:01.00]Only line")

    assert b"SYLT" not in tag
    assert b"USLT" in tag

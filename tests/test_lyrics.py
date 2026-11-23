"""Lyrics extraction from descriptions and the LRCLIB fallback."""

from __future__ import annotations

import pytest

from core import config, lyrics

LYRICS_DESCRIPTION = """\
Official music video for Never Gonna Give You Up.

Lyrics:
[Verse 1]
We're no strangers to love
You know the rules and so do I
A full commitment's what I'm thinking of
You wouldn't get this from any other guy

[Chorus]
Never gonna give you up
Never gonna let you down
Never gonna run around and desert you

Follow Rick Astley:
https://example.com/rick
"""

GOOD_RECORD = {
    "trackName": "Die With A Smile",
    "artistName": "Lady Gaga, Bruno Mars",
    "albumName": "Die With A Smile",
    "duration": 251.7,
    "instrumental": False,
    "plainLyrics": (
        "I, I just woke up from a dream\n"
        "Where you and I had to say goodbye\n"
        "And I don't know what it all means"
    ),
}


@pytest.fixture(autouse=True)
def _clear_lrclib_cache() -> None:
    lyrics.clear_cache()
    yield
    lyrics.clear_cache()


def test_reads_a_lyrics_block_and_stops_at_credits() -> None:
    text = lyrics.from_description({"description": LYRICS_DESCRIPTION})

    assert "We're no strangers to love" in text
    assert "Never gonna run around and desert you" in text
    assert "Follow Rick Astley" not in text
    assert "https://example.com/rick" not in text
    assert text.startswith("[Verse 1]")


def test_accepts_a_block_that_starts_with_a_section_marker() -> None:
    description = (
        "[Verse 1]\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
        "\n"
        "[Chorus]\n"
        "Never gonna give you up\n"
    )

    assert lyrics.from_description({"description": description}).startswith("[Verse 1]")


def test_keeps_a_lyric_that_starts_with_a_promo_verb() -> None:
    description = (
        "Lyrics:\n"
        "Listen to the rhythm of the falling rain\n"
        "Telling me just what a fool I've been\n"
        "I wish that it would go and let me cry in vain\n"
    )

    text = lyrics.from_description({"description": description})

    assert text.startswith("Listen to the rhythm")


def test_accepts_a_full_width_colon_in_the_heading() -> None:
    description = (
        "Lyrics：\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
    )

    assert "strangers" in lyrics.from_description({"description": description})


def test_stops_at_a_copyright_line() -> None:
    description = (
        "Lyrics:\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
        "© 1987 Sony Music Entertainment\n"
        "More promo text\n"
    )

    text = lyrics.from_description({"description": description})

    assert "Sony Music" not in text
    assert "More promo text" not in text


def test_ignores_a_description_without_a_lyrics_heading() -> None:
    description = (
        "The official video for Never Gonna Give You Up.\n"
        "Composer: Stock Aitken Waterman\n"
        "Lyrics: Stock Aitken Waterman\n"
    )

    assert lyrics.from_description({"description": description}) == ""


def test_rejects_a_block_that_is_too_short() -> None:
    assert lyrics.from_description({"description": "Lyrics:\nla la la\nla la la\n"}) == ""


def test_strips_control_characters_and_crlf() -> None:
    description = LYRICS_DESCRIPTION.replace("\n", "\r\n") + "\x00"

    text = lyrics.from_description({"description": description})

    assert "\r" not in text
    assert "\x00" not in text


def test_description_wins_without_any_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lyrics, "_search", lambda params: pytest.fail("network used"))
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)

    text = lyrics.lookup(
        {"description": LYRICS_DESCRIPTION},
        {"artist": "Rick Astley", "title": "Never Gonna Give You Up"},
        213,
    )

    assert "We're no strangers to love" in text


def test_lookup_is_skipped_when_disabled() -> None:
    assert config.LYRICS_LOOKUP is False
    assert lyrics.lookup({"categories": ["Music"]}, {"artist": "A", "title": "T"}, 200) == ""


def test_lookup_is_skipped_for_non_music(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)
    monkeypatch.setattr(lyrics, "_search", lambda params: pytest.fail("network used"))

    info = {"categories": ["Gaming"], "description": "No lyrics here."}
    assert lyrics.lookup(info, {"artist": "A", "title": "T"}, 200) == ""


def test_lookup_rejects_unknown_artist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)
    monkeypatch.setattr(lyrics, "_search", lambda params: pytest.fail("network used"))

    info = {"categories": ["Music"]}
    assert lyrics.lookup(info, {"artist": "Unknown Artist", "title": "T"}, 200) == ""


def test_lrclib_result_is_used_when_description_has_no_lyrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)
    monkeypatch.setattr(lyrics, "_search", lambda params: [GOOD_RECORD])

    info = {"description": "Official music video.", "categories": ["Music"]}
    text = lyrics.lookup(
        info,
        {"artist": "Lady Gaga, Bruno Mars", "title": "Die With A Smile", "genre": "Pop"},
        252,
    )

    assert text.startswith("I, I just woke up from a dream")


def test_pick_rejects_instrumentals_and_wrong_artists() -> None:
    instrumental = dict(GOOD_RECORD, instrumental=True)
    wrong_artist = dict(GOOD_RECORD, artistName="Some Other Band")

    assert lyrics._pick([instrumental], "Lady Gaga", "Die With A Smile", 251) == ""
    assert lyrics._pick([wrong_artist], "Lady Gaga", "Die With A Smile", 251) == ""


def test_pick_rejects_a_different_duration() -> None:
    long_version = dict(GOOD_RECORD, duration=400)

    assert lyrics._pick([long_version], "Lady Gaga, Bruno Mars", "Die With A Smile", 251) == ""


def test_pick_strips_lrc_timestamps_when_plain_lyrics_are_missing() -> None:
    record = dict(
        GOOD_RECORD,
        plainLyrics=None,
        syncedLyrics=(
            "[ar:Lady Gaga]\n"
            "[ti:Die With A Smile]\n"
            "[00:01.00]I, I just woke up from a dream\n"
            "[00:05.20]Where you and I had to say goodbye\n"
            "[00:09.40]And I don't know what it all means"
        ),
    )

    text = lyrics._pick([record], "Lady Gaga, Bruno Mars", "Die With A Smile", 251)

    assert text.startswith("I, I just woke up from a dream")
    assert "[00:01" not in text
    assert "[ar:" not in text


def test_from_lrclib_falls_back_to_free_text_search(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_search(params: dict[str, str]):
        calls.append(params)
        if "q" in params:
            return [GOOD_RECORD]
        return []

    monkeypatch.setattr(lyrics, "_search", fake_search)

    text = lyrics.from_lrclib("Lady Gaga, Bruno Mars", "Die With A Smile", "", 251)

    assert text.startswith("I, I just woke up")
    assert len(calls) == 2
    assert "track_name" in calls[0]


def test_from_lrclib_does_not_retry_after_a_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []

    def failing_search(params: dict[str, str]):
        calls.append(params)
        return None

    monkeypatch.setattr(lyrics, "_search", failing_search)

    assert lyrics.from_lrclib("Lady Gaga", "Die With A Smile", "", 251) == ""
    assert len(calls) == 1


def test_probe_reports_description_as_the_source() -> None:
    text, source = lyrics.probe(
        {"description": LYRICS_DESCRIPTION},
        {"artist": "Rick Astley", "title": "Never Gonna Give You Up"},
        213,
    )

    assert text.startswith("[Verse 1]")
    assert source == lyrics.SOURCE_DESCRIPTION


def test_probe_explains_a_disabled_lookup() -> None:
    text, source = lyrics.probe(
        {"categories": ["Music"]}, {"artist": "A", "title": "T"}, 200
    )

    assert text == ""
    assert source == lyrics.SOURCE_DISABLED


def test_probe_skips_non_music_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)
    monkeypatch.setattr(lyrics, "_search", lambda params: pytest.fail("network used"))

    text, source = lyrics.probe(
        {"categories": ["Gaming"], "description": "No lyrics here."},
        {"artist": "A", "title": "T"},
        200,
    )

    assert text == ""
    assert source == lyrics.SOURCE_NOT_MUSIC


def test_probe_reports_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)
    monkeypatch.setattr(lyrics, "_search", lambda params: [])

    text, source = lyrics.probe(
        {"categories": ["Music"]}, {"artist": "A", "title": "T"}, 200
    )

    assert text == ""
    assert source == lyrics.SOURCE_NONE


def test_probe_reports_lrclib_as_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LYRICS_LOOKUP", True)
    monkeypatch.setattr(lyrics, "_search", lambda params: [GOOD_RECORD])

    text, source = lyrics.probe(
        {"description": "Official music video.", "categories": ["Music"]},
        {"artist": "Lady Gaga, Bruno Mars", "title": "Die With A Smile"},
        252,
    )

    assert text.startswith("I, I just woke up")
    assert source == lyrics.SOURCE_LRCLIB


def test_describe_marks_embedded_lyrics() -> None:
    status = lyrics.describe(
        lyrics.SOURCE_DESCRIPTION, text="la la la", embedded=True
    )

    assert status["available"] is True
    assert status["embedded"] is True
    assert status["characters"] == 8
    assert "embedded" in status["label"].lower()


def test_describe_leaves_availability_open_for_format_cards() -> None:
    status = lyrics.describe(lyrics.SOURCE_LRCLIB, text="la la la")

    assert status["available"] is True
    assert status["embedded"] is False
    assert "cannot store" not in status["label"].lower()


def test_describe_uses_a_custom_detail() -> None:
    status = lyrics.describe(
        lyrics.SOURCE_UNSUPPORTED, detail="WAV has no lyrics field."
    )

    assert status["available"] is False
    assert status["label"] == "WAV has no lyrics field."

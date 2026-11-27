"""Offline tests for the optional Groq lyrics and tag assistance."""

from __future__ import annotations

import pytest

from core import ai_lyrics, config, lyrics

KEY = "gsk-test-key"


@pytest.fixture()
def groq_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GROQ_API_KEY", KEY)
    monkeypatch.setattr(config, "AI_LYRICS_ENABLED", True)
    monkeypatch.setattr(config, "FFMPEG_OK", True)
    monkeypatch.setattr(config, "FFMPEG", "ffmpeg")
    ai_lyrics.clear_cache()


def test_availability_needs_a_key(monkeypatch: pytest.MonkeyPatch, sample_info: dict) -> None:
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    status = ai_lyrics.availability(sample_info)

    assert status["lyrics_enabled"] is False
    assert status["tags_enabled"] is False
    assert "GROQ_API_KEY" in status["note"]


def test_availability_rejects_over_ten_minutes(
    groq_ready: None, sample_info: dict
) -> None:
    sample_info["duration"] = 900
    status = ai_lyrics.availability(sample_info)

    assert status["lyrics_enabled"] is False
    assert status["too_long"] is True
    assert status["tags_enabled"] is True


def test_availability_accepts_a_short_music_video(
    groq_ready: None, sample_info: dict
) -> None:
    status = ai_lyrics.availability(sample_info)

    assert status["lyrics_enabled"] is True
    assert status["tags_enabled"] is True
    assert status["max_seconds"] == 600


def test_free_lyrics_skip_groq(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    sample_info["description"] = (
        "Lyrics:\n"
        "[Verse 1]\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
    )

    def fail(*args, **kwargs):
        raise AssertionError("Groq must not be called when the description has lyrics")

    monkeypatch.setattr(ai_lyrics, "_transcribe", fail)
    result, cached = ai_lyrics.resolve(sample_info, {}, 213)

    assert result.source == lyrics.SOURCE_DESCRIPTION
    assert "We're no strangers to love" in result.plain
    assert cached is False
    again, cached = ai_lyrics.resolve(sample_info, {}, 213)
    assert again is result
    assert cached is True


def test_no_ai_outcome_is_not_cached_for_later_ai_use(
    groq_ready: None, sample_info: dict
) -> None:
    first, cached = ai_lyrics.resolve(sample_info, {}, 213, use_ai=False)
    assert first.available is False
    assert cached is False
    assert ai_lyrics.peek(sample_info) is None


def test_transcription_becomes_plain_and_timed_lyrics(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    sample_info["description"] = ""
    segments = [
        {"start": 1.0, "text": "we are no strangers to love"},
        {"start": 4.5, "text": "you know the rules and so do i"},
    ]
    raw = "we are no strangers to love you know the rules and so do i"
    monkeypatch.setattr(
        ai_lyrics, "_transcribe", lambda info, duration: (raw, "en", segments)
    )
    monkeypatch.setattr(
        ai_lyrics,
        "_chat",
        lambda messages: (
            '{"segments": ['
            '{"index": 0, "text": "We are no strangers to love."},'
            '{"index": 1, "text": "You know the rules and so do I."}'
            '], "has_lyrics": true, "language": "en", "warnings": []}'
        ),
    )

    result, _cached = ai_lyrics.resolve(sample_info, {}, 213)

    assert result.source == lyrics.SOURCE_AI_AUDIO
    assert result.plain == "We are no strangers to love.\nYou know the rules and so do I."
    assert result.synced.splitlines()[0] == "[00:01.00]We are no strangers to love."
    assert result.raw == raw
    assert result.formatted == result.plain


def test_formatter_cannot_invent_words(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    sample_info["description"] = ""
    segments = [{"start": 0.0, "text": "hello darkness my old friend"}]
    raw = "hello darkness my old friend"
    monkeypatch.setattr(
        ai_lyrics, "_transcribe", lambda info, duration: (raw, "en", segments)
    )
    monkeypatch.setattr(
        ai_lyrics,
        "_chat",
        lambda messages: (
            '{"segments": [{"index": 0, "text": '
            '"Hello darkness my old friend, I have come to talk with you again."}], '
            '"has_lyrics": true, "warnings": []}'
        ),
    )

    result, _cached = ai_lyrics.resolve(sample_info, {}, 213)

    assert result.plain == raw
    assert "I have come to talk" not in result.plain


def test_formatter_cannot_drop_words(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    sample_info["description"] = ""
    segments = [{"start": 0.0, "text": "hello darkness my old friend"}]
    raw = "hello darkness my old friend"
    monkeypatch.setattr(
        ai_lyrics, "_transcribe", lambda info, duration: (raw, "en", segments)
    )
    monkeypatch.setattr(
        ai_lyrics,
        "_chat",
        lambda messages: (
            '{"segments": [{"index": 0, "text": '
            '"Hello darkness, old friend."}], "has_lyrics": true, "warnings": []}'
        ),
    )

    result, _cached = ai_lyrics.resolve(sample_info, {}, 213)

    assert result.plain == raw
    assert "my" in result.plain


def test_no_vocals_is_cached_as_a_negative_result(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    sample_info["description"] = ""
    calls = {"count": 0}

    def transcribe(info, duration):
        calls["count"] += 1
        return "", "en", []

    monkeypatch.setattr(ai_lyrics, "_transcribe", transcribe)

    first, _cached = ai_lyrics.resolve(sample_info, {}, 213)
    second, cached = ai_lyrics.resolve(sample_info, {}, 213)

    assert first.source == lyrics.SOURCE_AI_NO_VOCALS
    assert second.source == lyrics.SOURCE_AI_NO_VOCALS
    assert cached is True
    assert calls["count"] == 1


def test_too_long_never_calls_transcription(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    sample_info["description"] = ""
    sample_info["duration"] = 601

    def fail(*args, **kwargs):
        raise AssertionError("transcription must not start for a long video")

    monkeypatch.setattr(ai_lyrics, "_transcribe", fail)
    result, _cached = ai_lyrics.resolve(sample_info, {}, 601)

    assert result.source == lyrics.SOURCE_AI_TOO_LONG


def test_verbatim_lyrics_are_checked_against_the_description() -> None:
    description = (
        "Verse one line\nVerse two line\nVerse three line\nVerse four line"
    )

    assert ai_lyrics._verbatim_lyrics(
        "Verse one line\nVerse two line\nVerse three line\nVerse four line",
        description,
    )
    assert (
        ai_lyrics._verbatim_lyrics(
            "Verse one line\nInvented line that is not there", description
        )
        == ""
    )
    # Even one invented line out of five is rejected.
    assert (
        ai_lyrics._verbatim_lyrics(
            "Verse one line\nVerse two line\nVerse three line\nVerse four line\n"
            "One more invented line",
            description,
        )
        == ""
    )


def test_supported_tags_require_description_evidence() -> None:
    description = (
        "Album: Whenever You Need Somebody\n"
        "Released in 1987\n"
        "Track: 3\n"
        "Synth-pop anthem\n"
        "ISRC: USABC1234567\n"
    )
    values = {
        "album": "Whenever You Need Somebody",
        "year": "1987",
        "genre": "Synth-pop",
        "track": "3",
        "publisher": "Definitely Real Records",
        "isrc": "USABC1234567",
    }

    tags = ai_lyrics._supported_tags(
        values, description, {"genre"}
    )

    assert tags["album"] == "Whenever You Need Somebody"
    assert tags["year"] == "1987"
    assert tags["genre"] == "Synth-pop"
    assert tags["track"] == "3"
    assert tags["isrc"] == "USABC1234567"
    assert "publisher" not in tags


def test_enrich_tags_fills_only_missing_fields_with_details(
    groq_ready: None, monkeypatch: pytest.MonkeyPatch, sample_info: dict
) -> None:
    monkeypatch.setattr(
        ai_lyrics,
        "_description_data",
        lambda info: {
            "tags": {"album": "Whenever You Need Somebody", "genre": "Pop"},
            "inferred_tags": ["genre"],
            "warnings": [],
        },
    )

    values, details, warnings = ai_lyrics.enrich_tags(
        sample_info, {"album": "User album"}
    )

    assert values == {"genre": "Pop"}
    assert details["genre"]["inferred"] is True
    assert warnings == []


def test_lrc_timestamp_uses_two_centisecond_digits() -> None:
    assert ai_lyrics._lrc_timestamp(0) == "[00:00.00]"
    assert ai_lyrics._lrc_timestamp(61.234) == "[01:01.23]"


def test_payload_keeps_raw_and_formatted_transcripts() -> None:
    result = lyrics.Result(
        source=lyrics.SOURCE_AI_AUDIO,
        plain="Formatted line",
        synced="[00:01.00]Formatted line",
        raw="formatted line",
        formatted="Formatted line",
        language="en",
        warnings=("Check the spelling.",),
    )

    data = ai_lyrics.payload(result, cached=True)

    assert data["available"] is True
    assert data["synchronized"] is True
    assert data["raw_transcript"] == "formatted line"
    assert data["formatted_transcript"] == "Formatted line"
    assert data["cached"] is True
    assert data["warnings"] == ["Check the spelling."]

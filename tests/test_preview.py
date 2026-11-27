"""Inline preview page and stream route (offline, synthetic yt-dlp payload)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import main
from core import config
from core.streaming import PreparedStream

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _prepared() -> PreparedStream:
    def chunks():
        yield b"fake-video-bytes"

    return PreparedStream(chunks=chunks(), close=lambda: None, content_length=None)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, sample_info: dict) -> TestClient:
    monkeypatch.setattr(config, "FFMPEG_OK", True)
    monkeypatch.setattr(config, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(main, "get_info", lambda url: sample_info)
    monkeypatch.setattr(main, "prepare_stream", lambda plan: _prepared())
    with TestClient(main.app) as test_client:
        yield test_client


def test_preview_page_renders_inline_player(client: TestClient) -> None:
    response = client.post("/preview", data={"media_url": URL})

    assert response.status_code == 200
    body = response.text
    assert 'id="inlinePreview"' in body
    assert 'preload="none"' in body
    assert "/preview/stream?url=" in body
    assert "Play before you download" in body


def test_preview_tag_editor_lists_the_extra_fields(
    client: TestClient, sample_info: dict
) -> None:
    sample_info.update(
        {
            "track_number": 3,
            "track_total": 12,
            "copyright": "© 1987 Sony Music",
            "publisher": "RCA Records",
            "isrc": "GBARL9300135",
        }
    )

    body = client.post("/preview", data={"media_url": URL}).text

    for field in ("tagDisc", "tagCopyright", "tagPublisher", "tagIsrc"):
        assert f'id="{field}"' in body
    assert 'value="3/12"' in body
    assert 'value="© 1987 Sony Music"' in body
    assert "LRCLIB" in body


def test_preview_audio_cards_show_a_lyrics_chip(
    client: TestClient, sample_info: dict
) -> None:
    sample_info["description"] = (
        "Lyrics:\n"
        "[Verse 1]\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
    )

    body = client.post("/preview", data={"media_url": URL}).text

    assert 'data-lyrics-chip="1"' in body
    assert ">Lyrics</span>" in body
    assert "Lyrics when available" not in body


def test_preview_audio_cards_show_no_lyrics(client: TestClient) -> None:
    body = client.post("/preview", data={"media_url": URL}).text

    assert ">No lyrics</span>" in body
    assert ">Lyrics</span>" not in body
    assert "chip-lyrics" in body


def test_preview_has_a_download_confirmation_step(client: TestClient) -> None:
    body = client.post("/preview", data={"media_url": URL}).text

    for marker in (
        'id="downloadConfirm"',
        'id="confirmStart"',
        'id="confirmCancel"',
        'id="confirmLyricsRow"',
        'id="confirmCaptionsRow"',
        "Confirm download",
    ):
        assert marker in body


def test_api_prepare_reports_lyrics_and_captions(
    client: TestClient, sample_info: dict
) -> None:
    sample_info["description"] = (
        "Lyrics:\n"
        "[Verse 1]\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
    )
    sample_info["subtitles"] = {"en": [{"ext": "vtt"}]}

    payload = client.get(
        "/api/prepare",
        params={"url": URL, "kind": "audio", "profile": "flac"},
    ).json()

    plan = payload["plan"]
    assert plan["lyrics"]["available"] is True
    assert plan["lyrics"]["status"] == "description"
    assert plan["captions"]["manual_count"] == 1
    assert plan["captions"]["manual"][0]["name"] == "English"


def test_preview_stream_is_inline_and_uncached(client: TestClient) -> None:
    response = client.get("/preview/stream", params={"url": URL})

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["accept-ranges"] == "none"
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"fake-video-bytes"


def test_preview_stream_rejects_unsupported_url(client: TestClient) -> None:
    response = client.get("/preview/stream", params={"url": "https://example.com/video"})

    assert response.status_code == 400


def test_api_prepare_forwards_extra_tags(client: TestClient) -> None:
    response = client.get(
        "/api/prepare",
        params={
            "url": URL,
            "kind": "audio",
            "profile": "flac",
            "copyright": "© 1987 Sony Music",
            "publisher": "RCA Records",
            "isrc": "GBARL9300135",
            "disc": "1/2",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True

    query = parse_qs(urlparse(payload["download_url"]).query)
    assert query["copyright"] == ["© 1987 Sony Music"]
    assert query["publisher"] == ["RCA Records"]
    assert query["isrc"] == ["GBARL9300135"]
    assert query["disc"] == ["1/2"]


def test_preview_audio_tab_has_ai_controls(client: TestClient) -> None:
    body = client.post("/preview", data={"media_url": URL}).text

    for marker in ('id="aiLyrics"', 'id="aiTags"', 'id="aiNote"', 'id="aiStatus"'):
        assert marker in body
    # No GROQ_API_KEY in the test environment, so both opt-ins stay disabled.
    assert 'id="aiLyrics" disabled' in body
    assert 'id="aiTags" disabled' in body


def test_api_lyrics_returns_free_description_lyrics(
    client: TestClient, sample_info: dict
) -> None:
    sample_info["description"] = (
        "Lyrics:\n"
        "[Verse 1]\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
    )

    payload = client.get(
        "/api/lyrics", params={"url": URL, "ai": "1"}
    ).json()

    assert payload["ok"] is True
    assert payload["lyrics"]["status"] == "description"
    assert payload["lyrics"]["available"] is True
    assert "We're no strangers to love" in payload["lyrics"]["text"]


def test_api_prepare_forwards_ai_flags(client: TestClient, sample_info: dict) -> None:
    sample_info["description"] = (
        "Lyrics:\n"
        "[Verse 1]\n"
        "We're no strangers to love\n"
        "You know the rules and so do I\n"
        "A full commitment's what I'm thinking of\n"
    )

    payload = client.get(
        "/api/prepare",
        params={
            "url": URL,
            "kind": "audio",
            "profile": "mp3_320",
            "ai_lyrics": "1",
            "ai_tags": "1",
        },
    ).json()

    query = parse_qs(urlparse(payload["download_url"]).query)
    assert query["ai_lyrics"] == ["1"]
    assert query["ai_tags"] == ["1"]

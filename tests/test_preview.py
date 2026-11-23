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

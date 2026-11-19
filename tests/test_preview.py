"""Inline preview page and stream route (offline, synthetic yt-dlp payload)."""

from __future__ import annotations

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

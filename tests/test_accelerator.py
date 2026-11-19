"""Parallel-range accelerator tests (offline, local origin)."""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import httpx
import pytest

from core import accelerator, config


class _OriginHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body: bytes = self.server.body  # type: ignore[attr-defined]
        supports_range: bool = self.server.supports_range  # type: ignore[attr-defined]
        header = self.headers.get("Range")
        match = re.match(r"bytes=(\d+)-(\d*)", header or "")
        if supports_range and match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else len(body) - 1
            chunk = body[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        else:
            chunk = body
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes" if supports_range else "none")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except OSError:
            pass


@pytest.fixture()
def origin() -> Iterator[tuple[str, bytes, ThreadingHTTPServer]]:
    body = bytes(range(256)) * 8192 + b"tail" * 3081  # ~2.09 MB, not segment-aligned
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
    server.daemon_threads = True
    server.body = body  # type: ignore[attr-defined]
    server.supports_range = True  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/media", body, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture()
def tuned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PARALLEL_ENABLED", True)
    monkeypatch.setattr(config, "PARALLEL_CONNECTIONS", 4)
    monkeypatch.setattr(config, "PARALLEL_SEGMENT_MB", 1)
    monkeypatch.setattr(config, "PARALLEL_WINDOW_MB", 8)
    monkeypatch.setattr(config, "PARALLEL_MAX_CONNECTIONS", 16)
    accelerator.reset_pool_state()
    yield
    accelerator.reset_pool_state()


def test_accelerator_serves_the_whole_body(tuned: None, origin) -> None:
    url, body, _ = origin
    accel = accelerator.open_accelerator(url, {}, label="test")
    assert accel is not None
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(accel.url)
        assert response.status_code == 200
        assert response.content == body
        assert response.headers["accept-ranges"] == "bytes"
    finally:
        accel.close()


def test_accelerator_honours_range_requests(tuned: None, origin) -> None:
    url, body, _ = origin
    accel = accelerator.open_accelerator(url, {}, label="test")
    assert accel is not None
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(accel.url, headers={"Range": "bytes=1024-2047"})
        assert response.status_code == 206
        assert response.content == body[1024:2048]
        assert response.headers["content-range"] == f"bytes 1024-2047/{len(body)}"
    finally:
        accel.close()


def test_accelerator_skips_origins_without_range_support(tuned: None, origin) -> None:
    url, _, server = origin
    server.supports_range = False  # type: ignore[attr-defined]

    assert accelerator.open_accelerator(url, {}, label="test") is None


def test_accelerator_skips_non_http_urls(tuned: None) -> None:
    assert accelerator.open_accelerator("file:///tmp/movie.mp4", {}, label="test") is None


def test_accelerator_disabled_by_config(monkeypatch: pytest.MonkeyPatch, origin) -> None:
    url, _, _ = origin
    monkeypatch.setattr(config, "PARALLEL_ENABLED", False)

    assert accelerator.open_accelerator(url, {}, label="test") is None


def test_ffmpeg_input_swaps_in_the_local_proxy(tuned: None, origin) -> None:
    from core.download import _ffmpeg_input

    url, _, _ = origin
    cleanups: list = []
    args = _ffmpeg_input({"url": url, "http_headers": {}}, cleanups=cleanups)

    assert args[args.index("-i") + 1].startswith("http://127.0.0.1:")
    assert cleanups
    for cleanup in cleanups:
        cleanup()


def test_ffmpeg_input_keeps_direct_url_without_acceleration(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.download import _ffmpeg_input

    monkeypatch.setattr(config, "PARALLEL_ENABLED", False)
    args = _ffmpeg_input({"url": "https://media.example/video.mp4"}, cleanups=[])

    assert args[args.index("-i") + 1] == "https://media.example/video.mp4"


def test_ffmpeg_input_keeps_manifests_on_the_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.download import _ffmpeg_input

    monkeypatch.setattr(config, "PARALLEL_ENABLED", True)
    fmt = {"url": "https://media.example/master.m3u8", "protocol": "m3u8_native"}
    args = _ffmpeg_input(fmt, cleanups=[])

    assert args[args.index("-i") + 1] == "https://media.example/master.m3u8"

"""Parallel-range download accelerator.

Some CDNs (googlevideo in particular) cap the throughput of a single TCP
connection.  A 4K/8K stream is muxed by ffmpeg over one connection per input,
so a fast line still crawls: on a test video a single connection managed
~0.36 MiB/s while twelve parallel ranges reached ~4.2 MiB/s.

This module exposes an origin stream through a tiny localhost HTTP server.
ffmpeg reads it like any other URL, while a pool of workers fetches contiguous
``Range`` segments over several connections and serves them in order from a
bounded in-memory window.  Nothing is written to disk, so the app keeps its
zero-storage guarantee; memory use is capped by the window size.

Everything degrades gracefully: when an origin does not support ranges, has an
unknown size, or is too small to benefit, :func:`open_accelerator` returns
``None`` and the caller keeps using the original URL.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

import httpx

from . import config

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 20.0
_FETCH_TIMEOUT = 60.0
_FETCH_RETRIES = 3
_READ_CHUNK = 256 * 1024
_MIN_TOTAL_BYTES = 2 * 1024 * 1024

_slot_lock = threading.Lock()
_slot_semaphore: threading.BoundedSemaphore | None = None
_slot_size = -1


class RangeUnsupported(Exception):
    """The origin ignored a ``Range`` request and returned the whole body."""


def _connection_slots() -> threading.BoundedSemaphore:
    """Global cap so several downloads cannot open hundreds of sockets."""
    global _slot_semaphore, _slot_size
    limit = max(1, int(config.PARALLEL_MAX_CONNECTIONS))
    with _slot_lock:
        if _slot_semaphore is None or _slot_size != limit:
            _slot_semaphore = threading.BoundedSemaphore(limit)
            _slot_size = limit
        return _slot_semaphore


def reset_pool_state() -> None:
    """Drop the shared semaphore (used by tests after changing config)."""
    global _slot_semaphore, _slot_size
    with _slot_lock:
        _slot_semaphore = None
        _slot_size = -1


# --------------------------------------------------------------------------- #
# Origin probing
# --------------------------------------------------------------------------- #


def _probe(url: str, headers: dict[str, str]) -> tuple[bool, int | None]:
    """Check range support and resolve the exact total size."""
    request_headers = dict(headers)
    request_headers["Range"] = "bytes=0-1"
    request_headers["Accept-Encoding"] = "identity"

    with httpx.Client(timeout=_PROBE_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url, headers=request_headers)

    if response.status_code != 206:
        return False, None
    match = re.match(r"bytes\s+\d+-\d+/(\d+)", response.headers.get("content-range", ""))
    if not match:
        return False, None
    total = int(match.group(1))
    return total > 0, total


def _parse_range(header: str | None, total: int) -> tuple[int, int, bool] | None:
    """Return ``(start, end, ranged)`` or ``None`` when unsatisfiable."""
    if not header:
        return 0, total - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        return 0, total - 1, False
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        suffix = int(last)
        start, end = max(0, total - suffix), total - 1
    else:
        start = int(first)
        end = int(last) if last else total - 1
        end = min(end, total - 1)
    if start >= total or start > end:
        return None
    return start, end, True


# --------------------------------------------------------------------------- #
# Segment source
# --------------------------------------------------------------------------- #


class SegmentSource:
    """Fetch one origin stream in parallel uncontiguous-range segments.

    A single reader consumes segments in order.  Fetching runs ahead by at most
    ``window`` segments so memory stays bounded even for 8K files.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        total: int,
        *,
        segment_size: int,
        connections: int,
        window: int,
        start_offset: int = 0,
        label: str = "media",
    ) -> None:
        self.url = url
        self.headers = dict(headers)
        self.total = total
        self.segment_size = max(64 * 1024, int(segment_size))
        self.segment_count = (total + self.segment_size - 1) // self.segment_size
        self.label = label

        self._connections = max(1, int(connections))
        self._window = max(2, int(window))
        start_index = min(max(0, start_offset // self.segment_size), self.segment_count)

        self._condition = threading.Condition()
        self._ready: dict[int, bytes] = {}
        self._next_scheduled = start_index
        self._read_position = start_index
        self._cursor = start_index
        self._error: str | None = None
        self._closed = False

        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=20.0, read=_FETCH_TIMEOUT, write=30.0, pool=30.0),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=self._connections + 4,
                max_keepalive_connections=self._connections,
            ),
        )
        self._threads = [
            threading.Thread(target=self._worker, name=f"rr-seg-{label}-{index}", daemon=True)
            for index in range(self._connections)
        ]
        for thread in self._threads:
            thread.start()

    # -- fetching -------------------------------------------------------- #

    def _fetch(self, index: int) -> bytes:
        start = index * self.segment_size
        end = min(start + self.segment_size, self.total) - 1
        expected = end - start + 1
        headers = dict(self.headers)
        headers["Range"] = f"bytes={start}-{end}"
        headers["Accept-Encoding"] = "identity"

        last_error: Exception | None = None
        for attempt in range(_FETCH_RETRIES):
            if self._closed:
                raise RuntimeError("accelerator closed")
            try:
                slots = _connection_slots()
                acquired = slots.acquire(timeout=30)
                if not acquired:
                    raise TimeoutError("no download slot available")
                try:
                    with self._client.stream("GET", self.url, headers=headers) as response:
                        if response.status_code == 416:
                            raise RangeUnsupported("origin rejected the range")
                        if response.status_code not in (200, 206):
                            raise RuntimeError(f"origin returned HTTP {response.status_code}")
                        if response.status_code == 200 and (start, end) != (0, expected - 1):
                            raise RangeUnsupported("origin ignored the range request")
                        buffer = bytearray()
                        for chunk in response.iter_raw(64 * 1024):
                            buffer += chunk
                            if len(buffer) >= expected:
                                break
                        if len(buffer) < expected:
                            raise RuntimeError(
                                f"short segment {index}: {len(buffer)} of {expected} bytes"
                            )
                        return bytes(buffer[:expected])
                finally:
                    slots.release()
            except RangeUnsupported:
                raise
            except Exception as exc:  # noqa: BLE001 - network flakiness
                last_error = exc
                if self._closed:
                    raise RuntimeError("accelerator closed") from exc
                threading.Event().wait(0.4 * (attempt + 1))
        raise RuntimeError(f"could not fetch segment {index}: {last_error}")

    def _worker(self) -> None:
        while True:
            with self._condition:
                while (
                    not self._closed
                    and (
                        self._next_scheduled >= self.segment_count
                        or self._next_scheduled - self._read_position >= self._window
                    )
                ):
                    self._condition.wait(0.5)
                if self._closed:
                    return
                index = self._next_scheduled
                self._next_scheduled += 1

            try:
                data = self._fetch(index)
            except RangeUnsupported:
                with self._condition:
                    self._error = "origin does not support parallel ranges"
                    self._condition.notify_all()
                return
            except Exception as exc:  # noqa: BLE001
                with self._condition:
                    if not self._closed:
                        self._error = str(exc)
                        logger.debug("%s segment %s failed: %s", self.label, index, exc)
                    self._condition.notify_all()
                return

            with self._condition:
                if self._closed:
                    return
                self._ready[index] = data
                self._condition.notify_all()

    # -- reading --------------------------------------------------------- #

    def read_from(self, offset: int, limit: int | None = None) -> Iterator[bytes]:
        index, skip = divmod(offset, self.segment_size)
        remaining = limit

        with self._condition:
            if index != self._cursor:
                # A seek: discard look-ahead and reschedule from the new spot.
                self._ready.clear()
                self._next_scheduled = index
                self._cursor = index
            self._read_position = index
            self._condition.notify_all()

        while index < self.segment_count and (remaining is None or remaining > 0):
            with self._condition:
                self._read_position = index
                self._cursor = index
                self._condition.notify_all()
                while (
                    index not in self._ready
                    and self._error is None
                    and not self._closed
                ):
                    self._condition.wait(0.5)
                if self._error is not None:
                    raise RuntimeError(self._error)
                if self._closed:
                    return
                data = self._ready.pop(index)

            chunk = data[skip:]
            skip = 0
            if remaining is not None:
                chunk = chunk[:remaining]
                remaining -= len(chunk)
            if chunk:
                yield chunk
            index += 1

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._ready.clear()
            self._condition.notify_all()
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass
        current = threading.current_thread()
        deadline = time.monotonic() + 2.0
        for thread in self._threads:
            if thread is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)


# --------------------------------------------------------------------------- #
# Localhost server
# --------------------------------------------------------------------------- #


@dataclass
class _ServerConfig:
    url: str
    headers: dict[str, str]
    total: int
    path: str
    content_type: str
    segment_size: int
    connections: int
    window: int
    label: str


class _AccelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: _ServerConfig) -> None:
        super().__init__(address, _AccelHandler)
        self.config = config
        self._sources: set[SegmentSource] = set()
        self._sources_lock = threading.Lock()

    def new_source(self, start: int) -> SegmentSource:
        source = SegmentSource(
            self.config.url,
            self.config.headers,
            self.config.total,
            segment_size=self.config.segment_size,
            connections=self.config.connections,
            window=self.config.window,
            start_offset=start,
            label=self.config.label,
        )
        with self._sources_lock:
            self._sources.add(source)
        return source

    def release_source(self, source: SegmentSource) -> None:
        with self._sources_lock:
            self._sources.discard(source)
        source.close()

    def close_sources(self) -> None:
        with self._sources_lock:
            sources = list(self._sources)
            self._sources.clear()
        for source in sources:
            source.close()


class _AccelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ReelRelayAccelerator"

    def log_message(self, *args: object) -> None:  # noqa: D401 - silence http.server
        return

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        self._serve(head_only=True)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._serve(head_only=False)

    def _serve(self, *, head_only: bool) -> None:
        server: _AccelServer = self.server  # type: ignore[assignment]
        cfg = server.config
        if self.path.split("?", 1)[0] != cfg.path:
            self.send_error(404)
            return

        span = _parse_range(self.headers.get("Range"), cfg.total)
        if span is None:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{cfg.total}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start, end, ranged = span
        length = end - start + 1
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", cfg.content_type)
        self.send_header("Accept-Ranges", "bytes")
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{cfg.total}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        if head_only:
            return

        source = server.new_source(start)
        try:
            for chunk in source.read_from(start, length):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:  # noqa: BLE001 - client went away / origin failed
            logger.debug("accelerator stream ended: %s", exc)
        finally:
            server.release_source(source)


class Accelerator:
    """A running localhost proxy for one origin stream."""

    def __init__(self, server: _AccelServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread
        self._closed = False
        self.url = f"http://127.0.0.1:{server.server_address[1]}{server.config.path}"
        self.total = server.config.total

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._server.server_close()
        except Exception:  # noqa: BLE001
            pass
        self._server.close_sources()
        self._thread.join(timeout=2.0)


def open_accelerator(
    url: str,
    headers: dict[str, str],
    *,
    content_type: str = "video/mp4",
    label: str = "media",
) -> Accelerator | None:
    """Start a parallel-range proxy, or return ``None`` when not worthwhile."""
    if not config.PARALLEL_ENABLED:
        return None
    if not str(url).lower().startswith(("http://", "https://")):
        return None

    try:
        supported, total = _probe(url, headers)
    except Exception as exc:  # noqa: BLE001 - never block a download
        logger.debug("Parallel probe failed for %s: %s", label, exc)
        return None

    segment_size = max(256 * 1024, int(config.PARALLEL_SEGMENT_MB) * 1024 * 1024)
    if not supported or total is None or total < max(_MIN_TOTAL_BYTES, segment_size):
        return None

    window = max(2, int(config.PARALLEL_WINDOW_MB) * 1024 * 1024 // segment_size)
    # Every connection needs a segment of look-ahead, otherwise the window
    # silently throttles concurrency below PARALLEL_CONNECTIONS.
    window = max(window, int(config.PARALLEL_CONNECTIONS))
    path = f"/stream/{secrets.token_urlsafe(16)}"
    options = _ServerConfig(
        url=url,
        headers=dict(headers),
        total=total,
        path=path,
        content_type=content_type,
        segment_size=segment_size,
        connections=max(1, int(config.PARALLEL_CONNECTIONS)),
        window=window,
        label=label,
    )

    try:
        server = _AccelServer(("127.0.0.1", 0), options)
    except OSError as exc:
        logger.warning("Could not start the download accelerator: %s", exc)
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        name=f"rr-accel-{label}",
        daemon=True,
    )
    thread.start()
    logger.info(
        "Accelerating %s: %s MB over %s connections in %s MB segments",
        label,
        round(total / 1024 / 1024, 1),
        options.connections,
        round(segment_size / 1024 / 1024, 1),
    )
    return Accelerator(server, thread)

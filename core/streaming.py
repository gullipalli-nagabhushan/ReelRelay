"""Streaming primitives.

Two delivery modes, both of which send bytes straight to the browser:

* ``RelayStream`` — proxies an existing media URL with httpx.
* ``FFmpegStream`` — pipes ffmpeg's stdout (remux / transcode) to the client.

No media file is ever written to disk.  The only optional temp file is a JPEG
cover for FLAC tagging, which lives for the duration of one request.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import httpx

from . import config

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
FIRST_BYTE_TIMEOUT = 75  # seconds before a stalled ffmpeg process is killed


class DownloadError(Exception):
    """Raised when a download cannot be started; shown to the user."""


@dataclass
class PreparedStream:
    """An opened stream ready to be consumed by StreamingResponse."""

    chunks: Iterator[bytes]
    close: Callable[[], None]
    content_length: int | None = None


def _friendly_http_error(status: int, body: bytes) -> str:
    text = body[:400].decode("utf-8", "ignore").lower()
    if status in (401, 403):
        return (
            "The source refused the media request (it may have expired or be "
            "restricted). Reload the preview and try again."
        )
    if status == 404:
        return "The media stream is no longer available. Reload the preview and try again."
    if status == 410:
        return "The media link expired. Reload the preview and try again."
    if status == 429:
        return "The source is rate-limiting your connection. Wait a minute and try again."
    if "sign in" in text or "login" in text:
        return "The source requires a sign-in for this media (cookies are not configured)."
    return f"The media server returned HTTP {status} for this stream."


class _RangeNotSatisfiable(Exception):
    """Internal signal: the origin already returned every byte we asked for."""


class RelayStream:
    """Proxy a direct media URL without buffering it on disk.

    Google's CDN occasionally drops a connection mid-body.  Because the bytes
    have already been handed to the browser, the only way to recover is to ask
    the origin for the remainder with an HTTP ``Range`` request and continue.
    We track exactly how many bytes the client has been sent and resume from
    that offset, a bounded number of times.
    """

    MAX_RESUMES = 10
    OPEN_RETRIES = 2

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}
        self._client: httpx.Client | None = None
        self._context = None
        self._response: httpx.Response | None = None
        self._iterator: Iterator[bytes] | None = None
        self._stream_headers: dict[str, str] = {}
        self._first: bytes = b""
        self._sent: int = 0
        self._total: int | None = None
        self._skip: int = 0

    # ------------------------------------------------------------------ #
    # Connection handling
    # ------------------------------------------------------------------ #

    def open(self) -> PreparedStream:
        self._stream_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() in ("user-agent", "referer", "origin", "cookie", "accept-language")
        }
        self._stream_headers.setdefault("User-Agent", config.DEFAULT_USER_AGENT)
        self._stream_headers.setdefault("Referer", config.YOUTUBE_REFERER)
        self._stream_headers.setdefault("Accept", "*/*")
        self._stream_headers["Accept-Encoding"] = "identity"

        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
            follow_redirects=True,
        )

        attempt = 0
        while True:
            try:
                self._connect(0)
                # Read one chunk up front so connection problems surface while
                # we can still show a friendly error page.
                self._first = self._read_chunk() or b""
                if not self._first:
                    raise DownloadError("The source returned an empty media stream. Reload and try again.")
                break
            except DownloadError:
                self.close()
                raise
            except Exception as exc:  # noqa: BLE001 - network flakiness
                self._close_response()
                attempt += 1
                if attempt > self.OPEN_RETRIES:
                    self.close()
                    logger.warning("Could not open media stream: %s", exc)
                    raise DownloadError(
                        "Could not connect to the media server. Wait a moment and try again."
                    ) from exc
                time.sleep(min(2.0 * attempt, 4.0))

        return PreparedStream(
            chunks=self._iter_chunks(),
            close=self.close,
            content_length=self._total,
        )

    def _connect(self, offset: int) -> None:
        """Open a response that starts at ``offset`` bytes into the media."""
        if self._client is None:
            raise DownloadError("The download stream is no longer open.")

        self._close_response()
        headers = dict(self._stream_headers)
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        context = self._client.stream("GET", self.url, headers=headers)
        response = context.__enter__()
        self._context = context
        self._response = response

        status = response.status_code
        if offset > 0 and status == 416:
            self._close_response()
            raise _RangeNotSatisfiable

        if status >= 400:
            body = b""
            try:
                body = response.read()[:2000]
            except Exception:  # noqa: BLE001
                pass
            self._close_response()
            raise DownloadError(_friendly_http_error(status, body))

        # If the origin ignores Range we receive the whole body again; skip the
        # portion the client already has. When it honours Range, make sure the
        # response really starts at the offset we asked for.
        self._skip = 0
        if offset > 0:
            if status == 206:
                match = re.match(r"\s*bytes\s+(\d+)-", response.headers.get("content-range", ""))
                if match:
                    start = int(match.group(1))
                    if start < offset:
                        self._skip = offset - start
                    elif start > offset:
                        self._close_response()
                        raise DownloadError(
                            "The media server returned an inconsistent resume point. Reload the preview and try again."
                        )
            else:
                self._skip = offset
        self._update_total(offset)
        self._iterator = response.iter_raw(CHUNK_SIZE)

    def _update_total(self, offset: int) -> None:
        response = self._response
        if response is None:
            return
        # Only identity bodies have a meaningful length for the bytes we forward.
        encoding = (response.headers.get("content-encoding") or "identity").lower()
        if encoding not in ("identity", ""):
            return

        content_range = response.headers.get("content-range") or ""
        match = re.search(r"/\s*(\d+)\s*$", content_range)
        if match:
            self._total = int(match.group(1))
            return

        raw = response.headers.get("content-length")
        if not raw:
            return
        try:
            length = int(raw)
        except ValueError:
            return
        if response.status_code == 206:
            self._total = offset + length
        elif length > 0:
            self._total = length

    def _close_response(self) -> None:
        context = self._context
        self._context = None
        self._response = None
        self._iterator = None
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Body iteration
    # ------------------------------------------------------------------ #

    def _read_chunk(self) -> bytes | None:
        """Return the next body chunk, honouring a Range fallback skip."""
        iterator = self._iterator
        if iterator is None:
            return None
        try:
            chunk = next(iterator)
        except StopIteration:
            return None

        while self._skip:
            if not chunk:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    return None
                continue
            if len(chunk) <= self._skip:
                self._skip -= len(chunk)
                try:
                    chunk = next(iterator)
                except StopIteration:
                    return None
                continue
            chunk = chunk[self._skip :]
            self._skip = 0
        return chunk

    def _resume(self) -> bool:
        """Reconnect at the current offset. False means nothing is left."""
        try:
            self._connect(self._sent)
        except _RangeNotSatisfiable:
            return False
        return True

    def _iter_chunks(self) -> Iterator[bytes]:
        try:
            if self._first:
                yield self._first
                self._sent += len(self._first)
                self._first = b""

            resumes = 0
            while True:
                if self._iterator is None:
                    try:
                        if not self._resume():
                            break
                    except DownloadError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        resumes += 1
                        if resumes > self.MAX_RESUMES:
                            raise DownloadError(
                                "The connection to the media server kept dropping. Reload the preview and try again."
                            ) from exc
                        logger.warning(
                            "Could not reconnect at %s bytes (%s); retrying", self._sent, exc
                        )
                        time.sleep(min(1.5 * resumes, 6.0))
                        continue

                try:
                    chunk = self._read_chunk()
                except Exception as exc:  # noqa: BLE001 - truncated body / timeout
                    resumes += 1
                    if resumes > self.MAX_RESUMES:
                        logger.error(
                            "Relay gave up after %s attempts at %s/%s bytes: %s",
                            resumes,
                            self._sent,
                            self._total,
                            exc,
                        )
                        raise DownloadError(
                            "The media stream kept dropping before the file finished. "
                            "Reload the preview and try again."
                        ) from exc
                    logger.warning(
                        "Relay interrupted at %s/%s bytes (%s); resuming %s/%s",
                        self._sent,
                        self._total,
                        exc.__class__.__name__,
                        resumes,
                        self.MAX_RESUMES,
                    )
                    self._close_response()
                    time.sleep(min(1.5 * resumes, 6.0))
                    continue

                if chunk is None:
                    # A body that ends early is indistinguishable from a clean
                    # EOF for a plain read, so compare against the known total.
                    if self._total is not None and self._sent < self._total:
                        resumes += 1
                        if resumes > self.MAX_RESUMES:
                            logger.error(
                                "Relay ended early at %s/%s bytes", self._sent, self._total
                            )
                            raise DownloadError(
                                "The media stream ended before the file was complete. "
                                "Reload the preview and try again."
                            )
                        logger.warning(
                            "Relay ended early at %s/%s bytes; resuming %s/%s",
                            self._sent,
                            self._total,
                            resumes,
                            self.MAX_RESUMES,
                        )
                        self._close_response()
                        time.sleep(min(1.5 * resumes, 6.0))
                        continue
                    break

                if chunk:
                    resumes = 0
                    yield chunk
                    self._sent += len(chunk)
        finally:
            self.close()

    def close(self) -> None:
        self._close_response()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


class FFmpegStream:
    """Run ffmpeg and stream its stdout without touching the filesystem."""

    def __init__(
        self,
        command: list[str],
        prefix: bytes = b"",
        label: str = "ffmpeg",
        cleanup: Callable[[], None] | None = None,
    ) -> None:
        self.command = command
        self.prefix = prefix
        self.label = label
        self.cleanup = cleanup
        self.process: subprocess.Popen[bytes] | None = None
        self._first: bytes = b""
        self._stderr: deque[bytes] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None
        self._watchdog: threading.Timer | None = None

    def open(self) -> PreparedStream:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        logger.info("Starting %s pipeline", self.label)
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creationflags,
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self.process.stderr,), daemon=True
        )
        self._stderr_thread.start()

        self._watchdog = threading.Timer(FIRST_BYTE_TIMEOUT, self._kill_if_stalled)
        self._watchdog.daemon = True
        self._watchdog.start()

        try:
            self._first = os.read(self.process.stdout.fileno(), 64 * 1024)
        except Exception:  # noqa: BLE001
            self._first = b""
        finally:
            if self._watchdog:
                self._watchdog.cancel()

        if not self._first:
            tail = self.stderr_tail()
            code = self.process.poll()
            self.close()
            message = _friendly_ffmpeg_error(tail)
            logger.warning("%s produced no output (exit=%s): %s", self.label, code, tail)
            raise DownloadError(message)

        return PreparedStream(
            chunks=self._iter_chunks(),
            close=self.close,
            content_length=None,
        )

    def _kill_if_stalled(self) -> None:
        if self.process and self.process.poll() is None:
            logger.warning("%s did not produce output in %ss - stopping it", self.label, FIRST_BYTE_TIMEOUT)
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass

    def _drain_stderr(self, pipe: Any) -> None:
        try:
            for line in iter(pipe.readline, b""):
                self._stderr.append(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                pipe.close()
            except Exception:  # noqa: BLE001
                pass

    def stderr_tail(self) -> str:
        return b"".join(self._stderr).decode("utf-8", "ignore").strip()

    def _iter_chunks(self) -> Iterator[bytes]:
        assert self.process is not None and self.process.stdout is not None
        fd = self.process.stdout.fileno()
        try:
            if self.prefix:
                yield self.prefix
            if self._first:
                yield self._first
            while True:
                chunk = os.read(fd, CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:  # noqa: BLE001
                        pass
            self.process = None

        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None

        if self.cleanup is not None:
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                logger.warning("Failed to clean up a temporary download resource", exc_info=True)
            self.cleanup = None


def _friendly_ffmpeg_error(stderr: str) -> str:
    text = (stderr or "").lower()
    if "403" in text or "forbidden" in text:
        return "The source refused the stream (link expired). Reload the preview and try again."
    if "404" in text or "not found" in text:
        return "The media stream disappeared before conversion finished. Reload and try again."
    if "server returned 5" in text:
        return "The media server is temporarily unavailable for this stream. Try again in a moment."
    if "invalid data found" in text:
        return "The selected stream could not be decoded. Try another quality."
    if "no such file" in text:
        return "ffmpeg is missing on this computer. Install ffmpeg and restart the app."
    if stderr:
        last_line = stderr.strip().splitlines()[-1]
        return f"Media conversion failed: {last_line[:300]}"
    return "Media conversion failed before any data was produced. Try another quality."

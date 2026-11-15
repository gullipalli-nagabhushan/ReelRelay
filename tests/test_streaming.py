"""RelayStream resilience: resume truncated bodies with HTTP Range."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core import streaming
from core.streaming import DownloadError, RelayStream

BODY = b"abcdefghij"


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        fail_at_end: bool = False,
    ) -> None:
        self.status_code = status
        self.headers = httpx.Headers(headers or {})
        self._chunks = chunks
        self._fail_at_end = fail_at_end

    def iter_raw(self, chunk_size: int):
        for chunk in self._chunks:
            yield chunk
        if self._fail_at_end:
            raise httpx.RemoteProtocolError("peer closed connection early")

    def read(self) -> bytes:
        return b"".join(self._chunks)


class FakeContext:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> FakeResponse:
        return self._response

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeClient:
    def __init__(self, responses: list[FakeResponse], log: list[dict[str, str]]) -> None:
        self._responses = responses
        self._log = log

    def stream(self, method: str, url: str, headers: dict[str, str] | None = None) -> FakeContext:
        self._log.append(dict(headers or {}))
        return FakeContext(self._responses.pop(0))

    def close(self) -> None:
        pass


@pytest.fixture()
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming.time, "sleep", lambda _seconds: None)


def install_client(monkeypatch: pytest.MonkeyPatch, responses: list[FakeResponse], log: list[dict[str, str]]) -> None:
    monkeypatch.setattr(streaming.httpx, "Client", lambda *args, **kwargs: FakeClient(responses, log))


def read_all(stream: RelayStream) -> tuple[bytes, int | None]:
    prepared = stream.open()
    body = b"".join(prepared.chunks)
    prepared.close()
    return body, prepared.content_length


def test_resumes_with_range_after_truncation(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [
            FakeResponse([b"abc", b"de"], headers={"Content-Length": "10"}, fail_at_end=True),
            FakeResponse(
                [b"fghij"],
                status=206,
                headers={"Content-Range": "bytes 5-9/10", "Content-Length": "5"},
            ),
        ],
        log,
    )

    body, length = read_all(RelayStream("https://media.example/audio"))

    assert body == BODY
    assert length == 10
    assert log[0].get("Range") is None
    assert log[1]["Range"] == "bytes=5-"


def test_resumes_when_body_ends_without_an_exception(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [
            FakeResponse([b"abc"], headers={"Content-Length": "10"}),
            FakeResponse(
                [b"defghij"],
                status=206,
                headers={"Content-Range": "bytes 3-9/10", "Content-Length": "7"},
            ),
        ],
        log,
    )

    body, length = read_all(RelayStream("https://media.example/audio"))

    assert body == BODY
    assert length == 10
    assert log[1]["Range"] == "bytes=3-"


def test_skips_prefix_when_origin_ignores_range(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [
            FakeResponse([b"abc"], headers={"Content-Length": "10"}),
            # Range ignored: 200 + the full body again.
            FakeResponse([b"abcdefghij"], headers={"Content-Length": "10"}),
        ],
        log,
    )

    body, length = read_all(RelayStream("https://media.example/audio"))

    assert body == BODY
    assert length == 10
    assert log[1]["Range"] == "bytes=3-"


def test_stops_cleanly_when_range_is_not_satisfiable(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [
            # Advertises more bytes than it delivers.
            FakeResponse([BODY], headers={"Content-Length": "13"}),
            FakeResponse([b""], status=416),
        ],
        log,
    )

    body, length = read_all(RelayStream("https://media.example/audio"))

    assert body == BODY
    assert length == 13
    assert log[1]["Range"] == "bytes=10-"


def test_rejects_inconsistent_resume_offset(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [
            FakeResponse([b"abc"], headers={"Content-Length": "10"}),
            # We asked for bytes=3- but the origin starts at 5.
            FakeResponse(
                [b"fghij"],
                status=206,
                headers={"Content-Range": "bytes 5-9/10", "Content-Length": "5"},
            ),
        ],
        log,
    )

    with pytest.raises(DownloadError):
        read_all(RelayStream("https://media.example/audio"))


def test_friendly_error_for_forbidden_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [FakeResponse([b"denied"], status=403)],
        log,
    )

    with pytest.raises(DownloadError) as excinfo:
        RelayStream("https://media.example/audio").open()

    assert "refused" in str(excinfo.value).lower()


def test_reports_origin_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[dict[str, str]] = []
    install_client(
        monkeypatch,
        [FakeResponse([BODY], headers={"Content-Length": "10"})],
        log,
    )

    body, length = read_all(RelayStream("https://media.example/audio"))

    assert body == BODY
    assert length == 10

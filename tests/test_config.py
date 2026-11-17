"""Cookie configuration, including serverless-friendly env content."""

from __future__ import annotations

import base64
from pathlib import Path

from core import config

COOKIE_TEXT = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.haxx.se/docs/http-cookies.html\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc123\n"
    ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\txyz789\n"
)


def _clear_cookie_env(monkeypatch) -> None:
    for name in ("YTDLP_COOKIES_FILE", "YTDLP_COOKIES", "YTDLP_COOKIES_B64"):
        monkeypatch.delenv(name, raising=False)


def test_no_cookie_configuration_returns_none(monkeypatch) -> None:
    _clear_cookie_env(monkeypatch)
    assert config._cookies_from_env() is None


def test_cookie_file_path_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("YTDLP_COOKIES_FILE", "/srv/secret/cookies.txt")
    monkeypatch.setenv("YTDLP_COOKIES", COOKIE_TEXT)
    monkeypatch.setenv("YTDLP_COOKIES_B64", "ignored")
    assert config._cookies_from_env() == "/srv/secret/cookies.txt"


def test_raw_cookie_text_is_written_to_a_temp_file(monkeypatch) -> None:
    _clear_cookie_env(monkeypatch)
    monkeypatch.setenv("YTDLP_COOKIES", COOKIE_TEXT)

    path = config._cookies_from_env()

    assert path
    assert Path(path).name.startswith("reelrelay-cookies-")
    content = Path(path).read_text(encoding="utf-8")
    assert "SID\tabc123" in content
    assert "sessionid\txyz789" in content


def test_base64_cookie_text_is_decoded(monkeypatch) -> None:
    _clear_cookie_env(monkeypatch)
    monkeypatch.setenv(
        "YTDLP_COOKIES_B64",
        base64.b64encode(COOKIE_TEXT.encode("utf-8")).decode("ascii"),
    )

    path = config._cookies_from_env()

    assert path
    assert "SID\tabc123" in Path(path).read_text(encoding="utf-8")


def test_invalid_base64_is_ignored(monkeypatch, caplog) -> None:
    _clear_cookie_env(monkeypatch)
    monkeypatch.setenv("YTDLP_COOKIES_B64", "not base64 at all!")

    assert config._cookies_from_env() is None
    assert "not valid base64" in caplog.text


def test_unexpected_cookie_format_still_produces_a_file(monkeypatch, caplog) -> None:
    _clear_cookie_env(monkeypatch)
    monkeypatch.setenv("YTDLP_COOKIES", "SID=abc123; sessionid=xyz789")

    path = config._cookies_from_env()

    assert path
    assert "do not look like a Netscape" in caplog.text

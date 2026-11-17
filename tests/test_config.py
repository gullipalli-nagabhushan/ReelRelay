"""Cookie configuration for local use."""

from __future__ import annotations

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


def test_cookie_file_path_is_used(monkeypatch, tmp_path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(COOKIE_TEXT, encoding="utf-8")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(cookies))

    assert config._cookies_from_env() == str(cookies)


def test_legacy_env_vars_are_ignored(monkeypatch, tmp_path) -> None:
    _clear_cookie_env(monkeypatch)
    monkeypatch.setenv("YTDLP_COOKIES", COOKIE_TEXT)
    monkeypatch.setenv("YTDLP_COOKIES_B64", "ignored")

    assert config._cookies_from_env() is None


def test_missing_cookie_file_is_ignored(monkeypatch, tmp_path, caplog) -> None:
    _clear_cookie_env(monkeypatch)
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(tmp_path / "missing.txt"))

    assert config._cookies_from_env() is None
    assert "does not exist" in caplog.text

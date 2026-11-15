"""Multi-platform URL handling and progressive-only media."""

from __future__ import annotations

import pytest

from core import config
from core.cover import cover_candidates
from core.download import build_plan
from core.ytdlp import (
    ExtractionError,
    UnavailableError,
    build_audio_options,
    build_video_options,
    detect_platform,
    is_valid_url,
    normalize_metadata,
    resolve_source,
)


@pytest.fixture()
def ffmpeg_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FFMPEG_OK", True)
    monkeypatch.setattr(config, "FFMPEG", "ffmpeg")


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://www.instagram.com/reel/CxYz123/?igsh=abc", "instagram"),
        ("https://instagram.com/p/CxYz123/", "instagram"),
        ("https://www.instagram.com/reels/CxYz123/", "instagram"),
        ("https://www.facebook.com/watch/?v=123456", "facebook"),
        ("https://www.facebook.com/reel/123456", "facebook"),
        ("https://www.facebook.com/someone/videos/123456", "facebook"),
        ("https://fb.watch/abcDEF/", "facebook"),
    ],
)
def test_detects_supported_platform(url: str, platform: str) -> None:
    assert detect_platform(url) == platform
    assert is_valid_url(url) is True
    assert resolve_source(url).platform == platform


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://example.com/reel/123",
        "https://www.instagram.com/someone/",
        "https://www.instagram.com/explore/",
        "https://www.facebook.com/",
        "https://www.facebook.com/someone/photos/123",
        "https://www.youtube.com/playlist?list=PL123",
    ],
)
def test_rejects_unsupported_or_non_video_links(url: str) -> None:
    assert detect_platform(url) is None
    assert is_valid_url(url) is False
    with pytest.raises(ExtractionError):
        resolve_source(url)


def test_instagram_source_drops_tracking_parameters() -> None:
    source = resolve_source("https://www.instagram.com/reel/CxYz123/?igsh=tracking")
    assert source.url == "https://www.instagram.com/reel/CxYz123/"


def test_facebook_watch_keeps_media_id() -> None:
    source = resolve_source("https://www.facebook.com/watch/?v=123456&fbclid=tracking")
    assert source.url == "https://www.facebook.com/watch/?v=123456"


def test_vertical_video_uses_display_height(progressive_info: dict, ffmpeg_enabled: None) -> None:
    options = build_video_options(progressive_info)
    by_id = {option["id"]: option for option in options}

    assert "v1080-progressive-mp4" in by_id
    assert "v720-progressive-mp4" in by_id
    option = by_id["v1080-progressive-mp4"]
    assert option["height"] == 1080
    assert option["vertical"] is True
    assert option["needs_merge"] is False
    assert option["direct"] is True


def test_progressive_only_audio_has_no_original(progressive_info: dict, ffmpeg_enabled: None) -> None:
    options = build_audio_options(progressive_info)
    profiles = {option["profile"]: option for option in options}

    assert "original" not in profiles
    assert profiles["mp3_320"]["from_video"] is True
    assert "extracted" in profiles["mp3_320"]["description"].lower()


def test_progressive_audio_extracts_from_video(progressive_info: dict, ffmpeg_enabled: None) -> None:
    plan = build_plan(progressive_info, kind="audio", profile="mp3_320")

    assert plan.mode == "audio"
    assert plan.audio_format is not None
    assert plan.audio_format["format_id"] == "hd"
    assert any("no standalone audio" in note.lower() for note in plan.notes)
    assert plan.platform == "instagram"


def test_original_audio_rejected_when_only_progressive(
    progressive_info: dict, ffmpeg_enabled: None
) -> None:
    with pytest.raises(UnavailableError):
        build_plan(progressive_info, kind="audio", profile="original")


def test_progressive_video_is_relayed(progressive_info: dict, ffmpeg_enabled: None) -> None:
    plan = build_plan(progressive_info, kind="video", option_id="v1080-progressive-mp4")

    assert plan.mode == "relay"
    assert plan.stream_url == "https://media.example/reel-hd.mp4"
    assert plan.filename.endswith("[1080p].mp4")
    assert plan.content_length == 10_500_000
    assert plan.platform == "instagram"


def test_cover_candidates_do_not_invent_youtube_urls(progressive_info: dict) -> None:
    urls = cover_candidates(progressive_info)

    assert urls == ["https://cdn.example/thumb.jpg"]
    assert all("ytimg" not in url for url in urls)


def test_instagram_metadata_labels(progressive_info: dict) -> None:
    metadata = normalize_metadata(progressive_info)

    assert metadata["platform"] == "instagram"
    assert metadata["platform_label"] == "Instagram"
    assert metadata["is_youtube"] is False
    assert metadata["channel_label"] == "Creator"
    assert metadata["artist"] == "creator"

"""Format discovery and planning (offline, synthetic yt-dlp payload)."""

from __future__ import annotations

import pytest

from core import config
from core.download import _ffmpeg_input, build_plan, plan_preview
from core.ytdlp import (
    UnavailableError,
    build_audio_options,
    build_video_options,
    find_audio_format,
    find_video_format,
    pick_preview_option,
)


@pytest.fixture()
def ffmpeg_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FFMPEG_OK", True)
    monkeypatch.setattr(config, "FFMPEG", "ffmpeg")


def test_video_catalog_includes_4k_and_1080p(sample_info: dict, ffmpeg_enabled: None) -> None:
    options = build_video_options(sample_info)
    by_id = {option["id"]: option for option in options}

    assert "v2160-vp9-webm" in by_id
    assert by_id["v2160-vp9-webm"]["container"] == "webm"
    assert by_id["v2160-vp9-webm"]["needs_merge"] is True

    assert "v1080-h264-mp4" in by_id
    assert by_id["v1080-h264-mp4"]["codec"] == "H.264"
    assert by_id["v1080-h264-mp4"]["audio_format_id"] == "140"
    assert by_id["v1080-h264-mp4"]["recommended"] is True


def test_video_catalog_includes_8k_when_configured(
    sample_info: dict, monkeypatch: pytest.MonkeyPatch, ffmpeg_enabled: None
) -> None:
    monkeypatch.setattr(config, "MAX_VIDEO_HEIGHT", 4320)
    sample_info["formats"].insert(
        0,
        {
            "format_id": "571",
            "ext": "webm",
            "protocol": "https",
            "vcodec": "vp9",
            "acodec": "none",
            "width": 7680,
            "height": 4320,
            "fps": 60,
            "tbr": 50000,
            "filesize": 2_000_000_000,
            "url": "https://media.example/571.webm",
        },
    )

    options = build_video_options(sample_info)
    by_id = {option["id"]: option for option in options}

    assert "v4320-vp9-webm" in by_id
    assert by_id["v4320-vp9-webm"]["tier"] == "8K"
    assert by_id["v4320-vp9-webm"]["height"] == 4320


def test_video_catalog_without_ffmpeg_uses_progressive(
    sample_info: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "FFMPEG_OK", False)

    options = build_video_options(sample_info)

    assert {option["height"] for option in options} == {360}
    assert options[0]["needs_merge"] is False
    assert options[0]["container"] == "mp4"


def test_audio_catalog_original_and_converted(sample_info: dict, ffmpeg_enabled: None) -> None:
    options = build_audio_options(sample_info)
    profiles = {option["profile"]: option for option in options}

    original = profiles["original"]
    assert original["format_id"] == "140"
    assert original["tags"] is False
    assert original["cover"] is False

    assert "mp3_320" in profiles
    assert profiles["mp3_320"]["recommended"] is True
    assert profiles["flac"]["cover"] is True
    assert profiles["wav"]["tags"] is True


def test_find_video_format_pairs_audio(sample_info: dict, ffmpeg_enabled: None) -> None:
    video, audio = find_video_format(sample_info, "v1080-h264-mp4")

    assert video["format_id"] == "137"
    assert audio["format_id"] == "140"


def test_find_video_format_rejects_unknown(sample_info: dict, ffmpeg_enabled: None) -> None:
    with pytest.raises(UnavailableError):
        find_video_format(sample_info, "v9000-nope")


def test_find_audio_format_prefers_requested_id(sample_info: dict) -> None:
    assert find_audio_format(sample_info, "251")["ext"] == "webm"
    assert find_audio_format(sample_info, None)["format_id"] == "140"


def test_plan_video_merge(sample_info: dict, ffmpeg_enabled: None) -> None:
    plan = build_plan(sample_info, kind="video", option_id="v1080-h264-mp4")

    assert plan.mode == "video"
    assert plan.filename == "Rick Astley - Never Gonna Give You Up [1080p].mp4"
    assert plan.media_type == "video/mp4"
    assert plan.content_length is None
    assert plan.video_format is not None and plan.video_format["format_id"] == "137"
    assert plan.audio_format is not None and plan.audio_format["format_id"] == "140"
    assert plan.notes


def test_plan_video_progressive_is_relayed(sample_info: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FFMPEG_OK", False)

    plan = build_plan(sample_info, kind="video", height=360, container="mp4")

    assert plan.mode == "relay"
    assert plan.stream_url == "https://media.example/18.mp4"
    assert plan.filename.endswith("[360p].mp4")
    assert plan.content_length == 18_000_000


def test_plan_video_unknown_quality(sample_info: dict, ffmpeg_enabled: None) -> None:
    with pytest.raises(UnavailableError):
        build_plan(sample_info, kind="video", option_id="does-not-exist")


def test_ffmpeg_input_uses_reconnect_flags(sample_info: dict) -> None:
    args = _ffmpeg_input(sample_info["formats"][0])

    for flag in (
        "-reconnect",
        "-reconnect_streamed",
        "-reconnect_at_eof",
        "-reconnect_on_network_error",
        "-reconnect_max_retries",
    ):
        assert flag in args
    assert args[args.index("-reconnect_at_eof") + 1] == "1"


def test_plan_audio_original(sample_info: dict) -> None:
    plan = build_plan(sample_info, kind="audio", profile="original", format_id="140")

    assert plan.mode == "relay"
    assert plan.filename == "Rick Astley - Never Gonna Give You Up.m4a"
    assert plan.media_type == "audio/mp4"
    assert plan.content_length == 3_400_000
    assert plan.quality == "128 kbps"


def test_plan_audio_mp3_embeds_tags(sample_info: dict, ffmpeg_enabled: None) -> None:
    plan = build_plan(
        sample_info,
        kind="audio",
        profile="mp3_320",
        tags={"composer": "Custom Composer", "year": "1999"},
    )

    assert plan.mode == "audio"
    assert plan.filename == "Rick Astley - Never Gonna Give You Up.mp3"
    assert plan.media_type == "audio/mpeg"
    assert plan.content_length is None
    assert plan.embed_tags is True
    assert plan.embed_cover is True
    assert plan.tags["composer"] == "Custom Composer"
    assert plan.tags["year"] == "1999"
    assert plan.tags["artist"] == "Rick Astley"


def test_plan_audio_flac_and_wav(sample_info: dict, ffmpeg_enabled: None) -> None:
    flac = build_plan(sample_info, kind="audio", profile="flac")
    wav = build_plan(sample_info, kind="audio", profile="wav")

    assert flac.media_type == "audio/flac"
    assert flac.embed_cover is True
    assert wav.media_type == "audio/wav"
    assert wav.embed_cover is False


def test_plan_audio_requires_ffmpeg(sample_info: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FFMPEG_OK", False)

    with pytest.raises(UnavailableError):
        build_plan(sample_info, kind="audio", profile="mp3_320")


def test_preview_picks_smallest_progressive(sample_info: dict, ffmpeg_enabled: None) -> None:
    option = pick_preview_option(sample_info)

    assert option is not None
    assert option["height"] == 360
    assert option["needs_merge"] is False
    assert option["container"] == "mp4"


def _preview_option(height: int, *, container: str = "mp4", merge: bool = False) -> dict:
    return {
        "id": f"v{height}",
        "container": container,
        "height": height,
        "needs_merge": merge,
        "estimated_size": height * 10,
    }


def test_preview_skips_144p_when_360p_exists() -> None:
    catalog = [_preview_option(144), _preview_option(360), _preview_option(720)]

    option = pick_preview_option({}, catalog)

    assert option is not None and option["height"] == 360


def test_preview_falls_back_to_largest_small_rendition() -> None:
    catalog = [_preview_option(144), _preview_option(240)]

    option = pick_preview_option({}, catalog)

    assert option is not None and option["height"] == 240


def test_plan_preview_relays_a_playable_stream(
    sample_info: dict, ffmpeg_enabled: None
) -> None:
    plan = plan_preview(sample_info)

    assert plan is not None
    assert plan.mode == "relay"
    assert plan.media_type == "video/mp4"
    assert plan.stream_url == "https://media.example/18.mp4"


def test_preview_handles_portrait_progressive(progressive_info: dict, ffmpeg_enabled: None) -> None:
    option = pick_preview_option(progressive_info)
    plan = plan_preview(progressive_info)

    assert option is not None and option["height"] == 720
    assert plan is not None
    assert plan.mode == "relay"
    assert plan.media_type == "video/mp4"


def test_preview_returns_none_for_audio_only(sample_info: dict) -> None:
    audio_only = {
        **sample_info,
        "formats": [fmt for fmt in sample_info["formats"] if fmt["vcodec"] == "none"],
    }

    assert pick_preview_option(audio_only) is None
    assert plan_preview(audio_only) is None

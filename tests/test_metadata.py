"""Metadata derivation from raw yt-dlp dictionaries."""

from __future__ import annotations

from core.ytdlp import normalize_metadata


def test_derives_artist_song_composer_and_year(sample_info: dict) -> None:
    meta = normalize_metadata(sample_info)

    assert meta["artist"] == "Rick Astley"
    assert meta["song_title"] == "Never Gonna Give You Up"
    assert meta["composer"] == "Stock Aitken Waterman"
    assert meta["composer_source"] == "description"
    assert meta["year"] == "1987"
    assert meta["genre"] == "Music"
    assert meta["is_music"] is True
    assert meta["video_id"] == "dQw4w9WgXcQ"


def test_topic_channel_splits_artist_from_title(sample_info: dict) -> None:
    sample_info["channel"] = "Rick Astley - Topic"
    sample_info["uploader"] = "Rick Astley - Topic"
    sample_info["title"] = "Never Gonna Give You Up (Official Audio)"
    sample_info["description"] = ""

    meta = normalize_metadata(sample_info)

    assert meta["artist"] == "Rick Astley"
    assert meta["song_title"] == "Never Gonna Give You Up"
    assert meta["composer"] == ""
    assert meta["composer_source"] == "none"


def test_explicit_metadata_wins(sample_info: dict) -> None:
    sample_info.update(
        {
            "title": "Some upload title",
            "artist": "Explicit Artist",
            "track": "Explicit Song",
            "album": "Explicit Album",
            "composer": "Explicit Composer",
            "release_year": 2001,
        }
    )

    meta = normalize_metadata(sample_info)

    assert meta["artist"] == "Explicit Artist"
    assert meta["song_title"] == "Explicit Song"
    assert meta["album"] == "Explicit Album"
    assert meta["composer"] == "Explicit Composer"
    assert meta["composer_source"] == "youtube"
    assert meta["year"] == "2001"


def test_year_falls_back_to_upload_date(sample_info: dict) -> None:
    sample_info.pop("release_year")
    meta = normalize_metadata(sample_info)
    assert meta["year"] == "2009"


def test_prefers_jpeg_thumbnail(sample_info: dict) -> None:
    meta = normalize_metadata(sample_info)
    assert meta["thumbnail"].endswith("maxresdefault.jpg")
    assert meta["thumbnail_jpg"].endswith("maxresdefault.jpg")

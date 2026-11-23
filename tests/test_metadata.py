"""Metadata derivation from raw yt-dlp dictionaries."""

from __future__ import annotations

from core.ytdlp import normalize_metadata, build_view


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


def test_normalises_extra_audio_identifiers(sample_info: dict) -> None:
    sample_info.update(
        {
            "track_number": 3,
            "track_total": 12,
            "disc_number": 1,
            "disc_total": 2,
            "release_date": "19871116",
            "copyright": "© 1987 Sony Music",
            "publisher": "RCA Records",
            "isrc": "GBARL9300135",
        }
    )

    meta = normalize_metadata(sample_info)

    assert meta["track_number"] == "3"
    assert meta["track_total"] == "12"
    assert meta["disc_number"] == "1"
    assert meta["disc_total"] == "2"
    assert meta["release_date"] == "1987-11-16"
    assert meta["copyright"] == "© 1987 Sony Music"
    assert meta["publisher"] == "RCA Records"
    assert meta["isrc"] == "GBARL9300135"


def test_ignores_an_invalid_release_date(sample_info: dict) -> None:
    sample_info["release_date"] = "not-a-date"

    assert normalize_metadata(sample_info)["release_date"] == ""


def test_reads_list_valued_ytdlp_music_fields(sample_info: dict) -> None:
    sample_info.update(
        {
            "artists": ["Rick Astley", "Guest Singer"],
            "track": "Never Gonna Give You Up",
            "album_artists": ["Rick Astley"],
            "composers": ["Stock Aitken Waterman", "Pete Waterman"],
            "genres": ["Pop", "Dance"],
        }
    )
    sample_info.pop("artist", None)

    meta = normalize_metadata(sample_info)

    assert meta["artist"] == "Rick Astley, Guest Singer"
    assert meta["album_artist"] == "Rick Astley"
    assert meta["composer"] == "Stock Aitken Waterman, Pete Waterman"
    assert meta["composer_source"] == "youtube"
    assert meta["genre"] == "Pop, Dance"


def test_year_can_come_from_release_date(sample_info: dict) -> None:
    sample_info.pop("release_year")
    sample_info["release_date"] = "19991116"

    meta = normalize_metadata(sample_info)

    assert meta["year"] == "1999"
    assert meta["release_date"] == "1999-11-16"


def test_view_exposes_the_full_tag_set(sample_info: dict) -> None:
    sample_info.update(
        {
            "track_number": 3,
            "track_total": 12,
            "disc_number": 1,
            "disc_total": 2,
            "copyright": "© 1987 Sony Music",
            "publisher": "RCA Records",
            "isrc": "GBARL9300135",
        }
    )

    tags = build_view(sample_info)["tags"]

    assert tags == {
        "title": "Never Gonna Give You Up",
        "artist": "Rick Astley",
        "album": "",
        "composer": "Stock Aitken Waterman",
        "year": "1987",
        "genre": "Music",
        "track": "3/12",
        "disc": "1/2",
        "copyright": "© 1987 Sony Music",
        "publisher": "RCA Records",
        "isrc": "GBARL9300135",
    }

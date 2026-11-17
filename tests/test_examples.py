"""The curated landing-page examples are well formed."""

from __future__ import annotations

from core.examples import get_examples


def test_examples_have_urls_and_thumbnails() -> None:
    data = get_examples()

    assert set(data) == {"most_viewed", "trending"}
    for group in data.values():
        assert len(group) >= 6
        for item in group:
            assert item["url"] == f"https://www.youtube.com/watch?v={item['id']}"
            assert item["thumbnail"].startswith("https://i.ytimg.com/vi/")
            assert item["title"]
            assert item["channel"]
            assert item["views"]
            assert item["length"]

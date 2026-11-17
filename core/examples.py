"""Curated example videos shown on the landing page.

The lists are static, hand-picked samples: YouTube's live trending feed is not
publicly available without a signed-in session, so ReelRelay ships a small
selection instead. Each card simply fills the search box with a real link, and
the normal preview/download flow takes over from there.
"""

from __future__ import annotations

MOST_VIEWED = [
    {
        "id": "kJQP7kiw5Fk",
        "title": "Despacito",
        "channel": "Luis Fonsi",
        "views": "8.7B",
        "length": "4:42",
    },
    {
        "id": "JGwWNGJdvx8",
        "title": "Shape of You",
        "channel": "Ed Sheeran",
        "views": "6.6B",
        "length": "4:24",
    },
    {
        "id": "RgKAFK5djSk",
        "title": "See You Again",
        "channel": "Wiz Khalifa",
        "views": "6.6B",
        "length": "3:58",
    },
    {
        "id": "9bZkp7q19f0",
        "title": "GANGNAM STYLE",
        "channel": "officialpsy",
        "views": "5.5B",
        "length": "4:13",
    },
    {
        "id": "OPf0YbXqDm0",
        "title": "Uptown Funk",
        "channel": "Mark Ronson",
        "views": "5.4B",
        "length": "4:31",
    },
    {
        "id": "hT_nvWreIhg",
        "title": "Counting Stars",
        "channel": "OneRepublic",
        "views": "4.2B",
        "length": "4:44",
    },
    {
        "id": "CevxZvSJLk8",
        "title": "Roar",
        "channel": "Katy Perry",
        "views": "4.1B",
        "length": "4:30",
    },
    {
        "id": "09R8_2nJtjg",
        "title": "Sugar",
        "channel": "Maroon 5",
        "views": "4.0B",
        "length": "5:02",
    },
]

TRENDING_PICKS = [
    {
        "id": "ekr2nIex040",
        "title": "APT.",
        "channel": "ROSÉ & Bruno Mars",
        "views": "2.1B",
        "length": "2:53",
    },
    {
        "id": "kPa7bsKwL-c",
        "title": "Die With A Smile",
        "channel": "Lady Gaga, Bruno Mars",
        "views": "1.6B",
        "length": "4:12",
    },
    {
        "id": "0e3GPea1Tyg",
        "title": "$456,000 Squid Game In Real Life!",
        "channel": "MrBeast",
        "views": "700M",
        "length": "25:41",
    },
    {
        "id": "eVli-tstM5E",
        "title": "Espresso",
        "channel": "Sabrina Carpenter",
        "views": "1.0B",
        "length": "3:13",
    },
    {
        "id": "T6eK-2OQtew",
        "title": "Not Like Us",
        "channel": "Kendrick Lamar",
        "views": "560M",
        "length": "4:55",
    },
    {
        "id": "7wtfhZwyrcc",
        "title": "Believer",
        "channel": "Imagine Dragons",
        "views": "3.2B",
        "length": "3:37",
    },
    {
        "id": "DyDfgMOUjCI",
        "title": "bad guy",
        "channel": "Billie Eilish",
        "views": "1.5B",
        "length": "3:26",
    },
    {
        "id": "fJ9rUzIMcZQ",
        "title": "Bohemian Rhapsody",
        "channel": "Queen Official",
        "views": "2.1B",
        "length": "5:59",
    },
]


def _decorate(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            **item,
            "url": f"https://www.youtube.com/watch?v={item['id']}",
            "thumbnail": f"https://i.ytimg.com/vi/{item['id']}/mqdefault.jpg",
        }
        for item in items
    ]


def get_examples() -> dict[str, list[dict[str, str]]]:
    """Return the example lists with URLs and thumbnails filled in."""
    return {
        "most_viewed": _decorate(MOST_VIEWED),
        "trending": _decorate(TRENDING_PICKS),
    }

"""Static asset caching: versioned URLs instead of conditional 304 round trips."""

from __future__ import annotations

import mimetypes
import re

import pytest
from fastapi.testclient import TestClient

import main
from core import config


@pytest.fixture()
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_home_page_links_versioned_assets(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    for name in ("css/style.css", "js/main.js", "img/reelrelay.svg", "img/reelrelay.ico"):
        assert re.search(
            rf"/static/{re.escape(name)}\?v=[0-9a-f]+",
            response.text,
        ), name


def test_versioned_asset_is_immutable(client: TestClient) -> None:
    response = client.get(main.static_url("/js/main.js"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["content-type"].startswith("text/javascript")


def test_script_and_style_types_do_not_depend_on_the_host() -> None:
    # Windows reads .js from the registry, where it may be text/plain. With
    # nosniff that makes browsers block the file, so the app pins both types.
    assert mimetypes.types_map[".js"] == "text/javascript"
    assert mimetypes.types_map[".css"] == "text/css"


def test_unversioned_asset_still_revalidates(client: TestClient) -> None:
    response = client.get("/static/js/main.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_token_changes_when_the_content_changes(tmp_path, monkeypatch) -> None:
    asset = tmp_path / "static" / "css" / "style.css"
    asset.parent.mkdir(parents=True)
    asset.write_text("body { color: red; }", encoding="utf-8")
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    before = main.static_url("/css/style.css")
    assert before.startswith("/static/css/style.css?v=")

    # Same length, so a size-based or coarse-timestamp token would miss it.
    asset.write_text("body { color: tan; }", encoding="utf-8")
    after = main.static_url("/css/style.css")

    assert before != after


def test_missing_asset_falls_back_to_the_app_version(monkeypatch) -> None:
    monkeypatch.setattr(main, "BASE_DIR", main.BASE_DIR / "does-not-exist")

    assert main.static_url("/js/gone.js").endswith(f"?v={config.APP_VERSION}")

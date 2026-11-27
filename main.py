"""FastAPI application — ReelRelay.

Design goals
------------
* One preview for YouTube, Instagram and Facebook links.
* Rich preview with every available video quality and audio profile.
* Audio downloads carry artist / album / composer / year tags and cover art.
* Downloads are **streamed straight to the browser**.  Media is never written
  to disk storage, so nothing is left behind after the request ends.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yt_dlp
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from core import ai_lyrics, config
from core.download import DownloadPlan, build_plan, plan_preview, prepare_stream
from core.examples import get_examples
from core.progress import progress
from core.streaming import DownloadError
from core.utils import compact_number, content_disposition, human_duration, human_size
from core.ytdlp import (
    PLATFORM_LABELS,
    ExtractionError,
    UnavailableError,
    build_view,
    get_info,
    is_valid_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "%s %s starting — ffmpeg=%s, max height=%sp, concurrent downloads=%s",
        config.APP_NAME,
        config.APP_VERSION,
        config.FFMPEG or "MISSING",
        config.MAX_VIDEO_HEIGHT,
        config.MAX_CONCURRENT_DOWNLOADS,
    )
    if not config.FFMPEG_OK:
        logger.warning(
            "ffmpeg was not found. 8K merging and MP3/FLAC/M4A/WAV conversion are disabled. "
            "Install ffmpeg and restart to unlock every format."
        )
    yield


# Windows resolves MIME types through the registry, where `.js` is sometimes
# registered as `text/plain`. The app sends `X-Content-Type-Options: nosniff`,
# so a wrong type would make browsers refuse the stylesheet and script. Pin the
# types the app actually serves so behaviour does not depend on the host.
for _extension, _mime_type in {
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}.items():
    mimetypes.add_type(_mime_type, _extension)


app = FastAPI(
    title=config.APP_NAME,
    description=(
        "Inspect and stream video or tagged audio from YouTube, Instagram and "
        "Facebook straight to the browser. Nothing is stored on disk."
    ),
    version=config.APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["human_size"] = human_size
templates.env.filters["human_duration"] = human_duration
templates.env.filters["compact"] = compact_number


def _asset_token(path: str) -> str:
    """Short token that changes whenever the file on disk changes.

    Static files are cached for a year (see the middleware below), so the URL
    must change with the content or an edit would never reach the browser. The
    content is hashed rather than using its modification time: some filesystems
    only store whole seconds, and an edit that keeps the same size would then
    keep the old URL. The assets are small, so the hash is cheap.
    """
    file = BASE_DIR / "static" / path.lstrip("/")
    try:
        data = file.read_bytes()
    except OSError:
        return config.APP_VERSION
    return hashlib.blake2b(data, digest_size=8).hexdigest()


def static_url(path: str) -> str:
    """Versioned URL for a bundled asset, e.g. `/static/js/main.js?v=…`."""
    clean = "/" + path.lstrip("/")
    return f"/static{clean}?v={_asset_token(clean)}"


templates.env.globals["static_url"] = static_url

# --------------------------------------------------------------------------- #
# Download concurrency guard
# --------------------------------------------------------------------------- #

_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_DOWNLOADS)
_active_lock = threading.Lock()
_active_downloads = 0


def _acquire_slot() -> bool:
    global _active_downloads
    if not _slots.acquire(blocking=False):
        return False
    with _active_lock:
        _active_downloads += 1
    return True


def _release_slot() -> None:
    global _active_downloads
    with _active_lock:
        if _active_downloads > 0:
            _active_downloads -= 1
    try:
        _slots.release()
    except ValueError:  # pragma: no cover - over-release guard
        pass


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

    path = request.url.path
    if path.startswith("/static/"):
        if response.status_code in (200, 304):
            if "v" in request.query_params:
                # The URL changes with the file, so the browser never has to
                # revalidate it: no more conditional "304 Not Modified" round
                # trips, and no stale CSS or JavaScript after an edit.
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers.setdefault("Cache-Control", "no-cache")
    elif response.headers.get("content-type", "").startswith("text/html"):
        # Pages embed the versioned asset URLs above; caching the markup would
        # keep old tokens alive after an update.
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _base_context(request: Request) -> dict[str, Any]:
    return {
        "request": request,
        "app_name": config.APP_NAME,
        "app_tagline": config.APP_TAGLINE,
        "app_version": config.APP_VERSION,
        "ffmpeg_ok": config.FFMPEG_OK,
        "platforms": list(PLATFORM_LABELS.values()),
        "max_height": config.MAX_VIDEO_HEIGHT,
        "max_height_label": {2160: "4K", 1440: "2K", 4320: "8K"}.get(
            config.MAX_VIDEO_HEIGHT, f"{config.MAX_VIDEO_HEIGHT}p"
        ),
    }


def _error_response(
    request: Request,
    message: str,
    status_code: int = 400,
    *,
    hint: str | None = None,
) -> HTMLResponse:
    context = _base_context(request)
    context.update({"error": message, "hint": hint})
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context=context,
        status_code=status_code,
    )


def _collect_tags(
    title: str | None,
    artist: str | None,
    album: str | None,
    composer: str | None,
    genre: str | None,
    year: str | None,
    track: str | None,
    disc: str | None = None,
    copyright: str | None = None,
    publisher: str | None = None,
    isrc: str | None = None,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "title": title,
            "artist": artist,
            "album": album,
            "composer": composer,
            "genre": genre,
            "year": year,
            "track": track,
            "disc": disc,
            "copyright": copyright,
            "publisher": publisher,
            "isrc": isrc,
        }.items()
        if value
    }


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    context = _base_context(request)
    context["examples"] = get_examples()
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.post("/preview", response_class=HTMLResponse)
async def preview(request: Request, media_url: str = Form(...)):
    url = (media_url or "").strip()
    if not is_valid_url(url):
        return _error_response(
            request,
            "That does not look like a supported video link.",
            hint="Paste a YouTube, Instagram or Facebook video, Shorts or Reel link.",
        )

    try:
        info = await run_in_threadpool(get_info, url)
        view = await run_in_threadpool(build_view, info)
    except ExtractionError as exc:
        return _error_response(request, str(exc), status_code=422, hint="The link may be private, age-restricted, or removed.")
    except Exception:  # noqa: BLE001
        logger.exception("Preview failed for %s", url)
        return _error_response(
            request,
            "The preview could not be built. Please try again.",
            status_code=500,
        )

    context = _base_context(request)
    context.update({"media": view})
    return templates.TemplateResponse(request=request, name="preview.html", context=context)


# --------------------------------------------------------------------------- #
# Inline preview stream
# --------------------------------------------------------------------------- #


@app.get("/preview/stream")
async def preview_stream(request: Request, url: str = Query(..., min_length=3)):
    """Stream a small rendition straight to the inline ``<video>`` player.

    The stream is relayed from the source (or muxed live by ffmpeg) and is
    never written to disk. Previews share the download slot guard so a pile of
    open players cannot starve real downloads.
    """
    if not is_valid_url(url):
        return PlainTextResponse("Not a supported video URL.", status_code=400)

    if not _acquire_slot():
        return PlainTextResponse(
            "ReelRelay is busy right now. Wait a moment and press play again.",
            status_code=429,
            headers={"Retry-After": "5"},
        )

    plan: DownloadPlan | None = None
    try:
        info = await run_in_threadpool(get_info, url)
        plan = await run_in_threadpool(plan_preview, info)
        if plan is None:
            _release_slot()
            return PlainTextResponse(
                "This link has no inline-playable stream. Use Open original or download it instead.",
                status_code=404,
            )
        prepared = await run_in_threadpool(prepare_stream, plan)
    except (ExtractionError, UnavailableError, DownloadError) as exc:
        _release_slot()
        return PlainTextResponse(str(exc), status_code=422)
    except Exception:  # noqa: BLE001
        _release_slot()
        logger.exception("Preview stream failed for %s", url)
        return PlainTextResponse("The preview stream could not be started.", status_code=500)

    logger.info(
        "Previewing %s (%s) from %s — %s",
        plan.filename,
        plan.quality,
        plan.platform,
        plan.mode,
    )

    async def body():
        try:
            async for chunk in iterate_in_threadpool(prepared.chunks):
                yield chunk
        finally:
            try:
                prepared.close()
            finally:
                _release_slot()

    headers = {
        "Content-Disposition": content_disposition(plan.filename, inline=True),
        "Cache-Control": "no-store, no-transform",
        "X-Accel-Buffering": "no",
        "X-Preview-Quality": plan.quality,
        "X-Preview-Mode": plan.mode,
        # The relay starts at byte zero and plays forward; advertising ranges
        # would make players seek into a stream that cannot be resumed.
        "Accept-Ranges": "none",
    }
    if prepared.content_length:
        headers["Content-Length"] = str(prepared.content_length)

    return StreamingResponse(body(), media_type=plan.media_type, headers=headers)


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #


@app.get("/api/info")
async def api_info(request: Request, url: str = Query(..., min_length=3)):
    if not is_valid_url(url):
        return JSONResponse({"ok": False, "error": "Not a supported video URL."}, status_code=400)
    try:
        info = await run_in_threadpool(get_info, url)
        view = await run_in_threadpool(build_view, info)
    except (ExtractionError, UnavailableError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception:  # noqa: BLE001
        logger.exception("API info failed for %s", url)
        return JSONResponse({"ok": False, "error": "Unexpected server error."}, status_code=500)
    return JSONResponse({"ok": True, "data": view})


@app.get("/api/prepare")
async def api_prepare(
    request: Request,
    url: str = Query(..., min_length=3),
    kind: str = Query("video", pattern="^(video|audio)$"),
    token: str | None = Query(None, max_length=64),
    option: str | None = Query(None, max_length=80),
    height: int | None = Query(None, ge=144, le=4320),
    container: str | None = Query(None, pattern="^(mp4|webm|mkv|m4v)$"),
    profile: str | None = Query(None, max_length=24),
    format_id: str | None = Query(None, max_length=24),
    embed_tags: bool = Query(True),
    embed_cover: bool = Query(True),
    ai_lyrics: bool = Query(False),
    ai_tags: bool = Query(False),
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    composer: str | None = None,
    genre: str | None = None,
    year: str | None = None,
    track: str | None = None,
    disc: str | None = None,
    copyright: str | None = None,
    publisher: str | None = None,
    isrc: str | None = None,
):
    if not is_valid_url(url):
        return JSONResponse({"ok": False, "error": "Not a supported video URL."}, status_code=400)

    token = (token or "").strip()[:64]
    tags = _collect_tags(
        title, artist, album, composer, genre, year, track, disc, copyright, publisher, isrc
    )
    try:
        info = await run_in_threadpool(get_info, url)
        plan = await run_in_threadpool(
            build_plan,
            info,
            kind=kind,
            option_id=option,
            height=height,
            container=container,
            profile=profile,
            format_id=format_id,
            tags=tags,
            embed_tags=embed_tags,
            embed_cover=embed_cover,
            use_ai_lyrics=ai_lyrics,
            use_ai_tags=ai_tags,
        )
    except (ExtractionError, UnavailableError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception:  # noqa: BLE001
        logger.exception("Prepare failed for %s", url)
        return JSONResponse({"ok": False, "error": "Could not prepare this download."}, status_code=500)

    if token:
        progress.create(
            token,
            plan.filename,
            plan.content_length or plan.estimated_total,
            estimated=plan.content_length is None and plan.estimated_total is not None,
        )

    params = {
        "url": url,
        "kind": kind,
        "token": token,
        "option": option,
        "height": height,
        "container": container,
        "profile": profile,
        "format_id": format_id,
        "embed_tags": "1" if embed_tags else "0",
        "embed_cover": "1" if embed_cover else "0",
        "ai_lyrics": "1" if ai_lyrics else "0",
        "ai_tags": "1" if ai_tags else "0",
        **tags,
    }
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    # Keep this relative: works behind HTTPS reverse proxies without mixed content.
    download_url = f"/download?{query}"

    payload = {
        "ok": True,
        "plan": plan.summary(),
        "download_url": download_url,
        "token": token or None,
    }
    return JSONResponse(payload)


@app.get("/api/lyrics")
async def api_lyrics(
    request: Request,
    url: str = Query(..., min_length=3),
    ai: bool = Query(True),
    duration: int | None = Query(None, ge=0, le=86_400),
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    composer: str | None = None,
    genre: str | None = None,
    year: str | None = None,
    track: str | None = None,
    disc: str | None = None,
    copyright: str | None = None,
    publisher: str | None = None,
    isrc: str | None = None,
):
    """Resolve lyrics once for the AI checkbox and cache the outcome.

    Free sources are tried first. Groq is only called when ``ai=true`` and the
    description/LRCLIB have no lyrics. The response includes the text so the
    preview can show it, but the API key never leaves the server.
    """
    if not is_valid_url(url):
        return JSONResponse(
            {"ok": False, "error": "Not a supported video URL."}, status_code=400
        )

    tags = _collect_tags(
        title, artist, album, composer, genre, year, track, disc, copyright, publisher, isrc
    )
    try:
        info = await run_in_threadpool(get_info, url)
        effective = int(duration if duration is not None else info.get("duration") or 0)
        result, cached = await run_in_threadpool(
            ai_lyrics.resolve, info, tags, effective, use_ai=ai
        )
    except (ExtractionError, UnavailableError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception:  # noqa: BLE001
        logger.exception("Lyrics lookup failed for %s", url)
        return JSONResponse(
            {"ok": False, "error": "Could not look up lyrics for this link."},
            status_code=500,
        )

    return JSONResponse(
        {"ok": True, "lyrics": ai_lyrics.payload(result, cached=cached)}
    )


@app.get("/api/tags")
async def api_tags(
    request: Request,
    url: str = Query(..., min_length=3),
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    composer: str | None = None,
    genre: str | None = None,
    year: str | None = None,
    track: str | None = None,
    disc: str | None = None,
    copyright: str | None = None,
    publisher: str | None = None,
    isrc: str | None = None,
):
    """Suggest missing tags from facts stated in the description.

    The caller sends the values already in the tag editor; only empty fields
    are filled. Nothing is embedded here; ``/api/prepare`` and ``/download``
    apply the same suggestions when ``ai_tags=1``.
    """
    if not is_valid_url(url):
        return JSONResponse(
            {"ok": False, "error": "Not a supported video URL."}, status_code=400
        )

    tags = _collect_tags(
        title, artist, album, composer, genre, year, track, disc, copyright, publisher, isrc
    )
    try:
        info = await run_in_threadpool(get_info, url)
        suggestions, details, warnings = await run_in_threadpool(
            ai_lyrics.enrich_tags, info, tags
        )
    except (ExtractionError, UnavailableError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception:  # noqa: BLE001
        logger.exception("AI tag lookup failed for %s", url)
        return JSONResponse(
            {"ok": False, "error": "Could not read tags from this description."},
            status_code=500,
        )

    return JSONResponse(
        {
            "ok": True,
            "tags": suggestions,
            "details": details,
            "warnings": warnings,
        }
    )


# --------------------------------------------------------------------------- #
# Streaming download
# --------------------------------------------------------------------------- #


@app.get("/download")
async def download(
    request: Request,
    url: str = Query(..., min_length=3),
    kind: str = Query("video", pattern="^(video|audio)$"),
    token: str | None = Query(None, max_length=64),
    option: str | None = Query(None, max_length=80),
    height: int | None = Query(None, ge=144, le=4320),
    container: str | None = Query(None, pattern="^(mp4|webm|mkv|m4v)$"),
    profile: str | None = Query(None, max_length=24),
    format_id: str | None = Query(None, max_length=24),
    embed_tags: bool = Query(True),
    embed_cover: bool = Query(True),
    ai_lyrics: bool = Query(False),
    ai_tags: bool = Query(False),
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    composer: str | None = None,
    genre: str | None = None,
    year: str | None = None,
    track: str | None = None,
    disc: str | None = None,
    copyright: str | None = None,
    publisher: str | None = None,
    isrc: str | None = None,
):
    if not is_valid_url(url):
        return _error_response(request, "Not a supported video URL.")

    token = (token or "").strip()[:64]
    if token:
        # /api/prepare created this token while the user read the confirmation
        # dialog. Restart the clock now that the transfer is really starting.
        progress.create(token, restart=True)

    if not _acquire_slot():
        if token:
            progress.finish(token, "ReelRelay is busy. Wait a moment and try again.")
        return _error_response(
            request,
            "ReelRelay is already streaming the maximum number of downloads.",
            status_code=429,
            hint="Wait for a download to finish, then try again.",
        )

    plan: DownloadPlan | None = None
    try:
        tags = _collect_tags(
            title, artist, album, composer, genre, year, track, disc, copyright, publisher, isrc
        )
        info = await run_in_threadpool(get_info, url)
        plan = await run_in_threadpool(
            build_plan,
            info,
            kind=kind,
            option_id=option,
            height=height,
            container=container,
            profile=profile,
            format_id=format_id,
            tags=tags,
            embed_tags=embed_tags,
            embed_cover=embed_cover,
            use_ai_lyrics=ai_lyrics,
            use_ai_tags=ai_tags,
        )
        if token:
            progress.create(
                token,
                plan.filename,
                plan.content_length or plan.estimated_total,
                estimated=plan.content_length is None and plan.estimated_total is not None,
            )
        prepared = await run_in_threadpool(prepare_stream, plan)
    except (ExtractionError, UnavailableError, DownloadError) as exc:
        _release_slot()
        if token:
            progress.finish(token, str(exc))
        return _error_response(request, str(exc), status_code=422)
    except ValueError as exc:
        _release_slot()
        if token:
            progress.finish(token, str(exc))
        return _error_response(request, str(exc), status_code=400)
    except Exception:  # noqa: BLE001
        _release_slot()
        logger.exception("Download failed before streaming for %s", url)
        if token:
            progress.finish(token, "The download could not be started.")
        return _error_response(
            request,
            "The download could not be started. Please try another quality.",
            status_code=500,
        )

    logger.info(
        "Streaming %s (%s) from %s — %s",
        plan.filename,
        plan.quality,
        plan.platform,
        plan.mode,
    )

    stream_total = prepared.content_length or plan.content_length or plan.estimated_total
    stream_estimated = (
        prepared.content_length is None
        and plan.content_length is None
        and plan.estimated_total is not None
    )

    if token:
        progress.begin(token, stream_total, estimated=stream_estimated)

    async def body():
        sent = 0
        completed = False
        try:
            async for chunk in iterate_in_threadpool(prepared.chunks):
                yield chunk
                sent += len(chunk)
                if token:
                    progress.update(token, sent, stream_total, estimated=stream_estimated)
            completed = True
            if token:
                progress.finish(token)
        finally:
            if token and not completed:
                progress.finish(token, "The download was interrupted before it finished.")
            try:
                prepared.close()
            finally:
                _release_slot()

    headers = {
        "Content-Disposition": content_disposition(plan.filename),
        "Cache-Control": "no-store, no-transform",
        "X-Accel-Buffering": "no",
        "X-Download-Mode": plan.mode,
        "X-Download-Quality": plan.quality,
        "X-Download-Platform": plan.platform,
    }
    if prepared.content_length:
        # Only the origin's exact length is safe to advertise; ffmpeg output
        # (live mux / transcode) is chunked instead.
        headers["Content-Length"] = str(prepared.content_length)

    return StreamingResponse(
        body(),
        media_type=plan.media_type,
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Progress + health
# --------------------------------------------------------------------------- #


@app.get("/api/progress/{token}")
async def api_progress(token: str):
    snapshot = progress.get(token[:64])
    if snapshot is None:
        return JSONResponse(
            {"ok": False, "state": "unknown", "error": "This download is no longer being tracked."},
            status_code=404,
        )
    return JSONResponse(snapshot)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "yt_dlp": yt_dlp.version.__version__,
        "active_downloads": _active_downloads,
        "tracked_downloads": len(progress),
        "platforms": list(PLATFORM_LABELS.values()),
        "storage": "none — downloads are streamed to the client",
        **config.summary(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=False)

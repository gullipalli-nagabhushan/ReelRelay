# ReelRelay

**YouTube · Instagram · Facebook — straight to your device.**

ReelRelay previews a video link, lists every deliverable quality, and streams the selected
video or tagged audio **straight to the browser**. Media is never written to server storage.

Audio downloads include artist, album, composer, year, genre and cover-art metadata.
Available formats: original audio, MP3 320/192, FLAC, M4A (AAC 256) and WAV. Instagram and
Facebook links often expose only a progressive video file, so ReelRelay extracts audio from
that file when no standalone audio stream exists.

## Features

- **Up to 4K video** — every real stream the source exposes, bounded by `MAX_VIDEO_HEIGHT`
  (2160p by default).
  - H.264 / AV1 in MP4, VP9 in WebM, plus progressive fallbacks.
  - Video + audio are muxed live with ffmpeg when the platform serves them separately.
  - Portrait reels are labelled by their short edge (1080×1920 is shown as 1080p).
- **High-quality audio** — original stream (no re-encode), MP3 320/192, FLAC, AAC 256 and WAV.
- **Full metadata** — artist and song are derived from the title/channel (`- Topic` handling);
  composer is read from description credits when present. Every field is editable in the UI.
- **Cover art** — the best thumbnail is embedded for MP3 and FLAC.
- **Zero server storage** — direct streams are relayed with `httpx`; converted formats are
  piped from ffmpeg's stdout. The only optional temp file is a JPEG cover for FLAC, which is
  deleted as soon as the request ends.
- **Resilient delivery** — if the CDN drops a connection mid-body, the relay resumes from the
  exact byte offset with an HTTP `Range` request (up to 10 attempts).
- **Live progress** — the download panel shows elapsed time, bytes, percentage and speed, then
  a clear completion state. Downloads use a native browser download, so the tab never spins.
- **Tag editor, format picker**, responsive dark UI, friendly error pages.

## Requirements

- Python 3.11+ recommended (3.10 works, but yt-dlp prints a deprecation warning)
- **ffmpeg** on `PATH` (required for 4K merging and audio conversion)
  - Windows: `winget install Gyan.FFmpeg` or `choco install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`

Without ffmpeg the app still runs, but only single-file video streams and original audio
are available.

## Quick start

```bash
git clone <your-repo-url>
cd youtube-url-processor

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
# optional, for offline tests:
pip install -r requirements-dev.txt

uvicorn main:app --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>. On Windows with the bundled environment:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8080
```

## Configuration

Copy `.env.example` to `.env` (values are read from the process environment).

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_VIDEO_HEIGHT` | `2160` | Highest height offered (144–4320). |
| `MAX_CONCURRENT_DOWNLOADS` | `4` | Simultaneous streams; extra requests get `429`. |
| `INFO_CACHE_TTL` | `600` | Seconds a yt-dlp metadata result is reused. |
| `INFO_CACHE_SIZE` | `64` | Number of cached videos. |
| `YTDLP_COOKIES_FILE` | — | Cookies for age-restricted / sign-in media. Instagram and Facebook frequently need this. |
| `YTDLP_PROXY` | — | Proxy used by yt-dlp. |
| `FFMPEG_BINARY` | auto | Explicit ffmpeg path. |
| `FFPROBE_BINARY` | auto | Explicit ffprobe path. |

## How streaming works

1. `POST /preview` runs yt-dlp (metadata only) and renders every available quality.
2. A download click calls `GET /api/prepare`, which validates the choice and returns the
   filename, MIME type, a progress token and the final `/download` URL.
3. The browser starts a native download. `GET /download` opens one of two pipelines:
   - **relay** — `httpx` proxies the media URL, forwarding bytes as they arrive. Truncated
     connections are resumed with `Range: bytes=<sent>-`.
   - **ffmpeg** — ffmpeg reads the source stream(s) and writes the container/transcode to
     `pipe:1`; FastAPI forwards stdout to the client.
4. MP3 tags (including APIC cover art) are prepended by a small ID3v2.3 writer, because
   ffmpeg cannot write ID3 to a non-seekable pipe. FLAC/m4a/wav tags are written by ffmpeg.
5. The page polls `/api/progress/{token}` for elapsed time, bytes, percentage and speed. This
   state lives in memory only and is pruned automatically.

Nothing is saved to disk. Exact `Content-Length` is advertised for direct relays; live
ffmpeg output is chunked.

## API

The UI no longer links to these, but they remain available for automation.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Home page and URL form. |
| `POST` | `/preview` | Form field `media_url`; renders the preview page. |
| `GET` | `/api/info?url=…` | JSON metadata, video options and audio options. |
| `GET` | `/api/prepare` | Validate a download choice; returns `plan`, `token` + `download_url`. |
| `GET` | `/api/progress/{token}` | Live progress snapshot for one download. |
| `GET` | `/download` | Stream the selected media. |
| `GET` | `/health` | Service status, active downloads, storage mode. |

`/api/prepare` and `/download` accept:

```
url, kind=video|audio, token, option, height, container, profile, format_id,
embed_tags=0|1, embed_cover=0|1,
title, artist, album, composer, genre, year, track
```

## Tests

The test suite is fully offline (synthetic yt-dlp payloads and a fake HTTP origin):

```bash
pip install -r requirements-dev.txt
pytest
```

It covers multi-platform URL validation, metadata derivation, format discovery (including
progressive-only media), download planning, the progress registry, the ID3 writer, and
RelayStream range-resume behaviour.

## Docker

```bash
docker build -t reelrelay .
docker run --rm -p 8000:8000 reelrelay
```

The image installs ffmpeg and runs as a non-root user.

## Platform notes

- `Procfile` and `runtime.txt` are included for Heroku-style platforms.
- PythonAnywhere works with an ASGI/uvicorn setup, but streaming long downloads may hit
  platform time limits; a VPS or container host is a better fit.
- Media URLs expire after a few hours. If a download fails, reload the preview and try again.
- Instagram and Facebook often require cookies (`YTDLP_COOKIES_FILE`) for anything beyond
  public posts, and may only expose a progressive video file.
- M4A in a streamed MP4 container carries tags but not cover art; use MP3 or FLAC for
  artwork. WAV has no composer or cover-art slot.
- Age-restricted or members-only content requires `YTDLP_COOKIES_FILE`.

## Project layout

```
main.py              FastAPI routes, progress endpoint and streaming response
core/config.py       environment-driven settings
core/ytdlp.py        multi-platform extraction, metadata and format catalogs
core/download.py     download plans, ffmpeg commands, tag defaults
core/streaming.py    RelayStream (Range resume) + FFmpegStream
core/progress.py     in-memory download progress registry
core/id3.py          ID3v2.3 writer with APIC cover art
core/cover.py        thumbnail selection / fetching
core/utils.py        filenames, sizes, durations, RFC 6266 headers
templates/           Jinja2 pages
static/              CSS and JS
tests/               offline pytest suite
```

## Legal

Download only content you have the right to save. Respect each platform's Terms of Service
and copyright law.

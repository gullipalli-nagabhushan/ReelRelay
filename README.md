<div align="center">

# ReelRelay

**A local-first video and audio downloader for YouTube, Instagram and Facebook.**

Paste a link, preview every available quality, and stream the result straight to
your browser — up to **8K video**, or fully tagged audio with cover art.

[![CI](https://github.com/gullipalli-nagabhushan/ReelRelay/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gullipalli-nagabhushan/ReelRelay/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#requirements)

[Quick start](#quick-start) ·
[Architecture](#architecture) ·
[HTTP API](#http-api) ·
[Testing](#testing) ·
[Contributing](CONTRIBUTING.md) ·
[FAQ](#frequently-asked-questions) ·
[License](#license)

</div>

---

## Overview

ReelRelay is a FastAPI application that runs on your own machine. It uses
[yt-dlp](https://github.com/yt-dlp/yt-dlp) for metadata and format discovery,
[ffmpeg](https://ffmpeg.org/) for muxing and transcoding, and `httpx` for
byte-exact relay of direct media streams.

The application never stores media on disk. A download is either relayed from
the platform's CDN or piped from ffmpeg's stdout directly into the HTTP
response. The only lasting file is the ordinary browser download chosen by the
user.

**Version:** 3.0.0 · **Status:** actively maintained · **License:** MIT

<p align="center">
  <img src="docs/screenshot.png" alt="ReelRelay landing page showing the search box, 8K badge and example videos" width="100%">
</p>

## Features

| Capability | Details |
| --- | --- |
| Multi-platform | YouTube (videos, Shorts, Music), Instagram (reels, posts, IGTV), Facebook (videos, reels, watch) |
| Up to 8K | Every real stream the source exposes, bounded by `MAX_VIDEO_HEIGHT` (4320p by default) |
| Video codecs | H.264, H.265, AV1 and VP9; MP4/WebM containers; HDR and high-frame-rate detection |
| Audio formats | Original stream, MP3 320/192, FLAC, M4A (AAC 256), WAV |
| Metadata | Title, artist, album, composer, genre, year and track, editable before download |
| Cover art | Best thumbnail embedded as ID3 `APIC` (MP3) or FLAC picture |
| Inline preview | Play the video in the page before downloading; a small progressive rendition is relayed to a native `<video>` player |
| Android app | Standalone Kotlin app in `android/` with bundled yt-dlp + ffmpeg, parallel high-resolution downloads, inline ExoPlayer preview and MediaStore downloads |
| Zero storage | Relay or ffmpeg pipe; no media cache and no app-managed downloads folder |
| Fast high-res | Parallel-range accelerator feeds ffmpeg over up to 12 connections, staying in memory |
| Resilient relay | Resumes truncated CDN connections with HTTP `Range` requests (bounded retries) |
| Live progress | Elapsed time, bytes, percentage, speed, ETA and stall detection |
| Local by default | Binds to `127.0.0.1`; no accounts, telemetry, analytics or cloud storage |
| One-click start | Windows launcher and desktop-shortcut creator, with automatic dependency setup |
| Offline tests | 116 tests with synthetic yt-dlp payloads and a fake HTTP origin |

## Tech stack

| Layer | Choice |
| --- | --- |
| Web framework | FastAPI + Uvicorn |
| Templates | Jinja2 |
| Frontend | Bootstrap 5, Bootstrap Icons, vanilla JavaScript (no build step) |
| Extraction | yt-dlp |
| HTTP client | httpx |
| Media processing | ffmpeg / ffprobe |
| Configuration | Environment variables, optional `python-dotenv` |
| Testing | pytest (fully offline) |

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        UI["Preview UI"]
    end

    subgraph App["FastAPI process"]
        PREVIEW["POST /preview"]
        PREPARE["GET /api/prepare"]
        DOWNLOAD["GET /download"]
        PROGRESS["GET /api/progress/{token}"]
        CACHE[("Info cache")]
        REG[("Progress registry")]
        SEM["Concurrency semaphore"]
    end

    YTDLP["yt-dlp"]
    RELAY["httpx relay"]
    FFMPEG["ffmpeg pipe"]
    SOURCE[("YouTube / Instagram / Facebook")]

    UI -->|"media_url"| PREVIEW --> YTDLP --> SOURCE
    PREVIEW --> CACHE
    UI --> PREPARE --> YTDLP
    PREPARE -->|"plan + token + download_url"| UI
    UI -->|"native download"| DOWNLOAD --> SEM
    DOWNLOAD -->|"progressive"| RELAY --> SOURCE
    DOWNLOAD -->|"merge / convert"| FFMPEG --> SOURCE
    DOWNLOAD --> REG
    UI -.->|"poll every second"| PROGRESS --> REG
```

### Request lifecycle

1. **Preview** — `POST /preview` validates the URL, runs yt-dlp in a threadpool,
   caches the metadata, and renders every deliverable video and audio option.
2. **Inline preview** — pressing play calls `/preview/stream`, which relays a
   small (usually 360p) progressive rendition to the native `<video>` element.
   The stream is never written to disk and shares the download slot guard.
3. **Prepare** — clicking Download calls `/api/prepare`. This builds a
   `DownloadPlan` (filename, MIME type, stream URLs, ffmpeg arguments, tags) and
   returns a progress token plus the final `/download` URL. Planning is cheap
   and side-effect free, so invalid choices fail before the browser starts
   receiving bytes.
4. **Download** — `/download` acquires a concurrency slot and opens exactly one
   pipeline:
   - **relay** for a single file that already contains video and audio;
   - **ffmpeg** for merging separate video/audio streams or transcoding audio.
5. **Progress** — the preview page polls `/api/progress/{token}`. The registry is
   in memory, bounded to 512 entries, and pruned after an hour.
6. **Cleanup** — the response ends, the slot is released, and no media file ever
   existed on disk.

### Module map

| Path | Responsibility |
| --- | --- |
| `main.py` | App factory, routes, template context, security headers, concurrency gate |
| `core/config.py` | Environment configuration, ffmpeg discovery, cookie-file validation |
| `core/ytdlp.py` | URL validation, extraction, metadata normalization, format catalogs, friendly errors |
| `core/download.py` | `DownloadPlan`, `build_plan()`, `prepare_stream()`, tagging rules |
| `core/streaming.py` | `RelayStream` (range resume) and `FFmpegStream` (pipe) |
| `core/accelerator.py` | Parallel-range localhost proxy for high-resolution muxing |
| `core/progress.py` | Thread-safe, bounded progress registry |
| `core/cover.py` | Thumbnail download and JPEG normalization |
| `core/id3.py` | Dependency-free ID3v2.3 writer for non-seekable MP3 pipes |
| `core/utils.py` | Human-readable sizes, durations, filenames and text cleanup |
| `core/examples.py` | Curated landing-page examples |
| `templates/` | Jinja2 pages: home, preview, error, base layout |
| `static/` | CSS, JavaScript and icons |
| `tests/` | Offline pytest suite |

### Design principles

- **Stream, do not store.** Every media byte travels from the origin to the
  browser through the response iterator. Temporary files are limited to
  short-lived cover art for FLAC tagging.
- **Plan, then execute.** A download is validated before a response starts, so
  errors surface as friendly HTML instead of a broken partial file.
- **Local first.** The app binds to `127.0.0.1` and has no authentication by
  design. Public hosting is explicitly out of scope.
- **Boring dependencies.** yt-dlp absorbs platform churn; FastAPI and httpx
  handle transport; ffmpeg handles media. There is no frontend build system.
- **Errors are a feature.** Platform failures are translated into short,
  actionable messages instead of raw extractor tracebacks.

## Project structure

```text
.
├── main.py                     # FastAPI application and routes
├── core/
│   ├── accelerator.py          # Parallel-range download proxy
│   ├── config.py               # Environment + binary discovery
│   ├── cover.py                # Thumbnail → JPEG
│   ├── download.py             # Plan building and execution
│   ├── examples.py             # Curated landing-page videos
│   ├── id3.py                  # ID3v2.3 writer
│   ├── progress.py             # In-memory progress registry
│   ├── streaming.py            # Relay and ffmpeg pipelines
│   ├── utils.py                # Formatting and filename helpers
│   └── ytdlp.py                # Extraction, validation, format catalogs
├── templates/                  # Jinja2 pages
├── static/                     # CSS, JavaScript, icons
├── docs/                       # README screenshots
├── android/                    # Standalone Kotlin app (bundled yt-dlp + ffmpeg)
├── tests/                      # Offline pytest suite
├── .github/                    # CI, Dependabot, issue and PR templates
├── run-reelrelay.bat / .ps1    # Windows launcher
├── create-desktop-shortcut.*   # Windows shortcut creator
├── health_check.py             # CLI health probe
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Runtime + test dependencies
├── .env.example                # Documented configuration
├── CONTRIBUTING.md
├── SECURITY.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
└── LICENSE
```

## Quick start

### Windows: one click

1. Double-click `create-desktop-shortcut.bat`.
2. Double-click the **ReelRelay** icon on your desktop.
3. The launcher creates or rebuilds `.venv`, installs dependencies, starts the
   server, and opens the browser.

Keep the console window open while you use the app. Close it (or press
`Ctrl+C`) to stop ReelRelay.

### Manual setup: Windows

```powershell
git clone https://github.com/gullipalli-nagabhushan/ReelRelay.git
cd ReelRelay

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8080
```

### Manual setup: macOS / Linux

```bash
git clone https://github.com/gullipalli-nagabhushan/ReelRelay.git
cd ReelRelay

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>.

### Verify the installation

```bash
curl http://127.0.0.1:8080/health
```

```powershell
.\.venv\Scripts\python.exe health_check.py
```

A healthy response includes `"status": "healthy"`, the yt-dlp version, the
configured maximum height, and whether ffmpeg and cookies are available.

### Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.11+ | Required by current yt-dlp releases. CI covers 3.11 through 3.15 on Ubuntu and Windows. |
| ffmpeg | Required for 4K/8K merging, MP3/FLAC/M4A/WAV conversion and cover embedding. Without it, only progressive video and original audio are offered. |
| Internet | Needed for extraction and download; the test suite is offline. |
| Windows 10/11 | Required only for the `.bat` / `.ps1` launchers. The app runs anywhere Python does. |

Install ffmpeg:

```powershell
winget install Gyan.FFmpeg        # Windows
```

```bash
brew install ffmpeg               # macOS
sudo apt install ffmpeg           # Debian / Ubuntu
```

## Configuration

Copy `.env.example` to `.env` and uncomment the settings you need. All values
are optional; `python-dotenv` loads `.env` automatically at startup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_VIDEO_HEIGHT` | `4320` | Highest video height offered, 144–4320. Set `2160` for 4K-only. |
| `MAX_CONCURRENT_DOWNLOADS` | `4` | Simultaneous streams. Extra requests receive `429`. |
| `PARALLEL_DOWNLOADS` | `true` | Fetch high-resolution streams over several connections. |
| `PARALLEL_CONNECTIONS` | `12` | Range connections per stream (1-32). |
| `PARALLEL_SEGMENT_MB` | `4` | Size of each ranged segment in MiB (1-32). |
| `PARALLEL_WINDOW_MB` | `32` | In-memory look-ahead window per stream (8-512 MiB); at least one segment per connection |
| `PARALLEL_MAX_CONNECTIONS` | `32` | Process-wide cap across every active stream (1-128). |
| `INFO_CACHE_TTL` | `600` | Seconds a yt-dlp metadata result is reused. |
| `INFO_CACHE_SIZE` | `64` | Maximum cached videos. |
| `YTDLP_COOKIES_FILE` | — | Path to a Netscape `cookies.txt` for your own account. |
| `YTDLP_PROXY` | — | Proxy used by yt-dlp, e.g. `http://user:pass@host:8080`. |
| `FFMPEG_BINARY` | auto | Explicit ffmpeg path. |
| `FFPROBE_BINARY` | auto | Explicit ffprobe path. |

### Cookies

Most public media needs no cookies. `YTDLP_COOKIES_FILE` exists only for media
that requires **your own** account: age-restricted videos, private posts, or
Instagram/Facebook content behind a sign-in wall.

```ini
YTDLP_COOKIES_FILE=C:\Users\you\Downloads\cookies.txt
```

> A Netscape cookie file is a login token. Keep it on your machine, never commit
> it, never attach it to an issue, and use a throwaway account when possible.
> `.env` is git-ignored for this reason.

The file is validated at startup. A missing path logs a warning and is ignored
rather than crashing the app.

## HTTP API

The web UI uses the same endpoints. They are intentionally unauthenticated
because the app is expected to run on loopback.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Landing page. |
| `POST` | `/preview` | Form field `media_url`; renders the preview page. |
| `GET` | `/preview/stream?url=…` | Small inline-playable rendition for the `<video>` player. |
| `GET` | `/api/info?url=…` | JSON metadata and all video/audio options. |
| `GET` | `/api/prepare` | Validate a download choice; returns a plan, token and URL. |
| `GET` | `/api/progress/{token}` | Snapshot for one download. |
| `GET` | `/download` | Stream the selected media. |
| `GET` | `/health` | Status, versions, ffmpeg and cookie state. |

### Inspect a link

```bash
curl --get "http://127.0.0.1:8080/api/info" \
  --data-urlencode "url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

The response contains `ok`, `data.video_options`, `data.audio_options`,
normalized metadata, and capability flags such as `has_4k`, `has_hdr`,
`has_60fps` and `ffmpeg_ok`.

### Prepare a download

```bash
curl --get "http://127.0.0.1:8080/api/prepare" \
  --data-urlencode "url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --data-urlencode "kind=video" \
  --data-urlencode "option=v1080-h264-mp4" \
  --data-urlencode "token=demo-token"
```

Example response:

```json
{
  "ok": true,
  "plan": {
    "filename": "Artist - Title [1080p].mp4",
    "media_type": "video/mp4",
    "quality": "1080p",
    "mode": "video",
    "size_label": "size varies",
    "notes": ["Video and audio are muxed live while streaming — the first bytes take a few seconds."]
  },
  "download_url": "/download?url=...&kind=video&option=v1080-h264-mp4&token=demo-token",
  "token": "demo-token"
}
```

### Download parameters

`/api/prepare` and `/download` accept:

| Parameter | Values | Notes |
| --- | --- | --- |
| `url` | platform URL | Required. |
| `kind` | `video` \| `audio` | Default `video`. |
| `option` | option id | Preferred selection; use the id returned by `/api/info`. |
| `height` | 144–4320 | Fallback selection when no `option` is given. |
| `container` | `mp4` \| `webm` \| `mkv` \| `m4v` | Video container override. |
| `profile` | e.g. `mp3_320`, `flac`, `wav`, `original` | Audio profile. |
| `format_id` | yt-dlp format id | Explicit audio stream selection. |
| `embed_tags` | `true` \| `false` | Audio tags, default `true`. |
| `embed_cover` | `true` \| `false` | Cover art, default `true`. |
| `token` | up to 64 chars | Enables progress tracking. |
| `title`, `artist`, `album`, `composer`, `genre`, `year`, `track` | text | Override derived metadata. |

### Progress snapshots

```json
{
  "ok": true,
  "token": "demo-token",
  "state": "streaming",
  "sent": 184549376,
  "total": 734003200,
  "percent": 25.1,
  "elapsed": 42.3,
  "speed": 4369066,
  "eta": 126.0,
  "stalled": false
}
```

States are `preparing`, `streaming`, `done` and `error`. When the total is an
estimate, `total_is_estimate` is `true` and the percentage is capped below 100
until the response actually completes.

## Download pipeline

### Relay mode

Used for progressive files (video + audio in one stream), Instagram/Facebook
media, and the "Original stream" audio option. `httpx` opens the CDN URL with
the extractor's headers and forwards 64 KiB chunks. If the connection drops
mid-body, `RelayStream` reopens the URL with `Range: bytes=<sent>-` and
continues, up to 10 times. `Content-Length` is advertised when the origin
provides it.

### ffmpeg mode

Used for:

- merging separate video and audio streams (typically 1080p and above);
- converting audio to MP3, FLAC, M4A or WAV;
- copying the audio track out of a progressive video.

ffmpeg reads the source URL(s) with reconnect flags and writes to `pipe:1`.
Because the output is non-seekable, MP3 tags are prepended by `core/id3.py`
instead of ffmpeg. FLAC cover art uses a short-lived JPEG temp file that is
deleted immediately after the request.

### Concurrency and caching

- A `threading.BoundedSemaphore` limits active downloads to
  `MAX_CONCURRENT_DOWNLOADS`; excess requests get HTTP `429`.
- High-resolution muxing goes through `core/accelerator.py`: a pool of
  `PARALLEL_CONNECTIONS` workers fetches 4 MiB `Range` segments into a
  `PARALLEL_WINDOW_MB` memory window, and ffmpeg reads the result from a
  token-guarded `127.0.0.1` URL. `PARALLEL_MAX_CONNECTIONS` caps the whole
  process, and the proxy shuts down with the stream. Nothing touches the disk.
- yt-dlp extraction runs in Starlette's threadpool so the event loop stays
  responsive.
- Metadata is cached in a small thread-safe LRU-style cache with a TTL.
- Progress is in memory only and per process. Run **one** worker: multiple
  Uvicorn workers would split the cache, progress registry and semaphore.

## Android app

`android/` contains a standalone Kotlin app for Android 7.0+ (API 24+) that
does not need a PC. It bundles yt-dlp's Python runtime and ffmpeg through
[youtubedl-android](https://github.com/yausername/youtubedl-android), so
extraction, merging and audio conversion all run on the phone.

- Paste a link or share one from the YouTube, Instagram or Facebook app.
- The app shows the thumbnail, title, duration and an inline ExoPlayer preview
  of the smallest progressive stream.
- The format picker mirrors the desktop catalogue: the same heights, up to three
  codec families per height (H.264, H.265, AV1, VP9), fps/HDR/Portrait badges,
  estimated sizes, a starred suggestion, and the five audio profiles (MP3 320,
  MP3 192, M4A 256, FLAC, WAV). Instagram/Facebook, which usually expose a
  single progressive file, get their audio extracted from that file.
- Instagram and Facebook frequently require a logged-in session for private
  Reels and Videos. Import a `cookies.txt` exported from a logged-in browser
  (for example with the *Get cookies.txt* extension); the app keeps it in
  private storage and passes it to yt-dlp for both extraction and download.
  Failures caused by login requirements, rate limits or region locks are
  explained in plain language instead of a raw yt-dlp traceback.
- Paste a YouTube playlist link (including `watch?v=…&list=…`) to queue it. The
  first few entries are probed to offer the heights the playlist really has,
  then one video quality or audio profile is applied to every entry. Files
  download one at a time — extraction, muxing and audio conversion never run
  concurrently — and a failing entry is skipped and reported while the rest
  continue. The first 100 videos of a playlist are queued.
- High-resolution tracks are fetched over 12 parallel `Range` connections, the
  same trick the desktop app uses, then remuxed on the phone by the bundled
  ffmpeg. If a stream cannot be split, yt-dlp handles it automatically.
- Finished files are published through MediaStore to `Downloads/ReelRelay`
  (MediaStore on Android 10+, public Downloads plus a permission prompt below
  that).

Build it locally with JDK 17 or newer and the Android SDK:

```bash
cd android
./gradlew testDebugUnitTest assembleDebug
# android/app/build/outputs/apk/debug/app-debug.apk
```

The first build downloads Gradle 8.14.3 and the Android dependencies, so allow
a few minutes. The APK stays around 60 MB because it targets `arm64-v8a` by
default — the architecture of every current phone — while still bundling the
Python runtime, yt-dlp and ffmpeg. Add more ABIs only if you need them:

```bash
./gradlew assembleDebug -Preelrelay.abis=arm64-v8a,armeabi-v7a
./gradlew assembleDebug -Preelrelay.abis=arm64-v8a,armeabi-v7a,x86,x86_64  # ~200 MB
```

The app ships its own current yt-dlp at `android/app/src/main/res/raw/ytdlp`
(overriding the older copy inside youtubedl-android), because YouTube rejects
stale extractors with `HTTP 403` on high-resolution formats. When you refresh
that file, also bump `BUNDLED_YTDLP_VERSION` in `ReelRelayApp.kt` so existing
installs replace the extracted copy on the next launch.

CI builds the default arm64 APK on every push and uploads it as the
`reelrelay-android-debug` artifact. Release signing is intentionally not
configured: sideload the debug APK, or add your own keystore for a release
build.

## Testing

Install the development dependencies and run the suite:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests are completely offline:

- synthetic yt-dlp payloads in `tests/conftest.py`;
- a fake HTTP origin for relay and range-resume tests;
- monkeypatched ffmpeg availability, so no local binary is required.

Coverage areas include URL validation and platform detection, metadata
derivation, format discovery, progressive-only media, download planning, tag
handling, the ID3 writer, progress snapshots, and `RelayStream` resume logic.

CI runs the same suite on Ubuntu and Windows with Python 3.11 through 3.15,
byte-compiles `main.py` and `core/`, and validates the PowerShell launchers on
Windows.

### Adding tests

- Add a regression test for every bug fix.
- Keep new tests offline — never call a live platform or CDN.
- Prefer the shared fixtures in `tests/conftest.py`.
- For ffmpeg-dependent code, monkeypatch `config.FFMPEG_OK` and
  `config.FFMPEG` rather than requiring ffmpeg.

## Development workflow

```bash
git switch -c feat/my-change
# edit code and tests
python -m pytest -q
git commit -m "feat: describe the change"
```

Run with auto-reload while developing:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8080
```

Guidelines:

- Python 3.11+, type hints on public functions, four-space indentation.
- Keep the plan/execute split: validate in `build_plan()`, stream in
  `prepare_stream()`.
- Never write media to disk and never log cookies or authenticated URLs.
- Keep platform-specific logic in `core/ytdlp.py`; keep the UI free of build
  tooling.
- Update `.env.example` and this README when adding a setting.
- Update `CHANGELOG.md` for user-visible changes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide, commit conventions
and pull-request checklist.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Python was not found` | Python missing from `PATH` | Install Python 3.11+, tick **Add Python to PATH**, reopen the terminal. |
| Launcher closes immediately | Startup error | Run `run-reelrelay.bat` from a terminal to see the traceback. |
| Browser does not open | Port busy or slow start | Open `http://127.0.0.1:8080` manually; the launcher prints the chosen port. |
| `ffmpeg was not found` | Binary missing | Install ffmpeg and restart the app. |
| `Failed building wheel for pydantic-core` / `httptools` | A free-threaded (`3.13t`) or unsupported Python build has no prebuilt wheels | Install a standard Python 3.11–3.15 build, delete `.venv`, and start the launcher again. |
| `Sign in to confirm you're not a bot` | IP reputation / platform challenge | Update yt-dlp first. Public media may work later; private media needs your own cookie file. |
| `Media link expired` | CDN URL TTL | Reload the preview and download again. |
| Download stalls at 0% | ffmpeg preparing output | Wait for the first byte; the UI detects stalls. Try a lower quality if it persists. |
| 429 response | Concurrency limit reached | Wait for a download to finish or raise `MAX_CONCURRENT_DOWNLOADS`. |
| 4K/8K download slower than expected | CDN per-connection throttle | Keep `PARALLEL_DOWNLOADS=true` and raise `PARALLEL_CONNECTIONS` (up to 32). |
| Instagram/Facebook public post fails | Extractor or IP blocking | Update yt-dlp; retry later; a proxy may help. |
| Progress jumps back to "check your downloads" | Instance restarted or token expired | The download may still complete in the browser; check your downloads folder. |
| Antivirus flags the launcher | Unsigned PowerShell script | Allow the script for this folder; it only starts the local app. |

Capture a diagnostic snapshot while the app is running:

```bash
python health_check.py
```

## Privacy and security

- **No telemetry.** No analytics, accounts, crash reporting or cloud storage.
- **No media retention.** Files are streamed to the browser; the app keeps no
  copy.
- **Loopback binding.** The server binds to `127.0.0.1`. Do not expose it to the
  internet without authentication and TLS.
- **Cookies are optional.** A cookie file is read only from
  `YTDLP_COOKIES_FILE`, never logged, and never written into the repository.
- **Credential hygiene.** `.env` and cookie files are git-ignored. Use a
  throwaway account for authenticated tests.
- **Dependency updates.** Dependabot watches pip and GitHub Actions; yt-dlp
  should be updated regularly because platforms change often.

Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/gullipalli-nagabhushan/ReelRelay/security/advisories/new).
See [SECURITY.md](SECURITY.md) for scope, response expectations and a hardening
checklist.

### Public hosting

ReelRelay is **not** designed as a public service. It has no authentication,
no per-user isolation, no persistent job queue, and a datacenter IP is likely to
be challenged by the platforms. If you self-host anyway, put it behind a
reverse proxy with HTTPS and authentication, restrict access by firewall, and
run one worker.

## Frequently asked questions

### Do I need an account or a subscription?

No. ReelRelay is free, local and has no sign-up. A platform account is only
relevant for media that requires one, such as age-restricted or private posts.

### What is the highest quality I can download?

Up to 4320p (8K) when the source offers it. A 1080p upload cannot become 8K.
High resolutions usually need ffmpeg to merge separate video and audio streams.

### Where do my files go?

Wherever your browser saves downloads, normally your **Downloads** folder.
ReelRelay itself writes no media anywhere.

### Why does it say "sign in required" or "age-restricted"?

The media is private, age-gated or members-only. If it is your own account,
export a Netscape `cookies.txt` and set `YTDLP_COOKIES_FILE`. If it belongs to
someone else and is private, there is no legitimate workaround.

### It says ffmpeg is missing. What now?

Install it with `winget install Gyan.FFmpeg` (Windows), `brew install ffmpeg`
(macOS), or `sudo apt install ffmpeg` (Debian/Ubuntu), then restart ReelRelay.
Single-file video and original audio still work without ffmpeg.

### Can I download just the audio?

Yes. Choose MP3, FLAC, M4A, WAV or the untouched original stream. MP3 and FLAC
provide the most reliable cover art.

### Is downloading videos legal?

It depends on your jurisdiction and the content. Save only material you own,
material under a permissive licence, or material you have permission to
download. Respect copyright law and each platform's terms. You are responsible
for your use of the tool.

### How do I update yt-dlp?

```bash
python -m pip install -U yt-dlp
```

Then restart the app. If a previously working link fails, update yt-dlp first.

### How do I uninstall it?

Delete the repository folder and the desktop shortcut. Nothing is installed
system-wide. Browser downloads are separate files and are not touched.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request.

Good first issues:

- add a synthetic test for an uncovered extractor shape;
- improve a user-facing error message;
- document a platform quirk;
- add a test for a new audio profile or container.

Please keep the local-first, zero-storage design intact. Pull requests that
write media to disk, add telemetry, or expose the app publicly by default will
not be merged.

## License

Released under the [MIT License](LICENSE).

Copyright © 2026 Gullipalli Nagabhushan.

## Acknowledgements

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — extraction and format discovery.
- [FastAPI](https://fastapi.tiangolo.com/) and [Starlette](https://www.starlette.io/) — web framework and streaming responses.
- [httpx](https://www.python-httpx.org/) — relay transport.
- [ffmpeg](https://ffmpeg.org/) — muxing, transcoding and cover art.
- [Bootstrap](https://getbootstrap.com/) and [Bootstrap Icons](https://icons.getbootstrap.com/) — UI.
- [pytest](https://pytest.org/) — offline test suite.

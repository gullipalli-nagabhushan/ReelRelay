# Changelog

All notable changes to ReelRelay are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Lyrics and complete audio identifiers in the web app. Audio downloads now
  embed the video description's lyrics block, or a title/artist/duration-matched
  plain-text result from LRCLIB for music links, in MP3 (`USLT`), FLAC
  (`LYRICS`) and M4A (`©lyr`). MP3 files also gain the missing `COMM` source
  comment. Copyright, publisher, ISRC, disc number and track/disc totals are
  extracted from the source and written to every format that supports them,
  exposed in the preview tag editor, and accepted as `/api/prepare` and
  `/download` overrides. WAV keeps its existing basic RIFF tags and skips lyrics.
- Desktop-parity format catalogue in the Android app. The picker now shows up to
  three codec families per height (H.264, H.265, AV1, VP9) with fps, HDR and
  Portrait badges, estimated file sizes, a starred 1080p H.264 suggestion and
  the five desktop audio profiles (MP3 320, MP3 192, M4A 256, FLAC, WAV).
  Two-line rows replace the plain label list.
- Cookie import in the Android app: pick a browser-exported `cookies.txt` and it
  is copied into private storage and passed to yt-dlp, which unlocks
  login-only Instagram Reels and Facebook videos.
- YouTube playlist downloads in the Android app. A playlist link is listed
  without downloading, briefly probed for its real heights, and then every
  entry is fetched one by one with the chosen video quality or audio profile.
  Failed entries are skipped and reported, the download button shows the queue
  size, and the first 100 videos are queued.
- Audio tags and cover art in the Android app. Audio files now carry the video
  title, the channel as artist/album artist, the source page as comment, and —
  for MP3, M4A and FLAC — the thumbnail as embedded cover art, matching the
  desktop app. WAV keeps the tags without a cover block, and a cover that cannot
  be decoded never discards the audio.
- Platform `Origin`, `Referer`, `Accept-Language` and `Cookie` headers are sent
  for direct Instagram/Facebook CDN downloads, matching the desktop app.
- Human-readable Android errors for private posts, login requirements, rate
  limits, age restrictions, region locks, deleted posts and live streams instead
  of raw yt-dlp output.
- Parallel-range download accelerator. High-resolution streams that ffmpeg
  must mux are fetched over up to 12 HTTP `Range` connections into a bounded
  in-memory window and exposed to ffmpeg through a localhost URL. On a 4K AV1
  test stream this raised throughput from ~0.36 MiB/s (one connection) to
  multiple MiB/s without writing media to disk. Tune it with
  `PARALLEL_DOWNLOADS`, `PARALLEL_CONNECTIONS`, `PARALLEL_SEGMENT_MB`,
  `PARALLEL_WINDOW_MB` and `PARALLEL_MAX_CONNECTIONS`.
- Inline preview player on the preview page. `GET /preview/stream` relays a
  small (360p where available) browser-playable rendition, preferring a
  progressive stream, to a native `<video>` element, with a graceful
  "open original" fallback when a link exposes no playable stream.
- Standalone Android app under `android/`. It bundles yt-dlp's Python runtime
  and ffmpeg, previews with ExoPlayer, downloads or extracts audio on the
  device, publishes to `Downloads/ReelRelay` through MediaStore, and accepts
  links shared from other apps.
- Android parallel downloads. Direct video/audio streams are fetched with 12
  ranged connections and remuxed by the bundled ffmpeg, matching the desktop
  accelerator; anything that cannot be split falls back to yt-dlp.
- ABI selection for the Android build. The APK targets `arm64-v8a` by default
  (about 60 MB with Python and ffmpeg still bundled); pass
  `-Preelrelay.abis=...` to build additional architectures.
- Android CI job that runs the app's JVM unit tests and uploads a debug APK
  artifact.
- MIT license.
- Contributor guide, security policy, code of conduct and changelog.
- GitHub issue forms, pull-request template, Dependabot config and CI workflow.
- EditorConfig rules for consistent formatting across editors.

### Changed

- Minimum supported Python is now 3.11, which current yt-dlp releases require.
  The Windows launcher rebuilds a `.venv` that was created with Python 3.10.
- CI matrix now covers Python 3.11 through 3.15.
- `uvicorn` extras are listed explicitly instead of via `uvicorn[standard]`,
  because its `pyyaml` dependency has no wheels for Python 3.15 and needed a
  C compiler to install.
- README rewritten for developers: architecture, request lifecycle, API,
  testing, contribution workflow and troubleshooting.

### Fixed

- Web-app static assets no longer rely on conditional requests that show up as
  `304 Not Modified`. `style.css`, `main.js` and the icons are referenced with a
  token derived from each file's content, served with
  `Cache-Control: public, max-age=31536000, immutable`, and the HTML that carries
  those URLs is `no-store`. The browser fetches each file once, an edit gets a
  fresh URL, and a stale cached sheet or script can no longer hide a UI change.
  CSS, JavaScript and the other bundled asset types are also registered
  explicitly, because Windows resolves `.js` through the registry and can call it
  `text/plain`; with `nosniff` that would make browsers reject the script.
- Android progress no longer sweeps back and forth or completes several times
  per video. A monotonic progress mapping treats separate video and audio
  streams as slices of one bar, retries no longer subtract downloaded bytes from
  the percentage, and a yt-dlp fallback resumes from where the accelerated
  attempt stopped. The bar is determinate from the first frame, "Saving" is a
  99% step, and the screen stays awake while downloading.
- Instagram and Facebook downloads on Android: progressive files are now
  downloadable when a link has no separate audio stream, the audio profiles are
  extracted from the video track, and the CDN gets the `Referer`/`Origin`
  headers it requires.
- Android downloads no longer fail with `HTTP Error 403: Forbidden` on
  high-resolution YouTube formats. The app now overrides youtubedl-android's
  stale yt-dlp (2025.11.12) with the current release (2026.10.26), and replaces
  the extracted copy whenever `BUNDLED_YTDLP_VERSION` changes.
- Windows launcher no longer stops on a fresh install when Python writes to
  stderr while the launcher checks dependencies.
- The launcher skips free-threaded Python builds (`3.13t`), which have no
  prebuilt wheels for `pydantic-core`, `httptools`, `watchfiles` or `pyyaml`,
  and rebuilds an existing `.venv` that uses one.

## [3.0.0] - 2026-11-17

### Added

- One-click Windows launchers (`run-reelrelay.bat` / `.ps1`) and a desktop
  shortcut creator.
- 8K (4320p) support, now the default `MAX_VIDEO_HEIGHT`.
- Curated example videos on the landing page: all-time most viewed and
  trending picks.
- Plain-language FAQ in the app and in the README.
- Branded application icons for the shortcut and browser tab.

### Changed

- Cookie support is local-only: `YTDLP_COOKIES_FILE` is the single supported
  mechanism. Serverless base64/raw cookie environment variables were removed.
- The app binds to `127.0.0.1` by default.

### Removed

- Deployment-only artifacts: Dockerfile, Procfile, `runtime.txt`, Vercel
  configuration and the `deploy/` kit.

## Earlier versions

ReelRelay 1.x and 2.x were internal iterations and were never tagged as public
releases.

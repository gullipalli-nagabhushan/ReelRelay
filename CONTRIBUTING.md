# Contributing to ReelRelay

Thanks for helping improve ReelRelay. This guide covers local setup, the test
suite, coding conventions and the pull-request process.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Search [existing issues](https://github.com/gullipalli-nagabhushan/ReelRelay/issues)
  before opening a new one.
- For large changes, open an issue first so the design can be discussed.
- Report security problems privately using [SECURITY.md](SECURITY.md), never in
  a public issue.
- Never paste cookies, session tokens or private media URLs into an issue or
  pull request.

## Development setup

### Windows (PowerShell)

```powershell
git clone https://github.com/gullipalli-nagabhushan/ReelRelay.git
cd ReelRelay

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8080
```

### macOS / Linux

```bash
git clone https://github.com/gullipalli-nagabhushan/ReelRelay.git
cd ReelRelay

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>. Install ffmpeg to test 4K/8K merging and converted
audio:

- Windows: `winget install Gyan.FFmpeg`
- macOS: `brew install ffmpeg`
- Debian/Ubuntu: `sudo apt install ffmpeg`

## Tests

Run the whole suite:

```bash
python -m pytest -q
```

Guidelines:

- Tests must be **offline**. Use the synthetic payloads in
  `tests/conftest.py` and the fake HTTP origin in `tests/test_streaming.py`.
- Do not add tests that call YouTube, Instagram, Facebook or any live CDN.
- Add a regression test for every bug fix where practical.
- Prefer small, focused tests over end-to-end tests that need ffmpeg.
- Monkeypatch `config.FFMPEG_OK` / `config.FFMPEG` instead of requiring a local
  ffmpeg binary.
- CI runs the suite on Ubuntu and Windows with Python 3.11 through 3.15.

The current suite covers URL validation, metadata derivation, format discovery,
download planning, inline preview selection, progress tracking, the ID3 writer,
and resumable relay behaviour. The Android app has its own JVM unit tests for
format and preview selection (`android/app/src/test`).

## Project conventions

- Target Python 3.11+ and keep `from __future__ import annotations` in modules
  that use modern type syntax.
- Type hints are expected on public functions.
- Keep the app streaming: do **not** write downloaded media to disk. Temporary
  files are allowed only for short-lived metadata work such as a FLAC cover.
- Bind to `127.0.0.1` by default. Do not add an option that silently exposes the
  app to the network.
- User-facing errors should be short, actionable and free of stack traces.
  Log the technical detail separately.
- Keep dependencies minimal. A new runtime dependency needs a clear reason in
  the pull request.
- Match the existing style: four-space indentation, double quotes, f-strings,
  `snake_case` and descriptive names.
- Update `.env.example` and the README configuration table when adding a
  setting.
- Do not commit `.env`, `cookies.txt`, generated downloads or editor state.

### Adding or changing a platform

Platform URL parsing and labels live in `core/ytdlp.py`:

- `PLATFORM_LABELS`
- `is_valid_url` / `resolve_source`
- platform-specific error messages

Add validation cases to `tests/test_platforms.py` and a synthetic payload to
`tests/conftest.py` when the extractor shape differs.

### Adding an audio profile

Audio profiles live in `AUDIO_PROFILES` in `core/ytdlp.py`. Update the catalog
tests in `tests/test_formats.py` and, if the profile needs special handling,
the plan builder in `core/download.py`.

### Changing the UI

Templates are Jinja2 files under `templates/`; styles and browser code are under
`static/`. The UI must remain usable without a build step, and every new control
should work with keyboard navigation. The inline player is a plain
`<video preload="none">` fed by `/preview/stream`; keep it keyboard-accessible
and provide the existing fallback when a link has no playable stream.

### Android app

The standalone app lives in `android/` and is ordinary Kotlin/Gradle:

- UI, player and share-sheet handling: `MainActivity.kt`
- extraction/download state: `MainViewModel.kt`
- yt-dlp execution and MediaStore publishing: `DownloadRepository.kt`
- format and preview selection: `Formats.kt`
- JVM unit tests: `android/app/src/test/`

Build with JDK 17 or newer and an installed Android SDK:

```bash
cd android
./gradlew testDebugUnitTest assembleDebug
```

Do not commit `android/local.properties`, `.gradle/` or build outputs; CI builds
the debug APK on every push and uploads it as an artifact. When changing format
selection, extend `FormatsTest.kt` so the behaviour is covered without a
device.

## Commit messages

Use short, imperative subjects. [Conventional Commits](https://www.conventionalcommits.org/)
style is welcome but not mandatory:

```text
feat: add 8K quality labels
fix: resume relay after a truncated body
docs: explain local cookie setup
test: cover missing cookie file
chore: update yt-dlp
```

Group related changes into one commit where possible. Separate refactors from
behaviour changes.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make the change and add tests.
3. Run `python -m pytest -q`.
4. Update the README, docstrings or CHANGELOG when behaviour changes.
5. Open a pull request and fill in the template.

The pull-request checklist:

- [ ] Tests pass locally.
- [ ] New behaviour has tests.
- [ ] Docs are updated.
- [ ] No secrets, cookies or `.env` content are included.
- [ ] Windows and Linux behaviour is considered.
- [ ] Media is still streamed rather than written to disk.

A maintainer may ask for changes or close a pull request that conflicts with
the local-first, zero-storage design.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).

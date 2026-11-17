# Changelog

All notable changes to ReelRelay are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- MIT license.
- Contributor guide, security policy, code of conduct and changelog.
- GitHub issue forms, pull-request template, Dependabot config and CI workflow.
- EditorConfig rules for consistent formatting across editors.

### Changed

- README rewritten for developers: architecture, request lifecycle, API,
  testing, contribution workflow and troubleshooting.

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

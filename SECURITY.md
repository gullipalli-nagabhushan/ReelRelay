# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 3.x (latest `main`) | Yes |
| Older versions | No |

ReelRelay is a local-first application. The security model assumes the app is
bound to loopback (`127.0.0.1`) and used by one person on their own machine.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private reporting flow:

<https://github.com/gullipalli-nagabhushan/ReelRelay/security/advisories/new>

Please include:

- ReelRelay version and commit (if you are on `main`).
- Operating system and Python version.
- Steps to reproduce, with cookies and personal data removed.
- Impact: what an attacker could read, change or download.
- Any suggested fix or mitigation.

You can expect an acknowledgement as soon as possible. This is a
maintainer-run project without a paid support contract, so there is no formal
response SLA. Please allow time for a fix before public disclosure.

## What is in scope

- Path traversal, header injection, or arbitrary file write/read.
- Server-side request forgery through URL handling, redirects, or headers.
- Command injection into the ffmpeg/ffprobe arguments.
- XSS in the Jinja templates or the progress UI.
- Denial of service that defeats the concurrency or size limits.
- Leakage of cookies, tokens or downloaded media.

## What is not in scope

- The app being reachable when deliberately exposed to the public internet
  without authentication. It is designed for loopback use only.
- Platform-side restrictions, rate limits, account bans or DRM.
- Reports that require access to someone else's account or cookies.
- Vulnerabilities in yt-dlp, ffmpeg, FastAPI or other upstream dependencies
  that have no exploit path in ReelRelay. Please report those upstream.

## Cookie and credential handling

- A `cookies.txt` file is a login credential. It is read from
  `YTDLP_COOKIES_FILE` and never copied into the repository, logs or download
  filenames.
- `.env` is git-ignored. Never attach a cookie file to an issue or pull request.
- Use a throwaway account when testing authenticated media.
- Delete or rotate the cookie file when you no longer need it.

## Hardening checklist for self-hosters

If you must run ReelRelay beyond `127.0.0.1`:

1. Put it behind a reverse proxy with HTTPS and authentication.
2. Restrict access with a firewall or private network.
3. Lower `MAX_CONCURRENT_DOWNLOADS` to match the machine.
4. Keep `yt-dlp`, FastAPI, Uvicorn and httpx up to date.
5. Run as a dedicated, unprivileged user with no access to unrelated files.
6. Do not mount a personal cookie file you would not want exposed.

Public hosting is not a supported mode. See the README for details.

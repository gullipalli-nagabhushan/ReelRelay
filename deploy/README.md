# Deploying ReelRelay 3.0.0

A production setup for Oracle Cloud Always Free — and for any other Ubuntu 22.04 /
24.04 VPS (Hetzner, DigitalOcean, Contabo, a home server). Everything here is
already in this folder; the installer wires it together.

```
Internet ── 443 ──▶ Caddy            TLS, HTTP/2, streaming proxy (no buffering)
                      │ 127.0.0.1:8080
                      ▼
                 ReelRelay           uvicorn, exactly one worker
                      │ yt-dlp + ffmpeg
                      ▼
             YouTube / Instagram / Facebook
```

Media is relayed straight through the process — nothing is written to disk, so a
small boot volume is enough and there is nothing to clean up.

| File | Purpose |
| --- | --- |
| `install.sh` | One-shot installer for Ubuntu: packages, Docker, Caddy, firewall, service |
| `docker-compose.yml` | ReelRelay container, bound to `127.0.0.1:8080`, config mounted read-only |
| `Caddyfile` | HTTPS site with buffering disabled for live progress |
| `reelrelay.env.example` | Runtime settings copied to `/etc/reelrelay/reelrelay.env` |
| `reelrelay.service` | systemd unit used by `SERVICE=systemd` (native, no Docker) |

---

## 1. Create the server

**Oracle Cloud → Compute → Instances → Create instance**

| Field | Value |
| --- | --- |
| Image | Canonical **Ubuntu 24.04** (aarch64 for Ampere, x86_64 for AMD) |
| Shape | **VM.Standard.A1.Flex** (Always Free: up to 4 OCPU / 24 GB) |
| OCPU / memory | 2 OCPU / 12 GB is a good start; 4 / 24 for heavy 4K use |
| Boot volume | 50 GB (default) is plenty — no media is stored |
| Public IPv4 | **Assign a public IPv4 address** (ephemeral is fine) |
| SSH keys | Upload or generate, and keep the private key |

If you get *"Out of host capacity"*, try another availability domain, retry later,
or drop to 2 OCPU / 12 GB. The AMD `VM.Standard.E2.1.Micro` (1/8 OCPU, 1 GB) also
works for 720p and audio if you set `MAX_CONCURRENT_DOWNLOADS=1` and add swap:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Always Free includes 10 TB/month of outbound traffic, which is the number that
matters for a downloader.

## 2. Open the ports in the VCN

The cloud firewall is separate from the host firewall. In the Oracle console:

**Networking → Virtual Cloud Networks → your VCN → Security Lists → Default Security List
→ Add Ingress Rules**

| Stateless | Source | IP protocol | Destination port |
| --- | --- | --- | --- |
| No | `0.0.0.0/0` | TCP | `80` |
| No | `0.0.0.0/0` | TCP | `443` |
| No | `::/0` | TCP | `80` (only if the VCN has IPv6) |
| No | `::/0` | TCP | `443` (only if the VCN has IPv6) |

Port 80 must stay open: Let's Encrypt uses it for HTTP-01 validation and Caddy
redirects plain HTTP to HTTPS with it.

Oracle's Ubuntu images also ship host `iptables` rules that reject 80/443. The
installer fixes that; if you install by hand, see
[Troubleshooting](#troubleshooting).

## 3. Point a domain at it

Create an **A record** for e.g. `reel.example.com` pointing at the instance's
*public* IPv4 (not the `10.x` private address).

- **Cloudflare**: DNS → Add record → `A`, proxy status **DNS only** (grey cloud)
  while Caddy obtains its certificate; you can turn the orange cloud on later if
  you set SSL/TLS mode to **Full (strict)**.
- **DuckDNS**: `reelrelay.duckdns.org`, then set the `current ip` field. DuckDNS
  supports Let's Encrypt normally.
- No domain? Caddy can serve plain HTTP on the IP, but then edit `Caddyfile` to
  `http://__DOMAIN__` and drop automatic HTTPS. A free DuckDNS subdomain is easier.

Verify before installing:

```bash
dig +short reel.example.com
```

## 4. Copy the app to the server

From your machine (PowerShell, Windows):

```powershell
tar --exclude=.venv --exclude=.git --exclude=__pycache__ -czf reelrelay.tgz .
scp -i "$HOME\.ssh\oracle.key" reelrelay.tgz ubuntu@<server-ip>:~/
ssh -i "$HOME\.ssh\oracle.key" ubuntu@<server-ip> "mkdir -p ~/reelrelay && tar -xzf ~/reelrelay.tgz -C ~/reelrelay"
```

Or clone it on the server if you push to a Git remote:

```bash
git clone <your-repo-url> ~/reelrelay
```

## 5. Install

```bash
cd ~/reelrelay
sudo DOMAIN=reel.example.com bash deploy/install.sh
```

Optional variables:

```bash
sudo DOMAIN=reel.example.com EMAIL=you@example.com bash deploy/install.sh
sudo DOMAIN=reel.example.com OPEN_FIREWALL=0 bash deploy/install.sh     # leave iptables alone
sudo DOMAIN=reel.example.com SERVICE=systemd bash deploy/install.sh     # no Docker
```

The installer:

1. checks that `DOMAIN` resolves to this server (warns, does not abort),
2. installs `ffmpeg`, Python, Docker Engine, Compose and Caddy,
3. creates `/etc/reelrelay/` (755) and `/etc/reelrelay/reelrelay.env` (640),
4. writes a Caddy site for your domain and validates it,
5. builds and starts the container (or the systemd service),
6. allows 80/443 in `iptables` and saves it with `netfilter-persistent`,
7. waits for `http://127.0.0.1:8080/health` and prints the result.

It is idempotent: rerun it after a `git pull` or after adding cookies.

## 6. Cookies (Instagram, Facebook, age-restricted YouTube)

Only needed when a platform asks for a sign-in. Use a **throwaway account** —
cookies are equivalent to that account's password.

1. In a desktop browser, install **Get cookies.txt LOCALLY** (Chrome / Firefox / Edge).
2. Sign in to the platform and open the page you want to download from.
3. Export the site's cookies in **Netscape** format as `cookies.txt`.
4. Copy it up:

   ```powershell
   scp -i "$HOME\.ssh\oracle.key" cookies.txt ubuntu@<server-ip>:~/
   ssh -i "$HOME\.ssh\oracle.key" ubuntu@<server-ip> \
     "sudo install -o 10001 -g 10001 -m 0640 ~/cookies.txt /etc/reelrelay/cookies.txt && rm ~/cookies.txt"
   ```

5. Add the path to `/etc/reelrelay/reelrelay.env`:

   ```bash
   sudo sed -i 's|^# *YTDLP_COOKIES_FILE=.*|YTDLP_COOKIES_FILE=/etc/reelrelay/cookies.txt|' /etc/reelrelay/reelrelay.env
   ```

   (or uncomment it with `sudo nano`). If you already placed the file and then run
   the installer, it adds this line for you.

6. Restart: `cd ~/reelrelay/deploy && sudo docker compose up -d` (or
   `sudo systemctl restart reelrelay` in native mode).

The container runs as uid/gid `10001`, so the file must stay owned by `10001` and
be group-readable — that is what the `install` command above does. In native
`systemd` mode the owner is `reelrelay:reelrelay` instead.

Confirm with `curl -s http://127.0.0.1:8080/health` → `"cookies_configured": true`.

## 7. Verify

```bash
curl -s http://127.0.0.1:8080/health
curl -I https://reel.example.com
```

Then open the site, paste a link, pick a quality, and confirm:

- the preview lists video and audio formats,
- the download overlay shows percent, size, speed, elapsed and remaining time,
- the file lands in your browser's downloads (not on the server).

If HTTPS fails immediately after install, give Caddy 10–60 seconds to obtain the
certificate, then check `sudo journalctl -u caddy -n 50 --no-pager`.

---

## Configuration

Edit `/etc/reelrelay/reelrelay.env`, then restart the service. Defaults are safe;
the ones worth tuning:

```ini
MAX_VIDEO_HEIGHT=2160
MAX_CONCURRENT_DOWNLOADS=3
INFO_CACHE_TTL=600
INFO_CACHE_SIZE=64
# YTDLP_COOKIES_FILE=/etc/reelrelay/cookies.txt
# YTDLP_PROXY=http://user:pass@proxy.example.com:8080
```

| Server | `MAX_CONCURRENT_DOWNLOADS` |
| --- | --- |
| 1 GB / 1 vCPU (AMD micro) | `1` |
| 2–4 GB RAM | `2` |
| 8 GB+ / A1.Flex | `3–4` |

Every stream is CPU-bound while muxing or transcoding, so raising this on a small
instance makes all downloads slower rather than faster.

**Keep exactly one app instance and one uvicorn worker.** The progress registry,
metadata cache and concurrency semaphore are in memory; a second worker or a
load-balanced second instance breaks the progress display and the limits.

## Updating

```bash
cd ~/reelrelay
git pull
sudo DOMAIN=reel.example.com bash deploy/install.sh
```

Shorter, if nothing about the system setup changed:

```bash
cd ~/reelrelay && git pull
cd deploy && sudo docker compose up -d --build
```

Rebuild at least monthly — yt-dlp is pinned by minimum version and platforms
change constantly. Optional weekly refresh as root:

```cron
0 4 * * 1 cd /home/ubuntu/reelrelay && git pull -q && cd deploy && docker compose up -d --build >/dev/null 2>&1
```

## Native systemd instead of Docker

```bash
sudo DOMAIN=reel.example.com SERVICE=systemd bash deploy/install.sh
```

That copies the app to `/opt/reelrelay`, creates `.venv`, installs
`reelrelay.service`, and starts it. Logs: `sudo journalctl -u reelrelay -f`.
Restart: `sudo systemctl restart reelrelay`.

The unit runs as `reelrelay`, listens on `127.0.0.1:8080`, trusts proxy headers
from Caddy only, and is hardened with `ProtectSystem=strict`, `PrivateTmp=yes`
and a writable `StateDirectory`. Note that systemd cannot parse multiline values:
in native mode always use `YTDLP_COOKIES_FILE`, never `YTDLP_COOKIES` or
`YTDLP_COOKIES_B64`.

## Optional: password protection

The site has no accounts by design. If you want a single shared password, add
basic auth in Caddy:

```bash
caddy hash-password --plaintext 'choose-a-password'
```

`sudo nano /etc/caddy/Caddyfile`, inside the site block:

```
basic_auth {
	me <paste-the-hash>
}
```

(`basicauth` on Caddy < 2.8), then `sudo systemctl reload caddy`. Downloads keep
streaming normally because the browser reuses the credentials.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| HTTPS times out | VCN ingress 80/443 **and** host firewall: `sudo iptables -L INPUT -n \| head -20`. Fix: `sudo iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo netfilter-persistent save` |
| `502 Bad Gateway` | App not running: `sudo docker compose -f ~/reelrelay/deploy/docker-compose.yml ps`, `sudo ss -ltnp \| grep 8080` |
| Certificate errors | DNS mismatch (`dig +short <domain>`), port 80 blocked, or rate limits: `sudo journalctl -u caddy -n 80 --no-pager` |
| `Sign in to confirm you're not a bot` | Add cookies, update yt-dlp (`git pull && docker compose up -d --build`), or use `YTDLP_PROXY` |
| Instagram/Facebook "login required" | Missing or expired cookies; re-export from a logged-in throwaway account |
| 4K download fails near the end | ffmpeg missing in `/health`, or RAM exhausted — lower `MAX_CONCURRENT_DOWNLOADS`, add swap |
| Very slow transfers | Datacenter IP throttling by YouTube; a residential `YTDLP_PROXY` helps more than a bigger server |
| `429` in the browser | Concurrency limit reached; wait or raise it on a larger shape |
| Progress jumps or resets | More than one worker/instance running |
| Cookies suddenly ignored | Ownership/mode changed, or the account was logged out: `sudo ls -l /etc/reelrelay/cookies.txt` |
| Disk filling up | Should not happen (no media on disk); check `sudo journalctl --disk-usage` and `df -h` |

Useful commands:

```bash
sudo docker compose -f ~/reelrelay/deploy/docker-compose.yml logs -f --tail=100
sudo journalctl -u caddy -f
curl -s http://127.0.0.1:8080/health
```

## Notes and limits

- **Zero storage.** Streams are proxied; `/` and `/health` are the only public app
  endpoints (`/docs` is disabled).
- **One machine.** Scaling horizontally requires shared progress/cache state, which
  this app does not have — scale vertically (more CPU) instead.
- **Bandwidth is the real cost.** A 4K hour is roughly 7–15 GB of egress. Oracle
  Always Free includes 10 TB/month.
- **Datacenter IPs are throttled.** YouTube often serves VPS IPs at a fraction of
  residential speed; cookies do not change that.
- **Legality and platform terms** are your responsibility — see the main README.

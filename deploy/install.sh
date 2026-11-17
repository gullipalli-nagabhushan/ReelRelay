#!/usr/bin/env bash
#
# ReelRelay 3.0.0 installer for Ubuntu 22.04 / 24.04
# (Oracle Cloud Always Free, Hetzner, any fresh VPS).
#
#   DOMAIN=reel.example.com sudo -E bash deploy/install.sh
#   DOMAIN=reel.example.com SERVICE=systemd sudo -E bash deploy/install.sh
#
# Environment:
#   DOMAIN          required, must already resolve to this server
#   SERVICE         docker (default) or systemd-native
#   OPEN_FIREWALL   1 (default) inserts iptables rules for 80/443, 0 skips
#   EMAIL           optional ACME account email for Let's Encrypt
#
set -euo pipefail

DOMAIN="${DOMAIN:-}"
SERVICE="${SERVICE:-docker}"
OPEN_FIREWALL="${OPEN_FIREWALL:-1}"
EMAIL="${EMAIL:-}"

CONF_DIR=/etc/reelrelay
ENV_FILE="$CONF_DIR/reelrelay.env"
COOKIE_FILE="$CONF_DIR/cookies.txt"
APP_USER=reelrelay
APP_PATH=/opt/reelrelay

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "run with sudo: DOMAIN=$DOMAIN sudo -E bash deploy/install.sh"
[ -n "$DOMAIN" ] || die "DOMAIN is required, e.g. DOMAIN=reel.example.com sudo -E bash deploy/install.sh"
[[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
    || die "DOMAIN '$DOMAIN' does not look like a bare hostname (no https://, no path)"
case "$SERVICE" in
    docker|systemd) ;;
    *) die "SERVICE must be 'docker' or 'systemd'" ;;
esac

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=$(dirname -- "$SCRIPT_DIR")
[ -f "$APP_DIR/Dockerfile" ] || die "cannot find the ReelRelay checkout next to $SCRIPT_DIR"

# Service account for native mode (Docker uses uid 10001 from the image).
if [ "$SERVICE" = "systemd" ]; then
    getent group "$APP_USER" >/dev/null 2>&1 || groupadd --system "$APP_USER"
    id -u "$APP_USER" >/dev/null 2>&1 \
        || useradd --system --gid "$APP_USER" --home-dir "$APP_PATH" --shell /usr/sbin/nologin "$APP_USER"
fi

step "Checking DNS for $DOMAIN"
PUBLIC_IP=$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
RESOLVED_IP=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)
if [ -n "$PUBLIC_IP" ] && [ -n "$RESOLVED_IP" ] && [ "$PUBLIC_IP" != "$RESOLVED_IP" ]; then
    printf '\033[33mwarning:\033[0m %s resolves to %s but this server is %s\n' \
        "$DOMAIN" "$RESOLVED_IP" "$PUBLIC_IP"
    note "HTTPS will fail until the A record points at this server."
    note "Oracle: use the instance's public IPv4, not the private 10.x address."
elif [ -z "$RESOLVED_IP" ]; then
    printf '\033[33mwarning:\033[0m %s does not resolve yet\n' "$DOMAIN"
else
    note "$DOMAIN -> $RESOLVED_IP (matches this server)"
fi

step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | debconf-set-selections
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    ca-certificates curl gnupg rsync ffmpeg \
    python3 python3-venv python3-pip \
    iptables iptables-persistent >/dev/null

if [ "$SERVICE" = "docker" ]; then
    step "Installing Docker Engine"
    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi
    systemctl enable --now docker
    if ! docker compose version >/dev/null 2>&1; then
        apt-get install -y -qq docker-compose-plugin >/dev/null
    fi
    note "$(docker --version)"
fi

step "Installing Caddy (automatic HTTPS)"
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
        /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy >/dev/null
fi

step "Preparing $CONF_DIR"
install -d -m 0755 "$CONF_DIR"
if [ ! -f "$ENV_FILE" ]; then
    install -m 0640 "$SCRIPT_DIR/reelrelay.env.example" "$ENV_FILE"
    note "created $ENV_FILE — tune MAX_CONCURRENT_DOWNLOADS for this server"
else
    note "keeping existing $ENV_FILE"
fi
chown root:root "$ENV_FILE"

if [ -f "$COOKIE_FILE" ]; then
    if [ "$SERVICE" = "docker" ]; then
        # The container runs as uid/gid 10001, so it must be able to read it.
        chown 10001:10001 "$COOKIE_FILE"
    else
        chown "$APP_USER:$APP_USER" "$COOKIE_FILE"
    fi
    chmod 0640 "$COOKIE_FILE"
    if ! grep -q '^YTDLP_COOKIES_FILE=' "$ENV_FILE"; then
        printf 'YTDLP_COOKIES_FILE=%s\n' "$COOKIE_FILE" >> "$ENV_FILE"
    fi
    note "cookies: $COOKIE_FILE is in place"
else
    note "cookies: none yet (optional — see deploy/README.md)"
fi

step "Configuring Caddy for $DOMAIN"
{
    if [ -n "$EMAIL" ]; then
        printf '{\n\temail %s\n}\n\n' "$EMAIL"
    fi
    sed "s|__DOMAIN__|$DOMAIN|g" "$SCRIPT_DIR/Caddyfile"
} > /etc/caddy/Caddyfile
chown root:root /etc/caddy/Caddyfile
chmod 0644 /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null \
    || die "generated Caddyfile failed validation"
systemctl enable caddy >/dev/null 2>&1
systemctl reload caddy 2>/dev/null || systemctl restart caddy

if [ "$SERVICE" = "docker" ]; then
    step "Building and starting ReelRelay (Docker)"
    cd "$APP_DIR/deploy"
    docker compose up -d --build
else
    step "Installing ReelRelay into $APP_PATH (systemd)"
    install -d -m 0755 -o "$APP_USER" -g "$APP_USER" "$APP_PATH"
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude 'venv' --exclude 'env' \
        --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.env' \
        --exclude 'downloads' \
        "$APP_DIR"/ "$APP_PATH"/
    python3 -m venv "$APP_PATH/.venv"
    "$APP_PATH/.venv/bin/pip" install --upgrade pip >/dev/null
    "$APP_PATH/.venv/bin/pip" install --no-cache-dir -r "$APP_PATH/requirements.txt" >/dev/null
    chown -R "$APP_USER:$APP_USER" "$APP_PATH"
    install -m 0644 "$SCRIPT_DIR/reelrelay.service" /etc/systemd/system/reelrelay.service
    systemctl daemon-reload
    systemctl enable reelrelay >/dev/null 2>&1
    systemctl restart reelrelay
fi

if [ "$OPEN_FIREWALL" = "1" ]; then
    step "Allowing 80/443 in the host firewall"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        ufw allow 80/tcp >/dev/null
        ufw allow 443/tcp >/dev/null
        note "ufw: allowed 80/tcp and 443/tcp"
    fi
    # Oracle Ubuntu images ship REJECT rules that drop 80/443. Inserting at the
    # top of INPUT keeps working even after those rules.
    if ! iptables -C INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT 2>/dev/null; then
        iptables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT
    fi
    if command -v ip6tables >/dev/null 2>&1; then
        if ! ip6tables -C INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT 2>/dev/null; then
            ip6tables -I INPUT 1 -p tcp -m multiport --dports 80,443 -j ACCEPT || true
        fi
    fi
    netfilter-persistent save >/dev/null 2>&1 || true
    note "iptables + netfilter-persistent saved"
    note "The Oracle VCN Security List must also allow TCP 80 and 443 — see deploy/README.md"
else
    step "Skipping host firewall changes (OPEN_FIREWALL=0)"
fi

step "Waiting for ReelRelay on 127.0.0.1:8080"
READY=0
for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 2
done
if [ "$READY" = "1" ]; then
    note "app is answering: $(curl -fsS --max-time 5 http://127.0.0.1:8080/health)"
else
    printf '\033[33mwarning:\033[0m the app did not answer within 120s\n'
    if [ "$SERVICE" = "docker" ]; then
        note "inspect: cd $APP_DIR/deploy && docker compose logs --tail=50"
    else
        note "inspect: journalctl -u reelrelay -n 50 --no-pager"
    fi
fi

cat <<EOF

  ReelRelay is installed.

    URL              https://$DOMAIN
    Config           $ENV_FILE
    Cookies          $COOKIE_FILE (optional)
    Service          $([ "$SERVICE" = "docker" ] && echo "docker compose (deploy/docker-compose.yml)" || echo "systemd unit reelrelay.service")
    Logs             $([ "$SERVICE" = "docker" ] && echo "cd $APP_DIR/deploy && docker compose logs -f" || echo "journalctl -u reelrelay -f")
    Restart          $([ "$SERVICE" = "docker" ] && echo "cd $APP_DIR/deploy && docker compose restart" || echo "systemctl restart reelrelay")

  Let's Encrypt needs a moment on first boot; then open the URL above.
  Still required: Oracle VCN ingress rules for TCP 80/443 (see deploy/README.md).

EOF

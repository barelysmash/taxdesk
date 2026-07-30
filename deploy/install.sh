#!/usr/bin/env bash
# taxdesk install script — run on guildenstern as your sudo user (barelysmash).
#
#   sudo ./install.sh
#
# Idempotent: re-running upgrades in place. Owns nothing it didn't create.
set -euo pipefail

APP_DIR="/opt/taxdesk"
DATA_DIR="/var/lib/taxdesk"
ETC_DIR="/etc/taxdesk"
RUN_USER="ocelia"
RUN_GROUP="ocelia"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (use sudo)" >&2
    exit 1
fi

echo ">> preflight"
missing=()
command -v python3 >/dev/null || missing+=("python3")
command -v rsync   >/dev/null || missing+=("rsync")
python3 -c "import venv" 2>/dev/null || missing+=("python3-venv")
python3 -c "import ensurepip" 2>/dev/null || missing+=("python3-pip")
id "$RUN_USER" >/dev/null 2>&1 || {
    echo "!! user '$RUN_USER' does not exist. Create it first or set RUN_USER." >&2
    exit 1
}
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "!! missing packages: ${missing[*]}"
    echo "!! run: apt-get install -y ${missing[*]}"
    exit 1
fi

echo ">> creating directories"
install -d -m 755 -o "$RUN_USER" -g "$RUN_GROUP" "$APP_DIR"
install -d -m 755 -o "$RUN_USER" -g "$RUN_GROUP" "$DATA_DIR"
install -d -m 750 -o root        -g "$RUN_GROUP" "$ETC_DIR"

echo ">> rsyncing source"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
rsync -a --delete \
    --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='web/node_modules' --exclude='web/dist' \
    "$SRC/" "$APP_DIR/"
chown -R "$RUN_USER:$RUN_GROUP" "$APP_DIR"

echo ">> creating virtualenv"
sudo -u "$RUN_USER" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo ">> seeding env file"
if [[ ! -f "$ETC_DIR/taxdesk.env" ]]; then
    install -m 640 -o root -g "$RUN_GROUP" \
        "$APP_DIR/deploy/taxdesk.env.example" "$ETC_DIR/taxdesk.env"
fi

# Mirror VITE_-prefixed vars into web/.env.local for the frontend build.
# We deliberately do NOT symlink /etc/taxdesk/taxdesk.env into the web tree —
# it contains a server-side Socrata token that must stay off the public
# bundle. Vite only exposes VITE_-prefixed vars, but isolation is cleaner.
grep -E '^VITE_' "$ETC_DIR/taxdesk.env" > "$APP_DIR/web/.env.local" || true
chown "$RUN_USER:$RUN_GROUP" "$APP_DIR/web/.env.local"
chmod 644 "$APP_DIR/web/.env.local"

echo ">> installing systemd units"
install -m 644 "$APP_DIR/deploy/taxdesk-api.service"    /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/taxdesk-scrape.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/taxdesk-scrape.timer"   /etc/systemd/system/
systemctl daemon-reload

echo ">> running schema probe (verifies Socrata dataset IDs + columns)"
set +e
sudo -u "$RUN_USER" \
    env $(grep -v '^#' "$ETC_DIR/taxdesk.env" | xargs -d '\n') \
    bash -c "cd '$APP_DIR' && '$APP_DIR/.venv/bin/python' -m scraper.probe"
probe_status=$?
set -e
if [[ $probe_status -ne 0 ]]; then
    echo
    echo "!! schema probe failed. Inspect /var/lib/taxdesk/schema_probe.json"
    echo "!! and update CANDIDATES in scraper/probe.py + the column names in"
    echo "!! scraper/scrape.py. Then re-run this installer."
    echo "!! Nothing else has been started. Safe to fix and retry."
    exit $probe_status
fi

echo ">> running initial scrape (first-time backfill)"
if [[ ! -f "$DATA_DIR/taxdesk.db" || "${TAXDESK_FORCE_BACKFILL:-0}" = "1" ]]; then
    sudo -u "$RUN_USER" \
        env $(grep -v '^#' "$ETC_DIR/taxdesk.env" | xargs -d '\n') \
        bash -c "cd '$APP_DIR' && '$APP_DIR/.venv/bin/python' -m scraper.scrape --full-backfill" \
        || echo "!! initial scrape failed — fix and rerun: systemctl start taxdesk-scrape"
fi

echo ">> enabling services"
systemctl enable --now taxdesk-scrape.timer
systemctl enable --now taxdesk-api.service

# Read the bind host/port from the env file so the success message is accurate
bind_host=$(grep -E '^TAXDESK_BIND_HOST=' "$ETC_DIR/taxdesk.env" | cut -d= -f2)
bind_port=$(grep -E '^TAXDESK_BIND_PORT=' "$ETC_DIR/taxdesk.env" | cut -d= -f2)

echo
echo "API is up at http://${bind_host:-127.0.0.1}:${bind_port:-8770}"
echo "Health:       curl http://${bind_host:-127.0.0.1}:${bind_port:-8770}/api/health"
echo "Tail logs:    journalctl -u taxdesk-api -f"
echo "Manual pull:  systemctl start taxdesk-scrape"
echo
echo "Next: cd $APP_DIR/web && npm install && npm run build"
echo "Then serve dist/ from your tailnet however you like (caddy, nginx,"
echo "uvicorn --static, or just 'python -m http.server' for a quick test)."

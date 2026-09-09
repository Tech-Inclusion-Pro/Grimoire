#!/bin/zsh
# Install Grimoire as a macOS LaunchAgent and publish it on your tailnet.
#
# Everything is derived from where this script lives, so the repo can sit
# anywhere. Override with environment variables:
#
#   GRIMOIRE_PORT=8787          local port uvicorn binds (127.0.0.1 only)
#   GRIMOIRE_SERVE_PORT=8444    HTTPS port Tailscale Serve publishes
#   GRIMOIRE_VAULT=~/Vault      Obsidian vault deck notes are written into
#   GRIMOIRE_LABEL=...          LaunchAgent label
set -euo pipefail

REPO="${0:A:h:h}"
LABEL="${GRIMOIRE_LABEL:-com.grimoire.server}"
PORT="${GRIMOIRE_PORT:-8787}"
SERVE_PORT="${GRIMOIRE_SERVE_PORT:-8444}"
VAULT="${GRIMOIRE_VAULT:-$REPO/vault}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

UV="$(command -v uv || true)"
[[ -n "$UV" ]] || { echo "uv not found. See https://github.com/astral-sh/uv" >&2; exit 1; }

TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
[[ -x "$TS" ]] || TS="$(command -v tailscale || true)"

echo "==> repo:  $REPO"
echo "==> vault: $VAULT"

echo "==> syncing Python dependencies"
(cd "$REPO" && "$UV" sync --extra dev)

echo "==> building the frontend"
# Vite writes into backend/static, which FastAPI serves directly. Without this
# the service comes up with an API and no interface.
if command -v npm >/dev/null; then
  (cd "$REPO/frontend" && npm install --silent && npm run build)
else
  echo "    npm not found — skipping. The API will run, but there will be no UI." >&2
fi

echo "==> installing the LaunchAgent"
mkdir -p "$HOME/Library/LaunchAgents" "$VAULT"
sed -e "s|__LABEL__|$LABEL|g" \
    -e "s|__UV__|$UV|g" \
    -e "s|__PORT__|$PORT|g" \
    -e "s|__REPO__|$REPO|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__VAULT__|$VAULT|g" \
    -e "s|__PATH__|$(dirname "$UV"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin|g" \
    "$REPO/deploy/com.catrone.grimoire.plist.template" > "$PLIST"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl kickstart -k "gui/$UID/$LABEL"

echo "==> waiting for the API"
for i in {1..40}; do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null || {
  echo "backend did not start; see ~/Library/Logs/Grimoire.stderr.log" >&2
  exit 1
}
echo "    up on http://127.0.0.1:$PORT"

if [[ -n "$TS" ]]; then
  echo "==> publishing over Tailscale Serve on :$SERVE_PORT"
  "$TS" serve --bg --https=$SERVE_PORT "http://127.0.0.1:$PORT"
  HOSTNAME=$("$TS" status --json | /usr/bin/python3 -c \
    'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
  echo
  echo "Grimoire is at https://$HOSTNAME:$SERVE_PORT"
else
  echo "Tailscale not found. Grimoire is local-only on http://127.0.0.1:$PORT"
fi

cat <<'NOTE'

Next steps
  1. Set a password:
       uv run python -m backend.auth set-password 'your password' --days 30
     Until you do, anyone who can reach the page can read everything.
  2. Import card data:
       uv run python scripts/import_bulk.py
  3. If your tailnet is shared with anyone, scope this service to your own
     devices — see deploy/acl-snippet.hujson.
NOTE

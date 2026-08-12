#!/usr/bin/env bash
# =============================================================================
# refresh-cookie.sh — Upload YouTube cookies to VidGrab cookie pool
# =============================================================================
#
# USAGE:
#   # Auto-extract from Chrome (no args needed):
#   ./scripts/refresh-cookie.sh
#
#   # Use a pre-exported cookies.txt:
#   ./scripts/refresh-cookie.sh --file ~/Downloads/youtube_cookies.txt
#
#   # Different browser:
#   ./scripts/refresh-cookie.sh --browser firefox
#
#   # Other platform (tiktok, instagram, facebook):
#   ./scripts/refresh-cookie.sh --platform tiktok --file ~/Downloads/tiktok.txt
#
#   # Just check cookie expiry status (no upload):
#   ./scripts/refresh-cookie.sh --check
#
#   # Install weekly cron (Mac/Linux):
#   ./scripts/refresh-cookie.sh --install-cron
#
# CONFIG (via env or scripts/.env.local):
#   ADMIN_TOKEN=your_admin_token
#   VIDGRAB_URL=https://dowloadvideo.io.vn
#   PLATFORM=youtube
#   BROWSER=chrome
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Defaults ─────────────────────────────────────────────────────────────────
PLATFORM="${PLATFORM:-youtube}"
VIDGRAB_URL="${VIDGRAB_URL:-https://dowloadvideo.io.vn}"
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
BROWSER="${BROWSER:-chrome}"
COOKIE_FILE=""
MODE="upload"   # upload | check | install-cron

# ── Load .env.local if exists ─────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env.local"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --file)       COOKIE_FILE="$2"; shift 2 ;;
        --platform)   PLATFORM="$2";    shift 2 ;;
        --browser)    BROWSER="$2";     shift 2 ;;
        --url)        VIDGRAB_URL="$2"; shift 2 ;;
        --token)      ADMIN_TOKEN="$2"; shift 2 ;;
        --check)      MODE="check";     shift   ;;
        --install-cron) MODE="install-cron"; shift ;;
        -h|--help)
            sed -n '3,35p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown option: $1  (use --help)"; exit 1 ;;
    esac
done

LABEL="manual-$(date +%Y-%m-%d)"
[[ -n "$COOKIE_FILE" ]] || LABEL="auto-$(date +%Y-%m-%d)"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
err()  { echo -e "${RED}✗${RESET} $*"; }
info() { echo -e "${CYAN}→${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }

# ── Require admin token ───────────────────────────────────────────────────────
require_token() {
    if [[ -z "$ADMIN_TOKEN" ]]; then
        err "ADMIN_TOKEN required. Set it in scripts/.env.local:"
        echo ""
        echo "    echo 'ADMIN_TOKEN=your_token' >> $SCRIPT_DIR/.env.local"
        echo ""
        echo "Find your token in the admin panel or .env on the VPS."
        exit 1
    fi
}

# ── MODE: check ───────────────────────────────────────────────────────────────
if [[ "$MODE" == "check" ]]; then
    require_token
    echo -e "${BOLD}Cookie expiry status for $VIDGRAB_URL${RESET}"
    curl -sf \
        -H "X-Admin-Token: $ADMIN_TOKEN" \
        "$VIDGRAB_URL/api/v1/admin/cookies/expiry" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for platform, cookies in data.items():
    print(f'\n  {platform.upper()}:')
    for c in cookies:
        expires = c.get('expires_at', 'unknown')
        label = c.get('label', 'unlabeled')
        health = c.get('health', 'ok')
        status = '✓' if health == 'ok' else '✗'
        print(f'    {status} [{label}] expires: {expires}  health: {health}')
" 2>/dev/null || {
        warn "Could not parse response. Raw:"
        curl -sf -H "X-Admin-Token: $ADMIN_TOKEN" "$VIDGRAB_URL/api/v1/admin/cookies/expiry"
    }
    exit 0
fi

# ── MODE: install-cron ────────────────────────────────────────────────────────
if [[ "$MODE" == "install-cron" ]]; then
    require_token
    CRON_CMD="ADMIN_TOKEN=$ADMIN_TOKEN VIDGRAB_URL=$VIDGRAB_URL BROWSER=$BROWSER bash $SCRIPT_DIR/refresh-cookie.sh >> $SCRIPT_DIR/cron.log 2>&1"
    # Weekly on Sunday at 02:00
    CRON_ENTRY="0 2 * * 0 $CRON_CMD"

    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: use launchd plist (more reliable than cron on Mac)
        PLIST="$HOME/Library/LaunchAgents/io.vidgrab.cookie-refresh.plist"
        cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.vidgrab.cookie-refresh</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$SCRIPT_DIR/refresh-cookie.sh</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ADMIN_TOKEN</key>
        <string>$ADMIN_TOKEN</string>
        <key>VIDGRAB_URL</key>
        <string>$VIDGRAB_URL</string>
        <key>BROWSER</key>
        <string>$BROWSER</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>  <integer>0</integer>
        <key>Hour</key>     <integer>2</integer>
        <key>Minute</key>   <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>  <string>$SCRIPT_DIR/cron.log</string>
    <key>StandardErrorPath</key><string>$SCRIPT_DIR/cron.log</string>
</dict>
</plist>
PLIST
        launchctl load "$PLIST" 2>/dev/null || launchctl bootstrap gui/$(id -u) "$PLIST" 2>/dev/null || true
        ok "Cron installed (macOS launchd): every Sunday 02:00"
        info "Plist: $PLIST"
        info "Log:   $SCRIPT_DIR/cron.log"
        info "Uninstall: launchctl unload $PLIST && rm $PLIST"
    else
        # Linux: crontab
        (crontab -l 2>/dev/null | grep -v "vidgrab.cookie-refresh"; echo "# vidgrab.cookie-refresh") | crontab -
        (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
        ok "Cron installed (crontab): every Sunday 02:00"
        info "Log: $SCRIPT_DIR/cron.log"
        info "Edit: crontab -e"
        info "Uninstall: crontab -l | grep -v 'vidgrab.cookie-refresh' | crontab -"
    fi
    exit 0
fi

# ── MODE: upload ──────────────────────────────────────────────────────────────
require_token
echo ""
echo -e "${BOLD}VidGrab Cookie Refresh${RESET}  (platform: $PLATFORM)"
echo "─────────────────────────────────────────"

TMP_COOKIE=""

# Step 1: Get cookie file
if [[ -n "$COOKIE_FILE" ]]; then
    info "Using provided file: $COOKIE_FILE"
else
    info "Auto-extracting $PLATFORM cookies from $BROWSER..."

    TMP_COOKIE="$(mktemp /tmp/vidgrab_cookie_XXXXXX.txt)"

    if EXTRACTED=$(python3 "$SCRIPT_DIR/extract-cookie.py" "$TMP_COOKIE" "$BROWSER" 2>&1); then
        COOKIE_FILE="$TMP_COOKIE"
        ok "Cookies extracted from $BROWSER"
    else
        echo "$EXTRACTED" | grep -v "^$" | sed 's/^/  /'
        echo ""
        warn "Auto-extract failed. Manual steps:"
        echo "  1. Install Chrome extension: 'Get cookies.txt LOCALLY'"
        echo "     https://chrome.google.com/webstore/detail/cclelndahbckbenkjhflpdbgdldlbecc"
        echo "  2. Open youtube.com → click extension → Export"
        echo "  3. Run: $0 --file ~/Downloads/youtube.com_cookies.txt"
        [[ -f "$TMP_COOKIE" ]] && rm -f "$TMP_COOKIE"
        exit 1
    fi
fi

# Step 2: Validate
if [[ ! -f "$COOKIE_FILE" ]]; then
    err "Cookie file not found: $COOKIE_FILE"
    exit 1
fi

SIZE=$(wc -c < "$COOKIE_FILE" | tr -d ' ')
if [[ "$SIZE" -lt 200 ]]; then
    err "Cookie file too small (${SIZE}B) — likely empty or invalid"
    [[ -n "$TMP_COOKIE" ]] && rm -f "$TMP_COOKIE"
    exit 1
fi

# Quick sanity: check for YouTube auth cookies
if ! grep -qE "SID|SAPISID|LOGIN_INFO|__Secure-1PSID" "$COOKIE_FILE" 2>/dev/null; then
    warn "No YouTube session cookies found in file — are you logged in on YouTube?"
    warn "Uploading anyway, but it may not fix the auth error."
fi

info "File: $COOKIE_FILE (${SIZE}B)"

# Step 3: Upload
info "Uploading to $VIDGRAB_URL..."

HTTP_STATUS=$(curl -s -o /tmp/vidgrab_cookie_upload_resp.json -w "%{http_code}" \
    -X POST \
    -H "X-Admin-Token: $ADMIN_TOKEN" \
    -F "file=@$COOKIE_FILE;type=text/plain" \
    -F "platform=$PLATFORM" \
    -F "label=$LABEL" \
    "$VIDGRAB_URL/api/v1/admin/cookies/upload")

# Cleanup temp file
[[ -n "$TMP_COOKIE" && -f "$TMP_COOKIE" ]] && rm -f "$TMP_COOKIE"

RESP=$(cat /tmp/vidgrab_cookie_upload_resp.json 2>/dev/null || echo "{}")
rm -f /tmp/vidgrab_cookie_upload_resp.json

if [[ "$HTTP_STATUS" == "200" ]]; then
    echo ""
    ok "Cookie uploaded!"
    python3 -c "
import json, sys
d = json.loads('''$RESP''')
size = d.get('size', '?')
msg  = d.get('message', '')
cookies = d.get('cookies', [])
print(f'  Pool size: {size} cookie(s)')
print(f'  Message: {msg}')
if cookies:
    for c in cookies[:3]:
        exp = c.get('expires_at', 'unknown')
        print(f'  Expires: {exp}')
" 2>/dev/null || echo "  Response: $RESP"

    echo ""
    ok "Done. Restart workers to pick up new cookies:"
    echo ""
    echo "  ssh oracle-vps 'cd ~/vidgrab && docker compose restart celery celery-beat'"
    echo ""
    info "Check pool: $VIDGRAB_URL/api/v1/admin/cookies/status"
    info "Next time, run: $0 --install-cron  (auto-refresh weekly)"

elif [[ "$HTTP_STATUS" == "401" || "$HTTP_STATUS" == "403" ]]; then
    err "Auth failed (HTTP $HTTP_STATUS) — check ADMIN_TOKEN"
    exit 1
else
    err "Upload failed (HTTP $HTTP_STATUS)"
    echo "Response: $RESP"
    exit 1
fi

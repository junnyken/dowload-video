#!/usr/bin/env bash
# launch-check.sh — VidGrab pre-launch readiness gate
#
# Usage:
#   ./scripts/launch-check.sh               — full check suite (local dev)
#   ./scripts/launch-check.sh --env prod    — production-safe read-only checks
#   BASE_URL=https://dowloadvideo.io.vn ./scripts/launch-check.sh --env prod
#
# Exit codes:
#   0 — all blockers passed (warnings OK)
#   1 — one or more BLOCKER checks failed
#
# This script orchestrates:
#   1. Python pytest launch readiness suite (unit + static checks)
#   2. Script presence & executability checks
#   3. Docker Compose service definition checks
#   4. Optional: live HTTP smoke checks (requires running server)
#
set -euo pipefail

# ─── Colours ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ─── Defaults ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$ROOT_DIR/backend"
ENV_MODE=""
BASE_URL="${BASE_URL:-http://localhost:8000}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
declare -a BLOCKERS=()
declare -a WARNINGS=()

# ─── Arg parse ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env|-e) ENV_MODE="$2"; shift 2 ;;
        --base-url) BASE_URL="$2"; shift 2 ;;
        --help|-h)
            sed -n 's/^# \{0,1\}//p' "$0" | head -20
            exit 0 ;;
        *) echo -e "${RED}Unknown arg: $1${RESET}" >&2; exit 1 ;;
    esac
done

case "${ENV_MODE:-}" in
    prod|production) ENV_MODE="prod" ;;
    preview|staging) ENV_MODE="preview" ;;
    "")              ENV_MODE="preview" ;;
    *) echo -e "${RED}Unknown --env: $ENV_MODE${RESET}" >&2; exit 1 ;;
esac

# ─── Helpers ─────────────────────────────────────────────────────────────────
_ts()      { date '+%H:%M:%S'; }
pass()     { echo -e "${GREEN}[$(_ts)] ✓ PASS${RESET}  $*"; (( PASS_COUNT++ )) || true; }
warn()     { echo -e "${YELLOW}[$(_ts)] ⚠ WARN${RESET}  $*"; (( WARN_COUNT++ )) || true; WARNINGS+=("$*"); }
blocker()  { echo -e "${RED}[$(_ts)] ✗ FAIL${RESET}  ${BOLD}$*${RESET}"; (( FAIL_COUNT++ )) || true; BLOCKERS+=("$*"); }
section()  { echo -e "\n${CYAN}${BOLD}── $* ─────────────────────────────────${RESET}"; }
info()     { echo -e "${DIM}[$(_ts)]   $*${RESET}"; }

http_get() {
    local url="$1"
    curl -sf --max-time 5 "$url" 2>/dev/null
}

http_status() {
    local url="$1"
    curl -so /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000"
}

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Python pytest launch readiness suite
# ═════════════════════════════════════════════════════════════════════════════
section "1. Python test suite (test_launch_readiness.py)"

PYTEST_BIN=""
for _p in python3 python; do
    if command -v "$_p" &>/dev/null; then
        PYTEST_BIN="$_p -m pytest"
        break
    fi
done

if [[ -z "$PYTEST_BIN" ]]; then
    warn "Python not found — skipping pytest suite"
else
    info "Running: cd $BACKEND_DIR && $PYTEST_BIN tests/test_launch_readiness.py -q --tb=short"
    PYTEST_RESULT=0
    (
        cd "$BACKEND_DIR"
        $PYTEST_BIN tests/test_launch_readiness.py -q --tb=short 2>&1
    ) || PYTEST_RESULT=$?

    if [[ $PYTEST_RESULT -eq 0 ]]; then
        pass "All launch readiness tests passed"
    else
        blocker "launch readiness test suite failed (exit $PYTEST_RESULT) — see output above"
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Script presence & executability
# ═════════════════════════════════════════════════════════════════════════════
section "2. Deploy / rollback scripts"

_check_script() {
    local name="$1" path="$2"
    if [[ ! -f "$path" ]]; then
        blocker "$name missing ($path)"
    elif [[ ! -x "$path" ]]; then
        blocker "$name not executable — run: chmod +x $path"
    else
        pass "$name present and executable"
    fi
}

_check_script "deploy-vps.sh"      "$ROOT_DIR/deploy-vps.sh"
_check_script "scripts/rollback.sh"      "$SCRIPT_DIR/rollback.sh"
_check_script "scripts/smoke-test.sh"    "$SCRIPT_DIR/smoke-test.sh"
_check_script "scripts/deploy-check.sh" "$SCRIPT_DIR/deploy-check.sh"

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Docker Compose configuration
# ═════════════════════════════════════════════════════════════════════════════
section "3. Docker Compose"

COMPOSE="$ROOT_DIR/docker-compose.yml"
if [[ ! -f "$COMPOSE" ]]; then
    blocker "docker-compose.yml not found at $COMPOSE"
else
    pass "docker-compose.yml present"

    for _svc in backend celery redis caddy; do
        if grep -q "^  ${_svc}:" "$COMPOSE" 2>/dev/null || grep -q "^  ${_svc}-" "$COMPOSE" 2>/dev/null; then
            pass "compose: service '$_svc' defined"
        else
            warn "compose: service '$_svc' not found in docker-compose.yml"
        fi
    done

    if grep -qi "healthcheck" "$COMPOSE"; then
        pass "compose: healthcheck defined for at least one service"
    else
        warn "compose: no healthcheck found — container restarts may be delayed"
    fi
fi

# Preview compose
if [[ -f "$ROOT_DIR/docker-compose.preview.yml" ]]; then
    pass "docker-compose.preview.yml present"
else
    warn "docker-compose.preview.yml missing — preview env may not be deployable"
fi

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Backend config & dependencies
# ═════════════════════════════════════════════════════════════════════════════
section "4. Backend dependencies"

if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
    pass "requirements.txt present"
else
    blocker "requirements.txt missing — Docker build will fail"
fi

# yt-dlp version check
if command -v yt-dlp &>/dev/null; then
    YTDLP_VER=$(yt-dlp --version 2>/dev/null || echo "unknown")
    info "yt-dlp version: $YTDLP_VER"
    # Minimum: 2024.01.01
    if [[ "$YTDLP_VER" > "2024" ]]; then
        pass "yt-dlp version OK ($YTDLP_VER)"
    else
        warn "yt-dlp version may be outdated: $YTDLP_VER (want >= 2024.01.01)"
    fi
else
    warn "yt-dlp not found locally (OK if only running in Docker)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Frontend static assets
# ═════════════════════════════════════════════════════════════════════════════
section "5. Frontend static assets"

FRONTEND="$ROOT_DIR/frontend"

_check_file() {
    local label="$1" path="$2"
    if [[ -f "$path" ]]; then pass "$label"; else blocker "$label missing ($path)"; fi
}

_check_file "frontend/index.html"          "$FRONTEND/index.html"
_check_file "frontend/public/manifest.json" "$FRONTEND/public/manifest.json"

if [[ -f "$FRONTEND/public/sw.js" ]] || [[ -f "$FRONTEND/public/service-worker.js" ]]; then
    pass "service worker file present"
else
    blocker "service worker (sw.js) missing — PWA install and offline broken"
fi

# Viewport meta
if grep -q 'width=device-width' "$FRONTEND/index.html" 2>/dev/null; then
    pass "index.html: viewport meta present"
else
    blocker "index.html missing viewport meta — mobile layout broken"
fi

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Live HTTP smoke checks (only in preview mode or if VIDGRAB_LIVE=1)
# ═════════════════════════════════════════════════════════════════════════════

RUN_LIVE="${VIDGRAB_LIVE:-}"
if [[ "$ENV_MODE" == "preview" || -n "$RUN_LIVE" ]]; then
    section "6. Live HTTP smoke checks (${BASE_URL})"

    info "Checking server reachability..."
    PING_STATUS=$(http_status "${BASE_URL}/ping")

    if [[ "$PING_STATUS" == "200" ]]; then
        pass "GET /ping → 200"
        PING_BODY=$(http_get "${BASE_URL}/ping" || echo "{}")
        if echo "$PING_BODY" | grep -q '"ok":true'; then
            pass "GET /ping body has ok=true"
        else
            warn "GET /ping body does not have ok=true: $PING_BODY"
        fi
    elif [[ "$PING_STATUS" == "000" ]]; then
        warn "Server not reachable at $BASE_URL — skipping live checks"
    else
        blocker "GET /ping returned HTTP $PING_STATUS (expected 200)"
    fi

    if [[ "$PING_STATUS" == "200" ]]; then
        # /platforms
        PLAT_STATUS=$(http_status "${BASE_URL}/platforms")
        if [[ "$PLAT_STATUS" == "200" ]]; then
            pass "GET /platforms → 200"
        else
            blocker "GET /platforms → $PLAT_STATUS (expected 200)"
        fi

        # Admin must be protected (no token → 401/403)
        ADMIN_STATUS=$(http_status "${BASE_URL}/admin/system-health")
        if [[ "$ADMIN_STATUS" == "401" || "$ADMIN_STATUS" == "403" || "$ADMIN_STATUS" == "404" ]]; then
            pass "GET /admin/system-health (no auth) → $ADMIN_STATUS ✓ protected"
        else
            blocker "GET /admin/system-health (no auth) → $ADMIN_STATUS (expected 401/403)"
        fi

        # Bulk-download must reject GET
        BULK_STATUS=$(http_status "${BASE_URL}/bulk-download")
        if [[ "$BULK_STATUS" == "405" ]]; then
            pass "GET /bulk-download → 405 (correct — POST only)"
        else
            warn "GET /bulk-download → $BULK_STATUS (expected 405)"
        fi
    fi
else
    section "6. Live HTTP smoke checks"
    info "Skipped (--env prod or VIDGRAB_LIVE not set)."
    info "To run: VIDGRAB_LIVE=1 BASE_URL=https://dowloadvideo.io.vn $0"
fi

# ═════════════════════════════════════════════════════════════════════════════
# FINAL REPORT
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  LAUNCH READINESS REPORT${RESET}"
echo -e "  ${GREEN}PASS:${RESET} $PASS_COUNT   ${YELLOW}WARN:${RESET} $WARN_COUNT   ${RED}BLOCKER:${RESET} $FAIL_COUNT"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    echo -e "\n${YELLOW}${BOLD}Warnings:${RESET}"
    for w in "${WARNINGS[@]}"; do
        echo -e "  ${YELLOW}⚠${RESET} $w"
    done
fi

if [[ ${#BLOCKERS[@]} -gt 0 ]]; then
    echo -e "\n${RED}${BOLD}Blockers (fix before launching):${RESET}"
    for b in "${BLOCKERS[@]}"; do
        echo -e "  ${RED}✗${RESET} $b"
    done
    echo ""
    echo -e "${RED}${BOLD}  ✗ LAUNCH BLOCKED — resolve blockers above${RESET}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    exit 1
else
    echo ""
    if [[ $WARN_COUNT -gt 0 ]]; then
        echo -e "${GREEN}${BOLD}  ✓ LAUNCH READY${RESET} ${YELLOW}(${WARN_COUNT} warning(s) — review before deploy)${RESET}"
    else
        echo -e "${GREEN}${BOLD}  ✓ LAUNCH READY — all checks passed${RESET}"
    fi
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    exit 0
fi

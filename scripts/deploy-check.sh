#!/usr/bin/env bash
# deploy-check.sh — Pre/post deploy validation for VidGrab
# Usage:
#   ./scripts/deploy-check.sh pre   — run before deploy (call on VPS)
#   ./scripts/deploy-check.sh post  — run after deploy (call on VPS)
#
# Exit codes:
#   0 — all checks passed (PASS + WARN only)
#   1 — one or more checks FAILED
set -euo pipefail

# ─── ANSI colours ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Constants ────────────────────────────────────────────────────────────────
VPS_DIR="${VPS_DIR:-/home/ubuntu/vidgrab}"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
MIN_DISK_GB=2
QUEUE_WARN_THRESHOLD=100
HEALTH_TIMEOUT=60   # seconds to wait for healthy container
LAST_MIGRATION_FILE="/tmp/vidgrab_last_migration.txt"
REQUIRED_ENV_VARS=(SUPABASE_URL SUPABASE_KEY TELEGRAM_BOT_TOKEN REDIS_URL)
MIN_YTDLP_DATE="2024.01.01"  # YYYY.MM.DD

# ─── State ───────────────────────────────────────────────────────────────────
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
declare -a RESULTS=()    # "STATUS|NAME|DETAIL|ELAPSED_MS"
START_EPOCH=$(date +%s)

# ─── Helpers ─────────────────────────────────────────────────────────────────
log() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${RESET} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARN${RESET} $*"; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] ERROR${RESET} $*" >&2; }

record() {
    local status="$1" name="$2" detail="${3:-}" elapsed="${4:-0}"
    RESULTS+=("${status}|${name}|${detail}|${elapsed}")
    case "$status" in
        PASS) (( ++PASS_COUNT )) ;;
        WARN) (( ++WARN_COUNT )) ;;
        FAIL) (( ++FAIL_COUNT )) ;;
    esac
}

ms_since() {
    local start_ns="$1"
    local end_ns
    end_ns=$(date +%s%N)
    echo $(( (end_ns - start_ns) / 1000000 ))
}

print_summary() {
    local log_file="$1"
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}  VidGrab Deploy Check Summary${RESET}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    printf "  %-35s %-6s %s\n" "Check" "Status" "Detail"
    echo "  ─────────────────────────────────────────────────────"
    for entry in "${RESULTS[@]}"; do
        IFS='|' read -r status name detail elapsed <<< "$entry"
        case "$status" in
            PASS) colour="$GREEN" ;;
            WARN) colour="$YELLOW" ;;
            FAIL) colour="$RED" ;;
            *)    colour="$RESET" ;;
        esac
        printf "  %-35s ${colour}%-6s${RESET} %s  ${CYAN}(%sms)${RESET}\n" \
            "$name" "$status" "$detail" "$elapsed"
    done
    echo "  ─────────────────────────────────────────────────────"
    echo -e "  ${GREEN}PASS: ${PASS_COUNT}${RESET}   ${YELLOW}WARN: ${WARN_COUNT}${RESET}   ${RED}FAIL: ${FAIL_COUNT}${RESET}"
    local total=$(( $(date +%s) - START_EPOCH ))
    echo -e "  Total time: ${total}s"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    [[ -n "$log_file" ]] && echo "  Log: $log_file"
    echo ""
}

# ─── PRE-DEPLOY CHECKS ───────────────────────────────────────────────────────

check_disk_free() {
    log "Checking disk space..."
    local t0; t0=$(date +%s%N)

    local root_avail_kb
    root_avail_kb=$(df / --output=avail | tail -1)
    local root_avail_gb=$(( root_avail_kb / 1024 / 1024 ))

    if (( root_avail_gb < MIN_DISK_GB )); then
        record FAIL "Disk: / free space" "${root_avail_gb}GB < ${MIN_DISK_GB}GB required" "$(ms_since "$t0")"
        return
    fi

    # Also check downloads volume mount if it exists separately
    local vol_detail=""
    if mountpoint -q "${VPS_DIR}/downloads" 2>/dev/null; then
        local vol_avail_kb
        vol_avail_kb=$(df "${VPS_DIR}/downloads" --output=avail | tail -1)
        local vol_avail_gb=$(( vol_avail_kb / 1024 / 1024 ))
        if (( vol_avail_gb < MIN_DISK_GB )); then
            record FAIL "Disk: downloads volume free" "${vol_avail_gb}GB < ${MIN_DISK_GB}GB required" "$(ms_since "$t0")"
            return
        fi
        vol_detail=" | downloads: ${vol_avail_gb}GB free"
    fi

    record PASS "Disk: / free space" "${root_avail_gb}GB free${vol_detail}" "$(ms_since "$t0")"
}

check_redis_reachable() {
    log "Checking Redis connectivity..."
    local t0; t0=$(date +%s%N)

    local pong
    # Try via docker exec redis container first; fall back to docker run
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^redis$\|vidgrab-redis'; then
        local redis_cname
        redis_cname=$(docker ps --format '{{.Names}}' | grep -E '^redis$|vidgrab-redis' | head -1)
        pong=$(docker exec "$redis_cname" redis-cli ping 2>/dev/null || true)
    else
        pong=$(docker run --rm --network vidgrab_default redis:7-alpine \
            redis-cli -h redis -p 6379 ping 2>/dev/null || true)
    fi

    if [[ "$pong" == "PONG" ]]; then
        record PASS "Redis: connectivity" "PONG received" "$(ms_since "$t0")"
    else
        record FAIL "Redis: connectivity" "Expected PONG, got: '${pong}'" "$(ms_since "$t0")"
    fi
}

check_pending_migrations() {
    log "Checking for pending DB migrations..."
    local t0; t0=$(date +%s%N)

    local migration_dir="${VPS_DIR}/database"
    if [[ ! -d "$migration_dir" ]]; then
        migration_dir="${VPS_DIR}/migrations"
    fi

    if [[ ! -d "$migration_dir" ]]; then
        record WARN "DB: migrations directory" "No migrations dir found at ${migration_dir}" "$(ms_since "$t0")"
        return
    fi

    local latest_sql
    latest_sql=$(find "$migration_dir" -name "*.sql" | sort | tail -1)
    if [[ -z "$latest_sql" ]]; then
        record PASS "DB: pending migrations" "No .sql migration files found" "$(ms_since "$t0")"
        return
    fi

    local latest_basename
    latest_basename=$(basename "$latest_sql")

    if [[ -f "$LAST_MIGRATION_FILE" ]]; then
        local last_applied
        last_applied=$(cat "$LAST_MIGRATION_FILE")
        if [[ "$last_applied" != "$latest_basename" ]]; then
            record WARN "DB: pending migrations" "Latest: ${latest_basename}, Last applied: ${last_applied}" "$(ms_since "$t0")"
            return
        fi
        record PASS "DB: pending migrations" "Up-to-date at ${latest_basename}" "$(ms_since "$t0")"
    else
        # First run — store current latest and warn
        echo "$latest_basename" > "$LAST_MIGRATION_FILE"
        record WARN "DB: pending migrations" "No baseline found; recorded ${latest_basename}" "$(ms_since "$t0")"
    fi
}

check_env_vars() {
    log "Checking .env file and required variables..."
    local t0; t0=$(date +%s%N)

    local env_file="${VPS_DIR}/.env"
    if [[ ! -f "$env_file" ]]; then
        record FAIL "Env: .env file exists" "Not found at ${env_file}" "$(ms_since "$t0")"
        return
    fi

    local missing=()
    for var in "${REQUIRED_ENV_VARS[@]}"; do
        # Check if var exists and has a non-empty value
        if ! grep -qE "^${var}=.+" "$env_file" 2>/dev/null; then
            missing+=("$var")
        fi
    done

    if (( ${#missing[@]} > 0 )); then
        record FAIL "Env: required variables" "Missing/empty: ${missing[*]}" "$(ms_since "$t0")"
    else
        record PASS "Env: required variables" "All ${#REQUIRED_ENV_VARS[@]} vars present" "$(ms_since "$t0")"
    fi
}

check_ytdlp_version() {
    log "Checking yt-dlp version..."
    local t0; t0=$(date +%s%N)

    # Only run if backend container is already up (pre-deploy may not have it)
    local backend_cname
    backend_cname=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'vidgrab-backend|vidgrab_backend' | head -1 || true)

    if [[ -z "$backend_cname" ]]; then
        record WARN "yt-dlp: version check" "Backend container not running yet; skipped" "$(ms_since "$t0")"
        return
    fi

    local version
    version=$(docker exec "$backend_cname" yt-dlp --version 2>/dev/null || true)
    if [[ -z "$version" ]]; then
        record WARN "yt-dlp: version check" "yt-dlp not found in container" "$(ms_since "$t0")"
        return
    fi

    # version format: YYYY.MM.DD[.N]  e.g. 2024.03.15
    # Compare as YYYYMMDD integers (strip dots, take first 8 digits)
    local ver_int
    ver_int=$(echo "$version" | tr -d '.' | cut -c1-8)
    local min_int
    min_int=$(echo "$MIN_YTDLP_DATE" | tr -d '.')

    if (( ver_int >= min_int )); then
        record PASS "yt-dlp: version" "${version} >= ${MIN_YTDLP_DATE}" "$(ms_since "$t0")"
    else
        record WARN "yt-dlp: version" "${version} is older than ${MIN_YTDLP_DATE}" "$(ms_since "$t0")"
    fi
}

run_pre_checks() {
    local log_file="/tmp/vidgrab_precheck_$(date +%Y%m%d_%H%M%S).log"
    echo -e "${BOLD}${CYAN}=== VidGrab PRE-DEPLOY CHECKS ===${RESET}"
    echo -e "${CYAN}Timestamp: $(date)${RESET}"
    echo ""

    # Run all checks, tee output to log
    {
        check_disk_free
        check_redis_reachable
        check_pending_migrations
        check_env_vars
        check_ytdlp_version
    } 2>&1 | tee "$log_file"

    print_summary "$log_file"

    if (( FAIL_COUNT > 0 )); then
        echo -e "${RED}${BOLD}PRE-DEPLOY FAILED — ${FAIL_COUNT} check(s) failed. Aborting deploy.${RESET}"
        return 1
    else
        echo -e "${GREEN}${BOLD}PRE-DEPLOY OK — proceed with deploy.${RESET}"
        return 0
    fi
}

# ─── POST-DEPLOY CHECKS ──────────────────────────────────────────────────────

wait_for_healthy() {
    log "Waiting for backend container to become healthy (up to ${HEALTH_TIMEOUT}s)..."
    local t0; t0=$(date +%s%N)
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))

    local backend_cname
    # Poll until container appears
    while true; do
        backend_cname=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'vidgrab-backend|vidgrab_backend' | head -1 || true)
        [[ -n "$backend_cname" ]] && break
        if (( $(date +%s) >= deadline )); then
            record FAIL "Container: backend healthy" "Container never appeared within ${HEALTH_TIMEOUT}s" "$(ms_since "$t0")"
            return 1
        fi
        sleep 2
    done

    while true; do
        local health_status
        health_status=$(docker inspect --format='{{.State.Health.Status}}' "$backend_cname" 2>/dev/null || echo "none")

        if [[ "$health_status" == "healthy" ]]; then
            record PASS "Container: backend healthy" "${backend_cname} is healthy" "$(ms_since "$t0")"
            return 0
        fi

        if [[ "$health_status" == "unhealthy" ]]; then
            record FAIL "Container: backend healthy" "${backend_cname} health = unhealthy" "$(ms_since "$t0")"
            return 1
        fi

        # "starting" or "none" — keep waiting
        if (( $(date +%s) >= deadline )); then
            record WARN "Container: backend healthy" "Status still '${health_status}' after ${HEALTH_TIMEOUT}s" "$(ms_since "$t0")"
            return 0  # WARN not FAIL — container may not have HEALTHCHECK defined
        fi
        sleep 3
    done
}

check_health_endpoint() {
    log "Checking /health endpoint..."
    local t0; t0=$(date +%s%N)

    # Backend port is internal to Docker network — use docker exec instead of curl
    local body
    body=$(docker exec vidgrab-backend-1 python3 -c "
import urllib.request, sys
try:
    r = urllib.request.urlopen('http://localhost:8000/health', timeout=10)
    sys.stdout.write(r.read().decode())
except Exception as e:
    sys.stdout.write('')
" 2>/dev/null || true)

    if [[ -z "$body" ]]; then
        record FAIL "Health: /health endpoint" "No response from backend container" "$(ms_since "$t0")"
        return
    fi

    local status redis_ok
    status=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || true)
    redis_ok=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('redis',{}).get('ok',False)).lower())" 2>/dev/null || true)

    if [[ "$status" != "ok" ]]; then
        record FAIL "Health: status=ok" "Got status='${status}'" "$(ms_since "$t0")"
        return
    fi
    if [[ "$redis_ok" != "true" ]]; then
        record FAIL "Health: redis.ok=true" "redis.ok=${redis_ok}" "$(ms_since "$t0")"
        return
    fi

    record PASS "Health: /health endpoint" "status=ok, redis.ok=true" "$(ms_since "$t0")"
}

check_celery_workers() {
    log "Checking Celery worker count..."
    local t0; t0=$(date +%s%N)

    local response
    response=$(docker exec vidgrab-backend-1 python3 -c "
import urllib.request, sys
try:
    r = urllib.request.urlopen('http://localhost:8000/health', timeout=10)
    sys.stdout.write(r.read().decode())
except Exception:
    sys.stdout.write('')
" 2>/dev/null || true)
    if [[ -z "$response" ]]; then
        record FAIL "Celery: worker count" "Could not reach /health via docker exec" "$(ms_since "$t0")"
        return
    fi

    local worker_count
    worker_count=$(echo "$response" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('celery',{}).get('worker_count',0))" \
        2>/dev/null || echo "0")

    if (( worker_count == 0 )); then
        record FAIL "Celery: worker count" "worker_count=0 — no workers online!" "$(ms_since "$t0")"
    else
        record PASS "Celery: worker count" "${worker_count} worker(s) online" "$(ms_since "$t0")"
    fi
}

check_queue_depths() {
    log "Checking queue depths..."
    local t0; t0=$(date +%s%N)

    local redis_cname
    redis_cname=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^redis$|vidgrab-redis' | head -1 || true)

    local exec_prefix=()
    if [[ -n "$redis_cname" ]]; then
        exec_prefix=(docker exec "$redis_cname" redis-cli)
    else
        exec_prefix=(docker run --rm --network vidgrab_default redis:7-alpine redis-cli -h redis -p 6379)
    fi

    local downloads_depth celery_depth
    downloads_depth=$("${exec_prefix[@]}" llen downloads 2>/dev/null || echo "ERR")
    celery_depth=$("${exec_prefix[@]}" llen celery 2>/dev/null || echo "ERR")

    local detail="downloads=${downloads_depth}, celery=${celery_depth}"

    if [[ "$downloads_depth" == "ERR" || "$celery_depth" == "ERR" ]]; then
        record WARN "Queue: depths" "Could not read queue depths from Redis" "$(ms_since "$t0")"
        return
    fi

    if (( downloads_depth > QUEUE_WARN_THRESHOLD || celery_depth > QUEUE_WARN_THRESHOLD )); then
        record WARN "Queue: depths" "${detail} — queue(s) above ${QUEUE_WARN_THRESHOLD}" "$(ms_since "$t0")"
    else
        record PASS "Queue: depths" "$detail" "$(ms_since "$t0")"
    fi
}

check_smoke_fetch() {
    log "Running smoke test (fetch-link canary)..."
    local t0; t0=$(date +%s%N)

    # Use docker exec — backend port is internal to Docker network
    local canary_url="https://youtu.be/dQw4w9WgXcQ"
    local body
    body=$(docker exec vidgrab-backend-1 python3 -c "
import urllib.request, json, sys
req = urllib.request.Request(
    'http://localhost:8000/api/v1/fetch-link',
    data=json.dumps({'url': '${canary_url}'}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req, timeout=20)
    sys.stdout.write(r.read().decode())
except urllib.error.HTTPError as e:
    sys.stdout.write(e.read().decode())
except Exception:
    sys.stdout.write('')
" 2>/dev/null || true)

    if [[ -z "$body" ]]; then
        record FAIL "Smoke: fetch-link canary" "No response from backend container" "$(ms_since "$t0")"
        return
    fi

    # Expect job started or info returned — not an error key
    local has_error
    has_error=$(echo "$body" | python3 -c \
        "import sys,json
try:
    d=json.load(sys.stdin)
    print('yes' if 'error' in d and d.get('error') else 'no')
except:
    print('parse_error')" 2>/dev/null || echo "parse_error")

    if [[ "$has_error" == "yes" ]]; then
        local err_msg
        err_msg=$(echo "$body" | python3 -c \
            "import sys,json; d=json.load(sys.stdin); print(d.get('error','unknown'))" 2>/dev/null || true)
        record FAIL "Smoke: fetch-link canary" "API returned error: ${err_msg}" "$(ms_since "$t0")"
    elif [[ "$has_error" == "parse_error" ]]; then
        record WARN "Smoke: fetch-link canary" "Could not parse response" "$(ms_since "$t0")"
    else
        record PASS "Smoke: fetch-link canary" "Job started OK" "$(ms_since "$t0")"
    fi
}

run_post_checks() {
    echo -e "${BOLD}${CYAN}=== VidGrab POST-DEPLOY CHECKS ===${RESET}"
    echo -e "${CYAN}Timestamp: $(date)${RESET}"
    echo ""

    wait_for_healthy
    check_health_endpoint
    check_celery_workers
    check_queue_depths
    check_smoke_fetch

    print_summary ""

    if (( FAIL_COUNT > 0 )); then
        echo -e "${RED}${BOLD}POST-DEPLOY FAILED — ${FAIL_COUNT} check(s) failed. Consider rollback.${RESET}"
        return 1
    else
        echo -e "${GREEN}${BOLD}POST-DEPLOY OK — VidGrab is healthy.${RESET}"
        return 0
    fi
}

# ─── ENTRYPOINT ──────────────────────────────────────────────────────────────

MODE="${1:-}"
if [[ -z "$MODE" ]]; then
    echo -e "${RED}Usage: $0 <pre|post>${RESET}" >&2
    exit 1
fi

case "$MODE" in
    pre)  run_pre_checks ;;
    post) run_post_checks ;;
    *)
        echo -e "${RED}Unknown mode: ${MODE}. Use 'pre' or 'post'.${RESET}" >&2
        exit 1
        ;;
esac

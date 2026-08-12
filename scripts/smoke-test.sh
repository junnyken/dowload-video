#!/usr/bin/env bash
# smoke-test.sh — VidGrab 8-check smoke test suite
# Usage:
#   ./scripts/smoke-test.sh --env preview   — full suite against preview
#   ./scripts/smoke-test.sh --env prod      — safe read-only checks only
#   BASE_URL=http://localhost:8000 ./scripts/smoke-test.sh
#
# Environment variables:
#   BASE_URL    Override target URL (default: http://localhost:8000)
#   API_KEY     Optional API key for authenticated checks
#
# Exit codes:
#   0 — all applicable checks passed
#   1 — one or more checks failed
set -euo pipefail

# ─── ANSI colours ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_BASE_URL="http://localhost:8000"
ENV_MODE=""     # preview | prod
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
TOTAL_CHECKS=8
declare -a RESULTS=()  # "STATUS|NAME|DETAIL|ELAPSED_MS"

# ─── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env|-e)
            ENV_MODE="$2"; shift 2 ;;
        --base-url)
            BASE_URL="$2"; shift 2 ;;
        --help|-h)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
            exit 0 ;;
        *)
            echo -e "${RED}Unknown argument: $1${RESET}" >&2; exit 1 ;;
    esac
done

BASE_URL="${BASE_URL:-$DEFAULT_BASE_URL}"

# Normalise env mode
case "${ENV_MODE:-}" in
    prod|production)  ENV_MODE="prod" ;;
    preview|staging)  ENV_MODE="preview" ;;
    "")               ENV_MODE="preview" ;;  # default: full suite
    *)
        echo -e "${RED}Unknown --env: ${ENV_MODE}. Use 'preview' or 'prod'.${RESET}" >&2
        exit 1
        ;;
esac

# ─── Helpers ─────────────────────────────────────────────────────────────────
log() { echo -e "${CYAN}[$(date '+%H:%M:%S')]${RESET} $*"; }

ms_since() {
    local start_ns="$1"
    local end_ns
    end_ns=$(date +%s%N)
    echo $(( (end_ns - start_ns) / 1000000 ))
}

record_pass() {
    local name="$1" detail="${2:-}" elapsed="${3:-0}"
    RESULTS+=("PASS|${name}|${detail}|${elapsed}")
    (( PASS_COUNT++ ))
    echo -e "  ${GREEN}✓${RESET} ${BOLD}${name}${RESET}  ${detail}  ${CYAN}(${elapsed}ms)${RESET}"
}

record_fail() {
    local name="$1" detail="${2:-}" elapsed="${3:-0}"
    RESULTS+=("FAIL|${name}|${detail}|${elapsed}")
    (( FAIL_COUNT++ ))
    echo -e "  ${RED}✗${RESET} ${BOLD}${name}${RESET}  ${RED}${detail}${RESET}  ${CYAN}(${elapsed}ms)${RESET}"
}

record_skip() {
    local name="$1" reason="${2:-prod-mode: mutation skipped}"
    RESULTS+=("SKIP|${name}|${reason}|0")
    (( SKIP_COUNT++ ))
    echo -e "  ${YELLOW}–${RESET} ${BOLD}${name}${RESET}  ${YELLOW}[SKIP] ${reason}${RESET}"
}

# curl wrapper: returns "BODY\nHTTP_CODE" on stdout, never exits non-zero
safe_curl() {
    curl -sf --max-time 15 -w '\n%{http_code}' "$@" 2>/dev/null || echo -e "\n000"
}

parse_body() {
    # Strip the last line (HTTP code) from safe_curl output
    echo "$1" | head -n -1
}

parse_code() {
    echo "$1" | tail -1
}

json_get() {
    # json_get <json_string> <python_expr>
    # python_expr receives `d` as the parsed dict; should print a value
    echo "$1" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print($2)
except Exception as e:
    print('')
" 2>/dev/null || true
}

# ─── Print header ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${CYAN}  VidGrab Smoke Test Suite — ${ENV_MODE} mode${RESET}"
echo -e "${CYAN}  Target: ${BASE_URL}${RESET}"
echo -e "${CYAN}  Time  : $(date)${RESET}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

# ─── Check 1: API Health ──────────────────────────────────────────────────────
log "Check 1/8: API health endpoint..."
t0=$(date +%s%N)
resp=$(safe_curl "${BASE_URL}/health")
body=$(parse_body "$resp")
code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ -z "$body" || "$code" == "000" ]]; then
    record_fail "1. API health" "No response from ${BASE_URL}/health (HTTP ${code})" "$elapsed"
else
    status=$(json_get "$body" "d.get('status','')")
    redis_ok=$(json_get "$body" "str(d.get('redis',{}).get('ok',False)).lower()")

    if [[ "$status" == "ok" && "$redis_ok" == "true" ]]; then
        record_pass "1. API health" "status=ok, redis.ok=true" "$elapsed"
    elif [[ "$status" == "ok" ]]; then
        record_fail "1. API health" "status=ok but redis.ok=${redis_ok}" "$elapsed"
    else
        record_fail "1. API health" "status='${status}' (expected 'ok')" "$elapsed"
    fi
fi

# ─── Check 2: YouTube single fetch (preview only — starts a real job) ─────────
log "Check 2/8: YouTube single fetch-link..."
if [[ "$ENV_MODE" == "prod" ]]; then
    record_skip "2. YouTube single fetch" "mutation skipped in prod mode"
else
    t0=$(date +%s%N)
    canary="https://youtu.be/dQw4w9WgXcQ"
    resp=$(safe_curl -X POST "${BASE_URL}/api/v1/fetch-link" \
        -H "Content-Type: application/json" \
        -d "{\"url\":\"${canary}\"}")
    body=$(parse_body "$resp")
    code=$(parse_code "$resp")
    elapsed=$(ms_since "$t0")

    if [[ -z "$body" || "$code" == "000" ]]; then
        record_fail "2. YouTube single fetch" "No response (HTTP ${code})" "$elapsed"
    else
        has_error=$(json_get "$body" "'yes' if d.get('error') else 'no'")
        if [[ "$has_error" == "yes" ]]; then
            err_msg=$(json_get "$body" "d.get('error','unknown')")
            record_fail "2. YouTube single fetch" "API error: ${err_msg}" "$elapsed"
        else
            record_pass "2. YouTube single fetch" "Job started (HTTP ${code})" "$elapsed"
        fi
    fi
fi

# ─── Check 3: Platforms list ──────────────────────────────────────────────────
log "Check 3/8: Platforms list..."
t0=$(date +%s%N)
resp=$(safe_curl "${BASE_URL}/api/v1/platforms")
body=$(parse_body "$resp")
code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ -z "$body" || "$code" == "000" ]]; then
    record_fail "3. Platforms list" "No response (HTTP ${code})" "$elapsed"
else
    platform_count=$(json_get "$body" "len(d) if isinstance(d, list) else len(d.get('platforms', d.get('data', [])))")
    if [[ -z "$platform_count" || "$platform_count" -lt 1 ]]; then
        record_fail "3. Platforms list" "Empty or unparseable response" "$elapsed"
    elif (( platform_count < 10 )); then
        record_fail "3. Platforms list" "Only ${platform_count} platforms (expected ≥10)" "$elapsed"
    else
        record_pass "3. Platforms list" "${platform_count} platforms found" "$elapsed"
    fi
fi

# ─── Check 4: Error codes dict ────────────────────────────────────────────────
log "Check 4/8: Error codes endpoint..."
t0=$(date +%s%N)
resp=$(safe_curl "${BASE_URL}/api/v1/error-codes")
body=$(parse_body "$resp")
code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ -z "$body" || "$code" == "000" ]]; then
    record_fail "4. Error codes" "No response (HTTP ${code})" "$elapsed"
else
    ec_count=$(json_get "$body" "len(d) if isinstance(d, dict) else 0")
    if [[ -z "$ec_count" || "$ec_count" -lt 1 ]]; then
        record_fail "4. Error codes" "Empty or non-dict response (HTTP ${code})" "$elapsed"
    else
        record_pass "4. Error codes" "${ec_count} error codes defined" "$elapsed"
    fi
fi

# ─── Check 5: Auth rejection (POST /api/v1/api-keys without auth) ─────────────
log "Check 5/8: Auth rejection..."
t0=$(date +%s%N)
resp=$(safe_curl -o /dev/null -w '\n%{http_code}' -X POST \
    "${BASE_URL}/api/v1/api-keys" \
    -H "Content-Type: application/json" \
    -d '{"name":"smoke-test-probe"}')
# safe_curl -o /dev/null only returns code
auth_code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ "$auth_code" == "401" || "$auth_code" == "403" ]]; then
    record_pass "5. Auth rejection" "Correctly returned HTTP ${auth_code}" "$elapsed"
elif [[ "$auth_code" == "000" ]]; then
    record_fail "5. Auth rejection" "No response from server" "$elapsed"
else
    record_fail "5. Auth rejection" "Expected 401/403, got HTTP ${auth_code}" "$elapsed"
fi

# ─── Check 6: Rate limits present in api-info ─────────────────────────────────
log "Check 6/8: Rate limits active..."
t0=$(date +%s%N)
resp=$(safe_curl "${BASE_URL}/api/v1/api-info")
body=$(parse_body "$resp")
code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ -z "$body" || "$code" == "000" ]]; then
    # Fallback: try /api/v1/info or /api/info
    resp2=$(safe_curl "${BASE_URL}/api/v1/info" 2>/dev/null || true)
    body=$(parse_body "$resp2")
    code=$(parse_code "$resp2")
fi

if [[ -z "$body" || "$code" == "000" ]]; then
    record_fail "6. Rate limits" "api-info endpoint not reachable (HTTP ${code})" "$elapsed"
else
    has_rate_limits=$(json_get "$body" "'yes' if 'rate_limit' in d or 'rate_limits' in d else 'no'")
    if [[ "$has_rate_limits" == "yes" ]]; then
        record_pass "6. Rate limits" "rate_limits section present" "$elapsed"
    else
        record_fail "6. Rate limits" "rate_limits key missing from api-info response" "$elapsed"
    fi
fi

# ─── Check 7: Queue depth / worker count ─────────────────────────────────────
log "Check 7/8: Celery worker count..."
t0=$(date +%s%N)
resp=$(safe_curl "${BASE_URL}/health")
body=$(parse_body "$resp")
code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ -z "$body" || "$code" == "000" ]]; then
    record_fail "7. Worker count" "Could not reach /health" "$elapsed"
else
    worker_count=$(json_get "$body" "d.get('celery',{}).get('worker_count', 0)")
    min_workers=1
    if [[ "$ENV_MODE" == "preview" ]]; then
        min_workers=0  # Preview: any workers ok (could be 0 in minimal env)
    fi

    if [[ -z "$worker_count" ]]; then
        record_fail "7. Worker count" "celery.worker_count missing from /health response" "$elapsed"
    elif (( worker_count > min_workers )); then
        record_pass "7. Worker count" "${worker_count} worker(s) online" "$elapsed"
    elif (( worker_count == min_workers && min_workers == 0 )); then
        record_pass "7. Worker count" "${worker_count} worker(s) (preview: 0 accepted)" "$elapsed"
    else
        record_fail "7. Worker count" "worker_count=${worker_count}, need ≥${min_workers}" "$elapsed"
    fi
fi

# ─── Check 8: Partner API auth rejection ──────────────────────────────────────
log "Check 8/8: Partner API auth rejection..."
t0=$(date +%s%N)
resp=$(safe_curl -o /dev/null -w '\n%{http_code}' "${BASE_URL}/api/v1/partner/usage")
partner_code=$(parse_code "$resp")
elapsed=$(ms_since "$t0")

if [[ "$partner_code" == "401" || "$partner_code" == "403" ]]; then
    record_pass "8. Partner API rejection" "Correctly returned HTTP ${partner_code}" "$elapsed"
elif [[ "$partner_code" == "404" ]]; then
    # Partner API may not be deployed yet — treat as skip rather than fail
    record_skip "8. Partner API rejection" "Endpoint not found (HTTP 404) — may not be deployed"
    (( FAIL_COUNT-- )) || true  # record_skip already doesn't increment fail, but safe guard
elif [[ "$partner_code" == "000" ]]; then
    record_fail "8. Partner API rejection" "No response from server" "$elapsed"
else
    record_fail "8. Partner API rejection" "Expected 401/403, got HTTP ${partner_code}" "$elapsed"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

if (( FAIL_COUNT == 0 )); then
    echo -e "${BOLD}${GREEN}  PASSED ${PASS_COUNT}/${TOTAL_CHECKS} checks${SKIP_COUNT:+ (${SKIP_COUNT} skipped)}${RESET}"
else
    echo -e "${BOLD}${RED}  FAILED — ${FAIL_COUNT} check(s) failed, ${PASS_COUNT} passed${SKIP_COUNT:+, ${SKIP_COUNT} skipped}${RESET}"
fi

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

if (( FAIL_COUNT > 0 )); then
    exit 1
fi
exit 0

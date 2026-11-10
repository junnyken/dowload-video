#!/usr/bin/env bash
# deploy-vps.sh — Deploy VidGrab lên Oracle VPS (dowloadvideo.io.vn)
# Usage:
#   ./deploy-vps.sh --confirm-migration-044                  # full deploy with pre/post checks
#   ./deploy-vps.sh --confirm-migration-044 --dry-run        # pre-checks only, no deploy
#   ./deploy-vps.sh --confirm-migration-044 --no-build       # docker compose up -d without rebuild
#   ./deploy-vps.sh --confirm-migration-044 --skip-pre-check # emergency deploy, skip pre-checks
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

# ── Config ─────────────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VPS="vidgrab"
VPS_DIR="/home/ubuntu/vidgrab"
BUNDLE="/tmp/vidgrab_deploy_$$.bundle"
DEPLOYMENTS_LOG="$REPO_DIR/deployments.json"

# ── Flags ──────────────────────────────────────────────────────────────────────
DRY_RUN=false
NO_BUILD=false
SKIP_PRE_CHECK=false
CONFIRM_MIGRATION_044=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)                DRY_RUN=true ;;
    --no-build)               NO_BUILD=true ;;
    --skip-pre-check)         SKIP_PRE_CHECK=true ;;
    --confirm-migration-044)  CONFIRM_MIGRATION_044=true ;;
    *) echo -e "${RED}Unknown flag: $arg${RESET}"; exit 1 ;;
  esac
done

# ── Helpers ────────────────────────────────────────────────────────────────────
ts()  { date '+%H:%M:%S'; }
log() { echo -e "${CYAN}[$(ts)]${RESET} $*"; }
ok()  { echo -e "${GREEN}[$(ts)] ✓${RESET} $*"; }
warn(){ echo -e "${YELLOW}[$(ts)] ⚠${RESET} $*"; }
err() { echo -e "${RED}[$(ts)] ✗${RESET} $*"; }

DEPLOY_START=$(date +%s)

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  VidGrab Deploy  —  $(date '+%Y-%m-%d %H:%M:%S')${RESET}"
if $DRY_RUN;        then echo -e "${YELLOW}  Mode: DRY RUN (pre-checks only, no deploy)${RESET}"; fi
if $NO_BUILD;       then echo -e "${YELLOW}  Mode: NO BUILD (config-only, no image rebuild)${RESET}"; fi
if $SKIP_PRE_CHECK; then echo -e "${YELLOW}  Mode: SKIP PRE-CHECK (emergency deploy)${RESET}"; fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

# ── Metadata ───────────────────────────────────────────────────────────────────
cd "$REPO_DIR"
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
PRE_CHECK_RESULT="skipped"
POST_CHECK_RESULT="skipped"

log "Git: ${BOLD}${GIT_BRANCH}${RESET} @ ${GIT_SHA}"

# ── Step 0: Mandatory preflight guard (migration 044 + read-only VPS .env check) ──
if ! $CONFIRM_MIGRATION_044; then
  err "Thiếu cờ xác nhận migration: --confirm-migration-044"
  echo ""
  echo -e "${RED}${BOLD}DEPLOY DỪNG LẠI — Chưa xác nhận đã chạy migration 044 trên Supabase.${RESET}"
  echo -e "  1. Chạy file: ${CYAN}database/migrations/044_flow_consent_audit_logs.sql${RESET} trên Supabase SQL Editor / psql."
  echo -e "  2. Chạy lại lệnh deploy kèm cờ xác nhận:"
  echo -e "     ${CYAN}./deploy-vps.sh --confirm-migration-044${RESET}"
  exit 1
fi
ok "Đã xác nhận cờ --confirm-migration-044"

log "Checking VPS .env preflight requirements (read-only via SSH)..."
if ! ssh "$VPS" "VPS_DIR='$VPS_DIR' bash -s" << 'PREFLIGHT_EOF'
set -euo pipefail
ENV_FILE="${VPS_DIR}/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[PREFLIGHT FAIL] Không tìm thấy file $ENV_FILE trên VPS."
  exit 1
fi

# Helper: read raw value of KEY from .env (last occurrence, strip CR and surrounding quotes)
get_env_val() {
  local key="$1"
  local line
  line=$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n 1 || true)
  if [ -z "$line" ]; then
    echo ""
    return
  fi
  local val="${line#*=}"
  val="${val%$'\r'}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  printf '%s' "$val"
}

FAIL=0

# 1. FLOW_URL_SIGNING_SECRET >= 32 chars (NEVER print the value)
FLOW_SECRET=$(get_env_val "FLOW_URL_SIGNING_SECRET")
FLOW_LEN=${#FLOW_SECRET}
if [ "$FLOW_LEN" -ge 32 ]; then
  echo "[PREFLIGHT OK]   FLOW_URL_SIGNING_SECRET: có (độ dài = ${FLOW_LEN} ký tự)"
elif [ "$FLOW_LEN" -gt 0 ]; then
  echo "[PREFLIGHT FAIL] FLOW_URL_SIGNING_SECRET: có nhưng quá ngắn (độ dài = ${FLOW_LEN} < 32 ký tự)"
  FAIL=1
else
  echo "[PREFLIGHT FAIL] FLOW_URL_SIGNING_SECRET: không (độ dài = 0)"
  FAIL=1
fi

# 2. VITE_FEATURE_LOGO_INPAINT must NOT be true or 1
VITE_FLAG=$(get_env_val "VITE_FEATURE_LOGO_INPAINT")
VITE_FLAG_LOWER=$(printf '%s' "$VITE_FLAG" | tr '[:upper:]' '[:lower:]')
if [ "$VITE_FLAG_LOWER" = "true" ] || [ "$VITE_FLAG_LOWER" = "1" ]; then
  echo "[PREFLIGHT FAIL] VITE_FEATURE_LOGO_INPAINT đang bật (=${VITE_FLAG}) trong $ENV_FILE — phải tắt hoặc xoá trước khi deploy."
  FAIL=1
else
  echo "[PREFLIGHT OK]   VITE_FEATURE_LOGO_INPAINT: không bật"
fi

# 3. REDIS_URL (or CELERY_BROKER_URL / REDIS_HOST) must exist
REDIS_VAL=$(get_env_val "REDIS_URL")
REDIS_VAR_NAME="REDIS_URL"
if [ -z "$REDIS_VAL" ]; then
  REDIS_VAL=$(get_env_val "CELERY_BROKER_URL")
  REDIS_VAR_NAME="CELERY_BROKER_URL"
fi
if [ -z "$REDIS_VAL" ]; then
  REDIS_VAL=$(get_env_val "REDIS_HOST")
  REDIS_VAR_NAME="REDIS_HOST"
fi
if [ -n "$REDIS_VAL" ]; then
  echo "[PREFLIGHT OK]   Redis biến môi trường (${REDIS_VAR_NAME}): có"
else
  echo "[PREFLIGHT FAIL] Thiếu cấu hình Redis (REDIS_URL / CELERY_BROKER_URL / REDIS_HOST) trong $ENV_FILE"
  FAIL=1
fi

exit "$FAIL"
PREFLIGHT_EOF
then
  err "VPS .env preflight check FAILED"
  echo ""
  echo -e "${RED}${BOLD}DEPLOY DỪNG LẠI — Điều kiện .env trên VPS chưa đạt yêu cầu an toàn.${RESET}"
  exit 1
fi
ok "VPS .env preflight checks passed"

# ── Step 1: Pre-deploy checks ──────────────────────────────────────────────────
if $SKIP_PRE_CHECK; then
  warn "Pre-deploy checks SKIPPED (--skip-pre-check flag)"
  PRE_CHECK_RESULT="skipped"
else
  log "Running pre-deploy checks on VPS..."
  if ssh "$VPS" "test -f $VPS_DIR/scripts/deploy-check.sh && bash $VPS_DIR/scripts/deploy-check.sh pre"; then
    ok "Pre-deploy checks passed"
    PRE_CHECK_RESULT="passed"
  else
    PRE_CHECK_RESULT="failed"
    err "Pre-deploy checks FAILED"
    echo ""
    echo -e "${RED}${BOLD}DEPLOY ABORTED — pre-checks did not pass.${RESET}"
    echo -e "  Fix the issues above, then re-run: ${CYAN}./deploy-vps.sh${RESET}"
    echo -e "  To bypass (emergency only): ${CYAN}./deploy-vps.sh --skip-pre-check${RESET}"
    # Save failed pre-check to log
    _record_deploy() {
      local ts; ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
      local entry="{\"timestamp\":\"$ts\",\"git_sha\":\"$GIT_SHA\",\"git_branch\":\"$GIT_BRANCH\",\"pre_check\":\"$PRE_CHECK_RESULT\",\"post_check\":\"$POST_CHECK_RESULT\",\"status\":\"aborted\"}"
      if [ -f "$DEPLOYMENTS_LOG" ]; then
        # Append to JSON array
        python3 -c "
import json,sys
f='$DEPLOYMENTS_LOG'
data=json.load(open(f))
data.append(json.loads('$entry'))
json.dump(data,open(f,'w'),indent=2)
" 2>/dev/null || true
      else
        echo "[$entry]" > "$DEPLOYMENTS_LOG"
      fi
    }
    _record_deploy
    exit 1
  fi
fi

# ── Dry run exits here ─────────────────────────────────────────────────────────
if $DRY_RUN; then
  echo ""
  ok "DRY RUN complete — pre-checks passed. No changes deployed."
  exit 0
fi

# ── Step 2: Save current image state for rollback ──────────────────────────────
log "Saving current image state for rollback..."
ssh "$VPS" "cd $VPS_DIR && docker compose images --format json > /tmp/vidgrab_last_images.json 2>/dev/null || true" \
  && ok "Rollback snapshot saved to /tmp/vidgrab_last_images.json" \
  || warn "Could not save image snapshot (non-fatal)"

# ── Step 3: Bundle + upload ────────────────────────────────────────────────────
log "Packaging code..."
git bundle create "$BUNDLE" HEAD
SIZE=$(du -sh "$BUNDLE" | cut -f1)
ok "Bundle ready: ${BOLD}${SIZE}${RESET}"

log "Uploading to VPS..."
scp -q "$BUNDLE" "$VPS:/tmp/vidgrab_deploy.bundle"
rm -f "$BUNDLE"
ok "Upload complete"

# ── Step 4: Remote deploy ──────────────────────────────────────────────────────
log "Deploying on VPS..."

if $NO_BUILD; then
  COMPOSE_CMD="docker compose up -d"
  log "No-build mode: skipping image rebuild"
else
  COMPOSE_CMD="docker compose up --build -d"
fi

ssh "$VPS" bash << REMOTE
set -e

VPS_DIR="/home/ubuntu/vidgrab"

# Backup .env
cp "\$VPS_DIR/.env" /tmp/.env.vps.bak 2>/dev/null || true

# Clone from bundle into temp dir
rm -rf /tmp/vidgrab_new
git clone /tmp/vidgrab_deploy.bundle /tmp/vidgrab_new 2>/dev/null

# Rsync code (preserve .env, .git, downloads, backups)
rsync -a --delete \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='downloads/' \
  --exclude='backups/' \
  --exclude='*.pem' \
  /tmp/vidgrab_new/ "\$VPS_DIR/"

# Restore .env
cp /tmp/.env.vps.bak "\$VPS_DIR/.env" 2>/dev/null || true

# Cleanup bundle
rm -rf /tmp/vidgrab_new /tmp/vidgrab_deploy.bundle

# Rebuild & restart
cd "\$VPS_DIR"
$COMPOSE_CMD
REMOTE

ok "Remote deploy command completed"

# ── Step 5: Post-deploy checks ─────────────────────────────────────────────────
log "Waiting 12s for services to stabilise..."
sleep 12

log "Running post-deploy checks on VPS..."
if ssh "$VPS" "test -f $VPS_DIR/scripts/deploy-check.sh && bash $VPS_DIR/scripts/deploy-check.sh post"; then
  ok "Post-deploy checks passed"
  POST_CHECK_RESULT="passed"
  DEPLOY_STATUS="success"
else
  POST_CHECK_RESULT="failed"
  DEPLOY_STATUS="failed"
  echo ""
  echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${RED}${BOLD}  POST-DEPLOY CHECKS FAILED — ROLLBACK SUGGESTED  ${RESET}"
  echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  echo -e "  Option 1 (recommended): ${CYAN}./scripts/rollback.sh${RESET}"
  echo -e "  Option 2 (manual):      ${CYAN}ssh vidgrab 'cd $VPS_DIR && docker compose down && docker compose up -d --no-build'${RESET}"
  echo -e "  Check logs:             ${CYAN}ssh vidgrab 'docker compose logs backend --tail=50'${RESET}"
  echo ""
fi

# ── Step 6: Container status ───────────────────────────────────────────────────
log "Container status:"
ssh "$VPS" "cd $VPS_DIR && docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'" || true

# ── Step 7: Save deploy metadata ───────────────────────────────────────────────
DEPLOY_END=$(date +%s)
DURATION=$(( DEPLOY_END - DEPLOY_START ))
DEPLOY_TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

ENTRY="{\"timestamp\":\"$DEPLOY_TS\",\"git_sha\":\"$GIT_SHA\",\"git_branch\":\"$GIT_BRANCH\",\"duration_sec\":$DURATION,\"pre_check\":\"$PRE_CHECK_RESULT\",\"post_check\":\"$POST_CHECK_RESULT\",\"status\":\"$DEPLOY_STATUS\",\"no_build\":$NO_BUILD,\"skip_pre_check\":$SKIP_PRE_CHECK}"

if [ -f "$DEPLOYMENTS_LOG" ]; then
  python3 -c "
import json
f='$DEPLOYMENTS_LOG'
data=json.load(open(f))
data.append(json.loads('''$ENTRY'''))
# Keep last 50 deploys
if len(data) > 50:
    data = data[-50:]
json.dump(data,open(f,'w'),indent=2)
" 2>/dev/null || warn "Could not update deployments.json (non-fatal)"
else
  echo "[$ENTRY]" > "$DEPLOYMENTS_LOG"
fi
ok "Deploy metadata saved → deployments.json"

# ── Step 8: Final summary ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
if [ "$DEPLOY_STATUS" = "success" ]; then
  echo -e "${GREEN}${BOLD}  DEPLOY SUCCESSFUL${RESET}  (${DURATION}s)"
  echo -e "  Branch:     ${GIT_BRANCH} @ ${GIT_SHA}"
  echo -e "  Pre-check:  ${GREEN}${PRE_CHECK_RESULT}${RESET}"
  echo -e "  Post-check: ${GREEN}${POST_CHECK_RESULT}${RESET}"
  echo -e "  URL:        ${CYAN}https://dowloadvideo.io.vn${RESET}"
else
  echo -e "${RED}${BOLD}  DEPLOY FAILED${RESET}  (${DURATION}s)"
  echo -e "  Branch:     ${GIT_BRANCH} @ ${GIT_SHA}"
  echo -e "  Pre-check:  ${PRE_CHECK_RESULT}"
  echo -e "  Post-check: ${RED}${POST_CHECK_RESULT}${RESET}"
  echo -e "  Action:     Run ${CYAN}./scripts/rollback.sh${RESET} to revert"
fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

[ "$DEPLOY_STATUS" = "success" ] && exit 0 || exit 1

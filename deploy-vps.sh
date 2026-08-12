#!/usr/bin/env bash
# deploy-vps.sh — Deploy VidGrab lên Oracle VPS (dowloadvideo.io.vn)
# Usage:
#   ./deploy-vps.sh                  # full deploy with pre/post checks
#   ./deploy-vps.sh --dry-run        # pre-checks only, no deploy
#   ./deploy-vps.sh --no-build       # docker compose up -d without rebuild
#   ./deploy-vps.sh --skip-pre-check # emergency deploy, skip pre-checks
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

for arg in "$@"; do
  case "$arg" in
    --dry-run)        DRY_RUN=true ;;
    --no-build)       NO_BUILD=true ;;
    --skip-pre-check) SKIP_PRE_CHECK=true ;;
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

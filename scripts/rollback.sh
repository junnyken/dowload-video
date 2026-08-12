#!/usr/bin/env bash
# rollback.sh — Roll back VidGrab to the previous Docker image version
# Usage: ./scripts/rollback.sh [service_name]
#   service_name: backend|celery|all (default: all)
#
# Strategy:
#   Before each deploy, the current image digest is saved to:
#     /tmp/vidgrab_last_image.txt  (format: service=image_id, one per line)
#   Rollback re-tags that digest and restarts the service.
#
# ⚠  WARNING: This is a best-effort rollback. Verify the service manually
#    after completion. Database schema changes are NOT reversed.
set -euo pipefail

# ─── ANSI colours ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Config ──────────────────────────────────────────────────────────────────
VPS_DIR="${VPS_DIR:-/home/ubuntu/vidgrab}"
IMAGE_RECORD="/tmp/vidgrab_last_image.txt"
COMPOSE_FILE="${VPS_DIR}/docker-compose.yml"
COMPOSE_ROLLBACK_FILE="/tmp/vidgrab_docker-compose.rollback.yml"
POST_CHECK_WAIT=15  # seconds to let services stabilise before post-check

# Map of service name → docker compose service name → container prefix
declare -A SERVICE_MAP=(
    [backend]="backend"
    [celery]="celery"
    [all]="backend celery celery-light celery-beat"
)

# ─── Helpers ─────────────────────────────────────────────────────────────────
log()     { echo -e "${CYAN}[$(date '+%H:%M:%S')]${RESET} $*"; }
success() { echo -e "${GREEN}[$(date '+%H:%M:%S')] ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${RESET} $*"; }
err()     { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${RESET} $*" >&2; }
die()     { err "$*"; exit 1; }

# ─── Save current image IDs (call this BEFORE docker compose up --build) ─────
save_current_images() {
    log "Saving current image IDs to ${IMAGE_RECORD}..."
    > "$IMAGE_RECORD"

    local services=("backend" "celery" "celery-light" "celery-beat")
    for svc in "${services[@]}"; do
        # docker compose images prints: Service  Image  ID  Size
        local image_id
        image_id=$(cd "$VPS_DIR" && docker compose images --format json "$svc" 2>/dev/null \
            | python3 -c "import sys,json; rows=json.load(sys.stdin); print(rows[0]['ID'] if rows else '')" \
            2>/dev/null || true)

        if [[ -n "$image_id" && "$image_id" != "null" ]]; then
            echo "${svc}=${image_id}" >> "$IMAGE_RECORD"
            log "  Recorded ${svc}: ${image_id}"
        else
            warn "  Could not find image for service '${svc}' — it may not be running yet"
        fi
    done

    if [[ ! -s "$IMAGE_RECORD" ]]; then
        warn "No image IDs recorded. First deploy? Rollback via git stash only."
    else
        success "Image IDs saved to ${IMAGE_RECORD}"
    fi
}

# ─── Read saved image ID for a service ───────────────────────────────────────
get_saved_image() {
    local svc="$1"
    if [[ ! -f "$IMAGE_RECORD" ]]; then
        echo ""
        return
    fi
    grep -E "^${svc}=" "$IMAGE_RECORD" | cut -d= -f2 || true
}

# ─── Rollback a single service via re-tagged image ───────────────────────────
rollback_service_image() {
    local svc="$1"
    local old_image_id
    old_image_id=$(get_saved_image "$svc")

    if [[ -z "$old_image_id" ]]; then
        warn "No saved image ID for '${svc}'. Falling back to git stash rollback."
        return 1
    fi

    log "Rolling back '${svc}' to image ${old_image_id}..."

    # Verify image still exists locally
    if ! docker inspect --type image "$old_image_id" >/dev/null 2>&1; then
        warn "Image ${old_image_id} no longer exists locally. Falling back to git stash rollback."
        return 1
    fi

    # Tag it as a rollback image
    local rollback_tag="vidgrab-${svc}:rollback"
    docker tag "$old_image_id" "$rollback_tag"
    success "Tagged ${old_image_id} as ${rollback_tag}"

    # Patch compose file to use the rollback image instead of build
    # We do this via a temporary override file (compose override pattern)
    cat > "$COMPOSE_ROLLBACK_FILE" <<OVERRIDE
services:
  ${svc}:
    build: !reset null
    image: ${rollback_tag}
OVERRIDE

    log "Restarting '${svc}' with rollback image..."
    cd "$VPS_DIR"
    docker compose -f docker-compose.yml -f "$COMPOSE_ROLLBACK_FILE" up -d "$svc"
    success "Service '${svc}' restarted with rollback image."
    return 0
}

# ─── Fallback: git stash + redeploy ──────────────────────────────────────────
rollback_via_git() {
    local services_arg="$1"  # space-separated list

    warn "Attempting git stash rollback (code-level only)..."
    cd "$VPS_DIR"

    # Check if there's a stash
    if ! git stash list | head -1 | grep -q 'stash@'; then
        die "No git stash available and no saved Docker image. Cannot rollback."
    fi

    log "Popping git stash..."
    git stash pop

    log "Rebuilding and restarting services: ${services_arg}..."
    # shellcheck disable=SC2086
    docker compose up --build -d $services_arg
    success "Git stash rollback complete. Services restarted."
}

# ─── Run post-check after rollback ───────────────────────────────────────────
run_post_check() {
    local check_script
    check_script="$(dirname "$0")/deploy-check.sh"

    if [[ ! -x "$check_script" ]]; then
        warn "deploy-check.sh not found or not executable at ${check_script}. Skipping post-check."
        return 0
    fi

    log "Waiting ${POST_CHECK_WAIT}s for services to stabilise..."
    sleep "$POST_CHECK_WAIT"

    log "Running post-deploy health checks..."
    if bash "$check_script" post; then
        success "Post-rollback checks PASSED."
    else
        err "Post-rollback checks FAILED. Manual intervention may be needed."
        return 1
    fi
}

# ─── Main rollback orchestration ─────────────────────────────────────────────
main() {
    local target="${1:-all}"

    echo ""
    echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}${YELLOW}  VidGrab Rollback — target: ${target}${RESET}"
    echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${YELLOW}  ⚠  This is a best-effort rollback.${RESET}"
    echo -e "${YELLOW}  ⚠  DB schema changes are NOT reversed.${RESET}"
    echo -e "${YELLOW}  ⚠  Verify the service manually after completion.${RESET}"
    echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""

    # Validate target
    if [[ -z "${SERVICE_MAP[$target]+_}" ]]; then
        die "Unknown target '${target}'. Use: backend | celery | all"
    fi

    # Determine services to roll back
    local services_str="${SERVICE_MAP[$target]}"
    read -ra services <<< "$services_str"

    log "Services to roll back: ${services[*]}"

    # Track if any service needed git fallback
    local git_fallback_services=()
    local success_services=()

    for svc in "${services[@]}"; do
        echo ""
        log "── Rolling back service: ${svc} ──"
        if rollback_service_image "$svc"; then
            success_services+=("$svc")
        else
            git_fallback_services+=("$svc")
        fi
    done

    # If any services need git fallback, do it in one batch
    if (( ${#git_fallback_services[@]} > 0 )); then
        echo ""
        warn "Services needing git fallback: ${git_fallback_services[*]}"
        rollback_via_git "${git_fallback_services[*]}"
    fi

    # Clean up temporary rollback override file
    [[ -f "$COMPOSE_ROLLBACK_FILE" ]] && rm -f "$COMPOSE_ROLLBACK_FILE" && log "Cleaned up rollback override file."

    echo ""
    # Run post-checks
    if ! run_post_check; then
        echo ""
        echo -e "${RED}${BOLD}Rollback completed but health checks failed.${RESET}"
        echo -e "${RED}Manual verification required. Check logs with:${RESET}"
        echo -e "  ${CYAN}ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose logs --tail=50'${RESET}"
        exit 1
    fi

    echo ""
    echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${GREEN}${BOLD}  Rollback COMPLETE. Rolled back: ${success_services[*]:-} ${git_fallback_services[*]:-}${RESET}"
    echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
}

# ─── Sub-command: save (used by deploy-vps.sh before building) ───────────────
# Usage: ./scripts/rollback.sh save
if [[ "${1:-}" == "save" ]]; then
    save_current_images
    exit 0
fi

main "${1:-all}"

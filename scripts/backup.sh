#!/usr/bin/env bash
# backup.sh — Backup VidGrab critical state (Redis data + configs)
# Usage: ./scripts/backup.sh [--destination /path/to/backup/dir]
# Typically run daily via cron on VPS.
#
# Add to cron: 0 2 * * * /home/ubuntu/vidgrab/scripts/backup.sh >> /var/log/vidgrab-backup.log 2>&1
#
# What is backed up:
#   - Redis dump.rdb  (via BGSAVE + docker volume copy)
#   - Redis appendonly.aof  (if it exists)
#   - Env var KEY NAMES only  (never values — secrets stay out of backups)
#   - Latest migration filename
#
# Retention: last 7 daily backups (older ones pruned automatically).
set -euo pipefail

# ─── ANSI colours ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Config ──────────────────────────────────────────────────────────────────
DEFAULT_DEST="/home/ubuntu/backups/vidgrab"
VPS_DIR="${VPS_DIR:-/home/ubuntu/vidgrab}"
REDIS_VOLUME_NAME="${REDIS_VOLUME_NAME:-vidgrab_redis-data}"   # docker volume name
KEEP_DAYS=7

# ─── Parse args ──────────────────────────────────────────────────────────────
DEST="$DEFAULT_DEST"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --destination|-d)
            DEST="$2"; shift 2 ;;
        --help|-h)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
            exit 0 ;;
        *)
            echo -e "${RED}Unknown argument: $1${RESET}" >&2; exit 1 ;;
    esac
done

# ─── Helpers ─────────────────────────────────────────────────────────────────
log()     { echo -e "${CYAN}[$(date '+%H:%M:%S')]${RESET} $*"; }
success() { echo -e "${GREEN}[$(date '+%H:%M:%S')] ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${RESET} $*"; }
err()     { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${RESET} $*" >&2; }
die()     { err "$*"; exit 1; }

human_size() {
    # Print human-readable size for a file path
    du -sh "$1" 2>/dev/null | cut -f1 || echo "?"
}

# ─── Setup ───────────────────────────────────────────────────────────────────
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_STAGING="/tmp/vidgrab_backup_${TIMESTAMP}"
ARCHIVE_NAME="vidgrab_backup_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="${DEST}/${ARCHIVE_NAME}"

echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${CYAN}  VidGrab Backup — $(date)${RESET}"
echo -e "${BOLD}${CYAN}  Destination: ${DEST}${RESET}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

mkdir -p "$DEST" "$BACKUP_STAGING"

# ─── 1. Trigger Redis BGSAVE ─────────────────────────────────────────────────
backup_redis() {
    log "Step 1/5: Backing up Redis data..."

    # Find redis container
    local redis_cname
    redis_cname=$(docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -E '^redis$|vidgrab-redis|vidgrab_redis' | head -1 || true)

    if [[ -z "$redis_cname" ]]; then
        warn "Redis container not found. Attempting cold copy via volume mount."
    else
        log "  Triggering BGSAVE on container '${redis_cname}'..."
        docker exec "$redis_cname" redis-cli BGSAVE >/dev/null 2>&1 || warn "BGSAVE command failed (non-fatal)"

        # Wait for BGSAVE to complete (poll LASTSAVE)
        local last_save_before last_save_after
        last_save_before=$(docker exec "$redis_cname" redis-cli LASTSAVE 2>/dev/null || echo "0")
        local attempts=0
        while true; do
            sleep 1
            last_save_after=$(docker exec "$redis_cname" redis-cli LASTSAVE 2>/dev/null || echo "0")
            if [[ "$last_save_after" != "$last_save_before" ]]; then
                log "  BGSAVE completed (LASTSAVE updated)."
                break
            fi
            (( attempts++ ))
            if (( attempts >= 30 )); then
                warn "  BGSAVE did not complete within 30s. Proceeding with current dump."
                break
            fi
        done
    fi

    # Copy dump.rdb from the Redis volume using a transient Alpine container
    log "  Copying dump.rdb from volume '${REDIS_VOLUME_NAME}'..."
    if docker run --rm \
        -v "${REDIS_VOLUME_NAME}:/data:ro" \
        -v "${BACKUP_STAGING}:/backup" \
        alpine sh -c "cp /data/dump.rdb /backup/redis_${TIMESTAMP}.rdb 2>/dev/null && echo ok || echo missing" \
        2>/dev/null | grep -q "^ok$"; then
        success "  dump.rdb → redis_${TIMESTAMP}.rdb ($(human_size "${BACKUP_STAGING}/redis_${TIMESTAMP}.rdb"))"
    else
        warn "  dump.rdb not found in volume (Redis may have AOF-only persistence)"
    fi

    # Copy AOF if it exists
    log "  Checking for appendonly.aof..."
    if docker run --rm \
        -v "${REDIS_VOLUME_NAME}:/data:ro" \
        -v "${BACKUP_STAGING}:/backup" \
        alpine sh -c "[ -f /data/appendonly.aof ] && cp /data/appendonly.aof /backup/redis_aof_${TIMESTAMP}.aof && echo ok || echo missing" \
        2>/dev/null | grep -q "^ok$"; then
        success "  appendonly.aof → redis_aof_${TIMESTAMP}.aof ($(human_size "${BACKUP_STAGING}/redis_aof_${TIMESTAMP}.aof"))"
    else
        log "  appendonly.aof not present — skipping."
    fi
}

# ─── 2. Backup env var key names (NOT values) ────────────────────────────────
backup_env_keys() {
    log "Step 2/5: Recording .env key names (values are NOT saved)..."

    local env_file="${VPS_DIR}/.env"
    local keys_file="${BACKUP_STAGING}/env_keys_${TIMESTAMP}.txt"

    if [[ ! -f "$env_file" ]]; then
        warn "  .env not found at ${env_file}. Skipping."
        return
    fi

    {
        echo "# VidGrab .env key inventory — $(date)"
        echo "# VALUES ARE NOT STORED HERE — keys only"
        echo ""
        grep -E '^[A-Z_]+=' "$env_file" \
            | cut -d= -f1 \
            | sort
    } > "$keys_file"

    local key_count
    key_count=$(grep -c '^[A-Z_]' "$keys_file" || true)
    success "  ${key_count} env key names saved → env_keys_${TIMESTAMP}.txt"
}

# ─── 3. Save latest migration record ─────────────────────────────────────────
backup_migration_record() {
    log "Step 3/5: Recording latest migration..."

    local migration_dir="${VPS_DIR}/database"
    [[ ! -d "$migration_dir" ]] && migration_dir="${VPS_DIR}/migrations"

    local record_file="${BACKUP_STAGING}/migration_record_${TIMESTAMP}.txt"

    if [[ -d "$migration_dir" ]]; then
        local latest_sql
        latest_sql=$(find "$migration_dir" -name "*.sql" | sort | tail -1)
        if [[ -n "$latest_sql" ]]; then
            local latest_basename
            latest_basename=$(basename "$latest_sql")
            {
                echo "# VidGrab migration record — $(date)"
                echo "latest_migration=${latest_basename}"
                echo "migration_dir=${migration_dir}"
                echo ""
                echo "# All migrations:"
                find "$migration_dir" -name "*.sql" | sort | xargs -I{} basename {}
            } > "$record_file"
            success "  Latest migration: ${latest_basename}"
        else
            echo "# No .sql files found in ${migration_dir}" > "$record_file"
            warn "  No SQL migration files found in ${migration_dir}"
        fi
    else
        echo "# No migrations directory found" > "$record_file"
        warn "  No migrations directory found (checked database/ and migrations/)"
    fi
}

# ─── 4. Compress staging dir ─────────────────────────────────────────────────
compress_backup() {
    log "Step 4/5: Compressing backup..."

    tar -czf "$ARCHIVE_PATH" -C "$(dirname "$BACKUP_STAGING")" "$(basename "$BACKUP_STAGING")"

    local archive_size
    archive_size=$(human_size "$ARCHIVE_PATH")
    success "  Archive: ${ARCHIVE_PATH} (${archive_size})"
}

# ─── 5. Prune old backups ─────────────────────────────────────────────────────
prune_old_backups() {
    log "Step 5/5: Pruning backups older than ${KEEP_DAYS} days..."

    local pruned=0
    while IFS= read -r -d '' old_archive; do
        log "  Removing: $(basename "$old_archive")"
        rm -f "$old_archive"
        (( pruned++ ))
    done < <(find "$DEST" -maxdepth 1 -name "vidgrab_backup_*.tar.gz" \
        -mtime "+${KEEP_DAYS}" -print0 2>/dev/null)

    if (( pruned > 0 )); then
        success "  Pruned ${pruned} old archive(s)."
    else
        log "  No archives older than ${KEEP_DAYS} days found."
    fi

    # List remaining archives
    local remaining
    remaining=$(find "$DEST" -maxdepth 1 -name "vidgrab_backup_*.tar.gz" | wc -l)
    log "  Remaining archives in ${DEST}: ${remaining}"
}

# ─── Cleanup staging dir ─────────────────────────────────────────────────────
cleanup() {
    rm -rf "$BACKUP_STAGING"
}
trap cleanup EXIT

# ─── Run all steps ───────────────────────────────────────────────────────────
backup_redis
backup_env_keys
backup_migration_record
compress_backup
prune_old_backups

# ─── Final report ────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}${GREEN}  Backup COMPLETE${RESET}"
echo -e "${GREEN}  Archive : ${ARCHIVE_PATH}${RESET}"
echo -e "${GREEN}  Size    : $(human_size "$ARCHIVE_PATH")${RESET}"
echo -e "${GREEN}  Time    : $(date)${RESET}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

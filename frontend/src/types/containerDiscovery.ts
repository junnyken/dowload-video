/**
 * Container Discovery Types — Phase 25 PR2
 * Mirrors backend/app/schemas/container_discovery.py
 *
 * These types are for the DISCOVERY JOB lifecycle only.
 * Container data types (ContainerMeta, LeafMediaItem, etc.) live in container.ts
 */

// ── Enums ─────────────────────────────────────────────────────────────────────

export type DiscoveryJobStatus =
  | "queued"
  | "resolving"
  | "discovering"
  | "expanding"
  | "partial"
  | "success"
  | "failed"
  | "expired";

export type DiscoveryStage =
  | "normalize_input"
  | "resolve_capability"
  | "load_cache"
  | "fetch_summary"
  | "fetch_preview"
  | "assemble_sections"
  | "finalize"
  | "expand_section";

// ── Component models ──────────────────────────────────────────────────────────

export interface DiscoveryError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface DiscoveryStats {
  sections_ready:  number;
  sections_total:  number;
  items_loaded:    number;
  items_estimated: number;
  cache_hits:      number;
  expandables:     number;
}

// ── Main snapshot — polled from GET /discover-container/{job_id} ──────────────

export interface DiscoveryJobSnapshot {
  job_id:        string;
  container_id:  string;
  platform:      string;
  source_type:   string;
  status:        DiscoveryJobStatus;
  progress_pct:  number;
  stage:         DiscoveryStage;
  message:       string;
  created_at:    number;
  updated_at:    number;
  expires_at:    number;
  from_cache:    boolean;
  partial:       boolean;
  warnings:      string[];

  /** Inline container summary (subset of ContainerMeta) */
  summary:       Record<string, unknown> | null;
  /** First 5 items per section for preview (full data via GET /container/{id}) */
  sections:      Record<string, unknown>[];
  stats:         DiscoveryStats;
  error:         DiscoveryError | null;

  canonical_url: string;
  raw_input:     string;
}

// ── Request schemas ───────────────────────────────────────────────────────────

export interface DiscoverContainerRequest {
  url?: string;
  raw_input?: string;
  context?: "web" | "bulk" | "extension";
  preview_limit?: number;
  include_sections?: string[];
  force_refresh?: boolean;
}

export interface ExpandContainerRequest {
  section_id?: string;
  ref_id?: string;
  limit?: number;
  cursor?: string;
  force_refresh?: boolean;
}

// ── POST /discover-container response ────────────────────────────────────────

export interface DiscoverContainerResponse {
  job_id:        string;
  container_id:  string;
  status:        DiscoveryJobStatus | string;
  cached:        boolean;
  platform:      string;
  container_type: string;
  title:         string;
  poll_url:      string;
  eta_seconds:   number;
}

// ── Warning codes ─────────────────────────────────────────────────────────────

export type DiscoveryWarningCode =
  | "container_partial_success"
  | "container_section_expand_failed"
  | "container_summary_only"
  | "container_cache_hit"
  | "container_capability_not_ready"
  | "container_discovery_timeout"
  | "container_expand_unavailable";

// ── Utility helpers ───────────────────────────────────────────────────────────

export const TERMINAL_STATUSES = new Set<DiscoveryJobStatus>([
  "success", "failed", "expired",
]);

export const ACTIVE_STATUSES = new Set<DiscoveryJobStatus>([
  "queued", "resolving", "discovering", "expanding",
]);

export function isTerminal(status: DiscoveryJobStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function isActive(status: DiscoveryJobStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

/** Returns a user-facing Vietnamese label for a discovery status */
export function statusLabel(status: DiscoveryJobStatus): string {
  const labels: Record<DiscoveryJobStatus, string> = {
    queued:      "Đang chờ…",
    resolving:   "Đang nhận diện…",
    discovering: "Đang khám phá…",
    expanding:   "Đang mở rộng…",
    partial:     "Hoàn thành một phần",
    success:     "Hoàn tất",
    failed:      "Thất bại",
    expired:     "Đã hết hạn",
  };
  return labels[status] ?? status;
}

/** Returns suggested poll interval in ms based on current status */
export function pollIntervalMs(status: DiscoveryJobStatus, pollCount: number): number {
  if (status === "queued") return 800;
  if (status === "resolving") return 1000;
  if (status === "discovering") return pollCount < 5 ? 1200 : 2000;
  if (status === "expanding") return 1500;
  if (status === "partial") return 2500;
  return 0; // terminal — stop polling
}

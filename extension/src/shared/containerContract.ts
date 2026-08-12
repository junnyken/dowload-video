/**
 * Container Contract — Phase 25 PR1
 * Shared types for the Chrome/Firefox extension popup.
 * Mirrors backend/app/schemas/container.py and frontend/src/types/container.ts.
 *
 * Keep this file dependency-free (no React, no Node APIs).
 * Extension popup imports from here to stay in sync with the backend contract.
 */

// ── Enums (as string unions — no need for enum objects in extension) ──────────

export type SupportLevel =
  | "full" | "partial" | "cookie_required" | "proxy_required"
  | "experimental" | "temporarily_disabled" | "unsupported";

export type EntryFlow =
  | "single_download" | "container_preview" | "playlist_preview"
  | "album_preview" | "artist_preview" | "profile_scrape"
  | "batch_queue" | "unsupported";

export type WarningCode =
  | "cookie_required" | "login_required" | "public_only"
  | "proxy_recommended" | "proxy_required" | "temporarily_disabled"
  | "extraction_unstable" | "partial_support_only"
  | "batch_not_supported" | "channel_not_supported"
  | "audio_only" | "video_only";

export type WarningSeverity = "info" | "warning" | "error";

// ── Component types ───────────────────────────────────────────────────────────

export interface RequirementFlags {
  cookie_required:   boolean;
  login_required:    boolean;
  public_only:       boolean;
  proxy_required:    boolean;
  proxy_recommended: boolean;
}

export interface ContainerWarning {
  code:     WarningCode;
  message:  string;
  severity: WarningSeverity;
}

export interface CapabilityDescriptor {
  platform:             string;
  source_type:          string;
  support_level:        SupportLevel;
  supported_actions:    string[];
  requirements:         RequirementFlags;
  warnings:             ContainerWarning[];
  best_entry_flow:      EntryFlow;
  recommended_endpoint: string;
  notes:                string;
}

export interface RoutingInfo {
  surface_flow:         EntryFlow;
  recommended_endpoint: string;
  is_container:         boolean;
  is_batch:             boolean;
}

// ── Resolve-input ─────────────────────────────────────────────────────────────

export interface ResolveInputItem {
  raw_input:           string;
  normalized_input:    string;
  canonical_url:       string;
  is_short_link:       boolean;
  platform:            string;
  source_type:         string;
  normalized_id:       string | null;
  capability:          CapabilityDescriptor;
  routing:             RoutingInfo;
  transformations:     string[];
  platform_label:      string;
  platform_emoji:      string;
  source_type_label:   string;
  support_level:       string;
  support_level_label: string;
}

export interface ResolveInputResult {
  batch_mode:        boolean;
  normalized_inputs: string[];
  items:             ResolveInputItem[];
  total:             number;
  supported:         number;
  cookie_required:   number;
  unsupported:       number;
  context:           string;
}

// ── Extension-specific popup action ──────────────────────────────────────────

/**
 * What the extension popup should render for a detected URL.
 * Derived from ResolveInputItem by the popup logic.
 */
export interface PopupAction {
  /** "download" | "browse" | "unsupported" | "auth_needed" */
  kind:            "download" | "browse" | "unsupported" | "auth_needed";
  label:           string;        // button label (Vietnamese)
  deepLinkUrl:     string;        // URL to open in VidGrab web app
  platform:        string;
  platformEmoji:   string;
  sourceTypeLabel: string;
  supportLevel:    SupportLevel;
  warnings:        ContainerWarning[];
  isContainer:     boolean;
}

// ── Helper: derive popup action from resolve result ───────────────────────────

const VIDGRAB_ORIGIN = "https://vidgrab.matbao.dev";

export function resolvedItemToPopupAction(item: ResolveInputItem): PopupAction {
  const { capability, routing, platform, platform_emoji, source_type_label } = item;
  const level = capability.support_level;

  const encUrl = encodeURIComponent(item.canonical_url);

  if (level === "unsupported" || level === "temporarily_disabled") {
    return {
      kind: "unsupported",
      label: "Nền tảng chưa hỗ trợ",
      deepLinkUrl: VIDGRAB_ORIGIN,
      platform,
      platformEmoji: platform_emoji,
      sourceTypeLabel: source_type_label,
      supportLevel: level,
      warnings: capability.warnings,
      isContainer: routing.is_container,
    };
  }

  if (level === "cookie_required") {
    return {
      kind: "auth_needed",
      label: "Cần đăng nhập / cấu hình cookie",
      deepLinkUrl: `${VIDGRAB_ORIGIN}/#/settings/cookies`,
      platform,
      platformEmoji: platform_emoji,
      sourceTypeLabel: source_type_label,
      supportLevel: level,
      warnings: capability.warnings,
      isContainer: routing.is_container,
    };
  }

  if (routing.is_container || routing.surface_flow === "container_preview") {
    return {
      kind: "browse",
      label: "Xem trước & chọn nội dung",
      deepLinkUrl: `${VIDGRAB_ORIGIN}/#/bulk?container=${encUrl}`,
      platform,
      platformEmoji: platform_emoji,
      sourceTypeLabel: source_type_label,
      supportLevel: level,
      warnings: capability.warnings,
      isContainer: true,
    };
  }

  return {
    kind: "download",
    label: "Tải xuống",
    deepLinkUrl: `${VIDGRAB_ORIGIN}/#/download?url=${encUrl}`,
    platform,
    platformEmoji: platform_emoji,
    sourceTypeLabel: source_type_label,
    supportLevel: level,
    warnings: capability.warnings,
    isContainer: false,
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

export const SUPPORTED_LEVELS: ReadonlySet<SupportLevel> = new Set([
  "full", "partial", "experimental",
]);

export function isSupported(level: SupportLevel): boolean {
  return SUPPORTED_LEVELS.has(level);
}

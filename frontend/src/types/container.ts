/**
 * Container Types — Phase 25 PR1
 * Mirrors backend/app/schemas/container.py exactly.
 * Do NOT add frontend-only logic here — keep this a pure type file.
 */

// ── Enums ─────────────────────────────────────────────────────────────────────

export type Platform =
  | "tiktok" | "douyin" | "facebook" | "threads" | "pinterest"
  | "soundcloud" | "spotify" | "instagram" | "twitter" | "reddit"
  | "youtube" | "bilibili" | "xiaohongshu" | "lemon8" | "snapchat"
  | "vk" | "twitch" | "rumble" | "odysee" | "dailymotion"
  | "podcast_rss" | "generic" | "unknown";

export type SourceType =
  | "single_video" | "single_audio" | "single_post"
  | "playlist" | "album" | "artist" | "profile" | "channel"
  | "board" | "feed" | "subreddit" | "thread" | "media_tab"
  | "carousel" | "podcast_feed" | "batch_mixed" | "unknown";

export type SupportLevel =
  | "full" | "partial" | "cookie_required" | "proxy_required"
  | "experimental" | "temporarily_disabled" | "unsupported";

export type ActionType =
  | "fetch_metadata" | "preview_items" | "expand_children"
  | "queue_download" | "queue_audio" | "queue_video"
  | "queue_no_watermark" | "queue_subtitles" | "queue_zip"
  | "export_csv" | "save_cloud"
  | "open_single_download" | "open_bulk_discovery";

export type WarningCode =
  | "cookie_required" | "login_required" | "public_only"
  | "proxy_recommended" | "proxy_required" | "temporarily_disabled"
  | "extraction_unstable" | "partial_support_only"
  | "batch_not_supported" | "channel_not_supported"
  | "audio_only" | "video_only";

export type EntryFlow =
  | "single_download" | "container_preview" | "playlist_preview"
  | "album_preview" | "artist_preview" | "profile_scrape"
  | "batch_queue" | "unsupported";

export type WarningSeverity = "info" | "warning" | "error";

export type MediaKind = "video" | "audio" | "image" | "mixed";

export type SectionType = "flat_list" | "group" | "collection_ref";

// ── Component models ──────────────────────────────────────────────────────────

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
  supported_actions:    ActionType[];
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

// ── Resolve-input response ────────────────────────────────────────────────────

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
  // Flat compat fields (Phase 24)
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

// ── Container summary ─────────────────────────────────────────────────────────

export interface ContainerSummary {
  container_id:        string;
  platform:            string;
  source_type:         string;
  canonical_url:       string;
  title:               string;
  subtitle:            string;
  thumbnail_url:       string;
  item_count_estimate: number;
  avatar_url:          string;
  support:             CapabilityDescriptor | null;
  status:              "discovering" | "ready" | "partial" | "failed";
  error_message:       string;
  warning:             string;
  cached:              boolean;
}

// ── Leaf item ─────────────────────────────────────────────────────────────────

export interface LeafMediaItem {
  item_id:           string;
  platform:          string;
  source_type:       string;
  url:               string;
  title:             string;
  author:            string;
  thumbnail_url:     string;
  duration_sec:      number | null;
  published_at:      string | null;
  media_kind:        MediaKind;
  selectable:        boolean;
  supported_actions: ActionType[];
  views:             number;
  extras:            Record<string, unknown>;
}

// ── Section / children ────────────────────────────────────────────────────────

export interface ChildCollectionRef {
  ref_id:              string;
  label:               string;
  source_type:         string;
  thumbnail_url:       string;
  item_count_estimate: number;
  expandable:          boolean;
  expand_token:        string;
}

export interface PreviewSection {
  section_id:   string;
  label:        string;
  section_type: SectionType;
  item_count:   number;
  expandable:   boolean;
  items_loaded: boolean;
  items:        LeafMediaItem[];
  child_refs:   ChildCollectionRef[];
  has_more:     boolean;
  next_cursor:  string;
}

// ── Container detail (full polling response) ──────────────────────────────────

export interface ContainerDetail extends ContainerSummary {
  sections:      PreviewSection[];
  description:   string;
  fetched_at:    string;
  expires_at:    string;
  section_count: number;
}

// ── Queue request / response ──────────────────────────────────────────────────

export type QueueMode = "selected" | "latest_n" | "top_n" | "section" | "all";

export interface QueueContainerRequest {
  item_ids?:    string[];
  queue_mode:   QueueMode;
  n?:           number;
  section_key?: string;
  apply_dedupe: boolean;
  quality:      string;
}

export interface QueueContainerResponse {
  batch_id:     string;
  queued:       number;
  deduped:      number;
  dedupe_summary: string;
}

// ── Utility type guards ───────────────────────────────────────────────────────

export const CONTAINER_SOURCE_TYPES = new Set<SourceType>([
  "playlist", "album", "artist", "profile", "channel",
  "board", "feed", "subreddit", "media_tab", "podcast_feed",
]);

export function isContainerSourceType(st: string): st is SourceType {
  return CONTAINER_SOURCE_TYPES.has(st as SourceType);
}

export function isSupportedLevel(level: SupportLevel): boolean {
  return level === "full" || level === "partial" || level === "experimental";
}

export function requiresAuth(cap: CapabilityDescriptor): boolean {
  return cap.requirements.cookie_required || cap.requirements.login_required;
}

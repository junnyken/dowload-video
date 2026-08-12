/**
 * Container API client — Phase 25 PR1
 * Typed wrappers around the container endpoints.
 * All functions return typed results or throw on HTTP error.
 */

import type {
  ContainerDetail,
  QueueContainerRequest,
  QueueContainerResponse,
  ResolveInputResult,
} from "../types/container";

const BASE = `${import.meta.env.VITE_API_URL ?? ""}/api/v1`;

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── resolve-input ─────────────────────────────────────────────────────────────

export interface ResolveInputRequest {
  raw_input?: string;
  raw_inputs?: string[];
  context?: string;
}

/** Normalize + classify + capability lookup. No network calls on backend (<50ms). */
export async function resolveInput(
  req: ResolveInputRequest,
  signal?: AbortSignal,
): Promise<ResolveInputResult> {
  const res = await fetch(`${BASE}/resolve-input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_input: req.raw_input ?? "", ...req }),
    signal,
  });
  return handleResponse<ResolveInputResult>(res);
}

// ── discover-container ────────────────────────────────────────────────────────

export interface DiscoverContainerRequest {
  url: string;
  max_items?: number;
  include_sections?: string[];
}

export interface DiscoverContainerResponse {
  container_id: string;
  status: string;
  cached: boolean;
  platform: string;
  container_type: string;
  poll_url: string;
  eta_seconds?: number;
}

/** Start async container discovery. Returns container_id; poll getContainer() for status. */
export async function discoverContainer(
  req: DiscoverContainerRequest,
  signal?: AbortSignal,
): Promise<DiscoverContainerResponse> {
  const res = await fetch(`${BASE}/discover-container`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  return handleResponse<DiscoverContainerResponse>(res);
}

/** Poll container status + sections until status is terminal. */
export async function getContainer(
  containerId: string,
  signal?: AbortSignal,
): Promise<ContainerDetail> {
  const res = await fetch(`${BASE}/container/${containerId}`, { signal });
  return handleResponse<ContainerDetail>(res);
}

// ── expand section ────────────────────────────────────────────────────────────

export interface ExpandSectionRequest {
  section: string;
  child_id?: string;
  cursor?: string;
}

export async function expandSection(
  containerId: string,
  req: ExpandSectionRequest,
  signal?: AbortSignal,
): Promise<{ items: ContainerDetail["sections"][number]["items"] }> {
  const res = await fetch(`${BASE}/container/${containerId}/expand`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  return handleResponse(res);
}

// ── queue selected items ──────────────────────────────────────────────────────

export async function queueContainer(
  containerId: string,
  req: QueueContainerRequest,
  signal?: AbortSignal,
): Promise<QueueContainerResponse> {
  const res = await fetch(`${BASE}/container/${containerId}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  return handleResponse<QueueContainerResponse>(res);
}

// ── refresh ───────────────────────────────────────────────────────────────────

export async function refreshContainer(containerId: string): Promise<DiscoverContainerResponse> {
  const res = await fetch(`${BASE}/container/${containerId}/refresh`, { method: "POST" });
  return handleResponse<DiscoverContainerResponse>(res);
}

// ── manifest CSV ──────────────────────────────────────────────────────────────

export function getManifestUrl(containerId: string): string {
  return `${BASE}/container/${containerId}/manifest`;
}

// ── platforms/capabilities ────────────────────────────────────────────────────

export interface PlatformCapabilitiesResponse {
  platforms: Array<{
    platform:        string;
    display_name:    string;
    emoji:           string;
    overall_status:  string;
    domains:         string[];
    source_types:    unknown[];
    requires_cookie: boolean;
    proxy_required:  boolean;
  }>;
  total: number;
}

export async function getPlatformCapabilities(): Promise<PlatformCapabilitiesResponse> {
  const res = await fetch(`${BASE}/platforms/capabilities`);
  return handleResponse<PlatformCapabilitiesResponse>(res);
}

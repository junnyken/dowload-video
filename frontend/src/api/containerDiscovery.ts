/**
 * Container Discovery API — Phase 25 PR2
 * Typed client for the discovery job lifecycle endpoints.
 *
 * Separate from container.ts (which handles container data: sections, queue, manifest).
 * Use this for the POLLING loop: start → poll → done.
 */

import type {
  DiscoverContainerRequest,
  DiscoverContainerResponse,
  DiscoveryJobSnapshot,
  ExpandContainerRequest,
} from "../types/containerDiscovery";
import { isTerminal, pollIntervalMs } from "../types/containerDiscovery";

const BASE = `${import.meta.env.VITE_API_URL ?? ""}/api/v1`;

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── POST /discover-container ──────────────────────────────────────────────────

/**
 * Start async container discovery.
 * Returns immediately with job_id — poll pollJobSnapshot() for progress.
 */
export async function startDiscovery(
  req: DiscoverContainerRequest,
  signal?: AbortSignal,
): Promise<DiscoverContainerResponse> {
  const body = req.url
    ? { url: req.url, ...req }
    : req;
  const res = await fetch(`${BASE}/discover-container`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return handleResponse<DiscoverContainerResponse>(res);
}

// ── GET /discover-container/{job_id} ─────────────────────────────────────────

/** Poll a single snapshot for a job_id. */
export async function pollJobSnapshot(
  jobId: string,
  signal?: AbortSignal,
): Promise<DiscoveryJobSnapshot> {
  const res = await fetch(`${BASE}/discover-container/${jobId}`, { signal });
  return handleResponse<DiscoveryJobSnapshot>(res);
}

// ── Polling loop helper ───────────────────────────────────────────────────────

export interface PollOptions {
  /** Called on every snapshot update (including terminal states). */
  onUpdate: (snapshot: DiscoveryJobSnapshot) => void;
  /** Called when discovery reaches a terminal state (success/failed/expired). */
  onComplete?: (snapshot: DiscoveryJobSnapshot) => void;
  /** Called on network/parse errors. Return true to stop polling, false to retry. */
  onError?: (err: Error, pollCount: number) => boolean;
  /** AbortSignal to stop polling from outside. */
  signal?: AbortSignal;
  /** Maximum number of consecutive errors before giving up (default: 5). */
  maxErrors?: number;
}

/**
 * Start a managed polling loop for a discovery job.
 * Automatically adjusts poll interval and stops on terminal status.
 *
 * Returns a cancel function that stops polling when called.
 *
 * Usage:
 *   const cancel = pollDiscovery(jobId, {
 *     onUpdate: snap => setState(snap),
 *     onComplete: snap => handleDone(snap),
 *   });
 *   // later: cancel();
 */
export function pollDiscovery(jobId: string, opts: PollOptions): () => void {
  const { onUpdate, onComplete, onError, signal, maxErrors = 5 } = opts;

  let stopped = false;
  let pollCount = 0;
  let errorCount = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  const cancel = () => {
    stopped = true;
    if (timeoutId !== null) clearTimeout(timeoutId);
  };

  if (signal) {
    signal.addEventListener("abort", cancel);
  }

  async function tick() {
    if (stopped) return;
    try {
      const snap = await pollJobSnapshot(jobId, signal);
      if (stopped) return;
      errorCount = 0;
      pollCount += 1;
      onUpdate(snap);
      if (isTerminal(snap.status)) {
        onComplete?.(snap);
        return;
      }
      const delay = pollIntervalMs(snap.status, pollCount);
      timeoutId = setTimeout(tick, delay);
    } catch (err) {
      if (stopped) return;
      errorCount += 1;
      const stop = onError?.(err as Error, pollCount) ?? (errorCount >= maxErrors);
      if (stop) return;
      timeoutId = setTimeout(tick, 2000 * Math.min(errorCount, 4));
    }
  }

  tick();
  return cancel;
}

// ── POST /container/{id}/expand (PR2 richer request) ─────────────────────────

export async function expandContainerSection(
  containerId: string,
  req: ExpandContainerRequest,
  signal?: AbortSignal,
): Promise<{ items: unknown[]; section: string; items_loaded: number }> {
  const body = {
    section: req.section_id,
    child_id: req.ref_id ?? "",
    cursor: req.cursor ?? "",
    ...req,
  };
  const res = await fetch(`${BASE}/container/${containerId}/expand`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return handleResponse(res);
}

// ── GET /discover-container/{job_id} convenience re-export ───────────────────

export { isTerminal, pollIntervalMs };

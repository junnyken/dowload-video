/**
 * useContainerDiscover — Phase 25 PR3 (hardened)
 *
 * Full container discovery flow using PR2 job polling contract:
 *   1. POST /api/v1/discover-container  → job_id + container_id
 *   2. Poll GET /api/v1/discover-container/{job_id} every adaptive interval
 *      (queued=800ms, discovering=1.5s, expanding=1.5s)
 *   3. On success/partial → fetch GET /api/v1/container/{container_id} for full data
 *   4. On expired → expose jobSnapshot so UI can show "Re-discover" CTA
 *   5. On cookie_required / proxy_required → expose capabilityGate so UI shows banner
 *
 * Exposes:
 *   container     — full ContainerMeta (null until success/partial)
 *   jobSnapshot   — DiscoveryJobSnapshot (live during polling)
 *   isLoading     — true while discovering
 *   error         — string error message
 *   progress      — 0..100
 *   stage         — current stage string
 *   capabilityGate — { required: "cookie"|"proxy", platform } | null
 *   discover(url) — start / re-start discovery
 *   refresh()     — re-discover the same URL
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

const TERMINAL_JOB_STATUSES = new Set(['success', 'partial', 'failed', 'expired']);
const TERMINAL_META_STATUSES = new Set(['ready', 'partial', 'failed']);  // PR1 compat

function pollIntervalMs(status, pollCount) {
  if (status === 'queued')      return 800;
  if (status === 'resolving')   return 1000;
  if (status === 'discovering') return pollCount < 5 ? 1200 : 2000;
  if (status === 'expanding')   return 1500;
  if (status === 'partial')     return 2500;
  return 0;
}

function detectCapabilityGate(snapshot, platform) {
  if (!snapshot) return null;
  const reason = snapshot.terminal_reason;
  const errorCode = snapshot.error?.code;

  if (reason === 'cookie_required' || errorCode === 'C010') {
    return { required: 'cookie', platform };
  }
  if (reason === 'proxy_required' || errorCode === 'C020') {
    return { required: 'proxy', platform };
  }
  // Phase 26-A: proxy configured but connectivity probe failed
  if (reason === 'proxy_unhealthy' || errorCode === 'C021') {
    return { required: 'proxy_unhealthy', platform };
  }
  // Phase 26-A: YouTube single video is disabled
  if (reason === 'youtube_single_temporarily_disabled') {
    return { required: 'single_disabled', platform };
  }
  // Phase 26-B: cookie states
  if (reason === 'cookie_unavailable') return { required: 'cookie_unavailable', platform };
  if (reason === 'cookie_expired')     return { required: 'cookie_expired',     platform };
  if (reason === 'cookie_blocked')     return { required: 'cookie_blocked',     platform };
  if (reason === 'cookie_partial')     return { required: 'cookie_partial',     platform };
  return null;
}

/**
 * Parse a structured error detail from the discover endpoint.
 * FastAPI HTTPException detail can be a string or {code, message} dict.
 */
function _parseErrorDetail(detail) {
  if (!detail) return { code: null, message: String(detail) };
  if (typeof detail === 'string') return { code: null, message: detail };
  return { code: detail.code || null, message: detail.message || JSON.stringify(detail) };
}

export function useContainerDiscover(url, { maxItems = 100, autoStart = true } = {}) {
  const [container, setContainer]       = useState(null);
  const [jobSnapshot, setJobSnapshot]   = useState(null);
  const [isLoading, setIsLoading]       = useState(false);
  const [error, setError]               = useState(null);
  const [progress, setProgress]         = useState(0);
  const [stage, setStage]               = useState('');
  const [capabilityGate, setCapabilityGate] = useState(null);

  const pollRef    = useRef(null);
  const abortRef   = useRef(null);
  const activeUrl  = useRef(null);
  const pollCount  = useRef(0);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
  }, []);

  // ── Load full ContainerMeta after job reaches terminal success/partial ──────
  const loadContainerData = useCallback(async (containerId, signal) => {
    try {
      const res = await fetch(`${API_BASE}/container/${containerId}`, { signal });
      if (!res.ok) return;
      const data = await res.json();
      setContainer(data);
    } catch {
      // Non-fatal — jobSnapshot already has preview sections
    }
  }, []);

  // ── Polling loop (PR2 contract) ───────────────────────────────────────────
  const schedulePoll = useCallback((jobId, containerId, platform) => {
    const tick = async () => {
      if (!abortRef.current) return;
      try {
        const res = await fetch(`${API_BASE}/discover-container/${jobId}`, {
          signal: abortRef.current?.signal,
        });
        if (!res.ok) {
          setError(`Poll lỗi: HTTP ${res.status}`);
          setIsLoading(false);
          return;
        }
        const snap = await res.json();
        pollCount.current += 1;
        setJobSnapshot(snap);
        setProgress(snap.progress_pct ?? 0);
        setStage(snap.stage ?? '');

        if (TERMINAL_JOB_STATUSES.has(snap.status)) {
          setIsLoading(false);
          if (snap.status === 'success' || snap.status === 'partial') {
            await loadContainerData(snap.container_id || containerId, abortRef.current?.signal);
          } else if (snap.status === 'failed') {
            const gate = detectCapabilityGate(snap, platform);
            if (gate) {
              setCapabilityGate(gate);
            } else {
              setError(snap.error?.message || snap.message || 'Discovery thất bại.');
            }
          }
          // expired — UI reads jobSnapshot.status to show "Re-discover" CTA
          return;
        }

        const delay = pollIntervalMs(snap.status, pollCount.current);
        pollRef.current = setTimeout(tick, delay);
      } catch (err) {
        if (err.name === 'AbortError') return;
        setError(err.message);
        setIsLoading(false);
      }
    };

    pollRef.current = setTimeout(tick, 800);  // initial delay
  }, [loadContainerData]);

  // ── Start discovery ───────────────────────────────────────────────────────
  const discover = useCallback(async (targetUrl) => {
    if (!targetUrl) return;
    stopPolling();
    pollCount.current = 0;

    setIsLoading(true);
    setError(null);
    setContainer(null);
    setJobSnapshot(null);
    setProgress(0);
    setStage('queued');
    setCapabilityGate(null);
    activeUrl.current = targetUrl;

    abortRef.current = new AbortController();

    try {
      const res = await fetch(`${API_BASE}/discover-container`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: targetUrl, max_items: maxItems }),
        signal: abortRef.current.signal,
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        const { code, message } = _parseErrorDetail(body?.detail);

        // Map structured Phase 26-A error codes to capability gates
        // so the UI shows a helpful banner instead of a raw error string.
        if (code === 'youtube_single_temporarily_disabled') {
          setCapabilityGate({ required: 'single_disabled', platform: 'youtube' });
          setIsLoading(false);
          return;
        }
        if (code === 'youtube_proxy_required') {
          setCapabilityGate({ required: 'proxy', platform: 'youtube' });
          setIsLoading(false);
          return;
        }
        if (code === 'youtube_proxy_unhealthy') {
          setCapabilityGate({ required: 'proxy_unhealthy', platform: 'youtube' });
          setIsLoading(false);
          return;
        }
        // Phase 26-B: cookie state codes  (format: {platform}_{cookie_state})
        const avail = body?.detail?.availability_reason;
        if (avail === 'cookie_unavailable') {
          setCapabilityGate({ required: 'cookie_unavailable', platform });
          setIsLoading(false);
          return;
        }
        if (avail === 'cookie_expired') {
          setCapabilityGate({ required: 'cookie_expired', platform });
          setIsLoading(false);
          return;
        }
        if (avail === 'cookie_blocked') {
          setCapabilityGate({ required: 'cookie_blocked', platform });
          setIsLoading(false);
          return;
        }

        throw new Error(message || `HTTP ${res.status}`);
      }

      const data = await res.json();

      // Cache hit (PR1 compat) — job_id may be empty
      if (data.cached && TERMINAL_META_STATUSES.has(data.status)) {
        if (data.container_id) {
          await loadContainerData(data.container_id, abortRef.current?.signal);
        }
        setProgress(100);
        setStage('finalize');
        setIsLoading(false);
        return;
      }

      // PR2 flow — start polling job snapshot
      if (!data.job_id) {
        // pre-PR2 cache hit with no job_id → fall back to PR1 meta polling
        _pollLegacyMeta(data.container_id);
        return;
      }

      setJobSnapshot({
        job_id:       data.job_id,
        container_id: data.container_id,
        platform:     data.platform,
        source_type:  data.container_type,
        status:       'queued',
        progress_pct: 5,
        stage:        'normalize_input',
        message:      'Đang xếp hàng...',
      });

      schedulePoll(data.job_id, data.container_id, data.platform);
    } catch (e) {
      if (e.name === 'AbortError') return;
      setError(e.message);
      setIsLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxItems, schedulePoll, stopPolling, loadContainerData]);

  // ── PR1 legacy meta polling fallback ─────────────────────────────────────
  function _pollLegacyMeta(containerId) {
    const tick = async () => {
      try {
        const res = await fetch(`${API_BASE}/container/${containerId}`, {
          signal: abortRef.current?.signal,
        });
        if (!res.ok) { setIsLoading(false); return; }
        const data = await res.json();
        setContainer(data);
        if (TERMINAL_META_STATUSES.has(data.status)) {
          setIsLoading(false);
          return;
        }
        pollRef.current = setTimeout(tick, 2000);
      } catch (e) {
        if (e.name !== 'AbortError') setIsLoading(false);
      }
    };
    pollRef.current = setTimeout(tick, 1000);
  }

  const refresh = useCallback(() => {
    if (activeUrl.current) discover(activeUrl.current);
  }, [discover]);

  // Auto-start when url changes
  useEffect(() => {
    if (!autoStart || !url) return;
    discover(url);
    return () => stopPolling();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return { container, jobSnapshot, isLoading, error, progress, stage, capabilityGate, discover, refresh };
}

export default useContainerDiscover;

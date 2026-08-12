/**
 * useResolveInput — Phase 24 Universal Capture
 *
 * Debounced hook: calls POST /api/v1/resolve-input when URL changes.
 * Returns capability info (platform, source_type, support_level, warnings).
 * Fast (<50ms backend, pure URL pattern matching, no yt-dlp).
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || '';
const DEBOUNCE_MS = 350;
const MIN_LENGTH  = 12; // "https://x.co" minimum

export function useResolveInput(rawUrl, { context = 'web', enabled = true } = {}) {
  const [result,  setResult]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  const timerRef = useRef(null);
  const abortRef = useRef(null);

  const resolve = useCallback(async (url) => {
    if (!url || url.length < MIN_LENGTH || !url.startsWith('http')) {
      setResult(null);
      setError(null);
      return;
    }

    if (abortRef.current) abortRef.current.abort();
    abortRef.current = new AbortController();

    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/v1/resolve-input`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_input: url, context }),
        signal: abortRef.current.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // For single URL, expose the first item directly
      setResult(data.items?.[0] ?? null);
    } catch (e) {
      if (e.name === 'AbortError') return;
      setError(e.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [context]);

  useEffect(() => {
    if (!enabled) { setResult(null); return; }

    clearTimeout(timerRef.current);

    if (!rawUrl || rawUrl.length < MIN_LENGTH) {
      setResult(null);
      setLoading(false);
      return;
    }

    timerRef.current = setTimeout(() => resolve(rawUrl), DEBOUNCE_MS);
    return () => clearTimeout(timerRef.current);
  }, [rawUrl, resolve, enabled]);

  return { result, loading, error };
}

export default useResolveInput;

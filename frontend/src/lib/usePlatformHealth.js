import { useEffect, useState } from 'react';
import { API_BASE } from './apiBase';

// Phase 34D — the site must not ADVERTISE a platform as supported while its
// health probe is failing or the platform has no probe. Downloading is never
// blocked; this only drives what the marketing surfaces show.
//
// No public admin-config endpoint exists, so the toggle is a frontend
// constant (default ON). Flip to false to advertise every platform again.
export const HIDE_UNHEALTHY_PLATFORMS = true;

const CACHE_KEY = 'vg_platform_health_v1';
const CACHE_TTL_MS = 5 * 60 * 1000;

// Labels used by the UI -> platform keys used by /platform-status.
const ALIASES = {
  x: 'twitter', 'x / twitter': 'twitter', 'twitter / x': 'twitter', 'twitter/x': 'twitter',
  'podcast rss': 'podcast', vkontakte: 'vk',
};

export function healthKey(name) {
  const n = String(name || '').trim().toLowerCase();
  if (ALIASES[n]) return ALIASES[n];
  return n.replace(/\s.*/, '').replace(/[^a-z0-9]/g, '');
}

let memory = null;      // { at, map }
let inflight = null;

function readSession() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (v && v.map && Date.now() - v.at < CACHE_TTL_MS) return v;
  } catch { /* storage unavailable or corrupt: ignore */ }
  return null;
}

function writeSession(v) {
  try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(v)); } catch { /* ignore */ }
}

async function load() {
  if (memory && Date.now() - memory.at < CACHE_TTL_MS) return memory.map;
  const s = readSession();
  if (s) { memory = s; return s.map; }
  if (!inflight) {
    inflight = (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/v1/platform-status`);
        if (!r.ok) return null;
        const j = await r.json();
        if (!j || !Array.isArray(j.platforms)) return null;
        const map = {};
        j.platforms.forEach((p) => {
          map[p.platform] = {
            status: p.status,
            known_limitation: p.known_limitation || '',
            last_verified_at: p.last_verified_at || null,
          };
        });
        memory = { at: Date.now(), map };
        writeSession(memory);
        return map;
      } catch {
        return null; // fail open
      } finally {
        inflight = null;
      }
    })();
  }
  return inflight;
}

// Returns { ready, map, info(name), classify(name) }.
// classify -> 'healthy' | 'degraded' | 'unknown'.
// Fail open: if the API is down (map null) everything classifies 'healthy',
// i.e. the site looks exactly as it did before this feature.
export function usePlatformHealth() {
  const [map, setMap] = useState(() => (memory ? memory.map : null));
  const [ready, setReady] = useState(!!memory);

  useEffect(() => {
    let cancelled = false;
    load().then((m) => {
      if (cancelled) return;
      setMap(m);
      setReady(true);
    });
    return () => { cancelled = true; };
  }, []);

  const info = (name) => (map ? map[healthKey(name)] || null : null);
  const classify = (name) => {
    if (!HIDE_UNHEALTHY_PLATFORMS || !map) return 'healthy';
    const i = map[healthKey(name)];
    if (!i) return 'unknown';
    if (i.status === 'healthy' || i.status === 'constrained') return 'healthy'; // constrained = slower, still works
    if (i.status === 'degraded') return 'degraded';
    return 'unknown'; // unknown / constrained-without-probe etc.
  };
  return { ready, map, info, classify };
}

import { useEffect, useState } from 'react';
import { API_BASE } from './apiBase';
import i18n from '../i18n';

// One source for plan numbers: GET /api/v1/plans (backend app/core/plans.py).
// Every surface that talks about Khách / Free / Pro limits reads from here so
// the copy cannot drift from what the server enforces. -1 means unlimited.
const CACHE_KEY = 'vg_plans_v1';
const CACHE_TTL_MS = 5 * 60 * 1000;

let memory = null; // { at, data }
let inflight = null;

function readSession() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (v && v.data && Date.now() - v.at < CACHE_TTL_MS) return v;
  } catch { /* storage unavailable or corrupt: ignore */ }
  return null;
}

function writeSession(v) {
  try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(v)); } catch { /* ignore */ }
}

async function load() {
  if (memory && Date.now() - memory.at < CACHE_TTL_MS) return memory.data;
  const s = readSession();
  if (s) { memory = s; return s.data; }
  if (!inflight) {
    inflight = (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/v1/plans`);
        if (!r.ok) return null;
        const j = await r.json();
        if (!j || !Array.isArray(j.tiers)) return null;
        memory = { at: Date.now(), data: j };
        writeSession(memory);
        return j;
      } catch {
        return null;
      } finally {
        inflight = null;
      }
    })();
  }
  return inflight;
}

// ── formatting helpers shared by the pricing page and the upgrade modal ──

export function formatVnd(n) {
  const v = Number(n) || 0;
  return `${v.toLocaleString('vi-VN')}đ`;
}

export function qualityLabel(h) {
  const n = Number(h) || 0;
  if (n <= 0) return '—';
  return n >= 4320 ? '8K' : n >= 2160 ? '4K' : n >= 1440 ? '2K' : `${n}p`;
}

export function limitLabel(n, unit = '') {
  if (n === -1) return i18n.t('unlimited', { ns: 'plans' });
  return unit ? `${n} ${unit}` : String(n);
}

// Returns { ready, plans, tier(id), free, pro, guest }. Fails soft: when the
// API is unreachable `plans` is null and callers show no numbers at all
// rather than invented ones.
export function usePlans() {
  const [plans, setPlans] = useState(() => (memory ? memory.data : null));
  const [ready, setReady] = useState(!!memory);

  useEffect(() => {
    let cancelled = false;
    load().then((d) => {
      if (cancelled) return;
      setPlans(d);
      setReady(true);
    });
    return () => { cancelled = true; };
  }, []);

  const tier = (id) => (plans ? plans.tiers.find((t) => t.id === id) || null : null);
  return { ready, plans, tier, guest: tier('guest'), free: tier('free'), pro: tier('pro') };
}

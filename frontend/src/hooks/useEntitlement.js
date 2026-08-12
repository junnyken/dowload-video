import { useState, useEffect, useCallback, useRef } from 'react';
import { supabase } from '../lib/supabaseClient';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;
const CACHE_KEY = 'vg_entitlement_cache';
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

// Mirror backend PLAN_DEFS — fast client-side gating without extra round-trip
const PRO_FEATURES  = new Set([
  'bulk_zip', 'youtube_video', 'spotify_full', 'cloud_save',
  'chapters', 'logo_inpaint', 'api_key', 'webhook', 'ai_tools', 'priority_queue',
]);
const TEAM_EXTRA    = new Set(['team_workspace', 'shared_history']);
const ENTERPRISE_EXTRA = new Set(['white_label', 'sso', 'custom_domain', 'dedicated_support']);

const PLAN_FEATURES = {
  free:       new Set(['basic_platforms']),
  pro:        new Set(['basic_platforms', ...PRO_FEATURES]),
  team:       new Set(['basic_platforms', ...PRO_FEATURES, ...TEAM_EXTRA]),
  enterprise: new Set(['basic_platforms', ...PRO_FEATURES, ...TEAM_EXTRA, ...ENTERPRISE_EXTRA]),
};

function readCache() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const { ts, data } = JSON.parse(raw);
    if (Date.now() - ts > CACHE_TTL) return null;
    return data;
  } catch {
    return null;
  }
}

function writeCache(data) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), data }));
  } catch {
    // sessionStorage unavailable — skip caching
  }
}

function clearCache() {
  try {
    sessionStorage.removeItem(CACHE_KEY);
  } catch {
    // noop
  }
}

export function useEntitlement() {
  const [tier, setTier]                 = useState('free');
  const [billingStatus, setBillingStatus] = useState('none');
  const [loading, setLoading]           = useState(true);
  const fetchedRef = useRef(false);

  const fetchStatus = useCallback(async (force = false) => {
    if (!force) {
      const cached = readCache();
      if (cached) {
        setTier(cached.tier || 'free');
        setBillingStatus(cached.billing_status || 'none');
        setLoading(false);
        return;
      }
    }

    setLoading(true);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        setTier('free');
        setBillingStatus('none');
        setLoading(false);
        return;
      }

      const res = await fetch(`${API}/payments/billing-status`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      writeCache(data);
      setTier(data.tier || 'free');
      setBillingStatus(data.billing_status || 'none');
    } catch {
      // Network error — fall back to free, don't hard-block user
      setTier('free');
      setBillingStatus('none');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    fetchStatus();
  }, [fetchStatus]);

  const refresh = useCallback(() => {
    clearCache();
    fetchedRef.current = false;
    fetchStatus(true);
  }, [fetchStatus]);

  const isProEquivalent = ['pro', 'team', 'enterprise'].includes(tier);

  const allowed = useCallback(
    (feature) => {
      const featureSet = PLAN_FEATURES[tier] || PLAN_FEATURES.free;
      return featureSet.has(feature);
    },
    [tier],
  );

  return { tier, billingStatus, isProEquivalent, allowed, loading, refresh };
}

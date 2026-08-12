/**
 * Phase 14 — Lifecycle nudge engine.
 * Determines which banner (if any) to show based on user state.
 *
 * Contexts:
 *   'new_user'         — never downloaded anything
 *   'first_success'    — just completed first download
 *   'quota_warning'    — free user at 80%+ quota
 *   'quota_reached'    — free user hit limit
 *   'paywall_hit'      — user just hit a Pro-only gate
 *   'extension_nudge'  — desktop user who has succeeded 1+ times
 *   'telegram_nudge'   — mobile user who has succeeded 3+ times
 *   'pro_power_user'   — pro user who hasn't used API keys yet
 */

import { useState, useEffect, useCallback } from 'react';

const SUPPRESSION_KEY = 'vg_nudge_suppression';
const NUDGE_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

function getSuppressions() {
  try {
    return JSON.parse(localStorage.getItem(SUPPRESSION_KEY) || '{}');
  } catch {
    return {};
  }
}

function isSuppressed(nudgeType) {
  const s = getSuppressions();
  const ts = s[nudgeType];
  if (!ts) return false;
  return Date.now() - ts < NUDGE_COOLDOWN_MS;
}

function suppress(nudgeType) {
  const s = getSuppressions();
  s[nudgeType] = Date.now();
  localStorage.setItem(SUPPRESSION_KEY, JSON.stringify(s));
}

function isDesktop() {
  return window.innerWidth >= 768 && !/Mobi|Android/i.test(navigator.userAgent);
}

function isMobile() {
  return !isDesktop();
}

/**
 * @param {object} opts
 * @param {object|null} opts.user           - Supabase user or null
 * @param {string}      opts.tier           - 'free' | 'pro'
 * @param {number}      opts.downloadCount  - Total successful downloads this session/day
 * @param {number}      opts.quotaUsed      - Current quota used (0-1 fraction)
 * @param {boolean}     opts.hasApiKeys     - Whether user has created API keys
 * @param {string|null} opts.paywallTrigger - Which feature triggered paywall (null = no paywall)
 */
export function useLifecycleBanners({
  user = null,
  tier = 'free',
  downloadCount = 0,
  quotaUsed = 0,
  hasApiKeys = false,
  paywallTrigger = null,
}) {
  const [banner, setBanner] = useState(null);

  const dismiss = useCallback((nudgeType) => {
    suppress(nudgeType);
    setBanner(null);
  }, []);

  useEffect(() => {
    // Determine which nudge to show (priority order)
    const candidates = [];

    if (paywallTrigger && !isSuppressed('paywall_hit')) {
      candidates.push({
        type: 'paywall_hit',
        trigger: paywallTrigger,
        priority: 100,
      });
    }

    if (quotaUsed >= 1.0 && tier === 'free' && !isSuppressed('quota_reached')) {
      candidates.push({ type: 'quota_reached', priority: 90 });
    } else if (quotaUsed >= 0.8 && tier === 'free' && !isSuppressed('quota_warning')) {
      candidates.push({ type: 'quota_warning', priority: 80 });
    }

    if (downloadCount >= 1 && isDesktop() && !isSuppressed('extension_nudge')) {
      candidates.push({ type: 'extension_nudge', priority: 50 });
    }

    if (downloadCount >= 3 && isMobile() && !isSuppressed('telegram_nudge')) {
      candidates.push({ type: 'telegram_nudge', priority: 50 });
    }

    if (tier === 'pro' && !hasApiKeys && downloadCount >= 5 && !isSuppressed('pro_power_user')) {
      candidates.push({ type: 'pro_power_user', priority: 30 });
    }

    if (downloadCount === 0 && !user && !isSuppressed('new_user')) {
      candidates.push({ type: 'new_user', priority: 10 });
    }

    candidates.sort((a, b) => b.priority - a.priority);
    setBanner(candidates[0] || null);
  }, [paywallTrigger, quotaUsed, tier, downloadCount, hasApiKeys, user]);

  return { banner, dismiss };
}

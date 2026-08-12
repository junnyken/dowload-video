/**
 * useSmartDefaults — Phase 21 Smart Defaults Resolver
 * =====================================================
 * Provides context-aware download defaults based on:
 *   1. URL-detected platform
 *   2. User's active preset for this platform
 *   3. User's learned preferences (localStorage)
 *   4. Platform system defaults
 *   5. Global app defaults
 *
 * Usage:
 *   const { platform, defaults, quickActions, applyDefaults, recordUsed } = useSmartDefaults(url);
 *
 *   // After metadata load, apply smart defaults to form state:
 *   const { quality, remove_watermark } = applyDefaults(currentFormState, videoInfo);
 *
 *   // After successful download, record the choice:
 *   recordUsed(platform, { quality, remove_watermark });
 */

import { useMemo, useCallback } from 'react';

// ── Platform detection ────────────────────────────────────────────────────────

const PLATFORM_HOST_MAP = {
  'tiktok.com':      'tiktok',
  'vm.tiktok.com':   'tiktok',
  'vt.tiktok.com':   'tiktok',
  'douyin.com':      'douyin',
  'youtube.com':     'youtube',
  'youtu.be':        'youtube',
  'music.youtube.com': 'youtube_music',
  'spotify.com':     'spotify',
  'open.spotify.com': 'spotify',
  'soundcloud.com':  'soundcloud',
  'instagram.com':   'instagram',
  'facebook.com':    'facebook',
  'fb.watch':        'facebook',
  'twitter.com':     'twitter',
  'x.com':           'twitter',
  'threads.net':     'threads',
  'reddit.com':      'reddit',
  'twitch.tv':       'twitch',
  'rumble.com':      'rumble',
  'dailymotion.com': 'dailymotion',
  'bilibili.com':    'bilibili',
  'vk.com':          'vk',
  'pinterest.com':   'pinterest',
  'snapchat.com':    'snapchat',
  'lemon8-app.com':  'lemon8',
};

export function detectPlatform(url) {
  if (!url) return null;
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    return PLATFORM_HOST_MAP[host] || null;
  } catch {
    return null;
  }
}

// ── Platform system defaults ──────────────────────────────────────────────────

const PLATFORM_DEFAULTS = {
  tiktok:        { quality: 'video',   remove_watermark: true,  suggest_mp3: false, suggest_gif: true  },
  douyin:        { quality: 'video',   remove_watermark: true,  suggest_mp3: false, suggest_gif: false },
  youtube:       { quality: 'video',   remove_watermark: false, suggest_mp3: true,  suggest_gif: true  },
  youtube_music: { quality: 'mp3_320', remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  spotify:       { quality: 'mp3_320', remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  soundcloud:    { quality: 'mp3_128', remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  instagram:     { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: true  },
  facebook:      { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  twitter:       { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  threads:       { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  reddit:        { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  twitch:        { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: true  },
  rumble:        { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: false },
  bilibili:      { quality: 'video',   remove_watermark: false, suggest_mp3: false, suggest_gif: false },
};

const GLOBAL_DEFAULT = { quality: 'video', remove_watermark: false, suggest_mp3: false, suggest_gif: false };

// ── Quick action definitions ──────────────────────────────────────────────────

export const QUICK_ACTIONS = {
  mp3_fast: {
    id: 'mp3_fast',
    label: 'MP3',
    icon: '🎵',
    settings: { quality: 'mp3_128' },
    description: 'Trích âm thanh MP3',
    tier: 'free',
  },
  mp3_hq: {
    id: 'mp3_hq',
    label: 'MP3 HQ',
    icon: '🎵',
    settings: { quality: 'mp3_320' },
    description: 'MP3 chất lượng cao 320kbps',
    tier: 'pro',
  },
  video_hd: {
    id: 'video_hd',
    label: 'HD 1080p',
    icon: '📹',
    settings: { quality: '1080', format: 'mp4' },
    description: 'Video HD 1080p',
    tier: 'free',
  },
  no_watermark: {
    id: 'no_watermark',
    label: 'Sạch WM',
    icon: '✨',
    settings: { remove_watermark: true, quality: 'video' },
    description: 'Tải không watermark',
    tier: 'free',
  },
  thumbnail: {
    id: 'thumbnail',
    label: 'Thumbnail',
    icon: '🖼',
    settings: { quality: 'thumbnail_only' },
    description: 'Chỉ lấy ảnh thumbnail',
    tier: 'free',
  },
  gif_quick: {
    id: 'gif_quick',
    label: 'GIF 15s',
    icon: '🎞',
    settings: { quality: 'video', output: 'gif', duration: 15 },
    description: 'Tạo GIF 15 giây đầu',
    tier: 'pro',
  },
};

/** Get platform-appropriate quick actions (max 4, most relevant first) */
export function getQuickActionsForPlatform(platform, videoInfo = null) {
  const actions = [];
  const isAudio = ['spotify', 'soundcloud', 'youtube_music'].includes(platform);
  const isShortVideo = videoInfo?.duration && videoInfo.duration < 90;

  if (isAudio) {
    actions.push(QUICK_ACTIONS.mp3_hq);
    actions.push(QUICK_ACTIONS.mp3_fast);
  } else {
    actions.push(QUICK_ACTIONS.video_hd);
    actions.push(QUICK_ACTIONS.mp3_fast);
    if (['tiktok', 'douyin'].includes(platform)) {
      actions.push(QUICK_ACTIONS.no_watermark);
    }
    if (isShortVideo) {
      actions.push(QUICK_ACTIONS.gif_quick);
    }
    actions.push(QUICK_ACTIONS.thumbnail);
  }

  return actions.slice(0, 4);
}

// ── Learned preference storage ────────────────────────────────────────────────

const LEARNED_KEY = 'vg_learned_prefs';
const LEARNED_TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

export function getLearnedPrefs() {
  try {
    const raw = localStorage.getItem(LEARNED_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    const now = Date.now();
    // Prune expired entries
    const valid = {};
    for (const [platform, entry] of Object.entries(parsed)) {
      if (entry.timestamp && now - entry.timestamp < LEARNED_TTL_MS) {
        valid[platform] = entry;
      }
    }
    return valid;
  } catch {
    return {};
  }
}

export function saveLearnedPref(platform, prefs) {
  if (!platform) return;
  try {
    const current = getLearnedPrefs();
    current[platform] = { ...prefs, timestamp: Date.now() };
    localStorage.setItem(LEARNED_KEY, JSON.stringify(current));
  } catch {}
}

// ── Main hook ─────────────────────────────────────────────────────────────────

export function useSmartDefaults(url, { activePresets = [] } = {}) {
  const platform = useMemo(() => detectPlatform(url), [url]);

  const platformDef = useMemo(
    () => PLATFORM_DEFAULTS[platform] || GLOBAL_DEFAULT,
    [platform],
  );

  const learnedPref = useMemo(() => {
    if (!platform) return {};
    const all = getLearnedPrefs();
    return all[platform] || {};
  }, [platform]);

  const activePreset = useMemo(() => {
    if (!platform || !activePresets.length) return null;
    return (
      activePresets.find((p) => p.platform === platform && p.is_default) ||
      activePresets.find((p) => p.platform === platform) ||
      null
    );
  }, [platform, activePresets]);

  /**
   * Returns merged defaults for form state.
   * Priority: preset > learned pref > platform default > global
   */
  const defaults = useMemo(() => {
    const base = { ...GLOBAL_DEFAULT, ...platformDef };
    const learned = learnedPref;
    const preset = activePreset?.settings || {};

    return {
      quality:          preset.quality          ?? learned.quality          ?? base.quality,
      remove_watermark: preset.remove_watermark ?? learned.remove_watermark ?? base.remove_watermark,
      suggest_mp3:      base.suggest_mp3,
      suggest_gif:      base.suggest_gif,
      _from_preset:     activePreset ? activePreset.name : null,
      _from_learned:    !activePreset && !!learned.quality,
    };
  }, [platformDef, learnedPref, activePreset]);

  /**
   * Returns quick actions appropriate for this URL + video metadata.
   */
  const quickActions = useMemo(
    () => getQuickActionsForPlatform(platform),
    [platform],
  );

  /**
   * Apply smart defaults to a form state object.
   * Call this when videoInfo is loaded, passing current form state.
   * Returns merged state — caller should spread into setState.
   */
  const applyDefaults = useCallback(
    (currentState, videoInfo = null) => {
      // If user has already manually changed something, respect it
      if (currentState._user_modified) return currentState;

      const merged = { ...currentState, ...defaults };
      // Platform-specific overrides after metadata
      if (videoInfo) {
        const isMusicContent = videoInfo.format === 'audio' || videoInfo.has_video === false;
        if (isMusicContent && platform !== 'youtube') {
          merged.quality = defaults.quality || 'mp3_128';
        }
      }
      return merged;
    },
    [defaults, platform],
  );

  /**
   * Record that user used this platform with these settings.
   * Call after successful download to train learned preferences.
   */
  const recordUsed = useCallback(
    (usedPlatform, settingsUsed) => {
      const p = usedPlatform || platform;
      if (!p) return;
      saveLearnedPref(p, {
        quality:          settingsUsed.quality,
        remove_watermark: settingsUsed.remove_watermark,
      });
    },
    [platform],
  );

  return {
    platform,
    platformDef,
    defaults,
    quickActions,
    activePreset,
    applyDefaults,
    recordUsed,
  };
}

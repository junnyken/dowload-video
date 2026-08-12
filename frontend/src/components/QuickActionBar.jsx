/**
 * QuickActionBar — Phase 21
 * ==========================
 * Context-aware quick action buttons shown AFTER metadata is fetched.
 * Reduces "which format/quality do I pick?" friction to a single tap.
 *
 * Renders 2–4 platform-specific actions (e.g. "MP3", "HD", "Sạch WM")
 * alongside a "More" overflow for less common options.
 *
 * Props:
 *   platform         — detected platform string ('tiktok'|'spotify'|...)
 *   videoInfo        — metadata object from backend (duration, has_video, etc.)
 *   activePreset     — user's preset for this platform (or null)
 *   onAction(cfg)    — called with { quality, remove_watermark, ... }
 *   disabled         — while a download is running
 *   userTier         — 'free'|'pro'|'team'|'enterprise' for paywall hints
 */

import { useState, useMemo } from 'react';
import { Music, Video, Sparkles, Image, Film, Lock, ChevronDown } from 'lucide-react';

const ICON_MAP = {
  '🎵': Music,
  '📹': Video,
  '✨': Sparkles,
  '🖼': Image,
  '🎞': Film,
};

function ActionIcon({ icon, className = 'w-3.5 h-3.5' }) {
  const LucideIcon = ICON_MAP[icon];
  if (LucideIcon) return <LucideIcon className={className} />;
  return <span className="text-xs leading-none">{icon}</span>;
}

const PLATFORM_ACTIONS = {
  tiktok: [
    { id: 'no_wm',     label: 'Sạch WM',   icon: '✨', settings: { remove_watermark: true, quality: 'video' },   tier: 'free', primary: true  },
    { id: 'mp3',       label: 'MP3',        icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: false },
    { id: 'thumb',     label: 'Thumbnail',  icon: '🖼', settings: { quality: 'thumbnail_only' },                    tier: 'free', primary: false },
    { id: 'gif15',     label: 'GIF 15s',    icon: '🎞', settings: { quality: 'video', _trigger_gif: true },         tier: 'free', primary: false },
  ],
  douyin: [
    { id: 'no_wm',     label: 'Sạch WM',   icon: '✨', settings: { remove_watermark: true, quality: 'video' },   tier: 'free', primary: true  },
    { id: 'mp3',       label: 'MP3',        icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: false },
    { id: 'thumb',     label: 'Thumbnail',  icon: '🖼', settings: { quality: 'thumbnail_only' },                    tier: 'free', primary: false },
  ],
  spotify: [
    { id: 'mp3_hq',    label: 'MP3 HQ',    icon: '🎵', settings: { quality: 'mp3_320' },                          tier: 'pro',  primary: true  },
    { id: 'mp3',       label: 'MP3 128',   icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: false },
  ],
  soundcloud: [
    { id: 'mp3',       label: 'MP3',        icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: true  },
    { id: 'mp3_hq',    label: 'MP3 HQ',    icon: '🎵', settings: { quality: 'mp3_320' },                          tier: 'pro',  primary: false },
  ],
  youtube: [
    { id: 'hd',        label: 'HD 1080p',  icon: '📹', settings: { quality: '1080', format: 'mp4' },               tier: 'free', primary: true  },
    { id: 'mp3',       label: 'MP3',        icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: false },
    { id: 'thumb',     label: 'Thumbnail',  icon: '🖼', settings: { quality: 'thumbnail_only' },                    tier: 'free', primary: false },
    { id: 'gif15',     label: 'GIF 15s',    icon: '🎞', settings: { quality: 'video', _trigger_gif: true },         tier: 'free', primary: false },
  ],
  instagram: [
    { id: 'hd',        label: 'Video',     icon: '📹', settings: { quality: 'video' },                             tier: 'free', primary: true  },
    { id: 'mp3',       label: 'MP3',        icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: false },
    { id: 'thumb',     label: 'Thumbnail',  icon: '🖼', settings: { quality: 'thumbnail_only' },                    tier: 'free', primary: false },
  ],
  _default: [
    { id: 'hd',        label: 'HD',        icon: '📹', settings: { quality: 'video' },                             tier: 'free', primary: true  },
    { id: 'mp3',       label: 'MP3',        icon: '🎵', settings: { quality: 'mp3_128' },                          tier: 'free', primary: false },
    { id: 'thumb',     label: 'Thumbnail',  icon: '🖼', settings: { quality: 'thumbnail_only' },                    tier: 'free', primary: false },
  ],
};

const PRO_PLATFORMS = new Set(['pro', 'team', 'enterprise', 'api']);

export default function QuickActionBar({ platform, videoInfo, activePreset, onAction, disabled = false, userTier = 'free' }) {
  const [showMore, setShowMore] = useState(false);
  const isPro = PRO_PLATFORMS.has(userTier);

  const actions = useMemo(() => {
    const base = PLATFORM_ACTIONS[platform] || PLATFORM_ACTIONS._default;
    // For very short videos (<15s), remove GIF quick (not worth it)
    if (videoInfo?.duration && videoInfo.duration < 15) {
      return base.filter((a) => a.id !== 'gif15');
    }
    return base;
  }, [platform, videoInfo]);

  const primary = actions.filter((a) => a.primary || actions.indexOf(a) < 2);
  const secondary = actions.filter((a) => !primary.includes(a));

  function handleAction(action) {
    if (disabled) return;
    if (action.tier === 'pro' && !isPro) return; // caller shows upgrade prompt
    onAction({ ...action.settings, _action_id: action.id });
  }

  function buttonCls(action, isPrimaryBtn) {
    const locked = action.tier === 'pro' && !isPro;
    const base = 'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-150 cursor-pointer whitespace-nowrap';
    if (disabled) return `${base} opacity-40 cursor-not-allowed bg-white/5 text-white/30`;
    if (locked)   return `${base} bg-white/5 text-white/30 border border-white/10`;
    if (isPrimaryBtn) return `${base} bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm shadow-emerald-900/30`;
    return `${base} bg-white/8 hover:bg-white/15 text-white/80 border border-white/10`;
  }

  if (!actions.length) return null;

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {/* Label */}
      <span className="text-xs text-white/35 font-medium shrink-0">Tải nhanh:</span>

      {/* Primary actions */}
      {primary.map((action) => {
        const locked = action.tier === 'pro' && !isPro;
        return (
          <button
            key={action.id}
            onClick={() => handleAction(action)}
            className={buttonCls(action, action.primary)}
            title={locked ? `${action.label} — Yêu cầu Pro` : action.label}
            disabled={disabled}
          >
            <ActionIcon icon={action.icon} />
            {action.label}
            {locked && <Lock className="w-2.5 h-2.5 text-white/40" />}
          </button>
        );
      })}

      {/* Preset button (if user has an active preset for this platform) */}
      {activePreset && (
        <button
          onClick={() => onAction(activePreset.settings)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600/20 hover:bg-indigo-600/35 text-indigo-300 border border-indigo-500/30 transition-all cursor-pointer"
          title={`Preset: ${activePreset.name}`}
          disabled={disabled}
        >
          ⚡ {activePreset.name}
        </button>
      )}

      {/* More / secondary actions */}
      {secondary.length > 0 && (
        <div className="relative">
          <button
            onClick={() => setShowMore((v) => !v)}
            className="flex items-center gap-0.5 px-2 py-1.5 rounded-lg text-xs text-white/40 hover:text-white/70 hover:bg-white/8 transition-all cursor-pointer"
            disabled={disabled}
          >
            Thêm <ChevronDown className={`w-3 h-3 transition-transform ${showMore ? 'rotate-180' : ''}`} />
          </button>
          {showMore && (
            <div className="absolute left-0 top-full mt-1 z-20 bg-[#0d2e29] border border-white/15 rounded-xl shadow-xl p-1.5 min-w-36 flex flex-col gap-0.5">
              {secondary.map((action) => {
                const locked = action.tier === 'pro' && !isPro;
                return (
                  <button
                    key={action.id}
                    onClick={() => { handleAction(action); setShowMore(false); }}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-white/80 hover:bg-white/10 transition-colors cursor-pointer text-left w-full"
                    disabled={disabled || locked}
                  >
                    <ActionIcon icon={action.icon} />
                    {action.label}
                    {locked && <Lock className="w-2.5 h-2.5 text-white/30 ml-auto" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * CapabilityBadge — Phase 24 Universal Capture
 *
 * Shows platform + source_type + support_level + warnings after URL paste.
 * Receives a ResolvedItem from useResolveInput.
 */
import { CheckCircle, AlertCircle, Clock, Lock, Wifi, Info } from 'lucide-react';

const SUPPORT_CONFIG = {
  full: {
    color: 'text-emerald-400',
    bg:    'bg-emerald-500/10',
    border:'border-emerald-500/25',
    Icon:  CheckCircle,
  },
  partial: {
    color: 'text-amber-400',
    bg:    'bg-amber-500/10',
    border:'border-amber-500/25',
    Icon:  AlertCircle,
  },
  cookie_required: {
    color: 'text-blue-400',
    bg:    'bg-blue-500/10',
    border:'border-blue-500/25',
    Icon:  Lock,
  },
  proxy_required: {
    color: 'text-purple-400',
    bg:    'bg-purple-500/10',
    border:'border-purple-500/25',
    Icon:  Wifi,
  },
  experimental: {
    color: 'text-slate-400',
    bg:    'bg-slate-500/10',
    border:'border-slate-500/25',
    Icon:  Info,
  },
  temporarily_disabled: {
    color: 'text-red-400',
    bg:    'bg-red-500/10',
    border:'border-red-500/25',
    Icon:  Clock,
  },
  unsupported: {
    color: 'text-red-400',
    bg:    'bg-red-500/10',
    border:'border-red-500/25',
    Icon:  AlertCircle,
  },
};

const ACTION_LABELS = {
  video:         '🎬 Video',
  audio:         '🎵 Audio',
  no_watermark:  '✨ Không watermark',
  subtitle:      '💬 Phụ đề',
  '4k':          '🔆 4K',
  image:         '🖼️ Ảnh',
  carousel:      '📚 Carousel',
  gif:           '🌀 GIF',
  batch_video:   '📦 Batch video',
  batch_audio:   '📦 Batch audio',
  batch_image:   '📦 Batch ảnh',
  batch_mp3:     '📦 Batch MP3',
  batch_post:    '📦 Batch bài',
  browse_tracks: '🎛️ Duyệt track',
  artist_preview:'🎤 Preview nghệ sĩ',
};

export default function CapabilityBadge({ result, loading, className = '' }) {
  if (loading) {
    return (
      <div className={`flex items-center gap-2 ${className}`}>
        <div className="h-5 w-20 bg-slate-700/50 rounded animate-pulse" />
        <div className="h-5 w-28 bg-slate-700/40 rounded animate-pulse" />
      </div>
    );
  }

  if (!result || result.platform === 'unknown') return null;

  const cfg = SUPPORT_CONFIG[result.support_level] || SUPPORT_CONFIG.partial;
  const { Icon } = cfg;

  const isShortLink = result.is_short_link;
  const showWarnings = result.warnings?.length > 0;

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      {/* Platform badge */}
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#0d2821]/80 border border-slate-700/40 text-xs font-semibold text-white">
        <span>{result.platform_emoji}</span>
        <span>{result.platform_label}</span>
      </span>

      {/* Source type badge */}
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-slate-700/30 border border-slate-600/30 text-xs text-slate-300">
        {result.source_type_label}
      </span>

      {/* Support level */}
      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full border text-xs font-semibold ${cfg.bg} ${cfg.border} ${cfg.color}`}>
        <Icon className="w-3 h-3" />
        {result.support_level_label}
      </span>

      {/* Short-link indicator */}
      {isShortLink && (
        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-slate-600/20 border border-slate-600/30 text-[10px] text-slate-500">
          🔗 Rút gọn
        </span>
      )}

      {/* Warning chips */}
      {showWarnings && result.warnings.slice(0, 1).map((w, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-amber-500/8 border border-amber-500/20 text-[10px] text-amber-400/80 max-w-[200px] truncate"
          title={w}
        >
          ⚠ {w}
        </span>
      ))}
    </div>
  );
}

/**
 * CapabilityDetail — expanded view shown in a card below the badge.
 * Optional; used in intake overlays or batch preview.
 */
export function CapabilityDetail({ result }) {
  if (!result || result.platform === 'unknown') return null;
  const cfg = SUPPORT_CONFIG[result.support_level] || SUPPORT_CONFIG.partial;

  return (
    <div className={`rounded-xl border p-3 ${cfg.bg} ${cfg.border} space-y-2`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-base">{result.platform_emoji}</span>
        <span className={`text-sm font-bold ${cfg.color}`}>{result.platform_label}</span>
        <span className="text-slate-400 text-xs">— {result.source_type_label}</span>
      </div>

      {/* Actions */}
      {result.supported_actions?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {result.supported_actions.map(a => (
            <span key={a} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-300 border border-slate-600/30">
              {ACTION_LABELS[a] || a}
            </span>
          ))}
        </div>
      )}

      {/* Warnings */}
      {result.warnings?.map((w, i) => (
        <p key={i} className="text-[11px] text-amber-400/80 flex items-start gap-1.5">
          <span className="flex-shrink-0">⚠</span>
          <span>{w}</span>
        </p>
      ))}
    </div>
  );
}

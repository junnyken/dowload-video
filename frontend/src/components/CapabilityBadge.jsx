/**
 * CapabilityBadge — Phase 24 Universal Capture
 *
 * Shows platform + source_type + support_level + warnings after URL paste.
 * Receives a ResolvedItem from useResolveInput.
 */
import { CheckCircle, AlertCircle, Clock, Lock, Wifi, Info } from 'lucide-react';

const SUPPORT_CONFIG = {
  full: {
    color: 'text-success',
    bg:    'bg-success-soft',
    border:'border-success/25',
    Icon:  CheckCircle,
  },
  partial: {
    color: 'text-accent-text',
    bg:    'bg-accent-soft',
    border:'border-accent/25',
    Icon:  AlertCircle,
  },
  cookie_required: {
    color: 'text-fg-2',
    bg:    'bg-surface-2',
    border:'border-line',
    Icon:  Lock,
  },
  proxy_required: {
    color: 'text-fg-2',
    bg:    'bg-surface-2',
    border:'border-line',
    Icon:  Wifi,
  },
  experimental: {
    color: 'text-fg-muted',
    bg:    'bg-line-strong',
    border:'border-line-strong',
    Icon:  Info,
  },
  temporarily_disabled: {
    color: 'text-danger',
    bg:    'bg-danger-soft',
    border:'border-danger/25',
    Icon:  Clock,
  },
  unsupported: {
    color: 'text-danger',
    bg:    'bg-danger-soft',
    border:'border-danger/25',
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
        <div className="h-5 w-20 bg-surface-2 rounded animate-pulse" />
        <div className="h-5 w-28 bg-surface-2 rounded animate-pulse" />
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
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface/80 border border-line text-xs font-semibold text-fg">
        <span>{result.platform_emoji}</span>
        <span>{result.platform_label}</span>
      </span>

      {/* Source type badge */}
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-surface-2 border border-line-strong text-xs text-fg-2">
        {result.source_type_label}
      </span>

      {/* Support level */}
      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full border text-xs font-semibold ${cfg.bg} ${cfg.border} ${cfg.color}`}>
        <Icon className="w-3 h-3" />
        {result.support_level_label}
      </span>

      {/* Short-link indicator */}
      {isShortLink && (
        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-line border border-line-strong text-[10px] text-fg-muted">
          🔗 Rút gọn
        </span>
      )}

      {/* Warning chips */}
      {showWarnings && result.warnings.slice(0, 1).map((w, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-accent-soft border border-accent/20 text-[10px] text-accent-text max-w-[200px] truncate"
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
        <span className="text-fg-muted text-xs">— {result.source_type_label}</span>
      </div>

      {/* Actions */}
      {result.supported_actions?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {result.supported_actions.map(a => (
            <span key={a} className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-fg-2 border border-line-strong">
              {ACTION_LABELS[a] || a}
            </span>
          ))}
        </div>
      )}

      {/* Warnings */}
      {result.warnings?.map((w, i) => (
        <p key={i} className="text-[11px] text-accent-text flex items-start gap-1.5">
          <span className="flex-shrink-0">⚠</span>
          <span>{w}</span>
        </p>
      ))}
    </div>
  );
}

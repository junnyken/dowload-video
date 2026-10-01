/**
 * Phase 14 — Lifecycle Banner Component
 * Shows a contextual nudge bar under the main content area.
 */
import { X, Zap, AppWindow as Chrome, Send, Key, ArrowUpCircle } from 'lucide-react';
import { trackEvent, EVENT } from '../utils/trackEvent';
import { useEffect } from 'react';

const BANNER_CONFIG = {
  new_user: {
    icon: Zap,
    color: 'bg-surface-2 border-line',
    text: 'Chào mừng! Dán link video bất kỳ để tải xuống ngay — YouTube, TikTok, Facebook và hơn 30 nền tảng.',
    cta: null,
  },
  first_success: {
    icon: Zap,
    color: 'bg-success-soft border-success/30',
    text: 'Tuyệt! Cài extension Chrome để tải nhanh hơn ngay từ bất kỳ trang nào.',
    cta: { label: 'Cài Extension', action: 'extension' },
  },
  quota_warning: {
    icon: ArrowUpCircle,
    color: 'bg-accent-soft border-accent/30',
    text: 'Bạn đã dùng hơn 80% quota hôm nay. Nâng cấp Pro để tải không giới hạn.',
    cta: { label: 'Nâng cấp Pro', action: 'upgrade' },
  },
  quota_reached: {
    icon: ArrowUpCircle,
    color: 'bg-danger-soft border-danger/30',
    text: 'Bạn đã hết quota hôm nay. Nâng cấp Pro để tiếp tục tải không giới hạn.',
    cta: { label: 'Nâng cấp Pro ngay', action: 'upgrade' },
  },
  paywall_hit: {
    icon: ArrowUpCircle,
    color: 'bg-surface-2 border-line',
    text: 'Tính năng này dành cho Pro — dùng nhiều tài nguyên hơn và cho phép chất lượng cao nhất.',
    cta: { label: 'Xem gói Pro', action: 'upgrade' },
  },
  extension_nudge: {
    icon: Chrome,
    color: 'bg-surface-2 border-line',
    text: 'Đang dùng desktop? Cài extension để tải ngay từ tab đang xem, không cần dán link.',
    cta: { label: 'Cài Extension', action: 'extension' },
  },
  telegram_nudge: {
    icon: Send,
    color: 'bg-surface-2 border-line',
    text: 'Trên mobile? Dùng Telegram bot @vidgrab_bot để tải nhanh chóng ngay trong app nhắn tin.',
    cta: { label: 'Mở Bot', action: 'telegram' },
  },
  pro_power_user: {
    icon: Key,
    color: 'bg-accent-soft border-accent/30',
    text: 'Bạn là Pro — hãy tạo API key để tích hợp VidGrab vào workflow của bạn.',
    cta: { label: 'Tạo API Key', action: 'api_key' },
  },
};

const ACTIONS = {
  upgrade:   () => { document.querySelector('[data-upgrade-trigger]')?.click(); },
  extension: () => { window.open('/install', '_blank'); },
  telegram:  () => { window.open('https://t.me/vidgrab_bot', '_blank'); },
  api_key:   () => { window.location.hash = '#/api-keys'; },
};

export default function LifecycleBanner({ banner, onDismiss }) {
  useEffect(() => {
    if (banner) {
      trackEvent(EVENT.NUDGE_SHOWN, { nudge_type: banner.type, trigger: banner.trigger });
    }
  }, [banner?.type]);

  if (!banner) return null;
  const cfg = BANNER_CONFIG[banner.type];
  if (!cfg) return null;

  const Icon = cfg.icon;

  function handleCta() {
    trackEvent(EVENT.NUDGE_CLICKED, { nudge_type: banner.type, action: cfg.cta?.action });
    onDismiss(banner.type);
    if (cfg.cta?.action && ACTIONS[cfg.cta.action]) {
      ACTIONS[cfg.cta.action]();
    }
  }

  function handleDismiss() {
    trackEvent(EVENT.NUDGE_DISMISSED, { nudge_type: banner.type });
    onDismiss(banner.type);
  }

  return (
    <div className={`relative flex items-center gap-3 px-4 py-3 rounded-xl border ${cfg.color} text-sm mb-3 animate-fade-in`}>
      <Icon className="w-4 h-4 flex-shrink-0 text-fg-2" />
      <span className="flex-1 text-fg-2">{cfg.text}</span>
      {cfg.cta && (
        <button
          onClick={handleCta}
          className="flex-shrink-0 px-3 py-1.5 rounded-lg bg-surface-2 hover:bg-line text-fg text-xs font-semibold transition border border-line cursor-pointer"
        >
          {cfg.cta.label}
        </button>
      )}
      <button onClick={handleDismiss} className="flex-shrink-0 text-fg-muted hover:text-fg transition cursor-pointer p-0.5">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

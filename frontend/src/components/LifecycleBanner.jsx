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
    color: 'from-indigo-500/20 to-purple-500/20 border-indigo-500/30',
    text: 'Chào mừng! Dán link video bất kỳ để tải xuống ngay — YouTube, TikTok, Facebook và hơn 30 nền tảng.',
    cta: null,
  },
  first_success: {
    icon: Zap,
    color: 'from-emerald-500/20 to-teal-500/20 border-emerald-500/30',
    text: 'Tuyệt! Cài extension Chrome để tải nhanh hơn ngay từ bất kỳ trang nào.',
    cta: { label: 'Cài Extension', action: 'extension' },
  },
  quota_warning: {
    icon: ArrowUpCircle,
    color: 'from-amber-500/20 to-orange-500/20 border-amber-500/30',
    text: 'Bạn đã dùng hơn 80% quota hôm nay. Nâng cấp Pro để tải không giới hạn.',
    cta: { label: 'Nâng cấp Pro', action: 'upgrade' },
  },
  quota_reached: {
    icon: ArrowUpCircle,
    color: 'from-red-500/20 to-rose-500/20 border-red-500/30',
    text: 'Bạn đã hết quota hôm nay. Nâng cấp Pro để tiếp tục tải không giới hạn.',
    cta: { label: 'Nâng cấp Pro ngay', action: 'upgrade' },
  },
  paywall_hit: {
    icon: ArrowUpCircle,
    color: 'from-purple-500/20 to-pink-500/20 border-purple-500/30',
    text: 'Tính năng này dành cho Pro — dùng nhiều tài nguyên hơn và cho phép chất lượng cao nhất.',
    cta: { label: 'Xem gói Pro', action: 'upgrade' },
  },
  extension_nudge: {
    icon: Chrome,
    color: 'from-blue-500/20 to-indigo-500/20 border-blue-500/30',
    text: 'Đang dùng desktop? Cài extension để tải ngay từ tab đang xem, không cần dán link.',
    cta: { label: 'Cài Extension', action: 'extension' },
  },
  telegram_nudge: {
    icon: Send,
    color: 'from-cyan-500/20 to-blue-500/20 border-cyan-500/30',
    text: 'Trên mobile? Dùng Telegram bot @vidgrab_bot để tải nhanh chóng ngay trong app nhắn tin.',
    cta: { label: 'Mở Bot', action: 'telegram' },
  },
  pro_power_user: {
    icon: Key,
    color: 'from-amber-500/20 to-yellow-500/20 border-amber-500/30',
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
    <div className={`relative flex items-center gap-3 px-4 py-3 rounded-xl border bg-gradient-to-r ${cfg.color} text-sm mb-3 animate-fade-in`}>
      <Icon className="w-4 h-4 flex-shrink-0 text-white/70" />
      <span className="flex-1 text-slate-200">{cfg.text}</span>
      {cfg.cta && (
        <button
          onClick={handleCta}
          className="flex-shrink-0 px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white text-xs font-semibold transition border border-white/20 cursor-pointer"
        >
          {cfg.cta.label}
        </button>
      )}
      <button onClick={handleDismiss} className="flex-shrink-0 text-slate-400 hover:text-white transition cursor-pointer p-0.5">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

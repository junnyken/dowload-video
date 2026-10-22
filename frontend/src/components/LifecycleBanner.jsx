/**
 * Phase 14 — Lifecycle Banner Component
 * Shows a contextual nudge bar under the main content area.
 */
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';
import { X, Zap, AppWindow as Chrome, Send, Key, ArrowUpCircle } from 'lucide-react';
import { trackEvent, EVENT } from '../utils/trackEvent';
import { useEffect } from 'react';

const BANNER_CONFIG = {
  new_user: {
    icon: Zap,
    color: 'bg-surface-2 border-line',
    // text: guide.json banner.<type>.text
    cta: null,
  },
  first_success: {
    icon: Zap,
    color: 'bg-success-soft border-success/30',
    // text: guide.json banner.<type>.text
    cta: { action: 'extension' },
  },
  quota_warning: {
    icon: ArrowUpCircle,
    color: 'bg-accent-soft border-accent/30',
    // text: guide.json banner.<type>.text (wording: BA review)
    cta: { action: 'upgrade' },
  },
  quota_reached: {
    icon: ArrowUpCircle,
    color: 'bg-danger-soft border-danger/30',
    // text: guide.json banner.<type>.text (wording: BA review)
    cta: { action: 'upgrade' },
  },
  paywall_hit: {
    icon: ArrowUpCircle,
    color: 'bg-surface-2 border-line',
    // text: guide.json banner.<type>.text (wording: BA review)
    cta: { action: 'upgrade' },
  },
  extension_nudge: {
    icon: Chrome,
    color: 'bg-surface-2 border-line',
    // text: guide.json banner.<type>.text
    cta: { action: 'extension' },
  },
  telegram_nudge: {
    icon: Send,
    color: 'bg-surface-2 border-line',
    // text: guide.json banner.<type>.text
    cta: { action: 'telegram' },
  },
  pro_power_user: {
    icon: Key,
    color: 'bg-accent-soft border-accent/30',
    // text: guide.json banner.<type>.text
    cta: { action: 'api_key' },
  },
};

const ACTIONS = {
  upgrade:   () => { document.querySelector('[data-upgrade-trigger]')?.click(); },
  extension: (lp) => { window.open(lp('/install'), '_blank'); },
  telegram:  () => { window.open('https://t.me/vidgrab_bot', '_blank'); },
  api_key:   () => { window.location.hash = '#/api-keys'; },
};

export default function LifecycleBanner({ banner, onDismiss }) {
  const { t } = useTranslation('guide');
  const lp = useLocalePath();
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
      ACTIONS[cfg.cta.action](lp);
    }
  }

  function handleDismiss() {
    trackEvent(EVENT.NUDGE_DISMISSED, { nudge_type: banner.type });
    onDismiss(banner.type);
  }

  return (
    <div className={`relative flex items-center gap-3 px-4 py-3 rounded-xl border ${cfg.color} text-sm mb-3 animate-fade-in`}>
      <Icon className="w-4 h-4 flex-shrink-0 text-fg-2" />
      <span className="flex-1 text-fg-2">{t(`banner.${banner.type}.text`)}</span>
      {cfg.cta && (
        <button
          onClick={handleCta}
          className="flex-shrink-0 px-3 py-1.5 rounded-lg bg-surface-2 hover:bg-line text-fg text-xs font-semibold transition border border-line cursor-pointer"
        >
          {t(`banner.${banner.type}.cta`)}
        </button>
      )}
      <button onClick={handleDismiss} className="flex-shrink-0 text-fg-muted hover:text-fg transition cursor-pointer p-0.5">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

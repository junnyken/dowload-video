import { Lock, Crown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useEntitlement } from '../hooks/useEntitlement';

// Descriptions only for features the plan table (GET /api/v1/plans) really
// lists. Cloud save, chapters, logo removal, priority queue and team
// workspace were advertised here but no code enforces them, so they are gone.
// wording: BA review
// Texts live in locales/*/usage.json (paywall.desc.<feature>).
const FEATURE_DESCRIPTIONS = new Set(['bulk_zip', 'youtube_video', 'ai_tools', 'spotify_full', 'api_key', 'webhook']);

function navigate(path) {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function DefaultOverlay({ feature }) {
  const { t } = useTranslation('usage');
  const description = FEATURE_DESCRIPTIONS.has(feature) ? t(`paywall.desc.${feature}`) : t('paywall.descDefault');
  const planLabel   = 'Pro';

  return (
    <div
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3
                 bg-surface/80 backdrop-blur-sm rounded-xl px-6 text-center"
    >
      <div className="w-12 h-12 rounded-full bg-surface border border-line
                      flex items-center justify-center">
        <Lock className="w-5 h-5 text-fg-muted" aria-hidden="true" />
      </div>

      <div className="space-y-1">
        <p className="text-fg font-semibold text-sm flex items-center justify-center gap-1.5">
          <Crown className="w-4 h-4 text-accent-text" aria-hidden="true" />
          {t('paywall.featureTitle', { plan: planLabel })}
        </p>
        <p className="text-fg-muted text-xs max-w-[240px] leading-relaxed">
          {description}
        </p>
      </div>

      <button
        onClick={() => navigate('/pricing')}
        className="mt-1 px-4 py-2 rounded-lg bg-accent hover:opacity-90
                   text-accent-fg text-xs font-semibold transition-colors focus:outline-none
                   focus:ring-2 focus:ring-accent focus:ring-offset-2
                   focus:ring-offset-canvas"
      >
        {t('paywall.upgradeTo', { plan: planLabel })}
      </button>
    </div>
  );
}

/**
 * PaywallGate — wraps children with a paywall overlay when the user lacks
 * the required feature. When allowed, renders children normally.
 *
 * Props:
 *   feature      — entitlement key (e.g. 'ai_tools', 'bulk_zip')
 *   requiredPlan — display label ('pro' | 'team' | 'enterprise')
 *   children     — content to gate
 *   fallback     — optional custom ReactNode instead of default overlay
 */
export default function PaywallGate({
  feature,
  requiredPlan = 'pro',
  children,
  fallback,
}) {
  const { allowed, loading } = useEntitlement();

  // While loading, render children without overlay to avoid layout shift
  if (loading) {
    return <div className="relative">{children}</div>;
  }

  const isAllowed = allowed(feature);

  if (isAllowed) {
    return <>{children}</>;
  }

  return (
    <div className="relative">
      {/* Blur + pointer-events disabled so underlying content is visible but inaccessible */}
      <div className="select-none pointer-events-none opacity-40 blur-[1px]" aria-hidden="true">
        {children}
      </div>

      {fallback !== undefined ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center">
          {fallback}
        </div>
      ) : (
        <DefaultOverlay feature={feature} requiredPlan={requiredPlan} />
      )}
    </div>
  );
}

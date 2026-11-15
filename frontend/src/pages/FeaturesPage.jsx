import {
  Sparkles, Monitor, Puzzle, Cloud, Crown, CheckCircle2,
  Subtitles, Volume2, RefreshCw, FolderOpen, Wand2, MousePointerClick,
  ShieldCheck, ArrowRight, Zap,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';
import { CHROME_STORE_URL } from '../lib/chromeExtension';
import { trackEvent, EVENT } from '../utils/trackEvent';

const PILLAR_ICONS = [Cloud, Puzzle, Monitor];
const CARD_ICONS = [MousePointerClick, Subtitles, Volume2, RefreshCw, FolderOpen, Wand2];

export default function FeaturesPage({ onNavigate }) {
  const { t } = useTranslation('features');
  const lp = useLocalePath();

  const pillars = t('pillars', { returnObjects: true }) || [];
  const cards = t('cards', { returnObjects: true }) || [];
  const tiers = t('tiers', { returnObjects: true }) || [];

  const go = (view, path) => (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    if (onNavigate) onNavigate(view, path);
    window.scrollTo(0, 0);
  };

  const badgeStyle = (tone) => {
    if (tone === 'team') return 'bg-accent text-accent-fg border-accent';
    if (tone === 'pro') return 'bg-accent-soft text-accent-text border-accent/40';
    return 'bg-surface border-line-strong text-fg-2';
  };

  return (
    <div className="bg-canvas text-fg">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        {/* ── Hero Section ─────────────────────────────────────────── */}
        <header className="text-center max-w-3xl mx-auto mb-12 sm:mb-16">
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-accent-soft border border-accent/30 text-accent-text text-xs font-bold mb-4">
            <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
            {t('badge')}
          </div>
          <h1 className="text-2xl sm:text-4xl md:text-5xl font-extrabold tracking-tight text-fg leading-tight">
            {t('heroTitle')}
          </h1>
          <p className="mt-4 text-sm sm:text-base text-fg-muted leading-relaxed">
            {t('heroSub')}
          </p>
          <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
            <a
              href={lp('/pricing')}
              onClick={go('pricing', '/pricing')}
              className="inline-flex items-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg text-sm font-bold hover:opacity-90 transition shadow-sm"
            >
              <Crown className="w-4 h-4" aria-hidden="true" />
              {t('ctaPricing')}
            </a>
            <a
              href={lp('/download')}
              onClick={go('download', '/download')}
              className="inline-flex items-center gap-2 px-4 py-3 rounded-xl border border-line-strong bg-surface-2 text-fg text-sm font-semibold hover:border-accent hover:text-accent-text transition"
            >
              <Monitor className="w-4 h-4 text-accent-text" aria-hidden="true" />
              {t('ctaWindows')}
            </a>
            <a
              href={CHROME_STORE_URL}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => trackEvent(EVENT.EXTENSION_INSTALL_CLICK, { from: 'features_hero' })}
              className="inline-flex items-center gap-2 px-4 py-3 rounded-xl border border-line bg-surface text-fg-2 text-sm font-semibold hover:text-fg hover:border-line-strong transition"
            >
              <Puzzle className="w-4 h-4 text-accent-text" aria-hidden="true" />
              {t('ctaExtension')}
            </a>
          </div>
        </header>

        {/* ── 3-Pillar Ecosystem Overview ──────────────────────────── */}
        <section aria-labelledby="pillars-heading" className="mb-14 sm:mb-20">
          <div className="text-center mb-7">
            <h2 id="pillars-heading" className="text-xl sm:text-2xl font-bold text-fg">
              {t('pillarsTitle')}
            </h2>
            <p className="mt-1.5 text-sm text-fg-muted">{t('pillarsSub')}</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {Array.isArray(pillars) && pillars.map((p, idx) => {
              const Icon = PILLAR_ICONS[idx] || Cloud;
              return (
                <div key={p.tag || idx} className="rounded-2xl border border-line bg-surface-2 p-5 sm:p-6 flex flex-col">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-accent-text">
                      {p.tag}
                    </span>
                    <div className="w-9 h-9 rounded-xl bg-accent-soft border border-accent/25 flex items-center justify-center">
                      <Icon className="w-4 h-4 text-accent-text" aria-hidden="true" />
                    </div>
                  </div>
                  <h3 className="text-base font-bold text-fg mb-2">{p.title}</h3>
                  <p className="text-xs sm:text-sm text-fg-muted leading-relaxed">{p.desc}</p>
                </div>
              );
            })}
          </div>
        </section>

        {/* ── 6 Flagship Feature Cards ─────────────────────────────── */}
        <section aria-labelledby="flagship-heading" className="mb-14 sm:mb-20">
          <div className="text-center max-w-2xl mx-auto mb-8">
            <h2 id="flagship-heading" className="text-xl sm:text-3xl font-extrabold text-fg tracking-tight">
              {t('flagshipTitle')}
            </h2>
            <p className="mt-2 text-sm text-fg-muted">{t('flagshipSub')}</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {Array.isArray(cards) && cards.map((card, idx) => {
              const Icon = CARD_ICONS[idx] || Sparkles;
              return (
                <article
                  key={card.title || idx}
                  className="rounded-2xl border border-line bg-surface-2 p-5 sm:p-6 flex flex-col justify-between hover:border-accent/40 transition-colors"
                >
                  <div>
                    <div className="flex items-center justify-between gap-3 mb-4">
                      <div className="w-10 h-10 rounded-xl bg-surface border border-line flex items-center justify-center shrink-0">
                        <Icon className="w-5 h-5 text-accent-text" aria-hidden="true" />
                      </div>
                      <span className={`px-2.5 py-1 rounded-full border text-[11px] font-bold ${badgeStyle(card.tierTone)}`}>
                        {card.tier}
                      </span>
                    </div>
                    <h3 className="text-base sm:text-lg font-bold text-fg mb-2">
                      {card.title}
                    </h3>
                    <p className="text-xs sm:text-sm text-fg-muted leading-relaxed mb-4">
                      {card.desc}
                    </p>
                  </div>
                  <ul className="space-y-2 pt-3 border-t border-line">
                    {Array.isArray(card.highlights) && card.highlights.map((h, hIdx) => (
                      <li key={hIdx} className="flex items-start gap-2 text-xs text-fg-2">
                        <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" aria-hidden="true" />
                        <span>{h}</span>
                      </li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </section>

        {/* ── Paid-Tier Value Matrix (Starter vs Pro vs Team) ──────── */}
        <section aria-labelledby="tier-matrix-heading" className="mb-12">
          <div className="text-center max-w-2xl mx-auto mb-8">
            <h2 id="tier-matrix-heading" className="text-xl sm:text-3xl font-extrabold text-fg tracking-tight">
              {t('tierMatrixTitle')}
            </h2>
            <p className="mt-2 text-sm text-fg-muted">{t('tierMatrixSub')}</p>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            {Array.isArray(tiers) && tiers.map((tier) => {
              const isPro = tier.id === 'pro';
              const isTeam = tier.id === 'team';
              return (
                <div
                  key={tier.id}
                  className={`rounded-2xl border p-5 sm:p-6 flex flex-col justify-between bg-surface-2 ${
                    isTeam
                      ? 'border-accent shadow-lg ring-2 ring-accent/30'
                      : isPro
                        ? 'border-accent/60'
                        : 'border-line'
                  }`}
                >
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                        isTeam || isPro ? 'bg-accent text-accent-fg' : 'bg-surface border border-line text-fg-2'
                      }`}>
                        {tier.badge}
                      </span>
                    </div>
                    <h3 className="text-lg font-extrabold text-fg mt-2">{tier.name}</h3>
                    <p className="text-sm font-bold text-accent-text mt-1">{tier.priceHint}</p>
                    <p className="text-xs text-fg-muted mt-2 pb-4 border-b border-line leading-relaxed">
                      {tier.who}
                    </p>
                    <ul className="space-y-2.5 my-4">
                      {Array.isArray(tier.perks) && tier.perks.map((perk, pIdx) => (
                        <li key={pIdx} className="flex items-start gap-2 text-xs sm:text-sm text-fg">
                          <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" aria-hidden="true" />
                          <span>{perk}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <a
                    href={`${lp('/checkout')}?plan=${tier.planParam}`}
                    className={`mt-2 w-full inline-flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-bold transition ${
                      isTeam || isPro
                        ? 'bg-accent text-accent-fg hover:opacity-90'
                        : 'border border-line-strong bg-surface text-fg hover:border-accent hover:text-accent-text'
                    }`}
                  >
                    <Zap className="w-4 h-4" aria-hidden="true" />
                    {tier.cta}
                  </a>
                </div>
              );
            })}
          </div>
        </section>

        {/* ── DRM & Copyright Compliance Note ──────────────────────── */}
        <div className="rounded-xl border border-line bg-surface px-4 py-3.5 flex items-start gap-3 mb-12">
          <ShieldCheck className="w-5 h-5 text-success shrink-0 mt-0.5" aria-hidden="true" />
          <p className="text-xs text-fg-muted leading-relaxed">
            {t('complianceNote')}
          </p>
        </div>

        {/* ── Bottom CTA Banner ────────────────────────────────────── */}
        <section className="rounded-2xl border border-accent/30 bg-surface-2 p-6 sm:p-8 text-center">
          <h2 className="text-lg sm:text-2xl font-extrabold text-fg">
            {t('bottomTitle')}
          </h2>
          <p className="mt-2 text-xs sm:text-sm text-fg-muted max-w-xl mx-auto">
            {t('bottomSub')}
          </p>
          <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
            <a
              href={lp('/pricing')}
              onClick={go('pricing', '/pricing')}
              className="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-xl bg-accent text-accent-fg text-xs sm:text-sm font-bold hover:opacity-90 transition"
            >
              {t('bottomBtnPricing')}
              <ArrowRight className="w-4 h-4" aria-hidden="true" />
            </a>
            <a
              href={lp('/')}
              onClick={go('landing', '/')}
              className="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-line-strong bg-surface text-fg text-xs sm:text-sm font-semibold hover:border-accent hover:text-accent-text transition"
            >
              {t('bottomBtnHome')}
            </a>
          </div>
        </section>
      </div>
    </div>
  );
}

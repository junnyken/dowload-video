import { useEffect, useState } from 'react';
import { Check, X, Crown, Copy, Loader2 } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';
import { usePlans, formatVnd } from '../lib/usePlans';
import { buildPlanRows } from '../lib/planRows';
import { useTranslation } from 'react-i18next';
import { useLang } from '../i18n';

// Pricing page — Khách / Free / Pro. Every number comes from GET /api/v1/plans
// (backend app/core/plans.py); nothing about limits is written in this file.
// Payment is a manual bank transfer: an admin turns Pro on once the money
// arrives. wording: BA review

// Tier names / sublines live in locales/*/pricing.json (tier.<id>.name|sub).
const TIER_IDS = ['guest', 'free', 'pro'];

function Cell({ value }) {
  const { t } = useTranslation('pricing');
  if (value === true) return <Check className="w-4 h-4 text-success shrink-0" aria-label={t('yes')} />;
  if (value === false) return <X className="w-4 h-4 text-fg-muted shrink-0" aria-label={t('no')} />;
  return <span className="text-fg text-sm break-words">{value}</span>;
}

function CopyButton({ text, label }) {
  const { t } = useTranslation('pricing');
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* clipboard blocked: user can still select the text */ }
  };
  return (
    <button type="button" onClick={copy} aria-label={label}
      className="shrink-0 inline-flex items-center gap-1 px-2.5 py-1.5 min-h-9 rounded-lg border border-line bg-surface text-fg-2 text-xs hover:bg-surface-2 transition-colors">
      {done ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      {done ? t('copied') : t('copy')} {/* wording: BA review */}
    </button>
  );
}

function InfoRow({ label, value, copy }) {
  const { t } = useTranslation('pricing');
  return (
    <div className="flex items-center justify-between gap-3 py-2 border-b border-line last:border-0">
      <div className="min-w-0">
        <p className="text-xs text-fg-muted">{label}</p>
        <p className="text-sm font-semibold text-fg break-words">{value || '—'}</p>
      </div>
      {copy && value ? <CopyButton text={value} label={t('copyLabel', { label })} /> : null}
    </div>
  );
}

function PaymentSection({ pay, pro, period }) {
  const { t } = useTranslation('pricing');
  const lang = useLang();
  const { isAuthenticated, session } = useAuth();
  const [me, setMe] = useState(null);
  const [err, setErr] = useState('');
  const token = session?.access_token;

  useEffect(() => {
    if (!token) return undefined;
    let alive = true;
    fetch(`${API_BASE}/api/v1/plans/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { if (alive) { setMe(d); setErr(''); } })
      .catch(() => { if (alive) setErr(t('payLoadError')); }); // wording: BA review
    return () => { alive = false; };
  }, [token, t]);

  const amount = period === 'year' ? pro.price_year_vnd : pro.price_month_vnd;
  const periodLabel = period === 'year' ? t('days365') : t('days30');

  const box = 'mt-10 rounded-2xl border border-line bg-surface-2 p-5 sm:p-6';
  const openLogin = () => window.dispatchEvent(new Event('vidgrab:open-auth'));

  if (!pay?.ready) {
    return (
      <section className={box} aria-labelledby="pay-h">
        <h2 id="pay-h" className="text-lg font-bold text-fg mb-2">{t('payTitle')}</h2>
        <p className="text-sm text-fg-2">
          {/* wording: BA review */}
          {pay?.contact ? t('contactToUpgrade', { contact: pay.contact }) : t('contactViaFeedback')}
        </p>
      </section>
    );
  }

  if (!isAuthenticated) {
    return (
      <section className={box} aria-labelledby="pay-h">
        <h2 id="pay-h" className="text-lg font-bold text-fg mb-2">{t('payTitle')}</h2>
        {/* wording: BA review */}
        <p className="text-sm text-fg-2 mb-4">{t('loginForCode')}</p>
        <button type="button" onClick={openLogin}
          className="px-5 py-2.5 min-h-11 rounded-xl bg-accent text-accent-fg text-sm font-semibold hover:opacity-90 transition">
          {t('login')}
        </button>
      </section>
    );
  }

  const isPro = me?.tier === 'pro';
  return (
    <section className={box} aria-labelledby="pay-h">
      <h2 id="pay-h" className="text-lg font-bold text-fg mb-1">{t('payProTitle')}</h2>
      {/* wording: BA review */}
      <p className="text-sm text-fg-muted mb-4">
        {t('payProIntro')}
      </p>
      {isPro && (
        <p className="mb-4 rounded-lg bg-accent-soft border border-accent/30 px-3 py-2 text-sm text-fg">
          {/* wording: BA review */}
          {me.expiry
            ? t('proActiveWithExpiry', { date: new Date(me.expiry).toLocaleDateString(lang === 'en' ? 'en-US' : 'vi-VN') })
            : t('proActive')}
        </p>
      )}
      {me && !isPro && (
        <p className="mb-4 text-sm text-fg-2">{t('currentFree')}{/* wording: BA review */}</p>
      )}
      {err && <p role="alert" className="mb-3 text-sm text-danger">{err}</p>}
      {!me && !err && (
        <p className="text-sm text-fg-muted flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> {t('loading')}</p>
      )}
      {me && (
        <div>
          <InfoRow label={t('bank')} value={pay.bank_name} />
          <InfoRow label={t('accountNo')} value={pay.bank_account} copy />
          <InfoRow label={t('accountHolder')} value={pay.bank_holder} />
          <InfoRow label={t('amount', { period: periodLabel })} value={formatVnd(amount)} />
          <InfoRow label={t('transferContent')} value={me.transfer_code} copy />
        </div>
      )}
      {pay.contact ? <p className="mt-4 text-xs text-fg-muted">{t('needHelp', { contact: pay.contact })}{/* wording: BA review */}</p> : null}
    </section>
  );
}

export default function PricingPage() {
  const { t } = useTranslation('pricing');
  const { ready, plans } = usePlans();
  const [period, setPeriod] = useState('month');

  // <title> / description: lib/seo.js (per language)

  const tiers = plans?.tiers || [];
  const pro = tiers.find((x) => x.id === 'pro');
  const rows = tiers.length ? buildPlanRows(tiers) : [];

  return (
    <div className="bg-canvas text-fg">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        <div className="text-center mb-8">
          <h1 className="text-2xl sm:text-4xl font-extrabold tracking-tight mb-3">{t('title')}</h1>
          {/* wording: BA review */}
          <p className="text-fg-muted text-sm sm:text-base max-w-xl mx-auto">
            {t('subtitle')}
          </p>
        </div>

        {!ready && (
          <div className="flex justify-center py-16" role="status" aria-label={t('loadingAria')}>
            <Loader2 className="w-6 h-6 animate-spin text-fg-muted" />
          </div>
        )}
        {ready && !plans && (
          <p role="alert" className="text-center text-sm text-danger py-12">
            {/* wording: BA review */}
            {t('plansLoadError')}
          </p>
        )}

        {plans && pro && (
          <>
            <div className="flex justify-center mb-8">
              <div role="group" aria-label={t('billingCycle')} className="inline-flex rounded-xl border border-line bg-surface p-1">
                {[['month', t('monthly')], ['year', t('yearly')]].map(([k, l]) => (
                  <button key={k} type="button" aria-pressed={period === k} onClick={() => setPeriod(k)}
                    className={`px-4 py-2 min-h-10 rounded-lg text-sm font-medium transition-colors ${period === k ? 'bg-accent text-accent-fg' : 'text-fg-2 hover:text-fg'}`}>
                    {l}{/* wording: BA review */}
                  </button>
                ))}
              </div>
            </div>

            <div className="plan-grid grid grid-cols-1 md:grid-cols-3 gap-4"
              style={{ '--plan-rows': rows.length + 1 }}>
              {tiers.map((tier) => {
                const meta = TIER_IDS.includes(tier.id)
                  ? { name: t(`tier.${tier.id}.name`), sub: t(`tier.${tier.id}.sub`) }
                  : { name: tier.id, sub: '' };
                const isPro = tier.id === 'pro';
                const price = isPro ? (period === 'year' ? tier.price_year_vnd : tier.price_month_vnd) : 0;
                return (
                  <article key={tier.id}
                    className={`plan-card min-w-0 rounded-2xl border p-5 bg-surface-2 ${isPro ? 'border-accent shadow-lg' : 'border-line'}`}>
                    <div className="pb-4">
                      <h2 className="flex items-center gap-2 text-lg font-bold">
                        {isPro && <Crown className="w-4 h-4 text-accent-text" aria-hidden="true" />}
                        {meta.name}
                      </h2>
                      <p className="text-xs text-fg-muted mb-3">{meta.sub}</p>
                      <p>
                        <span className="text-3xl font-extrabold">{isPro ? formatVnd(price) : '0đ'}</span>
                        {isPro && <span className="text-sm text-fg-muted"> / {period === 'year' ? t('perYear') : t('perMonth')}</span>}
                      </p>
                    </div>
                    {rows.map((r) => {
                      const v = r.get(tier);
                      const isIcon = typeof v === 'boolean';
                      return (
                        <div key={r.key} role="group" aria-label={r.label}
                          className={`border-t border-line py-2.5 min-h-12 flex gap-x-3 gap-y-0.5 ${isIcon ? 'flex-row items-center justify-between' : 'flex-col justify-center'}`}>
                          <span className="text-xs text-fg-muted">{r.label}</span>
                          <Cell value={v} />
                        </div>
                      );
                    })}
                  </article>
                );
              })}
            </div>

            <p className="mt-4 text-xs text-fg-muted text-center">
              {/* wording: BA review */}
              {t('resetNote', { time: plans.daily_reset_utc || '00:00' })}
            </p>

            <PaymentSection pay={plans.payment} pro={pro} period={period} />
          </>
        )}
      </div>
    </div>
  );
}

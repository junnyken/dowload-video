import { useEffect, useState } from 'react';
import { Check, X, Crown, Copy, Loader2, CreditCard, Key, Zap, Globe } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';
import { usePlans, formatVnd, formatUsd } from '../lib/usePlans';
import { buildPlanRows } from '../lib/planRows';
import { useTranslation } from 'react-i18next';
import { useLang, useLocalePath } from '../i18n';

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

function PaymentSection({ pay, pro, period, autoCheckout }) {
  const { t } = useTranslation('pricing');
  const lang = useLang();
  const { isAuthenticated, session } = useAuth();
  const [me, setMe] = useState(null);
  const [err, setErr] = useState('');
  const [stripeLoading, setStripeLoading] = useState(false);
  const [stripeErr, setStripeErr] = useState('');
  const [stripeSuccess, setStripeSuccess] = useState('');
  const token = session?.access_token;

  // Auto-capture PayPal order when returning from PayPal Checkout (?paypal_capture=1&token=ORDER_ID)
  useEffect(() => {
    if (!token) return;
    const params = new URLSearchParams(window.location.search);
    const ppOrderId = params.get('token');
    if (params.get('paypal_capture') !== '1' || !ppOrderId) return;
    setStripeLoading(true);
    setStripeErr('');
    fetch(`${API_BASE}/api/v1/payments/paypal-capture`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ order_id: ppOrderId }),
    })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
        setStripeSuccess(t('paypalSuccess'));
        window.history.replaceState({}, '', window.location.pathname);
        return fetch(`${API_BASE}/api/v1/plans/me`, { headers: { Authorization: `Bearer ${token}` } });
      })
      .then((r) => (r && r.ok ? r.json() : null))
      .then((d) => { if (d) setMe(d); })
      .catch((e) => setStripeErr(e.message || t('payLoadError')))
      .finally(() => setStripeLoading(false));
  }, [token, t]);

  const startStripeCheckout = async () => {
    if (!token) {
      window.dispatchEvent(new Event('vidgrab:open-auth'));
      return;
    }
    setStripeLoading(true);
    setStripeErr('');
    try {
      const res = await fetch(`${API_BASE}/api/v1/payments/create-checkout-session`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          period,
          success_url: `${window.location.origin}${lang === 'en' ? '/en/billing?success=1' : '/billing?success=1'}`,
          cancel_url: window.location.href,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail?.user_message || data?.detail || `HTTP ${res.status}`);
      }
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
      }
    } catch (e) {
      setStripeErr(e.message || t('payLoadError'));
    } finally {
      setStripeLoading(false);
    }
  };

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
  const usdAmount = period === 'year' ? (pro.price_year_usd || 49) : (pro.price_month_usd || 6.99);
  const periodLabel = period === 'year' ? t('days365') : t('days30');
  const periodShort = period === 'year' ? t('perYear') : t('perMonth');

  const box = 'mt-10 rounded-2xl border border-line bg-surface-2 p-5 sm:p-6';
  const openLogin = () => window.dispatchEvent(new Event('vidgrab:open-auth'));

  const stripeBlock = pay?.stripe_ready ? (
    <div className="mb-6 pb-6 border-b border-line">
      <h3 className="text-base font-bold text-fg flex items-center gap-2 mb-1">
        <CreditCard className="w-4 h-4 text-accent-text" aria-hidden="true" />
        {t('stripeTitle')}
      </h3>
      <p className="text-sm text-fg-muted mb-3">{t('stripeIntro')}</p>
      {stripeSuccess && <p role="status" className="mb-3 rounded-lg bg-success/15 border border-success/30 px-3 py-2 text-sm font-semibold text-success">{stripeSuccess}</p>}
      {stripeErr && <p role="alert" className="mb-3 text-sm text-danger">{stripeErr}</p>}
      <button
        type="button"
        onClick={startStripeCheckout}
        disabled={stripeLoading}
        className="inline-flex items-center gap-2 px-5 py-2.5 min-h-11 rounded-xl bg-accent text-accent-fg text-sm font-semibold hover:opacity-90 transition disabled:opacity-60 cursor-pointer"
      >
        {stripeLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CreditCard className="w-4 h-4" />}
        {stripeLoading
          ? t('stripeLoading')
          : t('stripeBtn', { usd: lang === 'en' ? usdAmount : `$${usdAmount}`, period: periodShort })}
      </button>
    </div>
  ) : null;

  if (!pay?.ready && !pay?.stripe_ready) {
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
  const checkoutHref = `${lang === 'en' ? '/en' : ''}/checkout`;
  return (
    <section className={box} aria-labelledby="pay-h">
      {autoCheckout?.enabled && (
        <div className="mb-6 pb-6 border-b border-line flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h3 className="text-base font-bold text-fg flex items-center gap-2 mb-1">
              ⚡ {lang === 'en' ? 'Instant Automated Checkout (VietQR & PayPal)' : 'Thanh toán tự động bằng QR Code & PayPal'}
            </h3>
            <p className="text-sm text-fg-muted">
              {lang === 'en'
                ? 'Generate your dynamic VietQR code or pay with PayPal — Pro activates automatically within seconds!'
                : 'Tạo mã VietQR động hoặc thanh toán qua PayPal — hệ thống tự động nâng cấp Pro ngay khi nhận tiền!'}
            </p>
          </div>
          <a
            href={checkoutHref}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 min-h-11 rounded-xl bg-accent text-accent-fg text-sm font-semibold hover:opacity-90 transition shrink-0"
          >
            <CreditCard className="w-4 h-4" />
            {lang === 'en' ? 'Go to Auto Checkout →' : 'Mở trang Thanh toán tự động →'}
          </a>
        </div>
      )}
      {stripeBlock}
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
  const lang = useLang();
  const lp = useLocalePath();
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
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-accent-soft border border-accent/30 text-accent-text text-xs font-bold mb-3">
            <Globe className="w-3.5 h-3.5" />
            {lang === 'en' ? 'GLOBAL PRICING (USD) · INSTANT ACTIVATION' : 'BẢNG GIÁ QUỐC TẾ (USD) · KÍCH HOẠT TỰ ĐỘNG'}
          </div>
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
                {[['month', t('monthly')], ['year', `${t('yearly')} (-42%)`]].map(([k, l]) => (
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
                const priceVnd = isPro ? (period === 'year' ? tier.price_year_vnd : tier.price_month_vnd) : 0;
                const priceUsd = isPro ? (period === 'year' ? (tier.price_year_usd || 49) : (tier.price_month_usd || 6.99)) : 0;
                return (
                  <article key={tier.id}
                    className={`plan-card min-w-0 rounded-2xl border p-5 bg-surface-2 ${isPro ? 'border-accent shadow-lg ring-1 ring-accent/30' : 'border-line'}`}>
                    <div className="pb-4">
                      <div className="flex items-center justify-between gap-2">
                        <h2 className="flex items-center gap-2 text-lg font-bold">
                          {isPro && <Crown className="w-4 h-4 text-accent-text" aria-hidden="true" />}
                          {meta.name}
                        </h2>
                        {isPro && (
                          <span className="px-2 py-0.5 rounded-full bg-accent text-accent-fg text-[10px] font-bold uppercase tracking-wider">
                            {lang === 'en' ? 'Most Popular' : 'Phổ biến nhất'}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-fg-muted mb-3">{meta.sub}</p>
                      <p className="flex items-baseline gap-1.5 flex-wrap">
                        <span className="text-3xl font-extrabold text-fg">{formatUsd(priceUsd)}</span>
                        {isPro && <span className="text-sm text-fg-muted"> / {period === 'year' ? t('perYear') : t('perMonth')}</span>}
                        {isPro && period === 'year' && (
                          <span className="ml-1 px-1.5 py-0.5 rounded bg-success/15 text-success text-[10px] font-bold">
                            {lang === 'en' ? 'Save 42% ($4.08/mo)' : 'Tiết kiệm 42% ($4.08/th)'}
                          </span>
                        )}
                      </p>
                      {isPro && priceVnd > 0 && (
                        <p className="text-[11px] text-fg-muted mt-1">
                          {lang === 'en'
                            ? `PayPal / Visa / Mastercard · or ${formatVnd(priceVnd)} via VietQR`
                            : `Thanh toán Quốc tế (PayPal/Thẻ) · hoặc ${formatVnd(priceVnd)} qua VietQR`}
                        </p>
                      )}
                      {isPro && (
                        <a
                          href={lp('/checkout')}
                          className="mt-3 w-full inline-flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl bg-accent text-accent-fg text-xs font-bold hover:opacity-90 transition shadow-sm"
                        >
                          <Zap className="w-3.5 h-3.5" />
                          {lang === 'en' ? 'Upgrade to Pro Now →' : 'Nâng cấp Pro ngay →'}
                        </a>
                      )}
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

            {/* Paid-Only Developer API & Commercial Priority Banner */}
            <div className="mt-8 rounded-2xl border border-accent/30 bg-surface-2 p-5 sm:p-6">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div className="space-y-1.5">
                  <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-accent-soft text-accent-text text-[11px] font-bold">
                    <Key className="w-3.5 h-3.5" />
                    {lang === 'en' ? 'DEVELOPER & COMMERCIAL REST API (PAID PLANS ONLY)' : 'DEVELOPER & COMMERCIAL REST API (CHỈ DÀNH CHO GÓI TRẢ PHÍ)'}
                  </div>
                  <h3 className="text-base sm:text-lg font-bold text-fg">
                    {lang === 'en'
                      ? 'Automate Video Extraction, Subtitles & Reup Processing via API'
                      : 'Tự động hóa Tải Video, Phụ đề & Xử lý Reup qua REST API'}
                  </h3>
                  <p className="text-xs sm:text-sm text-fg-muted max-w-2xl">
                    {lang === 'en'
                      ? 'API Keys are exclusively unlocked on paid plans. Pro ($6.99/mo) includes 3,000 API calls/month & up to 3 self-service keys. Need higher volume? Team/Business unlocks 15,000+ calls/month or subscribe on RapidAPI Hub.'
                      : 'Quyền tạo và sử dụng API Key chỉ mở khóa từ gói trả phí. Gói Pro ($6.99/tháng) tặng kèm 3.000 lượt gọi API/tháng & tối đa 3 API key cá nhân. Gói Team/Doanh nghiệp hỗ trợ 15.000+ lượt/tháng hoặc đăng ký qua chợ RapidAPI.'}
                  </p>
                </div>
                <div className="flex flex-wrap sm:flex-col gap-2 shrink-0">
                  <a
                    href={lp('/api-keys')}
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-2.5 rounded-xl bg-accent text-accent-fg text-xs font-bold hover:opacity-90 transition"
                  >
                    <Key className="w-3.5 h-3.5" />
                    {lang === 'en' ? 'Manage API Keys (Pro)' : 'Quản lý API Key (Pro)'}
                  </a>
                  <a
                    href={lp('/api-docs')}
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl border border-line bg-surface text-fg-2 hover:text-fg text-xs font-semibold transition"
                  >
                    {lang === 'en' ? 'View API Documentation →' : 'Xem Tài liệu API →'}
                  </a>
                </div>
              </div>
            </div>

            <p className="mt-4 text-xs text-fg-muted text-center">
              {/* wording: BA review */}
              {t('resetNote', { time: plans.daily_reset_utc || '00:00' })}
            </p>

            <PaymentSection pay={plans.payment} pro={pro} period={period} autoCheckout={plans.auto_checkout} />
          </>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Crown, ArrowLeft, Check, Copy, Loader2, QrCode, CreditCard,
  Clock, AlertTriangle, PartyPopper, ChevronRight, Shield, Zap,
  X, RefreshCw,
} from 'lucide-react';
import { supabase } from '../lib/supabaseClient';
import { API_BASE } from '../lib/apiBase';
import { useLang } from '../i18n';

// ── Helpers ──────────────────────────────────────────────────────────────────

const API = `${API_BASE}/api/v1`;

async function getToken() {
  const { data: { session } } = await supabase.auth.getSession();
  return session?.access_token || null;
}

async function apiFetch(path, { method = 'GET', body } = {}) {
  const token = await getToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail?.user_message || data?.detail || `HTTP ${res.status}`);
  return data;
}

function formatVnd(amount) {
  return new Intl.NumberFormat('vi-VN').format(amount) + '₫';
}

// ── Copy button ──────────────────────────────────────────────────────────────

function CopyButton({ text, className = '' }) {
  const { t } = useTranslation('checkout');
  const [done, setDone] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 2000);
    } catch { /* clipboard blocked */ }
  };

  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-line
                  bg-surface text-fg-2 text-xs font-medium hover:bg-surface-2
                  transition-colors shrink-0 ${className}`}
    >
      {done ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
      {done ? t('vietqr.copied') : t('vietqr.copy')}
    </button>
  );
}

// ── Countdown timer ──────────────────────────────────────────────────────────

function Countdown({ expiresAt, onExpired }) {
  const { t } = useTranslation('checkout');
  const [remaining, setRemaining] = useState(null);

  useEffect(() => {
    if (!expiresAt) return;
    const target = new Date(expiresAt).getTime();

    const tick = () => {
      const diff = Math.max(0, target - Date.now());
      setRemaining(diff);
      if (diff <= 0 && onExpired) onExpired();
    };

    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt, onExpired]);

  if (remaining === null) return null;

  const totalSeconds = Math.floor(remaining / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const isUrgent = totalSeconds < 600; // < 10 minutes

  return (
    <div className={`flex items-center gap-2 font-mono text-sm ${isUrgent ? 'text-danger' : 'text-fg-2'}`}>
      <Clock className="w-4 h-4 shrink-0" />
      <span>
        {t('vietqr.expires')}:{' '}
        <span className="font-bold">
          {String(hours).padStart(2, '0')}{t('countdown.hours')}{' '}
          {String(minutes).padStart(2, '0')}{t('countdown.minutes')}{' '}
          {String(seconds).padStart(2, '0')}{t('countdown.seconds')}
        </span>
      </span>
    </div>
  );
}

// ── Confetti animation (CSS-only lightweight) ────────────────────────────────

function Confetti() {
  return (
    <div className="fixed inset-0 pointer-events-none z-50 overflow-hidden" aria-hidden="true">
      {Array.from({ length: 50 }).map((_, i) => {
        const left = Math.random() * 100;
        const delay = Math.random() * 2;
        const duration = 2 + Math.random() * 2;
        const size = 6 + Math.random() * 8;
        const colors = ['#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#a855f7', '#ec4899'];
        const color = colors[Math.floor(Math.random() * colors.length)];
        return (
          <div
            key={i}
            className="absolute top-0 animate-confetti-fall"
            style={{
              left: `${left}%`,
              animationDelay: `${delay}s`,
              animationDuration: `${duration}s`,
              width: `${size}px`,
              height: `${size}px`,
              backgroundColor: color,
              borderRadius: Math.random() > 0.5 ? '50%' : '2px',
              transform: `rotate(${Math.random() * 360}deg)`,
            }}
          />
        );
      })}
      <style>{`
        @keyframes confetti-fall {
          0% { transform: translateY(-20px) rotate(0deg); opacity: 1; }
          100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
        }
        .animate-confetti-fall {
          animation: confetti-fall linear forwards;
        }
      `}</style>
    </div>
  );
}

// ── Stepper ──────────────────────────────────────────────────────────────────

function Stepper({ current, steps }) {
  return (
    <div className="flex items-center justify-center gap-2 sm:gap-3 mb-8">
      {steps.map((label, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={i} className="flex items-center gap-2 sm:gap-3">
            <div className="flex items-center gap-1.5">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-colors
                  ${done ? 'bg-success text-white' : active ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-muted border border-line'}`}
              >
                {done ? <Check className="w-3.5 h-3.5" /> : i + 1}
              </div>
              <span className={`text-xs font-medium hidden sm:inline ${active ? 'text-fg' : 'text-fg-muted'}`}>
                {label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <ChevronRight className="w-4 h-4 text-fg-muted shrink-0" />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Plan card ────────────────────────────────────────────────────────────────

function PlanCard({ id, name, price, period, desc, badge, selected, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative w-full text-left rounded-2xl border-2 p-5 transition-all
        ${selected
          ? 'border-accent bg-accent-soft shadow-lg ring-2 ring-accent/20'
          : 'border-line bg-surface-2 hover:border-line-strong hover:shadow-md'
        }`}
    >
      {badge && (
        <span className="absolute -top-2.5 right-4 px-2.5 py-0.5 rounded-full bg-success text-white text-[10px] font-bold uppercase">
          {badge}
        </span>
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1 min-w-0">
          <p className="font-bold text-fg text-base">{name}</p>
          <p className="text-fg-muted text-xs">{desc}</p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-2xl font-extrabold text-fg">{price}</p>
          <p className="text-fg-muted text-xs">{period}</p>
        </div>
      </div>
      <div className={`absolute top-5 left-5 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors
        ${selected ? 'border-accent bg-accent' : 'border-line'}`}
      >
        {selected && <Check className="w-3 h-3 text-white" />}
      </div>
    </button>
  );
}

// ── Main Checkout Page ───────────────────────────────────────────────────────

export default function CheckoutPage() {
  const { t } = useTranslation('checkout');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const lang = useLang();
  const pollRef = useRef(null);

  // Auth state
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);

  // Steps: 0=plan, 1=method, 2=payment
  const [step, setStep] = useState(0);

  // Selections — default to international payment ('paypal') first
  const [selectedPlan, setSelectedPlan] = useState('month'); // 'month' | 'year'
  const [selectedMethod, setSelectedMethod] = useState('paypal'); // 'paypal' | 'vietqr'

  // Order state
  const [order, setOrder] = useState(null);
  const [vietqrData, setVietqrData] = useState(null);
  const [paypalData, setPaypalData] = useState(null);

  // UI state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [orderPaid, setOrderPaid] = useState(false);
  const [orderExpired, setOrderExpired] = useState(false);
  const [showConfetti, setShowConfetti] = useState(false);
  const [cancelLoading, setCancelLoading] = useState(false);

  // PayPal capture state
  const [capturingPaypal, setCapturingPaypal] = useState(false);

  // Plans from API
  const [plans, setPlans] = useState(null);
  const [plansLoading, setPlansLoading] = useState(true);

  // ── Check auth ──────────────────────────────────────────────────────
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setIsAuthenticated(!!session?.access_token);
      setAuthChecked(true);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setIsAuthenticated(!!session?.access_token);
    });

    return () => subscription.unsubscribe();
  }, []);

  // ── Load plans ──────────────────────────────────────────────────────
  useEffect(() => {
    setPlansLoading(true);
    apiFetch('/checkout/plans')
      .then((data) => setPlans(data))
      .catch(() => setError(t('error.loadPlans')))
      .finally(() => setPlansLoading(false));
  }, [t]);

  // ── PayPal capture on return ────────────────────────────────────────
  useEffect(() => {
    const paypalCapture = searchParams.get('paypal_capture');
    const orderId = searchParams.get('order_id') || searchParams.get('order');
    if (paypalCapture !== '1' || !orderId) return;

    setCapturingPaypal(true);
    setStep(2);

    apiFetch(`/checkout/orders/${orderId}/capture`, { method: 'POST' })
      .then((data) => {
        setOrder(data.order || data);
        if (data.success || data.order?.status === 'paid' || data.status === 'paid') {
          handlePaymentSuccess();
        }
        // Clean URL params
        const url = new URL(window.location.href);
        url.searchParams.delete('paypal_capture');
        url.searchParams.delete('order_id');
        url.searchParams.delete('order');
        url.searchParams.delete('token');
        url.searchParams.delete('PayerID');
        window.history.replaceState({}, '', url.pathname);
      })
      .catch((e) => setError(e.message || t('error.capturePaypal')))
      .finally(() => setCapturingPaypal(false));
  }, [searchParams, t]);

  // ── Create order ────────────────────────────────────────────────────
  const createOrder = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await apiFetch('/checkout/orders', {
        method: 'POST',
        body: {
          plan: selectedPlan === 'year' ? 'pro_year' : 'pro_month',
          rail: selectedMethod,
          return_url: `${window.location.origin}${lang === 'en' ? '/en' : ''}/checkout?paypal_capture=1`,
        },
      });

      setOrder(data.order);

      if (selectedMethod === 'vietqr' && data.vietqr) {
        setVietqrData(data.vietqr);
        setStep(2);
      } else if (selectedMethod === 'paypal' && data.paypal) {
        setPaypalData(data.paypal);
        // Redirect to PayPal
        const redirectUrl = data.paypal.approve_url || data.paypal.checkout_url;
        if (redirectUrl) {
          window.location.href = redirectUrl;
          return;
        }
        setStep(2);
      } else {
        setStep(2);
      }
    } catch (e) {
      setError(e.message || t('error.createOrder'));
    } finally {
      setLoading(false);
    }
  }, [selectedPlan, selectedMethod, lang, t]);

  // ── Poll order status (VietQR) ──────────────────────────────────────
  useEffect(() => {
    if (!order?.id || orderPaid || orderExpired || selectedMethod !== 'vietqr') {
      return;
    }

    const poll = async () => {
      try {
        const data = await apiFetch(`/checkout/orders/${order.id}`);
        if (data.status === 'paid') {
          setOrder(data);
          handlePaymentSuccess();
        } else if (data.status === 'expired' || data.status === 'canceled') {
          setOrder(data);
          if (data.status === 'expired') setOrderExpired(true);
        }
      } catch { /* ignore poll errors */ }
    };

    pollRef.current = setInterval(poll, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [order?.id, orderPaid, orderExpired, selectedMethod]);

  // ── Handle payment success ──────────────────────────────────────────
  const handlePaymentSuccess = useCallback(() => {
    setOrderPaid(true);
    setShowConfetti(true);
    if (pollRef.current) clearInterval(pollRef.current);
    // Hide confetti after 5s
    setTimeout(() => setShowConfetti(false), 5000);
  }, []);

  // ── Cancel order ────────────────────────────────────────────────────
  const cancelOrder = useCallback(async () => {
    if (!order?.id) return;
    if (!window.confirm(t('vietqr.cancelConfirm'))) return;

    setCancelLoading(true);
    try {
      await apiFetch(`/checkout/orders/${order.id}/cancel`, { method: 'POST' });
      setOrder(null);
      setVietqrData(null);
      setPaypalData(null);
      setStep(0);
      setOrderExpired(false);
      setError('');
    } catch (e) {
      setError(e.message || t('error.cancelOrder'));
    } finally {
      setCancelLoading(false);
    }
  }, [order?.id, t]);

  // ── Handle expired ──────────────────────────────────────────────────
  const handleExpired = useCallback(() => {
    setOrderExpired(true);
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  // ── Reset to new order ──────────────────────────────────────────────
  const resetOrder = useCallback(() => {
    setOrder(null);
    setVietqrData(null);
    setPaypalData(null);
    setOrderPaid(false);
    setOrderExpired(false);
    setStep(0);
    setError('');
  }, []);

  // ── Not authenticated ──────────────────────────────────────────────
  if (authChecked && !isAuthenticated) {
    return (
      <div className="min-h-screen bg-canvas text-fg flex items-center justify-center px-4">
        <div className="max-w-sm w-full text-center space-y-4">
          <Crown className="w-12 h-12 text-accent mx-auto" />
          <h1 className="text-xl font-bold">{t('title')}</h1>
          <p className="text-fg-muted text-sm">{t('loginRequired')}</p>
          <button
            onClick={() => window.dispatchEvent(new Event('vidgrab:open-auth'))}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-accent text-accent-fg
                       text-sm font-semibold hover:opacity-90 transition-colors"
          >
            {t('login')}
          </button>
        </div>
      </div>
    );
  }

  // ── Loading ─────────────────────────────────────────────────────────
  if (!authChecked || plansLoading) {
    return (
      <div className="min-h-screen bg-canvas text-fg flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-fg-muted" />
      </div>
    );
  }

  // ── Steps ───────────────────────────────────────────────────────────
  const stepLabels = [t('step.plan'), t('step.method'), t('step.payment')];

  return (
    <div className="min-h-screen bg-canvas text-fg">
      {showConfetti && <Confetti />}

      <div className="max-w-xl mx-auto px-4 py-8 sm:py-12">
        {/* Header */}
        <div className="text-center mb-6">
          <div className="inline-flex items-center gap-2 mb-3">
            <Crown className="w-6 h-6 text-accent" />
            <h1 className="text-2xl font-extrabold tracking-tight">{t('title')}</h1>
          </div>
          <p className="text-fg-muted text-sm">{t('subtitle')}</p>
        </div>

        {/* Stepper */}
        {!orderPaid && <Stepper current={step} steps={stepLabels} />}

        {/* Error */}
        {error && (
          <div className="mb-6 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError('')} className="p-0.5 hover:bg-danger/10 rounded">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* ── Step 0: Choose Plan ──────────────────────────────────── */}
        {step === 0 && !orderPaid && (
          <div className="space-y-4">
            <h2 className="text-lg font-bold text-fg">{t('plan.title')}</h2>

            <div className="space-y-3">
              {(() => {
                const catalog = Array.isArray(plans?.plans) ? plans.plans : [];
                const mPlan = catalog.find((p) => p.id === 'pro_month');
                const yPlan = catalog.find((p) => p.id === 'pro_year');
                const mUsd = mPlan?.usd ? `$${mPlan.usd}` : t('plan.month.price');
                const yUsd = yPlan?.usd ? `$${yPlan.usd}` : t('plan.year.price');
                const mVnd = mPlan?.vnd ? formatVnd(mPlan.vnd) : '';
                const yVnd = yPlan?.vnd ? formatVnd(yPlan.vnd) : '';
                return (
                  <>
                    <PlanCard
                      id="month"
                      name={t('plan.month.name')}
                      price={mUsd}
                      period={`${t('plan.month.period')}${mVnd ? ` (${mVnd})` : ''}`}
                      desc={t('plan.month.desc')}
                      selected={selectedPlan === 'month'}
                      onClick={() => setSelectedPlan('month')}
                    />
                    <PlanCard
                      id="year"
                      name={t('plan.year.name')}
                      price={yUsd}
                      period={`${t('plan.year.period')}${yVnd ? ` (${yVnd})` : ''}`}
                      desc={t('plan.year.desc')}
                      badge={t('plan.year.badge')}
                      selected={selectedPlan === 'year'}
                      onClick={() => setSelectedPlan('year')}
                    />
                  </>
                );
              })()}
            </div>

            {/* Features list */}
            <div className="rounded-xl border border-line bg-surface-2 p-4 mt-4">
              <p className="text-sm font-semibold text-fg mb-3 flex items-center gap-2">
                <Zap className="w-4 h-4 text-accent" />
                {t('plan.features.title')}
              </p>
              <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {(t('plan.features.items', { returnObjects: true }) || []).map((feature, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm text-fg-2">
                    <Check className="w-3.5 h-3.5 text-success shrink-0" />
                    {feature}
                  </li>
                ))}
              </ul>
            </div>

            <button
              onClick={() => setStep(1)}
              className="w-full mt-4 px-6 py-3 rounded-xl bg-accent text-accent-fg text-sm font-semibold
                         hover:opacity-90 transition-colors flex items-center justify-center gap-2"
            >
              {t('plan.continue')}
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* ── Step 1: Choose Method ───────────────────────────────── */}
        {step === 1 && !orderPaid && (
          <div className="space-y-4">
            <button
              onClick={() => setStep(0)}
              className="inline-flex items-center gap-1.5 text-sm text-fg-muted hover:text-fg transition-colors mb-2"
            >
              <ArrowLeft className="w-4 h-4" />
              {t('back')}
            </button>

            <h2 className="text-lg font-bold text-fg">{t('method.title')}</h2>

            <div className="space-y-3">
              {/* PayPal / International Card option — prioritized first */}
              <button
                type="button"
                onClick={() => setSelectedMethod('paypal')}
                className={`relative w-full text-left rounded-2xl border-2 p-5 transition-all
                  ${selectedMethod === 'paypal'
                    ? 'border-accent bg-accent-soft'
                    : 'border-line bg-surface-2 hover:border-line-strong'
                  }`}
              >
                <div className="flex items-start gap-4">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0
                    ${selectedMethod === 'paypal' ? 'bg-accent text-accent-fg' : 'bg-surface text-fg-2 border border-line'}`}>
                    <CreditCard className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-bold text-fg">{t('method.paypal.name')}</p>
                      <span className="px-2 py-0.5 rounded-full bg-accent text-accent-fg text-[10px] font-bold">
                        {t('method.paypal.tag')}
                      </span>
                    </div>
                    <p className="text-fg-muted text-xs mt-0.5">{t('method.paypal.desc')}</p>
                  </div>
                  <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 mt-0.5
                    ${selectedMethod === 'paypal' ? 'border-accent bg-accent' : 'border-line'}`}>
                    {selectedMethod === 'paypal' && <Check className="w-3 h-3 text-white" />}
                  </div>
                </div>
              </button>

              {/* VietQR option — secondary for Vietnam users */}
              <button
                type="button"
                onClick={() => setSelectedMethod('vietqr')}
                className={`relative w-full text-left rounded-2xl border-2 p-5 transition-all
                  ${selectedMethod === 'vietqr'
                    ? 'border-accent bg-accent-soft'
                    : 'border-line bg-surface-2 hover:border-line-strong'
                  }`}
              >
                <div className="flex items-start gap-4">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0
                    ${selectedMethod === 'vietqr' ? 'bg-accent text-accent-fg' : 'bg-surface text-fg-2 border border-line'}`}>
                    <QrCode className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-bold text-fg">{t('method.vietqr.name')}</p>
                      <span className="px-2 py-0.5 rounded-full bg-success/15 text-success text-[10px] font-bold">
                        {t('method.vietqr.tag')}
                      </span>
                    </div>
                    <p className="text-fg-muted text-xs mt-0.5">{t('method.vietqr.desc')}</p>
                  </div>
                  <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 mt-0.5
                    ${selectedMethod === 'vietqr' ? 'border-accent bg-accent' : 'border-line'}`}>
                    {selectedMethod === 'vietqr' && <Check className="w-3 h-3 text-white" />}
                  </div>
                </div>
              </button>
            </div>

            <button
              onClick={createOrder}
              disabled={!selectedMethod || loading}
              className="w-full mt-4 px-6 py-3 rounded-xl bg-accent text-accent-fg text-sm font-semibold
                         hover:opacity-90 transition-colors flex items-center justify-center gap-2
                         disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>{selectedMethod === 'paypal' ? t('paypal.redirecting') : t('vietqr.waiting')}</span>
                </>
              ) : (
                <>
                  <Shield className="w-4 h-4" />
                  {t('method.continue')}
                </>
              )}
            </button>
          </div>
        )}

        {/* ── Step 2: VietQR Payment ──────────────────────────────── */}
        {step === 2 && selectedMethod === 'vietqr' && !orderPaid && !capturingPaypal && (
          <div className="space-y-5">
            <button
              onClick={() => { if (!orderExpired) return; resetOrder(); }}
              className={`inline-flex items-center gap-1.5 text-sm transition-colors mb-2
                ${orderExpired ? 'text-fg-muted hover:text-fg cursor-pointer' : 'text-fg-muted/50 cursor-default'}`}
            >
              <ArrowLeft className="w-4 h-4" />
              {t('back')}
            </button>

            <h2 className="text-lg font-bold text-fg">{t('vietqr.title')}</h2>
            <p className="text-fg-muted text-sm">{t('vietqr.subtitle')}</p>

            {/* Expired state */}
            {orderExpired && (
              <div className="rounded-xl border border-danger/30 bg-danger-soft p-5 text-center space-y-3">
                <AlertTriangle className="w-8 h-8 text-danger mx-auto" />
                <p className="font-bold text-danger">{t('vietqr.expired')}</p>
                <p className="text-sm text-danger/80">{t('vietqr.expiredDesc')}</p>
                <button
                  onClick={resetOrder}
                  className="px-5 py-2.5 rounded-xl bg-accent text-accent-fg text-sm font-semibold hover:opacity-90 transition-colors"
                >
                  {t('vietqr.newOrder')}
                </button>
              </div>
            )}

            {!orderExpired && (
              <>
                {/* QR Code */}
                {vietqrData?.qr_url && (
                  <div className="rounded-2xl border border-line bg-white p-4 flex flex-col items-center gap-3">
                    <p className="text-sm font-medium text-gray-700">{t('vietqr.scanQr')}</p>
                    <img
                      src={vietqrData.qr_url}
                      alt="VietQR Payment"
                      className="w-56 h-56 sm:w-64 sm:h-64 object-contain rounded-xl"
                    />
                  </div>
                )}

                {/* Manual transfer info */}
                <div className="rounded-xl border border-line bg-surface-2 divide-y divide-line">
                  <p className="px-4 py-3 text-sm font-semibold text-fg">{t('vietqr.orManual')}</p>

                  {/* Bank */}
                  <div className="px-4 py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs text-fg-muted">{t('vietqr.bank')}</p>
                      <p className="text-sm font-semibold text-fg">{vietqrData?.bank_name || '—'}</p>
                    </div>
                  </div>

                  {/* Account No */}
                  <div className="px-4 py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs text-fg-muted">{t('vietqr.accountNo')}</p>
                      <p className="text-sm font-semibold text-fg font-mono">{vietqrData?.account_no || '—'}</p>
                    </div>
                    {vietqrData?.account_no && <CopyButton text={vietqrData.account_no} />}
                  </div>

                  {/* Account Holder */}
                  <div className="px-4 py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs text-fg-muted">{t('vietqr.accountHolder')}</p>
                      <p className="text-sm font-semibold text-fg">{vietqrData?.account_holder || '—'}</p>
                    </div>
                  </div>

                  {/* Amount */}
                  <div className="px-4 py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs text-fg-muted">{t('vietqr.amount')}</p>
                      <p className="text-lg font-extrabold text-accent">
                        {order?.amount ? formatVnd(order.amount) : '—'}
                      </p>
                    </div>
                  </div>

                  {/* Transfer Content (VGP code) */}
                  <div className="px-4 py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs text-fg-muted">{t('vietqr.content')}</p>
                      <p className="text-sm font-bold text-accent font-mono">{order?.vgp_code || '—'}</p>
                    </div>
                    {order?.vgp_code && <CopyButton text={order.vgp_code} />}
                  </div>

                  <div className="px-4 py-3">
                    <p className="text-xs text-warning font-medium">{t('vietqr.contentNote')}</p>
                  </div>
                </div>

                {/* Countdown */}
                {order?.expires_at && (
                  <Countdown expiresAt={order.expires_at} onExpired={handleExpired} />
                )}

                {/* Waiting indicator */}
                <div className="rounded-xl border border-accent/20 bg-accent-soft p-4 flex items-start gap-3">
                  <div className="mt-0.5">
                    <RefreshCw className="w-4 h-4 text-accent animate-spin" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-fg">{t('vietqr.waiting')}</p>
                    <p className="text-xs text-fg-muted mt-0.5">{t('vietqr.waitingDesc')}</p>
                  </div>
                </div>

                {/* Cancel button */}
                <button
                  onClick={cancelOrder}
                  disabled={cancelLoading}
                  className="w-full px-4 py-2.5 rounded-xl border border-line text-fg-muted text-sm font-medium
                             hover:border-danger/30 hover:text-danger hover:bg-danger-soft transition-colors
                             disabled:opacity-50"
                >
                  {cancelLoading ? t('vietqr.canceling') : t('vietqr.cancel')}
                </button>
              </>
            )}
          </div>
        )}

        {/* ── Step 2: PayPal redirecting / capturing ──────────────── */}
        {step === 2 && (selectedMethod === 'paypal' || capturingPaypal) && !orderPaid && (
          <div className="text-center py-16 space-y-4">
            <Loader2 className="w-10 h-10 animate-spin text-accent mx-auto" />
            <h2 className="text-lg font-bold text-fg">
              {capturingPaypal ? t('paypal.capturing') : t('paypal.redirecting')}
            </h2>
            <p className="text-fg-muted text-sm">
              {capturingPaypal ? t('paypal.captureDesc') : t('paypal.redirectDesc')}
            </p>
          </div>
        )}

        {/* ── Success screen ──────────────────────────────────────── */}
        {orderPaid && (
          <div className="text-center py-12 space-y-6">
            <div className="w-20 h-20 rounded-full bg-success/15 flex items-center justify-center mx-auto">
              <PartyPopper className="w-10 h-10 text-success" />
            </div>
            <div className="space-y-2">
              <h2 className="text-2xl font-extrabold text-fg">{t('success.title')}</h2>
              <p className="text-fg-muted">{t('success.desc')}</p>
            </div>
            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              <button
                onClick={() => navigate(lang === 'en' ? '/en/billing' : '/billing')}
                className="px-6 py-3 rounded-xl bg-accent text-accent-fg text-sm font-semibold
                           hover:opacity-90 transition-colors inline-flex items-center justify-center gap-2"
              >
                <Crown className="w-4 h-4" />
                {t('success.goToBilling')}
              </button>
              <button
                onClick={() => navigate('/')}
                className="px-6 py-3 rounded-xl border border-line bg-surface-2 text-fg text-sm font-medium
                           hover:bg-surface transition-colors"
              >
                {t('success.continue')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

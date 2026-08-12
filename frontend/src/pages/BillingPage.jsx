import { useState, useEffect, useCallback } from 'react';
import {
  Crown, CreditCard, AlertTriangle, CheckCircle2, Info,
  ExternalLink, Zap, Download, Brain, RefreshCw, X,
} from 'lucide-react';
import { supabase } from '../lib/supabaseClient';
import QuotaBar from '../components/QuotaBar';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

// ── Helpers ──────────────────────────────────────────────────────────────────

function navigate(path) {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function formatDate(iso) {
  if (!iso) return null;
  try {
    return new Intl.DateTimeFormat('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(iso));
  } catch {
    return iso;
  }
}

// ── Plan badge ────────────────────────────────────────────────────────────────

const PLAN_BADGE = {
  free:       { label: 'Free',       color: 'bg-zinc-700 text-zinc-300' },
  pro:        { label: 'Pro',        color: 'bg-emerald-600/30 text-emerald-300 border border-emerald-600/50' },
  team:       { label: 'Team',       color: 'bg-indigo-600/30 text-indigo-300 border border-indigo-600/50' },
  enterprise: { label: 'Enterprise', color: 'bg-purple-600/30 text-purple-300 border border-purple-600/50' },
};

function PlanBadge({ tier }) {
  const cfg = PLAN_BADGE[tier] || PLAN_BADGE.free;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-semibold ${cfg.color}`}
    >
      <Crown className="w-3.5 h-3.5" aria-hidden="true" />
      {cfg.label}
    </span>
  );
}

// ── Billing status pill ───────────────────────────────────────────────────────

function BillingStatusPill({ status, expiryDate }) {
  const configs = {
    active:    { label: 'Đang hoạt động',                                  color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
    past_due:  { label: 'Quá hạn thanh toán — 3 ngày ân hạn',             color: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30'  },
    canceling: { label: `Đã huỷ — còn hiệu lực đến ${formatDate(expiryDate) || '...'}`, color: 'bg-orange-500/15 text-orange-400 border-orange-500/30' },
    canceled:  { label: 'Đã huỷ',                                          color: 'bg-red-500/15 text-red-400 border-red-500/30'           },
    none:      { label: 'Miễn phí',                                        color: 'bg-zinc-700/50 text-zinc-400 border-zinc-600/40'        },
  };

  const cfg = configs[status] || configs.none;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${cfg.color}`}
    >
      {cfg.label}
    </span>
  );
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function Toast({ message, type = 'info', onDismiss }) {
  const styles = {
    success: 'bg-emerald-600/20 border-emerald-500/40 text-emerald-300',
    info:    'bg-blue-600/20 border-blue-500/40 text-blue-300',
    error:   'bg-red-600/20 border-red-500/40 text-red-300',
  };

  return (
    <div
      role="alert"
      className={`fixed top-5 right-5 z-50 flex items-center gap-3 px-4 py-3 rounded-xl
                  border backdrop-blur-sm max-w-sm shadow-lg text-sm font-medium
                  animate-in fade-in slide-in-from-top-2 ${styles[type] || styles.info}`}
    >
      {type === 'success' && <CheckCircle2 className="w-4 h-4 shrink-0" />}
      {type === 'info'    && <Info          className="w-4 h-4 shrink-0" />}
      {type === 'error'   && <AlertTriangle className="w-4 h-4 shrink-0" />}
      <span className="flex-1">{message}</span>
      <button
        onClick={onDismiss}
        aria-label="Đóng thông báo"
        className="p-0.5 rounded hover:bg-white/10 transition-colors"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ── Section card wrapper ──────────────────────────────────────────────────────

function Card({ children, className = '' }) {
  return (
    <div
      className={`bg-[#0d2e29] border border-white/10 rounded-2xl p-6 ${className}`}
    >
      {children}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BillingPage() {
  const [billingInfo, setBillingInfo]   = useState(null);
  const [summary, setSummary]           = useState(null);
  const [loadingInfo, setLoadingInfo]   = useState(true);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [portalLoading, setPortalLoading] = useState(false);
  const [portalError, setPortalError]   = useState('');
  const [toast, setToast]               = useState(null);

  // ── Query-param toasts ───────────────────────────────────────────────
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('success') === '1') {
      setToast({ message: 'Nâng cấp thành công! Chào mừng bạn đến với gói mới.', type: 'success' });
      // Remove param from URL without reload
      params.delete('success');
      const newSearch = params.toString();
      window.history.replaceState({}, '', newSearch ? `?${newSearch}` : window.location.pathname);
    } else if (params.get('canceled') === '1') {
      setToast({ message: 'Đã huỷ thanh toán.', type: 'info' });
      params.delete('canceled');
      const newSearch = params.toString();
      window.history.replaceState({}, '', newSearch ? `?${newSearch}` : window.location.pathname);
    }
  }, []);

  // ── Fetch helpers ────────────────────────────────────────────────────
  const getToken = useCallback(async () => {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || null;
  }, []);

  const fetchBillingInfo = useCallback(async () => {
    setLoadingInfo(true);
    try {
      const token = await getToken();
      if (!token) { setBillingInfo(null); return; }
      const res = await fetch(`${API}/payments/billing-status`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setBillingInfo(await res.json());
    } catch {
      setBillingInfo(null);
    } finally {
      setLoadingInfo(false);
    }
  }, [getToken]);

  const fetchSummary = useCallback(async () => {
    setLoadingSummary(true);
    try {
      const token = await getToken();
      if (!token) { setSummary(null); return; }
      const res = await fetch(`${API}/billing/summary`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSummary(await res.json());
    } catch {
      setSummary(null);
    } finally {
      setLoadingSummary(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchBillingInfo();
    fetchSummary();
  }, [fetchBillingInfo, fetchSummary]);

  // ── Stripe Customer Portal ───────────────────────────────────────────
  const openPortal = useCallback(async () => {
    setPortalLoading(true);
    setPortalError('');
    try {
      const token = await getToken();
      if (!token) { setPortalError('Bạn cần đăng nhập để quản lý thanh toán.'); return; }
      const res = await fetch(`${API}/payments/create-portal-session`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ return_url: window.location.href }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail?.user_message || err?.detail || `HTTP ${res.status}`);
      }
      const { url } = await res.json();
      if (url) window.location.href = url;
    } catch (e) {
      setPortalError(e.message || 'Không thể mở trang quản lý thanh toán.');
    } finally {
      setPortalLoading(false);
    }
  }, [getToken]);

  // ── Derived values ───────────────────────────────────────────────────
  const tier          = billingInfo?.tier          || 'free';
  const billingStatus = billingInfo?.billing_status || 'none';
  const expiryDate    = billingInfo?.subscription_expiry || null;
  const canManage     = billingInfo?.can_manage_billing ?? false;
  const isFree        = tier === 'free';

  const usage         = summary?.usage   || {};
  const limits        = summary?.limits  || {};
  const credits       = summary?.remaining?.credits ?? null;

  const isLoading = loadingInfo || loadingSummary;

  // ── Render ───────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#071a16] text-white">
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onDismiss={() => setToast(null)}
        />
      )}

      <div className="max-w-2xl mx-auto px-4 py-10 space-y-6">

        {/* ── Header ─────────────────────────────────────────────── */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Thanh toán & Gói dịch vụ</h1>
            <p className="text-zinc-400 text-sm mt-1">
              Quản lý gói cước và lịch sử thanh toán của bạn
            </p>
          </div>
          <button
            onClick={() => { fetchBillingInfo(); fetchSummary(); }}
            aria-label="Tải lại"
            className="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* ── Plan + status card ──────────────────────────────────── */}
        <Card>
          {loadingInfo ? (
            <div className="space-y-3 animate-pulse">
              <div className="h-6 w-24 rounded-full bg-white/10" />
              <div className="h-4 w-48 rounded bg-white/10" />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <PlanBadge tier={tier} />
                <BillingStatusPill status={billingStatus} expiryDate={expiryDate} />
              </div>

              {expiryDate && billingStatus === 'active' && (
                <p className="text-zinc-400 text-sm">
                  Gia hạn tiếp theo:{' '}
                  <span className="text-white font-medium">{formatDate(expiryDate)}</span>
                </p>
              )}

              {/* CTA buttons */}
              <div className="flex flex-wrap gap-3 pt-1">
                {canManage && (
                  <button
                    onClick={openPortal}
                    disabled={portalLoading}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg
                               bg-white/10 hover:bg-white/15 border border-white/10
                               text-sm font-medium text-white transition-colors
                               disabled:opacity-50 disabled:cursor-not-allowed
                               focus:outline-none focus:ring-2 focus:ring-white/30"
                  >
                    <CreditCard className="w-4 h-4" aria-hidden="true" />
                    {portalLoading ? 'Đang chuyển hướng...' : 'Quản lý thanh toán'}
                    {!portalLoading && <ExternalLink className="w-3.5 h-3.5 opacity-60" aria-hidden="true" />}
                  </button>
                )}

                {isFree && (
                  <button
                    onClick={() => navigate('/pricing')}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg
                               bg-emerald-600 hover:bg-emerald-500 text-sm font-semibold
                               text-white transition-colors focus:outline-none
                               focus:ring-2 focus:ring-emerald-400 focus:ring-offset-2
                               focus:ring-offset-[#0d2e29]"
                  >
                    <Crown className="w-4 h-4" aria-hidden="true" />
                    Nâng cấp
                    <Zap className="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                )}
              </div>

              {portalError && (
                <p className="text-red-400 text-xs flex items-center gap-1.5 mt-1">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  {portalError}
                </p>
              )}
            </div>
          )}
        </Card>

        {/* ── Usage quotas ────────────────────────────────────────── */}
        <Card className="space-y-5">
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <Download className="w-4 h-4 text-emerald-400" aria-hidden="true" />
            Sử dụng hôm nay
          </h2>

          {loadingSummary ? (
            <div className="space-y-4 animate-pulse">
              {[1, 2, 3].map((n) => (
                <div key={n} className="space-y-2">
                  <div className="h-3 w-32 rounded bg-white/10" />
                  <div className="h-2 w-full rounded-full bg-white/10" />
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-5">
              <QuotaBar
                used={usage.downloads_today ?? 0}
                limit={limits.downloads_per_day ?? -1}
                label="Downloads hôm nay"
                unit="lượt"
              />
              <QuotaBar
                used={usage.ai_analyses_today ?? 0}
                limit={limits.ai_analyses_per_day ?? -1}
                label="Phân tích AI hôm nay"
                unit="lượt"
              />
              {limits.storage_mb !== undefined && (
                <QuotaBar
                  used={Math.round((usage.storage_used_mb ?? 0))}
                  limit={limits.storage_mb ?? -1}
                  label="Dung lượng cloud"
                  unit="MB"
                />
              )}
            </div>
          )}
        </Card>

        {/* ── Credit balance ───────────────────────────────────────── */}
        {credits !== null && credits > 0 && (
          <Card>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Brain className="w-4 h-4 text-purple-400" aria-hidden="true" />
                <span className="text-sm font-medium text-white">Credits còn lại</span>
              </div>
              <span className="text-emerald-400 font-bold text-lg">{credits}</span>
            </div>
            <p className="text-zinc-500 text-xs mt-1">
              Dùng để phân tích AI, xoá logo và các tính năng nâng cao.
            </p>
          </Card>
        )}

        {/* ── Payment history link ──────────────────────────────────── */}
        {canManage && (
          <Card>
            <h2 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
              <CreditCard className="w-4 h-4 text-zinc-400" aria-hidden="true" />
              Lịch sử thanh toán
            </h2>
            <p className="text-zinc-400 text-sm mb-4">
              Xem hoá đơn và lịch sử giao dịch trên trang quản lý của Stripe.
            </p>
            <button
              onClick={openPortal}
              disabled={portalLoading}
              className="inline-flex items-center gap-2 text-sm text-emerald-400
                         hover:text-emerald-300 transition-colors font-medium
                         focus:outline-none focus:underline disabled:opacity-50"
            >
              Xem lịch sử thanh toán
              <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </Card>
        )}

        {/* ── Upsell for free users ─────────────────────────────────── */}
        {isFree && !loadingInfo && (
          <div
            className="rounded-2xl border border-emerald-600/30 bg-emerald-600/5 p-6
                       flex flex-col sm:flex-row items-start sm:items-center gap-4"
          >
            <div className="flex-1 space-y-1">
              <p className="text-white font-semibold">
                Nâng cấp để mở khoá toàn bộ tính năng
              </p>
              <p className="text-zinc-400 text-sm">
                ZIP download, YouTube video 4K, phân tích AI, lưu cloud và nhiều hơn.
              </p>
            </div>
            <button
              onClick={() => navigate('/pricing')}
              className="shrink-0 inline-flex items-center gap-2 px-5 py-2.5 rounded-xl
                         bg-emerald-600 hover:bg-emerald-500 text-white text-sm
                         font-semibold transition-colors focus:outline-none
                         focus:ring-2 focus:ring-emerald-400 focus:ring-offset-2
                         focus:ring-offset-[#071a16]"
            >
              <Crown className="w-4 h-4" aria-hidden="true" />
              Xem các gói
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

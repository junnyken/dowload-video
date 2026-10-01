import { useState, useEffect, useCallback } from 'react';
import {
  Crown, CreditCard, AlertTriangle, CheckCircle2, Info,
  ExternalLink, Zap, Download, Brain, RefreshCw, X,
} from 'lucide-react';
import { supabase } from '../lib/supabaseClient';
import QuotaBar from '../components/QuotaBar';
import UpgradeModal from '../components/UpgradeModal';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

// ── Helpers ──────────────────────────────────────────────────────────────────

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
  free:       { label: 'Free',       color: 'bg-surface-2 text-fg-2' },
  pro:        { label: 'Pro',        color: 'bg-success-soft text-success border border-success/50' },
  team:       { label: 'Team',       color: 'bg-surface-2 text-fg-2 border border-line' },
  enterprise: { label: 'Enterprise', color: 'bg-surface-2 text-fg-2 border border-line' },
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
    active:    { label: 'Đang hoạt động',                                  color: 'bg-success-soft text-success border-success/30' },
    past_due:  { label: 'Quá hạn thanh toán — 3 ngày ân hạn',             color: 'bg-accent-soft text-accent-text border-accent/30'  },
    canceling: { label: `Đã huỷ — còn hiệu lực đến ${formatDate(expiryDate) || '...'}`, color: 'bg-warning-soft text-warning border-warning/30' },
    canceled:  { label: 'Đã huỷ',                                          color: 'bg-danger-soft text-danger border-danger/30'           },
    none:      { label: 'Miễn phí',                                        color: 'bg-surface-2 text-fg-muted border-line-strong'        },
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
    success: 'bg-success-soft border-success/40 text-success',
    info:    'bg-surface-2 border-line text-fg-2',
    error:   'bg-danger-soft border-danger/40 text-danger',
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
        className="p-0.5 rounded hover:bg-surface-2 transition-colors"
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
      className={`bg-surface-2 border border-line rounded-2xl p-6 ${className}`}
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
  // These two buttons used to navigate('/pricing'), a page App.jsx hides and
  // renders as nothing. Pointing them at /billing would be this page linking
  // to itself, so they open the upgrade modal instead — the path that already
  // reaches Stripe checkout and already fires upgrade_clicked.
  const [showUpgrade, setShowUpgrade]   = useState(false);
  const [authToken, setAuthToken]       = useState(null);

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

  // Token is read on click rather than kept in state from the load effect:
  // setting it there is a setState inside an effect, which the hooks lint
  // rejects, and a token grabbed at click time is the fresher one anyway.
  const openUpgrade = useCallback(async () => {
    setAuthToken(await getToken());
    setShowUpgrade(true);
  }, [getToken]);

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
    <div className="min-h-screen bg-canvas text-fg">
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
            <h1 className="text-2xl font-bold text-fg">Thanh toán & Gói dịch vụ</h1>
            <p className="text-fg-muted text-sm mt-1">
              Quản lý gói cước và lịch sử thanh toán của bạn
            </p>
          </div>
          <button
            onClick={() => { fetchBillingInfo(); fetchSummary(); }}
            aria-label="Tải lại"
            className="p-2 rounded-lg text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* ── Plan + status card ──────────────────────────────────── */}
        <Card>
          {loadingInfo ? (
            <div className="space-y-3 animate-pulse">
              <div className="h-6 w-24 rounded-full bg-surface-2" />
              <div className="h-4 w-48 rounded bg-surface-2" />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <PlanBadge tier={tier} />
                <BillingStatusPill status={billingStatus} expiryDate={expiryDate} />
              </div>

              {expiryDate && billingStatus === 'active' && (
                <p className="text-fg-muted text-sm">
                  Gia hạn tiếp theo:{' '}
                  <span className="text-fg font-medium">{formatDate(expiryDate)}</span>
                </p>
              )}

              {/* CTA buttons */}
              <div className="flex flex-wrap gap-3 pt-1">
                {canManage && (
                  <button
                    onClick={openPortal}
                    disabled={portalLoading}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg
                               bg-surface-2 hover:bg-line border border-line
                               text-sm font-medium text-fg transition-colors
                               disabled:opacity-50 disabled:cursor-not-allowed
                               focus:outline-none focus:ring-2 focus:ring-line-strong"
                  >
                    <CreditCard className="w-4 h-4" aria-hidden="true" />
                    {portalLoading ? 'Đang chuyển hướng...' : 'Quản lý thanh toán'}
                    {!portalLoading && <ExternalLink className="w-3.5 h-3.5 opacity-60" aria-hidden="true" />}
                  </button>
                )}

                {isFree && (
                  <button
                    onClick={openUpgrade}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg
                               bg-accent hover:opacity-90 text-sm font-semibold
                               text-accent-fg transition-colors focus:outline-none
                               focus:ring-2 focus:ring-accent focus:ring-offset-2
                               focus:ring-offset-surface-2"
                  >
                    <Crown className="w-4 h-4" aria-hidden="true" />
                    Nâng cấp
                    <Zap className="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                )}
              </div>

              {portalError && (
                <p className="text-danger text-xs flex items-center gap-1.5 mt-1">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  {portalError}
                </p>
              )}
            </div>
          )}
        </Card>

        {/* ── Usage quotas ────────────────────────────────────────── */}
        <Card className="space-y-5">
          <h2 className="text-base font-semibold text-fg flex items-center gap-2">
            <Download className="w-4 h-4 text-success" aria-hidden="true" />
            Sử dụng hôm nay
          </h2>

          {loadingSummary ? (
            <div className="space-y-4 animate-pulse">
              {[1, 2, 3].map((n) => (
                <div key={n} className="space-y-2">
                  <div className="h-3 w-32 rounded bg-surface-2" />
                  <div className="h-2 w-full rounded-full bg-surface-2" />
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
                <Brain className="w-4 h-4 text-fg-2" aria-hidden="true" />
                <span className="text-sm font-medium text-fg">Credits còn lại</span>
              </div>
              <span className="text-success font-bold text-lg">{credits}</span>
            </div>
            <p className="text-fg-muted text-xs mt-1">
              Dùng để phân tích AI, xoá logo và các tính năng nâng cao.
            </p>
          </Card>
        )}

        {/* ── Payment history link ──────────────────────────────────── */}
        {canManage && (
          <Card>
            <h2 className="text-base font-semibold text-fg mb-3 flex items-center gap-2">
              <CreditCard className="w-4 h-4 text-fg-muted" aria-hidden="true" />
              Lịch sử thanh toán
            </h2>
            <p className="text-fg-muted text-sm mb-4">
              Xem hoá đơn và lịch sử giao dịch trên trang quản lý của Stripe.
            </p>
            <button
              onClick={openPortal}
              disabled={portalLoading}
              className="inline-flex items-center gap-2 text-sm text-success
                         hover:text-success transition-colors font-medium
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
            className="rounded-2xl border border-accent/30 bg-accent-soft p-6
                       flex flex-col sm:flex-row items-start sm:items-center gap-4"
          >
            <div className="flex-1 space-y-1">
              <p className="text-fg font-semibold">
                Nâng cấp để mở khoá toàn bộ tính năng
              </p>
              <p className="text-fg-muted text-sm">
                ZIP download, YouTube video 4K, phân tích AI, lưu cloud và nhiều hơn.
              </p>
            </div>
            <button
              onClick={openUpgrade}
              className="shrink-0 inline-flex items-center gap-2 px-5 py-2.5 rounded-xl
                         bg-accent hover:opacity-90 text-accent-fg text-sm
                         font-semibold transition-colors focus:outline-none
                         focus:ring-2 focus:ring-accent focus:ring-offset-2
                         focus:ring-offset-canvas"
            >
              <Crown className="w-4 h-4" aria-hidden="true" />
              Xem các gói
            </button>
          </div>
        )}
      </div>

      <UpgradeModal
        isOpen={showUpgrade}
        onClose={() => setShowUpgrade(false)}
        authToken={authToken}
      />
    </div>
  );
}

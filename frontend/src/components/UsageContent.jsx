import { useEffect, useState } from 'react';
import { BarChart2, Zap, Calendar, Archive, Crown, RefreshCw, Key, Copy, Trash2, Lock, Webhook, Shield, Clock, HardDrive, Pin } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';

export default function UsageContent() {
  const { session } = useAuth();
  const [usage, setUsage]   = useState(null);
  const [loading, setLoading] = useState(true);

  // ── Storage state ─────────────────────────────────────
  const [storageInfo, setStorageInfo] = useState(null);

  // ── API Key state ─────────────────────────────────────────
  const [apiKeyStatus, setApiKeyStatus]   = useState(null);  // {has_key, created_at, last_used_at}
  const [newApiKey, setNewApiKey]         = useState(null);   // show-once reveal
  const [apiKeyLoading, setApiKeyLoading] = useState(false);
  const [apiKeyError, setApiKeyError]     = useState('');

  // ── Webhook state ──────────────────────────────────────────
  const [webhookUrl, setWebhookUrl]         = useState('');
  const [webhookSecret, setWebhookSecret]   = useState(null); // show-once
  const [webhookLogs, setWebhookLogs]       = useState([]);
  const [webhookHasReg, setWebhookHasReg]   = useState(false); // true = registered
  const [webhookLoading, setWebhookLoading] = useState(false);
  const [webhookError, setWebhookError]     = useState('');

  const load = async () => {
    if (!session) return;
    setLoading(true);
    const apiBase = API_BASE;
    const hdrs = { Authorization: `Bearer ${session.access_token}` };
    try {
      const [usageRes, storageRes] = await Promise.all([
        fetch(`${apiBase}/api/v1/user/usage`, { headers: hdrs }),
        fetch(`${apiBase}/api/v1/storage/info`, { headers: hdrs }),
      ]);
      if (usageRes.ok) setUsage(await usageRes.json());
      if (storageRes.ok) setStorageInfo(await storageRes.json());
    } catch { /* non-critical */ }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [session]);

  if (loading) return (
    <div className="flex justify-center py-16">
      <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );

  if (!usage) return (
    <div className="text-center py-16 text-fg-muted">Không tải được dữ liệu. Thử lại sau.</div>
  );

  // Daily allowance is per platform (platform_quota); the "Hôm nay" card shows
  // the platform closest to its limit (used/limit), older servers fall back.
  const pq        = usage.platform_quota || null;
  const dayUsed   = pq ? (usage.used ?? 0) : usage.downloads_today;
  const dayLimit  = pq ? pq.limit : usage.limits.daily;
  const dailyPct  = dayLimit > 0 ? Math.min(100, (dayUsed / dayLimit) * 100) : 0;
  const usedPlatforms = pq ? pq.platforms.filter(p => p.used > 0) : [];
  const monthPct  = usage.limits.monthly > 0 ? Math.min(100, (usage.downloads_this_month / usage.limits.monthly) * 100) : 0;
  const isPro     = usage.plan === 'pro';

  // Fetch API key status when pro tier is confirmed
  useEffect(() => {
    if (!isPro || !session) return;
    const apiBase = API_BASE;
    fetch(`${apiBase}/api/v1/user/api-key/status`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setApiKeyStatus(d); })
      .catch(() => {});
  }, [isPro, session]);

  const handleGenerateApiKey = async () => {
    setApiKeyLoading(true);
    setApiKeyError('');
    try {
      const apiBase = API_BASE;
      const res = await fetch(`${apiBase}/api/v1/user/api-key/generate`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setNewApiKey(data.api_key);
      setApiKeyStatus(prev => ({ ...(prev || {}), has_key: true, created_at: data.created_at || new Date().toISOString(), last_used_at: null }));
    } catch (err) {
      setApiKeyError(err.message || 'Không thể tạo API Key. Thử lại sau.');
    } finally {
      setApiKeyLoading(false);
    }
  };

  const handleRevokeApiKey = async () => {
    if (!window.confirm('Thu hồi API Key? Mọi ứng dụng đang dùng key này sẽ bị ngắt kết nối.')) return;
    setApiKeyLoading(true);
    setApiKeyError('');
    try {
      const apiBase = API_BASE;
      const res = await fetch(`${apiBase}/api/v1/user/api-key/revoke`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || `HTTP ${res.status}`); }
      setApiKeyStatus({ has_key: false, created_at: null, last_used_at: null });
      setNewApiKey(null);
    } catch (err) {
      setApiKeyError(err.message || 'Không thể thu hồi API Key. Thử lại sau.');
    } finally {
      setApiKeyLoading(false);
    }
  };

  const handleCopyKey = () => {
    if (!newApiKey) return;
    navigator.clipboard.writeText(newApiKey).then(() => {}).catch(() => {});
  };

  // ── Webhook: fetch logs on mount to detect registration ───────────
  useEffect(() => {
    if (!isPro || !session) return;
    const apiBase = API_BASE;
    fetch(`${apiBase}/api/v1/user/webhook/logs`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
      .then(r => {
        if (r.status === 404) { setWebhookHasReg(false); return null; }
        if (r.ok) { setWebhookHasReg(true); return r.json(); }
        return null;
      })
      .then(d => { if (d) setWebhookLogs(Array.isArray(d) ? d : (d.logs || [])); })
      .catch(() => {});
  }, [isPro, session]);

  const handleRegisterWebhook = async (e) => {
    e.preventDefault();
    if (!webhookUrl.trim()) return;
    setWebhookLoading(true);
    setWebhookError('');
    try {
      const apiBase = API_BASE;
      const res = await fetch(`${apiBase}/api/v1/user/webhook/register`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: webhookUrl.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setWebhookHasReg(true);
      setWebhookSecret(data.secret || null);
      setWebhookUrl('');
    } catch (err) {
      setWebhookError(err.message || 'Không thể đăng ký webhook. Thử lại sau.');
    } finally {
      setWebhookLoading(false);
    }
  };

  const handleRevokeWebhook = async () => {
    if (!window.confirm('Thu hồi webhook? Hệ thống sẽ ngừng gửi sự kiện tới URL này.')) return;
    setWebhookLoading(true);
    setWebhookError('');
    try {
      const apiBase = API_BASE;
      const res = await fetch(`${apiBase}/api/v1/user/webhook/revoke`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || `HTTP ${res.status}`); }
      setWebhookHasReg(false);
      setWebhookSecret(null);
      setWebhookLogs([]);
    } catch (err) {
      setWebhookError(err.message || 'Không thể thu hồi webhook. Thử lại sau.');
    } finally {
      setWebhookLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-surface flex items-center justify-center">
            <BarChart2 className="w-5 h-5 text-accent-text" />
          </div>
          <div>
            <h1 className="text-fg font-bold text-lg">Quota & Usage</h1>
            <p className="text-fg-muted text-xs">Theo dõi lượt tải theo tài khoản</p>
          </div>
        </div>
        <button onClick={load}
          className="p-2 rounded-lg text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Plan badge */}
      <div className={`flex items-center gap-2.5 px-4 py-3 rounded-xl border
        ${isPro
          ? 'bg-accent-soft border-accent/30'
          : 'bg-surface-2 border-line'}`}>
        <Crown className={`w-5 h-5 ${isPro ? 'text-accent-text' : 'text-fg-muted'}`} />
        <div>
          <p className={`text-sm font-bold ${isPro ? 'text-accent-text' : 'text-fg'}`}>
            {isPro ? 'Pro Plan' : 'Free Plan'}
          </p>
          <p className="text-fg-muted text-xs">
            {pq
              ? (pq.unlimited ? 'Không giới hạn lượt tải' : (pq.scope === 'per_platform' ? `Tối đa ${pq.limit} lượt/ngày cho mỗi nền tảng` : `Tối đa ${pq.limit} lượt/ngày cho tất cả nền tảng`))
              : (isPro ? 'Tải không giới hạn' : `Tối đa ${usage.limits.daily}/ngày, ${usage.limits.monthly}/tháng`)}
          </p>
        </div>
        {!isPro && (
          <span className="ml-auto text-[10px] font-bold text-accent-text bg-accent-soft border border-accent/20 px-2 py-0.5 rounded-full">
            Nâng cấp
          </span>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          icon={<Zap className="w-4 h-4" />}
          label={pq && usage.used_platform_label ? `Hôm nay · ${usage.used_platform_label}` : 'Hôm nay'}
          value={dayUsed}
          limit={dayLimit}
          pct={dailyPct}
          isPro={pq ? false : isPro}
        />
        <StatCard
          icon={<Calendar className="w-4 h-4" />}
          label="Tháng này"
          value={usage.downloads_this_month}
          limit={usage.limits.monthly}
          pct={monthPct}
          isPro={isPro}
        />
        {pq && (
          <div className="col-span-2 bg-surface-2 border border-line rounded-xl p-4 space-y-1.5">
            <p className="text-fg-muted text-xs">Lượt đã dùng hôm nay theo nền tảng</p>
            {usedPlatforms.length === 0
              ? <p className="text-fg text-sm">Chưa tải video nào hôm nay.</p>
              : usedPlatforms.map(p => (
                <div key={p.platform} className="flex justify-between text-sm">
                  <span className="text-fg">{p.label}</span>
                  <span className="text-fg-muted">{pq.unlimited ? p.used : `${p.used} / ${p.limit}`}</span>
                </div>
              ))}
            {!pq.unlimited && (
              <p className="text-fg-muted text-[11px] pt-1">Lượt mới được cộng lại lúc {pq.reset_time_vn} mỗi ngày.</p>
            )}
          </div>
        )}
        <div className="col-span-2 bg-surface-2 border border-line rounded-xl p-4 flex items-center gap-3">
          <Archive className="w-4 h-4 text-fg-muted" />
          <div>
            <p className="text-fg font-bold text-lg">{usage.bulk_jobs_count}</p>
            <p className="text-fg-muted text-xs">Bulk jobs tổng cộng</p>
          </div>
        </div>
      </div>

      {/* ── Storage Section ──────────────────────────────── */}
      {storageInfo && (
        <StorageSection info={storageInfo} isPro={isPro} />
      )}

      {/* Upgrade CTA for free users */}
      {!isPro && (
        <div className="bg-surface-2 border border-accent/20 rounded-xl p-4">
          <p className="text-fg font-semibold text-sm mb-1">Nâng lên Pro — tải không giới hạn</p>
          <p className="text-fg-muted text-xs mb-3">
            Xoá giới hạn ngày/tháng, ưu tiên queue, không có quảng cáo.
          </p>
          <button className="px-4 py-2 rounded-lg bg-accent text-accent-fg text-sm font-bold hover:opacity-90 transition">
            Xem gói Pro →
          </button>
        </div>
      )}

      {/* ── API Key Section (moved up, webhook follows) ─── */}
      <div className="rounded-xl border border-line overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 bg-surface border-b border-line">
          <Key className={`w-4 h-4 ${isPro ? 'text-fg-2' : 'text-fg-muted'}`} />
          <span className="text-fg font-bold text-sm">API Key</span>
          {isPro && (
            <span className="ml-auto text-[10px] font-bold text-fg-2 bg-surface-2 border border-line px-2 py-0.5 rounded-full">
              Pro
            </span>
          )}
        </div>

        <div className="p-4 bg-surface-2">
          {/* Free tier: locked */}
          {!isPro && (
            <div className="flex flex-col items-center gap-3 py-4 text-center">
              <Lock className="w-8 h-8 text-fg-muted" />
              <div>
                <p className="text-fg font-semibold text-sm">Tính năng Pro</p>
                <p className="text-fg-muted text-xs mt-1">Nâng cấp để dùng API Key và tích hợp VidGrab vào ứng dụng của bạn.</p>
              </div>
              <button className="mt-1 px-4 py-2 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:opacity-90 transition">
                Nâng cấp Pro →
              </button>
            </div>
          )}

          {/* Pro tier */}
          {isPro && (
            <>
              {/* Error */}
              {apiKeyError && (
                <div className="mb-3 px-3 py-2 rounded-lg bg-danger-soft border border-danger/30 text-danger text-xs font-semibold">
                  {apiKeyError}
                </div>
              )}

              {/* Show-once key reveal */}
              {newApiKey && (
                <div className="mb-4 rounded-lg border border-accent/40 bg-accent-soft overflow-hidden">
                  <div className="flex items-start gap-2 px-3 py-2.5 border-b border-accent/20">
                    <span className="text-accent-text text-xs font-bold">Lưu key ngay — không thể xem lại sau khi đóng.</span>
                  </div>
                  <div className="flex items-center gap-2 px-3 py-2.5">
                    <code className="flex-1 text-xs font-mono text-success break-all select-all">{newApiKey}</code>
                    <button
                      onClick={handleCopyKey}
                      title="Sao chép"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-surface-2 text-fg-2 hover:text-fg hover:bg-line transition-colors"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setNewApiKey(null)}
                      title="Đóng"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-surface-2 text-fg-muted hover:text-fg hover:bg-line transition-colors text-xs font-bold leading-none"
                      aria-label="Đóng"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              )}

              {/* No key yet */}
              {apiKeyStatus && !apiKeyStatus.has_key && !newApiKey && (
                <div className="flex flex-col items-center gap-3 py-3 text-center">
                  <p className="text-fg-muted text-xs">Bạn chưa có API Key. Tạo key để tích hợp VidGrab vào ứng dụng.</p>
                  <button
                    onClick={handleGenerateApiKey}
                    disabled={apiKeyLoading}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent hover:bg-accent-hover text-accent-fg text-sm font-bold transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {apiKeyLoading
                      ? <span className="w-4 h-4 border-2 border-line border-t-fg rounded-full animate-spin" />
                      : <Key className="w-4 h-4" />}
                    Tạo API Key
                  </button>
                </div>
              )}

              {/* Key active */}
              {apiKeyStatus && apiKeyStatus.has_key && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-success flex-shrink-0" />
                    <span className="text-success text-xs font-bold">Key đang hoạt động</span>
                  </div>
                  <div className="space-y-1 text-xs text-fg-muted">
                    {apiKeyStatus.created_at && (
                      <p>Tạo lúc: <span className="text-fg-2">{new Date(apiKeyStatus.created_at).toLocaleString('vi-VN')}</span></p>
                    )}
                    {apiKeyStatus.last_used_at ? (
                      <p>Dùng lần cuối: <span className="text-fg-2">{new Date(apiKeyStatus.last_used_at).toLocaleString('vi-VN')}</span></p>
                    ) : (
                      <p className="text-fg-muted">Chưa dùng lần nào.</p>
                    )}
                  </div>
                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={handleGenerateApiKey}
                      disabled={apiKeyLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-2 text-fg-2 border border-line hover:bg-line text-xs font-bold transition-colors disabled:opacity-60"
                    >
                      {apiKeyLoading
                        ? <span className="w-3.5 h-3.5 border-2 border-line border-t-line rounded-full animate-spin" />
                        : <RefreshCw className="w-3.5 h-3.5" />}
                      Tạo Key mới
                    </button>
                    <button
                      onClick={handleRevokeApiKey}
                      disabled={apiKeyLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-danger-soft text-danger border border-danger/30 hover:bg-danger/20 text-xs font-bold transition-colors disabled:opacity-60"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Thu hồi Key
                    </button>
                  </div>
                </div>
              )}

              {/* Loading initial status */}
              {!apiKeyStatus && (
                <div className="flex justify-center py-4">
                  <span className="w-5 h-5 border-2 border-line border-t-transparent rounded-full animate-spin" />
                </div>
              )}
            </>
          )}
        </div>
      </div>
      {/* ── Webhook Section ──────────────────────────────── */}
      <div className="rounded-xl border border-line overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 bg-surface border-b border-line">
          <Webhook className={`w-4 h-4 ${isPro ? 'text-fg-2' : 'text-fg-muted'}`} />
          <span className="text-fg font-bold text-sm">Webhook</span>
          {isPro && (
            <span className="ml-auto text-[10px] font-bold text-fg-2 bg-surface-2 border border-line px-2 py-0.5 rounded-full">
              Pro
            </span>
          )}
        </div>

        <div className="p-4 bg-surface-2">
          {/* Free tier: locked */}
          {!isPro && (
            <div className="flex flex-col items-center gap-3 py-4 text-center">
              <Lock className="w-8 h-8 text-fg-muted" />
              <div>
                <p className="text-fg font-semibold text-sm">Tính năng Pro</p>
                <p className="text-fg-muted text-xs mt-1">Nâng cấp để nhận webhook khi có sự kiện tải xuống.</p>
              </div>
              <button className="mt-1 px-4 py-2 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:opacity-90 transition">
                Nâng cấp Pro →
              </button>
            </div>
          )}

          {/* Pro tier */}
          {isPro && (
            <div className="space-y-4">
              {/* Error */}
              {webhookError && (
                <div className="px-3 py-2 rounded-lg bg-danger-soft border border-danger/30 text-danger text-xs font-semibold">
                  {webhookError}
                </div>
              )}

              {/* Show-once secret reveal */}
              {webhookSecret && (
                <div className="rounded-lg border border-accent/40 bg-accent-soft overflow-hidden">
                  <div className="flex items-start gap-2 px-3 py-2.5 border-b border-accent/20">
                    <Shield className="w-3.5 h-3.5 text-accent-text flex-shrink-0 mt-0.5" />
                    <span className="text-accent-text text-xs font-bold">Lưu secret ngay — không thể xem lại sau khi đóng.</span>
                  </div>
                  <div className="flex items-center gap-2 px-3 py-2.5">
                    <code className="flex-1 text-xs font-mono text-success break-all select-all">{webhookSecret}</code>
                    <button
                      onClick={() => navigator.clipboard.writeText(webhookSecret).catch(() => {})}
                      title="Sao chép"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-surface-2 text-fg-2 hover:text-fg hover:bg-line transition-colors"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setWebhookSecret(null)}
                      title="Đóng"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-surface-2 text-fg-muted hover:text-fg hover:bg-line transition-colors text-xs font-bold leading-none"
                      aria-label="Đóng"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              )}

              {/* No webhook: registration form */}
              {!webhookHasReg && !webhookLoading && (
                <form onSubmit={handleRegisterWebhook} className="space-y-3">
                  <p className="text-fg-muted text-xs">Nhận sự kiện tải xuống (job_done, job_failed) tới URL của bạn.</p>
                  <div className="flex gap-2">
                    <input
                      type="url"
                      value={webhookUrl}
                      onChange={e => setWebhookUrl(e.target.value)}
                      placeholder="https://your-server.com/webhook"
                      className="flex-1 bg-surface-2 border border-line-strong rounded-lg px-3 py-2 text-sm text-fg placeholder:text-fg-muted focus:outline-none focus:border-line transition-colors"
                    />
                    <button
                      type="submit"
                      disabled={!webhookUrl.trim()}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-accent hover:bg-accent-hover text-accent-fg text-sm font-bold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <Webhook className="w-4 h-4" />
                      Đăng ký
                    </button>
                  </div>
                </form>
              )}

              {/* Loading */}
              {webhookLoading && (
                <div className="flex justify-center py-4">
                  <span className="w-5 h-5 border-2 border-line border-t-transparent rounded-full animate-spin" />
                </div>
              )}

              {/* Webhook active */}
              {webhookHasReg && !webhookLoading && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-success flex-shrink-0" />
                      <span className="text-success text-xs font-bold">Webhook đang hoạt động</span>
                    </div>
                    <button
                      onClick={handleRevokeWebhook}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-danger-soft text-danger border border-danger/30 hover:bg-danger/20 text-xs font-bold transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Thu hồi
                    </button>
                  </div>

                  {/* Logs table */}
                  {webhookLogs.length > 0 && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 text-fg-muted text-xs font-semibold">
                        <Clock className="w-3.5 h-3.5" />
                        <span>Delivery logs ({webhookLogs.length})</span>
                      </div>
                      <div className="rounded-lg border border-line overflow-hidden">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="bg-surface border-b border-line">
                              <th className="px-3 py-2 text-left text-fg-muted font-semibold">Job ID</th>
                              <th className="px-3 py-2 text-left text-fg-muted font-semibold">Status</th>
                              <th className="px-3 py-2 text-left text-fg-muted font-semibold hidden sm:table-cell">Thời gian</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-line">
                            {webhookLogs.slice(0, 20).map((log, i) => (
                              <tr key={i} className="hover:bg-surface-2 transition-colors">
                                <td className="px-3 py-2 font-mono text-fg-2">
                                  {log.job_id ? `${log.job_id.slice(0, 8)}…` : '—'}
                                </td>
                                <td className="px-3 py-2">
                                  <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                    log.success
                                      ? 'bg-success-soft text-success border border-success/30'
                                      : 'bg-danger-soft text-danger border border-danger/30'
                                  }`}>
                                    {log.success ? 'OK' : 'FAIL'}
                                    {log.status_code && <span className="opacity-70">·{log.status_code}</span>}
                                  </span>
                                </td>
                                <td className="px-3 py-2 text-fg-muted hidden sm:table-cell">
                                  {log.delivered_at
                                    ? new Date(log.delivered_at).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
                                    : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {webhookLogs.length === 0 && (
                    <p className="text-fg-muted text-xs text-center py-3">Chưa có delivery nào. Webhook sẽ được gọi khi job hoàn tất.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function fmtBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${units[i]}`;
}

function StorageSection({ info, isPro }) {
  const pinnedLimit = isPro ? 50 : 3;
  const pinnedCount = info.pinned_count ?? 0;
  const pinnedBytes = info.pinned_bytes ?? 0;
  const tempCount   = info.temp_count ?? 0;
  const tempBytes   = info.temp_bytes ?? 0;
  const archiveTotal = info.archive_total ?? 0;
  const pinnedPct   = pinnedLimit > 0 ? Math.min(100, (pinnedCount / pinnedLimit) * 100) : 0;
  const pinBarColor = pinnedPct >= 90 ? 'bg-danger' : pinnedPct >= 70 ? 'bg-accent' : 'bg-success';

  return (
    <div className="rounded-xl border border-line overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 bg-surface border-b border-line">
        <HardDrive className="w-4 h-4 text-fg-2" />
        <span className="text-fg font-bold text-sm">Storage</span>
        <span className="ml-auto text-[10px] text-fg-muted">Oracle Cloud 10 GB</span>
      </div>

      <div className="p-4 bg-surface-2 space-y-4">
        {/* Pinned files */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-1.5 text-fg-2 font-semibold">
              <Pin className="w-3.5 h-3.5 text-fg-2" />
              File được ghim
            </div>
            <span className="text-fg-muted">
              {pinnedCount} / {pinnedLimit} file · {fmtBytes(pinnedBytes)}
            </span>
          </div>
          <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${pinBarColor}`}
              style={{ width: `${pinnedPct}%` }}
            />
          </div>
          <p className="text-[11px] text-fg-muted">
            {isPro
              ? 'Pro: ghim tối đa 50 file · giữ 30 ngày'
              : 'Free: ghim tối đa 3 file · giữ 7 ngày · Nâng cấp Pro để ghim nhiều hơn'}
          </p>
        </div>

        {/* Row: temp + archive */}
        <div className="grid grid-cols-2 gap-3 pt-1">
          <div className="bg-surface-2 rounded-lg p-3 space-y-0.5">
            <p className="text-xs text-fg-muted">File tạm</p>
            <p className="text-fg font-bold">{tempCount} <span className="text-fg-muted text-xs font-normal">file</span></p>
            <p className="text-[11px] text-fg-muted">{fmtBytes(tempBytes)} · hết hạn sau 24h</p>
          </div>
          <div className="bg-surface-2 rounded-lg p-3 space-y-0.5">
            <p className="text-xs text-fg-muted">Archive</p>
            <p className="text-fg font-bold">{archiveTotal} <span className="text-fg-muted text-xs font-normal">mục</span></p>
            <p className="text-[11px] text-fg-muted">Metadata only · không giới hạn</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, limit, pct, isPro }) {
  const barColor = pct >= 90 ? 'bg-danger' : pct >= 70 ? 'bg-accent' : 'bg-accent';

  return (
    <div className="bg-surface-2 border border-line rounded-xl p-4 space-y-2">
      <div className="flex items-center gap-2 text-fg-muted">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-fg font-bold text-2xl">{value}</span>
        {!isPro && limit > 0 && (
          <span className="text-fg-muted text-xs">/ {limit}</span>
        )}
        {isPro && <span className="text-fg-muted text-xs">lượt</span>}
      </div>
      {!isPro && limit > 0 && (
        <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

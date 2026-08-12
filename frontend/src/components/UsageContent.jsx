import { useEffect, useState } from 'react';
import { BarChart2, Zap, Calendar, Archive, Crown, RefreshCw, Key, Copy, Trash2, Lock, Webhook, Shield, Clock, HardDrive, Pin } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

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
    const apiBase = localStorage.getItem('vg_api_base') || '';
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
      <div className="w-6 h-6 border-2 border-[#FBBF24] border-t-transparent rounded-full animate-spin" />
    </div>
  );

  if (!usage) return (
    <div className="text-center py-16 text-slate-400">Không tải được dữ liệu. Thử lại sau.</div>
  );

  const dailyPct  = usage.limits.daily  > 0 ? Math.min(100, (usage.downloads_today  / usage.limits.daily)  * 100) : 0;
  const monthPct  = usage.limits.monthly > 0 ? Math.min(100, (usage.downloads_this_month / usage.limits.monthly) * 100) : 0;
  const isPro     = usage.plan === 'pro';

  // Fetch API key status when pro tier is confirmed
  useEffect(() => {
    if (!isPro || !session) return;
    const apiBase = localStorage.getItem('vg_api_base') || '';
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
      const apiBase = localStorage.getItem('vg_api_base') || '';
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
      const apiBase = localStorage.getItem('vg_api_base') || '';
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
    const apiBase = localStorage.getItem('vg_api_base') || '';
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
      const apiBase = localStorage.getItem('vg_api_base') || '';
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
      const apiBase = localStorage.getItem('vg_api_base') || '';
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
          <div className="w-9 h-9 rounded-lg bg-slate-800 flex items-center justify-center">
            <BarChart2 className="w-5 h-5 text-[#FBBF24]" />
          </div>
          <div>
            <h1 className="text-white font-bold text-lg">Quota & Usage</h1>
            <p className="text-slate-400 text-xs">Theo dõi lượt tải theo tài khoản</p>
          </div>
        </div>
        <button onClick={load}
          className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700/50 transition-colors">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Plan badge */}
      <div className={`flex items-center gap-2.5 px-4 py-3 rounded-xl border
        ${isPro
          ? 'bg-[#FBBF24]/10 border-[#FBBF24]/30'
          : 'bg-slate-800/40 border-slate-700/50'}`}>
        <Crown className={`w-5 h-5 ${isPro ? 'text-[#FBBF24]' : 'text-slate-500'}`} />
        <div>
          <p className={`text-sm font-bold ${isPro ? 'text-[#FBBF24]' : 'text-white'}`}>
            {isPro ? 'Pro Plan' : 'Free Plan'}
          </p>
          <p className="text-slate-400 text-xs">
            {isPro ? 'Tải không giới hạn' : `Tối đa ${usage.limits.daily}/ngày, ${usage.limits.monthly}/tháng`}
          </p>
        </div>
        {!isPro && (
          <span className="ml-auto text-[10px] font-bold text-[#FBBF24] bg-[#FBBF24]/10 border border-[#FBBF24]/20 px-2 py-0.5 rounded-full">
            Nâng cấp
          </span>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          icon={<Zap className="w-4 h-4" />}
          label="Hôm nay"
          value={usage.downloads_today}
          limit={usage.limits.daily}
          pct={dailyPct}
          isPro={isPro}
        />
        <StatCard
          icon={<Calendar className="w-4 h-4" />}
          label="Tháng này"
          value={usage.downloads_this_month}
          limit={usage.limits.monthly}
          pct={monthPct}
          isPro={isPro}
        />
        <div className="col-span-2 bg-slate-800/40 border border-slate-700/50 rounded-xl p-4 flex items-center gap-3">
          <Archive className="w-4 h-4 text-slate-400" />
          <div>
            <p className="text-white font-bold text-lg">{usage.bulk_jobs_count}</p>
            <p className="text-slate-400 text-xs">Bulk jobs tổng cộng</p>
          </div>
        </div>
      </div>

      {/* ── Storage Section ──────────────────────────────── */}
      {storageInfo && (
        <StorageSection info={storageInfo} isPro={isPro} />
      )}

      {/* Upgrade CTA for free users */}
      {!isPro && (
        <div className="bg-gradient-to-r from-[#FBBF24]/5 to-[#FB923C]/5 border border-[#FBBF24]/20 rounded-xl p-4">
          <p className="text-white font-semibold text-sm mb-1">Nâng lên Pro — tải không giới hạn</p>
          <p className="text-slate-400 text-xs mb-3">
            Xoá giới hạn ngày/tháng, ưu tiên queue, không có quảng cáo.
          </p>
          <button className="px-4 py-2 rounded-lg bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold hover:opacity-90 transition">
            Xem gói Pro →
          </button>
        </div>
      )}

      {/* ── API Key Section (moved up, webhook follows) ─── */}
      <div className="rounded-xl border border-slate-700/50 overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 bg-slate-800/60 border-b border-slate-700/50">
          <Key className={`w-4 h-4 ${isPro ? 'text-violet-400' : 'text-slate-500'}`} />
          <span className="text-white font-bold text-sm">API Key</span>
          {isPro && (
            <span className="ml-auto text-[10px] font-bold text-violet-300 bg-violet-500/15 border border-violet-500/30 px-2 py-0.5 rounded-full">
              Pro
            </span>
          )}
        </div>

        <div className="p-4 bg-slate-800/30">
          {/* Free tier: locked */}
          {!isPro && (
            <div className="flex flex-col items-center gap-3 py-4 text-center">
              <Lock className="w-8 h-8 text-slate-600" />
              <div>
                <p className="text-white font-semibold text-sm">Tính năng Pro</p>
                <p className="text-slate-400 text-xs mt-1">Nâng cấp để dùng API Key và tích hợp VidGrab vào ứng dụng của bạn.</p>
              </div>
              <button className="mt-1 px-4 py-2 rounded-lg bg-gradient-to-r from-violet-500 to-indigo-500 text-white text-xs font-bold hover:opacity-90 transition">
                Nâng cấp Pro →
              </button>
            </div>
          )}

          {/* Pro tier */}
          {isPro && (
            <>
              {/* Error */}
              {apiKeyError && (
                <div className="mb-3 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-xs font-semibold">
                  {apiKeyError}
                </div>
              )}

              {/* Show-once key reveal */}
              {newApiKey && (
                <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/8 overflow-hidden">
                  <div className="flex items-start gap-2 px-3 py-2.5 border-b border-amber-500/20">
                    <span className="text-amber-400 text-xs font-bold">Lưu key ngay — không thể xem lại sau khi đóng.</span>
                  </div>
                  <div className="flex items-center gap-2 px-3 py-2.5">
                    <code className="flex-1 text-xs font-mono text-emerald-300 break-all select-all">{newApiKey}</code>
                    <button
                      onClick={handleCopyKey}
                      title="Sao chép"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-slate-700/60 text-slate-300 hover:text-white hover:bg-slate-700 transition-colors"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setNewApiKey(null)}
                      title="Đóng"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-slate-700/60 text-slate-400 hover:text-white hover:bg-slate-700 transition-colors text-xs font-bold leading-none"
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
                  <p className="text-slate-400 text-xs">Bạn chưa có API Key. Tạo key để tích hợp VidGrab vào ứng dụng.</p>
                  <button
                    onClick={handleGenerateApiKey}
                    disabled={apiKeyLoading}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-bold transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {apiKeyLoading
                      ? <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      : <Key className="w-4 h-4" />}
                    Tạo API Key
                  </button>
                </div>
              )}

              {/* Key active */}
              {apiKeyStatus && apiKeyStatus.has_key && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
                    <span className="text-emerald-400 text-xs font-bold">Key đang hoạt động</span>
                  </div>
                  <div className="space-y-1 text-xs text-slate-400">
                    {apiKeyStatus.created_at && (
                      <p>Tạo lúc: <span className="text-slate-300">{new Date(apiKeyStatus.created_at).toLocaleString('vi-VN')}</span></p>
                    )}
                    {apiKeyStatus.last_used_at ? (
                      <p>Dùng lần cuối: <span className="text-slate-300">{new Date(apiKeyStatus.last_used_at).toLocaleString('vi-VN')}</span></p>
                    ) : (
                      <p className="text-slate-500">Chưa dùng lần nào.</p>
                    )}
                  </div>
                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={handleGenerateApiKey}
                      disabled={apiKeyLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600/20 text-violet-300 border border-violet-500/30 hover:bg-violet-600/30 text-xs font-bold transition-colors disabled:opacity-60"
                    >
                      {apiKeyLoading
                        ? <span className="w-3.5 h-3.5 border-2 border-violet-300/30 border-t-violet-300 rounded-full animate-spin" />
                        : <RefreshCw className="w-3.5 h-3.5" />}
                      Tạo Key mới
                    </button>
                    <button
                      onClick={handleRevokeApiKey}
                      disabled={apiKeyLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20 text-xs font-bold transition-colors disabled:opacity-60"
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
                  <span className="w-5 h-5 border-2 border-violet-400 border-t-transparent rounded-full animate-spin" />
                </div>
              )}
            </>
          )}
        </div>
      </div>
      {/* ── Webhook Section ──────────────────────────────── */}
      <div className="rounded-xl border border-slate-700/50 overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 bg-slate-800/60 border-b border-slate-700/50">
          <Webhook className={`w-4 h-4 ${isPro ? 'text-sky-400' : 'text-slate-500'}`} />
          <span className="text-white font-bold text-sm">Webhook</span>
          {isPro && (
            <span className="ml-auto text-[10px] font-bold text-sky-300 bg-sky-500/15 border border-sky-500/30 px-2 py-0.5 rounded-full">
              Pro
            </span>
          )}
        </div>

        <div className="p-4 bg-slate-800/30">
          {/* Free tier: locked */}
          {!isPro && (
            <div className="flex flex-col items-center gap-3 py-4 text-center">
              <Lock className="w-8 h-8 text-slate-600" />
              <div>
                <p className="text-white font-semibold text-sm">Tính năng Pro</p>
                <p className="text-slate-400 text-xs mt-1">Nâng cấp để nhận webhook khi có sự kiện tải xuống.</p>
              </div>
              <button className="mt-1 px-4 py-2 rounded-lg bg-gradient-to-r from-sky-500 to-indigo-500 text-white text-xs font-bold hover:opacity-90 transition">
                Nâng cấp Pro →
              </button>
            </div>
          )}

          {/* Pro tier */}
          {isPro && (
            <div className="space-y-4">
              {/* Error */}
              {webhookError && (
                <div className="px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-xs font-semibold">
                  {webhookError}
                </div>
              )}

              {/* Show-once secret reveal */}
              {webhookSecret && (
                <div className="rounded-lg border border-amber-500/40 bg-amber-500/8 overflow-hidden">
                  <div className="flex items-start gap-2 px-3 py-2.5 border-b border-amber-500/20">
                    <Shield className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 mt-0.5" />
                    <span className="text-amber-400 text-xs font-bold">Lưu secret ngay — không thể xem lại sau khi đóng.</span>
                  </div>
                  <div className="flex items-center gap-2 px-3 py-2.5">
                    <code className="flex-1 text-xs font-mono text-emerald-300 break-all select-all">{webhookSecret}</code>
                    <button
                      onClick={() => navigator.clipboard.writeText(webhookSecret).catch(() => {})}
                      title="Sao chép"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-slate-700/60 text-slate-300 hover:text-white hover:bg-slate-700 transition-colors"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => setWebhookSecret(null)}
                      title="Đóng"
                      className="flex-shrink-0 p-1.5 rounded-lg bg-slate-700/60 text-slate-400 hover:text-white hover:bg-slate-700 transition-colors text-xs font-bold leading-none"
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
                  <p className="text-slate-400 text-xs">Nhận sự kiện tải xuống (job_done, job_failed) tới URL của bạn.</p>
                  <div className="flex gap-2">
                    <input
                      type="url"
                      value={webhookUrl}
                      onChange={e => setWebhookUrl(e.target.value)}
                      placeholder="https://your-server.com/webhook"
                      className="flex-1 bg-slate-900/60 border border-slate-600/50 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-sky-400/60 transition-colors"
                    />
                    <button
                      type="submit"
                      disabled={!webhookUrl.trim()}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-sm font-bold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
                  <span className="w-5 h-5 border-2 border-sky-400 border-t-transparent rounded-full animate-spin" />
                </div>
              )}

              {/* Webhook active */}
              {webhookHasReg && !webhookLoading && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0" />
                      <span className="text-emerald-400 text-xs font-bold">Webhook đang hoạt động</span>
                    </div>
                    <button
                      onClick={handleRevokeWebhook}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20 text-xs font-bold transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Thu hồi
                    </button>
                  </div>

                  {/* Logs table */}
                  {webhookLogs.length > 0 && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 text-slate-400 text-xs font-semibold">
                        <Clock className="w-3.5 h-3.5" />
                        <span>Delivery logs ({webhookLogs.length})</span>
                      </div>
                      <div className="rounded-lg border border-slate-700/50 overflow-hidden">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="bg-slate-800/60 border-b border-slate-700/50">
                              <th className="px-3 py-2 text-left text-slate-400 font-semibold">Job ID</th>
                              <th className="px-3 py-2 text-left text-slate-400 font-semibold">Status</th>
                              <th className="px-3 py-2 text-left text-slate-400 font-semibold hidden sm:table-cell">Thời gian</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-700/30">
                            {webhookLogs.slice(0, 20).map((log, i) => (
                              <tr key={i} className="hover:bg-slate-700/20 transition-colors">
                                <td className="px-3 py-2 font-mono text-slate-300">
                                  {log.job_id ? `${log.job_id.slice(0, 8)}…` : '—'}
                                </td>
                                <td className="px-3 py-2">
                                  <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                    log.success
                                      ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                                      : 'bg-red-500/15 text-red-400 border border-red-500/30'
                                  }`}>
                                    {log.success ? 'OK' : 'FAIL'}
                                    {log.status_code && <span className="opacity-70">·{log.status_code}</span>}
                                  </span>
                                </td>
                                <td className="px-3 py-2 text-slate-500 hidden sm:table-cell">
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
                    <p className="text-slate-500 text-xs text-center py-3">Chưa có delivery nào. Webhook sẽ được gọi khi job hoàn tất.</p>
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
  const pinBarColor = pinnedPct >= 90 ? 'bg-red-500' : pinnedPct >= 70 ? 'bg-amber-400' : 'bg-emerald-400';

  return (
    <div className="rounded-xl border border-slate-700/50 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 bg-slate-800/60 border-b border-slate-700/50">
        <HardDrive className="w-4 h-4 text-teal-400" />
        <span className="text-white font-bold text-sm">Storage</span>
        <span className="ml-auto text-[10px] text-slate-500">Oracle Cloud 10 GB</span>
      </div>

      <div className="p-4 bg-slate-800/30 space-y-4">
        {/* Pinned files */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-1.5 text-slate-300 font-semibold">
              <Pin className="w-3.5 h-3.5 text-teal-400" />
              File được ghim
            </div>
            <span className="text-slate-400">
              {pinnedCount} / {pinnedLimit} file · {fmtBytes(pinnedBytes)}
            </span>
          </div>
          <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${pinBarColor}`}
              style={{ width: `${pinnedPct}%` }}
            />
          </div>
          <p className="text-[11px] text-slate-500">
            {isPro
              ? 'Pro: ghim tối đa 50 file · giữ 30 ngày'
              : 'Free: ghim tối đa 3 file · giữ 7 ngày · Nâng cấp Pro để ghim nhiều hơn'}
          </p>
        </div>

        {/* Row: temp + archive */}
        <div className="grid grid-cols-2 gap-3 pt-1">
          <div className="bg-slate-900/40 rounded-lg p-3 space-y-0.5">
            <p className="text-xs text-slate-400">File tạm</p>
            <p className="text-white font-bold">{tempCount} <span className="text-slate-500 text-xs font-normal">file</span></p>
            <p className="text-[11px] text-slate-500">{fmtBytes(tempBytes)} · hết hạn sau 24h</p>
          </div>
          <div className="bg-slate-900/40 rounded-lg p-3 space-y-0.5">
            <p className="text-xs text-slate-400">Archive</p>
            <p className="text-white font-bold">{archiveTotal} <span className="text-slate-500 text-xs font-normal">mục</span></p>
            <p className="text-[11px] text-slate-500">Metadata only · không giới hạn</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, limit, pct, isPro }) {
  const barColor = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-[#FBBF24]';

  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-4 space-y-2">
      <div className="flex items-center gap-2 text-slate-400">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-white font-bold text-2xl">{value}</span>
        {!isPro && limit > 0 && (
          <span className="text-slate-500 text-xs">/ {limit}</span>
        )}
        {isPro && <span className="text-slate-500 text-xs">lượt</span>}
      </div>
      {!isPro && limit > 0 && (
        <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

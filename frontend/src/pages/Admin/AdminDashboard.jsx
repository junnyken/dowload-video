import { useState, useEffect, useCallback } from 'react';
import {
  Shield, CheckCircle, XCircle, AlertTriangle, Users, Download, Activity,
  ToggleLeft, ToggleRight, Key, Copy, TrendingUp, BarChart3, Bell,
  RefreshCw, Zap, Clock, Loader2, Send, ChevronDown
} from 'lucide-react';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1/admin`;

// ── Pure CSS Mini Bar Chart ─────────────────────────────────────────
function MiniBarChart({ data, labelKey = 'date', valueKey = 'total', successKey = 'success', failedKey = 'failed', height = 200 }) {
  if (!data || data.length === 0) return <p className="text-text-muted text-sm">Không có dữ liệu</p>;

  const maxVal = Math.max(...data.map(d => d[valueKey] || 0), 1);

  return (
    <div className="flex items-end gap-1.5 w-full" style={{ height }}>
      {data.map((d, i) => {
        const total = d[valueKey] || 0;
        const success = d[successKey] || 0;
        const failed = d[failedKey] || 0;
        const successH = maxVal > 0 ? (success / maxVal) * 100 : 0;
        const failedH = maxVal > 0 ? (failed / maxVal) * 100 : 0;
        const dateLabel = d[labelKey]?.slice(5) || '';

        return (
          <div key={i} className="flex-1 flex flex-col items-center gap-1 group relative">
            {/* Tooltip */}
            <div className="absolute -top-10 left-1/2 -translate-x-1/2 bg-[#0a0a0a] text-white text-[10px] px-2 py-1 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-10 pointer-events-none border border-slate-700">
              {dateLabel}: {total} ({success}✓ {failed}✗)
            </div>
            {/* Bars stacked */}
            <div className="w-full flex flex-col justify-end" style={{ height: height - 24 }}>
              <div className="w-full rounded-t-sm bg-red-500/80 transition-all duration-500" style={{ height: `${failedH}%`, minHeight: failed > 0 ? 2 : 0 }} />
              <div className="w-full bg-emerald-500/80 transition-all duration-500 rounded-t-sm" style={{ height: `${successH}%`, minHeight: success > 0 ? 2 : 0 }} />
            </div>
            <span className="text-[9px] text-text-muted truncate w-full text-center">{dateLabel}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Platform Bar ────────────────────────────────────────────────────
function PlatformBar({ platform, count, maxCount }) {
  const pct = maxCount > 0 ? (count / maxCount) * 100 : 0;
  const colors = {
    TikTok: 'bg-cyan-400', Douyin: 'bg-rose-500', YouTube: 'bg-red-500',
    Facebook: 'bg-blue-500', Instagram: 'bg-gradient-to-r from-orange-400 to-pink-500',
    'X (Twitter)': 'bg-white', Spotify: 'bg-green-500', ZIP: 'bg-amber-400', Other: 'bg-slate-400',
  };
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-text-secondary w-20 truncate">{platform}</span>
      <div className="flex-1 bg-surface rounded-full h-3 overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${colors[platform] || 'bg-slate-400'}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-bold text-text-primary w-10 text-right">{count}</span>
    </div>
  );
}

// ── Active Job Item ─────────────────────────────────────────────────
function ActiveJobItem({ job }) {
  const isProcessing = job.status === 'processing';
  const url = job.original_url || '';
  const shortUrl = url.length > 50 ? url.slice(0, 50) + '...' : url;
  const time = job.created_at ? new Date(job.created_at).toLocaleTimeString('vi-VN') : '';
  return (
    <div className={`flex items-center gap-3 px-3 py-2 rounded-xl border transition-colors ${isProcessing ? 'bg-amber-500/5 border-amber-500/20' : 'bg-surface border-border/50'}`}>
      {isProcessing ? <Loader2 className="w-4 h-4 text-amber-400 animate-spin flex-shrink-0" /> : <Clock className="w-4 h-4 text-text-muted flex-shrink-0" />}
      <span className="text-xs text-text-secondary truncate flex-1">{shortUrl}</span>
      <span className="text-[10px] text-text-muted">{time}</span>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═════════════════════════════════════════════════════════════════════

export default function AdminDashboard() {
  const [stats, setStats] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [activeJobs, setActiveJobs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');
  const [chartDays, setChartDays] = useState(7);
  const [testingNotif, setTestingNotif] = useState(false);
  const [notifResult, setNotifResult] = useState(null);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API}/stats`);
      const data = await res.json();
      if (data.success) setStats(data);
    } catch (e) { console.error('Stats fetch failed', e); }
    finally { setLoading(false); }
  }, []);

  const fetchAnalytics = useCallback(async () => {
    try {
      const res = await fetch(`${API}/analytics?days=${chartDays}`);
      const data = await res.json();
      if (data.success) setAnalytics(data);
    } catch (e) { console.error('Analytics fetch failed', e); }
  }, [chartDays]);

  const fetchActiveJobs = useCallback(async () => {
    try {
      const res = await fetch(`${API}/active-jobs`);
      const data = await res.json();
      if (data.success) setActiveJobs(data);
    } catch (e) { console.error('Active jobs fetch failed', e); }
  }, []);

  useEffect(() => {
    fetchStats();
    fetchAnalytics();
    fetchActiveJobs();
    const intv = setInterval(() => { fetchStats(); fetchActiveJobs(); }, 10000);
    return () => clearInterval(intv);
  }, [fetchStats, fetchAnalytics, fetchActiveJobs]);

  useEffect(() => { fetchAnalytics(); }, [chartDays, fetchAnalytics]);

  const toggleUserPlan = async (user_id, currentPlan) => {
    const newPlan = currentPlan === 'pro' ? 'free' : 'pro';
    try {
      await fetch(`${API}/update-user`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id, plan: newPlan })
      });
      fetchStats();
    } catch { alert("Failed to update user"); }
  };

  const sendTestNotification = async () => {
    setTestingNotif(true);
    setNotifResult(null);
    try {
      const res = await fetch(`${API}/send-test-notification`, { method: 'POST' });
      const data = await res.json();
      setNotifResult(data.success ? '✅ Đã gửi!' : '❌ Thất bại');
    } catch { setNotifResult('❌ Lỗi kết nối'); }
    finally { setTestingNotif(false); setTimeout(() => setNotifResult(null), 4000); }
  };

  if (loading || !stats) {
    return <div className="p-8 text-text-muted flex gap-2"><Activity className="animate-pulse" /> Đang tải Admin Center...</div>;
  }

  const scraperAPIcredits = stats.providers?.ScraperAPI ?? stats.providers?.scraperapi ?? 0;
  const successRate = analytics?.summary?.success_rate ?? 0;
  const totalActive = (activeJobs?.processing_count || 0) + (activeJobs?.pending_count || 0);

  const tabs = [
    { id: 'overview', label: 'Tổng Quan', icon: BarChart3 },
    { id: 'analytics', label: 'Phân Tích', icon: TrendingUp },
    { id: 'apikeys', label: 'API & Credits', icon: Key },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ── Header ────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Shield className="w-6 h-6 text-red-400" />
            <h2 className="text-2xl font-bold text-white tracking-tight">Admin Center</h2>
          </div>
          <p className="text-sm text-slate-400 ml-9">Quản lý hệ thống, theo dõi hiệu suất & cảnh báo.</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Telegram Test */}
          <button
            onClick={sendTestNotification}
            disabled={testingNotif}
            className="flex items-center gap-2 px-3 py-2 text-xs font-bold rounded-xl bg-[#0088cc]/10 text-[#0088cc] border border-[#0088cc]/30 hover:bg-[#0088cc]/20 transition-colors cursor-pointer disabled:opacity-50"
          >
            {testingNotif ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            Test Telegram
          </button>
          {notifResult && <span className="text-xs font-bold animate-in fade-in">{notifResult}</span>}
          {/* Refresh */}
          <button onClick={() => { fetchStats(); fetchAnalytics(); fetchActiveJobs(); }} className="p-2 rounded-xl bg-surface border border-border text-text-muted hover:text-white transition-colors cursor-pointer">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* ── Tab Navigation ────────────────────────────── */}
      <div className="flex bg-[#012622]/50 rounded-xl p-1 border border-slate-700/50 gap-1 backdrop-blur-md">
        {tabs.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold rounded-lg transition-colors cursor-pointer ${
                activeTab === tab.id ? 'bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md' : 'text-slate-300 hover:text-white hover:bg-white/10'
              }`}
            >
              <Icon className="w-4 h-4" /> {tab.label}
            </button>
          );
        })}
      </div>

      {/* ══════════════════════════════════════════════════ */}
      {/* TAB: OVERVIEW                                     */}
      {/* ══════════════════════════════════════════════════ */}
      {activeTab === 'overview' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {/* Stat Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard icon={Download} label="Downloads Hôm Nay" value={stats.total_downloads_today} color="text-amber-400" bgColor="bg-amber-400/10" />
            <StatCard icon={Users} label="Người Dùng" value={stats.total_users} color="text-cyan-400" bgColor="bg-cyan-400/10" />
            <StatCard icon={TrendingUp} label="Tỷ Lệ Thành Công" value={`${successRate}%`} color={successRate >= 90 ? 'text-emerald-400' : successRate >= 70 ? 'text-amber-400' : 'text-red-400'} bgColor={successRate >= 90 ? 'bg-emerald-400/10' : successRate >= 70 ? 'bg-amber-400/10' : 'bg-red-400/10'} />
            <StatCard icon={Zap} label="Jobs Đang Chạy" value={totalActive} color="text-violet-400" bgColor="bg-violet-400/10" />
          </div>

          {/* Two Columns: Users + Logs */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* User Table */}
            <div className="p-5 bg-[#0a1a17] border border-slate-700/50 shadow-lg rounded-2xl">
              <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2"><Users className="w-4 h-4 text-cyan-400" /> Người Dùng Gần Đây</h3>
              <div className="overflow-auto max-h-[350px]">
                <table className="w-full text-sm text-left">
                  <thead className="bg-[#012622] sticky top-0 text-[10px] uppercase text-slate-400">
                    <tr><th className="px-3 py-2">ID</th><th className="px-3 py-2">DL</th><th className="px-3 py-2 text-right">Plan</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/30">
                    {stats.recent_users.map(u => (
                      <tr key={u.user_id} className="hover:bg-white/5 transition-colors">
                        <td className="px-3 py-2 text-slate-300 truncate max-w-[140px] text-xs">{u.user_id}</td>
                        <td className="px-3 py-2 text-slate-400 text-xs">{u.downloads_today}</td>
                        <td className="px-3 py-2 text-right">
                          <button onClick={() => toggleUserPlan(u.user_id, u.plan || 'free')} className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg font-bold text-[10px] transition-colors cursor-pointer ${(u.plan || 'free') === 'pro' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700/50 text-slate-400 hover:text-white'}`}>
                            {(u.plan || 'free') === 'pro' ? <ToggleRight className="w-3 h-3" /> : <ToggleLeft className="w-3 h-3" />}
                            {(u.plan || 'free').toUpperCase()}
                          </button>
                        </td>
                      </tr>
                    ))}
                    {stats.recent_users.length === 0 && <tr><td colSpan="3" className="text-center py-4 text-slate-500 text-xs">Chưa có người dùng.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>

            {/* System Logs */}
            <div className="p-5 bg-[#0a1a17] border border-slate-700/50 shadow-lg rounded-2xl flex flex-col">
              <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-amber-400" /> Lỗi Gần Đây</h3>
              <div className="flex-1 overflow-auto max-h-[350px] space-y-2 bg-[#012622] p-3 rounded-xl font-mono text-xs text-slate-400 border border-slate-700/30">
                {stats.failed_jobs.map(job => (
                  <div key={job.id} className="border-b border-slate-700/30 pb-2 last:border-0">
                    <span className="text-red-400 mr-2">[{job.created_at ? new Date(job.created_at).toLocaleTimeString('vi-VN') : '?'}]</span>
                    <span className="text-slate-300">{(job.original_url || '').slice(0, 60)}</span>
                    <p className="mt-1 text-red-400/70 break-words">{job.error_message}</p>
                  </div>
                ))}
                {stats.failed_jobs.length === 0 && <p className="text-emerald-400 flex items-center gap-2"><CheckCircle className="w-4 h-4" /> Không có lỗi. Hệ thống hoạt động tốt.</p>}
              </div>
            </div>
          </div>

          {/* Active Jobs */}
          {totalActive > 0 && (
            <div className="p-5 bg-[#0a1a17] border border-slate-700/50 shadow-lg rounded-2xl">
              <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-amber-400 animate-spin" /> Jobs Đang Xử Lý ({totalActive})
              </h3>
              <div className="space-y-2 max-h-[200px] overflow-auto">
                {(activeJobs?.processing || []).map(j => <ActiveJobItem key={j.id} job={j} />)}
                {(activeJobs?.pending || []).slice(0, 5).map(j => <ActiveJobItem key={j.id} job={j} />)}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════ */}
      {/* TAB: ANALYTICS                                    */}
      {/* ══════════════════════════════════════════════════ */}
      {activeTab === 'analytics' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {/* Chart Period Selector */}
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold text-white flex items-center gap-2"><TrendingUp className="w-5 h-5 text-amber-400" /> Xu Hướng Download</h3>
            <div className="flex gap-1 bg-[#012622]/50 rounded-lg p-1 border border-slate-700/50">
              {[7, 14, 30].map(d => (
                <button
                  key={d}
                  onClick={() => setChartDays(d)}
                  className={`px-3 py-1.5 text-xs font-bold rounded-md transition-colors cursor-pointer ${chartDays === d ? 'bg-amber-400 text-[#012622]' : 'text-slate-400 hover:text-white'}`}
                >
                  {d}D
                </button>
              ))}
            </div>
          </div>

          {/* Summary Cards */}
          {analytics?.summary && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <MiniStatCard label="Tổng Jobs" value={analytics.summary.total_jobs} />
              <MiniStatCard label="Thành Công" value={analytics.summary.total_success} color="text-emerald-400" />
              <MiniStatCard label="Thất Bại" value={analytics.summary.total_failed} color="text-red-400" />
              <MiniStatCard label="TB/Ngày" value={analytics.summary.avg_daily} />
            </div>
          )}

          {/* Chart */}
          <div className="p-5 bg-[#0a1a17] border border-slate-700/50 shadow-lg rounded-2xl">
            <div className="flex items-center justify-between mb-4">
              <p className="text-xs text-slate-400">
                <span className="inline-block w-3 h-3 bg-emerald-500/80 rounded-sm mr-1 align-middle" /> Thành công
                <span className="inline-block w-3 h-3 bg-red-500/80 rounded-sm mr-1 ml-3 align-middle" /> Thất bại
              </p>
            </div>
            <MiniBarChart data={analytics?.daily_stats || []} height={180} />
          </div>

          {/* Platform Distribution */}
          <div className="p-5 bg-[#0a1a17] border border-slate-700/50 shadow-lg rounded-2xl">
            <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><BarChart3 className="w-4 h-4 text-cyan-400" /> Nền Tảng Phổ Biến</h3>
            {analytics?.platform_stats?.length > 0 ? (
              <div className="space-y-3">
                {analytics.platform_stats.filter(p => p.platform !== 'ZIP').slice(0, 8).map(p => (
                  <PlatformBar key={p.platform} platform={p.platform} count={p.count} maxCount={analytics.platform_stats[0]?.count || 1} />
                ))}
              </div>
            ) : (
              <p className="text-xs text-slate-500">Chưa có dữ liệu.</p>
            )}
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════ */}
      {/* TAB: API KEYS & CREDITS                           */}
      {/* ══════════════════════════════════════════════════ */}
      {activeTab === 'apikeys' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <CreditCard provider="ScraperAPI" credits={scraperAPIcredits} threshold={10} apiKey={stats.api_keys?.ScraperAPI} />
            <CreditCard provider="IPRoyal" credits={"N/A"} threshold={0} apiKey={stats.api_keys?.IPRoyal} isProxy={true} />
          </div>

          <div className="p-5 bg-[#0a1a17] border border-amber-500/20 rounded-2xl">
            <h3 className="text-sm font-bold mb-2 text-white flex items-center gap-2"><Bell className="w-4 h-4 text-amber-400" /> Cảnh Báo Tự Động</h3>
            <ul className="text-xs text-slate-400 space-y-1.5 ml-1">
              <li>📨 Telegram thông báo khi <b>batch download hoàn tất</b></li>
              <li>🚨 Telegram alert khi <b>job thất bại</b></li>
              <li>⚠️ Telegram cảnh báo khi <b>API credits &lt; 50</b></li>
              <li>📊 Báo cáo ngày tự động lúc <b>6:00 AM (UTC+7)</b></li>
              <li>🔄 Kiểm tra credits tự động mỗi <b>6 giờ</b></li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Reusable Components ─────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, color, bgColor }) {
  return (
    <div className="p-4 bg-[#0a1a17] border border-slate-700/50 shadow-lg rounded-2xl flex items-center gap-3">
      <div className={`p-3 rounded-xl ${bgColor}`}><Icon className={`w-5 h-5 ${color}`} /></div>
      <div>
        <p className="text-[10px] text-slate-400 font-medium uppercase tracking-wider">{label}</p>
        <p className="text-xl font-bold text-white">{value}</p>
      </div>
    </div>
  );
}

function MiniStatCard({ label, value, color = 'text-white' }) {
  return (
    <div className="p-3 bg-[#012622]/50 border border-slate-700/30 rounded-xl text-center">
      <p className="text-[10px] text-slate-400 uppercase">{label}</p>
      <p className={`text-lg font-bold ${color}`}>{value}</p>
    </div>
  );
}

function CreditCard({ provider, credits, threshold, apiKey, isProxy }) {
  const isLow = credits !== "N/A" && credits < threshold;
  return (
    <div className={`relative overflow-hidden p-5 rounded-2xl border shadow-lg transition-all duration-300 ${isLow ? 'bg-red-500/5 border-red-500/30' : 'bg-[#0a1a17] border-slate-700/50'}`}>
      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">{provider} {isProxy ? 'Proxy' : 'Provider'}</h4>
      <div className="flex items-end gap-3">
        <span className={`text-4xl font-bold tracking-tighter ${isLow ? 'text-red-400' : 'text-white'}`}>
          {typeof credits === 'number' ? credits.toLocaleString() : credits}
        </span>
        <span className="text-slate-500 pb-1 text-sm">{isProxy ? 'status' : 'credits'}</span>
      </div>
      {apiKey && (
        <div className="mt-3 p-2.5 bg-[#012622] border border-slate-700/30 rounded-xl flex items-center justify-between">
          <div className="overflow-hidden">
            <p className="text-[9px] text-slate-500 uppercase font-bold mb-0.5">Key</p>
            <p className="text-xs text-slate-300 font-mono truncate">{apiKey.length > 30 ? apiKey.substring(0, 30) + '...' : apiKey}</p>
          </div>
          <button onClick={() => { navigator.clipboard.writeText(apiKey); }} className="p-1.5 text-slate-500 hover:text-amber-400 transition-colors cursor-pointer" title="Copy">
            <Copy className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
      {isLow && !isProxy && <div className="mt-3 flex items-center gap-2 text-xs font-bold text-red-400 bg-red-400/10 px-3 py-2 rounded-xl"><AlertTriangle className="w-4 h-4" /> Credits thấp! Cần nạp thêm.</div>}
      {!isLow && !isProxy && <div className="mt-3 flex items-center gap-2 text-xs text-emerald-400"><CheckCircle className="w-4 h-4" /> Ổn định</div>}
      {isProxy && apiKey && apiKey !== "Not Set" && <div className="mt-3 flex items-center gap-2 text-xs text-emerald-400"><CheckCircle className="w-4 h-4" /> Đã cấu hình</div>}
    </div>
  );
}

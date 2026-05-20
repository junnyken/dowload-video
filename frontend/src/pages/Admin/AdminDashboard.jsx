import { useState, useEffect, useCallback } from 'react';
import {
  Shield, CheckCircle, XCircle, AlertTriangle, Users, Download, Activity,
  ToggleLeft, ToggleRight, Key, Copy, TrendingUp, BarChart3, Bell,
  RefreshCw, Zap, Clock, Loader2, Send, HardDrive, Server, Wifi,
  WifiOff, Database, Globe, Flag, Bug, BarChart2
} from 'lucide-react';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1/admin`;

const adminFetch = (path, opts = {}) =>
  fetch(`${API}${path}`, {
    ...opts,
    headers: { 'X-Admin-Token': sessionStorage.getItem('admin_token') || '', ...opts.headers },
  });

// ── SVG Circular Gauge ──────────────────────────────────────────────
function Gauge({ pct = 0, label, sublabel, colorClass = 'text-amber-400', strokeColor = '#f59e0b' }) {
  const r = 36;
  const circ = 2 * Math.PI * r;
  const dash = ((Math.min(pct, 100) / 100) * circ).toFixed(1);
  const color = pct >= 85 ? '#ef4444' : pct >= 60 ? '#f59e0b' : strokeColor;
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="96" height="96" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r={r} fill="none" stroke="#1e3a35" strokeWidth="8" />
        <circle
          cx="48" cy="48" r={r} fill="none"
          stroke={color} strokeWidth="8"
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 48 48)"
          style={{ transition: 'stroke-dasharray 0.6s ease' }}
        />
        <text x="48" y="48" textAnchor="middle" dominantBaseline="central" fill="white" fontSize="15" fontWeight="bold">
          {Math.round(pct)}%
        </text>
      </svg>
      <p className="text-xs font-semibold text-white">{label}</p>
      {sublabel && <p className="text-[10px] text-slate-400">{sublabel}</p>}
    </div>
  );
}

// ── Status Dot ──────────────────────────────────────────────────────
function StatusDot({ ok, label, detail }) {
  return (
    <div className="flex items-center gap-3 py-2 border-b border-slate-700/30 last:border-0">
      <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${ok ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : 'bg-red-400 shadow-[0_0_6px_#f87171]'}`} />
      <span className="text-sm text-slate-200 flex-1">{label}</span>
      {detail && <span className="text-xs text-slate-400">{detail}</span>}
    </div>
  );
}

// ── Pure CSS Mini Bar Chart ─────────────────────────────────────────
function MiniBarChart({ data, labelKey = 'date', valueKey = 'total', successKey = 'success', failedKey = 'failed', height = 200 }) {
  if (!data || data.length === 0) return <p className="text-slate-500 text-sm">Không có dữ liệu</p>;
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
            <div className="absolute -top-10 left-1/2 -translate-x-1/2 bg-[#0a0a0a] text-white text-[10px] px-2 py-1 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-10 pointer-events-none border border-slate-700">
              {dateLabel}: {total} ({success}✓ {failed}✗)
            </div>
            <div className="w-full flex flex-col justify-end" style={{ height: height - 24 }}>
              <div className="w-full rounded-t-sm bg-red-500/80 transition-all duration-500" style={{ height: `${failedH}%`, minHeight: failed > 0 ? 2 : 0 }} />
              <div className="w-full bg-emerald-500/80 transition-all duration-500 rounded-t-sm" style={{ height: `${successH}%`, minHeight: success > 0 ? 2 : 0 }} />
            </div>
            <span className="text-[9px] text-slate-500 truncate w-full text-center">{dateLabel}</span>
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
      <span className="text-xs text-slate-300 w-20 truncate">{platform}</span>
      <div className="flex-1 bg-[#012622] rounded-full h-3 overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${colors[platform] || 'bg-slate-400'}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-bold text-white w-10 text-right">{count}</span>
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
    <div className={`flex items-center gap-3 px-3 py-2 rounded-xl border transition-colors ${isProcessing ? 'bg-amber-500/5 border-amber-500/20' : 'bg-[#012622]/50 border-slate-700/30'}`}>
      {isProcessing ? <Loader2 className="w-4 h-4 text-amber-400 animate-spin flex-shrink-0" /> : <Clock className="w-4 h-4 text-slate-500 flex-shrink-0" />}
      <span className="text-xs text-slate-400 truncate flex-1">{shortUrl}</span>
      <span className="text-[10px] text-slate-500">{time}</span>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═════════════════════════════════════════════════════════════════════

export default function AdminDashboard() {
  const [stats, setStats]           = useState(null);
  const [analytics, setAnalytics]   = useState(null);
  const [activeJobs, setActiveJobs] = useState(null);
  const [errors, setErrors]         = useState(null);
  const [users, setUsers]           = useState(null);
  const [health, setHealth]         = useState(null);
  const [loading, setLoading]       = useState(true);
  const [activeTab, setActiveTab]   = useState('overview');
  const [chartDays, setChartDays]   = useState(7);
  const [testingNotif, setTestingNotif] = useState(false);
  const [notifResult, setNotifResult]   = useState(null);

  const fetchStats = useCallback(async () => {
    try {
      const res = await adminFetch('/stats');
      const data = await res.json();
      if (data.success) setStats(data);
    } catch (e) { console.error('Stats fetch failed', e); }
    finally { setLoading(false); }
  }, []);

  const fetchAnalytics = useCallback(async () => {
    try {
      const res = await adminFetch(`/analytics?days=${chartDays}`);
      const data = await res.json();
      if (data.success) setAnalytics(data);
    } catch (e) { console.error('Analytics fetch failed', e); }
  }, [chartDays]);

  const fetchActiveJobs = useCallback(async () => {
    try {
      const res = await adminFetch('/active-jobs');
      const data = await res.json();
      if (data.success) setActiveJobs(data);
    } catch (e) { console.error('Active jobs fetch failed', e); }
  }, []);

  const fetchErrors = useCallback(async () => {
    try {
      const res = await adminFetch('/errors');
      const data = await res.json();
      if (data.success) setErrors(data);
    } catch (e) { console.error('Errors fetch failed', e); }
  }, []);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await adminFetch('/users');
      const data = await res.json();
      if (data.success) setUsers(data);
    } catch (e) { console.error('Users fetch failed', e); }
  }, []);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await adminFetch('/system-health');
      const data = await res.json();
      if (data.success) setHealth(data);
    } catch (e) { console.error('Health fetch failed', e); }
  }, []);

  useEffect(() => {
    fetchStats();
    fetchAnalytics();
    fetchActiveJobs();
    const intv = setInterval(() => { fetchStats(); fetchActiveJobs(); }, 10000);
    return () => clearInterval(intv);
  }, [fetchStats, fetchAnalytics, fetchActiveJobs]);

  useEffect(() => { fetchAnalytics(); }, [chartDays, fetchAnalytics]);

  // Lazy-load tab data on first visit
  useEffect(() => {
    if (activeTab === 'errors' && !errors) fetchErrors();
    if (activeTab === 'users' && !users) fetchUsers();
    if (activeTab === 'health' && !health) fetchHealth();
  }, [activeTab, errors, users, health, fetchErrors, fetchUsers, fetchHealth]);

  const toggleUserPlan = async (user_id, currentPlan) => {
    const newPlan = currentPlan === 'pro' ? 'free' : 'pro';
    try {
      await adminFetch('/update-user', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id, plan: newPlan }),
      });
      fetchStats();
    } catch { alert('Failed to update user'); }
  };

  const sendTestNotification = async () => {
    setTestingNotif(true);
    setNotifResult(null);
    try {
      const res = await adminFetch('/send-test-notification', { method: 'POST' });
      const data = await res.json();
      setNotifResult(data.success ? '✅ Đã gửi!' : '❌ Thất bại');
    } catch { setNotifResult('❌ Lỗi kết nối'); }
    finally { setTestingNotif(false); setTimeout(() => setNotifResult(null), 4000); }
  };

  const refreshAll = () => {
    fetchStats(); fetchAnalytics(); fetchActiveJobs();
    if (activeTab === 'errors') fetchErrors();
    if (activeTab === 'users') fetchUsers();
    if (activeTab === 'health') fetchHealth();
  };

  if (loading || !stats) {
    return <div className="p-8 text-slate-400 flex gap-2"><Activity className="animate-pulse" /> Đang tải Admin Center...</div>;
  }

  const scraperAPIcredits = stats.providers?.ScraperAPI ?? stats.providers?.scraperapi ?? 0;
  const successRate = analytics?.summary?.success_rate ?? 0;
  const totalActive = (activeJobs?.processing_count || 0) + (activeJobs?.pending_count || 0);

  const tabs = [
    { id: 'overview', label: 'Tổng Quan',       icon: BarChart3 },
    { id: 'analytics', label: 'Phân Tích',       icon: TrendingUp },
    { id: 'errors',    label: 'Lỗi & Cảnh Báo',  icon: Bug },
    { id: 'users',     label: 'Người Dùng',       icon: Users },
    { id: 'health',    label: 'Hệ Thống',         icon: Server },
    { id: 'apikeys',   label: 'API & Credits',    icon: Key },
    { id: 'cookies',   label: 'Cookie Pool',      icon: Key },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ── Header ── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Shield className="w-6 h-6 text-red-400" />
            <h2 className="text-2xl font-bold text-white tracking-tight">Admin Center</h2>
          </div>
          <p className="text-sm text-slate-400 ml-9">Quản lý hệ thống, theo dõi hiệu suất & cảnh báo.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={sendTestNotification}
            disabled={testingNotif}
            className="flex items-center gap-2 px-3 py-2 text-xs font-bold rounded-xl bg-[#0088cc]/10 text-[#0088cc] border border-[#0088cc]/30 hover:bg-[#0088cc]/20 transition-colors cursor-pointer disabled:opacity-50"
          >
            {testingNotif ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            Test Telegram
          </button>
          {notifResult && <span className="text-xs font-bold animate-in fade-in">{notifResult}</span>}
          <button onClick={refreshAll} className="p-2 rounded-xl bg-[#0a1a17] border border-slate-700/50 text-slate-400 hover:text-white transition-colors cursor-pointer">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* ── Tab Navigation ── */}
      <div className="flex flex-wrap bg-[#012622]/50 rounded-xl p-1 border border-slate-700/50 gap-1 backdrop-blur-md">
        {tabs.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold rounded-lg transition-colors cursor-pointer ${
                activeTab === tab.id
                  ? 'bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md'
                  : 'text-slate-300 hover:text-white hover:bg-white/10'
              }`}
            >
              <Icon className="w-4 h-4" /> {tab.label}
            </button>
          );
        })}
      </div>

      {/* ══ TAB: OVERVIEW ══ */}
      {activeTab === 'overview' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard icon={Download} label="Downloads Hôm Nay" value={stats.total_downloads_today} color="text-amber-400" bgColor="bg-amber-400/10" />
            <StatCard icon={Users} label="Người Dùng" value={stats.total_users} color="text-cyan-400" bgColor="bg-cyan-400/10" />
            <StatCard icon={TrendingUp} label="Tỷ Lệ Thành Công" value={`${successRate}%`}
              color={successRate >= 90 ? 'text-emerald-400' : successRate >= 70 ? 'text-amber-400' : 'text-red-400'}
              bgColor={successRate >= 90 ? 'bg-emerald-400/10' : successRate >= 70 ? 'bg-amber-400/10' : 'bg-red-400/10'} />
            <StatCard icon={Zap} label="Jobs Đang Chạy" value={totalActive} color="text-violet-400" bgColor="bg-violet-400/10" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* User Table */}
            <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
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
                          <button onClick={() => toggleUserPlan(u.user_id, u.plan || 'free')}
                            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg font-bold text-[10px] transition-colors cursor-pointer ${(u.plan || 'free') === 'pro' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700/50 text-slate-400 hover:text-white'}`}>
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

            {/* Recent Error Log */}
            <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl flex flex-col">
              <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-amber-400" /> Lỗi Gần Đây</h3>
              <div className="flex-1 overflow-auto max-h-[350px] space-y-2 bg-[#012622] p-3 rounded-xl font-mono text-xs text-slate-400 border border-slate-700/30">
                {stats.failed_jobs.map(job => (
                  <div key={job.id} className="border-b border-slate-700/30 pb-2 last:border-0">
                    <span className="text-red-400 mr-2">[{job.created_at ? new Date(job.created_at).toLocaleTimeString('vi-VN') : '?'}]</span>
                    <span className="text-slate-300">{(job.original_url || '').slice(0, 60)}</span>
                    <p className="mt-1 text-red-400/70 break-words">{job.error_message}</p>
                  </div>
                ))}
                {stats.failed_jobs.length === 0 && <p className="text-emerald-400 flex items-center gap-2"><CheckCircle className="w-4 h-4" /> Không có lỗi. Hệ thống ổn.</p>}
              </div>
            </div>
          </div>

          {totalActive > 0 && (
            <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
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

      {/* ══ TAB: ANALYTICS ══ */}
      {activeTab === 'analytics' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold text-white flex items-center gap-2"><TrendingUp className="w-5 h-5 text-amber-400" /> Xu Hướng Download</h3>
            <div className="flex gap-1 bg-[#012622]/50 rounded-lg p-1 border border-slate-700/50">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => setChartDays(d)}
                  className={`px-3 py-1.5 text-xs font-bold rounded-md transition-colors cursor-pointer ${chartDays === d ? 'bg-amber-400 text-[#012622]' : 'text-slate-400 hover:text-white'}`}>
                  {d}D
                </button>
              ))}
            </div>
          </div>

          {analytics?.summary && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <MiniStatCard label="Tổng Jobs" value={analytics.summary.total_jobs} />
              <MiniStatCard label="Thành Công" value={analytics.summary.total_success} color="text-emerald-400" />
              <MiniStatCard label="Thất Bại" value={analytics.summary.total_failed} color="text-red-400" />
              <MiniStatCard label="TB/Ngày" value={analytics.summary.avg_daily} />
            </div>
          )}

          <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
            <p className="text-xs text-slate-400 mb-4">
              <span className="inline-block w-3 h-3 bg-emerald-500/80 rounded-sm mr-1 align-middle" /> Thành công
              <span className="inline-block w-3 h-3 bg-red-500/80 rounded-sm mr-1 ml-3 align-middle" /> Thất bại
            </p>
            <MiniBarChart data={analytics?.daily_stats || []} height={180} />
          </div>

          <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
            <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><BarChart3 className="w-4 h-4 text-cyan-400" /> Nền Tảng Phổ Biến</h3>
            {analytics?.platform_stats?.length > 0 ? (
              <div className="space-y-3">
                {analytics.platform_stats.filter(p => p.platform !== 'ZIP').slice(0, 8).map(p => (
                  <PlatformBar key={p.platform} platform={p.platform} count={p.count} maxCount={analytics.platform_stats[0]?.count || 1} />
                ))}
              </div>
            ) : <p className="text-xs text-slate-500">Chưa có dữ liệu.</p>}
          </div>
        </div>
      )}

      {/* ══ TAB: ERRORS ══ */}
      {activeTab === 'errors' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {!errors ? (
            <div className="flex items-center gap-2 text-slate-400"><Loader2 className="animate-spin w-4 h-4" /> Đang tải dữ liệu lỗi...</div>
          ) : (
            <>
              {/* 24h Summary */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <MiniStatCard label="Tổng Jobs 24h" value={errors.summary?.total_24h ?? 0} />
                <MiniStatCard label="Thất Bại 24h" value={errors.summary?.failed_24h ?? 0} color="text-red-400" />
                <MiniStatCard label="Tỷ Lệ Lỗi" value={`${errors.summary?.fail_rate_24h ?? 0}%`} color={(errors.summary?.fail_rate_24h ?? 0) > 20 ? 'text-red-400' : 'text-amber-400'} />
                <MiniStatCard label="Lỗi Phân Tích" value={errors.recent_errors?.length ?? 0} />
              </div>

              {/* Error Pattern Breakdown */}
              {errors.error_patterns && Object.keys(errors.error_patterns).length > 0 && (
                <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                  <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><Bug className="w-4 h-4 text-red-400" /> Phân Loại Lỗi</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {Object.entries(errors.error_patterns).map(([type, count]) => (
                      <div key={type} className="bg-[#012622] rounded-xl p-3 text-center border border-slate-700/30">
                        <p className="text-xl font-bold text-red-400">{count}</p>
                        <p className="text-[10px] text-slate-400 capitalize mt-1">{type}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Per-Platform Fail Rate */}
              {errors.platform_fail_rates?.length > 0 && (
                <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                  <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><BarChart2 className="w-4 h-4 text-amber-400" /> Tỷ Lệ Lỗi Theo Nền Tảng</h3>
                  <div className="space-y-3">
                    {errors.platform_fail_rates.map(p => {
                      const failPct = p.total > 0 ? Math.round((p.failed / p.total) * 100) : 0;
                      return (
                        <div key={p.platform} className="flex items-center gap-3">
                          <span className="text-xs text-slate-300 w-20 truncate">{p.platform}</span>
                          <div className="flex-1 bg-[#012622] rounded-full h-3 overflow-hidden">
                            <div className={`h-full rounded-full transition-all duration-700 ${failPct > 30 ? 'bg-red-500' : failPct > 10 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                              style={{ width: `${failPct}%` }} />
                          </div>
                          <span className="text-xs font-bold text-white w-14 text-right">{failPct}% ({p.failed}/{p.total})</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Error Feed */}
              <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-amber-400" /> Log Lỗi Gần Nhất</h3>
                <div className="space-y-2 max-h-[400px] overflow-auto font-mono text-xs">
                  {(errors.recent_errors || []).map(job => (
                    <div key={job.id} className="bg-[#012622] rounded-xl p-3 border border-red-500/10">
                      <div className="flex items-center gap-2 mb-1">
                        <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                        <span className="text-slate-300 truncate flex-1">{(job.original_url || '').slice(0, 70)}</span>
                        <span className="text-slate-500 text-[10px] flex-shrink-0">{job.created_at ? new Date(job.created_at).toLocaleString('vi-VN') : ''}</span>
                      </div>
                      <p className="text-red-400/80 pl-5 break-words">{job.error_message || 'Unknown error'}</p>
                    </div>
                  ))}
                  {(errors.recent_errors || []).length === 0 && (
                    <p className="text-emerald-400 flex items-center gap-2 p-3"><CheckCircle className="w-4 h-4" /> Không có lỗi gần đây. Hệ thống hoạt động tốt.</p>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ══ TAB: USERS ══ */}
      {activeTab === 'users' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {!users ? (
            <div className="flex items-center gap-2 text-slate-400"><Loader2 className="animate-spin w-4 h-4" /> Đang tải dữ liệu người dùng...</div>
          ) : (
            <>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                <MiniStatCard label="Tổng Người Dùng" value={users.total_users ?? 0} />
                <MiniStatCard label="Người Dùng Đáng Ngờ" value={users.flagged_users?.length ?? 0} color="text-red-400" />
                <MiniStatCard label="Batches 48h" value={users.total_batches_48h ?? 0} />
              </div>

              {/* Flagged Users */}
              {users.flagged_users?.length > 0 && (
                <div className="p-5 bg-[#0a1a17] border border-red-500/20 rounded-2xl">
                  <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2"><Flag className="w-4 h-4 text-red-400" /> Người Dùng Đáng Ngờ (≥50 DL/ngày)</h3>
                  <div className="space-y-2">
                    {users.flagged_users.map(u => (
                      <div key={u.user_id} className="flex items-center gap-3 bg-red-500/5 border border-red-500/20 rounded-xl px-4 py-3">
                        <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
                        <span className="text-sm text-slate-300 flex-1 truncate font-mono">{u.user_id}</span>
                        <span className="text-sm font-bold text-red-400">{u.downloads_today} DL hôm nay</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Top Users */}
              <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                <h3 className="text-sm font-bold mb-3 text-white flex items-center gap-2"><Users className="w-4 h-4 text-cyan-400" /> Top 20 Người Dùng Hôm Nay</h3>
                <div className="overflow-auto max-h-[400px]">
                  <table className="w-full text-sm text-left">
                    <thead className="bg-[#012622] sticky top-0 text-[10px] uppercase text-slate-400">
                      <tr>
                        <th className="px-3 py-2">#</th>
                        <th className="px-3 py-2">User ID</th>
                        <th className="px-3 py-2 text-right">DL Hôm Nay</th>
                        <th className="px-3 py-2 text-right">Plan</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700/30">
                      {(users.top_users || []).slice(0, 20).map((u, i) => (
                        <tr key={u.user_id} className={`hover:bg-white/5 transition-colors ${u.downloads_today >= 50 ? 'bg-red-500/5' : ''}`}>
                          <td className="px-3 py-2 text-slate-500 text-xs">{i + 1}</td>
                          <td className="px-3 py-2 text-slate-300 text-xs font-mono truncate max-w-[200px]">{u.user_id}</td>
                          <td className="px-3 py-2 text-right">
                            <span className={`text-xs font-bold ${u.downloads_today >= 50 ? 'text-red-400' : 'text-white'}`}>{u.downloads_today}</span>
                          </td>
                          <td className="px-3 py-2 text-right">
                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${(u.plan || 'free') === 'pro' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-700/50 text-slate-400'}`}>
                              {(u.plan || 'free').toUpperCase()}
                            </span>
                          </td>
                        </tr>
                      ))}
                      {(users.top_users || []).length === 0 && (
                        <tr><td colSpan="4" className="text-center py-6 text-slate-500 text-xs">Chưa có dữ liệu.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Batch Size Distribution */}
              {users.batch_distribution?.length > 0 && (
                <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                  <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><BarChart2 className="w-4 h-4 text-violet-400" /> Phân Bố Kích Thước Batch (48h)</h3>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    {users.batch_distribution.map(({ range, count }) => (
                      <div key={range} className="bg-[#012622] rounded-xl p-3 text-center border border-slate-700/30">
                        <p className="text-xl font-bold text-violet-400">{count}</p>
                        <p className="text-[10px] text-slate-400 mt-1">{range} URLs</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ══ TAB: SYSTEM HEALTH ══ */}
      {activeTab === 'health' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          {!health ? (
            <div className="flex items-center gap-2 text-slate-400"><Loader2 className="animate-spin w-4 h-4" /> Đang kiểm tra hệ thống...</div>
          ) : (
            <>
              {/* Disk + Redis Gauges */}
              <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                <h3 className="text-sm font-bold mb-6 text-white flex items-center gap-2"><HardDrive className="w-4 h-4 text-amber-400" /> Tài Nguyên Hệ Thống</h3>
                <div className="flex flex-wrap justify-around gap-6">
                  <Gauge
                    pct={health.disk?.used_pct ?? 0}
                    label="Disk"
                    sublabel={`${health.disk?.used_gb ?? '?'} / ${health.disk?.total_gb ?? '?'} GB`}
                    strokeColor="#f59e0b"
                  />
                  <Gauge
                    pct={health.redis?.used_pct ?? 0}
                    label="Redis RAM"
                    sublabel={`${health.redis?.used_mb ?? '?'} / ${health.redis?.max_mb ?? 256} MB`}
                    strokeColor="#818cf8"
                  />
                  <div className="flex flex-col items-center gap-2 justify-center">
                    <div className="bg-[#012622] rounded-2xl p-5 text-center border border-slate-700/30">
                      <p className="text-3xl font-bold text-amber-400">{health.redis?.celery_queue_depth ?? 0}</p>
                      <p className="text-xs text-slate-400 mt-1">Celery Queue</p>
                    </div>
                    <div className="bg-[#012622] rounded-2xl p-3 text-center border border-slate-700/30 w-full">
                      <p className="text-sm font-bold text-slate-200">{health.disk?.downloads_files ?? 0} files</p>
                      <p className="text-[10px] text-slate-400">Downloads ({health.disk?.downloads_size_mb ?? 0} MB)</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Service Status */}
              <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><Wifi className="w-4 h-4 text-emerald-400" /> Trạng Thái Dịch Vụ</h3>
                <div>
                  <StatusDot ok={health.services?.redis ?? false} label="Redis" detail={health.redis?.used_mb ? `${health.redis.used_mb} MB used` : ''} />
                  <StatusDot ok={health.services?.cobalt_api ?? false} label="Cobalt API" detail={health.services?.cobalt_latency_ms ? `${health.services.cobalt_latency_ms} ms` : 'timeout'} />
                  <StatusDot ok={health.services?.supabase ?? false} label="Supabase" detail={health.services?.supabase_latency_ms ? `${health.services.supabase_latency_ms} ms` : 'timeout'} />
                  <StatusDot ok={true} label="yt-dlp" detail={health.ytdlp_version ?? 'unknown'} />
                </div>
              </div>

              {/* Proxy Chain */}
              <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                <h3 className="text-sm font-bold mb-4 text-white flex items-center gap-2"><Globe className="w-4 h-4 text-blue-400" /> Cấu Hình Proxy</h3>
                {health.proxy ? (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div className={`rounded-xl p-3 border ${health.proxy.iproyal_configured ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-slate-800/50 border-slate-700/30'}`}>
                        <p className="text-[10px] text-slate-400 uppercase font-bold mb-1">IPRoyal Global</p>
                        <p className={`text-sm font-semibold ${health.proxy.iproyal_configured ? 'text-emerald-400' : 'text-slate-500'}`}>
                          {health.proxy.iproyal_configured ? '✓ Đã cấu hình' : '✗ Chưa cấu hình'}
                        </p>
                        <p className="text-[10px] text-slate-500 mt-1">TikTok / Instagram</p>
                      </div>
                      <div className={`rounded-xl p-3 border ${health.proxy.iproyal_cn_configured ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-slate-800/50 border-slate-700/30'}`}>
                        <p className="text-[10px] text-slate-400 uppercase font-bold mb-1">IPRoyal CN</p>
                        <p className={`text-sm font-semibold ${health.proxy.iproyal_cn_configured ? 'text-emerald-400' : 'text-slate-500'}`}>
                          {health.proxy.iproyal_cn_configured ? '✓ Đã cấu hình' : '✗ Chưa cấu hình'}
                        </p>
                        <p className="text-[10px] text-slate-500 mt-1">Douyin (CN IP)</p>
                      </div>
                    </div>
                    <div className={`rounded-xl p-3 border ${health.proxy.scraperapi_configured ? 'bg-blue-500/5 border-blue-500/20' : 'bg-slate-800/50 border-slate-700/30'}`}>
                      <p className="text-[10px] text-slate-400 uppercase font-bold mb-1">ScraperAPI (Fallback miễn phí)</p>
                      <p className={`text-sm font-semibold ${health.proxy.scraperapi_configured ? 'text-blue-400' : 'text-slate-500'}`}>
                        {health.proxy.scraperapi_configured ? `✓ Active ${health.proxy.scraperapi_proxy_active ? '— đang dùng làm proxy chính' : ''}` : '✗ Chưa cấu hình'}
                      </p>
                    </div>
                    <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2">
                      <div className="bg-[#012622] rounded-xl p-3 border border-slate-700/30">
                        <p className="text-[10px] text-slate-500 mb-1">TikTok/IG Route</p>
                        <p className="text-xs text-slate-300 font-mono break-all">{health.proxy.tiktok_proxy}</p>
                      </div>
                      <div className="bg-[#012622] rounded-xl p-3 border border-slate-700/30">
                        <p className="text-[10px] text-slate-500 mb-1">Douyin Route</p>
                        <p className="text-xs text-slate-300 font-mono break-all">{health.proxy.douyin_proxy}</p>
                      </div>
                    </div>
                  </div>
                ) : <p className="text-xs text-slate-500">Không có dữ liệu proxy.</p>}
              </div>
            </>
          )}
        </div>
      )}

      {/* ══ TAB: API & CREDITS ══ */}
      {activeTab === 'apikeys' && (
        <div className="space-y-6 animate-in fade-in duration-300">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <CreditCard provider="ScraperAPI" credits={scraperAPIcredits} threshold={10} apiKey={stats.api_keys?.ScraperAPI} />
            <CreditCard provider="IPRoyal" credits="N/A" threshold={0} apiKey={stats.api_keys?.IPRoyal} isProxy />
          </div>

          <div className="p-5 bg-[#0a1a17] border border-amber-500/20 rounded-2xl">
            <h3 className="text-sm font-bold mb-2 text-white flex items-center gap-2"><Bell className="w-4 h-4 text-amber-400" /> Cảnh Báo Tự Động</h3>
            <ul className="text-xs text-slate-400 space-y-1.5 ml-1">
              <li>📨 Telegram thông báo khi <b>batch download hoàn tất</b></li>
              <li>🚨 Telegram alert khi <b>job thất bại</b></li>
              <li>⚠️ Telegram cảnh báo khi <b>API credits &lt; 10</b></li>
              <li>📊 Báo cáo ngày tự động lúc <b>6:00 AM (UTC+7)</b></li>
              <li>🔄 Kiểm tra credits tự động mỗi <b>6 giờ</b></li>
            </ul>
          </div>
        </div>
      )}

      {activeTab === 'cookies' && <CookiePoolTab />}
    </div>
  );
}

// ── Cookie Pool Tab ─────────────────────────────────────────────────
const PLATFORMS = ['youtube', 'tiktok', 'facebook', 'instagram'];
const PLATFORM_COLORS = {
  youtube:   'text-red-400 border-red-500/30 bg-red-500/10',
  tiktok:    'text-pink-400 border-pink-500/30 bg-pink-500/10',
  facebook:  'text-blue-400 border-blue-500/30 bg-blue-500/10',
  instagram: 'text-purple-400 border-purple-500/30 bg-purple-500/10',
};

function CookiePoolTab() {
  const [activePlat, setActivePlat] = useState('youtube');
  const [poolStatus, setPoolStatus] = useState({});
  const [cookieList, setCookieList] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState('');
  const [dragging, setDragging] = useState(false);

  const fetchStatus = async () => {
    try {
      const r = await adminFetch('/cookies/status');
      const d = await r.json();
      if (d.success) setPoolStatus(d.pools || {});
    } catch {}
  };

  const fetchList = async (platform) => {
    try {
      const r = await adminFetch(`/cookies/list/${platform}`);
      const d = await r.json();
      if (d.success) setCookieList(d.cookies || []);
    } catch {}
  };

  useEffect(() => { fetchStatus(); }, []);
  useEffect(() => { setCookieList([]); fetchList(activePlat); }, [activePlat]);

  const detectPlatformFromFile = (filename) => {
    const name = filename.toLowerCase();
    if (name.includes('tiktok')) return 'tiktok';
    if (name.includes('youtube') || name.includes('google')) return 'youtube';
    if (name.includes('facebook') || name.includes('fb.com')) return 'facebook';
    if (name.includes('instagram')) return 'instagram';
    return null;
  };

  const uploadFile = async (file) => {
    if (!file) return;
    setUploading(true); setMsg('');
    try {
      // Auto-detect platform from filename
      const detected = detectPlatformFromFile(file.name);
      const targetPlat = detected || activePlat;
      if (detected && detected !== activePlat) {
        setActivePlat(detected);
        setMsg(`🔍 Tự động nhận diện: ${detected} từ tên file`);
      }

      const form = new FormData();
      form.append('platform', targetPlat);
      form.append('file', file);
      const r = await adminFetch('/cookies/upload', { method: 'POST', body: form });
      const d = await r.json();
      if (d.success) {
        setMsg(`✅ Đã thêm vào pool ${targetPlat} (tổng: ${d.pool_size} tài khoản)`);
        fetchStatus(); fetchList(targetPlat);
      } else {
        setMsg(`❌ Lỗi: ${d.detail || 'Unknown error'}`);
      }
    } catch (e) { setMsg(`❌ ${e.message}`); }
    finally { setUploading(false); }
  };

  const removeCookie = async (index) => {
    if (!confirm(`Xóa tài khoản #${index + 1} khỏi pool ${activePlat}?`)) return;
    try {
      await adminFetch('/cookies/remove', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform: activePlat, index }),
      });
      setMsg(`🗑 Đã xóa tài khoản #${index + 1}`);
      fetchStatus(); fetchList(activePlat);
    } catch (e) { setMsg(`❌ ${e.message}`); }
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    // Upload all dropped files sequentially (each auto-detects platform)
    files.reduce((p, file) => p.then(() => uploadFile(file)), Promise.resolve());
  };

  const platInfo = poolStatus[activePlat] || { total: 0, healthy: 0, blocked: 0 };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* Header */}
      <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
        <h3 className="text-base font-bold text-white mb-1 flex items-center gap-2">
          <Shield className="w-4 h-4 text-emerald-400" /> Cookie Pool — Tài Khoản Xoay Vòng
        </h3>
        <p className="text-xs text-slate-400">
          Mỗi Celery worker tự động lấy 1 cookie từ pool. Khi bị block → tự rotate sang tài khoản tiếp theo.
          Thêm 3-5 tài khoản mỗi platform để tránh bị chặn khi public.
        </p>
        {/* Summary pills */}
        <div className="flex flex-wrap gap-2 mt-3">
          {PLATFORMS.map(p => {
            const s = poolStatus[p] || { total: 0, healthy: 0 };
            return (
              <button key={p} onClick={() => setActivePlat(p)}
                className={`px-3 py-1 rounded-full text-xs font-bold border transition-all cursor-pointer ${
                  activePlat === p ? PLATFORM_COLORS[p] : 'text-slate-400 border-slate-700/50 bg-slate-800/50'
                }`}>
                {p.charAt(0).toUpperCase() + p.slice(1)}&nbsp;
                <span className={s.healthy > 0 ? 'text-emerald-400' : 'text-red-400'}>
                  {s.healthy}/{s.total}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Upload zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`relative border-2 border-dashed rounded-2xl p-8 text-center transition-all ${
          dragging ? 'border-emerald-400 bg-emerald-400/5' : 'border-slate-600 bg-[#0a1a17]'
        }`}
      >
        <Database className="w-8 h-8 text-slate-500 mx-auto mb-3" />
        <p className="text-sm font-semibold text-white mb-1">
          Thêm cookies.txt cho <span className={`capitalize font-bold ${PLATFORM_COLORS[activePlat].split(' ')[0]}`}>{activePlat}</span>
        </p>
        <p className="text-xs text-slate-400 mb-4">Kéo thả nhiều file cùng lúc — tự nhận diện platform từ tên file</p>
        <label className="cursor-pointer">
          <span className="px-4 py-2 bg-emerald-600/20 border border-emerald-500/40 text-emerald-400 text-sm font-bold rounded-xl hover:bg-emerald-600/30 transition-colors">
            {uploading ? '⏳ Đang upload...' : '📂 Chọn file cookies.txt'}
          </span>
          <input
            type="file"
            accept=".txt"
            multiple
            className="hidden"
            disabled={uploading}
            onChange={e => {
              const files = Array.from(e.target.files);
              files.reduce((p, file) => p.then(() => uploadFile(file)), Promise.resolve());
              e.target.value = '';
            }}
          />
        </label>
        <p className="text-[10px] text-slate-500 mt-3">
          Tên file chứa "youtube", "tiktok", "facebook", "instagram" → tự vào đúng pool
        </p>
      </div>

      {msg && (
        <div className={`px-4 py-3 rounded-xl text-sm font-medium ${
          msg.startsWith('✅') ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300' : 'bg-red-500/10 border border-red-500/30 text-red-300'
        }`}>{msg}</div>
      )}

      {/* Cookie list */}
      <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
        <div className="flex items-center justify-between mb-4">
          <h4 className="text-sm font-bold text-white capitalize flex items-center gap-2">
            <span className={`px-2 py-0.5 rounded-full text-xs ${PLATFORM_COLORS[activePlat]}`}>{activePlat}</span>
            Pool — {platInfo.healthy} healthy · {platInfo.blocked} blocked · {platInfo.total} total
          </h4>
          <button onClick={() => fetchList(activePlat)} className="text-xs text-slate-400 hover:text-white cursor-pointer transition-colors">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {cookieList.length === 0 ? (
          <div className="text-center py-8 text-slate-500 text-sm">
            Chưa có tài khoản nào trong pool {activePlat}.<br />
            Upload cookies.txt ở trên để bắt đầu.
          </div>
        ) : (
          <div className="space-y-2">
            {cookieList.map((item, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-2.5 bg-[#012622]/60 rounded-xl border border-slate-700/30">
                <div className="flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    item.status === 'healthy' ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : 'bg-red-400 shadow-[0_0_6px_#f87171]'
                  }`} />
                  <div>
                    <p className="text-xs font-mono text-slate-300">Tài khoản #{item.index + 1} · <span className="text-slate-500">{item.hash}</span></p>
                    {item.status === 'blocked' && item.blocked_ttl_s > 0 && (
                      <p className="text-[10px] text-red-400">Blocked · tự mở lại sau {Math.ceil(item.blocked_ttl_s / 60)} phút</p>
                    )}
                    {item.status === 'healthy' && <p className="text-[10px] text-emerald-400">Đang hoạt động</p>}
                  </div>
                </div>
                <button
                  onClick={() => removeCookie(item.index)}
                  className="text-xs text-red-400 hover:text-red-300 cursor-pointer transition-colors px-2 py-1 rounded hover:bg-red-500/10"
                >
                  Xóa
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* How-to guide */}
      <div className="p-4 bg-amber-500/5 border border-amber-500/20 rounded-2xl">
        <p className="text-xs font-bold text-amber-400 mb-2">📖 Hướng dẫn thêm tài khoản vào pool</p>
        <ol className="text-xs text-slate-400 space-y-1 list-decimal list-inside">
          <li>Tạo tài khoản Gmail throwaway (dùng số điện thoại, tối đa 4 tài khoản/số)</li>
          <li>Đăng nhập YouTube/TikTok/Facebook trong Chrome</li>
          <li>Cài extension <b className="text-white">"Get cookies.txt LOCALLY"</b> (Chrome Web Store)</li>
          <li>Mở tab youtube.com → click extension → <b className="text-white">Export cookies.txt</b></li>
          <li>Upload file vào ô trên — xong! Worker tự rotate giữa các tài khoản</li>
        </ol>
      </div>

      {/* Throwaway Accounts Log */}
      <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
        <div className="flex items-center justify-between mb-4">
          <h4 className="text-sm font-bold text-white flex items-center gap-2">
            <Users className="w-4 h-4 text-violet-400" /> Throwaway Accounts Log
          </h4>
          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded-full bg-slate-700/50 text-slate-400 border border-slate-700/50">
            Coming Soon
          </span>
        </div>
        <div className="flex flex-col items-center justify-center py-10 gap-3 bg-[#012622]/50 rounded-xl border border-dashed border-slate-700/40">
          <div className="w-12 h-12 rounded-2xl bg-violet-500/10 border border-violet-500/20 flex items-center justify-center">
            <Users className="w-6 h-6 text-violet-400/60" />
          </div>
          <div className="text-center">
            <p className="text-sm font-semibold text-slate-300">Chức năng đang phát triển</p>
            <p className="text-xs text-slate-500 mt-1 max-w-xs">
              Log danh sách tài khoản throwaway đã sử dụng, trạng thái sống/chết, và lịch sử rotate.
              API endpoint <code className="text-violet-400 bg-violet-500/10 px-1 rounded">/api/v1/admin/throwaway/accounts</code> chưa sẵn sàng.
            </p>
          </div>
        </div>
      </div>
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
  const isLow = credits !== 'N/A' && credits < threshold;
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
          <button onClick={() => navigator.clipboard.writeText(apiKey)} className="p-1.5 text-slate-500 hover:text-amber-400 transition-colors cursor-pointer" title="Copy">
            <Copy className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
      {isLow && !isProxy && <div className="mt-3 flex items-center gap-2 text-xs font-bold text-red-400 bg-red-400/10 px-3 py-2 rounded-xl"><AlertTriangle className="w-4 h-4" /> Credits thấp! Cần nạp thêm.</div>}
      {!isLow && !isProxy && <div className="mt-3 flex items-center gap-2 text-xs text-emerald-400"><CheckCircle className="w-4 h-4" /> Ổn định</div>}
      {isProxy && apiKey && apiKey !== 'Not Set' && <div className="mt-3 flex items-center gap-2 text-xs text-emerald-400"><CheckCircle className="w-4 h-4" /> Đã cấu hình</div>}
    </div>
  );
}

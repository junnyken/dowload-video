import { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { BarChart2, Download, Globe, TrendingUp, Calendar, Crown, ChevronDown } from 'lucide-react';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

// ── SVG Bar Chart (no external lib) ─────────────────────────────────────────
function BarChart({ data, valueKey, color = '#FBBF24', height = 120 }) {
  const max = Math.max(...data.map(d => d[valueKey] || 0), 1);
  const w = 100 / data.length;
  return (
    <svg viewBox={`0 0 100 ${height}`} className="w-full" preserveAspectRatio="none">
      {data.map((d, i) => {
        const barH = ((d[valueKey] || 0) / max) * (height - 20);
        return (
          <rect
            key={i}
            x={i * w + w * 0.1}
            y={height - 20 - barH}
            width={w * 0.8}
            height={barH}
            fill={color}
            fillOpacity={0.7}
            rx={1}
          />
        );
      })}
    </svg>
  );
}

// ── Stat Card ────────────────────────────────────────────────────────────────
function StatCard({ icon, label, value, sub }) {
  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-4 space-y-1">
      <div className="flex items-center gap-2 text-slate-400 text-xs mb-2">
        {icon}
        <span>{label}</span>
      </div>
      <p className="text-white font-bold text-2xl leading-none">{value ?? '—'}</p>
      {sub && <p className="text-slate-500 text-xs">{sub}</p>}
    </div>
  );
}

// ── Platform bar ─────────────────────────────────────────────────────────────
function PlatformBar({ label, count, total, color }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-300 font-medium">{label}</span>
        <span className="text-slate-400">{count} ({pct}%)</span>
      </div>
      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color || '#FBBF24' }} />
      </div>
    </div>
  );
}

const PLATFORM_COLORS = {
  youtube:  '#EF4444',
  spotify:  '#22C55E',
  tiktok:   '#94A3B8',
  threads:  '#A78BFA',
  other:    '#64748B',
};

// ── Main component ────────────────────────────────────────────────────────────
export default function AnalyticsPage() {
  const { session } = useAuth();
  const apiBase = localStorage.getItem('vg_api_base') || '';

  const [range, setRange]           = useState(30);
  const [analytics, setAnalytics]   = useState(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState('');
  const [notPro, setNotPro]         = useState(false);

  const fetchAnalytics = async (r) => {
    setLoading(true);
    setError('');
    setNotPro(false);
    try {
      const res = await fetch(`${apiBase}${API}/user/analytics?range=${r}`, {
        headers: { Authorization: `Bearer ${session?.access_token}` },
      });
      if (res.status === 403) { setNotPro(true); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAnalytics(await res.json());
    } catch (err) {
      setError(err.message || 'Không tải được dữ liệu analytics.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (session) fetchAnalytics(range); }, [session, range]);

  const exportCSV = () => {
    const rows = [['Date', 'Downloads', 'MB']];
    (analytics?.daily || []).forEach(d => rows.push([d.date, d.downloads, d.mb]));
    const csv = rows.map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `vidgrab-analytics-${range}d.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ── Upgrade prompt (403) ────────────────────────────────────────────────
  if (notPro) {
    return (
      <div className="flex flex-col items-center justify-center py-20 space-y-4 text-center">
        <div className="w-16 h-16 rounded-2xl bg-[#FBBF24]/10 border border-[#FBBF24]/20 flex items-center justify-center">
          <Crown className="w-8 h-8 text-[#FBBF24]" />
        </div>
        <div>
          <p className="text-white font-bold text-lg">Analytics — tính năng Pro</p>
          <p className="text-slate-400 text-sm mt-1">Nâng cấp Pro để xem analytics chi tiết: lượt tải theo ngày, platform, dung lượng...</p>
        </div>
        <button className="px-5 py-2.5 rounded-lg bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold hover:opacity-90 transition">
          Nâng cấp Pro →
        </button>
      </div>
    );
  }

  // ── Derived stats ────────────────────────────────────────────────────────
  const totalDownloads = analytics?.total_downloads ?? 0;
  const totalMb        = analytics?.total_mb != null ? analytics.total_mb.toFixed(1) : '—';
  const topPlatform    = analytics?.top_platform || '—';
  const avgPerDay      = analytics?.daily?.length
    ? (totalDownloads / analytics.daily.length).toFixed(1)
    : '—';

  const daily = analytics?.daily || [];
  const platforms = analytics?.platforms || [];
  const platformTotal = platforms.reduce((s, p) => s + (p.count || 0), 0);

  // Date labels every 7 items
  const dateLabels = daily.map((d, i) => (i % 7 === 0 ? d.date?.slice(5) : ''));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-slate-800 flex items-center justify-center">
            <BarChart2 className="w-5 h-5 text-[#FBBF24]" />
          </div>
          <div>
            <h1 className="text-white font-bold text-lg">Analytics</h1>
            <p className="text-slate-400 text-xs">Thống kê lượt tải theo tài khoản</p>
          </div>
        </div>

        {/* Range selector */}
        <div className="flex items-center gap-1 bg-slate-800/50 rounded-lg p-1 border border-slate-700/50">
          {[7, 30, 90].map(r => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-colors ${
                range === r
                  ? 'bg-[#FBBF24] text-[#012622]'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {r}D
            </button>
          ))}
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-16">
          <div className="w-6 h-6 border-2 border-[#FBBF24] border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Content */}
      {!loading && !error && analytics && (
        <>
          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard
              icon={<Download className="w-4 h-4" />}
              label="Tổng lượt tải"
              value={totalDownloads}
              sub={`trong ${range} ngày`}
            />
            <StatCard
              icon={<TrendingUp className="w-4 h-4" />}
              label="Dung lượng (MB)"
              value={totalMb}
              sub="tổng cộng"
            />
            <StatCard
              icon={<Globe className="w-4 h-4" />}
              label="Platform phổ biến"
              value={topPlatform}
              sub="dùng nhiều nhất"
            />
            <StatCard
              icon={<Calendar className="w-4 h-4" />}
              label="Trung bình/ngày"
              value={avgPerDay}
              sub="lượt tải"
            />
          </div>

          {/* Daily bar chart */}
          {daily.length > 0 && (
            <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-white font-semibold text-sm">Lượt tải theo ngày</p>
                <button
                  onClick={exportCSV}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700/50 text-slate-300 hover:bg-slate-700 text-xs font-bold transition-colors"
                >
                  <Download className="w-3.5 h-3.5" />
                  Export CSV
                </button>
              </div>

              <div className="relative">
                <BarChart data={daily} valueKey="downloads" color="#FBBF24" height={120} />
                {/* Date label row */}
                <div className="flex mt-1" style={{ overflowX: 'hidden' }}>
                  {daily.map((d, i) => (
                    <div
                      key={i}
                      className="text-slate-500 text-[9px] text-center overflow-hidden"
                      style={{ width: `${100 / daily.length}%`, flexShrink: 0 }}
                    >
                      {i % 7 === 0 ? (d.date?.slice(5) || '') : ''}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Platform breakdown */}
          {platforms.length > 0 && (
            <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-4 space-y-3">
              <p className="text-white font-semibold text-sm">Theo platform</p>
              <div className="space-y-3">
                {platforms.map((p, i) => (
                  <PlatformBar
                    key={i}
                    label={p.platform || 'Other'}
                    count={p.count || 0}
                    total={platformTotal}
                    color={PLATFORM_COLORS[(p.platform || '').toLowerCase()] || PLATFORM_COLORS.other}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Empty daily */}
          {daily.length === 0 && (
            <div className="text-center py-10 text-slate-400 text-sm">
              Chưa có dữ liệu trong {range} ngày qua.
            </div>
          )}
        </>
      )}
    </div>
  );
}

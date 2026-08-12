import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Activity, AlertTriangle, Clock, Cpu, HardDrive,
  Layers, Loader2, Pause, Play, RefreshCw, RotateCcw, Server,
  Zap, ChevronUp, ChevronDown, Minus,
} from 'lucide-react';

const API_BASE = `${import.meta.env.VITE_API_URL || ''}/api/v1/intelligence`;

function makeApiFetch(adminToken) {
  return (path, opts = {}) =>
    fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${adminToken}`,
        ...opts.headers,
      },
    });
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtWait(seconds) {
  if (seconds == null || seconds < 0) return '—';
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  return `~${Math.round(seconds / 60)} phút`;
}

function diskColor(pct) {
  if (pct == null) return 'text-slate-400';
  if (pct > 85) return 'text-red-400';
  if (pct > 70) return 'text-amber-400';
  return 'text-emerald-400';
}

function diskBg(pct) {
  if (pct == null) return 'bg-slate-600';
  if (pct > 85) return 'bg-red-500';
  if (pct > 70) return 'bg-amber-400';
  return 'bg-emerald-500';
}

function providerColor(level) {
  if (!level) return 'text-slate-400';
  const l = level.toLowerCase();
  if (l === 'high') return 'text-red-400';
  if (l === 'elevated') return 'text-amber-400';
  return 'text-emerald-400';
}

function providerBadge(level) {
  if (!level) return 'bg-slate-700 text-slate-300';
  const l = level.toLowerCase();
  if (l === 'high') return 'bg-red-900/60 text-red-300 border border-red-700';
  if (l === 'elevated') return 'bg-amber-900/60 text-amber-300 border border-amber-700';
  return 'bg-emerald-900/60 text-emerald-300 border border-emerald-700';
}

function TrendIcon({ value, prev }) {
  if (prev == null || value == null) return <Minus size={14} className="text-slate-500" />;
  if (value > prev) return <ChevronUp size={14} className="text-red-400" />;
  if (value < prev) return <ChevronDown size={14} className="text-emerald-400" />;
  return <Minus size={14} className="text-slate-500" />;
}

// ── Priority bar ─────────────────────────────────────────────────────────────

const PRIORITY_LEVELS = [
  { key: 'interactive', label: 'Interactive', color: 'bg-violet-500' },
  { key: 'redownload',  label: 'Redownload',  color: 'bg-blue-500' },
  { key: 'batch',       label: 'Batch',       color: 'bg-cyan-500' },
  { key: 'scheduled',   label: 'Scheduled',   color: 'bg-amber-400' },
  { key: 'maintenance', label: 'Maintenance', color: 'bg-slate-500' },
];

function PriorityBar({ breakdown }) {
  const [hovered, setHovered] = useState(null);
  if (!breakdown) return null;

  const total = PRIORITY_LEVELS.reduce((s, p) => s + (breakdown[p.key] || 0), 0);
  if (total === 0) {
    return (
      <div className="flex items-center justify-center h-6 rounded-lg bg-slate-800 text-slate-500 text-xs">
        Hàng đợi trống
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex h-6 rounded-lg overflow-hidden gap-px">
        {PRIORITY_LEVELS.map(({ key, color }) => {
          const count = breakdown[key] || 0;
          if (count === 0) return null;
          const pct = ((count / total) * 100).toFixed(1);
          return (
            <div
              key={key}
              className={`${color} relative cursor-pointer transition-all duration-200 ${hovered === key ? 'opacity-100 brightness-125' : 'opacity-80'}`}
              style={{ width: `${pct}%` }}
              onMouseEnter={() => setHovered(key)}
              onMouseLeave={() => setHovered(null)}
              title={`${key}: ${count} job (${pct}%)`}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-3">
        {PRIORITY_LEVELS.map(({ key, label, color }) => {
          const count = breakdown[key] || 0;
          return (
            <div
              key={key}
              className={`flex items-center gap-1.5 cursor-pointer transition-opacity ${hovered && hovered !== key ? 'opacity-40' : 'opacity-100'}`}
              onMouseEnter={() => setHovered(key)}
              onMouseLeave={() => setHovered(null)}
            >
              <span className={`inline-block w-2.5 h-2.5 rounded-sm ${color}`} />
              <span className="text-xs text-slate-400">{label}</span>
              <span className="text-xs font-semibold text-slate-200">{count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Stat card ────────────────────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, sub, colorClass = 'text-slate-200', iconColor = 'text-slate-400', trend }) {
  return (
    <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 flex flex-col gap-1 min-w-0">
      <div className="flex items-center gap-2 text-slate-400 text-xs font-medium mb-1">
        <Icon size={14} className={iconColor} />
        <span>{label}</span>
      </div>
      <div className={`flex items-end gap-1 ${colorClass}`}>
        <span className="text-2xl font-bold leading-none tabular-nums">{value ?? '—'}</span>
        {trend}
      </div>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Auto-tune chip ───────────────────────────────────────────────────────────

function TuneChip({ label, value }) {
  return (
    <div className="flex flex-col items-center bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 gap-0.5 min-w-[90px]">
      <span className="text-slate-500 text-[10px] font-medium uppercase tracking-wide">{label}</span>
      <span className="text-slate-100 text-sm font-bold tabular-nums">{value ?? '—'}</span>
    </div>
  );
}

// ── Disk bar ─────────────────────────────────────────────────────────────────

function DiskBar({ pct }) {
  return (
    <div className="w-full bg-slate-700 rounded-full h-2 overflow-hidden">
      <div
        className={`h-2 rounded-full transition-all duration-500 ${diskBg(pct)}`}
        style={{ width: `${Math.min(pct || 0, 100)}%` }}
      />
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export default function QueueHealthPanel({ adminToken }) {
  const apiFetch = useCallback(makeApiFetch(adminToken), [adminToken]);

  const [health, setHealth] = useState(null);
  const [tune, setTune] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tuneLoading, setTuneLoading] = useState(false);
  const [error, setError] = useState(null);
  const [pausing, setPausing] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [pulse, setPulse] = useState(false);

  const prevDepthRef = useRef(null);
  const intervalRef = useRef(null);

  const fetchHealth = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await apiFetch('/queue-health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setHealth((prev) => {
        prevDepthRef.current = prev?.queue_depth ?? null;
        return data;
      });
      setLastRefresh(new Date());
      setError(null);
      setPulse(true);
      setTimeout(() => setPulse(false), 600);
    } catch (err) {
      setError(err.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [apiFetch]);

  const fetchTune = useCallback(async () => {
    setTuneLoading(true);
    try {
      const res = await apiFetch('/auto-tune');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTune(data);
    } catch {
      // non-critical
    } finally {
      setTuneLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    fetchHealth();
    fetchTune();
    intervalRef.current = setInterval(() => fetchHealth(true), 10_000);
    return () => clearInterval(intervalRef.current);
  }, [fetchHealth, fetchTune]);

  const handleTogglePause = async () => {
    const isPaused = health?.paused;
    const endpoint = isPaused ? '/queue-health/resume' : '/queue-health/pause';
    setPausing(true);
    try {
      const res = await apiFetch(endpoint, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchHealth(true);
    } catch (err) {
      setError(`Không thể ${isPaused ? 'tiếp tục' : 'tạm dừng'}: ${err.message}`);
    } finally {
      setPausing(false);
    }
  };

  const handleResetTune = async () => {
    setResetting(true);
    try {
      const res = await apiFetch('/auto-tune/reset', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchTune();
    } catch (err) {
      setError(`Reset thất bại: ${err.message}`);
    } finally {
      setResetting(false);
    }
  };

  const isPaused = health?.paused;
  const diskPct = health?.disk_pressure_pct;
  const providerLevel = health?.provider_pressure;

  return (
    <div className="space-y-5 text-slate-100">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Activity size={20} className="text-violet-400" />
          <h2 className="text-lg font-semibold">Sức khỏe hàng đợi</h2>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-slate-500 tabular-nums">
              Cập nhật: {lastRefresh.toLocaleTimeString('vi-VN')}
            </span>
          )}
          <div
            className={`w-2 h-2 rounded-full transition-all duration-300 ${pulse ? 'bg-violet-300 scale-125' : 'bg-violet-600'}`}
            title="Tự động làm mới mỗi 10s"
          />
          <button
            onClick={() => { fetchHealth(); fetchTune(); }}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white text-xs rounded-lg border border-slate-600 transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Làm mới
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 bg-red-900/40 border border-red-700/50 text-red-300 text-sm px-4 py-3 rounded-xl">
          <AlertTriangle size={15} />
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            className="ml-auto text-red-400 hover:text-red-200 text-xs"
          >
            ✕
          </button>
        </div>
      )}

      {/* Paused banner */}
      {isPaused && (
        <div className="flex items-center gap-2 bg-amber-900/40 border border-amber-700/50 text-amber-300 text-sm px-4 py-3 rounded-xl">
          <Pause size={15} />
          <span>
            Job ưu tiên thấp đang bị tạm dừng — hệ thống chỉ xử lý interactive &amp; redownload.
          </span>
        </div>
      )}

      {/* Stat cards */}
      {loading && !health ? (
        <div className="flex items-center justify-center py-16 text-slate-500 gap-2">
          <Loader2 size={20} className="animate-spin" />
          <span>Đang tải dữ liệu hàng đợi…</span>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">

            {/* Queue depth */}
            <StatCard
              icon={Layers}
              label="Độ sâu hàng đợi"
              value={health?.queue_depth ?? '—'}
              sub="job đang chờ"
              iconColor="text-violet-400"
              colorClass="text-violet-300"
              trend={<TrendIcon value={health?.queue_depth} prev={prevDepthRef.current} />}
            />

            {/* Wait time */}
            <StatCard
              icon={Clock}
              label="Thời gian chờ ước tính"
              value={fmtWait(health?.estimated_wait_seconds)}
              sub={health?.estimated_wait_seconds != null ? `${health.estimated_wait_seconds}s` : undefined}
              iconColor="text-cyan-400"
              colorClass="text-cyan-300"
            />

            {/* Active workers */}
            <StatCard
              icon={Cpu}
              label="Worker đang chạy"
              value={health?.active_workers ?? '—'}
              sub="tiến trình hoạt động"
              iconColor="text-blue-400"
              colorClass="text-blue-300"
            />

            {/* Disk pressure */}
            <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 flex flex-col gap-2 min-w-0 col-span-2 lg:col-span-1">
              <div className="flex items-center gap-2 text-slate-400 text-xs font-medium">
                <HardDrive size={14} className={diskColor(diskPct)} />
                <span>Áp lực đĩa</span>
              </div>
              <div className={`text-2xl font-bold tabular-nums ${diskColor(diskPct)}`}>
                {diskPct != null ? `${diskPct.toFixed(1)}%` : '—'}
              </div>
              <DiskBar pct={diskPct} />
              <p className="text-xs text-slate-500">
                {diskPct > 85
                  ? 'Nguy hiểm — cần dọn dẹp ngay'
                  : diskPct > 70
                  ? 'Cảnh báo — đang đầy dần'
                  : 'Bình thường'}
              </p>
            </div>

            {/* Provider pressure */}
            <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 flex flex-col gap-2 min-w-0">
              <div className="flex items-center gap-2 text-slate-400 text-xs font-medium">
                <Server size={14} className={providerColor(providerLevel)} />
                <span>Áp lực Provider</span>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className={`text-sm font-semibold px-2.5 py-1 rounded-md ${providerBadge(providerLevel)}`}>
                  {providerLevel
                    ? providerLevel.charAt(0).toUpperCase() + providerLevel.slice(1)
                    : '—'}
                </span>
              </div>
              {providerLevel === 'high' && (
                <p className="text-xs text-red-400">Giảm tốc độ tải xuống</p>
              )}
              {providerLevel === 'elevated' && (
                <p className="text-xs text-amber-400">Theo dõi chặt chẽ</p>
              )}
              {providerLevel === 'normal' && (
                <p className="text-xs text-emerald-400">Hoạt động ổn định</p>
              )}
            </div>
          </div>

          {/* Priority breakdown */}
          <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-3">
            <div className="flex items-center gap-2 text-slate-300 text-sm font-medium">
              <Zap size={15} className="text-amber-400" />
              Phân bổ theo mức ưu tiên
            </div>
            <PriorityBar breakdown={health?.priority_breakdown} />
          </div>

          {/* Pause / Resume */}
          <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-slate-200">Tạm dừng việc ưu tiên thấp</p>
              <p className="text-xs text-slate-500 mt-0.5">
                Tạm dừng batch, scheduled và maintenance jobs để ưu tiên tài nguyên cho interactive.
              </p>
            </div>
            <button
              onClick={handleTogglePause}
              disabled={pausing}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border transition-all min-w-[130px] justify-center ${
                isPaused
                  ? 'bg-emerald-900/50 border-emerald-700 text-emerald-300 hover:bg-emerald-800/60'
                  : 'bg-amber-900/50 border-amber-700 text-amber-300 hover:bg-amber-800/60'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              {pausing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : isPaused ? (
                <Play size={14} />
              ) : (
                <Pause size={14} />
              )}
              {isPaused ? 'Tiếp tục' : 'Tạm dừng'}
            </button>
          </div>
        </>
      )}

      {/* Auto-tune section */}
      <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-slate-300 text-sm font-medium">
            <Activity size={15} className="text-violet-400" />
            Tham số tự chỉnh (Auto-tune)
          </div>
          <button
            onClick={handleResetTune}
            disabled={resetting || tuneLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white text-xs rounded-lg border border-slate-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {resetting ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RotateCcw size={12} />
            )}
            Reset về mặc định
          </button>
        </div>

        {tuneLoading && !tune ? (
          <div className="flex items-center gap-2 text-slate-500 text-xs py-3">
            <Loader2 size={13} className="animate-spin" />
            Đang tải tham số…
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            <TuneChip label="Batch concurrency" value={tune?.batch_concurrency} />
            <TuneChip label="Wave size" value={tune?.wave_size} />
            <TuneChip
              label="Retry delay ×"
              value={tune?.retry_delay_multiplier != null ? `${tune.retry_delay_multiplier}×` : null}
            />
            {tune &&
              Object.entries(tune)
                .filter(([k]) => !['batch_concurrency', 'wave_size', 'retry_delay_multiplier'].includes(k))
                .map(([k, v]) => (
                  <TuneChip key={k} label={k.replace(/_/g, ' ')} value={String(v)} />
                ))}
          </div>
        )}
        <p className="text-xs text-slate-600">
          Chỉ đọc — hệ thống tự điều chỉnh dựa trên tải thực tế.
        </p>
      </div>
    </div>
  );
}

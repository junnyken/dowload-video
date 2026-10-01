import { useState, useEffect, useCallback } from 'react';
import {
  Activity, AlertTriangle, CheckCircle2, XCircle, Clock,
  RefreshCw, Loader2, Server, Wifi, WifiOff, Shield,
} from 'lucide-react';

// ── Helpers ─────────────────────────────────────────────────────────

function fmt(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return ts;
  }
}

function fmtFull(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleString('vi-VN', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return ts;
  }
}

// ── Sub-components ───────────────────────────────────────────────────

function MetricCard({ label, value, color = 'text-fg', bg = 'bg-surface', icon: Icon }) {
  return (
    <div className={`flex items-center gap-3 rounded-xl border border-line p-4 ${bg}`}>
      {Icon && (
        <div className="shrink-0 w-9 h-9 rounded-lg bg-surface-2 flex items-center justify-center">
          <Icon className={`w-4.5 h-4.5 ${color}`} style={{ width: '18px', height: '18px' }} />
        </div>
      )}
      <div className="min-w-0">
        <p className="text-[11px] font-medium text-fg-muted uppercase tracking-wider truncate">{label}</p>
        <p className={`text-2xl font-bold mt-0.5 ${color}`}>{value ?? '—'}</p>
      </div>
    </div>
  );
}

function SectionTitle({ icon: Icon, children }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      {Icon && <Icon className="w-4 h-4 text-fg-2" />}
      <h3 className="text-sm font-semibold text-fg-2 uppercase tracking-widest">{children}</h3>
    </div>
  );
}

function QueueBar({ name, depth }) {
  const max = 20;
  const pct = Math.min((depth / max) * 100, 100);
  const color = depth === 0 ? 'bg-success' : depth <= 5 ? 'bg-accent' : depth <= 15 ? 'bg-accent' : 'bg-danger';

  return (
    <div className="flex items-center gap-3">
      <span className="w-20 shrink-0 text-xs text-fg-muted font-mono truncate">{name}</span>
      <div className="flex-1 h-2 rounded-full bg-surface-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`w-6 text-right text-xs font-bold ${depth === 0 ? 'text-success' : depth <= 5 ? 'text-fg-2' : depth <= 15 ? 'text-accent-text' : 'text-danger'}`}>
        {depth}
      </span>
    </div>
  );
}

const CIRCUIT_STATE = {
  closed: {
    label: 'CLOSED',
    bg: 'bg-success-soft border-success/40',
    text: 'text-success',
    dot: 'bg-success',
    Icon: CheckCircle2,
  },
  open: {
    label: 'OPEN',
    bg: 'bg-danger-soft border-danger/40',
    text: 'text-danger',
    dot: 'bg-danger',
    Icon: XCircle,
  },
  half: {
    label: 'HALF',
    bg: 'bg-accent-soft border-accent/40',
    text: 'text-accent-text',
    dot: 'bg-accent',
    Icon: AlertTriangle,
  },
};

function CircuitBadge({ state }) {
  const cfg = CIRCUIT_STATE[state?.toLowerCase()] || {
    label: state?.toUpperCase() || '—',
    bg: 'bg-line-strong border-line-strong',
    text: 'text-fg-2',
    dot: 'bg-line-strong',
    Icon: Shield,
  };
  const { Icon } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-bold ${cfg.bg} ${cfg.text}`}>
      <Icon className="w-3 h-3" />
      {cfg.label}
    </span>
  );
}

function CookieHealthRow({ platform, health }) {
  const { total = 0, available = 0, blocked_soft = 0, blocked_hard = 0 } = health || {};
  const availPct = total > 0 ? Math.round((available / total) * 100) : 0;
  const healthColor =
    blocked_hard > 0 ? 'text-danger' :
    blocked_soft > 0 ? 'text-accent-text' :
    'text-success';

  return (
    <div className="flex items-center gap-3 py-1.5 border-b border-line last:border-0">
      <span className="w-20 shrink-0 text-xs text-fg-2 font-medium capitalize truncate">{platform}</span>
      <div className="flex-1 flex items-center gap-2 flex-wrap">
        <span className={`text-xs font-bold ${healthColor}`}>{available}/{total} avail</span>
        {blocked_soft > 0 && (
          <span className="text-[11px] px-1.5 py-0.5 rounded bg-accent-soft text-accent-text border border-accent/30">
            soft {blocked_soft}
          </span>
        )}
        {blocked_hard > 0 && (
          <span className="text-[11px] px-1.5 py-0.5 rounded bg-danger-soft text-danger border border-danger/30">
            hard {blocked_hard}
          </span>
        )}
      </div>
      <div className="w-20 h-1.5 rounded-full bg-surface-2 overflow-hidden shrink-0">
        <div
          className={`h-full rounded-full transition-all duration-500 ${availPct >= 50 ? 'bg-success' : availPct > 0 ? 'bg-accent' : 'bg-danger'}`}
          style={{ width: `${availPct}%` }}
        />
      </div>
    </div>
  );
}

function RecoveryLogRow({ entry }) {
  return (
    <div className="flex items-start gap-3 py-2 border-b border-line last:border-0 text-xs">
      <span className="shrink-0 font-mono text-fg-muted pt-0.5">{fmt(entry.ts)}</span>
      <span className="px-1.5 py-0.5 rounded bg-surface-2 border border-line text-fg-2 font-mono shrink-0">
        {entry.action || '—'}
      </span>
      <span className="text-fg-muted font-mono truncate">{entry.job_id || ''}</span>
      <span className="text-fg-muted truncate ml-auto pl-2">{entry.reason || ''}</span>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export default function OpsPanel({ adminToken, adminFetch: adminFetchProp }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);

  const adminFetch = useCallback(
    adminFetchProp || ((path, opts = {}) => {
      const API = `${import.meta.env.VITE_API_URL || ''}/api/v1/admin`;
      const authHeader = adminToken
        ? { Authorization: `Bearer ${adminToken}` }
        : { 'X-Admin-Token': sessionStorage.getItem('admin_token') || '' };
      return fetch(`${API}${path}`, { ...opts, headers: { ...authHeader, ...opts.headers } });
    }),
    [adminToken, adminFetchProp],
  );

  const fetchData = useCallback(async () => {
    try {
      const res = await adminFetch('/ops-signals');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
      setLastRefresh(new Date());
    } catch (e) {
      setError(e.message || 'Không thể tải dữ liệu');
    } finally {
      setLoading(false);
    }
  }, [adminFetch]);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [fetchData]);

  // ── Loading ──
  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-48 text-fg-muted gap-3">
        <Loader2 className="w-5 h-5 animate-spin text-fg-2" />
        <span className="text-sm">Đang tải Ops Signals...</span>
      </div>
    );
  }

  // ── Error ──
  if (error && !data) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-3 text-fg-muted">
        <XCircle className="w-6 h-6 text-danger" />
        <p className="text-sm text-danger">{error}</p>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg bg-surface-2 hover:bg-line border border-line-strong text-fg-2 transition-colors cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Thử lại
        </button>
      </div>
    );
  }

  const metrics = data?.job_metrics_30m || {};
  const queues = data?.queue_depths || {};
  const circuits = data?.provider_circuits || {};
  const cookies = data?.cookie_health || {};
  const recoveryLog = data?.recovery_log || [];
  const staleCount = data?.stale_job_count ?? metrics.stale ?? 0;
  const workerCount = data?.worker_count ?? '—';
  const quotaDenials = data?.quota_denials_30m ?? '—';

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-fg-2" />
          <h3 className="text-lg font-bold text-fg">Ops Signals</h3>
          {loading && <Loader2 className="w-4 h-4 animate-spin text-fg-2 ml-1" />}
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-[11px] text-fg-muted font-mono">
              {fmt(lastRefresh)}
            </span>
          )}
          {data?.generated_at && (
            <span className="text-[11px] text-fg-muted hidden sm:block">
              signal {fmtFull(data.generated_at)}
            </span>
          )}
          <button
            onClick={() => { setLoading(true); fetchData(); }}
            className="p-1.5 rounded-lg bg-surface border border-line text-fg-muted hover:text-fg-2 transition-colors cursor-pointer"
            title="Làm mới"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-danger-soft border border-danger/30 text-danger text-xs">
          <AlertTriangle className="w-4 h-4 shrink-0" /> {error} — hiển thị dữ liệu cũ
        </div>
      )}

      {/* ── Row 1: Stale + Workers + Quota + Job Metrics ── */}
      <div>
        <SectionTitle icon={Clock}>Job Metrics (30 phút qua)</SectionTitle>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <MetricCard
            label="Stale Jobs"
            value={staleCount}
            color={staleCount > 0 ? 'text-danger' : 'text-success'}
            bg={staleCount > 0 ? 'bg-danger-soft' : 'bg-success-soft'}
            icon={staleCount > 0 ? AlertTriangle : CheckCircle2}
          />
          <MetricCard label="Succeeded" value={metrics.succeeded ?? '—'} color="text-success" icon={CheckCircle2} />
          <MetricCard label="Failed" value={metrics.failed ?? '—'} color={metrics.failed > 0 ? 'text-danger' : 'text-fg-muted'} icon={XCircle} />
          <MetricCard label="Retrying" value={metrics.retrying ?? '—'} color={metrics.retrying > 0 ? 'text-warning' : 'text-fg-muted'} icon={RefreshCw} />
          <MetricCard label="Workers" value={workerCount} color="text-fg-2" icon={Server} />
          <MetricCard label="Quota Denials" value={quotaDenials} color={quotaDenials > 0 ? 'text-warning' : 'text-fg-muted'} icon={Shield} />
        </div>
      </div>

      {/* ── Row 2: Queue Depths + Circuit States ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Queue Depths */}
        <div className="rounded-xl border border-line bg-surface/80 p-4">
          <SectionTitle icon={Activity}>Queue Depths</SectionTitle>
          <div className="space-y-2.5">
            {Object.keys(queues).length === 0 ? (
              <p className="text-xs text-fg-muted">Không có dữ liệu queue</p>
            ) : (
              Object.entries(queues).map(([name, depth]) => (
                <QueueBar key={name} name={name} depth={depth} />
              ))
            )}
          </div>
        </div>

        {/* Circuit States */}
        <div className="rounded-xl border border-line bg-surface/80 p-4">
          <SectionTitle icon={Wifi}>Provider Circuit States</SectionTitle>
          <div className="space-y-2">
            {Object.keys(circuits).length === 0 ? (
              <p className="text-xs text-fg-muted">Không có dữ liệu circuit</p>
            ) : (
              Object.entries(circuits).map(([platform, state]) => (
                <div key={platform} className="flex items-center justify-between py-1.5 border-b border-line last:border-0">
                  <span className="text-sm text-fg-2 capitalize font-medium">{platform}</span>
                  <CircuitBadge state={state} />
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── Row 3: Cookie Pool Health ── */}
      <div className="rounded-xl border border-line bg-surface/80 p-4">
        <SectionTitle icon={WifiOff}>Cookie Pool Health</SectionTitle>
        {Object.keys(cookies).length === 0 ? (
          <p className="text-xs text-fg-muted">Không có dữ liệu cookie pool</p>
        ) : (
          <div>
            {Object.entries(cookies).map(([platform, health]) => (
              <CookieHealthRow key={platform} platform={platform} health={health} />
            ))}
          </div>
        )}
      </div>

      {/* ── Row 4: Recovery Log ── */}
      <div className="rounded-xl border border-line bg-surface/80 p-4">
        <SectionTitle icon={RefreshCw}>Recovery Log (10 gần nhất)</SectionTitle>
        {recoveryLog.length === 0 ? (
          <p className="text-xs text-fg-muted">Chưa có recovery actions</p>
        ) : (
          <div className="overflow-x-auto">
            {recoveryLog.slice(0, 10).map((entry, i) => (
              <RecoveryLogRow key={i} entry={entry} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

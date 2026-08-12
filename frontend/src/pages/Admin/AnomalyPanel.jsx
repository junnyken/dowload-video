import { useState, useEffect, useCallback } from 'react';
import {
  AlertTriangle, CheckCircle, Clock, RefreshCw, Loader2,
  ShieldAlert, Eye, TrendingUp, Zap, Shield,
} from 'lucide-react';

const INTEL_API = `${import.meta.env.VITE_API_URL || ''}/api/v1/intelligence`;

const STATE_CONFIG = {
  detected: {
    label: 'Phát hiện',
    bg: 'bg-yellow-500/15',
    border: 'border-yellow-500/40',
    text: 'text-yellow-300',
    badge: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40',
    dot: 'bg-yellow-400 shadow-[0_0_6px_#facc15]',
    icon: AlertTriangle,
  },
  under_watch: {
    label: 'Đang theo dõi',
    bg: 'bg-orange-500/15',
    border: 'border-orange-500/40',
    text: 'text-orange-300',
    badge: 'bg-orange-500/20 text-orange-300 border border-orange-500/40',
    dot: 'bg-orange-400 shadow-[0_0_6px_#fb923c]',
    icon: Eye,
  },
  escalated: {
    label: 'Leo thang',
    bg: 'bg-red-500/15',
    border: 'border-red-500/40',
    text: 'text-red-300',
    badge: 'bg-red-500/20 text-red-300 border border-red-500/40',
    dot: 'bg-red-400 shadow-[0_0_6px_#f87171]',
    icon: ShieldAlert,
  },
  resolved: {
    label: 'Đã xử lý',
    bg: 'bg-emerald-500/10',
    border: 'border-emerald-500/30',
    text: 'text-emerald-300',
    badge: 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40',
    dot: 'bg-emerald-400 shadow-[0_0_6px_#34d399]',
    icon: CheckCircle,
  },
};

const FALLBACK_STATE = {
  label: 'Không rõ',
  bg: 'bg-slate-500/15',
  border: 'border-slate-500/40',
  text: 'text-slate-300',
  badge: 'bg-slate-500/20 text-slate-300 border border-slate-500/40',
  dot: 'bg-slate-400',
  icon: AlertTriangle,
};

function StateBadge({ state }) {
  const cfg = STATE_CONFIG[state] || FALLBACK_STATE;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${cfg.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  );
}

function AnomalyCard({ anomaly, adminToken, onResolved }) {
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState(null);

  const cfg = STATE_CONFIG[anomaly.state] || FALLBACK_STATE;
  const Icon = cfg.icon;

  const handleResolve = useCallback(async () => {
    setResolving(true);
    setResolveError(null);
    try {
      const res = await fetch(
        `${INTEL_API}/anomalies/${anomaly.id}/resolve`,
        {
          method: 'POST',
          headers: {
            'X-Admin-Token': adminToken,
            'Content-Type': 'application/json',
          },
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      onResolved(anomaly.id);
    } catch (err) {
      setResolveError(err.message || 'Lỗi không xác định');
    } finally {
      setResolving(false);
    }
  }, [anomaly.id, adminToken, onResolved]);

  const magnitudeDisplay = anomaly.magnitude != null
    ? (typeof anomaly.magnitude === 'number'
      ? (anomaly.magnitude >= 0
        ? `+${anomaly.magnitude}% so với bình thường`
        : `${anomaly.magnitude}% so với bình thường`)
      : String(anomaly.magnitude))
    : null;

  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-3 transition-all ${cfg.bg} ${cfg.border}`}>
      {/* Header row */}
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 flex-shrink-0 ${cfg.text}`}>
          <Icon size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-white text-sm truncate">
              {anomaly.metric_name || (anomaly.metric
                ? String(anomaly.metric).replace(/_/g, ' ')
                : 'Chỉ số không rõ')}
            </span>
            <StateBadge state={anomaly.state} />
            {anomaly.auto_mitigated && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-300 border border-blue-500/40">
                <Zap size={10} />
                Tự động xử lý
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Details grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-slate-300">
        {(anomaly.time_window || anomaly.window) && (
          <div className="flex items-center gap-1.5">
            <Clock size={12} className="text-slate-500 flex-shrink-0" />
            <span className="text-slate-400">Cửa sổ thời gian:</span>
            <span className="text-slate-200 font-medium">{anomaly.time_window || anomaly.window}</span>
          </div>
        )}
        {magnitudeDisplay && (
          <div className="flex items-center gap-1.5">
            <TrendingUp size={12} className="text-slate-500 flex-shrink-0" />
            <span className="text-slate-400">Mức độ:</span>
            <span className={`font-semibold ${cfg.text}`}>{magnitudeDisplay}</span>
          </div>
        )}
        {anomaly.detected_at && (
          <div className="flex items-center gap-1.5 sm:col-span-2">
            <AlertTriangle size={12} className="text-slate-500 flex-shrink-0" />
            <span className="text-slate-400">Phát hiện lúc:</span>
            <span className="text-slate-200">
              {new Date(anomaly.detected_at).toLocaleString('vi-VN', {
                dateStyle: 'short',
                timeStyle: 'short',
              })}
            </span>
          </div>
        )}
      </div>

      {/* Likely cause */}
      {anomaly.likely_cause && (
        <div className="rounded-lg bg-black/20 border border-slate-700/40 px-3 py-2 text-xs text-slate-300 leading-relaxed">
          <span className="text-slate-500 font-medium uppercase tracking-wide text-[10px]">Nguyên nhân có thể</span>
          <p className="mt-0.5">{anomaly.likely_cause}</p>
        </div>
      )}

      {/* Resolve error */}
      {resolveError && (
        <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
          {resolveError}
        </p>
      )}

      {/* Actions */}
      {anomaly.state !== 'resolved' && (
        <div className="flex justify-end">
          <button
            onClick={handleResolve}
            disabled={resolving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
              bg-emerald-500/20 text-emerald-300 border border-emerald-500/40
              hover:bg-emerald-500/30 hover:border-emerald-500/60
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-all active:scale-95"
          >
            {resolving
              ? <><Loader2 size={12} className="animate-spin" /> Đang xử lý…</>
              : <><CheckCircle size={12} /> Đánh dấu đã xử lý</>
            }
          </button>
        </div>
      )}
    </div>
  );
}

export default function AnomalyPanel({ adminToken }) {
  const [anomalies, setAnomalies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchAnomalies = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const res = await fetch(`${INTEL_API}/anomalies`, {
        headers: { 'X-Admin-Token': adminToken },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const list = Array.isArray(data)
        ? data
        : Array.isArray(data.anomalies)
          ? data.anomalies
          : [];
      setAnomalies(list);
      setLastRefresh(new Date());
    } catch (err) {
      setError(err.message || 'Không thể tải dữ liệu bất thường');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [adminToken]);

  useEffect(() => {
    fetchAnomalies(false);
    const id = setInterval(() => fetchAnomalies(true), 30_000);
    return () => clearInterval(id);
  }, [fetchAnomalies]);

  const handleResolved = useCallback((id) => {
    setAnomalies(prev =>
      prev.map(a => a.id === id ? { ...a, state: 'resolved' } : a),
    );
  }, []);

  const handleManualRefresh = useCallback(() => {
    fetchAnomalies(true);
  }, [fetchAnomalies]);

  const activeCount = anomalies.filter(a => a.state !== 'resolved').length;

  return (
    <div className="rounded-2xl bg-zinc-900 border border-zinc-800 shadow-xl overflow-hidden">
      {/* Panel header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800 bg-zinc-900/80">
        <div className="flex items-center gap-2.5">
          <Shield size={18} className="text-amber-400" />
          <h2 className="text-sm font-bold text-white tracking-wide">Phát hiện bất thường</h2>
          {activeCount > 0 && (
            <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[10px] font-bold bg-red-500 text-white shadow-[0_0_8px_#ef4444]">
              {activeCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="hidden sm:block text-[10px] text-zinc-500">
              Cập nhật {lastRefresh.toLocaleTimeString('vi-VN', { timeStyle: 'short' })}
            </span>
          )}
          <button
            onClick={handleManualRefresh}
            disabled={refreshing || loading}
            title="Làm mới"
            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Panel body */}
      <div className="p-4 sm:p-5">
        {loading && (
          <div className="flex flex-col items-center gap-3 py-12 text-zinc-500">
            <Loader2 size={28} className="animate-spin text-amber-400/60" />
            <span className="text-sm">Đang tải dữ liệu…</span>
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <AlertTriangle size={28} className="text-red-400" />
            <p className="text-sm text-red-300 font-medium">Không thể tải dữ liệu</p>
            <p className="text-xs text-zinc-500">{error}</p>
            <button
              onClick={() => fetchAnomalies(false)}
              className="mt-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-zinc-700 text-zinc-200 hover:bg-zinc-600 transition-all"
            >
              Thử lại
            </button>
          </div>
        )}

        {!loading && !error && anomalies.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <div className="w-12 h-12 rounded-full bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center">
              <CheckCircle size={22} className="text-emerald-400" />
            </div>
            <p className="text-sm font-semibold text-zinc-200">Không phát hiện bất thường</p>
            <p className="text-xs text-zinc-500">Hệ thống hoạt động bình thường</p>
          </div>
        )}

        {!loading && !error && anomalies.length > 0 && (
          <div className="flex flex-col gap-3">
            {[...anomalies]
              .sort((a, b) => {
                const order = { escalated: 0, under_watch: 1, detected: 2, resolved: 3 };
                return (order[a.state] ?? 4) - (order[b.state] ?? 4);
              })
              .map(anomaly => (
                <AnomalyCard
                  key={anomaly.id}
                  anomaly={anomaly}
                  adminToken={adminToken}
                  onResolved={handleResolved}
                />
              ))
            }
          </div>
        )}
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback } from 'react';
import {
  Play, RefreshCw, Loader2, ToggleLeft, ToggleRight, AlertTriangle,
  DollarSign, Gauge, ShieldAlert, CheckCircle, Activity,
} from 'lucide-react';

const ADMIN_API = `${import.meta.env.VITE_API_URL || ''}/api/v1/admin`;

// status_color (from backend dashboard_snapshot) → Tailwind tokens
const COLOR = {
  green:  { dot: 'bg-emerald-400 shadow-[0_0_6px_#34d399]', text: 'text-emerald-300', bar: 'bg-emerald-500', ring: 'border-emerald-500/40', label: 'Bình thường' },
  yellow: { dot: 'bg-yellow-400 shadow-[0_0_6px_#facc15]',  text: 'text-yellow-300',  bar: 'bg-yellow-500',  ring: 'border-yellow-500/40',  label: 'Gần ngưỡng' },
  red:    { dot: 'bg-red-400 shadow-[0_0_6px_#f87171]',     text: 'text-red-300',     bar: 'bg-red-500',     ring: 'border-red-500/40',     label: 'Tạm tắt / vượt ngưỡng' },
};

const CB = {
  closed: { text: 'text-emerald-300', label: 'Đóng (OK)', icon: CheckCircle },
  half:   { text: 'text-yellow-300',  label: 'Nửa-mở (đang thử)', icon: ShieldAlert },
  open:   { text: 'text-red-300',     label: 'MỞ (đang tạm tắt)', icon: ShieldAlert },
};

function Stat({ label, value, sub, valueClass = 'text-white' }) {
  return (
    <div className="bg-[#0d1f1b] border border-emerald-900/40 rounded-xl p-4">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-2xl font-semibold ${valueClass}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </div>
  );
}

export default function YouTubeGatePanel({ adminToken }) {
  const [snap, setSnap] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);

  const headers = { 'X-Admin-Token': adminToken };

  const fetchStatus = useCallback(async (silent = false) => {
    silent ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${ADMIN_API}/youtube/status`, { headers });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${res.status}`);
      }
      setSnap(await res.json());
      setLastRefresh(new Date());
    } catch (e) {
      setError(e.message || 'Không tải được trạng thái YouTube');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [adminToken]);

  const toggle = useCallback(async (enabled) => {
    setToggling(true);
    setError(null);
    try {
      const res = await fetch(`${ADMIN_API}/youtube/toggle`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${res.status}`);
      }
      setSnap(await res.json());
      setLastRefresh(new Date());
    } catch (e) {
      setError(e.message || 'Không đổi được trạng thái YouTube');
    } finally {
      setToggling(false);
    }
  }, [adminToken]);

  useEffect(() => {
    fetchStatus(false);
    const id = setInterval(() => fetchStatus(true), 15_000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Đang tải trạng thái YouTube...
      </div>
    );
  }

  const c = COLOR[snap?.status_color] || COLOR.green;
  const pct = Math.round((snap?.bytes_pct || 0) * 100);
  const cbState = CB[snap?.circuit_state] || CB.closed;
  const CbIcon = cbState.icon;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Play className="w-5 h-5 text-red-400" />
          <h2 className="text-lg font-semibold text-white">YouTube Proxy Gate</h2>
          <span className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs border ${c.ring} ${c.text} bg-black/20`}>
            <span className={`w-2 h-2 rounded-full ${c.dot}`} /> {c.label}
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-400">
          {lastRefresh && <span>Cập nhật {lastRefresh.toLocaleTimeString('vi-VN')}</span>}
          <button onClick={() => fetchStatus(true)} disabled={refreshing}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-emerald-900/50 hover:bg-emerald-900/20">
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} /> Làm mới
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Master toggle */}
      <div className={`flex items-center justify-between rounded-xl p-4 border ${snap?.enabled ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-red-500/40 bg-red-500/5'}`}>
        <div>
          <div className="text-sm font-medium text-white">
            YouTube hiện {snap?.enabled ? 'BẬT' : 'TẮT'}
          </div>
          <div className="text-xs text-slate-400 mt-0.5">
            Tắt = dừng mọi extraction + chi phí proxy ngay (không cần deploy).
            {snap?.proxy_download_enabled
              ? ' Phase B (tải bytes qua proxy) đang BẬT.'
              : ' Phase B đang TẮT (metadata-only) — đặt env YOUTUBE_PROXY_DOWNLOAD=1 để tải thật.'}
          </div>
        </div>
        <button
          onClick={() => toggle(!snap?.enabled)}
          disabled={toggling}
          className="inline-flex items-center gap-2 text-sm font-medium disabled:opacity-50"
          title="Bật/tắt YouTube"
        >
          {toggling
            ? <Loader2 className="w-8 h-8 animate-spin text-slate-400" />
            : snap?.enabled
              ? <ToggleRight className="w-10 h-10 text-emerald-400" />
              : <ToggleLeft className="w-10 h-10 text-slate-500" />}
        </button>
      </div>

      {/* Stat grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat
          label="Chi phí proxy hôm nay"
          value={`$${(snap?.cost_today ?? 0).toFixed(2)}`}
          sub={`@ $${snap?.cost_per_gb}/GB`}
          valueClass={snap?.over_limit ? 'text-red-300' : c.text}
        />
        <Stat
          label="Băng thông đã dùng"
          value={`${(snap?.bytes_gb ?? 0).toFixed(2)} GB`}
          sub={`/ ${snap?.limit_gb} GB giới hạn ngày`}
          valueClass={c.text}
        />
        <Stat
          label="Tỷ lệ thành công"
          value={`${snap?.success_rate ?? 100}%`}
          sub={`${snap?.success ?? 0} ok · ${snap?.fail ?? 0} lỗi · ${snap?.requests ?? 0} req`}
        />
        <Stat
          label="Tier tối đa (proxy)"
          value={`${snap?.max_proxy_height ?? 720}p`}
          sub="1080p cần confirm · 4K bị chặn"
        />
      </div>

      {/* Bandwidth bar */}
      <div className="bg-[#0d1f1b] border border-emerald-900/40 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-sm text-slate-300">
            <Gauge className="w-4 h-4" /> Ngưỡng băng thông ngày
          </div>
          <div className={`text-sm font-medium ${c.text}`}>{pct}%</div>
        </div>
        <div className="w-full h-3 rounded-full bg-black/40 overflow-hidden">
          <div className={`h-full ${c.bar} transition-all`} style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
        <div className="flex items-center justify-between mt-2 text-xs text-slate-500">
          <span>{(snap?.bytes_gb ?? 0).toFixed(3)} GB</span>
          <span>{snap?.over_limit ? '⛔ Vượt ngưỡng — YouTube tự tắt tới hết ngày' : `${snap?.limit_gb} GB`}</span>
        </div>
      </div>

      {/* Circuit breaker */}
      <div className="bg-[#0d1f1b] border border-emerald-900/40 rounded-xl p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <CbIcon className={`w-5 h-5 ${cbState.text}`} />
          <div>
            <div className="text-sm text-slate-300">Circuit breaker (proxy)</div>
            <div className={`text-base font-semibold ${cbState.text}`}>{cbState.label}</div>
          </div>
        </div>
        {snap?.circuit_state === 'open' && (
          <div className="text-right text-xs text-slate-400">
            <div className="flex items-center gap-1 justify-end"><Activity className="w-3.5 h-3.5" /> Mở lại sau</div>
            <div className="text-red-300 font-medium">{snap?.circuit_cooldown_sec ?? 0}s</div>
          </div>
        )}
      </div>

      <p className="text-[11px] text-slate-600 flex items-center gap-1">
        <DollarSign className="w-3 h-3" />
        Tự động cảnh báo Telegram khi &gt;80% ngưỡng, success &lt;60%, hoặc circuit mở. Tự tắt YouTube khi vượt {snap?.limit_gb}GB/ngày.
      </p>
    </div>
  );
}

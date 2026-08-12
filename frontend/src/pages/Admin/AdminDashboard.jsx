import { useState, useEffect, useCallback } from 'react';
import {
  Shield, CheckCircle, XCircle, AlertTriangle, Users, Download, Activity,
  ToggleLeft, ToggleRight, Key, Copy, TrendingUp, BarChart3, Bell,
  RefreshCw, Zap, Clock, Loader2, Send, HardDrive, Server, Wifi,
  WifiOff, Database, Globe, Flag, Bug, BarChart2, Wand2, Eye, Info, LogOut, Play,
  Layers, UserSearch,
} from 'lucide-react';
import AnomalyPanel from './AnomalyPanel';
import QueueHealthPanel from './QueueHealthPanel';
import PlaybooksPanel from './PlaybooksPanel';
import AutomationHistoryPanel from './AutomationHistoryPanel';
import YouTubeGatePanel from './YouTubeGatePanel';
import OpsPanel from './OpsPanel';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1/admin`;

// adminFetch is created per-component using the token from React state (memory).
// Falls back to legacy X-Admin-Token via sessionStorage for backward compat.
function makeAdminFetch(token) {
  return (path, opts = {}) => {
    const authHeader = token
      ? { Authorization: `Bearer ${token}` }
      : { 'X-Admin-Token': sessionStorage.getItem('admin_token') || '' };
    return fetch(`${API}${path}`, {
      ...opts,
      headers: { ...authHeader, ...opts.headers },
    });
  };
}

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
// Flow/Veo Cleanup · QA Tab
// Jobs are ephemeral (~20 min). Visible-logo cleanup only — SynthID
// is out of scope and never implied as removed.
// ═════════════════════════════════════════════════════════════════════

const STATUS_CLR = {
  completed: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  failed:    'bg-red-500/20 text-red-400 border border-red-500/30',
  uploaded:  'bg-slate-700/40 text-slate-400 border border-slate-600/30',
};
const RESULT_CLR = {
  cleaned_visible_logo:        'bg-emerald-500/15 text-emerald-300',
  cropped_export:              'bg-sky-500/15 text-sky-300',
  original_passthrough_no_logo:'bg-slate-600/20 text-slate-400',
};
const FLAG_CLR = {
  long_clip:            'bg-amber-500/15 text-amber-400',
  medium_length_clip:   'bg-amber-500/10 text-amber-500',
  high_fps:             'bg-orange-500/15 text-orange-400',
  low_res:              'bg-purple-500/15 text-purple-400',
  crop_preferred:       'bg-sky-500/15 text-sky-400',
};
const SUIT_CLR = {
  good:   'text-emerald-400',
  manual: 'text-amber-400',
  crop:   'text-sky-400',
  low:    'text-orange-400',
};

function FlowQATab({ data, onRefresh }) {
  const [sel, setSel] = useState(null);

  if (!data) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="w-8 h-8 animate-spin text-slate-500" />
      </div>
    );
  }

  const jobs = data.jobs || [];
  const counts = {
    total:    jobs.length,
    cleaned:  jobs.filter(j => j.result_type === 'cleaned_visible_logo').length,
    cropped:  jobs.filter(j => j.result_type === 'cropped_export').length,
    no_logo:  jobs.filter(j => j.result_type === 'original_passthrough_no_logo').length,
    failed:   jobs.filter(j => j.status === 'failed').length,
    uploaded: jobs.filter(j => j.status === 'uploaded').length,
  };

  return (
    <div className="space-y-5 animate-in fade-in duration-300">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <Wand2 className="w-4 h-4 text-emerald-400" />
            <h3 className="text-base font-bold text-white">Flow/Veo Cleanup · QA</h3>
          </div>
          <p className="text-[11px] text-slate-500 ml-6">
            Jobs trong ~20 phút · Visible on-frame logo only · SynthID không trong phạm vi
          </p>
        </div>
        <button onClick={onRefresh} className="p-2 rounded-xl bg-slate-800/50 border border-slate-700/50 text-slate-400 hover:text-white transition-colors cursor-pointer">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        {[
          { label: 'Tổng',         val: counts.total,    color: 'text-white' },
          { label: 'Logo cleaned', val: counts.cleaned,  color: 'text-emerald-400' },
          { label: 'Cropped',      val: counts.cropped,  color: 'text-sky-400' },
          { label: 'No logo',      val: counts.no_logo,  color: 'text-slate-400' },
          { label: 'Chờ xử lý',   val: counts.uploaded, color: 'text-slate-500' },
          { label: 'Failed',       val: counts.failed,   color: 'text-red-400' },
        ].map(c => (
          <div key={c.label} className="px-3 py-3 rounded-xl bg-slate-800/40 border border-slate-700/30 text-center">
            <p className={`text-xl font-black ${c.color}`}>{c.val}</p>
            <p className="text-[10px] text-slate-500 mt-0.5 leading-tight">{c.label}</p>
          </div>
        ))}
      </div>

      {/* Scope guard */}
      <div className="flex items-start gap-2 px-3 py-2 rounded-xl bg-slate-800/40 border border-slate-700/30">
        <Info className="w-3.5 h-3.5 text-slate-500 flex-shrink-0 mt-0.5" />
        <p className="text-[11px] text-slate-500 leading-snug">
          Workflow này chỉ xử lý <strong className="text-slate-400">logo hiển thị trên khung hình</strong>.
          SynthID (invisible watermark) không được xử lý và không xuất hiện trong bất kỳ trạng thái nào ở đây.
        </p>
      </div>

      {/* Job list */}
      {jobs.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-12 text-slate-500">
          <Eye className="w-8 h-8" />
          <p className="text-sm">Không có job nào trong window hiện tại</p>
          <p className="text-xs text-slate-600">Upload video trên trang Flow/Veo Cleanup để tạo job mới</p>
        </div>
      ) : (
        <div className="space-y-2">
          {jobs.map(job => {
            const isOpen = sel?.temp_id === job.temp_id;
            return (
              <div
                key={job.temp_id}
                className={`rounded-xl border transition-all ${isOpen ? 'border-[#FB923C]/40 bg-[#FB923C]/5' : 'border-slate-700/30 bg-slate-800/30 hover:border-slate-600/50'}`}
              >
                {/* Row */}
                <div
                  className="flex items-center gap-2.5 px-4 py-3 cursor-pointer flex-wrap"
                  onClick={() => setSel(isOpen ? null : job)}
                >
                  <span className="font-mono text-[10px] text-slate-400 bg-slate-700/50 px-2 py-0.5 rounded flex-shrink-0">
                    {(job.temp_id || '').substring(0, 8)}
                  </span>

                  {/* Status */}
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full flex-shrink-0 ${STATUS_CLR[job.status] || 'bg-slate-700/30 text-slate-400 border border-slate-600/30'}`}>
                    {job.status || '—'}
                  </span>

                  {/* Result type */}
                  {job.result_type && (
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full flex-shrink-0 ${RESULT_CLR[job.result_type] || 'bg-slate-700/15 text-slate-400'}`}>
                      {job.result_type === 'cleaned_visible_logo' ? '✓ cleaned'
                        : job.result_type === 'cropped_export' ? '✂ cropped'
                        : job.result_type === 'original_passthrough_no_logo' ? '∅ no logo'
                        : job.result_type}
                    </span>
                  )}

                  {/* Method · preset */}
                  {job.method && (
                    <span className="text-[10px] text-slate-500 flex-shrink-0">
                      {job.method} · {job.preset}
                    </span>
                  )}

                  {/* Suitability */}
                  {job.suitability_level && (
                    <span className={`text-[10px] font-bold flex-shrink-0 ${SUIT_CLR[job.suitability_level] || 'text-slate-400'}`}>
                      suit:{job.suitability_level}
                    </span>
                  )}

                  {/* Warning flags */}
                  <div className="flex gap-1 flex-wrap flex-1 min-w-0">
                    {(job.warning_flags || []).map(f => (
                      <span key={f} className={`text-[9px] px-1.5 py-0.5 rounded-full ${FLAG_CLR[f] || 'bg-slate-600/20 text-slate-500'}`}>
                        {f}
                      </span>
                    ))}
                  </div>

                  {/* Time */}
                  <span className="text-[10px] text-slate-600 flex-shrink-0 ml-auto">
                    {job.created_at
                      ? new Date(job.created_at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                      : '—'}
                  </span>
                </div>

                {/* Inline detail panel */}
                {isOpen && (
                  <div className="border-t border-slate-700/30 px-4 py-4 space-y-4 animate-in fade-in duration-150">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">

                      {/* Preview frame + region overlay */}
                      <div>
                        <p className="text-[10px] font-bold text-slate-500 uppercase mb-2">Preview Frame</p>
                        {job.has_preview ? (
                          <div className="relative rounded-xl overflow-hidden border border-slate-700/30 bg-slate-900/50">
                            <img
                              src={`/api/v1/flow-cleanup/frame/${job.temp_id}`}
                              alt="frame"
                              className="w-full h-auto block"
                              onError={e => { e.currentTarget.style.display = 'none'; }}
                            />
                            {job.region && job.video_info?.width && (
                              <div
                                className="absolute border-2 border-[#FB923C] bg-[#FB923C]/20 pointer-events-none"
                                style={{
                                  left:   `${(job.region.x / job.video_info.width)  * 100}%`,
                                  top:    `${(job.region.y / job.video_info.height) * 100}%`,
                                  width:  `${(job.region.w / job.video_info.width)  * 100}%`,
                                  height: `${(job.region.h / job.video_info.height) * 100}%`,
                                }}
                              />
                            )}
                            {job.preset && (
                              <div className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-[#FB923C] font-semibold">
                                {job.preset}
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="flex items-center justify-center h-24 rounded-xl bg-slate-800/40 border border-slate-700/30 text-slate-600 text-xs">
                            Preview không còn
                          </div>
                        )}
                      </div>

                      {/* Metadata */}
                      <div className="space-y-4">

                        {/* Job fields */}
                        <div>
                          <p className="text-[10px] font-bold text-slate-500 uppercase mb-2">Job Metadata</p>
                          <div className="space-y-1.5">
                            {[
                              ['job_kind',          job.job_kind],
                              ['result_type',       job.result_type],
                              ['selected_mode',     job.selected_mode],
                              ['requested_mode',    job.requested_mode],
                              ['region_source',     job.region_source],
                              ['suitability_level', job.suitability_level],
                              ['suitability_outcome', job.suitability_outcome],
                              ['method',            job.method],
                              ['preset',            job.preset],
                              ['retry_count',       job.retry_count != null ? String(job.retry_count) : null],
                              ['error_code',        job.error_code],
                              ['output_size',       job.output_file_size_mb ? `${job.output_file_size_mb} MB` : null],
                            ].filter(([, v]) => v != null && v !== '').map(([k, v]) => (
                              <div key={k} className="flex gap-2 text-xs">
                                <span className="text-slate-500 w-28 flex-shrink-0">{k}</span>
                                <span className="text-slate-300 font-mono break-all">{v}</span>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Video info */}
                        {job.video_info && (
                          <div>
                            <p className="text-[10px] font-bold text-slate-500 uppercase mb-2">Video</p>
                            <div className="space-y-1 text-xs">
                              <div className="flex gap-2">
                                <span className="text-slate-500 w-28">resolution</span>
                                <span className="text-slate-300 font-mono">{job.video_info.width}×{job.video_info.height}</span>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-slate-500 w-28">duration / fps</span>
                                <span className="text-slate-300 font-mono">{job.video_info.duration}s · {job.video_info.fps}fps</span>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-slate-500 w-28">upload size</span>
                                <span className="text-slate-300 font-mono">{job.file_size_mb} MB</span>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Region coords */}
                        {job.region && (
                          <div>
                            <p className="text-[10px] font-bold text-slate-500 uppercase mb-1">Region (px)</p>
                            <p className="text-xs text-slate-300 font-mono">
                              x:{job.region.x} y:{job.region.y} w:{job.region.w} h:{job.region.h}
                            </p>
                          </div>
                        )}

                        {/* Warning flags */}
                        {job.warning_flags?.length > 0 && (
                          <div>
                            <p className="text-[10px] font-bold text-slate-500 uppercase mb-1.5">Warning Flags</p>
                            <div className="flex flex-wrap gap-1">
                              {job.warning_flags.map(f => (
                                <span key={f} className={`text-[10px] px-2 py-0.5 rounded-full ${FLAG_CLR[f] || 'bg-slate-600/20 text-slate-400'}`}>
                                  {f}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Error */}
                        {(job.error || job.error_code) && (
                          <div className="px-3 py-2.5 rounded-xl bg-red-500/10 border border-red-500/20 space-y-1">
                            <p className="text-[10px] font-bold text-red-400">Error</p>
                            {job.error_code && (
                              <p className="text-[10px] font-mono text-red-300 font-bold">{job.error_code}</p>
                            )}
                            {job.error && (
                              <p className="text-xs text-red-300/70 font-mono break-all leading-snug">{job.error}</p>
                            )}
                          </div>
                        )}

                        {/* Timestamps */}
                        <div className="text-[10px] text-slate-600 space-y-0.5">
                          {job.created_at           && <p>created: {new Date(job.created_at).toLocaleString('vi-VN')}</p>}
                          {job.processed_at         && <p>processed: {new Date(job.processed_at).toLocaleString('vi-VN')}</p>}
                          {job.no_logo_confirmed_at && <p>no-logo confirmed: {new Date(job.no_logo_confirmed_at).toLocaleString('vi-VN')}</p>}
                          {job.failed_at            && <p>failed: {new Date(job.failed_at).toLocaleString('vi-VN')}</p>}
                        </div>

                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


// ═════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═════════════════════════════════════════════════════════════════════

export default function AdminDashboard({ token, onLogout }) {
  // Memoize adminFetch so useCallback deps don't thrash
  const adminFetch = useCallback(makeAdminFetch(token), [token]);
  const [stats, setStats]           = useState(null);
  const [analytics, setAnalytics]   = useState(null);
  const [activeJobs, setActiveJobs] = useState(null);
  const [errors, setErrors]         = useState(null);
  const [users, setUsers]           = useState(null);
  const [health, setHealth]         = useState(null);
  const [ytHealth, setYtHealth]     = useState(null);
  const [flowQA, setFlowQA]         = useState(null);
  const [platformDeep, setPlatformDeep] = useState(null);
  const [userDeep, setUserDeep]         = useState(null);
  const [revenueData, setRevenueData]   = useState(null);
  const [surfaceData, setSurfaceData]   = useState(null);
  const [userDetailId, setUserDetailId] = useState('');
  const [userDetailData, setUserDetailData] = useState(null);
  const [userDetailLoading, setUserDetailLoading] = useState(false);
  const [analyticsDays, setAnalyticsDays] = useState(7);
  const [loading, setLoading]       = useState(true);
  const [activeTab, setActiveTab]   = useState('overview');
  const [chartDays, setChartDays]   = useState(7);
  const [testingNotif, setTestingNotif] = useState(false);
  const [notifResult, setNotifResult]   = useState(null);

  const fetchStats = useCallback(async () => {
    try {
      const res = await adminFetch('/stats');
      if (res.status === 401 || res.status === 403) {
        // Token expired — clear stale session and go back to login
        sessionStorage.removeItem('admin_token');
        onLogout?.();
        return;
      }
      const data = await res.json();
      if (data.success) setStats(data);
      else setStats({ _error: true });
    } catch (e) {
      console.error('Stats fetch failed', e);
      setStats({ _error: true });
    }
    finally { setLoading(false); }
  }, [adminFetch, onLogout]);

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

  const fetchYtHealth = useCallback(async () => {
    try {
      const res = await adminFetch('/youtube-health');
      if (res.ok) {
        const data = await res.json();
        setYtHealth(data);
      }
    } catch (e) { console.error('YT health fetch failed', e); }
  }, [adminFetch]);

  const fetchFlowQA = useCallback(async () => {
    try {
      const res = await adminFetch('/flow-cleanup/jobs');
      const data = await res.json();
      setFlowQA(data);
    } catch (e) { console.error('FlowQA fetch failed', e); }
  }, []);

  const fetchPlatformDeep = useCallback(async (days = 7) => {
    try {
      const res = await adminFetch(`/platform-analytics?days=${days}`);
      const data = await res.json();
      if (data.success) setPlatformDeep(data);
    } catch (e) { console.error('Platform deep fetch failed', e); }
  }, [adminFetch]);

  const fetchUserDeep = useCallback(async (days = 7) => {
    try {
      const res = await adminFetch(`/user-analytics-deep?days=${days}`);
      const data = await res.json();
      if (data.success) setUserDeep(data);
    } catch (e) { console.error('User deep fetch failed', e); }
  }, [adminFetch]);

  const fetchRevenue = useCallback(async (days = 7) => {
    try {
      const res = await adminFetch(`/revenue-signals?days=${days}`);
      const data = await res.json();
      if (data.success) setRevenueData(data);
    } catch (e) { console.error('Revenue fetch failed', e); }
  }, [adminFetch]);

  const fetchSurface = useCallback(async (days = analyticsDays) => {
    const r = await adminFetch(`/surface-breakdown?days=${days}`);
    const d = await r.json();
    setSurfaceData(d);
  }, [adminFetch, analyticsDays]);

  const fetchUserDetail = useCallback(async (uid) => {
    if (!uid) return;
    setUserDetailLoading(true);
    setUserDetailData(null);
    const r = await adminFetch(`/user-detail/${uid}?days=30`);
    const d = await r.json();
    setUserDetailData(d);
    setUserDetailLoading(false);
  }, [adminFetch]);

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
    if (activeTab === 'health') { if (!health) fetchHealth(); fetchYtHealth(); }
    if (activeTab === 'flowqa') fetchFlowQA();
    if (activeTab === 'platform-deep') fetchPlatformDeep(analyticsDays);
    if (activeTab === 'user-deep') fetchUserDeep(analyticsDays);
    if (activeTab === 'revenue') fetchRevenue(analyticsDays);
    if (activeTab === 'surface' && !surfaceData) fetchSurface();
  }, [activeTab, errors, users, health, fetchErrors, fetchUsers, fetchHealth, fetchFlowQA,
      fetchPlatformDeep, fetchUserDeep, fetchRevenue, fetchSurface, surfaceData, analyticsDays]);

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
    if (activeTab === 'health') { fetchHealth(); fetchYtHealth(); }
    if (activeTab === 'flowqa') fetchFlowQA();
  };

  if (loading) {
    return <div className="p-8 text-slate-400 flex gap-2"><Activity className="animate-pulse" /> Đang tải Admin Center...</div>;
  }

  if (!stats || stats._error || stats._auth_failed) {
    const isAuth = stats?._auth_failed;
    return (
      <div className="p-8 flex flex-col items-center gap-4 text-center">
        <Shield className="w-10 h-10 text-red-400" />
        <p className="text-slate-300 font-semibold">
          {isAuth ? 'Phiên đăng nhập đã hết hạn' : 'Không thể tải Admin Center'}
        </p>
        <p className="text-slate-500 text-sm">
          {isAuth
            ? 'Token hết hiệu lực (8 giờ). Vui lòng đăng nhập lại.'
            : 'Lỗi kết nối tới server. Thử lại hoặc đăng nhập lại.'}
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => { setStats(null); setLoading(true); fetchStats(); }}
            className="px-4 py-2 rounded-xl bg-slate-700/40 text-slate-300 text-sm font-bold border border-slate-600/50 hover:bg-slate-700 transition-colors cursor-pointer"
          >
            Thử lại
          </button>
          <button
            onClick={() => { sessionStorage.removeItem('admin_token'); onLogout?.(); }}
            className="px-4 py-2 rounded-xl bg-[#FBBF24]/20 text-[#FBBF24] text-sm font-bold border border-[#FBBF24]/30 hover:bg-[#FBBF24]/30 transition-colors cursor-pointer"
          >
            Đăng nhập lại
          </button>
        </div>
      </div>
    );
  }

  const scraperAPIcredits = stats.providers?.ScraperAPI ?? stats.providers?.scraperapi ?? 0;
  const successRate = analytics?.summary?.success_rate ?? 0;
  const totalActive = (activeJobs?.processing_count || 0) + (activeJobs?.pending_count || 0);

  const tabs = [
    { id: 'overview', label: 'Tổng Quan',       icon: BarChart3 },
    { id: 'analytics', label: 'Phân Tích',       icon: TrendingUp },
    { id: 'platform-deep', label: 'Nền Tảng',   icon: Globe },
    { id: 'user-deep',  label: 'User Depth',     icon: Users },
    { id: 'revenue',    label: 'Revenue',        icon: TrendingUp },
    { id: 'surface',   label: 'Nguồn Surface',   icon: Layers },
    { id: 'user-detail', label: 'User Detail',   icon: UserSearch },
    { id: 'errors',    label: 'Lỗi & Cảnh Báo',  icon: Bug },
    { id: 'users',     label: 'Người Dùng',       icon: Users },
    { id: 'health',    label: 'Hệ Thống',         icon: Server },
    { id: 'ops',       label: 'Ops Signals',       icon: Activity },
    { id: 'ytgate',    label: 'YouTube',          icon: Play },
    { id: 'apikeys',   label: 'API & Credits',    icon: Key },
    { id: 'cookies',   label: 'Cookie Pool',      icon: Key },
    { id: 'flowqa',    label: 'Flow QA',          icon: Wand2 },
    { id: 'anomaly',   label: 'Bất Thường',        icon: AlertTriangle },
    { id: 'queue',     label: 'Hàng Đợi',          icon: Activity },
    { id: 'playbooks', label: 'Playbooks',          icon: Zap },
    { id: 'autohistory', label: 'Tự Động Hóa',     icon: Clock },
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
          {onLogout && (
            <button
              onClick={() => { if (confirm('Đăng xuất Admin?')) onLogout(); }}
              className="flex items-center gap-1.5 px-3 py-2 text-xs font-bold rounded-xl bg-red-900/20 text-red-400 border border-red-700/30 hover:bg-red-900/40 transition-colors cursor-pointer"
              title="Đăng xuất Admin"
            >
              <LogOut className="w-3.5 h-3.5" />
              Đăng xuất
            </button>
          )}
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

              {/* YouTube Extraction Health */}
              <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Activity className="w-4 h-4 text-red-400" /> YouTube Extraction Health
                  </h3>
                  <button onClick={fetchYtHealth} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700/50 transition-colors cursor-pointer">
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                </div>
                {ytHealth ? (
                  <div className="space-y-3">
                    {/* Layer table */}
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-slate-700/40">
                            <th className="text-left text-slate-400 font-semibold pb-2">Layer</th>
                            <th className="text-left text-slate-400 font-semibold pb-2">Status</th>
                            <th className="text-left text-slate-400 font-semibold pb-2">Last Checked</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(ytHealth.layers || []).map(layer => (
                            <tr key={layer.name} className="border-b border-slate-700/20 last:border-0">
                              <td className="py-2 text-slate-300 font-mono">{layer.name}</td>
                              <td className="py-2">
                                {layer.healthy === null || layer.healthy === undefined ? (
                                  <span className="text-slate-500">Unknown</span>
                                ) : layer.healthy ? (
                                  <span className="flex items-center gap-1.5 text-emerald-400">
                                    <CheckCircle className="w-3.5 h-3.5" /> Healthy
                                  </span>
                                ) : (
                                  <span className="flex items-center gap-1.5 text-red-400">
                                    <XCircle className="w-3.5 h-3.5" /> Unhealthy
                                  </span>
                                )}
                              </td>
                              <td className="py-2 text-slate-500">{layer.last_checked || '—'}</td>
                            </tr>
                          ))}
                          {/* Oracle Circuit row */}
                          <tr className="border-b border-slate-700/20 last:border-0">
                            <td className="py-2 text-slate-300 font-mono">Oracle Circuit</td>
                            <td className="py-2">
                              {ytHealth.oracle_circuit?.state === 'closed' ? (
                                <span className="flex items-center gap-1.5 text-emerald-400"><CheckCircle className="w-3.5 h-3.5" /> CLOSED</span>
                              ) : ytHealth.oracle_circuit?.state === 'open' ? (
                                <span className="flex items-center gap-1.5 text-red-400"><XCircle className="w-3.5 h-3.5" /> OPEN ({Math.round(ytHealth.oracle_circuit?.seconds_until_half || 0)}s to probe)</span>
                              ) : ytHealth.oracle_circuit?.state === 'half' ? (
                                <span className="flex items-center gap-1.5 text-amber-400"><AlertTriangle className="w-3.5 h-3.5" /> HALF (probing)</span>
                              ) : (
                                <span className="text-slate-500">Unknown</span>
                              )}
                            </td>
                            <td className="py-2 text-slate-500">realtime</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    {/* Snapshot age */}
                    {ytHealth.snapshot_age_s != null && (
                      <p className="text-[10px] text-slate-500">
                        Snapshot: {ytHealth.snapshot_age_s < 60 ? `${ytHealth.snapshot_age_s}s` : `${Math.round(ytHealth.snapshot_age_s / 60)}m`} ago
                        {ytHealth.all_healthy === false && (
                          <span className="ml-2 text-amber-400 font-semibold">⚠ One or more layers unhealthy</span>
                        )}
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500 animate-pulse">Đang tải YouTube health...</p>
                )}
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
      {activeTab === 'apikeys' && <ApiKeysTab scraperAPIcredits={scraperAPIcredits} stats={stats} adminFetch={adminFetch} />}

      {activeTab === 'cookies' && <CookiePoolTab adminFetch={adminFetch} />}

      {/* ══ Phase 13: PLATFORM DEEP ANALYTICS ══ */}
      {activeTab === 'platform-deep' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white flex items-center gap-2"><Globe className="w-4 h-4 text-indigo-400" /> Phân Tích Sâu Theo Nền Tảng</h3>
            <div className="flex items-center gap-2">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => { setAnalyticsDays(d); fetchPlatformDeep(d); }}
                  className={`text-xs px-3 py-1 rounded-lg font-semibold transition cursor-pointer ${analyticsDays === d ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40' : 'bg-slate-800 text-slate-400 border border-slate-700 hover:border-slate-500'}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
          {!platformDeep ? (
            <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-slate-500" /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-700/50">
                    {['Nền tảng', 'Tổng', 'Thành công', 'Thất bại', 'Tỷ lệ OK', 'TB size (MB)', 'Retry %', 'Top lỗi'].map(h => (
                      <th key={h} className="text-left py-2 px-3 text-slate-400 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(platformDeep.platforms || []).map(p => (
                    <tr key={p.platform} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="py-2 px-3 font-bold text-slate-200">{p.platform}</td>
                      <td className="py-2 px-3 text-slate-300">{p.total}</td>
                      <td className="py-2 px-3 text-emerald-400">{p.success}</td>
                      <td className="py-2 px-3 text-red-400">{p.failed}</td>
                      <td className="py-2 px-3">
                        <span className={`font-bold ${p.success_rate >= 90 ? 'text-emerald-400' : p.success_rate >= 70 ? 'text-amber-400' : 'text-red-400'}`}>{p.success_rate}%</span>
                      </td>
                      <td className="py-2 px-3 text-slate-300">{p.avg_file_size_mb}</td>
                      <td className="py-2 px-3 text-slate-300">{p.retry_rate}%</td>
                      <td className="py-2 px-3 text-slate-400 max-w-[200px] truncate" title={p.top_errors?.map(e => `${e.msg} (${e.count})`).join(' | ')}>
                        {p.top_errors?.slice(0,2).map(e => e.msg.slice(0,30)).join(' | ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ══ Phase 13: USER DEEP ANALYTICS ══ */}
      {activeTab === 'user-deep' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white flex items-center gap-2"><Users className="w-4 h-4 text-cyan-400" /> Phân Tích Sâu Người Dùng</h3>
            <div className="flex items-center gap-2">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => { setAnalyticsDays(d); fetchUserDeep(d); }}
                  className={`text-xs px-3 py-1 rounded-lg font-semibold transition cursor-pointer ${analyticsDays === d ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40' : 'bg-slate-800 text-slate-400 border border-slate-700 hover:border-slate-500'}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
          {!userDeep ? (
            <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-slate-500" /></div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                <h4 className="text-xs font-bold text-slate-300 mb-3 flex items-center gap-2"><BarChart2 className="w-3.5 h-3.5" /> Phân bổ Tier</h4>
                {(userDeep.tier_distribution || []).map(t => (
                  <div key={t.tier} className="flex items-center justify-between py-1.5 border-b border-slate-800/50 last:border-0">
                    <span className={`text-xs font-semibold ${t.tier === 'pro' ? 'text-amber-400' : 'text-slate-300'}`}>{t.tier.toUpperCase()}</span>
                    <span className="text-xs text-slate-400">{t.count} users</span>
                  </div>
                ))}
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4 lg:col-span-2">
                <h4 className="text-xs font-bold text-slate-300 mb-3 flex items-center gap-2"><Flag className="w-3.5 h-3.5 text-red-400" /> Người Dùng Bất Thường ({userDeep.suspicious_users?.length || 0})</h4>
                {(userDeep.suspicious_users || []).slice(0, 10).map(u => (
                  <div key={u.user_id} className="flex items-center justify-between py-1.5 border-b border-slate-800/50 last:border-0 text-xs">
                    <span className="text-slate-400 font-mono truncate max-w-[120px]">{u.user_id.slice(0,12)}…</span>
                    <span className="text-slate-300">{u.total_jobs} jobs</span>
                    <span className={u.fail_rate >= 50 ? 'text-red-400' : 'text-slate-400'}>{u.fail_rate}% fail</span>
                    <span className="text-amber-400">{u.flags.join(', ')}</span>
                  </div>
                ))}
                {!userDeep.suspicious_users?.length && <p className="text-xs text-slate-500 text-center py-4">Không có người dùng bất thường</p>}
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4 lg:col-span-3">
                <h4 className="text-xs font-bold text-slate-300 mb-3">Top {userDeep.top_users?.length || 0} Người Dùng ({userDeep.days || analyticsDays}d)</h4>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead><tr className="border-b border-slate-700/50">
                      {['User ID', 'Jobs', 'Thành công', 'Thất bại', 'Fail %', 'Top Platform', 'Flags'].map(h => (
                        <th key={h} className="text-left py-1.5 px-2 text-slate-400 font-semibold">{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>
                      {(userDeep.top_users || []).slice(0, 20).map(u => (
                        <tr key={u.user_id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                          <td className="py-1.5 px-2 font-mono text-slate-400">{u.user_id.slice(0,10)}…</td>
                          <td className="py-1.5 px-2 text-slate-300">{u.total_jobs}</td>
                          <td className="py-1.5 px-2 text-emerald-400">{u.success}</td>
                          <td className="py-1.5 px-2 text-red-400">{u.failed}</td>
                          <td className="py-1.5 px-2"><span className={u.fail_rate >= 50 ? 'text-red-400 font-bold' : 'text-slate-400'}>{u.fail_rate}%</span></td>
                          <td className="py-1.5 px-2 text-slate-300">{u.top_platforms?.[0]?.platform || '—'}</td>
                          <td className="py-1.5 px-2">{u.flags.map(f => <span key={f} className="text-amber-400 text-[9px] bg-amber-400/10 px-1 py-0.5 rounded mr-0.5">{f}</span>)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ══ Phase 13: REVENUE SIGNALS ══ */}
      {activeTab === 'revenue' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white flex items-center gap-2"><TrendingUp className="w-4 h-4 text-amber-400" /> Revenue Signals</h3>
            <div className="flex items-center gap-2">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => { setAnalyticsDays(d); fetchRevenue(d); }}
                  className={`text-xs px-3 py-1 rounded-lg font-semibold transition cursor-pointer ${analyticsDays === d ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40' : 'bg-slate-800 text-slate-400 border border-slate-700 hover:border-slate-500'}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
          {!revenueData ? (
            <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-slate-500" /></div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                <p className="text-xs text-slate-400 mb-1">Paywall Hits ({revenueData.days}d)</p>
                <p className="text-2xl font-extrabold text-red-400">{revenueData.paywall_hits}</p>
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                <p className="text-xs text-slate-400 mb-1">Pro Users</p>
                <p className="text-2xl font-extrabold text-amber-400">{revenueData.tier_summary?.pro_users || 0}</p>
                <p className="text-xs text-slate-500 mt-1">Conversion: {revenueData.tier_summary?.conversion_rate_pct || 0}%</p>
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                <p className="text-xs text-slate-400 mb-1">Total Users</p>
                <p className="text-2xl font-extrabold text-slate-200">{revenueData.tier_summary?.total_users || 0}</p>
                <p className="text-xs text-slate-500 mt-1">Free: {revenueData.tier_summary?.free_users || 0}</p>
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4 sm:col-span-2">
                <h4 className="text-xs font-bold text-slate-300 mb-3">Feature Triggers (kích thích upgrade)</h4>
                {(revenueData.feature_triggers || []).map(f => (
                  <div key={f.feature} className="flex items-center gap-3 mb-2">
                    <span className="text-xs text-slate-400 w-28 capitalize">{f.feature.replace('_', ' ')}</span>
                    <div className="flex-1 h-2 bg-slate-800 rounded-full overflow-hidden">
                      <div className="h-full bg-amber-400/70 rounded-full transition-all duration-500"
                        style={{width: `${Math.min(100, (f.count / Math.max(...(revenueData.feature_triggers || []).map(x => x.count), 1)) * 100)}%`}} />
                    </div>
                    <span className="text-xs text-slate-300 w-12 text-right">{f.count}</span>
                  </div>
                ))}
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                <h4 className="text-xs font-bold text-slate-300 mb-3">Source Surface</h4>
                {(revenueData.source_distribution || []).map(s => (
                  <div key={s.source} className="flex items-center justify-between py-1.5 border-b border-slate-800/50 last:border-0">
                    <span className="text-xs text-slate-300 capitalize">{s.source}</span>
                    <span className="text-xs text-slate-400">{s.count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ══ Phase 14: SURFACE BREAKDOWN ══ */}
      {activeTab === 'surface' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white flex items-center gap-2"><Layers className="w-4 h-4 text-purple-400" /> Surface Breakdown (nguồn tải)</h3>
            <div className="flex gap-2">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => { setAnalyticsDays(d); fetchSurface(d); }}
                  className={`text-xs px-3 py-1 rounded-lg font-semibold transition cursor-pointer ${analyticsDays === d ? 'bg-purple-500/20 text-purple-300 border border-purple-500/40' : 'bg-slate-800 text-slate-400 border border-slate-700 hover:border-slate-500'}`}>
                  {d}d
                </button>
              ))}
            </div>
          </div>
          {!surfaceData ? (
            <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-slate-500" /></div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {(surfaceData.surfaces || []).map(s => (
                <div key={s.source} className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-bold text-white capitalize">{s.source.replace('_', ' ')}</span>
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${s.success_rate >= 90 ? 'bg-emerald-500/20 text-emerald-400' : s.success_rate >= 70 ? 'bg-amber-500/20 text-amber-400' : 'bg-red-500/20 text-red-400'}`}>{s.success_rate}% OK</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs text-center">
                    <div><p className="text-slate-400">Tổng</p><p className="font-bold text-slate-200">{s.total}</p></div>
                    <div><p className="text-slate-400">OK</p><p className="font-bold text-emerald-400">{s.success}</p></div>
                    <div><p className="text-slate-400">Lỗi</p><p className="font-bold text-red-400">{s.failed}</p></div>
                  </div>
                  {surfaceData.event_by_source?.[s.source] && (
                    <div className="mt-3 pt-3 border-t border-slate-700/50">
                      <p className="text-[10px] text-slate-500 mb-1">Top events:</p>
                      {Object.entries(surfaceData.event_by_source[s.source]).slice(0, 3).map(([en, cnt]) => (
                        <div key={en} className="flex justify-between text-[10px] py-0.5">
                          <span className="text-slate-400">{en}</span>
                          <span className="text-slate-300">{cnt}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ══ Phase 14: USER DETAIL PANEL ══ */}
      {activeTab === 'user-detail' && (
        <div className="space-y-4">
          <h3 className="text-sm font-bold text-white flex items-center gap-2"><UserSearch className="w-4 h-4 text-rose-400" /> User Detail Panel</h3>
          <div className="flex gap-2">
            <input
              type="text"
              value={userDetailId}
              onChange={e => setUserDetailId(e.target.value)}
              placeholder="Paste user_id (UUID)..."
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-rose-500"
              onKeyDown={e => e.key === 'Enter' && fetchUserDetail(userDetailId)}
            />
            <button onClick={() => fetchUserDetail(userDetailId)}
              className="px-4 py-2 bg-rose-500/20 text-rose-300 border border-rose-500/40 rounded-lg text-xs font-semibold hover:bg-rose-500/30 transition cursor-pointer">
              Tìm kiếm
            </button>
          </div>
          {userDetailLoading && <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-slate-500" /></div>}
          {userDetailData?.success && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4">
                <h4 className="text-xs font-bold text-slate-300 mb-3">Profile</h4>
                <div className="space-y-2 text-xs">
                  <div className="flex justify-between"><span className="text-slate-400">Email</span><span className="text-slate-200 truncate max-w-[130px]">{userDetailData.profile?.email || '—'}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Tier</span><span className={userDetailData.profile?.tier === 'pro' ? 'text-amber-400 font-bold' : 'text-slate-300'}>{(userDetailData.profile?.tier || 'free').toUpperCase()}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Downloads today</span><span className="text-slate-200">{userDetailData.profile?.downloads_today ?? '—'}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">API Keys</span><span className="text-slate-200">{userDetailData.api_keys_count}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Telegram</span><span className={userDetailData.telegram_linked ? 'text-emerald-400' : 'text-slate-500'}>{userDetailData.telegram_linked ? 'Đã link' : 'Chưa link'}</span></div>
                </div>
                <div className="mt-4 pt-3 border-t border-slate-700/50 space-y-2">
                  <p className="text-[10px] text-slate-500 mb-2">One-click Actions</p>
                  {[
                    { label: 'Reset Quota', action: 'reset_quota', color: 'text-blue-400 border-blue-500/40 hover:bg-blue-500/10' },
                    { label: 'Upgrade → Pro', action: 'set_tier', params: { tier: 'pro' }, color: 'text-amber-400 border-amber-500/40 hover:bg-amber-500/10' },
                    { label: 'Downgrade → Free', action: 'set_tier', params: { tier: 'free' }, color: 'text-slate-400 border-slate-600 hover:bg-slate-700' },
                    { label: 'Revoke API Keys', action: 'revoke_api_keys', color: 'text-red-400 border-red-500/40 hover:bg-red-500/10' },
                    { label: 'Retry Failed Jobs', action: 'retry_failed_jobs', color: 'text-emerald-400 border-emerald-500/40 hover:bg-emerald-500/10' },
                  ].map(btn => (
                    <button key={btn.action}
                      onClick={async () => {
                        try {
                          const r = await adminFetch('/user-action', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ action: btn.action, user_id: userDetailData.user_id, params: btn.params || {} }),
                          });
                          const d = await r.json();
                          alert(d.success ? `✅ ${btn.action} done` : `❌ ${d.error}`);
                          if (d.success) fetchUserDetail(userDetailData.user_id);
                        } catch(e) { alert('Error: ' + e.message); }
                      }}
                      className={`w-full px-3 py-1.5 text-[11px] font-semibold rounded-lg border transition cursor-pointer ${btn.color}`}>
                      {btn.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="bg-[#0a1a14] rounded-xl border border-slate-700/50 p-4 lg:col-span-2">
                <h4 className="text-xs font-bold text-slate-300 mb-3">Jobs (30 ngày) — {userDetailData.stats?.total_jobs} total / {userDetailData.stats?.fail_rate}% fail</h4>
                <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-[#0a1a14]">
                      <tr className="border-b border-slate-700/50">
                        {['Platform','Source','Status','Size','Khi','Lỗi'].map(h => (
                          <th key={h} className="text-left py-1.5 px-2 text-slate-400 font-semibold">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(userDetailData.recent_jobs || []).map(j => (
                        <tr key={j.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                          <td className="py-1 px-2 text-slate-300">{j.platform || '—'}</td>
                          <td className="py-1 px-2 text-slate-400">{j.source || 'web'}</td>
                          <td className="py-1 px-2"><span className={j.status === 'success' ? 'text-emerald-400' : 'text-red-400'}>{j.status}</span></td>
                          <td className="py-1 px-2 text-slate-400">{j.file_size_mb ? `${j.file_size_mb}MB` : '—'}</td>
                          <td className="py-1 px-2 text-slate-500">{new Date(j.created_at).toLocaleDateString('vi')}</td>
                          <td className="py-1 px-2 text-slate-500 max-w-[120px] truncate" title={j.error_message}>{j.error_message?.slice(0,40) || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
          {userDetailData && !userDetailData.success && (
            <p className="text-red-400 text-xs text-center py-4">Không tìm thấy user hoặc lỗi: {userDetailData.error}</p>
          )}
        </div>
      )}

      {/* ══ TAB: FLOW/VEO CLEANUP QA ══ */}
      {activeTab === 'flowqa' && <FlowQATab data={flowQA} onRefresh={fetchFlowQA} />}

      {/* ══ Phase 12: ANOMALY DETECTION ══ */}
      {activeTab === 'anomaly' && <AnomalyPanel adminToken={token || sessionStorage.getItem('admin_token') || ''} />}

      {/* ══ Phase 12: QUEUE HEALTH ══ */}
      {activeTab === 'queue' && <QueueHealthPanel adminToken={token || sessionStorage.getItem('admin_token') || ''} />}

      {/* ══ Phase 12: RECOVERY PLAYBOOKS ══ */}
      {activeTab === 'playbooks' && <PlaybooksPanel adminToken={token || sessionStorage.getItem('admin_token') || ''} />}

      {/* ══ Phase 12: AUTOMATION HISTORY ══ */}
      {activeTab === 'autohistory' && <AutomationHistoryPanel adminToken={token || sessionStorage.getItem('admin_token') || ''} />}

      {/* ══ Phase 8: YOUTUBE PROXY GATE ══ */}
      {activeTab === 'ytgate' && <YouTubeGatePanel adminToken={token || sessionStorage.getItem('admin_token') || ''} />}

      {/* ══ OPS SIGNALS ══ */}
      {activeTab === 'ops' && <OpsPanel adminToken={token || sessionStorage.getItem('admin_token') || ''} adminFetch={adminFetch} />}
    </div>
  );
}

// ── API Keys Tab ────────────────────────────────────────────────────
function ApiKeysTab({ scraperAPIcredits, stats, adminFetch: adminFetchProp }) {
  const adminFetch = adminFetchProp || makeAdminFetch('');
  const [keyPool, setKeyPool]       = useState(null);
  const [loading, setLoading]       = useState(false);
  const [rotating, setRotating]     = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [adding, setAdding]         = useState(false);
  const [newKey, setNewKey]         = useState('');
  const [showAdd, setShowAdd]       = useState(false);
  const [msg, setMsg]               = useState('');

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 5000); };

  const fetchPool = async (force = false) => {
    setLoading(true);
    try {
      const path = force ? '/scraperapi/refresh-credits' : '/scraperapi/keys';
      const r = await adminFetch(path, { method: force ? 'POST' : 'GET' });
      const d = await r.json();
      if (d.success) setKeyPool(d);
    } catch (e) { flash(`❌ ${e.message}`); }
    finally { setLoading(false); setRefreshing(false); }
  };

  const addKey = async () => {
    if (!newKey.trim()) return;
    setAdding(true);
    try {
      const r = await adminFetch('/scraperapi/keys/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: newKey.trim() }),
      });
      const d = await r.json();
      if (d.success) {
        flash(`✅ Đã thêm key ${d.key_prefix} — ${d.credits?.toLocaleString()} credits`);
        setNewKey(''); setShowAdd(false); fetchPool();
      } else {
        flash(`❌ ${d.detail || 'Thêm key thất bại'}`);
      }
    } catch (e) { flash(`❌ ${e.message}`); }
    finally { setAdding(false); }
  };

  const removeKey = async (index, prefix) => {
    if (!confirm(`Xóa key ${prefix} khỏi pool?`)) return;
    try {
      const r = await adminFetch(`/scraperapi/keys/remove?index=${index}`, { method: 'DELETE' });
      const d = await r.json();
      if (d.success) { flash(`🗑 Đã xóa key ${prefix}`); fetchPool(); }
      else flash(`❌ ${d.detail}`);
    } catch (e) { flash(`❌ ${e.message}`); }
  };

  const rotateKey = async () => {
    setRotating(true);
    try {
      const r = await adminFetch('/scraperapi/rotate', { method: 'POST' });
      const d = await r.json();
      if (d.success) { flash(`✅ Đã chuyển sang key tiếp theo (active: ${d.active_key_prefix})`); fetchPool(); }
    } catch (e) { flash(`❌ ${e.message}`); }
    finally { setRotating(false); }
  };

  useEffect(() => { fetchPool(); }, []);

  const totalCredits = keyPool?.total_credits ?? scraperAPIcredits;

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* ScraperAPI Key Pool */}
      <div className="p-5 bg-[#0a1a17] border border-slate-700/50 rounded-2xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-white flex items-center gap-2">
            <Key className="w-4 h-4 text-amber-400" /> ScraperAPI Key Pool
            {keyPool && <span className="text-xs font-normal text-slate-400">({keyPool.key_count} keys)</span>}
          </h3>
          <div className="flex items-center gap-2">
            <button onClick={() => setShowAdd(v => !v)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 transition-colors cursor-pointer">
              + Thêm key
            </button>
            <button onClick={rotateKey} disabled={rotating}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 hover:bg-amber-500/20 transition-colors cursor-pointer disabled:opacity-50">
              {rotating ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              Rotate
            </button>
            <button onClick={() => { setRefreshing(true); fetchPool(true); }} disabled={refreshing || loading}
              className="p-1.5 text-slate-400 hover:text-white transition-colors cursor-pointer disabled:opacity-50" title="Refresh credits">
              {refreshing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>

        {/* Add key form */}
        {showAdd && (
          <div className="mb-4 p-4 bg-[#012622] rounded-xl border border-emerald-500/20 animate-in fade-in duration-200">
            <p className="text-xs font-bold text-emerald-400 mb-2">Thêm ScraperAPI Key mới</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={newKey}
                onChange={e => setNewKey(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addKey()}
                placeholder="Paste API key tại đây..."
                className="flex-1 bg-[#0a1a17] border border-slate-600 text-white text-xs font-mono rounded-lg px-3 py-2 outline-none focus:border-emerald-500 transition-colors"
              />
              <button onClick={addKey} disabled={adding || !newKey.trim()}
                className="px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 transition-colors cursor-pointer disabled:opacity-50 flex items-center gap-1.5">
                {adding ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                {adding ? 'Đang kiểm tra...' : 'Thêm'}
              </button>
            </div>
            <p className="text-[10px] text-slate-500 mt-2">
              Key sẽ được xác minh trước khi thêm. Không cần sửa .env.
            </p>
          </div>
        )}

        {/* Total credits banner */}
        <div className="flex items-center gap-4 mb-4 p-3 bg-[#012622] rounded-xl border border-slate-700/30">
          <div>
            <p className="text-[10px] text-slate-400 uppercase font-bold mb-0.5">Tổng Credits</p>
            <p className={`text-3xl font-bold tracking-tight ${(totalCredits ?? 0) < 100 ? 'text-red-400' : 'text-white'}`}>
              {typeof totalCredits === 'number' ? totalCredits.toLocaleString() : '—'}
            </p>
          </div>
          {keyPool && (
            <div className="flex gap-4 ml-4">
              <div className="text-center">
                <p className="text-lg font-bold text-emerald-400">{keyPool.keys.filter(k => k.active).length}</p>
                <p className="text-[10px] text-slate-500">Active</p>
              </div>
              <div className="text-center">
                <p className="text-lg font-bold text-red-400">{keyPool.keys.filter(k => k.exhausted).length}</p>
                <p className="text-[10px] text-slate-500">Exhausted</p>
              </div>
              <div className="text-center">
                <p className="text-lg font-bold text-slate-300">{keyPool.key_count}</p>
                <p className="text-[10px] text-slate-500">Total</p>
              </div>
            </div>
          )}
        </div>

        {/* Key list */}
        {loading && !keyPool ? (
          <div className="flex items-center gap-2 text-slate-400 text-sm py-4"><Loader2 className="w-4 h-4 animate-spin" /> Đang tải...</div>
        ) : keyPool?.keys?.length > 0 ? (
          <div className="space-y-2">
            {keyPool.keys.map((k) => {
              const pct  = k.credits != null ? Math.min(100, Math.round(k.credits / 50)) : 0;
              const bar  = k.credits == null ? 'bg-slate-600' : k.credits < 50 ? 'bg-red-500' : k.credits < 500 ? 'bg-amber-500' : 'bg-emerald-500';
              return (
                <div key={k.index} className={`flex items-center gap-3 px-4 py-3 rounded-xl border transition-colors ${
                  k.active   ? 'bg-emerald-500/5 border-emerald-500/20' :
                  k.exhausted ? 'bg-red-500/5 border-red-500/20 opacity-60' :
                  'bg-[#012622]/50 border-slate-700/30'
                }`}>
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${k.active ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : k.exhausted ? 'bg-red-400' : 'bg-slate-500'}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-mono text-slate-300">{k.key_prefix}</span>
                      {k.active    && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-bold">ACTIVE</span>}
                      {k.exhausted && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/20 text-red-400 font-bold">EXHAUSTED</span>}
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-[#012622] rounded-full h-1.5 overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-500 ${bar}`} style={{ width: `${pct}%` }} />
                      </div>
                      <span className={`text-xs font-bold w-20 text-right ${k.credits == null ? 'text-slate-500' : k.credits < 50 ? 'text-red-400' : 'text-white'}`}>
                        {k.credits != null ? k.credits.toLocaleString() : '—'} credits
                      </span>
                    </div>
                  </div>
                  <button onClick={() => removeKey(k.index, k.key_prefix)}
                    className="text-xs text-red-400 hover:text-red-300 cursor-pointer transition-colors px-2 py-1 rounded hover:bg-red-500/10 flex-shrink-0">
                    Xóa
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-center py-8">
            <p className="text-sm text-slate-400 mb-2">Chưa có key nào trong pool.</p>
            <button onClick={() => setShowAdd(true)}
              className="text-xs text-emerald-400 underline cursor-pointer">
              Bấm "+ Thêm key" để bắt đầu
            </button>
          </div>
        )}

        {msg && (
          <div className={`mt-3 px-3 py-2 rounded-lg text-xs font-medium animate-in fade-in ${msg.startsWith('✅') || msg.startsWith('🗑') ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
            {msg}
          </div>
        )}
      </div>

      {/* IPRoyal */}
      <CreditCard provider="IPRoyal" credits="N/A" threshold={0} apiKey={stats.api_keys?.IPRoyal} isProxy />

      {/* Alerts info */}
      <div className="p-5 bg-[#0a1a17] border border-amber-500/20 rounded-2xl">
        <h3 className="text-sm font-bold mb-2 text-white flex items-center gap-2"><Bell className="w-4 h-4 text-amber-400" /> Cảnh Báo Tự Động</h3>
        <ul className="text-xs text-slate-400 space-y-1.5 ml-1">
          <li>📨 Telegram thông báo khi <b>batch download hoàn tất</b></li>
          <li>🚨 Telegram alert khi <b>job thất bại</b></li>
          <li>⚠️ Telegram cảnh báo khi <b>credits &lt; 50</b> (mỗi key)</li>
          <li>📊 Báo cáo ngày tự động lúc <b>6:00 AM (UTC+7)</b></li>
          <li>🔄 Kiểm tra credits tự động mỗi <b>6 giờ</b></li>
        </ul>
      </div>
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

function CookiePoolTab({ adminFetch: adminFetchProp }) {
  const adminFetch = adminFetchProp || makeAdminFetch('');
  const [activePlat, setActivePlat] = useState('youtube');
  const [poolStatus, setPoolStatus] = useState({});
  const [cookieList, setCookieList] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState('');
  const [dragging, setDragging] = useState(false);
  const [throwAccounts, setThrowAccounts] = useState(null);
  const [throwLoading, setThrowLoading] = useState(false);

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

  const fetchThrowaway = async () => {
    setThrowLoading(true);
    try {
      const r = await adminFetch('/throwaway/accounts');
      const d = await r.json();
      if (d.success) setThrowAccounts(d);
    } catch {}
    finally { setThrowLoading(false); }
  };

  useEffect(() => { fetchStatus(); fetchThrowaway(); }, []);
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
          <button onClick={fetchThrowaway} disabled={throwLoading} className="text-xs text-slate-400 hover:text-white cursor-pointer transition-colors disabled:opacity-50">
            {throwLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          </button>
        </div>

        {throwLoading && !throwAccounts && (
          <div className="flex items-center gap-2 text-slate-400 text-sm py-6 justify-center">
            <Loader2 className="w-4 h-4 animate-spin" /> Đang tải danh sách tài khoản...
          </div>
        )}

        {throwAccounts && (
          <>
            {/* Stats */}
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="bg-[#012622] rounded-xl p-3 text-center border border-slate-700/30">
                <p className="text-xl font-bold text-white">{throwAccounts.total}</p>
                <p className="text-[10px] text-slate-400 mt-0.5">Tổng</p>
              </div>
              <div className="bg-[#012622] rounded-xl p-3 text-center border border-emerald-500/20">
                <p className="text-xl font-bold text-emerald-400">{throwAccounts.success_count}</p>
                <p className="text-[10px] text-slate-400 mt-0.5">Thành công</p>
              </div>
              <div className="bg-[#012622] rounded-xl p-3 text-center border border-red-500/20">
                <p className="text-xl font-bold text-red-400">{throwAccounts.fail_count}</p>
                <p className="text-[10px] text-slate-400 mt-0.5">Thất bại</p>
              </div>
            </div>

            {throwAccounts.accounts.length === 0 ? (
              <div className="text-center py-8 text-slate-500 text-sm">
                Chưa có tài khoản nào được tạo.<br />
                <span className="text-xs">Chạy script <code className="text-violet-400 bg-violet-500/10 px-1 rounded">python create_throwaway.py</code> từ máy tính cá nhân.</span>
              </div>
            ) : (
              <div className="overflow-auto max-h-[400px]">
                <table className="w-full text-xs text-left">
                  <thead className="bg-[#012622] sticky top-0 text-[10px] uppercase text-slate-400">
                    <tr>
                      <th className="px-3 py-2">#</th>
                      <th className="px-3 py-2">Email</th>
                      <th className="px-3 py-2">Password</th>
                      <th className="px-3 py-2">Phone</th>
                      <th className="px-3 py-2">IP / Quốc gia</th>
                      <th className="px-3 py-2">Thời gian</th>
                      <th className="px-3 py-2 text-center">Trạng thái</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/30">
                    {throwAccounts.accounts.map((acc, i) => (
                      <tr key={i} className={`hover:bg-white/5 transition-colors ${acc.success === false ? 'opacity-60' : ''}`}>
                        <td className="px-3 py-2 text-slate-500">{i + 1}</td>
                        <td className="px-3 py-2 text-slate-200 font-mono truncate max-w-[160px]">{acc.email || '—'}</td>
                        <td className="px-3 py-2 text-slate-400 font-mono">{acc.password || '—'}</td>
                        <td className="px-3 py-2 text-slate-400">{acc.phone || '—'}</td>
                        <td className="px-3 py-2 text-slate-400">
                          {acc.exit_ip ? <span className="font-mono">{acc.exit_ip}</span> : ''}
                          {acc.exit_country ? <span className="ml-1 text-slate-500">({acc.exit_country})</span> : ''}
                          {!acc.exit_ip && !acc.exit_country ? '—' : ''}
                        </td>
                        <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{acc.created_at ? acc.created_at.slice(0, 16) : '—'}</td>
                        <td className="px-3 py-2 text-center">
                          {acc.success === true && <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400"><CheckCircle className="w-3 h-3" /> OK</span>}
                          {acc.success === false && <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-red-500/15 text-red-400"><XCircle className="w-3 h-3" /> Fail</span>}
                          {acc.success === null || acc.success === undefined ? <span className="text-slate-600">—</span> : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <p className="text-[10px] text-slate-500 mt-3">
              Tài khoản được tạo bằng script CLI từ máy tính cá nhân (home IP).
              Mật khẩu hiển thị đã ẩn 3 ký tự cuối.
            </p>
          </>
        )}

        {!throwLoading && !throwAccounts && (
          <div className="text-center py-8 text-slate-500 text-sm">
            <p>Không thể tải dữ liệu. Backend có thể chưa khởi động.</p>
          </div>
        )}
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

import { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Calendar, Clock, Plus, Trash2, Pause, Play, RefreshCw,
  AlertCircle, Check, X, Zap, Copy,
} from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';

const API = import.meta.env.VITE_API_URL || '';

const JOB_TYPE_LABELS   = { single: 'URL Đơn', channel: 'Kênh / Playlist', keyword: 'Từ Khóa' };
const SCHEDULE_LABELS   = { once: 'Một lần', daily: 'Hằng ngày', weekly: 'Hằng tuần' };
const WEEKDAY_LABELS    = ['Thứ Hai','Thứ Ba','Thứ Tư','Thứ Năm','Thứ Sáu','Thứ Bảy','Chủ Nhật'];
const DUP_LABELS        = { off: 'Không lọc trùng', url_24h: 'Bỏ qua URL 24h', canonical_id: 'Bỏ qua ID video' };

function fmtDatetime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('vi-VN', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function timeUntil(iso) {
  if (!iso) return null;
  const diff = new Date(iso) - Date.now();
  if (diff <= 0) return 'Sắp chạy';
  const h = Math.floor(diff / 3_600_000);
  const m = Math.floor((diff % 3_600_000) / 60_000);
  if (h > 24) return `${Math.floor(h / 24)} ngày nữa`;
  if (h > 0)  return `${h}h ${m}m nữa`;
  return `${m} phút nữa`;
}

// ── Create panel ─────────────────────────────────────────────────────────────
function CreatePanel({ collections, onCreated, onClose }) {
  const { session } = useAuth();
  const [jobType,    setJobType]    = useState('single');
  const [schedType,  setSchedType]  = useState('once');
  const [url,        setUrl]        = useState('');
  const [keyword,    setKeyword]    = useState('');
  const [kwPlatform, setKwPlatform] = useState('youtube');
  const [kwCount,    setKwCount]    = useState(5);
  const [runAt,      setRunAt]      = useState('02:00');
  const [weekday,    setWeekday]    = useState(0);
  const [nextRun,    setNextRun]    = useState('');
  const [dupMode,    setDupMode]    = useState('off');
  const [autoCollId, setAutoCollId] = useState('');
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState('');
  const [urlErr,     setUrlErr]     = useState('');

  // Auto-compute next_run_at
  useEffect(() => {
    const now = new Date();
    const [h, m] = (runAt || '00:00').split(':').map(Number);
    if (schedType === 'once') {
      const d = new Date(now);
      d.setUTCDate(d.getUTCDate() + 1);
      d.setUTCHours(h, m, 0, 0);
      setNextRun(d.toISOString().slice(0, 16));
    } else if (schedType === 'daily') {
      const d = new Date(now);
      d.setUTCHours(h, m, 0, 0);
      if (d <= now) d.setUTCDate(d.getUTCDate() + 1);
      setNextRun(d.toISOString().slice(0, 16));
    } else {
      const d = new Date(now);
      const diff = (weekday - d.getUTCDay() + 7) % 7 || 7;
      d.setUTCDate(d.getUTCDate() + diff);
      d.setUTCHours(h, m, 0, 0);
      setNextRun(d.toISOString().slice(0, 16));
    }
  }, [schedType, runAt, weekday]);

  function validateInput() {
    if (jobType !== 'keyword') {
      const v = url.trim();
      if (!v) { setUrlErr('Vui lòng nhập URL.'); return false; }
      if (!/^https?:\/\//i.test(v)) { setUrlErr('URL phải bắt đầu bằng https://'); return false; }
    } else {
      if (!keyword.trim()) { setError('Vui lòng nhập từ khóa.'); return false; }
    }
    return true;
  }

  async function handleCreate() {
    setError(''); setUrlErr('');
    if (!validateInput()) return;

    let input_payload = {};
    if (jobType === 'single')   input_payload = { url: url.trim() };
    else if (jobType === 'channel') input_payload = { url: url.trim(), max_videos: 10 };
    else input_payload = { keyword: keyword.trim(), platform: kwPlatform, count: kwCount };

    const body = {
      job_type:              jobType,
      input_payload,
      schedule_type:         schedType,
      next_run_at:           new Date(nextRun).toISOString(),
      run_at:                schedType !== 'once' ? `${runAt}:00` : null,
      run_on_weekday:        schedType === 'weekly' ? weekday : null,
      duplicate_suppression: dupMode,
      auto_collection_id:    autoCollId || null,
    };

    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail?.user_message || d.detail?.message || d.detail || 'Lỗi tạo lịch');
      onCreated(d);
      onClose();
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  return (
    <div className="bg-surface border border-line rounded-2xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-bold text-fg">Tạo Lịch Tải Mới</h3>
        <button onClick={onClose} className="text-fg-muted hover:text-fg cursor-pointer"><X className="w-5 h-5" /></button>
      </div>

      {/* Job type */}
      <div>
        <label className="text-xs text-fg-muted mb-1.5 block">Loại Job</label>
        <div className="grid grid-cols-3 gap-2">
          {Object.entries(JOB_TYPE_LABELS).map(([k, v]) => (
            <button key={k} onClick={() => { setJobType(k); setUrlErr(''); }}
              className={`py-2 rounded-lg text-sm cursor-pointer border transition-colors ${jobType === k ? 'border-accent/50 bg-accent-soft text-accent-text' : 'border-line text-fg-muted hover:border-line-strong'}`}>
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Input */}
      {jobType !== 'keyword' ? (
        <div>
          <label className="text-xs text-fg-muted mb-1 block">
            {jobType === 'single' ? 'URL Video' : 'URL Kênh / Playlist'}
          </label>
          <input
            value={url}
            onChange={e => { setUrl(e.target.value); setUrlErr(''); }}
            placeholder={jobType === 'single' ? 'https://youtube.com/watch?v=...' : 'https://youtube.com/@channel...'}
            className={`w-full bg-surface border rounded-lg px-3 py-2 text-sm text-fg focus:outline-none transition-colors ${urlErr ? 'border-danger/60 focus:border-danger/80' : 'border-line focus:border-accent/50'}`}
          />
          {urlErr && <p className="text-danger text-xs mt-1">{urlErr}</p>}
        </div>
      ) : (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-fg-muted mb-1 block">Từ Khóa</label>
            <input value={keyword} onChange={e => setKeyword(e.target.value)}
              placeholder="VD: react tutorial 2024"
              className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent/50"
            />
          </div>
          <div className="flex gap-2">
            <select value={kwPlatform} onChange={e => setKwPlatform(e.target.value)}
              className="flex-1 bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg-2 focus:outline-none cursor-pointer">
              <option value="youtube">YouTube</option>
              <option value="tiktok">TikTok</option>
            </select>
            <input type="number" min={1} max={20} value={kwCount} onChange={e => setKwCount(+e.target.value)}
              className="w-20 bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none"
              title="Số video tối đa"
            />
          </div>
        </div>
      )}

      {/* Schedule type */}
      <div>
        <label className="text-xs text-fg-muted mb-1.5 block">Lịch Chạy</label>
        <div className="grid grid-cols-3 gap-2">
          {Object.entries(SCHEDULE_LABELS).map(([k, v]) => (
            <button key={k} onClick={() => setSchedType(k)}
              className={`py-2 rounded-lg text-sm cursor-pointer border transition-colors ${schedType === k ? 'border-accent/50 bg-accent-soft text-accent-text' : 'border-line text-fg-muted hover:border-line-strong'}`}>
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Time / weekday */}
      <div className="flex gap-3">
        {schedType !== 'once' && (
          <div className="flex-1">
            <label className="text-xs text-fg-muted mb-1 block">Giờ chạy (UTC)</label>
            <input type="time" value={runAt} onChange={e => setRunAt(e.target.value)}
              className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none cursor-pointer"
            />
          </div>
        )}
        {schedType === 'weekly' && (
          <div className="flex-1">
            <label className="text-xs text-fg-muted mb-1 block">Thứ</label>
            <select value={weekday} onChange={e => setWeekday(+e.target.value)}
              className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg-2 focus:outline-none cursor-pointer">
              {WEEKDAY_LABELS.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select>
          </div>
        )}
        {schedType === 'once' && (
          <div className="flex-1">
            <label className="text-xs text-fg-muted mb-1 block">Thời điểm chạy</label>
            <input type="datetime-local" value={nextRun} onChange={e => setNextRun(e.target.value)}
              className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none cursor-pointer"
            />
          </div>
        )}
      </div>

      {schedType !== 'once' && nextRun && (
        <p className="text-xs text-fg-muted">Lần chạy tiếp theo: {fmtDatetime(nextRun)}</p>
      )}

      {/* Duplicate suppression */}
      <div>
        <label className="text-xs text-fg-muted mb-1 block">Lọc video trùng</label>
        <select value={dupMode} onChange={e => setDupMode(e.target.value)}
          className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg-2 focus:outline-none cursor-pointer">
          {Object.entries(DUP_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>

      {/* Auto collection */}
      {collections.length > 0 && (
        <div>
          <label className="text-xs text-fg-muted mb-1 block">Tự động thêm vào collection (tuỳ chọn)</label>
          <select value={autoCollId} onChange={e => setAutoCollId(e.target.value)}
            className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg-2 focus:outline-none cursor-pointer">
            <option value="">— Không —</option>
            {collections.map(c => <option key={c.id} value={c.id}>{c.icon || '📁'} {c.name}</option>)}
          </select>
        </div>
      )}

      {error && <p className="text-danger text-xs">{error}</p>}

      <button onClick={handleCreate} disabled={loading}
        className="w-full py-2.5 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition disabled:opacity-50 cursor-pointer">
        {loading ? 'Đang tạo...' : 'Tạo Lịch Tải'}
      </button>
    </div>
  );
}

// ── Schedule row ──────────────────────────────────────────────────────────────
function ScheduleRow({ job, onDelete, onToggle, onRunNow }) {
  const [runLoading, setRunLoading] = useState(false);
  const [copyDone,   setCopyDone]   = useState(false);
  const isActive  = job.is_active;
  const isFailed  = job.last_run_status === 'failed';
  const isSuccess = job.last_run_status === 'success';
  const source    = job.input_payload?.url || job.input_payload?.keyword || '';

  const handleCopySource = () => {
    navigator.clipboard.writeText(source).then(() => {
      setCopyDone(true);
      setTimeout(() => setCopyDone(false), 1500);
    }).catch(() => {});
  };

  const handleRunNow = async () => {
    setRunLoading(true);
    await onRunNow(job.id);
    setRunLoading(false);
  };

  return (
    <div className={`bg-surface border rounded-xl p-4 space-y-3 transition-all ${isActive ? 'border-line' : 'border-line opacity-60'}`}>
      {/* Top row: badges + source */}
      <div className="flex items-start gap-2 flex-wrap">
        <span className="text-xs font-bold px-2 py-0.5 rounded bg-surface-2 text-fg-2 shrink-0">
          {JOB_TYPE_LABELS[job.job_type] || job.job_type}
        </span>
        <span className="text-xs px-2 py-0.5 rounded bg-surface-2 text-fg-muted shrink-0">
          {SCHEDULE_LABELS[job.schedule_type] || job.schedule_type}
        </span>
        {job.duplicate_suppression && job.duplicate_suppression !== 'off' && (
          <span className="text-xs px-2 py-0.5 rounded bg-surface-2 text-fg-2 border border-line shrink-0">
            {DUP_LABELS[job.duplicate_suppression]}
          </span>
        )}
        {!isActive && (
          <span className="text-xs px-2 py-0.5 rounded bg-surface-2 text-fg-muted shrink-0">Tạm dừng</span>
        )}
      </div>

      {/* Source */}
      <div className="flex items-center gap-1.5 min-w-0">
        <p className="text-sm text-fg truncate flex-1">{source || JSON.stringify(job.input_payload)}</p>
        {source && (
          <button onClick={handleCopySource} title="Sao chép nguồn"
            className="flex-shrink-0 p-1 rounded text-fg-muted hover:text-fg-2 cursor-pointer transition-colors">
            {copyDone ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>

      {/* Timing row */}
      <div className="flex gap-4 text-xs flex-wrap">
        {/* Next run */}
        <span className="flex items-center gap-1 text-fg-muted">
          <Clock className="w-3 h-3" />
          Lần tới: <span className="text-fg-2">{fmtDatetime(job.next_run_at)}</span>
          {job.is_active && job.next_run_at && (
            <span className="text-fg-muted ml-1">({timeUntil(job.next_run_at)})</span>
          )}
        </span>

        {/* Last run */}
        {job.last_run_at && (
          <span className={`flex items-center gap-1 ${isFailed ? 'text-danger' : isSuccess ? 'text-success' : 'text-accent-text'}`}>
            {isSuccess ? <Check className="w-3 h-3" /> : isFailed ? <AlertCircle className="w-3 h-3" /> : <RefreshCw className="w-3 h-3 animate-spin" />}
            {fmtDatetime(job.last_run_at)}
          </span>
        )}
      </div>

      {/* Last run error */}
      {isFailed && job.last_run_error && (
        <p className="text-xs text-danger bg-danger-soft border border-danger/20 rounded-lg px-3 py-2">
          {job.last_run_error}
        </p>
      )}

      {/* Run count */}
      {job.run_count > 0 && (
        <p className="text-[11px] text-fg-muted">Đã chạy {job.run_count} lần</p>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-0.5">
        {/* Run now */}
        <button onClick={handleRunNow} disabled={runLoading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-accent/30 text-accent-text text-xs font-bold hover:bg-accent-soft transition-colors cursor-pointer disabled:opacity-50">
          {runLoading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
          Chạy ngay
        </button>

        {/* Pause / resume */}
        <button onClick={() => onToggle(job.id)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-colors ${isActive ? 'border-line-strong text-fg-muted hover:text-fg' : 'border-success/30 text-success hover:bg-success-soft'}`}>
          {isActive ? <><Pause className="w-3.5 h-3.5" /> Tạm dừng</> : <><Play className="w-3.5 h-3.5" /> Tiếp tục</>}
        </button>

        {/* Delete */}
        <button onClick={() => onDelete(job.id)}
          className="ml-auto p-1.5 rounded-lg text-fg-muted hover:text-danger hover:bg-danger-soft cursor-pointer border border-transparent hover:border-danger/30 transition-colors">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function SchedulePage() {
  const { session } = useAuth();
  const [schedules,   setSchedules]   = useState([]);
  const [collections, setCollections] = useState([]);
  const [loading,     setLoading]     = useState(false);
  const [showCreate,  setShowCreate]  = useState(false);
  const [runMsg,      setRunMsg]      = useState('');

  const headers = { Authorization: `Bearer ${session?.access_token}` };

  async function fetchSchedules() {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/schedule`, { headers });
      if (r.ok) { const d = await r.json(); setSchedules(d.schedules || []); }
    } finally { setLoading(false); }
  }

  async function fetchCollections() {
    const r = await fetch(`${API}/api/v1/archive/collections`, { headers });
    if (r.ok) { const d = await r.json(); setCollections(d.collections || []); }
  }

  useEffect(() => { fetchSchedules(); fetchCollections(); }, []);

  async function handleDelete(id) {
    if (!confirm('Xóa lịch tải này?')) return;
    await fetch(`${API}/api/v1/schedule/${id}`, { method: 'DELETE', headers });
    setSchedules(prev => prev.filter(s => s.id !== id));
  }

  async function handleToggle(id) {
    const r = await fetch(`${API}/api/v1/schedule/${id}/toggle`, { method: 'POST', headers });
    if (r.ok) {
      const d = await r.json();
      setSchedules(prev => prev.map(s => s.id === id ? { ...s, is_active: d.is_active } : s));
    }
  }

  async function handleRunNow(id) {
    const r = await fetch(`${API}/api/v1/schedule/${id}/run`, { method: 'POST', headers });
    const d = await r.json();
    if (r.ok) {
      setRunMsg(d.message || 'Đã kích hoạt chạy ngay.');
      setSchedules(prev => prev.map(s => s.id === id ? { ...s, last_run_status: 'running' } : s));
    } else {
      setRunMsg(d.detail?.user_message || d.detail?.message || d.detail || 'Không thể chạy ngay.');
    }
    setTimeout(() => setRunMsg(''), 4000);
  }

  const active = schedules.filter(s => s.is_active);
  const paused = schedules.filter(s => !s.is_active);

  return (
    <div className="max-w-3xl mx-auto px-4 md:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-fg">Lịch Tải</h1>
          <p className="text-sm text-fg-muted mt-1">Tự động tải video theo lịch đặt trước</p>
        </div>
        <button onClick={() => setShowCreate(s => !s)}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition cursor-pointer">
          <Plus className="w-4 h-4" />
          Tạo lịch mới
        </button>
      </div>

      {/* Tier hint */}
      <div className="mb-4 px-4 py-2.5 bg-surface-2 border border-line rounded-xl text-xs text-fg-muted flex items-center gap-2">
        <Calendar className="w-4 h-4 text-accent-text" />
        Free: tối đa 3 lịch · Pro: không giới hạn · Scanner chạy mỗi 60 giây
      </div>

      {/* Run-now feedback */}
      {runMsg && (
        <div className="mb-4 px-4 py-2.5 bg-surface-2 border border-line rounded-xl text-xs text-fg-2">
          {runMsg}
        </div>
      )}

      {/* Create panel */}
      {showCreate && (
        <div className="mb-5">
          <CreatePanel
            collections={collections}
            onCreated={s => { setSchedules(prev => [s, ...prev]); setShowCreate(false); }}
            onClose={() => setShowCreate(false)}
          />
        </div>
      )}

      {loading && schedules.length === 0 ? (
        <div className="flex justify-center py-12 text-fg-muted text-sm">Đang tải...</div>
      ) : schedules.length === 0 ? (
        <EmptyState
          icon={<Calendar className="w-12 h-12" />}
          title="Chưa có lịch tải nào"
          body='Nhấn "Tạo lịch mới" để tự động tải video theo giờ đặt trước.'
          action={
            <button onClick={() => setShowCreate(true)}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-accent-soft border border-accent/30 text-accent-text text-sm font-bold hover:bg-accent/20 transition cursor-pointer">
              <Plus className="w-4 h-4" /> Tạo lịch đầu tiên
            </button>
          }
        />
      ) : (
        <div className="space-y-6">
          {active.length > 0 && (
            <div>
              <p className="text-xs font-bold text-fg-muted uppercase tracking-wider mb-2">
                Đang hoạt động ({active.length})
              </p>
              <div className="space-y-3">
                {active.map(s => (
                  <ScheduleRow key={s.id} job={s} onDelete={handleDelete} onToggle={handleToggle} onRunNow={handleRunNow} />
                ))}
              </div>
            </div>
          )}
          {paused.length > 0 && (
            <div>
              <p className="text-xs font-bold text-fg-muted uppercase tracking-wider mb-2">
                Đã tạm dừng ({paused.length})
              </p>
              <div className="space-y-3">
                {paused.map(s => (
                  <ScheduleRow key={s.id} job={s} onDelete={handleDelete} onToggle={handleToggle} onRunNow={handleRunNow} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

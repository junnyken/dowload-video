import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Mic, Download, Trash2, Loader2, CheckCircle2, XCircle,
  Film, Languages, X, AlertCircle,
} from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';

const API = import.meta.env.VITE_API_URL || '';

// Same stable per-browser identity convention as TranscriptTranslatePage.jsx
// (see that file for the full rationale) — reused verbatim, not reinvented.
function getSessionId() {
  let sid = localStorage.getItem('vg_session_id');
  if (!sid) {
    sid = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
    localStorage.setItem('vg_session_id', sid);
  }
  return sid;
}
const SESSION_ID = getSessionId();

function authHeaders(session) {
  const headers = { 'X-Session-ID': SESSION_ID };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  return headers;
}

// Must match backend transcript_translate._TARGET_LANGS exactly.
const TARGET_LANGS = {
  vi: 'Tiếng Việt', en: 'Tiếng Anh', ja: 'Tiếng Nhật', ko: 'Tiếng Hàn',
  zh: 'Tiếng Trung', fr: 'Tiếng Pháp', de: 'Tiếng Đức', es: 'Tiếng Tây Ban Nha',
  th: 'Tiếng Thái', id: 'Tiếng Indonesia',
};

const ACTIVE_STATUSES = ['queued', 'extracting_audio', 'transcribing'];

const STATUS_CONFIG = {
  queued:           { label: 'Đang chờ',        cls: 'bg-slate-600/30 text-slate-400 border-slate-600/40' },
  extracting_audio: { label: 'Đang tách âm thanh', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30', spin: true },
  transcribing:     { label: 'Đang nhận diện giọng nói', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30', spin: true },
  done:             { label: 'Hoàn tất',         cls: 'bg-green-500/15 text-green-400 border-green-500/30' },
  failed:           { label: 'Thất bại',         cls: 'bg-red-500/15 text-red-400 border-red-500/30' },
};

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.queued;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-semibold whitespace-nowrap ${cfg.cls}`}>
      {cfg.spin && <Loader2 className="w-3 h-3 animate-spin" />}
      {status === 'done' && <CheckCircle2 className="w-3 h-3" />}
      {status === 'failed' && <XCircle className="w-3 h-3" />}
      {cfg.label}
    </span>
  );
}

function formatDuration(sec) {
  if (sec == null) return null;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function TranscriptAsrPage() {
  const { session } = useAuth();
  const [jobs, setJobs]                 = useState([]);
  const [errorMsg, setErrorMsg]         = useState('');
  const [picking, setPicking]           = useState(false);
  const [history, setHistory]           = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [creatingId, setCreatingId]     = useState(null);
  const submittingRef = useRef(false);

  // Chain-to-translate picker — which done ASR job is picking a target lang.
  const [chainJob, setChainJob]         = useState(null);
  const [chainLang, setChainLang]       = useState('');
  const [chainLoading, setChainLoading] = useState(false);
  const [chainError, setChainError]     = useState('');

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs`, { headers: authHeaders(session) });
      if (!r.ok) return;
      const d = await r.json();
      setJobs(d.jobs || []);
    } catch { /* keep last known state on transient errors */ }
  }, [session]);

  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  useEffect(() => {
    const hasActive = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
    if (!hasActive) return;
    const timer = setInterval(() => fetchJobs(), 5000);
    return () => clearInterval(timer);
  }, [jobs, fetchJobs]);

  async function openPicker() {
    setPicking(true);
    setHistoryError('');
    setHistoryLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/history?limit=10&status=success`, { headers: authHeaders(session) });
      const d = await r.json();
      const withLocalFile = (d.jobs || []).filter((j) => j.local_file_path);
      setHistory(withLocalFile);
      if (withLocalFile.length === 0) {
        setHistoryError('Không tìm thấy video nào còn trên server. Tải video trước khi tạo phụ đề.');
      }
    } catch {
      setHistoryError('Không tải được danh sách video đã tải. Thử lại sau.');
    } finally {
      setHistoryLoading(false);
    }
  }

  function closePicker() {
    setPicking(false);
    setHistory([]);
    setHistoryError('');
  }

  async function handlePick(item) {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setCreatingId(item.id);
    setErrorMsg('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs`, {
        method: 'POST',
        headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_local_path: item.local_file_path,
          video_title: item.title || item.original_url,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Tạo job thất bại.');
      setJobs((prev) => [d, ...prev]);
      closePicker();
    } catch (e) {
      setErrorMsg(e.message || 'Tạo job thất bại.');
    } finally {
      submittingRef.current = false;
      setCreatingId(null);
    }
  }

  async function handleDownload(job) {
    setErrorMsg('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs/${job.id}/download`, {
        headers: authHeaders(session),
      });
      if (!r.ok) throw new Error('Không thể tải file.');
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${job.video_title || 'transcript'}.srt`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setErrorMsg(e.message || 'Không thể tải file.');
    }
  }

  async function handleDelete(id) {
    if (!window.confirm('Xoá job này? Hành động này không thể hoàn tác.')) return;
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs/${id}`, {
        method: 'DELETE',
        headers: authHeaders(session),
      });
      if (r.ok) setJobs((prev) => prev.filter((j) => j.id !== id));
    } catch { /* non-critical */ }
  }

  function openChainPicker(job) {
    setChainJob(job);
    setChainLang('');
    setChainError('');
  }

  async function handleChainTranslate() {
    if (!chainJob || !chainLang) return;
    setChainLoading(true);
    setChainError('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs/${chainJob.id}/translate`, {
        method: 'POST',
        headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_lang: chainLang }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Tạo job dịch thất bại.');
      setChainJob(null);
      // The new job now lives under /transcript-translate — point the user there.
      window.location.href = '/transcript-translate';
    } catch (e) {
      setChainError(e.message || 'Tạo job dịch thất bại.');
    } finally {
      setChainLoading(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Mic className="w-6 h-6 text-[#FBBF24]" />
          Tạo Phụ Đề Từ Video
        </h1>
        <p className="text-sm text-slate-400 mt-1.5 leading-relaxed">
          Chọn 1 video đã tải, hệ thống tự nhận diện giọng nói và tạo phụ đề (SRT) kèm mốc thời gian —
          dành cho video chưa có sẵn phụ đề. Có thể dịch luôn phụ đề vừa tạo sang ngôn ngữ khác.
        </p>
        <a
          href="/transcript-translate"
          onClick={(e) => { e.preventDefault(); window.history.pushState({}, '', '/transcript-translate'); window.dispatchEvent(new PopStateEvent('popstate')); }}
          className="inline-flex items-center gap-1.5 text-xs text-[#FBBF24] hover:underline mt-2 cursor-pointer"
        >
          Đã có sẵn file phụ đề .srt/.vtt? Dịch trực tiếp →
        </a>
      </div>

      {errorMsg && (
        <div className="mb-4 flex items-start gap-2 px-4 py-3 bg-red-900/20 border border-red-700/30 rounded-xl text-sm text-red-300">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <p className="flex-1">{errorMsg}</p>
          <button onClick={() => setErrorMsg('')} className="text-red-400 hover:text-red-200 cursor-pointer shrink-0" aria-label="Đóng thông báo lỗi">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <button
        onClick={openPicker}
        className="w-full mb-8 flex items-center justify-center gap-2 px-5 py-4 rounded-xl border-2 border-dashed border-slate-700/50 hover:border-[#FBBF24]/40 text-slate-300 hover:text-[#FBBF24] transition-colors cursor-pointer"
      >
        <Mic className="w-5 h-5" />
        Chọn video đã tải để tạo phụ đề
      </button>

      <div>
        <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">
          Hàng đợi{jobs.length > 0 ? ` (${jobs.length})` : ''}
        </h2>

        {jobs.length === 0 ? (
          <EmptyState
            icon={<Mic className="w-12 h-12" />}
            title="Chưa có job tạo phụ đề nào"
            body="Chọn video đã tải ở trên để bắt đầu."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {jobs.map((job) => {
              const isDone = job.status === 'done';
              const pct = Math.min(100, Math.max(0, job.progress_pct ?? 0));
              return (
                <div key={job.id} className="bg-[#0d2320] border border-slate-700/30 rounded-xl p-4 flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-white flex items-center gap-2 min-w-0">
                        <Film className="w-4 h-4 text-slate-500 shrink-0" />
                        <span className="truncate">{job.video_title || 'Video'}</span>
                      </p>
                      <p className="text-xs text-slate-400 mt-1">
                        {formatDuration(job.duration_sec) && `${formatDuration(job.duration_sec)} · `}
                        {job.detected_language ? `Ngôn ngữ: ${job.detected_language}` : 'Chưa xác định ngôn ngữ'}
                      </p>
                    </div>
                    <StatusBadge status={job.status} />
                  </div>

                  <div className="h-1.5 rounded-full bg-slate-700/50 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        job.status === 'failed' ? 'bg-red-500' : job.status === 'done' ? 'bg-green-500' : 'bg-gradient-to-r from-[#FBBF24] to-[#FB923C]'
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>

                  {job.status === 'failed' && job.error && (
                    <p className="text-xs text-red-400">{job.error}</p>
                  )}

                  <div className="flex items-center gap-2 justify-end">
                    <button
                      onClick={() => openChainPicker(job)}
                      disabled={!isDone}
                      title="Dịch phụ đề vừa tạo sang ngôn ngữ khác"
                      aria-label={`Dịch phụ đề ${job.video_title}`}
                      className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-[#FBBF24] hover:border-[#FBBF24]/40 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
                    >
                      <Languages className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDownload(job)}
                      disabled={!isDone}
                      title="Tải file phụ đề"
                      aria-label={`Tải phụ đề ${job.video_title}`}
                      className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-[#FBBF24] hover:border-[#FBBF24]/40 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
                    >
                      <Download className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(job.id)}
                      title="Xoá job"
                      aria-label={`Xoá job ${job.video_title}`}
                      className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-red-400 hover:border-red-500/40 cursor-pointer transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="h-16 sm:h-0" aria-hidden="true" />

      {picking && (
        <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4" onClick={closePicker}>
          <div
            className="bg-[#0d2320] border border-slate-700/40 rounded-xl w-full max-w-md max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Mic className="w-4 h-4 text-[#FBBF24]" />
                Chọn video
              </h3>
              <button onClick={closePicker} className="text-slate-500 hover:text-white cursor-pointer" aria-label="Đóng">
                <X className="w-4 h-4" />
              </button>
            </div>

            {historyLoading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-slate-400 text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                Đang tải...
              </div>
            ) : (
              <>
                {historyError && (
                  <p className="text-xs text-red-400 mb-3 flex items-start gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    {historyError}
                  </p>
                )}
                <div className="flex flex-col gap-2">
                  {history.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => handlePick(item)}
                      disabled={creatingId === item.id}
                      className="text-left px-3 py-2.5 rounded-lg border border-slate-700/40 hover:border-[#FBBF24]/40 hover:bg-[#FBBF24]/5 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-between gap-2"
                    >
                      <span className="min-w-0">
                        <p className="text-sm text-white truncate">{item.title || item.original_url}</p>
                        {item.platform && <p className="text-[11px] text-slate-500 mt-0.5">{item.platform}</p>}
                      </span>
                      {creatingId === item.id && <Loader2 className="w-4 h-4 animate-spin text-[#FBBF24] shrink-0" />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {chainJob && (
        <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4" onClick={() => setChainJob(null)}>
          <div
            className="bg-[#0d2320] border border-slate-700/40 rounded-xl w-full max-w-sm p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Languages className="w-4 h-4 text-[#FBBF24]" />
                Dịch phụ đề
              </h3>
              <button onClick={() => setChainJob(null)} className="text-slate-500 hover:text-white cursor-pointer" aria-label="Đóng">
                <X className="w-4 h-4" />
              </button>
            </div>

            <label htmlFor="chain-lang-select" className="text-xs text-slate-400 mb-1 block">Ngôn ngữ đích</label>
            <select
              id="chain-lang-select"
              value={chainLang}
              onChange={(e) => setChainLang(e.target.value)}
              className="w-full bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#FBBF24]/50 cursor-pointer mb-3"
            >
              <option value="" disabled>-- Chọn ngôn ngữ đích --</option>
              {Object.entries(TARGET_LANGS).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>

            {chainError && <p className="text-xs text-red-400 mb-3">{chainError}</p>}

            <button
              onClick={handleChainTranslate}
              disabled={!chainLang || chainLoading}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-bold text-sm hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
            >
              {chainLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Languages className="w-4 h-4" />}
              {chainLoading ? 'Đang tạo...' : 'Bắt đầu dịch'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

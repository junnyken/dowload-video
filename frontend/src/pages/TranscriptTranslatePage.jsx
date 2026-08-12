import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Upload, Download, Trash2, Loader2, CheckCircle2, XCircle,
  FileText, Languages, X, AlertCircle, Film, Pencil, Save,
} from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';

const API = import.meta.env.VITE_API_URL || '';

// Stable per-browser identity for anonymous-ownership scoping — mirrors the
// existing `vg_session_id` convention in UserHistoryContent.jsx. Sent
// alongside Authorization (not instead of) so that if a Supabase access
// token expires mid-session, the backend's identity fallback resolves to
// this same session id instead of silently orphaning the request under a
// null identity — see app.api.transcript_translate.resolve_identity.
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

// Must match backend's `_TARGET_LANGS` allowlist exactly (key + order).
const TARGET_LANGS = {
  vi: 'Tiếng Việt',
  en: 'Tiếng Anh',
  ja: 'Tiếng Nhật',
  ko: 'Tiếng Hàn',
  zh: 'Tiếng Trung',
  fr: 'Tiếng Pháp',
  de: 'Tiếng Đức',
  es: 'Tiếng Tây Ban Nha',
  th: 'Tiếng Thái',
  id: 'Tiếng Indonesia',
};

// Backend's detect_source_language() returns the language name in English
// (e.g. "Vietnamese", "Korean") — map back to the Vietnamese label so the UI
// doesn't mix "Korean → Tiếng Đức". Falls back to the raw LLM answer for a
// detected language outside our 10-language target list (still correct,
// just not translated for display).
const SOURCE_LANG_DISPLAY = {
  Vietnamese: 'Tiếng Việt',
  English: 'Tiếng Anh',
  Japanese: 'Tiếng Nhật',
  Korean: 'Tiếng Hàn',
  Chinese: 'Tiếng Trung',
  French: 'Tiếng Pháp',
  German: 'Tiếng Đức',
  Spanish: 'Tiếng Tây Ban Nha',
  Thai: 'Tiếng Thái',
  Indonesian: 'Tiếng Indonesia',
};

function sourceLangLabel(sourceLang) {
  if (!sourceLang) return 'Đang phát hiện...';
  return SOURCE_LANG_DISPLAY[sourceLang] || sourceLang;
}

const ACTIVE_STATUSES = ['queued', 'detecting', 'translating'];

const STATUS_CONFIG = {
  queued:      { label: 'Đang chờ',              cls: 'bg-slate-600/30 text-slate-400 border-slate-600/40' },
  detecting:   { label: 'Đang nhận diện ngôn ngữ', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30', spin: true },
  translating: { label: 'Đang dịch',              cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30', spin: true },
  done:        { label: 'Hoàn tất',               cls: 'bg-green-500/15 text-green-400 border-green-500/30' },
  failed:      { label: 'Thất bại',               cls: 'bg-red-500/15 text-red-400 border-red-500/30' },
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

function Dropzone({ files, onFilesAdded, onRemoveFile }) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    onFilesAdded(Array.from(e.dataTransfer.files || []));
  }

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Chọn hoặc kéo thả file transcript .srt/.vtt để tải lên"
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click(); } }}
        className={`cursor-pointer rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragOver ? 'border-[#FBBF24] bg-[#FBBF24]/5' : 'border-slate-700/50 hover:border-slate-600'
        }`}
      >
        <Upload className="w-8 h-8 mx-auto text-slate-500 mb-2" />
        <p className="text-sm text-slate-300 font-medium">Kéo thả file .srt/.vtt vào đây, hoặc bấm để chọn file</p>
        <p className="text-xs text-slate-500 mt-1">Có thể chọn nhiều file cùng lúc</p>
        <input
          ref={inputRef}
          type="file"
          accept=".srt,.vtt"
          multiple
          className="hidden"
          onChange={(e) => { onFilesAdded(Array.from(e.target.files || [])); e.target.value = ''; }}
        />
      </div>

      {files.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {files.map((f, i) => (
            <li
              key={`${f.name}-${i}`}
              className="flex items-center justify-between gap-2 bg-slate-800/40 border border-slate-700/30 rounded-lg px-3 py-2 text-sm text-slate-300"
            >
              <span className="flex items-center gap-2 min-w-0">
                <FileText className="w-4 h-4 text-slate-500 shrink-0" />
                <span className="truncate">{f.name}</span>
              </span>
              <button
                onClick={() => onRemoveFile(i)}
                title="Bỏ file này"
                aria-label={`Bỏ file ${f.name}`}
                className="text-slate-500 hover:text-red-400 cursor-pointer shrink-0"
              >
                <X className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function JobCard({ job, onDownload, onDelete, onBurn, onEdit }) {
  const langLabel = TARGET_LANGS[job.target_lang] || job.target_lang;
  const isDone = job.status === 'done';
  const pct = Math.min(100, Math.max(0, job.progress_pct ?? 0));

  return (
    <div className="bg-[#0d2320] border border-slate-700/30 rounded-xl p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-white flex items-center gap-2 min-w-0">
            <FileText className="w-4 h-4 text-slate-500 shrink-0" />
            <span className="truncate">{job.filename}</span>
          </p>
          <p className="text-xs text-slate-400 mt-1">
            {sourceLangLabel(job.source_lang)} → {langLabel}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {/* Progress */}
      <div>
        <div className="h-1.5 rounded-full bg-slate-700/50 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              job.status === 'failed' ? 'bg-red-500' : job.status === 'done' ? 'bg-green-500' : 'bg-gradient-to-r from-[#FBBF24] to-[#FB923C]'
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
        {job.cue_count != null && (
          <p className="text-[11px] text-slate-500 mt-1">
            Đã dịch {job.translated_cue_count ?? 0}/{job.cue_count} dòng
          </p>
        )}
      </div>

      {job.status === 'failed' && job.error && (
        <p className="text-xs text-red-400">{job.error}</p>
      )}

      {job.skipped_block_count > 0 && (
        <p className="text-[11px] text-amber-400 flex items-center gap-1">
          <AlertCircle className="w-3 h-3 shrink-0" />
          Bỏ qua {job.skipped_block_count} dòng phụ đề bị lỗi định dạng trong file gốc.
        </p>
      )}

      {isDone && job.reading_speed_warning_count > 0 && (
        <p className="text-[11px] text-amber-400 flex items-center gap-1" title="Một số dòng dịch dài hơn thời gian hiển thị cho phép, người xem có thể không đọc kịp">
          <AlertCircle className="w-3 h-3 shrink-0" />
          {job.reading_speed_warning_count} dòng dịch có thể quá dài để đọc kịp.
        </p>
      )}

      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={() => onEdit(job)}
          disabled={!isDone}
          title="Xem/sửa bản dịch trước khi tải"
          aria-label={`Xem/sửa bản dịch ${job.filename}`}
          className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-[#FBBF24] hover:border-[#FBBF24]/40 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-slate-300 disabled:hover:border-slate-700/50 cursor-pointer transition-colors"
        >
          <Pencil className="w-4 h-4" />
        </button>
        <button
          onClick={() => onDownload(job)}
          disabled={!isDone}
          title="Tải file đã dịch"
          aria-label={`Tải file đã dịch ${job.filename}`}
          className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-[#FBBF24] hover:border-[#FBBF24]/40 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-slate-300 disabled:hover:border-slate-700/50 cursor-pointer transition-colors"
        >
          <Download className="w-4 h-4" />
        </button>
        <button
          onClick={() => onBurn(job)}
          disabled={!isDone}
          title="Ghép phụ đề đã dịch vào video đã tải"
          aria-label={`Ghép phụ đề vào video cho ${job.filename}`}
          className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-[#FBBF24] hover:border-[#FBBF24]/40 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-slate-300 disabled:hover:border-slate-700/50 cursor-pointer transition-colors"
        >
          <Film className="w-4 h-4" />
        </button>
        <button
          onClick={() => onDelete(job.id)}
          title="Xoá job dịch"
          aria-label={`Xoá job dịch ${job.filename}`}
          className="p-2 rounded-lg border border-slate-700/50 text-slate-300 hover:text-red-400 hover:border-red-500/40 cursor-pointer transition-colors"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

export default function TranscriptTranslatePage() {
  const { session } = useAuth();
  const [files, setFiles]           = useState([]);
  const [targetLang, setTargetLang] = useState('');
  const [jobs, setJobs]             = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [rejected, setRejected]     = useState([]);
  const [errorMsg, setErrorMsg]     = useState('');
  const submittingRef = useRef(false); // synchronous double-submit guard — `submitting` state can lag a fast double-click by a render

  // "Ghép vào video" picker — burnJob is the transcript job being burned;
  // burnHistory/burnLoading/burnError/burnResult all reset per-picker-open.
  const [burnJob, setBurnJob]         = useState(null);
  const [burnHistory, setBurnHistory] = useState([]);
  const [burnLoading, setBurnLoading] = useState(false);
  const [burnError, setBurnError]     = useState('');
  const [burnResult, setBurnResult]   = useState(null);

  // Cue editor — editJob is the job being reviewed; editCues holds the
  // live-editable copy (only .text ever changes locally, index/start/end
  // are display-only and never sent back).
  const [editJob, setEditJob]         = useState(null);
  const [editCues, setEditCues]       = useState([]);
  const [editLoading, setEditLoading] = useState(false);
  const [editSaving, setEditSaving]   = useState(false);
  const [editError, setEditError]     = useState('');
  const [editDirty, setEditDirty]     = useState(new Set());

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs`, {
        headers: authHeaders(session),
      });
      if (!r.ok) return;
      const d = await r.json();
      setJobs(d.jobs || []);
    } catch { /* keep last known state on transient errors */ }
  }, [session]);

  // Initial fetch
  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  // Poll every 5s only while at least one job is non-terminal
  useEffect(() => {
    const hasActive = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
    if (!hasActive) return;
    const timer = setInterval(() => fetchJobs(), 5000);
    return () => clearInterval(timer);
  }, [jobs, fetchJobs]);

  function addFiles(newFiles) {
    const valid = newFiles.filter((f) => /\.(srt|vtt)$/i.test(f.name));
    if (valid.length) setFiles((prev) => [...prev, ...valid]);
  }

  function removeFile(idx) {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleSubmit() {
    if (files.length === 0 || !targetLang || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setErrorMsg('');
    try {
      const form = new FormData();
      files.forEach((f) => form.append('files', f));
      form.append('target_lang', targetLang);

      const r = await fetch(`${API}/api/v1/transcript-translate/upload`, {
        method: 'POST',
        headers: authHeaders(session),
        body: form,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail?.message || d.detail || 'Tải lên thất bại. Vui lòng thử lại.');

      if (Array.isArray(d.jobs) && d.jobs.length > 0) {
        setJobs((prev) => [...d.jobs, ...prev]);
      }
      setRejected(Array.isArray(d.rejected) ? d.rejected : []);
      setFiles([]);
    } catch (e) {
      setErrorMsg(e.message || 'Có lỗi xảy ra khi tải lên.');
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function handleDownload(job) {
    setErrorMsg('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${job.id}/download`, {
        headers: authHeaders(session),
      });
      if (!r.ok) throw new Error('Không thể tải file. Vui lòng thử lại.');
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = job.filename || 'transcript-translated';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setErrorMsg(e.message || 'Không thể tải file.');
    }
  }

  async function handleDelete(id) {
    if (!window.confirm('Xoá job dịch này? Hành động này không thể hoàn tác.')) return;
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${id}`, {
        method: 'DELETE',
        headers: authHeaders(session),
      });
      if (r.ok) setJobs((prev) => prev.filter((j) => j.id !== id));
    } catch { /* non-critical */ }
  }

  async function openBurnPicker(job) {
    setBurnJob(job);
    setBurnResult(null);
    setBurnError('');
    setBurnLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/history?limit=10&status=success`, {
        headers: authHeaders(session),
      });
      const d = await r.json();
      // Only videos whose server-side file is still plausibly on disk are
      // useful here — burning needs the real local file, not just a record.
      const withLocalFile = (d.jobs || []).filter((j) => j.local_file_path);
      setBurnHistory(withLocalFile);
      if (withLocalFile.length === 0) {
        setBurnError('Không tìm thấy video nào còn trên server. Tải lại video trước khi ghép phụ đề.');
      }
    } catch {
      setBurnError('Không tải được danh sách video đã tải. Thử lại sau.');
    } finally {
      setBurnLoading(false);
    }
  }

  function closeBurnPicker() {
    setBurnJob(null);
    setBurnHistory([]);
    setBurnError('');
    setBurnResult(null);
  }

  async function handleBurnPick(historyItem) {
    if (!burnJob) return;
    setBurnLoading(true);
    setBurnError('');
    try {
      const r = await fetch(
        `${API}/api/v1/transcript-translate/jobs/${burnJob.id}/burn-into-video`,
        {
          method: 'POST',
          headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
          body: JSON.stringify({ video_local_path: historyItem.local_file_path }),
        }
      );
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Ghép phụ đề vào video thất bại.');
      setBurnResult(d);
    } catch (e) {
      setBurnError(e.message || 'Ghép phụ đề vào video thất bại.');
    } finally {
      setBurnLoading(false);
    }
  }

  async function openEditor(job) {
    setEditJob(job);
    setEditCues([]);
    setEditDirty(new Set());
    setEditError('');
    setEditLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${job.id}/cues`, {
        headers: authHeaders(session),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Không tải được nội dung để sửa.');
      setEditCues(d.cues || []);
    } catch (e) {
      setEditError(e.message || 'Không tải được nội dung để sửa.');
    } finally {
      setEditLoading(false);
    }
  }

  function closeEditor() {
    setEditJob(null);
    setEditCues([]);
    setEditDirty(new Set());
    setEditError('');
  }

  function editCueText(index, text) {
    setEditCues((prev) => prev.map((c) => (c.index === index ? { ...c, text } : c)));
    setEditDirty((prev) => new Set(prev).add(index));
  }

  async function saveEdits() {
    if (!editJob || editDirty.size === 0) return;
    setEditSaving(true);
    setEditError('');
    try {
      const changed = editCues
        .filter((c) => editDirty.has(c.index))
        .map((c) => ({ index: c.index, text: c.text }));
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${editJob.id}/cues`, {
        method: 'PUT',
        headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
        body: JSON.stringify({ cues: changed }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Lưu thay đổi thất bại.');

      setEditDirty(new Set());
      // Reflect the recomputed warning count in the job list right away,
      // without waiting for the next 5s poll.
      setJobs((prev) => prev.map((j) => (
        j.id === editJob.id ? { ...j, reading_speed_warning_count: d.reading_speed_warning_count } : j
      )));
    } catch (e) {
      setEditError(e.message || 'Lưu thay đổi thất bại.');
    } finally {
      setEditSaving(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2">
          <Languages className="w-6 h-6 text-[#FBBF24]" />
          Dịch Phụ Đề
        </h1>
        <p className="text-sm text-slate-400 mt-1.5 leading-relaxed">
          Tải lên transcript .srt/.vtt có sẵn, chọn ngôn ngữ đích — hệ thống dịch bám sát ngữ nghĩa nội dung
          gốc (không dịch máy móc từng chữ) và giữ nguyên mốc thời gian gốc của file.
        </p>
        <a
          href="/transcript-asr"
          onClick={(e) => { e.preventDefault(); window.history.pushState({}, '', '/transcript-asr'); window.dispatchEvent(new PopStateEvent('popstate')); }}
          className="inline-flex items-center gap-1.5 text-xs text-[#FBBF24] hover:underline mt-2 cursor-pointer"
        >
          Chưa có file phụ đề? Tự tạo bằng AI từ video đã tải →
        </a>
      </div>

      {/* Error banner */}
      {errorMsg && (
        <div className="mb-4 flex items-start gap-2 px-4 py-3 bg-red-900/20 border border-red-700/30 rounded-xl text-sm text-red-300">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <p className="flex-1">{errorMsg}</p>
          <button onClick={() => setErrorMsg('')} className="text-red-400 hover:text-red-200 cursor-pointer shrink-0" aria-label="Đóng thông báo lỗi">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Rejected files banner */}
      {rejected.length > 0 && (
        <div className="mb-4 px-4 py-3 bg-red-900/20 border border-red-700/30 rounded-xl text-sm text-red-300">
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <p className="font-semibold flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" /> Một số file bị từ chối
            </p>
            <button onClick={() => setRejected([])} className="text-red-400 hover:text-red-200 cursor-pointer shrink-0" aria-label="Đóng thông báo file bị từ chối">
              <X className="w-4 h-4" />
            </button>
          </div>
          <ul className="space-y-0.5 text-xs text-red-300/90">
            {rejected.map((r, i) => (
              <li key={`${r.filename}-${i}`}>• {r.filename}: {r.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Upload form */}
      <div className="bg-[#0d2320] border border-slate-700/30 rounded-xl p-5 mb-8">
        <Dropzone files={files} onFilesAdded={addFiles} onRemoveFile={removeFile} />

        <div className="mt-4 flex flex-col sm:flex-row gap-3 sm:items-end">
          <div className="flex-1">
            <label htmlFor="target-lang-select" className="text-xs text-slate-400 mb-1 block">Ngôn ngữ đích</label>
            <select
              id="target-lang-select"
              value={targetLang}
              onChange={(e) => setTargetLang(e.target.value)}
              className="w-full sm:w-auto bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#FBBF24]/50 cursor-pointer"
            >
              <option value="" disabled>-- Chọn ngôn ngữ đích --</option>
              {Object.entries(TARGET_LANGS).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </div>

          <button
            onClick={handleSubmit}
            disabled={files.length === 0 || !targetLang || submitting}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-bold text-sm hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {submitting ? 'Đang tải lên...' : 'Bắt đầu dịch'}
          </button>
        </div>
      </div>

      {/* Queue */}
      <div>
        <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">
          Hàng đợi dịch{jobs.length > 0 ? ` (${jobs.length})` : ''}
        </h2>

        {jobs.length === 0 ? (
          <EmptyState
            icon={<Languages className="w-12 h-12" />}
            title="Chưa có job dịch nào"
            body="Tải lên file .srt/.vtt ở trên để bắt đầu dịch phụ đề sang ngôn ngữ khác."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} onDownload={handleDownload} onDelete={handleDelete} onBurn={openBurnPicker} onEdit={openEditor} />
            ))}
          </div>
        )}
      </div>

      {/* Clears the fixed feedback button + mobile bottom nav so the last
          job card's action icons stay tappable (main's global pb-20 only
          clears the bottom nav bar, not the feedback button floating above it). */}
      <div className="h-16 sm:h-0" aria-hidden="true" />

      {burnJob && (
        <div
          className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4"
          onClick={closeBurnPicker}
        >
          <div
            className="bg-[#0d2320] border border-slate-700/40 rounded-xl w-full max-w-md max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Film className="w-4 h-4 text-[#FBBF24]" />
                Ghép vào video đã tải
              </h3>
              <button onClick={closeBurnPicker} className="text-slate-500 hover:text-white cursor-pointer" aria-label="Đóng">
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-xs text-slate-400 mb-3 truncate">
              Phụ đề: <span className="text-slate-300">{burnJob.filename}</span>
            </p>

            {burnResult ? (
              <div className="text-center py-4">
                <CheckCircle2 className="w-8 h-8 text-green-400 mx-auto mb-2" />
                <p className="text-sm text-white mb-3">Đã ghép phụ đề vào video thành công.</p>
                <a
                  href={`${API}${burnResult.download_url}`}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold hover:opacity-90 transition"
                >
                  <Download className="w-4 h-4" />
                  Tải video đã ghép phụ đề
                </a>
              </div>
            ) : burnLoading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-slate-400 text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                Đang xử lý...
              </div>
            ) : (
              <>
                {burnError && (
                  <p className="text-xs text-red-400 mb-3 flex items-start gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    {burnError}
                  </p>
                )}
                <div className="flex flex-col gap-2">
                  {burnHistory.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => handleBurnPick(item)}
                      className="text-left px-3 py-2.5 rounded-lg border border-slate-700/40 hover:border-[#FBBF24]/40 hover:bg-[#FBBF24]/5 transition-colors cursor-pointer"
                    >
                      <p className="text-sm text-white truncate">{item.title || item.original_url}</p>
                      {item.platform && (
                        <p className="text-[11px] text-slate-500 mt-0.5">{item.platform}</p>
                      )}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {editJob && (
        <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
          <div className="bg-[#0d2320] border border-slate-700/40 rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/30 shrink-0">
              <h3 className="text-sm font-bold text-white flex items-center gap-2 min-w-0">
                <Pencil className="w-4 h-4 text-[#FBBF24] shrink-0" />
                <span className="truncate">Xem/sửa bản dịch — {editJob.filename}</span>
              </h3>
              <button onClick={closeEditor} className="text-slate-500 hover:text-white cursor-pointer shrink-0" aria-label="Đóng">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              {editLoading ? (
                <div className="flex items-center justify-center gap-2 py-8 text-slate-400 text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Đang tải...
                </div>
              ) : editError && editCues.length === 0 ? (
                <p className="text-sm text-red-400 flex items-start gap-1.5">
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                  {editError}
                </p>
              ) : (
                <div className="flex flex-col gap-3">
                  {editCues.map((cue) => (
                    <div key={cue.index} className="flex gap-3">
                      <div className="w-24 shrink-0 pt-2 text-[10px] text-slate-500 font-mono leading-tight">
                        <div>#{cue.index}</div>
                        <div>{cue.start.split(',')[0].split('.')[0]}</div>
                      </div>
                      <textarea
                        value={cue.text}
                        onChange={(e) => editCueText(cue.index, e.target.value)}
                        rows={1}
                        className={`flex-1 bg-slate-800/40 border rounded-lg px-3 py-2 text-sm text-white resize-y focus:outline-none focus:border-[#FBBF24]/50 ${
                          editDirty.has(cue.index) ? 'border-[#FBBF24]/40' : 'border-slate-700/40'
                        }`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-slate-700/30 shrink-0">
              <div className="text-xs min-w-0">
                {editError && editCues.length > 0 && (
                  <span className="text-red-400 flex items-center gap-1"><AlertCircle className="w-3.5 h-3.5 shrink-0" />{editError}</span>
                )}
                {!editError && editDirty.size > 0 && (
                  <span className="text-slate-400">{editDirty.size} dòng đã sửa, chưa lưu.</span>
                )}
              </div>
              <button
                onClick={saveEdits}
                disabled={editDirty.size === 0 || editSaving}
                className="shrink-0 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              >
                {editSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {editSaving ? 'Đang lưu...' : 'Lưu thay đổi'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * ProcessingHub — Phase 22 Post-Processing Suite 2.0
 * ====================================================
 * Unified post-processing modal.
 *
 * Props:
 *   videoInfo    — { title, thumbnail, duration, local_file_path, original_url, available_subtitle_languages }
 *   localPath    — server-side local_file_path (may be null)
 *   sourceUrl    — original source URL
 *   onClose()
 *   userTier     — 'free'|'pro'|'team'|'enterprise'
 */

import { useState } from 'react';
import {
  X, Download, Scissors, Music, Film, FileText, Package,
  Loader2, AlertCircle, CheckCircle2, Lock,
} from 'lucide-react';
import { useProcessing } from '../hooks/useProcessing';
import ArtifactExpiry from './ArtifactExpiry';

const API = import.meta.env.VITE_API_URL || '';

const TABS = [
  { id: 'trim',     label: 'Cắt clip',     icon: Scissors },
  { id: 'audio',    label: 'Âm thanh',     icon: Music    },
  { id: 'gif',      label: 'GIF / Loop',   icon: Film     },
  { id: 'subtitle', label: 'Phụ đề',       icon: FileText },
  { id: 'package',  label: 'Đóng gói',     icon: Package  },
];

// Quick-trim presets in seconds
const TRIM_PRESETS = [
  { label: '5s',  value: 5  },
  { label: '10s', value: 10 },
  { label: '15s', value: 15 },
  { label: '30s', value: 30 },
  { label: '60s', value: 60 },
  { label: 'Tùy', value: 0  },
];

const GIF_PRESETS = [
  { label: 'Nhỏ',    width: 320, fps: 10, desc: '~320px · 10fps' },
  { label: 'Social', width: 480, fps: 15, desc: '~480px · 15fps' },
  { label: 'HQ',     width: 720, fps: 24, desc: '720px · 24fps · Pro', tier: 'pro' },
];

const NAMING_TEMPLATES = [
  { label: '{title}',              value: '{title}'             },
  { label: '{platform}_{title}',   value: '{platform}_{title}'  },
  { label: '{index:02d}_{title}',  value: '{index:02d}_{title}' },
  { label: '{date}_{title}',       value: '{date}_{title}'      },
];

function ResultRow({ result, label }) {
  if (!result) return null;
  return (
    <div className="mt-3 flex items-center gap-2 p-2.5 bg-emerald-900/30 border border-emerald-700/40 rounded-xl">
      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-emerald-300 font-semibold truncate">{label || 'Hoàn tất'}</p>
        {result.file_size_mb && (
          <p className="text-[10px] text-white/40">{result.file_size_mb.toFixed(1)} MB</p>
        )}
        {result.expires_in_seconds && (
          <ArtifactExpiry expiresAt={new Date(Date.now() + result.expires_in_seconds * 1000).toISOString()} />
        )}
      </div>
      {result.download_url && (
        <a
          href={result.download_url.startsWith('/') ? `${API}${result.download_url}` : result.download_url}
          download
          className="flex items-center gap-1 px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg transition-colors"
        >
          <Download className="w-3.5 h-3.5" /> Tải
        </a>
      )}
    </div>
  );
}

function ErrorRow({ error }) {
  if (!error) return null;
  return (
    <div className="mt-3 flex items-start gap-2 p-2.5 bg-red-900/20 border border-red-700/30 rounded-xl">
      <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
      <p className="text-xs text-red-300">{error}</p>
    </div>
  );
}

// ── Tab: Trim ──────────────────────────────────────────────────────────────
function TrimTab({ localPath, sourceUrl, title, duration, processing }) {
  const [preset, setPreset]     = useState(15);
  const [isCustom, setIsCustom] = useState(false);
  const [start, setStart]       = useState(0);
  const [end, setEnd]           = useState(15);
  const [isAudio, setIsAudio]   = useState(false);
  const st = processing.getState('trim');

  const handlePreset = (p) => {
    if (p.value === 0) { setIsCustom(true); return; }
    setIsCustom(false);
    setPreset(p.value);
    setStart(0);
    setEnd(p.value);
  };

  const handleSubmit = async () => {
    const body = {
      start_time: start,
      end_time:   end,
      filename:   title || 'clip',
      is_audio:   isAudio,
    };
    if (localPath) body.local_path = localPath;
    else           body.url        = sourceUrl;
    try { await processing.runTrim(body); } catch {}
  };

  return (
    <div className="space-y-4">
      {/* Presets */}
      <div>
        <p className="text-xs text-white/50 mb-2 font-medium">Chọn độ dài nhanh</p>
        <div className="flex gap-1.5 flex-wrap">
          {TRIM_PRESETS.map(p => (
            <button
              key={p.label}
              onClick={() => handlePreset(p)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer
                ${(!isCustom && end - start === p.value && p.value !== 0) || (isCustom && p.value === 0)
                  ? 'bg-emerald-600 text-white'
                  : 'bg-white/8 text-white/60 hover:bg-white/15 hover:text-white border border-white/10'
                }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Custom in/out */}
      {isCustom && (
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <label className="text-[10px] text-white/40 font-semibold uppercase tracking-wide">Từ (giây)</label>
            <input
              type="number" min={0} max={end - 1} value={start}
              onChange={e => setStart(Math.max(0, parseFloat(e.target.value) || 0))}
              className="w-full mt-1 px-3 py-2 bg-white/8 border border-white/15 rounded-lg text-sm text-white outline-none focus:border-emerald-500/60"
            />
          </div>
          <div className="flex-1">
            <label className="text-[10px] text-white/40 font-semibold uppercase tracking-wide">Đến (giây)</label>
            <input
              type="number" min={start + 1} max={Math.min((duration || 600), start + 600)} value={end}
              onChange={e => setEnd(parseFloat(e.target.value) || start + 1)}
              className="w-full mt-1 px-3 py-2 bg-white/8 border border-white/15 rounded-lg text-sm text-white outline-none focus:border-emerald-500/60"
            />
          </div>
        </div>
      )}

      {/* Options */}
      <label className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" checked={isAudio} onChange={e => setIsAudio(e.target.checked)}
          className="w-4 h-4 rounded accent-emerald-500" />
        <span className="text-sm text-white/70">Chỉ trích âm thanh (MP3)</span>
      </label>

      {/* Duration hint */}
      <p className="text-xs text-white/30">
        Đoạn chọn: {(end - start).toFixed(0)}s {!localPath && '· Sẽ tải lại từ nguồn'}
      </p>

      <button
        onClick={handleSubmit}
        disabled={st.loading || end <= start}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Scissors className="w-4 h-4" />}
        {st.loading ? 'Đang xử lý...' : 'Cắt clip'}
      </button>

      <ResultRow result={st.result} label={`Clip ${start}s–${end}s`} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Audio ─────────────────────────────────────────────────────────────
function AudioTab({ localPath, sourceUrl, title, processing }) {
  const [format, setFormat]       = useState('mp3');
  const [quality, setQuality]     = useState('320');
  const [normalize, setNormalize] = useState(false);
  const st = processing.getState('audio');

  const handleSubmit = async () => {
    const body = { format, quality, normalize, filename: title || 'audio' };
    if (localPath) body.local_path = localPath;
    else           body.url        = sourceUrl;
    try { await processing.runAudio(body); } catch {}
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[10px] text-white/40 font-semibold uppercase tracking-wide">Định dạng</label>
          <div className="flex gap-1.5 mt-1">
            {['mp3', 'm4a'].map(f => (
              <button key={f} onClick={() => setFormat(f)}
                className={`flex-1 py-2 rounded-lg text-xs font-bold uppercase transition-all cursor-pointer
                  ${format === f ? 'bg-blue-600 text-white' : 'bg-white/8 text-white/50 hover:bg-white/15 border border-white/10'}`}>
                {f}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="text-[10px] text-white/40 font-semibold uppercase tracking-wide">Chất lượng</label>
          <div className="flex gap-1 mt-1 flex-wrap">
            {['128', '192', '320'].map(q => (
              <button key={q} onClick={() => setQuality(q)}
                className={`flex-1 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer
                  ${quality === q ? 'bg-blue-600 text-white' : 'bg-white/8 text-white/50 hover:bg-white/15 border border-white/10'}`}>
                {q}k
              </button>
            ))}
          </div>
        </div>
      </div>

      <label className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" checked={normalize} onChange={e => setNormalize(e.target.checked)}
          className="w-4 h-4 rounded accent-blue-500" />
        <span className="text-sm text-white/70">Cân bằng âm lượng (loudnorm)</span>
      </label>

      <button
        onClick={handleSubmit}
        disabled={st.loading}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Music className="w-4 h-4" />}
        {st.loading ? 'Đang trích...' : `Trích âm thanh ${format.toUpperCase()} ${quality}k`}
      </button>

      <ResultRow result={st.result} label={`${format.toUpperCase()} ${quality}kbps`} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: GIF / Loop ────────────────────────────────────────────────────────
function GifTab({ localPath, sourceUrl, title, duration, processing, userTier }) {
  const [subTab, setSubTab]       = useState('gif');
  const [gifPreset, setGifPreset] = useState(1);
  const [start, setStart]         = useState(0);
  const [end, setEnd]             = useState(Math.min(10, duration || 10));
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const stGif  = processing.getState('gif');
  const stLoop = processing.getState('loop');
  const st = subTab === 'gif' ? stGif : stLoop;
  const preset = GIF_PRESETS[gifPreset];
  const dur = end - start;
  const estKB = Math.round(preset.width * preset.fps * Math.max(dur, 1) * 0.05);

  const handleSubmit = async () => {
    const base = {
      start_time: start,
      end_time:   end,
      width:      preset.width,
      filename:   title || 'output',
    };
    if (localPath) base.local_path = localPath;
    else           base.url        = sourceUrl;

    try {
      if (subTab === 'gif') {
        await processing.runGif({ ...base, fps: preset.fps });
      } else {
        await processing.runMp4Loop(base);
      }
    } catch {}
  };

  return (
    <div className="space-y-4">
      {/* Sub-tabs */}
      <div className="flex gap-1 p-1 bg-slate-800/60 rounded-xl">
        {['gif', 'mp4'].map(t => (
          <button key={t} onClick={() => setSubTab(t === 'mp4' ? 'loop' : 'gif')}
            className={`flex-1 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer
              ${(t === 'gif' ? subTab === 'gif' : subTab === 'loop')
                ? 'bg-gradient-to-r from-violet-600 to-purple-600 text-white'
                : 'text-white/40 hover:text-white'}`}>
            {t === 'gif' ? 'GIF animated' : 'MP4 Loop'}
          </button>
        ))}
      </div>

      {/* Presets */}
      <div className="flex gap-1.5">
        {GIF_PRESETS.map((p, i) => {
          const locked = p.tier === 'pro' && !isPro;
          return (
            <button key={p.label} onClick={() => !locked && setGifPreset(i)}
              disabled={locked}
              className={`flex-1 py-2 rounded-lg text-xs font-semibold transition-all cursor-pointer
                ${i === gifPreset ? 'bg-violet-600 text-white' : 'bg-white/8 text-white/50 hover:bg-white/15 border border-white/10'}
                ${locked ? 'opacity-40 cursor-not-allowed' : ''}`}>
              {p.label}
              {locked && <Lock className="w-2.5 h-2.5 inline ml-1" />}
            </button>
          );
        })}
      </div>

      {/* Start/End */}
      <div className="flex items-center gap-3">
        {[
          ['start', start, setStart, 0,          end - 1],
          ['end',   end,   setEnd,   start + 1,  Math.min((duration || 30), start + 30)],
        ].map(([lbl, val, setVal, min, max]) => (
          <div key={lbl} className="flex-1">
            <label className="text-[10px] text-white/40 uppercase tracking-wide">{lbl === 'start' ? 'Từ' : 'Đến'} (giây)</label>
            <input type="number" min={min} max={max} value={val}
              onChange={e => setVal(parseFloat(e.target.value) || 0)}
              className="w-full mt-1 px-3 py-2 bg-white/8 border border-white/15 rounded-lg text-sm text-white outline-none focus:border-violet-500/60" />
          </div>
        ))}
      </div>

      {/* Size estimate */}
      <p className="text-xs text-white/30">
        {preset.desc} · {dur.toFixed(0)}s
        {subTab === 'gif' && ` · Ước tính ~${estKB > 1024 ? `${(estKB / 1024).toFixed(1)}MB` : `${estKB}KB`}`}
        {estKB > 5120 && subTab === 'gif' && <span className="text-amber-400 ml-1">⚠ Có thể nặng</span>}
      </p>

      <button onClick={handleSubmit} disabled={st.loading || dur <= 0 || dur > 30}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white font-semibold text-sm transition-colors cursor-pointer">
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Film className="w-4 h-4" />}
        {st.loading ? 'Đang tạo...' : subTab === 'gif' ? 'Tạo GIF' : 'Xuất MP4 Loop'}
      </button>

      <ResultRow result={st.result} label={subTab === 'gif' ? 'GIF' : 'MP4 Loop'} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Subtitle ──────────────────────────────────────────────────────────
function SubtitleTab({ videoInfo, sourceUrl, processing, userTier }) {
  const langs = videoInfo?.available_subtitle_languages || [];
  const [lang, setLang]     = useState(langs[0] || 'auto');
  const [fmt, setFmt]       = useState('srt');
  const [burn, setBurn]     = useState(false);
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const stSub  = processing.getState('subtitle');
  const stBurn = processing.getState('burn');
  const st = burn ? stBurn : stSub;

  const handleSubmit = async () => {
    try {
      if (burn) {
        await processing.runBurnSub({
          source_url: sourceUrl,
          video_path: videoInfo?.local_file_path,
          language:   lang,
          filename:   videoInfo?.title || 'video',
        });
      } else {
        await processing.runSubtitle({
          source_url: sourceUrl,
          language:   lang,
          format:     fmt,
          filename:   videoInfo?.title || 'subtitles',
        });
      }
    } catch {}
  };

  return (
    <div className="space-y-4">
      {langs.length === 0 ? (
        <div className="p-4 rounded-xl bg-slate-800/40 border border-white/8 text-center">
          <FileText className="w-8 h-8 text-white/20 mx-auto mb-2" />
          <p className="text-sm text-white/50">Không phát hiện phụ đề từ metadata.</p>
          <p className="text-xs text-white/30 mt-1">Thử tải thử — một số nguồn có phụ đề ẩn.</p>
        </div>
      ) : (
        <div>
          <label className="text-[10px] text-white/40 uppercase tracking-wide">Ngôn ngữ</label>
          <div className="flex gap-1.5 flex-wrap mt-1">
            {['auto', ...langs.slice(0, 5)].map(l => (
              <button key={l} onClick={() => setLang(l)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer
                  ${lang === l ? 'bg-teal-600 text-white' : 'bg-white/8 text-white/50 hover:bg-white/15 border border-white/10'}`}>
                {l === 'auto' ? 'Tự động' : l}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Format */}
      <div>
        <label className="text-[10px] text-white/40 uppercase tracking-wide">Định dạng</label>
        <div className="flex gap-1.5 mt-1">
          {['srt', 'vtt', 'txt'].map(f => (
            <button key={f} onClick={() => setFmt(f)} disabled={burn}
              className={`flex-1 py-2 rounded-lg text-xs font-bold uppercase transition-all cursor-pointer
                ${fmt === f && !burn ? 'bg-teal-600 text-white' : 'bg-white/8 text-white/50 hover:bg-white/15 border border-white/10'}
                ${burn ? 'opacity-40 cursor-not-allowed' : ''}`}>
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Burn-in toggle */}
      <label className={`flex items-center gap-2 ${!isPro ? 'opacity-60' : 'cursor-pointer'}`}>
        <input type="checkbox" checked={burn} disabled={!isPro}
          onChange={e => setBurn(e.target.checked)}
          className="w-4 h-4 rounded accent-teal-500" />
        <span className="text-sm text-white/70">Ghép phụ đề vào video (burn-in)</span>
        {!isPro && <span className="text-[10px] text-amber-400 ml-1">Pro</span>}
      </label>

      <button onClick={handleSubmit} disabled={st.loading}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white font-semibold text-sm transition-colors cursor-pointer">
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
        {st.loading ? 'Đang xử lý...' : burn ? 'Ghép phụ đề' : `Tải phụ đề ${fmt.toUpperCase()}`}
      </button>

      <ResultRow result={st.result} label={burn ? 'Video + phụ đề' : `Phụ đề ${fmt.toUpperCase()}`} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Package ───────────────────────────────────────────────────────────
function PackageTab({ videoInfo, sourceUrl }) {
  const [template, setTemplate] = useState('{title}');
  const title    = videoInfo?.title    || 'video';
  const platform = videoInfo?.platform || '';
  const date     = new Date().toISOString().slice(0, 10);

  const preview = template
    .replace('{title}',       title.slice(0, 40))
    .replace('{platform}',    platform || 'vidgrab')
    .replace('{date}',        date)
    .replace('{index:02d}',   '01');

  const downloadUrl = videoInfo?.local_file_path
    ? `${API}/api/v1/download-local?filepath=${encodeURIComponent(videoInfo.local_file_path)}&filename=${encodeURIComponent(preview)}`
    : null;

  return (
    <div className="space-y-4">
      <div>
        <label className="text-[10px] text-white/40 uppercase tracking-wide">Template tên file</label>
        <div className="flex flex-col gap-1.5 mt-1">
          {NAMING_TEMPLATES.map(t => (
            <button key={t.value} onClick={() => setTemplate(t.value)}
              className={`px-3 py-2 rounded-lg text-xs text-left transition-all cursor-pointer font-mono
                ${template === t.value ? 'bg-indigo-600/30 text-indigo-200 border border-indigo-500/50' : 'bg-white/5 text-white/50 hover:bg-white/10 border border-white/8'}`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Preview */}
      <div className="p-3 bg-slate-800/50 rounded-xl border border-white/8">
        <p className="text-[10px] text-white/35 uppercase tracking-wide mb-1">Xem trước tên</p>
        <p className="text-sm text-white/80 font-mono break-all">{preview}.mp4</p>
      </div>

      {downloadUrl ? (
        <a href={downloadUrl} download={`${preview}.mp4`}
          className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-colors">
          <Download className="w-4 h-4" />
          Tải với tên này
        </a>
      ) : (
        <p className="text-xs text-white/30 text-center">File local đã hết hạn — tải lại từ nguồn.</p>
      )}

      <p className="text-xs text-white/25 text-center">ZIP nhiều file: dùng tính năng Batch.</p>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────
export default function ProcessingHub({ videoInfo, localPath, sourceUrl, onClose, userTier = 'free', initialTab = 'trim' }) {
  const [activeTab, setActiveTab] = useState(initialTab);
  const processing = useProcessing();

  const title    = videoInfo?.title    || '';
  const duration = videoInfo?.duration || 0;
  const hasSubs  = (videoInfo?.available_subtitle_languages || []).length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full sm:max-w-lg bg-[#0a1628] border border-white/12 rounded-t-2xl sm:rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b border-white/8 shrink-0">
          {videoInfo?.thumbnail && (
            <img src={videoInfo.thumbnail} alt="" className="w-12 h-8 object-cover rounded-lg bg-black/30 shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-white truncate">{title || 'Xử lý media'}</p>
            <p className="text-[10px] text-white/35">Post-Processing Suite 2.0</p>
          </div>
          <button onClick={onClose} className="p-1.5 text-white/30 hover:text-white/70 transition-colors cursor-pointer shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-0.5 p-2 border-b border-white/6 overflow-x-auto shrink-0">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-all cursor-pointer shrink-0
                ${activeTab === id
                  ? 'bg-white/12 text-white'
                  : 'text-white/40 hover:text-white/70 hover:bg-white/6'}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
              {id === 'subtitle' && hasSubs && (
                <span className="w-1.5 h-1.5 rounded-full bg-teal-400 shrink-0" />
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {activeTab === 'trim' && (
            <TrimTab localPath={localPath} sourceUrl={sourceUrl} title={title} duration={duration} processing={processing} />
          )}
          {activeTab === 'audio' && (
            <AudioTab localPath={localPath} sourceUrl={sourceUrl} title={title} processing={processing} />
          )}
          {activeTab === 'gif' && (
            <GifTab localPath={localPath} sourceUrl={sourceUrl} title={title} duration={duration} processing={processing} userTier={userTier} />
          )}
          {activeTab === 'subtitle' && (
            <SubtitleTab videoInfo={videoInfo} sourceUrl={sourceUrl} processing={processing} userTier={userTier} />
          )}
          {activeTab === 'package' && (
            <PackageTab videoInfo={videoInfo} sourceUrl={sourceUrl} />
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2.5 border-t border-white/6 shrink-0">
          <p className="text-[10px] text-white/20 text-center">File xử lý hết hạn sau 20 phút · Giới hạn: trim 10 phút · GIF/loop 30 giây</p>
        </div>
      </div>
    </div>
  );
}

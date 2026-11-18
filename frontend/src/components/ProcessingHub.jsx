/**
 * ProcessingHub — Phase 22 Post-Processing Suite 2.0
 * ====================================================
 * Unified post-processing modal.
 *
 * Props:
 *   videoInfo    — { title, thumbnail, duration, local_file_id, original_url, available_subtitle_languages }
 *   localPath    — file id of the server-side copy (local_file_id; may be null)
 *   sourceUrl    — original source URL
 *   onClose()
 *   userTier     — 'free'|'pro'|'team'|'enterprise'
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  X, Download, Scissors, Music, Film, FileText, Package,
  Loader2, AlertCircle, CheckCircle2, Lock, ShieldCheck, Crown,
  Languages, Sparkles, Copy, Check,
} from 'lucide-react';
import { useProcessing } from '../hooks/useProcessing';
import ArtifactExpiry from './ArtifactExpiry';
import { localFileId, localDownloadUrl } from '../lib/localFile';

const API = import.meta.env.VITE_API_URL || '';

// Tab labels: results.json tabs.<id>
const TABS = [
  { id: 'trim',       icon: Scissors    },
  { id: 'reup',       icon: ShieldCheck, pro: true },
  { id: 'vietnamize', icon: Languages,   pro: true },
  { id: 'viral',      icon: Sparkles,    pro: true },
  { id: 'audio',      icon: Music       },
  { id: 'gif',        icon: Film        },
  { id: 'subtitle',   icon: FileText    },
  { id: 'package',    icon: Package     },
];

// Quick-trim presets in seconds
const TRIM_PRESETS = [
  { label: '5s',  value: 5  },
  { label: '10s', value: 10 },
  { label: '15s', value: 15 },
  { label: '30s', value: 30 },
  { label: '60s', value: 60 },
  { label: 'custom', value: 0  }, // 'custom' -> results.json trimPreset.custom
];

const GIF_PRESETS = [
  { label: 'small', key: 'small',  width: 320, fps: 10, desc: '~320px · 10fps' },
  { label: 'Social', key: 'social', width: 480, fps: 15, desc: '~480px · 15fps' },
  { label: 'HQ', key: 'hq',     width: 720, fps: 24, desc: '720px · 24fps · Pro', tier: 'pro' },
];

const NAMING_TEMPLATES = [
  { label: '{title}',              value: '{title}'             },
  { label: '{platform}_{title}',   value: '{platform}_{title}'  },
  { label: '{index:02d}_{title}',  value: '{index:02d}_{title}' },
  { label: '{date}_{title}',       value: '{date}_{title}'      },
];

function ResultRow({ result, label }) {
  const { t } = useTranslation('results');
  if (!result) return null;
  return (
    <div className="mt-3 flex items-center gap-2 p-2.5 bg-success-soft border border-success/40 rounded-xl">
      <CheckCircle2 className="w-4 h-4 text-success shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-success font-semibold truncate">{label || t('hub.done')}</p>
        {result.file_size_mb && (
          <p className="text-[10px] text-fg-muted">{result.file_size_mb.toFixed(1)} MB</p>
        )}
        {result.expires_in_seconds && (
          <ArtifactExpiry expiresAt={new Date(Date.now() + result.expires_in_seconds * 1000).toISOString()} />
        )}
      </div>
      {result.download_url && (
        <a
          href={result.download_url.startsWith('/') ? `${API}${result.download_url}` : result.download_url}
          download
          className="flex items-center gap-1 px-2.5 py-1.5 bg-accent hover:opacity-90 text-accent-fg text-xs font-semibold rounded-lg transition-colors"
        >
          <Download className="w-3.5 h-3.5" /> {t('hub.download')}
        </a>
      )}
    </div>
  );
}

function ErrorRow({ error }) {
  if (!error) return null;
  return (
    <div className="mt-3 flex items-start gap-2 p-2.5 bg-danger-soft border border-danger/30 rounded-xl">
      <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
      <p className="text-xs text-danger">{error}</p>
    </div>
  );
}

// ── Tab: Trim ──────────────────────────────────────────────────────────────
function TrimTab({ localPath, sourceUrl, title, duration, processing, userTier, onOpenUpgrade }) {
  const maxTrimSec = ['team', 'enterprise', 'api', 'admin'].includes(userTier)
    ? 1800
    : userTier === 'pro'
      ? 600
      : userTier === 'starter'
        ? 120
        : 10;
  const [preset, setPreset]     = useState(10);
  const [isCustom, setIsCustom] = useState(false);
  const [start, setStart]       = useState(0);
  const [end, setEnd]           = useState(10);
  const [isAudio, setIsAudio]   = useState(false);
  const { t } = useTranslation('results');
  const st = processing.getState('trim');

  const handlePreset = (p) => {
    if (p.value === 0) { setIsCustom(true); return; }
    setIsCustom(false);
    setPreset(p.value);
    setStart(0);
    setEnd(p.value);
  };

  const handleSubmit = async () => {
    if ((end - start) > maxTrimSec) {
      onOpenUpgrade?.('tier_required_feature');
      return;
    }
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
        <p className="text-xs text-fg-muted mb-2 font-medium">{t('trim.quick')}</p>
        <div className="flex gap-1.5 flex-wrap">
          {TRIM_PRESETS.map(p => (
            <button
              key={p.label}
              onClick={() => handlePreset(p)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer
                ${(!isCustom && end - start === p.value && p.value !== 0) || (isCustom && p.value === 0)
                  ? 'bg-accent text-accent-fg'
                  : 'bg-surface-2 text-fg-2 hover:bg-line hover:text-fg border border-line'
                }`}
            >
              {p.value === 0 ? t('trimPreset.custom') : p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Custom in/out */}
      {isCustom && (
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <label className="text-[10px] text-fg-muted font-semibold uppercase tracking-wide">{t('hub.fromSec')}</label>
            <input
              type="number" min={0} max={end - 1} value={start}
              onChange={e => setStart(Math.max(0, parseFloat(e.target.value) || 0))}
              className="w-full mt-1 px-3 py-2 bg-surface-2 border border-line rounded-lg text-sm text-fg outline-none focus:border-success/60"
            />
          </div>
          <div className="flex-1">
            <label className="text-[10px] text-fg-muted font-semibold uppercase tracking-wide">{t('hub.toSec')}</label>
            <input
              type="number" min={start + 1} max={Math.min((duration || maxTrimSec), start + maxTrimSec)} value={end}
              onChange={e => setEnd(parseFloat(e.target.value) || start + 1)}
              className="w-full mt-1 px-3 py-2 bg-surface-2 border border-line rounded-lg text-sm text-fg outline-none focus:border-success/60"
            />
          </div>
        </div>
      )}

      {/* Options */}
      <label className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" checked={isAudio} onChange={e => setIsAudio(e.target.checked)}
          className="w-4 h-4 rounded accent-accent" />
        <span className="text-sm text-fg-2">{t('trim.audioOnly')}</span>
      </label>

      {/* Duration hint */}
      <p className="text-xs text-fg-muted">
        {t('trim.selected', { seconds: (end - start).toFixed(0) })} {!localPath && t('trim.refetch')}
      </p>

      <button
        onClick={handleSubmit}
        disabled={st.loading || end <= start}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:opacity-90 disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Scissors className="w-4 h-4" />}
        {st.loading ? t('hub.processing') : t('trim.submit')}
      </button>

      <ResultRow result={st.result} label={t('trim.resultLabel', { start, end })} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Audio ─────────────────────────────────────────────────────────────
function AudioTab({ localPath, sourceUrl, title, processing }) {
  const [format, setFormat]       = useState('mp3');
  const [quality, setQuality]     = useState('320');
  const [normalize, setNormalize] = useState(false);
  const { t } = useTranslation('results');
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
          <label className="text-[10px] text-fg-muted font-semibold uppercase tracking-wide">{t('hub.format')}</label>
          <div className="flex gap-1.5 mt-1">
            {['mp3', 'm4a'].map(f => (
              <button key={f} onClick={() => setFormat(f)}
                className={`flex-1 py-2 rounded-lg text-xs font-bold uppercase transition-all cursor-pointer
                  ${format === f ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-muted hover:bg-line border border-line'}`}>
                {f}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="text-[10px] text-fg-muted font-semibold uppercase tracking-wide">{t('audio.quality')}</label>
          <div className="flex gap-1 mt-1 flex-wrap">
            {['128', '192', '320'].map(q => (
              <button key={q} onClick={() => setQuality(q)}
                className={`flex-1 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer
                  ${quality === q ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-muted hover:bg-line border border-line'}`}>
                {q}k
              </button>
            ))}
          </div>
        </div>
      </div>

      <label className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" checked={normalize} onChange={e => setNormalize(e.target.checked)}
          className="w-4 h-4 rounded accent-accent" />
        <span className="text-sm text-fg-2">{t('audio.normalize')}</span>
      </label>

      <button
        onClick={handleSubmit}
        disabled={st.loading}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:bg-accent-hover disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Music className="w-4 h-4" />}
        {st.loading ? t('audio.extracting') : t('audio.submit', { format: format.toUpperCase(), quality })}
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
  const { t } = useTranslation('results');
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
      <div className="flex gap-1 p-1 bg-surface rounded-xl">
        {['gif', 'mp4'].map(k => (
          <button key={k} onClick={() => setSubTab(k === 'mp4' ? 'loop' : 'gif')}
            className={`flex-1 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer
              ${(k === 'gif' ? subTab === 'gif' : subTab === 'loop')
                ? 'bg-accent text-accent-fg'
                : 'text-fg-muted hover:text-fg'}`}>
            {k === 'gif' ? t('gif.subGif') : t('gif.subLoop')}
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
                ${i === gifPreset ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-muted hover:bg-line border border-line'}
                ${locked ? 'opacity-40 cursor-not-allowed' : ''}`}>
              {t(`gifPreset.${p.key}`)}
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
            <label className="text-[10px] text-fg-muted uppercase tracking-wide">{lbl === 'start' ? t('hub.fromSec') : t('hub.toSec')}</label>
            <input type="number" min={min} max={max} value={val}
              onChange={e => setVal(parseFloat(e.target.value) || 0)}
              className="w-full mt-1 px-3 py-2 bg-surface-2 border border-line rounded-lg text-sm text-fg outline-none focus:border-line" />
          </div>
        ))}
      </div>

      {/* Size estimate */}
      <p className="text-xs text-fg-muted">
        {preset.desc} · {dur.toFixed(0)}s
        {subTab === 'gif' && ` ${t('gif.estimate', { size: estKB > 1024 ? `${(estKB / 1024).toFixed(1)}MB` : `${estKB}KB` })}`}
        {estKB > 5120 && subTab === 'gif' && <span className="text-accent-text ml-1">{t('gif.heavy')}</span>}
      </p>

      <button onClick={handleSubmit} disabled={st.loading || dur <= 0 || dur > 30}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:bg-accent-hover disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer">
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Film className="w-4 h-4" />}
        {st.loading ? t('gif.creating') : subTab === 'gif' ? t('gif.makeGif') : t('gif.exportLoop')}
      </button>

      <ResultRow result={st.result} label={subTab === 'gif' ? 'GIF' : t('gif.subLoop')} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Subtitle ──────────────────────────────────────────────────────────
function SubtitleTab({ videoInfo, sourceUrl, processing, userTier, onOpenUpgrade }) {
  const langs = videoInfo?.available_subtitle_languages || [];
  const [lang, setLang]     = useState(langs[0] || 'auto');
  const [fmt, setFmt]       = useState('srt');
  const [burn, setBurn]     = useState(false);
  const [translateVi, setTranslateVi] = useState(false);
  const { t, i18n } = useTranslation('results');
  const isEn = i18n.language === 'en';
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const stSub  = processing.getState('subtitle');
  const stBurn = processing.getState('burn');
  const st = burn ? stBurn : stSub;

  const handleSubmit = async () => {
    try {
      if (burn) {
        if (!isPro) {
          onOpenUpgrade?.('tier_required_feature');
          return;
        }
        await processing.runBurnSub({
          source_url: sourceUrl,
          video_path: localFileId(videoInfo),
          language:   lang,
          translate_to_vi: translateVi,
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
        <div className="p-4 rounded-xl bg-surface-2 border border-line text-center">
          <FileText className="w-8 h-8 text-fg-muted mx-auto mb-2" />
          <p className="text-sm text-fg-muted">{t('subtitle.none')}</p>
          <p className="text-xs text-fg-muted mt-1">{t('subtitle.noneHint')}</p>
        </div>
      ) : (
        <div>
          <label className="text-[10px] text-fg-muted uppercase tracking-wide">{t('subtitle.language')}</label>
          <div className="flex gap-1.5 flex-wrap mt-1">
            {['auto', ...langs.slice(0, 5)].map(l => (
              <button key={l} onClick={() => setLang(l)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer
                  ${lang === l ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-muted hover:bg-line border border-line'}`}>
                {l === 'auto' ? t('subtitle.auto') : l}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Format */}
      <div>
        <label className="text-[10px] text-fg-muted uppercase tracking-wide">{t('hub.format')}</label>
        <div className="flex gap-1.5 mt-1">
          {['srt', 'vtt', 'txt'].map(f => (
            <button key={f} onClick={() => setFmt(f)} disabled={burn}
              className={`flex-1 py-2 rounded-lg text-xs font-bold uppercase transition-all cursor-pointer
                ${fmt === f && !burn ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-muted hover:bg-line border border-line'}
                ${burn ? 'opacity-40 cursor-not-allowed' : ''}`}>
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Burn-in toggle */}
      <div className="space-y-2.5 p-3 rounded-xl bg-surface-2 border border-line">
        <label className="flex items-center justify-between gap-2 cursor-pointer"
          onClick={(e) => {
            if (!isPro) {
              e.preventDefault();
              onOpenUpgrade?.('tier_required_feature');
            }
          }}>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={burn}
              onChange={e => isPro && setBurn(e.target.checked)}
              className="w-4 h-4 rounded accent-accent" />
            <span className="text-sm font-medium text-fg">{t('subtitle.burn')}</span>
          </div>
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent/15 text-accent-text border border-accent/30">
            <Crown className="w-2.5 h-2.5" /> PRO
          </span>
        </label>

        {burn && (
          <label className="flex items-center gap-2 pl-6 cursor-pointer">
            <input type="checkbox" checked={translateVi}
              onChange={e => setTranslateVi(e.target.checked)}
              className="w-4 h-4 rounded accent-accent" />
            <span className="text-xs text-fg-2">
              {isEn
                ? 'Auto-translate subtitles into Vietnamese before burning (Hardsub VI)'
                : 'Dịch tự động phụ đề sang Tiếng Việt trước khi ghép cứng (Hardsub Việt hóa)'}
            </span>
          </label>
        )}
      </div>

      <button onClick={handleSubmit} disabled={st.loading}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:bg-accent-hover disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer">
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
        {st.loading ? t('hub.processing') : burn ? t('subtitle.burnSubmit') : t('subtitle.download', { format: fmt.toUpperCase() })}
      </button>

      <ResultRow result={st.result} label={burn ? t('subtitle.resultBurn') : t('subtitle.resultSub', { format: fmt.toUpperCase() })} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Reup Clean v2 (Anti-Duplicate Pro Feature) ────────────────────────
const REUP_LEVELS = [
  {
    id: 'light',
    viLabel: 'Nhẹ — Chỉ đổi Metadata & MD5',
    enLabel: 'Light — Metadata & MD5 Only',
    viDesc: 'Xóa metadata gốc, đổi chữ ký MD5 (< 2 giây, giữ nguyên 100% chất lượng).',
    enDesc: 'Strip container metadata & generate fresh MD5 (< 2s, lossless).',
  },
  {
    id: 'medium',
    viLabel: 'Trung bình — Phá vân tay hình & tiếng (Khuyên dùng)',
    enLabel: 'Medium — Visual & Audio Fingerprint (Recommended)',
    viDesc: 'Hue +2°, Crop 2px, Speed 1.005x, Highpass 20Hz. Mắt thường không thấy khác biệt.',
    enDesc: 'Hue +2°, Crop 2px, Speed 1.005x, Highpass 20Hz. Imperceptible to viewers.',
  },
  {
    id: 'strong',
    viLabel: 'Mạnh — Đổi hoàn toàn fingerprint',
    enLabel: 'Strong — Full Visual & Audio Shift',
    viDesc: 'Hue +3°, Crop 4px, Contrast, Echo nhẹ, Speed 1.01x. Tối ưu chống detect reup.',
    enDesc: 'Hue +3°, Crop 4px, Contrast, micro-echo, Speed 1.01x. Maximum evasion.',
  },
];

function CopyInline({ text, label }) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(text).catch(() => {});
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface border border-line hover:border-accent/40 text-[10px] font-semibold text-fg-2 transition-colors cursor-pointer shrink-0"
    >
      {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
      {copied ? 'Đã copy' : label}
    </button>
  );
}

function ReupCleanTab({ localPath, sourceUrl, title, platform, processing, userTier, onOpenUpgrade }) {
  const [level, setLevel]           = useState('medium');
  const [mirrorFlip, setMirrorFlip] = useState(false);
  const [cleanName, setCleanName]   = useState(true);
  const { i18n } = useTranslation('results');
  const isEn = i18n.language === 'en';
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const st = processing.getState('reup');

  const handleSubmit = async () => {
    if (!isPro) {
      onOpenUpgrade?.('tier_required_feature');
      return;
    }
    const body = {
      level,
      mirror_flip: mirrorFlip,
      clean_name:  cleanName,
      platform:    platform || '',
      filename:    title || 'video',
    };
    if (localPath) body.local_path = localPath;
    else           body.url        = sourceUrl;
    try { await processing.runReupClean(body); } catch { /* error captured in hook state */ }
  };

  const ad = st.result?.anti_duplicate;
  const hashtagsText = (ad?.suggested_hashtags || []).join(' ');

  return (
    <div className="space-y-4">
      <div className="p-3 rounded-xl bg-accent/10 border border-accent/25 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-fg flex items-center gap-1.5">
            <ShieldCheck className="w-4 h-4 text-accent-text shrink-0" />
            {isEn ? 'Anti-Duplicate Video Cleaner v2' : 'Chống trùng lặp Reup v2 (Phá Fingerprint)'}
          </p>
          <p className="text-[11px] text-fg-muted mt-1">
            {isEn
              ? 'Breaks binary MD5, visual pHash/dHash & audio fingerprints, and cleans spammy titles for TikTok, Reels & Shorts.'
              : 'Thay đổi MD5, vân tay hình/tiếng (Hue, Crop, Speed, Highpass) và dọn sạch tiêu đề rác để đăng lại an toàn trên TikTok, Reels, Shorts.'}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent text-accent-fg shrink-0">
          <Crown className="w-3 h-3" /> PRO
        </span>
      </div>

      {/* 3 intensity levels */}
      <div className="space-y-2">
        <label className="text-[10px] text-fg-muted uppercase tracking-wide">
          {isEn ? 'Anti-duplicate intensity level' : 'Chọn mức độ chống trùng lặp'}
        </label>
        {REUP_LEVELS.map(lv => (
          <label
            key={lv.id}
            className={`flex items-start gap-2.5 p-2.5 rounded-xl border cursor-pointer transition-colors ${
              level === lv.id ? 'bg-accent-soft border-accent/50' : 'bg-surface-2 border-line hover:border-line-strong'
            }`}
          >
            <input
              type="radio"
              name="reup-level"
              checked={level === lv.id}
              onChange={() => setLevel(lv.id)}
              className="mt-0.5 accent-accent"
            />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold text-fg">{isEn ? lv.enLabel : lv.viLabel}</p>
              <p className="text-[11px] text-fg-muted">{isEn ? lv.enDesc : lv.viDesc}</p>
            </div>
          </label>
        ))}
      </div>

      {/* Additional switches */}
      <div className="space-y-2">
        <label className="flex items-start gap-2.5 p-2.5 rounded-xl bg-surface-2 border border-line cursor-pointer">
          <input
            type="checkbox"
            checked={mirrorFlip}
            onChange={e => setMirrorFlip(e.target.checked)}
            className="w-4 h-4 mt-0.5 rounded accent-accent"
          />
          <div>
            <p className="text-xs font-semibold text-fg">
              {isEn ? 'Horizontal Mirror Flip (hflip)' : 'Lật ngang khung hình (Mirror Flip)'}
            </p>
            <p className="text-[11px] text-fg-muted">
              {isEn ? 'Flips video horizontally (note: reverses on-screen text)' : 'Đảo chiều ngang video (lưu ý: chữ trong video sẽ bị ngược)'}
            </p>
          </div>
        </label>

        <label className="flex items-start gap-2.5 p-2.5 rounded-xl bg-surface-2 border border-line cursor-pointer">
          <input
            type="checkbox"
            checked={cleanName}
            onChange={e => setCleanName(e.target.checked)}
            className="w-4 h-4 mt-0.5 rounded accent-accent"
          />
          <div>
            <p className="text-xs font-semibold text-fg">
              {isEn ? 'Smart Clean Filename & Hashtag Suggestions' : 'Tự động làm sạch tên file & gợi ý Hashtag đăng lại'}
            </p>
            <p className="text-[11px] text-fg-muted">
              {isEn ? 'Strips #fyp, spam tags & watermark domains from filename' : 'Loại bỏ #fyp, rác chia sẻ Douyin/TikTok, tag [Official HD] khỏi tên file'}
            </p>
          </div>
        </label>
      </div>

      <button
        onClick={handleSubmit}
        disabled={st.loading}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:opacity-90 disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
        {!isPro
          ? (isEn ? 'Unlock Pro to Clean Reup Video' : 'Nâng cấp Pro để Chống trùng Reup')
          : st.loading
            ? (isEn ? 'Processing fingerprint & MD5...' : 'Đang xử lý fingerprint & MD5...')
            : (isEn ? 'Clean Video & Break Fingerprint' : 'Xử lý Chống trùng & Xuất video')}
      </button>

      {ad && (
        <div className="space-y-2 p-3 rounded-xl bg-surface-2 border border-line text-[11px]">
          <div className="font-mono text-fg-muted break-all space-y-0.5">
            {ad.original_md5 && <p><span className="text-fg-muted">MD5 gốc:</span> {ad.original_md5}</p>}
            {ad.new_md5 && <p><span className="font-bold text-success">MD5 mới:</span> {ad.new_md5}</p>}
          </div>
          {ad.clean_filename && (
            <p className="text-fg-2">
              <span className="font-semibold">File sạch:</span> <span className="font-mono">{ad.clean_filename}</span>
            </p>
          )}
          {ad.clean_title && (
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-line">
              <p className="text-fg truncate"><span className="font-semibold">Tiêu đề sạch:</span> {ad.clean_title}</p>
              <CopyInline text={ad.clean_title} label="Copy tiêu đề" />
            </div>
          )}
          {hashtagsText && (
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-line">
              <p className="text-accent-text truncate">{hashtagsText}</p>
              <CopyInline text={hashtagsText} label="Copy tags" />
            </div>
          )}
        </div>
      )}
      <ResultRow result={st.result} label={ad?.clean_filename || (isEn ? 'Cleaned Reup Video' : 'Video chống trùng đã sẵn sàng')} />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: AI Vietnamize ("Tải & Việt hóa 1 chạm") ───────────────────────────
const TARGET_LANGS = [
  { code: 'vi', label: 'Tiếng Việt (vi)' },
  { code: 'en', label: 'English (en)' },
  { code: 'ja', label: '日本語 (ja)' },
  { code: 'ko', label: '한국어 (ko)' },
  { code: 'zh', label: '中文 (zh)' },
  { code: 'fr', label: 'Français (fr)' },
  { code: 'es', label: 'Español (es)' },
  { code: 'th', label: 'ไทย (th)' },
  { code: 'id', label: 'Bahasa Indonesia (id)' },
];

function VietnamizeTab({ sourceUrl, title, processing, userTier, onOpenUpgrade }) {
  const [targetLang, setTargetLang] = useState('vi');
  const [burnSubs, setBurnSubs]     = useState(true);
  const [fontSize, setFontSize]     = useState(22);
  const { i18n } = useTranslation('results');
  const isEn = i18n.language === 'en';
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const st = processing.getState('vietnamize');

  const handleSubmit = async () => {
    if (!isPro) {
      onOpenUpgrade?.('tier_required_feature');
      return;
    }
    if (!sourceUrl) return;
    try {
      await processing.runVietnamize({
        url: sourceUrl,
        target_lang: targetLang,
        source_lang: 'auto',
        burn_subtitles: burnSubs,
        font_size: fontSize,
        filename: title || 'video',
      });
    } catch { /* error captured in hook state */ }
  };

  return (
    <div className="space-y-4">
      <div className="p-3 rounded-xl bg-accent/10 border border-accent/25 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-fg flex items-center gap-1.5">
            <Languages className="w-4 h-4 text-accent-text shrink-0" />
            {isEn ? 'AI 1-Click Transcribe, Translate & Burn Subtitles' : 'AI Việt hóa 1 chạm (Nghe → Dịch → Ghép phụ đề)'}
          </p>
          <p className="text-[11px] text-fg-muted mt-1">
            {isEn
              ? 'Uses AI speech recognition to generate accurate transcripts from any video without existing subtitles, translates, and hardcodes them.'
              : 'Tự động nghe lời thoại bằng AI (kể cả video không có phụ đề sẵn), dịch mượt sang tiếng Việt/đa ngôn ngữ và ghép cứng vào MP4.'}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent text-accent-fg shrink-0">
          <Crown className="w-3 h-3" /> PRO
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[10px] text-fg-muted uppercase tracking-wide">
            {isEn ? 'Target language' : 'Ngôn ngữ dịch đích'}
          </label>
          <select
            value={targetLang}
            onChange={e => setTargetLang(e.target.value)}
            className="mt-1 w-full bg-surface-2 border border-line rounded-lg px-2.5 py-2 text-xs text-fg focus:outline-none focus:border-accent"
          >
            {TARGET_LANGS.map(l => (
              <option key={l.code} value={l.code}>{l.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-[10px] text-fg-muted uppercase tracking-wide">
            {isEn ? 'Font size' : 'Cỡ chữ phụ đề'}
          </label>
          <select
            value={fontSize}
            onChange={e => setFontSize(Number(e.target.value))}
            disabled={!burnSubs}
            className="mt-1 w-full bg-surface-2 border border-line rounded-lg px-2.5 py-2 text-xs text-fg focus:outline-none focus:border-accent disabled:opacity-50"
          >
            {[18, 22, 26, 30].map(sz => (
              <option key={sz} value={sz}>{sz}px{sz === 22 ? ' (Chuẩn)' : ''}</option>
            ))}
          </select>
        </div>
      </div>

      <label className="flex items-start gap-2.5 p-2.5 rounded-xl bg-surface-2 border border-line cursor-pointer">
        <input
          type="checkbox"
          checked={burnSubs}
          onChange={e => setBurnSubs(e.target.checked)}
          className="w-4 h-4 mt-0.5 rounded accent-accent"
        />
        <div>
          <p className="text-xs font-semibold text-fg">
            {isEn ? 'Burn subtitles directly into MP4 video' : 'Ghép cứng phụ đề trực tiếp vào video MP4'}
          </p>
          <p className="text-[11px] text-fg-muted">
            {isEn ? 'Uncheck to export only the translated .SRT file (much faster).' : 'Bỏ chọn nếu chỉ muốn tải nhanh file phụ đề .SRT đã dịch.'}
          </p>
        </div>
      </label>

      <p className="text-[11px] text-fg-muted">
        ⏱ {isEn ? 'AI pipeline typically takes 1–3 minutes depending on video duration (max 30 min).' : 'Quá trình AI nghe + dịch + render thường mất 1–3 phút tùy độ dài video (tối đa 30 phút).'}
      </p>

      <button
        onClick={handleSubmit}
        disabled={st.loading || !sourceUrl}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:opacity-90 disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Languages className="w-4 h-4" />}
        {!isPro
          ? (isEn ? 'Unlock Pro for AI Subtitles' : 'Nâng cấp Pro để dùng AI Việt hóa')
          : st.loading
            ? (isEn ? 'Transcribing & translating with AI...' : 'AI đang nghe, dịch & ghép phụ đề...')
            : (isEn ? 'Start AI Subtitling' : 'Bắt đầu Việt hóa bằng AI')}
      </button>

      {st.result && (
        <div className="px-3 py-2 rounded-lg bg-surface-2 border border-line text-[11px] text-fg-2 flex flex-wrap gap-3">
          {st.result.language && <span>Ngôn ngữ: <strong>{st.result.language.toUpperCase()}</strong></span>}
          {st.result.cue_count != null && <span>Số dòng phụ đề: <strong>{st.result.cue_count}</strong></span>}
          {st.result.duration_seconds != null && <span>Thời lượng: <strong>{st.result.duration_seconds}s</strong></span>}
        </div>
      )}
      <ResultRow
        result={st.result}
        label={st.result?.type === 'subtitle' ? `Phụ đề .SRT (${targetLang.toUpperCase()})` : `Video đã Việt hóa (${targetLang.toUpperCase()})`}
      />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: AI Viral Clipper (9:16 Highlights) ─────────────────────────────────
function ViralClipsTab({ sourceUrl, title, duration, processing, userTier, onOpenUpgrade }) {
  const [aspectRatio, setAspectRatio]   = useState('9:16');
  const [maxClips, setMaxClips]         = useState(3);
  const [clipDuration, setClipDuration] = useState(45);
  const [burnSubs, setBurnSubs]         = useState(true);
  const { i18n } = useTranslation('results');
  const isEn = i18n.language === 'en';
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const tooShort = duration > 0 && duration < 30;
  const st = processing.getState('viral');

  const handleSubmit = async () => {
    if (!isPro) {
      onOpenUpgrade?.('tier_required_feature');
      return;
    }
    if (!sourceUrl || tooShort) return;
    try {
      await processing.runViralClips({
        url: sourceUrl,
        max_clips: maxClips,
        clip_duration: clipDuration,
        aspect_ratio: aspectRatio,
        burn_subtitles: burnSubs,
        font_size: 28,
        filename: title || 'clips',
      });
    } catch { /* error captured in hook state */ }
  };

  return (
    <div className="space-y-4">
      <div className="p-3 rounded-xl bg-accent/10 border border-accent/25 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-fg flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-accent-text shrink-0" />
            {isEn ? 'AI Viral Clipper (Auto-Cut Shorts / Reels / TikTok)' : 'AI Cắt Clip Viral tự động (TikTok / Reels / Shorts)'}
          </p>
          <p className="text-[11px] text-fg-muted mt-1">
            {isEn
              ? 'Detects audio peaks, scene action & AI hook moments from long videos, crops to 9:16 vertical, burns captions, and bundles a ZIP.'
              : 'Phân tích cao trào hình ảnh, âm thanh & câu hook bằng AI từ video dài, tự crop khung dọc 9:16 kèm phụ đề và đóng gói ZIP.'}
          </p>
        </div>
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent text-accent-fg shrink-0">
          <Crown className="w-3 h-3" /> PRO
        </span>
      </div>

      {tooShort && (
        <div className="p-2.5 rounded-xl bg-warning-soft border border-warning/30 text-xs text-warning">
          {isEn ? 'Video must be at least 30 seconds long to extract viral clips.' : 'Video cần dài tối thiểu 30 giây để tách clip viral.'}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-[10px] text-fg-muted uppercase tracking-wide">
            {isEn ? 'Aspect Ratio' : 'Tỷ lệ khung'}
          </label>
          <select
            value={aspectRatio}
            onChange={e => setAspectRatio(e.target.value)}
            className="mt-1 w-full bg-surface-2 border border-line rounded-lg px-2 py-2 text-xs text-fg focus:outline-none focus:border-accent"
          >
            <option value="9:16">9:16 (Dọc)</option>
            <option value="1:1">1:1 (Vuông)</option>
            <option value="16:9">16:9 (Ngang)</option>
          </select>
        </div>

        <div>
          <label className="text-[10px] text-fg-muted uppercase tracking-wide">
            {isEn ? 'Clips count' : 'Số lượng clip'}
          </label>
          <select
            value={maxClips}
            onChange={e => setMaxClips(Number(e.target.value))}
            className="mt-1 w-full bg-surface-2 border border-line rounded-lg px-2 py-2 text-xs text-fg focus:outline-none focus:border-accent"
          >
            {[3, 4, 5].map(n => (
              <option key={n} value={n}>{n} clips</option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-[10px] text-fg-muted uppercase tracking-wide">
            {isEn ? 'Target length' : 'Độ dài/clip'}
          </label>
          <select
            value={clipDuration}
            onChange={e => setClipDuration(Number(e.target.value))}
            className="mt-1 w-full bg-surface-2 border border-line rounded-lg px-2 py-2 text-xs text-fg focus:outline-none focus:border-accent"
          >
            {[30, 45, 60].map(sec => (
              <option key={sec} value={sec}>~{sec} giây</option>
            ))}
          </select>
        </div>
      </div>

      <label className="flex items-start gap-2.5 p-2.5 rounded-xl bg-surface-2 border border-line cursor-pointer">
        <input
          type="checkbox"
          checked={burnSubs}
          onChange={e => setBurnSubs(e.target.checked)}
          className="w-4 h-4 mt-0.5 rounded accent-accent"
        />
        <div>
          <p className="text-xs font-semibold text-fg">
            {isEn ? 'Auto-burn captions onto clips (when available)' : 'Tự động ghép phụ đề vào từng clip (nếu nguồn có phụ đề)'}
          </p>
        </div>
      </label>

      <button
        onClick={handleSubmit}
        disabled={st.loading || !sourceUrl || tooShort}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:opacity-90 disabled:opacity-50 text-accent-fg font-semibold text-sm transition-colors cursor-pointer"
      >
        {st.loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
        {!isPro
          ? (isEn ? 'Unlock Pro for AI Viral Clipper' : 'Nâng cấp Pro để Cắt Clip Viral')
          : st.loading
            ? (isEn ? 'Analyzing hooks & rendering clips...' : 'AI đang phân tích & cắt clip...')
            : (isEn ? `Extract ${maxClips} Viral Clips (ZIP)` : `Tạo ${maxClips} Clip Viral (ZIP)`)}
      </button>

      {Array.isArray(st.result?.clips) && st.result.clips.length > 0 && (
        <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
          {st.result.clips.map(c => (
            <div key={c.index} className="p-2 rounded-lg bg-surface-2 border border-line text-[11px]">
              <div className="flex items-center justify-between font-semibold text-fg">
                <span>Clip #{c.index} ({c.start}s – {c.end}s · {c.duration}s)</span>
                {c.confidence != null && <span className="text-accent-text">{Math.round(c.confidence * 100)}%</span>}
              </div>
              {c.reason && <p className="text-fg-muted mt-0.5">{c.reason}</p>}
              {c.hook_text && <p className="text-fg-2 italic mt-0.5">“{c.hook_text}”</p>}
            </div>
          ))}
        </div>
      )}

      <ResultRow
        result={st.result}
        label={st.result ? `Gói ${st.result.total_clips || maxClips} Clip Viral (${st.result.aspect_ratio || aspectRatio})` : undefined}
      />
      <ErrorRow error={st.error} />
    </div>
  );
}

// ── Tab: Package ───────────────────────────────────────────────────────────
function PackageTab({ videoInfo, sourceUrl }) {
  const [template, setTemplate] = useState('{title}');
  const { t } = useTranslation('results');
  const title    = videoInfo?.title    || 'video';
  const platform = videoInfo?.platform || '';
  const date     = new Date().toISOString().slice(0, 10);

  const preview = template
    .replace('{title}',       title.slice(0, 40))
    .replace('{platform}',    platform || 'vidgrab')
    .replace('{date}',        date)
    .replace('{index:02d}',   '01');

  const downloadUrl = localFileId(videoInfo)
    ? localDownloadUrl(API, localFileId(videoInfo), preview)
    : null;

  return (
    <div className="space-y-4">
      <div>
        <label className="text-[10px] text-fg-muted uppercase tracking-wide">{t('package.template')}</label>
        <div className="flex flex-col gap-1.5 mt-1">
          {NAMING_TEMPLATES.map(tpl => (
            <button key={tpl.value} onClick={() => setTemplate(tpl.value)}
              className={`px-3 py-2 rounded-lg text-xs text-left transition-all cursor-pointer font-mono
                ${template === tpl.value ? 'bg-surface-2 text-fg-2 border border-line' : 'bg-surface-2 text-fg-muted hover:bg-line border border-line'}`}>
              {tpl.label}
            </button>
          ))}
        </div>
      </div>

      {/* Preview */}
      <div className="p-3 bg-surface rounded-xl border border-line">
        <p className="text-[10px] text-fg-muted uppercase tracking-wide mb-1">{t('package.preview')}</p>
        <p className="text-sm text-fg-2 font-mono break-all">{preview}.mp4</p>
      </div>

      {downloadUrl ? (
        <a href={downloadUrl} download={`${preview}.mp4`}
          className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent hover:bg-accent-hover text-accent-fg font-semibold text-sm transition-colors">
          <Download className="w-4 h-4" />
          {t('package.useName')}
        </a>
      ) : (
        <p className="text-xs text-fg-muted text-center">{t('package.expired')}</p>
      )}

      <p className="text-xs text-fg-muted text-center">{t('package.zipHint')}</p>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────
export default function ProcessingHub({ videoInfo, localPath, sourceUrl, onClose, userTier = 'free', initialTab = 'trim', onOpenUpgrade }) {
  const [activeTab, setActiveTab] = useState(initialTab);
  const { t } = useTranslation('results');
  const processing = useProcessing();

  const title    = videoInfo?.title    || '';
  const duration = videoInfo?.duration || 0;
  const hasSubs  = (videoInfo?.available_subtitle_languages || []).length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full sm:max-w-lg bg-canvas border border-line rounded-t-2xl sm:rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b border-line shrink-0">
          {videoInfo?.thumbnail && (
            <img src={videoInfo.thumbnail} alt="" className="w-12 h-8 object-cover rounded-lg bg-black/30 shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-fg truncate">{title || t('hub.defaultTitle')}</p>
            <p className="text-[10px] text-fg-muted">Post-Processing Suite 2.0</p>
          </div>
          <button onClick={onClose} className="p-1.5 text-fg-muted hover:text-fg-2 transition-colors cursor-pointer shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-0.5 p-2 border-b border-line overflow-x-auto shrink-0">
          {TABS.map(({ id, icon: Icon, pro }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-all cursor-pointer shrink-0
                ${activeTab === id
                  ? 'bg-surface-2 text-fg'
                  : 'text-fg-muted hover:text-fg-2 hover:bg-surface-2'}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t(`tabs.${id}`)}
              {pro && (
                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-accent/15 text-accent-text">PRO</span>
              )}
              {id === 'subtitle' && hasSubs && (
                <span className="w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {activeTab === 'trim' && (
            <TrimTab localPath={localPath} sourceUrl={sourceUrl} title={title} duration={duration} processing={processing} userTier={userTier} onOpenUpgrade={onOpenUpgrade} />
          )}
          {activeTab === 'reup' && (
            <ReupCleanTab localPath={localPath} sourceUrl={sourceUrl} title={title} platform={videoInfo?.platform} processing={processing} userTier={userTier} onOpenUpgrade={onOpenUpgrade} />
          )}
          {activeTab === 'vietnamize' && (
            <VietnamizeTab sourceUrl={sourceUrl} title={title} processing={processing} userTier={userTier} onOpenUpgrade={onOpenUpgrade} />
          )}
          {activeTab === 'viral' && (
            <ViralClipsTab sourceUrl={sourceUrl} title={title} duration={duration} processing={processing} userTier={userTier} onOpenUpgrade={onOpenUpgrade} />
          )}
          {activeTab === 'audio' && (
            <AudioTab localPath={localPath} sourceUrl={sourceUrl} title={title} processing={processing} />
          )}
          {activeTab === 'gif' && (
            <GifTab localPath={localPath} sourceUrl={sourceUrl} title={title} duration={duration} processing={processing} userTier={userTier} />
          )}
          {activeTab === 'subtitle' && (
            <SubtitleTab videoInfo={videoInfo} sourceUrl={sourceUrl} processing={processing} userTier={userTier} onOpenUpgrade={onOpenUpgrade} />
          )}
          {activeTab === 'package' && (
            <PackageTab videoInfo={videoInfo} sourceUrl={sourceUrl} />
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-2.5 border-t border-line shrink-0">
          <p className="text-[10px] text-fg-muted text-center">{t('hub.footer')}</p>
        </div>
      </div>
    </div>
  );
}

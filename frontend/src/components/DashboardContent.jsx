import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Download, CheckCircle2, XCircle,
  Loader2, AlertCircle, Link2,
  Zap, Music, Video, Crown, Trash2, Clock, X,
  ClipboardPaste, Play, Pause, Scissors, ImageDown,
  Upload, ExternalLink, SkipBack, SkipForward,
  Clapperboard, List, Sparkles, Eraser, Move, ZoomIn,
  Layers, Stamp, CalendarClock
} from 'lucide-react';
import UpgradeModal from './UpgradeModal';
import QuickGuideSection from './QuickGuideSection';
import JSZip from 'jszip';
import { saveAs } from 'file-saver';
import { trackEvent, EVENT, setExperiments } from '../utils/trackEvent';
import { useLifecycleBanners } from '../hooks/useLifecycleBanners';
import LifecycleBanner from './LifecycleBanner';
import SmartActionsPanel from './SmartActionsPanel';
import { useClipboardPaste } from '../hooks/useClipboardPaste';
import { useNotifications } from '../context/NotificationContext';
import QuickActionBar from './QuickActionBar';
import SuccessCard from './SuccessCard';
import ProcessingHub from './ProcessingHub';
import { useSmartDefaults, detectPlatform } from '../hooks/useSmartDefaults';
import { usePresets } from '../hooks/usePresets';
import { useResolveInput } from '../hooks/useResolveInput';
import CapabilityBadge from './CapabilityBadge';
import PlatformStatusBanner from './PlatformStatusBanner';

const API_BASE = import.meta.env.VITE_API_URL || '';

// Returns true if this URL supports watermark removal (TikTok / Douyin only)
function isWatermarkPlatform(url = '') {
  const u = url.toLowerCase();
  return u.includes('tiktok.com') || u.includes('vm.tiktok.com') ||
         u.includes('douyin.com') || u.includes('v.douyin.com') ||
         u.includes('iesdouyin.com');
}

// Threads URL detection (public posts + profiles only)
function isThreadsUrl(url = '') {
  const u = url.toLowerCase();
  return u.includes('threads.net') || u.includes('threads.com');
}

// Honest, user-facing copy for each Threads error_code (keys must match the
// prefixed codes in backend threads_extractor.py ERR_* constants).
const THREADS_ERROR_COPY = {
  threads_invalid_url: 'Link Threads không hợp lệ.',
  threads_unsupported_url: 'Link Threads không được hỗ trợ. Chỉ nhận link bài viết hoặc trang cá nhân công khai.',
  threads_private_or_login_required: 'Nội dung này yêu cầu đăng nhập hoặc không công khai.',
  threads_no_media_found: 'Bài viết này không có media tải xuống được.',
  threads_media_unavailable: 'Bài Threads công khai nhưng không trích xuất được media.',
  threads_ambiguous_post_match: 'Phát hiện nhiều bài viết trong trang — không xác định chắc chắn được bài cần lấy. Hãy dán đúng link 1 bài.',
  threads_extraction_blocked: 'Threads tạm thời chặn yêu cầu. Vui lòng thử lại sau ít phút.',
  threads_extractor_failed: 'Không trích xuất được nội dung Threads này.',
};

// Parse response JSON safely — when backend is down nginx returns HTML (502/403/429)
async function safeJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      throw new Error('🔧 Máy chủ đang bảo trì hoặc khởi động lại. Vui lòng thử lại sau vài giây.');
    }
    if (response.status === 429) {
      throw new Error('⏳ Bạn đã gửi quá nhiều yêu cầu. Vui lòng chờ 1 phút rồi thử lại.');
    }
    throw new Error(`Lỗi máy chủ (${response.status}). Vui lòng thử lại.`);
  }
}

// ── User cookie helper ──────────────────────────────────────
function encodeUserCookies(text) {
  if (!text || !text.trim()) return undefined
  try {
    return btoa(unescape(encodeURIComponent(text.trim())))
  } catch { return undefined }
}

// ── Toast ───────────────────────────────────────────────────
const Toast = ({ message, show }) => (
  <div className={`fixed top-20 left-1/2 -translate-x-1/2 px-5 py-3 rounded-full shadow-xl bg-slate-800 text-white font-medium text-sm flex items-center gap-2 transition-all duration-300 z-[60] ${show ? 'translate-y-0 opacity-100' : '-translate-y-4 opacity-0 pointer-events-none'}`}>
    <CheckCircle2 className="w-5 h-5 text-emerald-400" />
    {message}
  </div>
);

// ── Resolution Badge ────────────────────────────────────────
const ResBadge = ({ label, height }) => {
  let colors = 'bg-slate-700 text-slate-300';
  if (height >= 2160) colors = 'bg-gradient-to-r from-[#FBBF24] to-[#F59E0B] text-[#012622]';
  else if (height >= 1440) colors = 'bg-gradient-to-r from-[#a78bfa] to-[#7c3aed] text-white';
  else if (height >= 1080) colors = 'bg-gradient-to-r from-[#10b981] to-[#059669] text-white';
  else if (height >= 720) colors = 'bg-[#0ea5e9] text-white';
  return (
    <span className={`px-2.5 py-0.5 rounded-lg text-xs font-black tracking-wide ${colors}`}>
      {label}
    </span>
  );
};

export default function DashboardContent() {
  const { withAuth, session } = useAuth();
  const [url, setUrl] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [delayedInfo, setDelayedInfo] = useState(null); // Phase 27D: admission-control delayed state
  const [videoInfo, setVideoInfo] = useState(null);
  const [spotifyData, setSpotifyData] = useState(null); // playlist / album track list
  const [artistData, setArtistData] = useState(null);   // Spotify artist overview
  const [artistTab, setArtistTab] = useState('top');    // 'top' | 'albums' | 'singles'
  const [artistPreviewKey, setArtistPreviewKey] = useState(null); // track id currently previewing
  const [expandedAlbum, setExpandedAlbum] = useState(null);       // album_id expanded
  const [albumTracksCache, setAlbumTracksCache] = useState({});   // {album_id: {state, tracks}}
  const artistPreviewRef = useRef(null);
  const [threadsData, setThreadsData] = useState(null); // Threads post/profile preview
  const [trackDownloads, setTrackDownloads] = useState({}); // { search_query: 'loading' | 'done' | 'error' }
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [upgradeErrorCode, setUpgradeErrorCode] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);
  const [formatTab, setFormatTab] = useState('video');
  const [recentDownloads, setRecentDownloads] = useState([]);
  const [toastMessage, setToastMessage] = useState('');
  const [isZipping, setIsZipping] = useState(false);
  const [zipProgress, setZipProgress] = useState(0);
  const [removeWatermark, setRemoveWatermark] = useState(true);
  const [downloadSubs, setDownloadSubs] = useState(false);
  const [subtitleMode, setSubtitleMode] = useState('off');   // off | file | burned | soft
  const [subtitleLang, setSubtitleLang] = useState('auto');   // auto | vi | en
  const [showUserCookie, setShowUserCookie] = useState(false)
  const [userCookieText, setUserCookieText] = useState('')
  const [scheduledAt, setScheduledAt] = useState('')
  const [showSchedulePicker, setShowSchedulePicker] = useState(false)
  const [selectedTracks, setSelectedTracks] = useState(new Set());
  // Phase 14: event tracking state
  const [sessionDownloadCount, setSessionDownloadCount] = useState(0);
  const [paywallTrigger, setPaywallTrigger] = useState(null);
  // Phase 24: quick preset selection
  const [selectedPreset, setSelectedPreset] = useState(null);
  const cancelZipRef = useRef(false);
  const previewRef = useRef(null);
  const [showPreview, setShowPreview] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [showTrimmer, setShowTrimmer] = useState(false);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [isTrimming, setIsTrimming] = useState(false);
  const [showCloudMenu, setShowCloudMenu] = useState(false);
  // ── GIF Converter state ──────────────────────────────────
  const [showGifPanel, setShowGifPanel] = useState(false);
  const [gifStart, setGifStart] = useState(0);
  const [gifEnd, setGifEnd] = useState(10);
  const [gifWidth, setGifWidth] = useState(480);
  const [gifFps, setGifFps] = useState(15);
  const [isConverting, setIsConverting] = useState(false);
  // ── Chapters state ───────────────────────────────────────
  const [showChapters, setShowChapters] = useState(false);
  const [downloadingChapter, setDownloadingChapter] = useState(null);
  // ── Logo Inpaint state ───────────────────────────────────
  const [showInpaint, setShowInpaint] = useState(false);
  const [inpaintStep, setInpaintStep] = useState('region'); // 'region' | 'preview' | 'processing' | 'done' | 'error'
  const [inpaintTempId, setInpaintTempId] = useState(null);
  const [inpaintFrameUrl, setInpaintFrameUrl] = useState(null);
  const [inpaintVideoDims, setInpaintVideoDims] = useState(null); // {width, height} from from-local
  const [inpaintPreset, setInpaintPreset] = useState(null);       // 'lower-right' etc
  const [inpaintPixelRegion, setInpaintPixelRegion] = useState(null); // {x,y,w,h} pixels after preview
  const [inpaintPreviewResult, setInpaintPreviewResult] = useState(null); // preview-frame response
  const [inpaintFinalUrl, setInpaintFinalUrl] = useState(null);
  const [inpaintError, setInpaintError] = useState('');
  const [inpaintLoading, setInpaintLoading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(null);
  // ── Merge Clips state ────────────────────────────────────
  const [showMerge, setShowMerge] = useState(false);
  const [mergeJobIds, setMergeJobIds] = useState('');
  const [mergeLoading, setMergeLoading] = useState(false);
  const [mergeResult, setMergeResult] = useState(null);
  const [mergeError, setMergeError] = useState('');
  // ── Watermark Embed state ────────────────────────────────
  const [showWatermark, setShowWatermark] = useState(false);
  const [wmType, setWmType] = useState('text');
  const [wmText, setWmText] = useState('');
  const [wmFontSize, setWmFontSize] = useState(28);
  const [wmPosition, setWmPosition] = useState('bottom-right');
  const [wmOpacity, setWmOpacity] = useState(0.8);
  const [wmImageB64, setWmImageB64] = useState(null);
  const [wmLoading, setWmLoading] = useState(false);
  const [wmPreviewUrl, setWmPreviewUrl] = useState(null);
  const [wmResult, setWmResult] = useState(null);
  const [wmError, setWmError] = useState('');
  const progressPollRef = useRef(null);
  const loadTimerRef = useRef(null);
  const [loadElapsed, setLoadElapsed] = useState(0);
  // ── Video Digest state ───────────────────────────────────
  const [showDigest, setShowDigest] = useState(() => localStorage.getItem('vg_show_digest') !== 'false');
  // Phase 19: mobile improvements
  const { addNotification } = useNotifications();
  const { clipboardURL, hasSuggestion, checkClipboard, clearSuggestion } = useClipboardPaste();
  const [lastDownloadInfo, setLastDownloadInfo] = useState(null);
  const [showSuccessCard, setShowSuccessCard] = useState(false);
  const [successCardInfo, setSuccessCardInfo] = useState(null);
  const [showProcessingHub, setShowProcessingHub] = useState(false);
  const [processingHubTab, setProcessingHubTab] = useState('trim');

  // Phase 14: load experiment variants once on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/events/experiments`)
      .then(r => r.json())
      .then(config => setExperiments(config))
      .catch(() => {});
  }, []);

  // Phase 21: preset + smart defaults
  const { allPresets, getPresetForPlatform, createPreset } = usePresets();
  const detectedPlatform = detectPlatform(url);
  const { defaults: smartDefaults, activePreset, recordUsed: recordSmartUsed } = useSmartDefaults(url, { activePresets: allPresets });

  // Phase 24: universal capability resolve (debounced, non-blocking)
  const { result: capResult, loading: capLoading } = useResolveInput(url, {
    enabled: !videoInfo && !isLoading,
  });

  // Phase 14: lifecycle banner hook
  const { banner: lifecycleBanner, dismiss: dismissBanner } = useLifecycleBanners({
    user: session?.user || null,
    tier: session?.user?.user_metadata?.tier || 'free',
    downloadCount: sessionDownloadCount,
    quotaUsed: 0, // TODO: wire from quota state if available
    hasApiKeys: false,
    paywallTrigger,
  });

  // Phase 11: request browser notification permission on first use
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  // Phase 11: fire browser notification when download finishes while tab is blurred
  useEffect(() => {
    if (!isLoading && videoInfo) {
      if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
        const title = videoInfo.title || 'VidGrab';
        new Notification('Tải xong!', {
          body: title.length > 60 ? title.slice(0, 57) + '…' : title,
          icon: '/icon-192.png',
        });
      }
    }
  }, [isLoading, videoInfo]);

  // Phase 19: handle incoming share target or pending URL from ShareTargetHandler
  useEffect(() => {
    try {
      const pending = sessionStorage.getItem('vg_pending_share_url');
      if (pending) {
        sessionStorage.removeItem('vg_pending_share_url');
        setUrl(pending);
      }
    } catch {}
    const onShareURL = (e) => {
      setUrl(e.detail.url);
    };
    window.addEventListener('vidgrab:share-url', onShareURL);

    // Mobile quick-tools deep-open: vidgrab:open-tool:{tabId}
    const TOOL_TAB_MAP = { trim: 'trim', audio: 'audio', gif: 'gif', subtitle: 'subtitle', cloud: 'package' };
    const onOpenTool = (e) => {
      const tabId = TOOL_TAB_MAP[e.type.split(':')[2]] || 'trim';
      setProcessingHubTab(tabId);
      setShowProcessingHub(true);
    };
    Object.keys(TOOL_TAB_MAP).forEach((t) => window.addEventListener(`vidgrab:open-tool:${t}`, onOpenTool));

    return () => {
      window.removeEventListener('vidgrab:share-url', onShareURL);
      Object.keys(TOOL_TAB_MAP).forEach((t) => window.removeEventListener(`vidgrab:open-tool:${t}`, onOpenTool));
    };
  }, []);

  const showToast = (msg) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(''), 3000);
  };

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/history?limit=5`)
      .then(res => res.json())
      .then(data => {
        if (data.success && data.jobs) setRecentDownloads(data.jobs);
      }).catch(() => {});
  }, []);

  // Reset track selection when the Spotify album/playlist changes
  useEffect(() => {
    setSelectedTracks(new Set());
  }, [spotifyData]);

  const handleToggleTrack = (key) => {
    setSelectedTracks(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleToggleAll = () => {
    if (!spotifyData?.tracks) return;
    const allKeys = spotifyData.tracks.map(t => t.search_query);
    setSelectedTracks(prev =>
      prev.size === allKeys.length ? new Set() : new Set(allKeys)
    );
  };

  const handleDeleteJob = async (jobId) => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/history/${jobId}`, { method: 'DELETE' });
      if (res.ok) {
        setRecentDownloads(prev => prev.filter(j => j.id !== jobId));
        showToast('Đã xóa thành công!');
      }
    } catch { showToast('Lỗi khi xóa!'); }
  };

  const handleClearAllHistory = async () => {
    if (!window.confirm('Bạn có chắc chắn muốn xóa tất cả lịch sử tải gần đây không?')) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/history/all`, { method: 'DELETE' });
      if (res.ok) {
        setRecentDownloads([]);
        showToast('Đã xóa tất cả lịch sử!');
      }
    } catch { showToast('Lỗi khi xóa!'); }
  };

  const extractUrl = (text) => {
    const match = text.match(/(https?:\/\/[^\s]+)/);
    return match ? match[1].replace(/[）》」】'"]+$/, '') : text.trim();
  };

  const handleInputPaste = (e) => {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text');
    const cleanUrl = extractUrl(pasted);
    setUrl(cleanUrl);
    trackEvent(EVENT.PASTE_URL, { url_length: cleanUrl.length });
    if (cleanUrl !== pasted.trim()) showToast('Đã tự động trích xuất URL!');
  };

  const isSpotifyPlaylistOrAlbum = (u) =>
    u.includes('open.spotify.com/playlist') || u.includes('open.spotify.com/album');

  const isSpotifyTrack = (u) => u.includes('open.spotify.com/track');

  const isSpotifyArtist = (u) =>
    u.includes('open.spotify.com/artist') || u.includes('spotify:artist:');

  const handleFetchLink = async (overrideUrl) => {
    // overrideUrl lets callers (e.g. loading a profile post) fetch a URL that
    // isn't yet committed to state. Ignore event objects from onClick/onKeyDown.
    const effUrl = (typeof overrideUrl === 'string' && overrideUrl) ? overrideUrl : url;
    if (typeof overrideUrl === 'string' && overrideUrl && overrideUrl !== url) setUrl(overrideUrl);
    if (!effUrl.trim()) { setError('Vui lòng nhập liên kết hợp lệ.'); return; }
    setIsLoading(true); setError(''); setDelayedInfo(null); setVideoInfo(null); setSpotifyData(null); setArtistData(null); setThreadsData(null); setFormatTab('video');
    setShowInpaint(false); setInpaintStep('region'); setInpaintTempId(null); setInpaintFrameUrl(null);
    setInpaintVideoDims(null); setInpaintPreset(null); setInpaintPixelRegion(null);
    setInpaintPreviewResult(null); setInpaintFinalUrl(null); setInpaintError('');
    setDownloadProgress(null);
    setShowMerge(false); setMergeJobIds(''); setMergeResult(null); setMergeError('');
    setShowWatermark(false); setWmText(''); setWmImageB64(null); setWmPreviewUrl(null); setWmResult(null); setWmError('');

    // Generate a unique token for this request so we can poll progress
    const progressToken = Math.random().toString(36).slice(2, 14) + Date.now().toString(36);
    setLoadElapsed(0);
    loadTimerRef.current = setInterval(() => setLoadElapsed(s => s + 1), 1000);

    // Phase 11: adaptive progress polling — 5s while pending, 1.5s while active, stop on done
    if (progressPollRef.current) clearTimeout(progressPollRef.current);
    const _scheduleProgressPoll = (token, delay) => {
      progressPollRef.current = setTimeout(async () => {
        try {
          const res = await fetch(`${API_BASE}/api/v1/progress/${token}`);
          if (res.ok) {
            const prog = await res.json();
            if (prog.status && prog.status !== 'pending') setDownloadProgress(prog);
            // Stop polling when terminal state reached
            if (prog.status === 'done' || prog.status === 'success' ||
                prog.status === 'failed' || prog.status === 'error') {
              progressPollRef.current = null;
              return;
            }
            // Adaptive: wait longer while still pending (nothing in Redis yet)
            _scheduleProgressPoll(token, prog.status === 'pending' ? 5000 : 1500);
          } else {
            _scheduleProgressPoll(token, 3000);
          }
        } catch { _scheduleProgressPoll(token, 3000); }
      }, delay);
    };
    _scheduleProgressPoll(progressToken, 1500);

    // Route Threads (public post / profile) to its dedicated extractor
    if (isThreadsUrl(effUrl.trim())) {
      try {
        const response = await fetch(`${API_BASE}/api/v1/fetch-threads`,
          withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: effUrl.trim() }) })
        );
        const data = await safeJson(response);
        if (!response.ok) throw new Error(data.detail || 'Không thể xử lý link Threads.');
        setThreadsData(data);
        if (data.success) {
          if (data.source_type === 'profile') showToast(`Tìm thấy ${data.posts?.length || 0} bài Threads công khai.`);
          else if (data.downloadable) showToast('Đã tìm thấy media từ bài Threads.');
        }
      } catch (err) {
        setError(err.message || 'Lỗi khi xử lý link Threads.');
      } finally {
        setIsLoading(false);
        if (progressPollRef.current) { clearTimeout(progressPollRef.current); progressPollRef.current = null; }
        if (loadTimerRef.current) { clearInterval(loadTimerRef.current); loadTimerRef.current = null; }
        setLoadElapsed(0);
      }
      return;
    }

    // Route Spotify ARTIST to the artist overview endpoint
    if (isSpotifyArtist(effUrl.trim())) {
      try {
        const response = await fetch(`${API_BASE}/api/v1/fetch-spotify`,
          withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: effUrl.trim() }) })
        );
        const data = await safeJson(response);
        if (!response.ok) {
          const d = data.detail;
          throw new Error((d && (d.message || d)) || 'Không thể tải thông tin nghệ sĩ.');
        }
        if (data.success) {
          setArtistData(data);
          setSpotifyData(null);
          setArtistTab('top');
          setTrackDownloads({});
          setSelectedTracks(new Set());
          const n = data.summary?.top_tracks_count || 0;
          showToast(data.partial
            ? `Đã lấy ${n} top tracks. Albums/Singles cần cấu hình Spotify API.`
            : `Nghệ sĩ ${data.artist?.name}: ${n} top tracks, ${data.summary?.albums_count || 0} album.`);
        } else throw new Error('Không thể tải thông tin nghệ sĩ.');
      } catch (err) {
        setError(err.message || 'Lỗi khi tải nghệ sĩ Spotify.');
      } finally {
        setIsLoading(false);
        if (progressPollRef.current) { clearTimeout(progressPollRef.current); progressPollRef.current = null; }
        if (loadTimerRef.current) { clearInterval(loadTimerRef.current); loadTimerRef.current = null; }
        setLoadElapsed(0);
      }
      return;
    }

    // Route Spotify playlist / album to dedicated endpoint
    if (isSpotifyPlaylistOrAlbum(effUrl.trim())) {
      try {
        const response = await fetch(`${API_BASE}/api/v1/fetch-spotify`,
          withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: effUrl.trim() }) })
        );
        const data = await safeJson(response);
        if (!response.ok) throw new Error(data.detail || 'Không thể tải danh sách nhạc Spotify');
        if (data.success) {
          setSpotifyData(data);
          setTrackDownloads({});
          showToast(`Tìm thấy ${data.tracks?.length || 0} bài nhạc!`);
        } else throw new Error('Không thể tải danh sách nhạc.');
      } catch (err) {
        setError(err.message || 'Lỗi khi tải danh sách Spotify.');
      } finally {
        setIsLoading(false);
        if (progressPollRef.current) { clearTimeout(progressPollRef.current); progressPollRef.current = null; }
        if (loadTimerRef.current) { clearInterval(loadTimerRef.current); loadTimerRef.current = null; }
        setLoadElapsed(0);
      }
      return;
    }

    try {
      const _schedPayload = scheduledAt ? { scheduled_at: new Date(scheduledAt).toISOString() } : {};
      const response = await fetch(`${API_BASE}/api/v1/fetch-link`,
        withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: effUrl.trim(), quality: 'video', remove_watermark: removeWatermark, download_subs: subtitleMode !== 'off', subtitle_mode: subtitleMode, subtitle_language: subtitleLang, progress_token: progressToken, user_cookies_b64: encodeUserCookies(userCookieText), ..._schedPayload }) })
      );
      const data = await safeJson(response);
      // Scheduled download: 202 + data.scheduled=true → show success toast, reset picker
      if (response.status === 202 && data?.scheduled === true) {
        const t = scheduledAt ? new Date(scheduledAt).toLocaleString('vi-VN') : '';
        setToastMessage(`Đã lên lịch tải lúc ${t}. Theo dõi ở tab Lịch sử.`);
        setScheduledAt(''); setShowSchedulePicker(false); setUrl('');
        return;
      }
      // Phase 27D: detect delayed acceptance (HTTP 202) from Phase 27C admission control.
      // data.delayed=true means the job is queued, NOT failed — show amber notice, not red error.
      if (response.status === 202 || data?.delayed === true) {
        setDelayedInfo(data);
        return;
      }
      if (!response.ok) {
        if (response.status === 429) {
          // Check if it's a fairness cap rejection (has retryAfter) — show as delayed, not error
          if (data?.error === 'fairness_cap_reached' && data?.retryAfter) {
            setDelayedInfo({ ...data, delayed: true, message: data.message || `Thử lại sau ${data.retryAfter}s` });
            return;
          }
          throw new Error('⏳ Bạn đã gửi quá nhiều yêu cầu. Vui lòng chờ 1 phút rồi thử lại.');
        }
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setUpgradeErrorCode('quota_exceeded_daily');
          setShowUpgradeModal(true);
          throw new Error('Đã đạt giới hạn tải. Vui lòng nâng cấp Pro.');
        }
        // Structured tier errors: 402 or known error_code → UpgradeModal
        {
          const _det = data?.detail;
          const _code = _det && typeof _det === 'object'
            ? (_det.error_code || _det.error) : null;
          const TIER_CODES = ['youtube_pro_only','quota_exceeded_daily','tier_limit_quality',
            'tier_limit_batch','tier_required_feature','spotify_artist_full','bulk_zip_pro'];
          if (response.status === 402 || TIER_CODES.includes(_code)) {
            const errorCode = _code || 'tier_required_feature';
            setUpgradeErrorCode(errorCode);
            setShowUpgradeModal(true);
            trackEvent(EVENT.PAYWALL_SEEN, { trigger: errorCode });
            setPaywallTrigger(errorCode);
            return;
          }
          throw new Error(
            (_det && typeof _det === 'object' ? _det.message : _det)
            || 'Không thể lấy thông tin video'
          );
        }
      }
      if (data.success) {
        setVideoInfo(data);
        trackEvent(EVENT.FETCH_SUCCESS, { platform: data.platform || 'unknown', has_subtitles: !!data.has_subtitles });
        showToast('Trích xuất thành công!');
        if (subtitleMode !== 'off') {
          if (data.subtitle_file_url) {
            const a = document.createElement('a');
            a.href = `${API_BASE}${data.subtitle_file_url}`;
            a.setAttribute('download', '');
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            showToast('Phụ đề đã được tải kèm (.srt).');
          } else if (data.subtitle_url) {
            const a = document.createElement('a');
            a.href = data.subtitle_url; a.setAttribute('download', '');
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            showToast('Đang tải phụ đề...');
          } else if (data.subtitle_error === 'no_subtitles_available') {
            showToast('Nền tảng này không có phụ đề công khai.');
          } else if (data.subtitle_error === 'subtitle_extraction_failed') {
            showToast('Không trích xuất được phụ đề. Thử lại sau.');
          } else if (data.subtitle_error === 'subtitle_embed_failed') {
            showToast?.('⚠️ Không thể đốt/mux phụ đề vào video — file phụ đề .srt đã được tải riêng.', 'warning');
          }
        }
      } else throw new Error('Không thể trích xuất thông tin video.');
    } catch (err) {
      setError(err.message || 'Đã xảy ra lỗi khi xử lý link.');
    } finally {
      setIsLoading(false);
      if (progressPollRef.current) { clearTimeout(progressPollRef.current); progressPollRef.current = null; }
      if (loadTimerRef.current) { clearInterval(loadTimerRef.current); loadTimerRef.current = null; }
      setLoadElapsed(0);
      setDownloadProgress(null);
    }
  };

  // Download a single Spotify track by search query
  const handleSpotifyTrackDownload = async (track) => {
    const key = track.search_query;
    setTrackDownloads(prev => ({ ...prev, [key]: 'loading' }));
    try {
      const response = await fetch(`${API_BASE}/api/v1/fetch-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: key, quality: 'mp3_128' }),
      });
      const data = await safeJson(response);
      if (!response.ok) throw new Error(data.detail || 'Lỗi tải nhạc');
      if (data.success) {
        const localPath = data.local_mp3_path || data.local_file_path;
        const title = track.title || data.title || 'audio';
        if (localPath) {
          const ext = localPath.split('.').pop() || 'mp3';
          const a = document.createElement('a');
          a.href = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=${encodeURIComponent(title)}.${ext}`;
          a.setAttribute('download', ''); document.body.appendChild(a); a.click(); document.body.removeChild(a);
        } else if (data.direct_mp4_url) {
          const a = document.createElement('a');
          a.href = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(data.direct_mp4_url)}&filename=${encodeURIComponent(title)}&ext=mp3`;
          a.setAttribute('download', ''); document.body.appendChild(a); a.click(); document.body.removeChild(a);
        }
        setTrackDownloads(prev => ({ ...prev, [key]: 'done' }));
        showToast(`Đang tải: ${track.name || title}`);
      }
    } catch (err) {
      setTrackDownloads(prev => ({ ...prev, [key]: 'error' }));
      showToast(`Lỗi: ${err.message}`);
    }
  };

  // Core ZIP downloader — works on ANY track list (playlist/album/artist).
  const zipDownloadTracks = async (tracksToDownload, zipName) => {
    if (!tracksToDownload || tracksToDownload.length === 0) return;

    setIsZipping(true);
    setZipProgress(0);
    cancelZipRef.current = false;
    const zip = new JSZip();
    let done = 0;
    let failed = 0;

    const downloadTrack = async (track, idx) => {
      if (cancelZipRef.current) return;
      const key = track.search_query;
      setTrackDownloads(prev => ({ ...prev, [key]: 'loading' }));
      try {
        const res = await fetch(`${API_BASE}/api/v1/fetch-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: key, quality: 'mp3_320' }),
        });
        const data = await safeJson(res);
        if (!res.ok || !data.success) throw new Error(data.detail || 'Fetch thất bại');

        const localPath = data.local_mp3_path || data.local_file_path;
        let downloadUrl;
        if (localPath) {
          downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=temp.mp3`;
        } else if (data.direct_mp4_url) {
          downloadUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(data.direct_mp4_url)}&filename=temp&ext=mp3`;
        } else {
          throw new Error('Không có URL tải');
        }

        const fileResp = await fetch(downloadUrl);
        if (!fileResp.ok) throw new Error('Tải file thất bại');
        const blob = await fileResp.blob();

        const num = String(idx + 1).padStart(2, '0');
        const safeName = `${num}. ${track.name} - ${track.artist_str}`.replace(/[/\\?%*:|"<>]/g, '-');
        zip.file(`${safeName}.mp3`, blob);
        setTrackDownloads(prev => ({ ...prev, [key]: 'done' }));
        done++;
      } catch {
        setTrackDownloads(prev => ({ ...prev, [key]: 'error' }));
        failed++;
      }
      setZipProgress(Math.round(((done + failed) / tracksToDownload.length) * 100));
    };

    try {
      showToast(`Đang tải ${tracksToDownload.length} bài nhạc...`);

      // Process 2 tracks concurrently
      for (let i = 0; i < tracksToDownload.length; i += 2) {
        if (cancelZipRef.current) { showToast('Đã hủy quá trình tải ZIP.'); break; }
        const batch = tracksToDownload.slice(i, i + 2);
        await Promise.allSettled(batch.map((t, j) => downloadTrack(t, i + j)));
      }

      if (!cancelZipRef.current) {
        if (done === 0) {
          showToast('Không có bài nào tải được. Vui lòng thử lại.');
          return;
        }
        setZipProgress(100);
        showToast('Đang nén ZIP, vui lòng chờ...');
        const content = await zip.generateAsync({ type: 'blob' });
        const safeName = (zipName || 'VidGrab').replace(/[/\\?%*:|"<>]/g, '-');
        saveAs(content, `${safeName}.zip`);
        showToast(failed > 0
          ? `Xong! ${done}/${tracksToDownload.length} bài (${failed} bài lỗi).`
          : `Tải ZIP thành công! ${done} bài nhạc.`
        );
      }
    } catch (err) {
      showToast(`Lỗi tạo ZIP: ${err.message}`);
    } finally {
      setIsZipping(false);
      setZipProgress(0);
      cancelZipRef.current = false;
    }
  };

  // Playlist / album ZIP (selected or all)
  const handleDownloadAllZip = async () => {
    if (!spotifyData?.tracks) return;
    const allTracks = spotifyData.tracks;
    const tracksToDownload = selectedTracks.size > 0
      ? allTracks.filter(t => selectedTracks.has(t.search_query))
      : allTracks;
    await zipDownloadTracks(tracksToDownload, spotifyData.playlist_name || spotifyData.album_name || 'Playlist');
  };

  // ── Artist download actions ──────────────────────────────────────
  const artistSpotifyUrl = () =>
    artistData?.artist?.id ? `https://open.spotify.com/artist/${artistData.artist.id}` : '';

  // Resolve a mode (top_tracks | albums | singles | all_tracks) → ZIP.
  const handleArtistDownload = async (mode) => {
    if (!artistData) return;

    // Top tracks are already in hand — honor multi-select if any. No API needed.
    if (mode === 'top_tracks') {
      const tt = artistData.top_tracks || [];
      const chosen = selectedTracks.size > 0 ? tt.filter(t => selectedTracks.has(t.search_query)) : tt;
      if (chosen.length === 0) { showToast('Chưa có bài nào để tải.'); return; }
      await zipDownloadTracks(chosen, `${artistData.artist?.name || 'Artist'} - Top tracks`);
      return;
    }

    // albums / singles / all_tracks → expand on the backend (real deduped list)
    try {
      showToast('Đang gom & loại trùng danh sách bài hát...');

      // Backend caps large catalogs (P3): it returns {needs_confirmation, real_count}
      // BEFORE expanding instead of queueing thousands of tracks. Ask the user,
      // then re-request with confirmed=true to expand up to the hard cap.
      const collect = async (confirmed) => {
        const r = await fetch(`${API_BASE}/api/v1/fetch-spotify-artist-tracks`,
          withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: artistSpotifyUrl(), mode, confirmed }) })
        );
        const d = await safeJson(r);
        if (!r.ok || !d.success) {
          const det = d.detail;
          throw new Error((det && (det.message || det)) || 'Không lấy được danh sách bài hát.');
        }
        return d;
      };

      let data = await collect(false);
      if (data.needs_confirmation) {
        if (!window.confirm(`${data.message || `Nghệ sĩ có ${data.real_count} bài.`}\n\nTải tối đa ${data.cap} bài. Tiếp tục?`)) return;
        data = await collect(true);
        if (data.needs_confirmation) return; // defensive: shouldn't recur
        data._userConfirmed = true;          // skip the secondary count confirm
      }

      const count = data.count || data.tracks?.length || 0;
      const sm = data.summary || {};
      if (sm.capped) {
        showToast(`Đã giới hạn còn ${count} bài (catalog lớn). Tải phần này trước.`);
      }
      // Confirm large jobs with the REAL deduped count — but skip if the user
      // already confirmed the catalog-cap prompt above (avoid double-confirm).
      if (!data._userConfirmed && count > 20 && !window.confirm(
        `Nghệ sĩ này có ${count} bài sau khi loại trùng${sm.requested_count ? ` (từ ${sm.requested_count} bản)` : ''}. `
        + `Quá trình có thể mất vài phút. Tiếp tục?`
      )) return;
      // Partial-expansion notice (e.g. expanded 8/10 albums)
      if (sm.albums_requested && sm.albums_expanded < sm.albums_requested) {
        showToast(`Lưu ý: lấy được ${sm.albums_expanded}/${sm.albums_requested} album. Vẫn tải phần thành công.`);
      }
      const label = mode === 'albums' ? 'Albums' : mode === 'singles' ? 'Singles' : 'Tất cả';
      showToast(`Đã gom ${count} bài, bắt đầu tải...`);
      await zipDownloadTracks(data.tracks, `${artistData.artist?.name || 'Artist'} - ${label}`);
    } catch (e) {
      showToast(`Lỗi: ${e.message}`);
    }
  };

  // Download one album/single (reuses the existing album flow).
  const handleArtistAlbumDownload = async (album) => {
    if (!album?.external_url) return;
    try {
      showToast(`Đang lấy bài hát của "${album.title}"...`);
      const res = await fetch(`${API_BASE}/api/v1/fetch-spotify`,
        withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: album.external_url }) })
      );
      const data = await safeJson(res);
      if (!res.ok || !data.success || !data.tracks?.length) throw new Error('Không lấy được bài hát của album này.');
      await zipDownloadTracks(data.tracks, `${artistData?.artist?.name || 'Artist'} - ${album.title}`);
    } catch (e) {
      showToast(`Lỗi: ${e.message}`);
    }
  };

  // Play / stop a 30s Spotify preview clip (preview_url, no download).
  const toggleArtistPreview = (track) => {
    const k = track.spotify_track_id || track.search_query;
    if (artistPreviewRef.current) { artistPreviewRef.current.pause(); artistPreviewRef.current = null; }
    if (artistPreviewKey === k) { setArtistPreviewKey(null); return; }
    if (!track.preview_url) { showToast('Bài này không có đoạn nghe thử.'); return; }
    try {
      const audio = new Audio(track.preview_url);
      audio.onended = () => setArtistPreviewKey(null);
      audio.play().catch(() => {});
      artistPreviewRef.current = audio;
      setArtistPreviewKey(k);
    } catch { /* ignore */ }
  };

  // Expand an album to preview its tracklist (reuses the album endpoint).
  const toggleAlbumExpand = async (album) => {
    const id = album.album_id;
    if (expandedAlbum === id) { setExpandedAlbum(null); return; }
    setExpandedAlbum(id);
    if (albumTracksCache[id]) return; // already loaded
    setAlbumTracksCache(prev => ({ ...prev, [id]: { state: 'loading' } }));
    try {
      const res = await fetch(`${API_BASE}/api/v1/fetch-spotify`,
        withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: album.external_url }) }));
      const data = await safeJson(res);
      if (!res.ok || !data.success || !data.tracks?.length) throw new Error('no tracks');
      setAlbumTracksCache(prev => ({ ...prev, [id]: { state: 'ok', tracks: data.tracks } }));
    } catch {
      setAlbumTracksCache(prev => ({ ...prev, [id]: { state: 'error' } }));
    }
  };

  const handleCancelZip = () => {
    cancelZipRef.current = true;
  };

  // Phase 21: populate SuccessCard after any successful download
  const showSuccessAfterDownload = (format, quality, fileUrl) => {
    setSuccessCardInfo({
      title: videoInfo?.title || null,
      platform: detectedPlatform,
      format: format || 'mp4',
      quality: quality || null,
      file_url: fileUrl || null,
      file_size: null,
      duration: videoInfo?.duration || null,
      thumbnail_url: videoInfo?.thumbnail || null,
      source_url: videoInfo?.original_url || url || null,
      job_id: null,
    });
    setShowSuccessCard(true);
    recordSmartUsed(detectedPlatform, { quality: quality || 'video', remove_watermark: removeWatermark });
  };

  // Phase 21: handle QuickActionBar tap
  const handleQuickAction = (cfg) => {
    if (!videoInfo) return;
    if (cfg._trigger_gif) { setShowGifPanel(true); return; }
    const { quality, remove_watermark } = cfg;
    if (remove_watermark !== undefined) setRemoveWatermark(remove_watermark);
    if (!quality || quality === 'video' || quality === 'best') {
      handleDefaultDownload();
    } else if (quality === 'thumbnail_only') {
      if (videoInfo.thumbnail) {
        const thumbUrl = `${API_BASE}/api/v1/download-thumbnail?url=${encodeURIComponent(videoInfo.thumbnail)}&filename=${encodeURIComponent(videoInfo.title || 'thumbnail')}`;
        const a = document.createElement('a'); a.href = thumbUrl; a.setAttribute('download', '');
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        showToast('Đang tải thumbnail...');
      }
    } else if (quality.startsWith('mp3')) {
      handleMergeDownload(quality === 'mp3_320' ? 'mp3' : 'ogg');
    } else {
      const h = parseInt(quality);
      handleMergeDownload(h || null);
    }
    recordSmartUsed(detectedPlatform, { quality: quality || 'video', remove_watermark });
  };

  // Direct download a format via proxy
  const handleFormatDownload = (fmt) => {
    const title = videoInfo?.title || 'video';
    const ext = fmt.ext || 'mp4';
    const downloadUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(fmt.url)}&filename=${encodeURIComponent(title)}&ext=${encodeURIComponent(ext)}`;
    const a = document.createElement('a');
    a.href = downloadUrl; a.setAttribute('download', '');
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    showToast('Bắt đầu tải về!');
    showSuccessAfterDownload(ext, fmt.label || ext, downloadUrl);
  };

  // ── Threads media download (reuses proxy-download / download-thumbnail) ──
  const handleThreadsMediaDownload = (item, idx = 0) => {
    const base = threadsData?.author_handle ? `threads-${threadsData.author_handle}` : 'threads';
    const name = `${base}-${idx + 1}`;
    let downloadUrl;
    if (item.type === 'image') {
      downloadUrl = `${API_BASE}/api/v1/download-thumbnail?url=${encodeURIComponent(item.url)}&filename=${encodeURIComponent(name)}`;
    } else {
      downloadUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(item.url)}&filename=${encodeURIComponent(name)}&ext=${encodeURIComponent(item.ext || 'mp4')}`;
    }
    const a = document.createElement('a');
    a.href = downloadUrl; a.setAttribute('download', '');
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    showToast('Bắt đầu tải về!');
  };

  // Load a single post from a profile listing into the post preview
  const handleLoadThreadsPost = (postUrl) => {
    handleFetchLink(postUrl);
  };

  // Download "best" (the default direct_mp4_url)
  const handleDefaultDownload = () => {
    if (!videoInfo) {
      setError("Không có thông tin video.");
      return;
    }
    
    // Prioritize local file path if backend downloaded it automatically
    const localPath = videoInfo.local_file_path || videoInfo.local_mp3_path;
    if (localPath) {
      const fileExt = localPath.split('.').pop() || 'mp4';
      const downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=${encodeURIComponent(videoInfo.title || 'video')}.${fileExt}`;
      const a = document.createElement('a');
      a.href = downloadUrl; a.setAttribute('download', '');
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      showToast('Bắt đầu tải về!');
      showSuccessAfterDownload(fileExt, fileExt, downloadUrl);
      return;
    }

    if (!videoInfo.direct_mp4_url) {
      setError("Không tìm thấy link tải video gốc.");
      return;
    }
    handleFormatDownload({ url: videoInfo.direct_mp4_url, ext: 'mp4' });
  };

  // Merge via backend re-fetch (4K, specific resolution, or audio extraction)
  // OPTIMIZATION: If the initial fetch already downloaded at the requested quality (or better),
  // serve the already-downloaded local file instead of making another request.
  const handleMergeDownload = async (param = null) => {
    let qualityReq = 'video_4k';
    const isAudioParam = param === 'm4a' || param === 'webm' || param === 'mp3' || param === 'ogg';
    if (isAudioParam) {
      qualityReq = param === 'mp3' ? 'mp3_320' : param === 'ogg' ? 'mp3_128' : `audio_${param}`;
    } else if (param) {
      qualityReq = `video_${param}`;
    }

    // Check if the initial download already has this quality available locally
    const requestedHeight = isAudioParam ? 0 : (typeof param === 'number' ? param : 0);
    const downloadedHeight = videoInfo?.downloaded_height || 0;
    const localPath = videoInfo?.local_file_path;

    if (!isAudioParam && localPath && requestedHeight > 0 && downloadedHeight >= requestedHeight) {
      // The initial fetch already downloaded at this quality or better — serve directly
      const fileExt = localPath.split('.').pop() || 'mp4';
      const downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=${encodeURIComponent(videoInfo.title || 'video')}.${fileExt}`;
      const a = document.createElement('a');
      a.href = downloadUrl; a.setAttribute('download', '');
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      showToast(`Bắt đầu tải ${downloadedHeight}p!`);
      return;
    }

    trackEvent(EVENT.DOWNLOAD_STARTED, { quality: qualityReq });
    setDownloadingId(`merge_${param || '4k'}`);
    try {
      // Phase 8: YouTube quality tiers are gated server-side. 1080p returns a
      // 409 needs_confirmation (costlier proxy bytes) → ask, then resend
      // confirmed=true. 4K / over-cap return a 400/503 with a friendly message
      // + suggested lower tier. `detail` is now an object, not a string.
      const send = (confirmed) => fetch(`${API_BASE}/api/v1/fetch-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: videoInfo.original_url || url.trim(),
          quality: qualityReq,
          remove_watermark: !isAudioParam && removeWatermark,
          confirmed,
        }),
      });
      let response = await send(false);
      let data = await safeJson(response);
      if (!response.ok) {
        const det = data.detail;
        const code = det && typeof det === 'object' ? det.error : null;
        const dmsg = det && typeof det === 'object' ? det.message : det;
        if (response.status === 429) {
          throw new Error('⏳ Quá nhiều yêu cầu. Vui lòng chờ 1 phút rồi thử lại.');
        }
        if (response.status === 403 && det === 'QUOTA_EXCEEDED') {
          setUpgradeErrorCode('quota_exceeded_daily');
          setShowUpgradeModal(true); return;
        }
        // Tier limit errors (402) → UpgradeModal
        {
          const TIER_CODES = ['youtube_pro_only','quota_exceeded_daily','tier_limit_quality',
            'tier_limit_batch','tier_required_feature','spotify_artist_full','bulk_zip_pro'];
          if (response.status === 402 || TIER_CODES.includes(code)) {
            const errorCode = code || 'tier_required_feature';
            setUpgradeErrorCode(errorCode);
            setShowUpgradeModal(true);
            trackEvent(EVENT.PAYWALL_SEEN, { trigger: errorCode });
            setPaywallTrigger(errorCode);
            return;
          }
        }
        // YouTube 1080p needs confirmation → ask + retry once with confirmed.
        if (code === 'youtube_confirm_required') {
          if (!window.confirm(`${dmsg}`)) return;
          response = await send(true);
          data = await safeJson(response);
          if (!response.ok) {
            const d2 = data.detail;
            throw new Error((d2 && (d2.message || d2)) || 'Không thể tải chất lượng này.');
          }
        } else if (code) {
          // Blocked by phase-8 gate / circuit / cost-limit — show message + suggest
          const sug = det.suggested ? ` Gợi ý: chọn ${det.suggested.replace('video_', '')}p.` : '';
          throw new Error(`${dmsg}${sug}`);
        } else {
          throw new Error(dmsg || 'Không thể xử lý ghép tệp video');
        }
      }
      if (data.success) {
        trackEvent(EVENT.DOWNLOAD_SUCCESS, { platform: data.platform || videoInfo?.platform || 'unknown' });
        setSessionDownloadCount(c => c + 1);
        try {
          const cnt = parseInt(localStorage.getItem('vg_download_count') || '0', 10);
          localStorage.setItem('vg_download_count', String(cnt + 1));
        } catch {}
        addNotification({
          type: 'download_done',
          title: 'Tải xong!',
          body: videoInfo?.title || 'File đã sẵn sàng',
          url: '/history',
        });
        // Determine file path and extension from the actual file
        const dlPath = data.local_mp3_path || data.local_file_path;
        if (dlPath) {
          const fileExt = dlPath.split('.').pop() || (isAudioParam ? 'mp3' : 'mp4');
          const downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlPath)}&filename=${encodeURIComponent(data.title || 'video')}.${fileExt}`;
          const a = document.createElement('a');
          a.href = downloadUrl; a.setAttribute('download', '');
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          showToast('Bắt đầu tải về!');
          if (data.local_file_path) {
            setLastDownloadInfo({ file_path: data.local_file_path });
          }
        } else if (data.direct_mp4_url) {
          handleFormatDownload({ url: data.direct_mp4_url, ext: isAudioParam ? 'mp3' : 'mp4' });
        }
      }
    } catch (err) {
      setError(err.message || 'Đã xảy ra lỗi khi xử lý ghép tệp.');
      addNotification({
        type: 'download_failed',
        title: 'Tải thất bại',
        body: typeof err.message === 'string' ? err.message : 'Vui lòng thử lại',
        url: null,
      });
    } finally { setDownloadingId(null); }
  };

  // Audio via backend re-fetch (fallback when direct audio formats aren't present)
  const handleAudioDownload = async () => {
    setDownloadingId('audio_mp3');
    try {
      const response = await fetch(`${API_BASE}/api/v1/fetch-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: videoInfo.original_url || url.trim(),
          quality: 'mp3_320',
          remove_watermark: false,
        }),
      });
      const data = await safeJson(response);
      if (!response.ok) {
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setUpgradeErrorCode('quota_exceeded_daily');
          setShowUpgradeModal(true); return;
        }
        throw new Error(data.detail || 'Không thể lấy âm thanh');
      }
      if (data.success) {
        if (data.local_mp3_path || data.local_file_path) {
          const path = data.local_mp3_path || data.local_file_path;
          const downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(path)}&filename=${encodeURIComponent(data.title || 'audio')}.mp3`;
          const a = document.createElement('a');
          a.href = downloadUrl; a.setAttribute('download', '');
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
        } else if (data.direct_mp4_url) {
          handleFormatDownload({ url: data.direct_mp4_url, ext: 'mp3' });
        }
      }
    } catch (err) {
      setError(err.message || 'Đã xảy ra lỗi khi tải âm thanh.');
    } finally { setDownloadingId(null); }
  };

  // ── Thumbnail Download ───────────────────────────────────────
  const handleThumbnailDownload = () => {
    if (!videoInfo?.thumbnail_url) return;
    const title = videoInfo?.title || 'thumbnail';
    const downloadUrl = `${API_BASE}/api/v1/download-thumbnail?url=${encodeURIComponent(videoInfo.thumbnail_url)}&filename=${encodeURIComponent(title)}`;
    const a = document.createElement('a');
    a.href = downloadUrl; a.setAttribute('download', '');
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    showToast('Đang tải ảnh bìa...');
  };

  // ── Preview Player ───────────────────────────────────────────
  const togglePreview = () => {
    setShowPreview(prev => !prev);
    setIsPlaying(false);
  };

  const getPreviewUrl = () => {
    if (!videoInfo) return null;
    const localPath = videoInfo.local_file_path || videoInfo.local_mp3_path;
    if (localPath) {
      return `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=preview`;
    }
    if (videoInfo.direct_mp4_url) {
      return `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(videoInfo.direct_mp4_url)}&filename=preview&ext=mp4`;
    }
    return null;
  };

  const handlePlayPause = () => {
    if (!previewRef.current) return;
    if (previewRef.current.paused) {
      previewRef.current.play();
      setIsPlaying(true);
    } else {
      previewRef.current.pause();
      setIsPlaying(false);
    }
  };

  // ── Media Trimmer ────────────────────────────────────────────
  const handleOpenTrimmer = () => {
    const dur = videoInfo?.duration || 0;
    setTrimStart(0);
    setTrimEnd(Math.min(dur, 30));
    setShowTrimmer(true);
  };

  const handleTrimDownload = async () => {
    if (!videoInfo) return;
    setIsTrimming(true);
    try {
      // Prefer local file (YouTube merge, TikTok, etc.) served via download-local endpoint
      const localPath = videoInfo.local_file_path || videoInfo.local_mp3_path;
      const sourceUrl = localPath
        ? `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=trim_source`
        : videoInfo.direct_mp4_url;
      if (!sourceUrl) { showToast('Không có nguồn để cắt.'); return; }

      const response = await fetch(`${API_BASE}/api/v1/trim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: sourceUrl,
          start_time: trimStart,
          end_time: trimEnd,
          filename: videoInfo.title || 'video',
          is_audio: videoInfo.is_audio_only || false,
        }),
      });
      const data = await safeJson(response);
      if (!response.ok) throw new Error(data.detail || 'Trim failed');
      if (data.success && data.trimmed_file_path) {
        const a = document.createElement('a');
        a.href = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(data.trimmed_file_path)}&filename=${encodeURIComponent(data.filename)}`;
        a.setAttribute('download', '');
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        showToast(`Đã cắt thành công! (${data.file_size_mb} MB)`);
      }
    } catch (err) {
      setError(err.message || 'Lỗi khi cắt video.');
    } finally { setIsTrimming(false); }
  };

  const formatTime = (s) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  };

  // ── GIF Converter ────────────────────────────────────────
  const handleOpenGif = () => {
    const dur = videoInfo?.duration || 30;
    setGifStart(0);
    setGifEnd(Math.min(dur, 10));
    setShowGifPanel(p => !p);
  };

  const handleConvertGif = async () => {
    if (!videoInfo) return;
    const localPath = videoInfo.local_file_path || videoInfo.local_mp3_path;
    const sourceUrl = videoInfo.direct_mp4_url;
    if (!localPath && !sourceUrl) { showToast('Không có nguồn để chuyển GIF.'); return; }
    if (gifEnd - gifStart > 30) { showToast('Giới hạn 30 giây cho GIF.'); return; }

    setIsConverting(true);
    try {
      const body = {
        start_time: gifStart,
        end_time: gifEnd,
        width: gifWidth,
        fps: gifFps,
        filename: videoInfo.title || 'animation',
      };
      // Prefer local_path to avoid SSRF-guard rejection on relative URLs
      if (localPath) body.local_path = localPath;
      else body.url = sourceUrl;

      const res = await fetch(`${API_BASE}/api/v1/to-gif`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'GIF conversion failed');
      if (data.success && data.gif_path) {
        const a = document.createElement('a');
        a.href = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(data.gif_path)}&filename=${encodeURIComponent(data.filename)}`;
        a.setAttribute('download', '');
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        showToast(`GIF tạo thành công! ${data.file_size_mb} MB · ${data.width}px · ${data.fps}fps`);
        setShowGifPanel(false);
      }
    } catch (err) {
      setError(err.message || 'Lỗi khi tạo GIF.');
    } finally { setIsConverting(false); }
  };

  // ── Chapter Download (reuses /trim endpoint) ─────────────
  const handleChapterDownload = async (chapter) => {
    const localPath = videoInfo?.local_file_path;
    const sourceUrl = localPath
      ? `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=chapter_source`
      : videoInfo?.direct_mp4_url;
    if (!sourceUrl) { showToast('Không có nguồn để tải chapter.'); return; }

    setDownloadingChapter(chapter.title);
    try {
      const res = await fetch(`${API_BASE}/api/v1/trim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: sourceUrl,
          start_time: chapter.start_time,
          end_time: chapter.end_time,
          filename: chapter.title,
          is_audio: videoInfo?.is_audio_only || false,
        }),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'Chapter download failed');
      if (data.success && data.trimmed_file_path) {
        const a = document.createElement('a');
        a.href = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(data.trimmed_file_path)}&filename=${encodeURIComponent(data.filename)}`;
        a.setAttribute('download', '');
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        showToast(`Đã tải chapter: ${chapter.title}`);
      }
    } catch (err) {
      showToast(`Lỗi tải chapter: ${err.message}`);
    } finally { setDownloadingChapter(null); }
  };

  // ── Logo Inpaint ─────────────────────────────────────────────
  const INPAINT_PRESETS = [
    { label: 'Dưới phải', key: 'lower-right' },
    { label: 'Dưới trái', key: 'lower-left'  },
    { label: 'Trên phải', key: 'upper-right' },
    { label: 'Trên trái', key: 'upper-left'  },
  ];

  const handleOpenInpaint = async () => {
    const userTier = session?.user?.user_metadata?.tier || 'free';
    const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
    if (!isPro) {
      setUpgradeErrorCode('tier_required_feature');
      setShowUpgradeModal(true);
      return;
    }
    if (showInpaint) { setShowInpaint(false); return; }
    const localPath = videoInfo?.local_file_path;
    if (!localPath) { showToast('Cần tải video về trước để xoá logo.'); return; }
    setShowInpaint(true);
    setInpaintStep('region');
    setInpaintTempId(null);
    setInpaintFrameUrl(null);
    setInpaintVideoDims(null);
    setInpaintPreset(null);
    setInpaintPixelRegion(null);
    setInpaintPreviewResult(null);
    setInpaintFinalUrl(null);
    setInpaintError('');
    setInpaintLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/flow-cleanup/from-local`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ local_path: localPath, title: videoInfo.title || '' }),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'Không khởi tạo được session.');
      setInpaintTempId(data.temp_id);
      setInpaintFrameUrl(`${API_BASE}${data.preview_url}`);
      if (data.video_info?.width && data.video_info?.height) {
        setInpaintVideoDims({ width: data.video_info.width, height: data.video_info.height });
      }
    } catch (err) {
      setInpaintError(err.message);
      setInpaintStep('error');
    } finally { setInpaintLoading(false); }
  };

  const handleInpaintPreview = async (preset) => {
    if (!inpaintTempId || !preset) return;
    setInpaintPreset(preset);
    setInpaintLoading(true);
    setInpaintStep('preview');
    try {
      const res = await fetch(`${API_BASE}/api/v1/flow-cleanup/preview-frame`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ temp_id: inpaintTempId, preset }),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'Xem trước thất bại.');
      setInpaintPreviewResult(data);
      // Store the pixel region returned by backend for process step
      if (data.region) setInpaintPixelRegion(data.region);
    } catch (err) {
      setInpaintError(err.message);
      setInpaintStep('error');
    } finally { setInpaintLoading(false); }
  };

  const handleInpaintProcess = async () => {
    if (!inpaintTempId || !inpaintPreset) return;
    setInpaintStep('processing');
    setInpaintLoading(true);
    try {
      const body = { temp_id: inpaintTempId, method: 'natural', preset: inpaintPreset };
      if (inpaintPixelRegion) { body.preset = 'custom'; body.region = inpaintPixelRegion; }
      const res = await fetch(`${API_BASE}/api/v1/flow-cleanup/process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'Xử lý thất bại.');
      if (data.success && (data.cleaned_path || data.output_path)) {
        const cleanedPath = data.cleaned_path || data.output_path;
        const ext = cleanedPath.split('.').pop() || 'mp4';
        const fname = `${videoInfo?.title || 'cleaned'}_nologin.${ext}`;
        setInpaintFinalUrl(
          `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(cleanedPath)}&filename=${encodeURIComponent(fname)}`
        );
        setInpaintStep('done');
      } else {
        throw new Error(data.detail || 'Xử lý không trả về file kết quả.');
      }
    } catch (err) {
      setInpaintError(err.message);
      setInpaintStep('error');
    } finally { setInpaintLoading(false); }
  };

  const handleInpaintDownload = () => {
    if (!inpaintFinalUrl) return;
    const a = document.createElement('a');
    a.href = inpaintFinalUrl;
    a.setAttribute('download', '');
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    showToast('Đang tải video đã xoá logo...');
  };

  // ── Merge Clips ──────────────────────────────────────────────
  const handleMergeClips = async () => {
    const ids = mergeJobIds.split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
    if (ids.length < 2) { setMergeError('Cần ít nhất 2 job ID để ghép.'); return; }
    if (ids.length > 5) { setMergeError('Tối đa 5 clip cùng lúc.'); return; }
    setMergeLoading(true);
    setMergeError('');
    setMergeResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/merge-clips`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      });
      const data = await res.json();
      if (!res.ok) {
        const code = data?.detail?.error_code || '';
        if (code === 'merge_duration_exceeded') setMergeError('Tổng thời lượng vượt quá 30 phút.');
        else if (code === 'merge_too_many_clips') setMergeError('Tối đa 5 clip.');
        else setMergeError(data?.detail?.message || data?.detail || 'Ghép thất bại.');
        return;
      }
      setMergeResult(data);
    } catch (err) {
      setMergeError(err.message || 'Lỗi kết nối.');
    } finally {
      setMergeLoading(false);
    }
  };

  const handleMergeClipsDownload = () => {
    if (!mergeResult?.merged_path) return;
    const a = document.createElement('a');
    a.href = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(mergeResult.merged_path)}&filename=merged_video.mp4`;
    a.download = 'merged_video.mp4';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  };

  // ── Watermark Embed ──────────────────────────────────────────
  const handleWatermarkRender = async (previewOnly = false) => {
    const sourcePath = videoInfo?.local_file_path;
    if (!sourcePath) { setWmError('Không có video nguồn.'); return; }
    if (wmType === 'text' && !wmText.trim()) { setWmError('Nhập nội dung watermark.'); return; }
    if (wmType === 'image' && !wmImageB64) { setWmError('Chọn ảnh watermark.'); return; }
    setWmLoading(true);
    setWmError('');
    if (!previewOnly) setWmResult(null);
    const body = {
      source_path: sourcePath,
      watermark_type: wmType,
      position: wmPosition,
      opacity: wmOpacity,
      preview_only: previewOnly,
      ...(wmType === 'text' ? { text: wmText, font_size: wmFontSize } : {}),
      ...(wmType === 'image' ? { image_base64: wmImageB64 } : {}),
    };
    try {
      const res = await fetch(`${API_BASE}/api/v1/watermark-embed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        const code = data?.detail?.error_code || '';
        if (code === 'watermark_duration_exceeded') setWmError('Video quá dài (tối đa 10 phút).');
        else if (code === 'watermark_image_too_large') setWmError('Ảnh watermark quá lớn (tối đa 200KB).');
        else setWmError(data?.detail?.message || data?.detail || 'Render thất bại.');
        return;
      }
      if (previewOnly) {
        setWmPreviewUrl(`${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(data.output_path)}&filename=preview.mp4`);
      } else {
        setWmResult(data);
      }
    } catch (err) {
      setWmError(err.message || 'Lỗi kết nối.');
    } finally {
      setWmLoading(false);
    }
  };

  const handleWmImageUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 200 * 1024) { setWmError('Ảnh quá lớn (tối đa 200KB).'); return; }
    const reader = new FileReader();
    reader.onload = (ev) => {
      const b64 = ev.target.result.split(',')[1];
      setWmImageB64(b64);
      setWmError('');
    };
    reader.readAsDataURL(file);
  };

  // Convert pixel region to % for CSS overlay on the preview frame image
  const inpaintRegionPct = inpaintPixelRegion && inpaintVideoDims
    ? {
        left:   `${(inpaintPixelRegion.x / inpaintVideoDims.width  * 100).toFixed(1)}%`,
        top:    `${(inpaintPixelRegion.y / inpaintVideoDims.height * 100).toFixed(1)}%`,
        width:  `${(inpaintPixelRegion.w / inpaintVideoDims.width  * 100).toFixed(1)}%`,
        height: `${(inpaintPixelRegion.h / inpaintVideoDims.height * 100).toFixed(1)}%`,
      }
    : null;

  // ── Cloud Save ───────────────────────────────────────────────
  const getDownloadUrl = () => {
    if (!videoInfo) return null;
    const localPath = videoInfo.local_file_path || videoInfo.local_mp3_path;
    if (localPath) {
      const ext = localPath.split('.').pop() || 'mp4';
      return `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=${encodeURIComponent(videoInfo.title || 'video')}.${ext}`;
    }
    if (videoInfo.direct_mp4_url) {
      return `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(videoInfo.direct_mp4_url)}&filename=${encodeURIComponent(videoInfo.title || 'video')}&ext=mp4`;
    }
    return null;
  };

  const handleSaveToGDrive = () => {
    const fileUrl = getDownloadUrl();
    if (!fileUrl) { showToast('Không có link tải.'); return; }
    const fullUrl = fileUrl.startsWith('http') ? fileUrl : `${window.location.origin}${fileUrl}`;
    // Google Drive không hỗ trợ upload từ URL trực tiếp — tải file về máy trước,
    // sau đó mở Drive để upload thủ công
    const a = document.createElement('a');
    a.href = fullUrl; a.setAttribute('download', '');
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => window.open('https://drive.google.com/drive/my-drive', '_blank'), 800);
    showToast('Tải file xong → kéo vào Google Drive đang mở!');
    setShowCloudMenu(false);
  };

  const handleSaveToDropbox = () => {
    const fileUrl = getDownloadUrl();
    if (!fileUrl) { showToast('Không có link tải.'); return; }
    const fullUrl = fileUrl.startsWith('http') ? fileUrl : `${window.location.origin}${fileUrl}`;
    window.open(`https://www.dropbox.com/save?url=${encodeURIComponent(fullUrl)}&filename=${encodeURIComponent(videoInfo?.title || 'video')}`, '_blank');
    showToast('Đang mở Dropbox...');
    setShowCloudMenu(false);
  };

  // Split formats
  const videoFormats = (videoInfo?.available_formats || []).filter(f => f.type === 'video');
  const audioFormats = (videoInfo?.available_formats || []).filter(f => f.type === 'audio');
  const hasFormats = videoFormats.length > 0 || audioFormats.length > 0;
  const maxMergeHeight = videoInfo?.max_merge_height || 0;
  const maxCombinedHeight = videoFormats.length > 0 ? Math.max(...videoFormats.map(f => f.height)) : 0;
  const showMergeOption = maxMergeHeight > maxCombinedHeight;

  return (
    <div className="w-full flex flex-col items-center">
      <Toast message={toastMessage} show={!!toastMessage} />
      <UpgradeModal
        isOpen={showUpgradeModal}
        onClose={() => { setShowUpgradeModal(false); setUpgradeErrorCode(null); }}
        errorCode={upgradeErrorCode}
        authToken={session?.access_token}
      />

      {/* ── Quick Guide ─────────────────────────────────── */}
      <QuickGuideSection activeToolTab="single" />

      {/* ── Platform health banner (shown only when degraded) ── */}
      <PlatformStatusBanner />

      {/* ── Input Area ──────────────────────────────────── */}
      <div className="relative group w-full max-w-3xl mb-10">
        <div className="absolute -inset-1 bg-gradient-to-r from-[#FDE047]/10 to-[#4ADE80]/10 rounded-[2rem] md:rounded-full blur-md opacity-0 group-hover:opacity-40 transition duration-500" />
        <div className="relative bg-white flex flex-col md:flex-row items-center p-2.5 rounded-3xl md:rounded-full shadow-2xl shadow-[#FDE047]/10 border border-slate-100/80">
          <div className="flex-1 flex items-center gap-3 w-full px-5 md:px-6">
            <Link2 className="w-6 h-6 text-slate-600 flex-shrink-0" />
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onPaste={handleInputPaste}
              onKeyDown={(e) => e.key === 'Enter' && handleFetchLink()}
              onFocus={() => { if (!url) checkClipboard(); }}
              placeholder="🔗 Dán liên kết video hoặc kênh vào đây..."
              disabled={isLoading}
              className="w-full bg-transparent text-slate-900 placeholder-slate-500 text-sm sm:text-base md:text-lg font-semibold focus:outline-none disabled:opacity-50 h-14"
            />
            {navigator.clipboard && (
              <button
                onClick={async () => {
                  try {
                    const text = await navigator.clipboard.readText();
                    if (text) setUrl(text);
                  } catch (err) {
                    console.error("Failed to read clipboard contents: ", err);
                  }
                }}
                className="p-2 text-slate-400 hover:text-orange-500 transition-colors"
                title="Dán từ bộ nhớ tạm"
              >
                <ClipboardPaste className="w-5 h-5" />
              </button>
            )}
          </div>
          <button
            onClick={handleFetchLink}
            disabled={isLoading || !url.trim()}
            className="w-full md:w-auto mt-3 md:mt-0 h-14 md:h-16 px-6 sm:px-10 rounded-2xl md:rounded-full bg-gradient-to-r from-[#FB923C] to-[#FBBF24] hover:from-[#F97316] hover:to-[#F59E0B] text-[#012622] font-black shadow-lg shadow-[#FBBF24]/20 active:scale-95 transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2.5 whitespace-nowrap text-base md:text-lg uppercase tracking-wider drop-shadow-md"
          >
            {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-6 h-6 fill-[#012622]" />}
            <span>{isLoading ? 'ĐANG XỬ LÝ...' : 'BÓC TÁCH NGAY'}</span>
          </button>
        </div>

        {/* ── Schedule download picker ───────────────────────── */}
        <div className="mt-2 px-1 flex flex-col gap-1.5">
          <button
            type="button"
            onClick={() => { setShowSchedulePicker(v => !v); if (showSchedulePicker) setScheduledAt(''); }}
            className={`self-start flex items-center gap-1.5 text-xs font-semibold rounded-full px-3.5 py-1.5 transition-all border ${
              showSchedulePicker || scheduledAt
                ? 'bg-amber-500/20 text-amber-300 border-amber-400/50 shadow-sm shadow-amber-500/10'
                : 'bg-slate-700/50 text-slate-300 border-slate-600/50 hover:bg-slate-600/60 hover:text-white hover:border-slate-500/60'
            }`}
          >
            <CalendarClock className="w-3.5 h-3.5" />
            {scheduledAt
              ? `Lên lịch: ${new Date(scheduledAt).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}`
              : 'Lên lịch tải'}
          </button>
          {showSchedulePicker && (
            <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
              <CalendarClock className="w-4 h-4 text-amber-500 flex-shrink-0" />
              <input
                type="datetime-local"
                value={scheduledAt}
                min={new Date(Date.now() + 60000).toISOString().slice(0, 16)}
                onChange={e => setScheduledAt(e.target.value)}
                className="text-xs bg-transparent text-amber-800 focus:outline-none flex-1"
              />
              {scheduledAt && (
                <button onClick={() => setScheduledAt('')} className="text-amber-400 hover:text-amber-600">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          )}
        </div>
        {/* Phase 24: Capability badge — shows platform/source_type/support_level */}
        {!videoInfo && (capResult || capLoading) && (
          <div className="mt-2 px-1">
            <CapabilityBadge result={capResult} loading={capLoading} />
          </div>
        )}

        {hasSuggestion && !url && clipboardURL && (
          <div className="mt-2 flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/30 rounded-lg px-3 py-2">
            <span className="text-xs text-indigo-300 flex-1 truncate min-w-0">
              Dán: {(() => { try { return new URL(clipboardURL).hostname; } catch { return clipboardURL; } })()}...
            </span>
            <button
              onClick={() => { setUrl(clipboardURL); clearSuggestion(); }}
              className="text-xs bg-indigo-600 text-white px-2.5 py-1 rounded-lg font-semibold flex-shrink-0"
            >
              Dán
            </button>
            <button onClick={clearSuggestion} className="text-white/30 hover:text-white/50 text-xs flex-shrink-0">✕</button>
          </div>
        )}
      </div>

      {/* ── Extracting Phase Panel ──────────────────────── */}
      {isLoading && !downloadProgress && (
        <div className="w-full max-w-3xl mb-4 flex items-center gap-3 px-5 py-3.5 rounded-2xl bg-slate-800/60 border border-slate-700/50 animate-in fade-in slide-in-from-top-2 duration-300">
          <Loader2 className="w-4 h-4 text-[#FBBF24] animate-spin flex-shrink-0" />
          <p className="flex-1 text-sm font-semibold text-slate-200">
            {isSpotifyArtist(url) ? 'Đang tải thông tin nghệ sĩ Spotify...' :
             isSpotifyPlaylistOrAlbum(url) ? 'Đang tải danh sách Spotify...' :
             isSpotifyTrack(url) ? 'Đang tìm và trích xuất bài nhạc...' :
             isWatermarkPlatform(url) && removeWatermark ? 'Đang trích xuất TikTok / Douyin — bỏ watermark khi tải xong...' :
             isWatermarkPlatform(url) ? 'Đang trích xuất TikTok / Douyin...' :
             'Đang phân tích liên kết và trích xuất...'}
          </p>
          {loadElapsed > 0 && (
            <span className="text-xs text-slate-500 font-mono tabular-nums flex-shrink-0">{loadElapsed}s</span>
          )}
        </div>
      )}

      {/* ── Download Progress Bar ────────────────────────── */}
      {isLoading && downloadProgress && downloadProgress.status === 'downloading' && (
        <div className="w-full max-w-3xl mb-4 px-5 py-4 rounded-2xl bg-slate-800/80 border border-slate-700/60 animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="flex items-center justify-between mb-2 text-sm font-semibold">
            <span className="text-slate-200">
              {isWatermarkPlatform(url) ? 'Đang tải bản sạch TikTok / Douyin...' :
               url.includes('spotify') ? 'Đang xử lý nhạc Spotify...' :
               'Đang tải video...'}
            </span>
            <span className="text-[#FBBF24] tabular-nums">{downloadProgress.percent}%</span>
          </div>
          <div className="w-full h-2.5 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-[#FB923C] to-[#FBBF24] rounded-full transition-all duration-500"
              style={{ width: `${downloadProgress.percent}%` }}
            />
          </div>
          <div className="flex items-center justify-between mt-2 text-xs text-slate-400 tabular-nums">
            <span>
              {downloadProgress.speed_kbps >= 1024
                ? `${(downloadProgress.speed_kbps / 1024).toFixed(1)} MB/s`
                : `${downloadProgress.speed_kbps} KB/s`}
            </span>
            <span>
              {downloadProgress.eta_seconds > 0 && `còn ~${downloadProgress.eta_seconds}s`}
            </span>
            <span>
              {downloadProgress.total_bytes > 0 && `${(downloadProgress.total_bytes / 1048576).toFixed(1)} MB`}
            </span>
          </div>
        </div>
      )}

      {/* ── Spotify Track Hint ───────────────────────────── */}
      {isSpotifyTrack(url) && (
        <div className="w-full max-w-3xl mb-4 flex items-center gap-3 px-5 py-3 rounded-2xl bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-sm font-medium animate-in fade-in slide-in-from-top-2 duration-300">
          <Music className="w-4 h-4 flex-shrink-0" />
          <span>
            Nhạc Spotify đơn — sẽ tự động tìm trên YouTube và tải dạng{' '}
            <strong className="text-emerald-300">MP3 128kbps</strong>.
            Nhấn <strong className="text-emerald-300">BÓC TÁCH NGAY</strong> để bắt đầu.
          </span>
        </div>
      )}

      {isSpotifyPlaylistOrAlbum(url) && (
        <div className="w-full max-w-3xl mb-4 flex items-center gap-3 px-5 py-3 rounded-2xl bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-sm font-medium animate-in fade-in slide-in-from-top-2 duration-300">
          <Music className="w-4 h-4 flex-shrink-0" />
          <span>
            Playlist / Album Spotify — sẽ hiển thị danh sách bài nhạc để tải từng bài hoặc tải tất cả dạng{' '}
            <strong className="text-emerald-300">ZIP MP3</strong>.
          </span>
        </div>
      )}

      {/* ── TikTok / Douyin watermark hint ─────────────── */}
      {isWatermarkPlatform(url) && (
        <div className="w-full max-w-3xl mb-4 flex items-center gap-3 px-5 py-3 rounded-2xl bg-emerald-500/10 border border-emerald-500/25 text-emerald-300 text-sm font-medium animate-in fade-in slide-in-from-top-2 duration-300">
          <Sparkles className="w-4 h-4 flex-shrink-0 text-emerald-400" />
          <span>
            <strong className="text-emerald-300">TikTok / Douyin</strong> — {removeWatermark ? 'Tùy chọn bỏ watermark đang bật — sẽ tải bản sạch không logo.' : 'Bật tùy chọn bên dưới để tải bản không watermark / logo.'}
          </span>
        </div>
      )}

      <div className="w-full max-w-3xl mb-5 flex flex-col sm:flex-row justify-center items-center gap-4 sm:gap-8 text-sm">
        {/* Watermark toggle — platform-aware */}
        {isWatermarkPlatform(url) ? (
          <label className="flex items-center gap-2.5 cursor-pointer group">
            <input
              type="checkbox"
              checked={removeWatermark}
              onChange={e => setRemoveWatermark(e.target.checked)}
              className="w-4 h-4 accent-emerald-500 rounded"
            />
            <span className="font-semibold text-emerald-300 group-hover:text-emerald-200 transition-colors">
              Xoá watermark / logo
            </span>
            <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-emerald-500/15 text-emerald-400 font-bold border border-emerald-500/25 leading-tight">
              ✓ Hỗ trợ
            </span>
          </label>
        ) : url.trim() ? (
          <label className="flex items-center gap-2.5 cursor-not-allowed opacity-40">
            <input
              type="checkbox"
              checked={false}
              disabled
              className="w-4 h-4 rounded"
            />
            <span className="font-medium text-slate-400 line-through">Xoá watermark / logo</span>
            <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-slate-700/50 text-slate-500 font-medium border border-slate-600/30 leading-tight">
              Chỉ TikTok / Douyin
            </span>
          </label>
        ) : (
          <label className="flex items-center gap-2 cursor-pointer hover:text-white transition-colors text-slate-300">
            <input
              type="checkbox"
              checked={removeWatermark}
              onChange={e => setRemoveWatermark(e.target.checked)}
              className="w-4 h-4 accent-emerald-500 bg-slate-800 border-slate-600 rounded"
            />
            <span className="font-medium">Xoá logo (TikTok / Douyin)</span>
          </label>
        )}

        <div className="mt-2 flex flex-col gap-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-semibold text-slate-400 min-w-[60px]">Phụ đề:</span>
            <select
              value={subtitleMode}
              onChange={e => setSubtitleMode(e.target.value)}
              className="text-[11px] px-2 py-1 rounded-md bg-[#0f1923] border border-slate-700 text-slate-200 cursor-pointer outline-none focus:border-blue-600"
            >
              <option value="off">Không có</option>
              <option value="file">File .srt rời</option>
              <option value="burned">Đốt vào video</option>
              <option value="soft">Phụ đề mềm (MKV)</option>
            </select>
            {subtitleMode !== 'off' && (
              <select
                value={subtitleLang}
                onChange={e => setSubtitleLang(e.target.value)}
                className="text-[11px] px-2 py-1 rounded-md bg-[#0f1923] border border-slate-700 text-slate-200 cursor-pointer outline-none focus:border-blue-600"
              >
                <option value="auto">Tự động</option>
                <option value="vi">Tiếng Việt</option>
                <option value="en">English</option>
                <option value="all">Tất cả ngôn ngữ</option>
              </select>
            )}
            {videoInfo && subtitleMode !== 'off' && (
              videoInfo.has_subtitles
                ? <span className="text-[10px] font-bold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
                    {videoInfo.subtitle_source === 'auto' ? 'tự động' : 'có sẵn'}
                    {videoInfo.available_subtitle_languages?.length > 0 ? ` · ${videoInfo.available_subtitle_languages.slice(0,3).join(', ')}` : ''}
                  </span>
                : <span className="text-[10px] text-slate-500 bg-slate-800/80 px-2 py-0.5 rounded-full border border-slate-700">không có</span>
            )}
          </div>
          {subtitleMode === 'burned' && (
            <p className="text-[10px] text-amber-400 mt-1">Đốt phụ đề vào video — quá trình xử lý lâu hơn, file không có phụ đề riêng.</p>
          )}
          {subtitleMode === 'soft' && (
            <p className="text-[10px] text-indigo-400 mt-1">Phụ đề mềm: video xuất ra file .mkv, dùng VLC để bật/tắt phụ đề khi xem.</p>
          )}
        </div>
      </div>

      {/* ── User Cookie Input ─────────────────────────────── */}
      <div className="w-full max-w-3xl mb-4">
        <button
          type="button"
          onClick={() => setShowUserCookie(v => !v)}
          className="flex items-center gap-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          <span className="font-mono">{showUserCookie ? '▼' : '▶'}</span>
          <span>Dùng cookie của tôi</span>
          <span className="text-[10px] text-slate-600">(nội dung riêng tư, members-only)</span>
        </button>

        {showUserCookie && (
          <div className="mt-2 rounded-xl border border-slate-700/60 bg-slate-900/60 p-3 space-y-3">
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Dán cookie từ trình duyệt để tải nội dung <span className="text-amber-400 font-semibold">members-only, channel membership, hoặc tài khoản đăng nhập</span>.
              Cookie chỉ dùng cho lần tải này — không lưu lại.
            </p>

            {/* Guide */}
            <details className="group">
              <summary className="flex cursor-pointer items-center gap-1.5 text-[11px] text-blue-400 hover:text-blue-300 transition-colors select-none list-none">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="w-3.5 h-3.5 transition-transform group-open:rotate-90">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
                Hướng dẫn lấy cookie từ trình duyệt
              </summary>
              <div className="mt-2.5 rounded-lg border border-slate-700/40 bg-slate-800/40 p-3 space-y-3 text-[11px]">

                {/* Chrome / Edge */}
                <div>
                  <p className="font-semibold text-slate-300 mb-1.5">🔵 Chrome / Edge (cách nhanh nhất)</p>
                  <ol className="space-y-1 text-slate-400 pl-1">
                    <li><span className="text-slate-500 mr-1.5">1.</span>Cài extension <span className="font-mono bg-slate-700/60 px-1 rounded text-slate-300">Get cookies.txt LOCALLY</span> từ Chrome Web Store</li>
                    <li><span className="text-slate-500 mr-1.5">2.</span>Truy cập trang cần tải (YouTube, Instagram…) và <span className="text-white">đăng nhập tài khoản</span></li>
                    <li><span className="text-slate-500 mr-1.5">3.</span>Click icon extension → chọn <span className="text-white font-medium">Export as Netscape</span></li>
                    <li><span className="text-slate-500 mr-1.5">4.</span>Copy toàn bộ nội dung file <span className="font-mono text-slate-300">.txt</span> → dán vào ô bên dưới</li>
                  </ol>
                </div>

                {/* Firefox */}
                <div>
                  <p className="font-semibold text-slate-300 mb-1.5">🦊 Firefox</p>
                  <ol className="space-y-1 text-slate-400 pl-1">
                    <li><span className="text-slate-500 mr-1.5">1.</span>Cài add-on <span className="font-mono bg-slate-700/60 px-1 rounded text-slate-300">Cookie Quick Manager</span></li>
                    <li><span className="text-slate-500 mr-1.5">2.</span>Đăng nhập trang cần tải, mở add-on → <span className="text-white font-medium">Export cookies</span> dạng Netscape</li>
                    <li><span className="text-slate-500 mr-1.5">3.</span>Dán nội dung vào ô bên dưới</li>
                  </ol>
                </div>

                {/* DevTools */}
                <div>
                  <p className="font-semibold text-slate-300 mb-1.5">⚙️ DevTools (không cần extension)</p>
                  <ol className="space-y-1 text-slate-400 pl-1">
                    <li><span className="text-slate-500 mr-1.5">1.</span>Nhấn <span className="font-mono bg-slate-700/60 px-1 rounded text-slate-300">F12</span> → tab <span className="text-white">Application</span> (Chrome) hoặc <span className="text-white">Storage</span> (Firefox)</li>
                    <li><span className="text-slate-500 mr-1.5">2.</span>Mục <span className="text-white">Cookies</span> → chọn domain trang</li>
                    <li><span className="text-slate-500 mr-1.5">3.</span>Dán dạng <span className="font-mono bg-slate-700/60 px-1 rounded text-slate-300">Name=Value; Name2=Value2</span> (Raw format)</li>
                  </ol>
                </div>

                <p className="text-[10px] text-slate-600 border-t border-slate-700/40 pt-2">
                  ⚠️ Cookie chứa thông tin đăng nhập — chỉ dùng trên thiết bị tin cậy. Không chia sẻ cookie với người khác.
                </p>
              </div>
            </details>

            {/* Textarea */}
            <textarea
              value={userCookieText}
              onChange={e => setUserCookieText(e.target.value)}
              placeholder={"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t...\n\nhoặc dán Raw: SID=abc123; HSID=def456\nhoặc JSON array: [{\"name\":\"SID\",\"value\":\"abc123\",...}]"}
              rows={4}
              className="w-full rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 font-mono text-[11px] text-slate-300 placeholder-slate-700 outline-none resize-y focus:border-blue-600 focus:ring-1 focus:ring-blue-600"
              spellCheck={false}
            />
            {userCookieText.trim() ? (
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[10px] text-emerald-400">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} className="w-3 h-3">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  Cookie sẽ được dùng cho lần tải này
                </span>
                <button
                  type="button"
                  onClick={() => setUserCookieText('')}
                  className="text-[10px] text-slate-500 hover:text-red-400 transition-colors"
                >
                  Xoá cookie
                </button>
              </div>
            ) : (
              <p className="text-[10px] text-slate-600">Hỗ trợ: Netscape (.txt từ extension), JSON array, hoặc Raw cookie string</p>
            )}
          </div>
        )}
      </div>

      {/* ── Delayed / queued notice (Phase 27D) ────────── */}
      {delayedInfo && !isLoading && (
        <div className="max-w-2xl mx-auto mb-6 w-full px-4">
          <div className="flex items-start gap-3 text-amber-400 bg-amber-500/10 px-5 py-3.5 rounded-2xl border border-amber-500/20 shadow-sm">
            <svg className="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
            </svg>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold leading-snug">
                {delayedInfo.message || 'Yêu cầu đang xếp hàng chờ xử lý.'}
              </p>
              {delayedInfo.estimatedWaitSec && (
                <p className="text-xs text-amber-300/70 mt-0.5">
                  Ước tính chờ ~{delayedInfo.estimatedWaitSec}s · Thử lại để kiểm tra trạng thái
                </p>
              )}
            </div>
            {url.trim() && (
              <button
                onClick={() => { setDelayedInfo(null); handleFetchLink(); }}
                className="flex-shrink-0 text-xs font-bold px-3 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 transition-colors whitespace-nowrap"
              >
                Thử lại
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Error ────────────────────────────────────────── */}
      {error && (
        <div className="max-w-2xl mx-auto mb-6 w-full px-4">
          <div className="flex items-start gap-3 text-red-400 bg-red-500/10 px-5 py-3.5 rounded-2xl border border-red-500/20 shadow-sm">
            <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
            <p className="text-sm font-bold flex-1">{error}</p>
            {url.trim() && (
              <button
                onClick={() => { setError(''); handleFetchLink(); }}
                className="flex-shrink-0 text-xs font-bold px-3 py-1.5 rounded-lg bg-red-500/20 hover:bg-red-500/30 text-red-300 transition-colors whitespace-nowrap"
              >
                Thử lại
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Result Card ──────────────────────────────────── */}
      {videoInfo && (
        <div className="w-full max-w-3xl mb-12">
          <div className="bg-[#012622]/50 border border-slate-700/50 backdrop-blur-md rounded-3xl p-5 sm:p-6 shadow-2xl shadow-[#FDE047]/5 overflow-hidden">

            {/* Thumbnail & Title */}
            <div className="flex flex-col sm:flex-row gap-5 items-center sm:items-start w-full mb-6">
              <div className="w-44 sm:w-52 aspect-video rounded-2xl overflow-hidden bg-slate-800 flex-shrink-0 border border-slate-700/50 shadow-lg">
                {videoInfo.thumbnail_url ? (
                  <img src={videoInfo.thumbnail_url} alt="Thumbnail" className="w-full h-full object-cover" />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-slate-400 text-sm">Không có ảnh</div>
                )}
              </div>
              <div className="flex-1 text-center sm:text-left min-w-0">
                <h4 className="text-white font-bold text-lg md:text-xl line-clamp-3 mb-3 leading-snug">{videoInfo.title || 'Video tải về'}</h4>
                <div className="flex flex-wrap items-center justify-center sm:justify-start gap-2 text-xs font-medium text-slate-300">
                  <span className="bg-slate-800/80 border border-slate-700/50 px-3 py-1.5 rounded-lg flex items-center gap-1.5">
                    <Link2 className="w-3.5 h-3.5" /> {(() => {
                      try { return new URL(videoInfo.original_url || url).hostname.replace('www.',''); }
                      catch { return 'Liên kết'; }
                    })()}
                  </span>
                  {isWatermarkPlatform(videoInfo.original_url || url) && removeWatermark && (
                    <span className="bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 px-3 py-1.5 rounded-lg flex items-center gap-1.5">
                      <Sparkles className="w-3 h-3" /> Không watermark
                    </span>
                  )}
                  {videoInfo.duration > 0 && (
                    <span className="bg-slate-800/80 border border-slate-700/50 px-3 py-1.5 rounded-lg flex items-center gap-1.5">
                      <Clock className="w-3.5 h-3.5 text-blue-400" />
                      {videoInfo.duration >= 3600 ? 
                        `${Math.floor(videoInfo.duration / 3600)}:${String(Math.floor((videoInfo.duration % 3600) / 60)).padStart(2, '0')}:${String(videoInfo.duration % 60).padStart(2, '0')}`
                        :
                        `${Math.floor(videoInfo.duration / 60)}:${String(videoInfo.duration % 60).padStart(2, '0')}`
                      }
                    </span>
                  )}
                  {videoInfo.file_size_mb > 0 && (
                    <span className="bg-slate-800/80 border border-slate-700/50 px-3 py-1.5 rounded-lg">
                      {videoInfo.file_size_mb.toFixed(1)} MB
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* ── Feature Action Bar ─────────────────────────── */}
            <div className="flex flex-wrap items-center gap-2 mb-5">
              {/* Preview Button */}
              {getPreviewUrl() && (
                <button
                  onClick={togglePreview}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    showPreview 
                      ? 'bg-purple-500/20 text-purple-300 border-purple-500/40'
                      : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-purple-500/40 hover:text-purple-300'
                  }`}
                >
                  {showPreview ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                  {showPreview ? 'Ẩn xem trước' : 'Xem trước'}
                </button>
              )}

              {/* Thumbnail Download */}
              {videoInfo.thumbnail_url && (
                <button
                  onClick={handleThumbnailDownload}
                  className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold bg-slate-800/60 text-slate-300 border border-slate-700/50 hover:border-cyan-500/40 hover:text-cyan-300 transition-all"
                >
                  <ImageDown className="w-3.5 h-3.5" />
                  Tải ảnh bìa
                </button>
              )}

              {/* Trim Button */}
              {videoInfo.duration > 0 && videoInfo.direct_mp4_url && (
                <button
                  onClick={handleOpenTrimmer}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    showTrimmer
                      ? 'bg-orange-500/20 text-orange-300 border-orange-500/40'
                      : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-orange-500/40 hover:text-orange-300'
                  }`}
                >
                  <Scissors className="w-3.5 h-3.5" />
                  Cắt đoạn
                </button>
              )}

              {/* GIF Converter */}
              {videoInfo.duration > 0 && !videoInfo.is_audio_only && (videoInfo.direct_mp4_url || videoInfo.local_file_path) && (
                <button
                  onClick={handleOpenGif}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    showGifPanel
                      ? 'bg-pink-500/20 text-pink-300 border-pink-500/40'
                      : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-pink-500/40 hover:text-pink-300'
                  }`}
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  Tạo GIF
                </button>
              )}

              {/* Logo Inpaint */}
              {videoInfo.local_file_path && !videoInfo.is_audio_only && (
                <button
                  onClick={handleOpenInpaint}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    showInpaint
                      ? 'bg-violet-500/20 text-violet-300 border-violet-500/40'
                      : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-violet-500/40 hover:text-violet-300'
                  }`}
                >
                  <Eraser className="w-3.5 h-3.5" />
                  Xoá Logo
                  <span className="bg-violet-600 text-white text-xs px-1 rounded font-bold leading-tight">PRO</span>
                </button>
              )}

              {/* Merge Clips */}
              <button
                onClick={() => setShowMerge(p => !p)}
                className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                  showMerge
                    ? 'bg-teal-500/20 text-teal-300 border-teal-500/40'
                    : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-teal-500/40 hover:text-teal-300'
                }`}
              >
                <Layers className="w-3.5 h-3.5" />
                Ghép Video
              </button>

              {/* Watermark Embed */}
              {videoInfo.local_file_path && !videoInfo.is_audio_only && (
                <button
                  onClick={() => setShowWatermark(p => !p)}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    showWatermark
                      ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                      : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-amber-500/40 hover:text-amber-300'
                  }`}
                >
                  <Stamp className="w-3.5 h-3.5" />
                  Thêm Watermark
                </button>
              )}

              {/* Chapters */}
              {(videoInfo.chapters?.length > 0) && (
                <button
                  onClick={() => setShowChapters(p => !p)}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    showChapters
                      ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40'
                      : 'bg-slate-800/60 text-slate-300 border-slate-700/50 hover:border-indigo-500/40 hover:text-indigo-300'
                  }`}
                >
                  <List className="w-3.5 h-3.5" />
                  Chapters ({videoInfo.chapters.length})
                  <span className="bg-violet-600 text-white text-xs px-1 rounded font-bold leading-tight">PRO</span>
                </button>
              )}

              {/* Cloud Save */}
              <div className="relative">
                <button
                  onClick={() => setShowCloudMenu(prev => !prev)}
                  className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold bg-slate-800/60 text-slate-300 border border-slate-700/50 hover:border-sky-500/40 hover:text-sky-300 transition-all"
                >
                  <Upload className="w-3.5 h-3.5" />
                  Lưu Cloud
                  <span className="bg-violet-600 text-white text-xs px-1 rounded font-bold leading-tight">PRO</span>
                </button>
                {showCloudMenu && (
                  <div className="absolute top-full left-0 mt-2 w-52 bg-slate-800 border border-slate-700 rounded-xl shadow-2xl z-30 overflow-hidden animate-in fade-in duration-200">
                    <button
                      onClick={handleSaveToGDrive}
                      className="w-full flex items-center gap-3 px-4 py-3 text-sm font-semibold text-white hover:bg-slate-700/70 transition-colors"
                    >
                      <img src="https://upload.wikimedia.org/wikipedia/commons/1/12/Google_Drive_icon_%282020%29.svg" className="w-5 h-5" alt="GDrive" />
                      Google Drive
                      <ExternalLink className="w-3 h-3 ml-auto text-slate-500" />
                    </button>
                    <button
                      onClick={handleSaveToDropbox}
                      className="w-full flex items-center gap-3 px-4 py-3 text-sm font-semibold text-white hover:bg-slate-700/70 transition-colors"
                    >
                      <img src="https://upload.wikimedia.org/wikipedia/commons/7/78/Dropbox_Icon.svg" className="w-5 h-5" alt="Dropbox" />
                      Dropbox
                      <ExternalLink className="w-3 h-3 ml-auto text-slate-500" />
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* ── Preview Player ──────────────────────────────── */}
            {showPreview && getPreviewUrl() && (
              <div className="mb-5 rounded-2xl overflow-hidden bg-black/40 border border-slate-700/50 shadow-lg">
                {videoInfo.is_audio_only ? (
                  <div className="p-5 flex flex-col items-center gap-3">
                    <div className="w-20 h-20 rounded-full bg-gradient-to-br from-purple-500/30 to-blue-500/30 flex items-center justify-center border border-purple-500/30">
                      <Music className="w-10 h-10 text-purple-300" />
                    </div>
                    <audio
                      ref={previewRef}
                      src={getPreviewUrl()}
                      controls
                      className="w-full max-w-md"
                      onPlay={() => setIsPlaying(true)}
                      onPause={() => setIsPlaying(false)}
                      onEnded={() => setIsPlaying(false)}
                    />
                  </div>
                ) : (
                  <video
                    ref={previewRef}
                    src={getPreviewUrl()}
                    controls
                    className="w-full max-h-[360px] object-contain"
                    onPlay={() => setIsPlaying(true)}
                    onPause={() => setIsPlaying(false)}
                    onEnded={() => setIsPlaying(false)}
                  />
                )}
              </div>
            )}

            {/* ── Trimmer UI ─────────────────────────────────── */}
            {showTrimmer && videoInfo.duration > 0 && (
              <div className="mb-5 p-4 rounded-2xl bg-slate-800/50 border border-orange-500/30 shadow-lg">
                <div className="flex items-center justify-between mb-3">
                  <h5 className="text-white font-bold text-sm flex items-center gap-2">
                    <Scissors className="w-4 h-4 text-orange-400" />
                    Cắt đoạn Video / Nhạc
                  </h5>
                  <button onClick={() => setShowTrimmer(false)} className="text-slate-400 hover:text-white transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                </div>
                
                {/* Time Display */}
                <div className="flex items-center justify-between mb-3 text-sm">
                  <span className="bg-slate-900 px-3 py-1.5 rounded-lg text-emerald-400 font-mono font-bold border border-slate-700/50">
                    {formatTime(trimStart)}
                  </span>
                  <span className="text-slate-500 text-xs">→ Thời lượng: {formatTime(trimEnd - trimStart)}</span>
                  <span className="bg-slate-900 px-3 py-1.5 rounded-lg text-orange-400 font-mono font-bold border border-slate-700/50">
                    {formatTime(trimEnd)}
                  </span>
                </div>

                {/* Range Sliders */}
                <div className="space-y-3 mb-4">
                  <div>
                    <label className="text-xs text-slate-400 font-medium mb-1 block">Bắt đầu</label>
                    <input
                      type="range"
                      min={0}
                      max={videoInfo.duration}
                      step={1}
                      value={trimStart}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (v < trimEnd) setTrimStart(v);
                      }}
                      className="w-full accent-emerald-500 h-2 bg-slate-700 rounded-full cursor-pointer"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 font-medium mb-1 block">Kết thúc</label>
                    <input
                      type="range"
                      min={0}
                      max={videoInfo.duration}
                      step={1}
                      value={trimEnd}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (v > trimStart) setTrimEnd(v);
                      }}
                      className="w-full accent-orange-500 h-2 bg-slate-700 rounded-full cursor-pointer"
                    />
                  </div>
                </div>

                {/* Quick Presets */}
                <div className="flex flex-wrap gap-2 mb-4">
                  {[15, 30, 60].map(sec => (
                    <button
                      key={sec}
                      onClick={() => { setTrimStart(0); setTrimEnd(Math.min(sec, videoInfo.duration)); }}
                      className="px-3 py-1.5 rounded-lg text-xs font-bold bg-slate-700/50 text-slate-300 border border-slate-600/50 hover:border-orange-500/40 hover:text-orange-300 transition-all"
                    >
                      {sec}s đầu
                    </button>
                  ))}
                  <button
                    onClick={() => { setTrimStart(0); setTrimEnd(videoInfo.duration); }}
                    className="px-3 py-1.5 rounded-lg text-xs font-bold bg-slate-700/50 text-slate-300 border border-slate-600/50 hover:border-emerald-500/40 hover:text-emerald-300 transition-all"
                  >
                    Toàn bộ
                  </button>
                </div>

                {/* Trim Action */}
                <button
                  onClick={handleTrimDownload}
                  disabled={isTrimming || trimEnd <= trimStart}
                  className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] font-bold text-sm shadow-lg hover:shadow-xl transition-all disabled:opacity-60 active:scale-[0.98]"
                >
                  {isTrimming ? <Loader2 className="w-4 h-4 animate-spin" /> : <Scissors className="w-4 h-4" />}
                  {isTrimming ? 'Đang cắt và xử lý...' : `Cắt & Tải về (${formatTime(trimStart)} → ${formatTime(trimEnd)})`}
                </button>
              </div>
            )}

            {/* ── GIF Converter Panel ─────────────────────────── */}
            {showGifPanel && (
              <div className="mb-5 p-4 rounded-2xl bg-slate-800/50 border border-pink-500/30 shadow-lg">
                <div className="flex items-center justify-between mb-3">
                  <h5 className="text-white font-bold text-sm flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-pink-400" />
                    Chuyển đổi sang GIF
                  </h5>
                  <button onClick={() => setShowGifPanel(false)} className="text-slate-400 hover:text-white transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                </div>

                {/* Time range */}
                <div className="flex items-center justify-between mb-2 text-sm">
                  <span className="bg-slate-900 px-3 py-1.5 rounded-lg text-pink-400 font-mono font-bold border border-slate-700/50">{formatTime(gifStart)}</span>
                  <span className="text-slate-500 text-xs">→ {formatTime(gifEnd - gifStart)} (tối đa 30s)</span>
                  <span className="bg-slate-900 px-3 py-1.5 rounded-lg text-orange-400 font-mono font-bold border border-slate-700/50">{formatTime(gifEnd)}</span>
                </div>
                <div className="space-y-2 mb-4">
                  <div>
                    <label className="text-xs text-slate-400 font-medium mb-1 block">Bắt đầu</label>
                    <input type="range" min={0} max={videoInfo.duration} step={1} value={gifStart}
                      onChange={e => { const v = Number(e.target.value); if (v < gifEnd) setGifStart(v); }}
                      className="w-full accent-pink-500 h-2 bg-slate-700 rounded-full cursor-pointer" />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 font-medium mb-1 block">Kết thúc (tối đa +30s)</label>
                    <input type="range" min={0} max={videoInfo.duration} step={1} value={gifEnd}
                      onChange={e => { const v = Number(e.target.value); if (v > gifStart && v - gifStart <= 30) setGifEnd(v); }}
                      className="w-full accent-orange-500 h-2 bg-slate-700 rounded-full cursor-pointer" />
                  </div>
                </div>

                {/* GIF options */}
                <div className="grid grid-cols-2 gap-3 mb-4">
                  <div>
                    <label className="text-xs text-slate-400 font-medium mb-1 block">Chiều rộng (px)</label>
                    <div className="flex flex-wrap gap-1">
                      {[320, 480, 640, 1080].map(w => (
                        <button key={w} onClick={() => setGifWidth(w)}
                          className={`px-2.5 py-1 rounded-lg text-xs font-bold border transition-all ${gifWidth === w ? 'bg-pink-500/20 border-pink-500/40 text-pink-300' : 'bg-slate-700/50 border-slate-600/50 text-slate-400 hover:text-white'}`}>
                          {w}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 font-medium mb-1 block">FPS</label>
                    <div className="flex flex-wrap gap-1">
                      {[10, 15, 20, 30].map(f => (
                        <button key={f} onClick={() => setGifFps(f)}
                          className={`px-2.5 py-1 rounded-lg text-xs font-bold border transition-all ${gifFps === f ? 'bg-pink-500/20 border-pink-500/40 text-pink-300' : 'bg-slate-700/50 border-slate-600/50 text-slate-400 hover:text-white'}`}>
                          {f}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <button onClick={handleConvertGif} disabled={isConverting || gifEnd <= gifStart || gifEnd - gifStart > 30}
                  className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-gradient-to-r from-pink-500 to-rose-500 text-white font-bold text-sm shadow-lg transition-all disabled:opacity-60 active:scale-[0.98]">
                  {isConverting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                  {isConverting ? 'Đang tạo GIF...' : `Tạo GIF · ${gifWidth}px · ${gifFps}fps · ${formatTime(gifEnd - gifStart)}`}
                </button>
              </div>
            )}

            {/* ── Logo Inpaint Panel ──────────────────────────── */}
            {showInpaint && (
              <div className="mb-5 p-4 rounded-2xl bg-slate-800/50 border border-violet-500/30 shadow-lg">
                <div className="flex items-center justify-between mb-3">
                  <h5 className="text-white font-bold text-sm flex items-center gap-2">
                    <Eraser className="w-4 h-4 text-violet-400" />
                    Logo Inpaint
                    <span className="px-2 py-0.5 rounded-full text-xs bg-violet-500/20 text-violet-300 border border-violet-500/30 font-semibold">Thử nghiệm</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-700/60 text-slate-400 border border-slate-600/40 font-medium">Chỉ web</span>
                  </h5>
                  <button onClick={() => setShowInpaint(false)} className="text-slate-400 hover:text-white transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                </div>

                {/* Safety disclosure — always visible */}
                <div className="mb-3 px-3 py-2.5 rounded-xl bg-violet-500/5 border border-violet-500/20 space-y-1">
                  <p className="text-violet-300 font-semibold text-xs flex items-center gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
                    Logo Inpaint (Experimental) — đọc trước khi dùng
                  </p>
                  <p className="text-slate-400 text-xs">• Kết quả thực nghiệm có thể chứa nhiễu hình ảnh.</p>
                  <p className="text-slate-400 text-xs">• Chỉ sử dụng với nội dung bạn có quyền chỉnh sửa.</p>
                  <p className="text-slate-400 text-xs">• Chất lượng phụ thuộc vào nội dung và chuyển động trong video.</p>
                  <p className="text-slate-400 text-xs">• Tính năng này không hoạt động tự động — bạn phải xác nhận từng bước.</p>
                </div>

                {/* Loading init */}
                {inpaintLoading && inpaintStep === 'region' && (
                  <div className="flex flex-col items-center gap-3 py-6">
                    <Loader2 className="w-8 h-8 text-violet-400 animate-spin" />
                    <p className="text-slate-400 text-sm">Đang tải khung hình...</p>
                  </div>
                )}

                {/* Step 1: Region select */}
                {!inpaintLoading && inpaintStep === 'region' && inpaintFrameUrl && (
                  <div>
                    <p className="text-slate-400 text-xs mb-3">Chọn vị trí logo cần xoá:</p>
                    {/* Preview frame */}
                    <div className="relative mb-3 rounded-xl overflow-hidden border border-slate-700/50 bg-black">
                      <img src={inpaintFrameUrl} alt="preview frame" className="w-full max-h-[220px] object-contain" />
                      {inpaintRegionPct && (
                        <div
                          className="absolute border-2 border-violet-400 bg-violet-400/20 pointer-events-none"
                          style={inpaintRegionPct}
                        />
                      )}
                    </div>
                    {/* Preset buttons */}
                    <p className="text-xs text-slate-500 mb-2">Vị trí logo thường gặp:</p>
                    <div className="grid grid-cols-2 gap-2 mb-3">
                      {INPAINT_PRESETS.map(preset => (
                        <button
                          key={preset.key}
                          onClick={() => setInpaintPreset(preset.key)}
                          className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-bold border transition-all ${
                            inpaintPreset === preset.key
                              ? 'bg-violet-500/20 border-violet-500/40 text-violet-300'
                              : 'bg-slate-700/40 border-slate-600/50 text-slate-300 hover:border-violet-500/30 hover:text-violet-300'
                          }`}
                        >
                          <Move className="w-3.5 h-3.5" />
                          {preset.label}
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={() => inpaintPreset && handleInpaintPreview(inpaintPreset)}
                      disabled={!inpaintPreset}
                      className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-purple-600 text-white font-bold text-sm shadow transition-all disabled:opacity-50 active:scale-[0.98]"
                    >
                      <ZoomIn className="w-4 h-4" />
                      Xem trước kết quả
                    </button>
                  </div>
                )}

                {/* Step 2: Preview result */}
                {(inpaintStep === 'preview' && !inpaintLoading) && inpaintPreviewResult && (
                  <div>
                    {inpaintPreviewResult.recommend_crop && (
                      <div className="mb-3 px-3 py-2 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs font-semibold flex items-center gap-2">
                        <AlertCircle className="w-4 h-4 flex-shrink-0" />
                        Kết quả xem trước chưa mịn — hãy thử crop vùng logo sẽ sạch hơn.
                      </div>
                    )}
                    <p className="text-slate-400 text-xs mb-2">Xem trước (nhanh):</p>
                    <div className="rounded-xl overflow-hidden border border-slate-700/50 bg-black mb-3">
                      <img
                        src={`${API_BASE}${inpaintPreviewResult.preview_clean_url || `/api/v1/flow-cleanup/preview-clean/${inpaintTempId}`}`}
                        alt="inpaint preview"
                        className="w-full max-h-[220px] object-contain"
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => { setInpaintStep('region'); setInpaintPixelRegion(null); setInpaintPreviewResult(null); }}
                        className="flex-1 py-2.5 rounded-xl text-sm font-bold bg-slate-700/50 text-slate-300 border border-slate-600/50 hover:bg-slate-700 transition-all"
                      >
                        Chọn lại vùng
                      </button>
                      <button
                        onClick={handleInpaintProcess}
                        className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-r from-violet-600 to-purple-600 text-white shadow transition-all active:scale-[0.98]"
                      >
                        <Eraser className="w-4 h-4" />
                        Xử lý toàn video
                      </button>
                    </div>
                  </div>
                )}

                {/* Step 2 loading */}
                {inpaintStep === 'preview' && inpaintLoading && (
                  <div className="flex flex-col items-center gap-3 py-6">
                    <Loader2 className="w-8 h-8 text-violet-400 animate-spin" />
                    <p className="text-slate-400 text-sm">Đang tạo xem trước...</p>
                  </div>
                )}

                {/* Step 3: Processing */}
                {inpaintStep === 'processing' && (
                  <div className="flex flex-col items-center gap-3 py-6">
                    <Loader2 className="w-10 h-10 text-violet-400 animate-spin" />
                    <p className="text-white font-semibold text-sm">Đang xoá logo toàn video...</p>
                    <p className="text-slate-400 text-xs text-center">Sử dụng SHIFTMAP motion-aware — có thể mất vài phút tùy độ dài video.</p>
                    <p className="text-slate-500 text-xs text-center">Khuyến nghị: clip dưới 5 phút · file dưới 500 MB · tối đa 1080p</p>
                  </div>
                )}

                {/* Step 4: Done */}
                {inpaintStep === 'done' && (
                  <div className="flex flex-col items-center gap-3 py-4">
                    <CheckCircle2 className="w-10 h-10 text-emerald-400" />
                    <p className="text-white font-semibold text-sm">Xoá logo thành công!</p>
                    <div className="w-full px-3 py-2 rounded-xl bg-slate-700/40 border border-slate-600/40 text-xs text-slate-400 space-y-0.5">
                      <p><span className="text-slate-300 font-semibold">Nguồn:</span> {videoInfo?.title || 'video gốc'}</p>
                      <p><span className="text-slate-300 font-semibold">Đầu ra:</span> video đã xoá logo (file mới, không ghi đè bản gốc)</p>
                    </div>
                    <button
                      onClick={handleInpaintDownload}
                      className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-green-600 text-white font-bold text-sm shadow transition-all active:scale-[0.98]"
                    >
                      <Download className="w-4 h-4" />
                      Tải video đã xoá logo
                    </button>
                    <button
                      onClick={() => { setInpaintStep('region'); setInpaintPreset(null); setInpaintPixelRegion(null); setInpaintPreviewResult(null); setInpaintFinalUrl(null); }}
                      className="text-xs text-slate-400 hover:text-white transition-colors"
                    >
                      Xoá vùng khác
                    </button>
                  </div>
                )}

                {/* Error state */}
                {inpaintStep === 'error' && (
                  <div className="flex flex-col items-center gap-3 py-4">
                    <XCircle className="w-8 h-8 text-red-400" />
                    <p className="text-red-300 text-sm font-semibold text-center">{inpaintError || 'Xử lý thất bại.'}</p>
                    <button
                      onClick={() => { setInpaintStep('region'); setInpaintError(''); }}
                      className="px-5 py-2 rounded-xl text-sm font-bold bg-slate-700 text-slate-200 border border-slate-600 hover:bg-slate-600 transition-all"
                    >
                      Thử lại
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* ── Merge Clips Panel ───────────────────────────────── */}
            {showMerge && (
              <div className="mt-4 p-4 rounded-xl bg-teal-500/5 border border-teal-500/20">
                <h3 className="text-sm font-bold text-teal-300 mb-3 flex items-center gap-2">
                  <Layers className="w-4 h-4" /> Ghép Video (2–5 clip)
                </h3>
                <p className="text-xs text-slate-400 mb-3">
                  Nhập Job ID (từ trang Lịch sử) mỗi dòng một ID hoặc cách nhau bằng dấu phẩy. Tối đa 5 clip, tổng thời lượng ≤30 phút.
                </p>
                <textarea
                  value={mergeJobIds}
                  onChange={e => setMergeJobIds(e.target.value)}
                  placeholder={"job-id-1\njob-id-2\njob-id-3"}
                  rows={4}
                  className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 font-mono resize-none focus:outline-none focus:border-teal-500/50 mb-3"
                />
                {mergeError && <p className="text-xs text-red-400 mb-2">{mergeError}</p>}
                {mergeResult && (
                  <div className="mb-3 p-3 rounded-lg bg-teal-500/10 border border-teal-500/30">
                    <p className="text-xs text-teal-300 font-semibold mb-1">Ghep thanh cong!</p>
                    <p className="text-xs text-slate-400">{mergeResult.title} · {Math.round(mergeResult.duration_seconds)}s · {mergeResult.file_size_mb?.toFixed(1)} MB</p>
                    <button
                      onClick={handleMergeClipsDownload}
                      className="mt-2 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-500/20 text-teal-300 text-xs font-bold hover:bg-teal-500/30 transition-colors cursor-pointer"
                    >
                      <Download className="w-3.5 h-3.5" /> Tai video da ghep
                    </button>
                  </div>
                )}
                <button
                  onClick={handleMergeClips}
                  disabled={mergeLoading || mergeJobIds.trim().length === 0}
                  className="px-4 py-2 rounded-xl bg-teal-500/20 text-teal-300 text-xs font-bold border border-teal-500/30 hover:bg-teal-500/30 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {mergeLoading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {mergeLoading ? 'Dang ghep...' : 'Ghep ngay'}
                </button>
              </div>
            )}

            {/* ── Watermark Embed Panel ─────────────────────────────── */}
            {showWatermark && videoInfo?.local_file_path && !videoInfo?.is_audio_only && (
              <div className="mt-4 p-4 rounded-xl bg-amber-500/5 border border-amber-500/20">
                <h3 className="text-sm font-bold text-amber-300 mb-3 flex items-center gap-2">
                  <Stamp className="w-4 h-4" /> Them Watermark vao Video
                </h3>

                {/* Type toggle */}
                <div className="flex gap-2 mb-3">
                  {['text', 'image'].map(t => (
                    <button key={t} onClick={() => setWmType(t)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors cursor-pointer ${
                        wmType === t ? 'bg-amber-500/20 text-amber-300 border-amber-500/40' : 'bg-slate-800 text-slate-400 border-slate-700 hover:text-slate-300'
                      }`}
                    >{t === 'text' ? 'Chu' : 'Anh'}</button>
                  ))}
                </div>

                {/* Text options */}
                {wmType === 'text' && (
                  <div className="space-y-2 mb-3">
                    <input value={wmText} onChange={e => setWmText(e.target.value)}
                      placeholder="Noi dung watermark..."
                      className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-amber-500/50"
                    />
                    <div className="flex gap-2 items-center">
                      <label className="text-xs text-slate-400">Co chu:</label>
                      <input type="number" value={wmFontSize} onChange={e => setWmFontSize(+e.target.value)}
                        min={12} max={72} className="w-16 bg-slate-900/60 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none"
                      />
                    </div>
                  </div>
                )}

                {/* Image options */}
                {wmType === 'image' && (
                  <div className="mb-3">
                    <input type="file" accept="image/png" onChange={handleWmImageUpload}
                      className="text-xs text-slate-400 file:mr-2 file:px-3 file:py-1 file:rounded-lg file:bg-amber-500/20 file:text-amber-300 file:border-0 file:text-xs file:cursor-pointer cursor-pointer"
                    />
                    {wmImageB64 && <p className="text-xs text-emerald-400 mt-1">Anh da tai len</p>}
                    <p className="text-[10px] text-slate-500 mt-1">PNG · toi da 200KB</p>
                  </div>
                )}

                {/* Position + opacity */}
                <div className="grid grid-cols-2 gap-2 mb-3">
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase mb-1 block">Vi tri</label>
                    <select value={wmPosition} onChange={e => setWmPosition(e.target.value)}
                      className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none"
                    >
                      {[['bottom-right','Goc duoi phai'],['bottom-left','Goc duoi trai'],
                        ['top-right','Goc tren phai'],['top-left','Goc tren trai'],
                        ['center','Giua']].map(([v,l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-[10px] text-slate-500 uppercase mb-1 block">Do trong ({Math.round(wmOpacity*100)}%)</label>
                    <input type="range" min={0.1} max={1} step={0.05} value={wmOpacity}
                      onChange={e => setWmOpacity(+e.target.value)}
                      className="w-full accent-amber-400"
                    />
                  </div>
                </div>

                {wmError && <p className="text-xs text-red-400 mb-2">{wmError}</p>}

                {wmPreviewUrl && (
                  <div className="mb-3 p-2 rounded-lg bg-amber-500/10 border border-amber-500/20">
                    <p className="text-xs text-amber-300 mb-1 font-semibold">Xem truoc (3 giay dau):</p>
                    <video src={wmPreviewUrl} controls className="w-full rounded max-h-32" />
                  </div>
                )}

                {wmResult && (
                  <div className="mb-3 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                    <p className="text-xs text-emerald-300 font-semibold mb-1">Render thanh cong!</p>
                    <p className="text-xs text-slate-400">{wmResult.file_size_mb?.toFixed(1)} MB</p>
                    <a
                      href={`${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(wmResult.output_path)}&filename=watermarked.mp4`}
                      download="watermarked.mp4"
                      className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/20 text-emerald-300 text-xs font-bold hover:bg-emerald-500/30 transition-colors"
                    >
                      <Download className="w-3.5 h-3.5" /> Tai video da watermark
                    </a>
                  </div>
                )}

                <div className="flex gap-2">
                  <button onClick={() => handleWatermarkRender(true)} disabled={wmLoading}
                    className="px-3 py-1.5 rounded-lg bg-slate-700/60 text-slate-300 text-xs font-semibold border border-slate-600 hover:bg-slate-700 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {wmLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin inline mr-1" /> : null}
                    Xem truoc
                  </button>
                  <button onClick={() => handleWatermarkRender(false)} disabled={wmLoading}
                    className="px-4 py-1.5 rounded-lg bg-amber-500/20 text-amber-300 text-xs font-bold border border-amber-500/30 hover:bg-amber-500/30 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {wmLoading ? 'Dang render...' : 'Render day du'}
                  </button>
                </div>
              </div>
            )}

            {/* ── Chapters Panel ──────────────────────────────── */}
            {showChapters && videoInfo.chapters?.length > 0 && (
              <div className="mb-5 rounded-2xl overflow-hidden border border-indigo-500/30 shadow-lg">
                <div className="flex items-center justify-between px-4 py-3 bg-indigo-500/10 border-b border-indigo-500/20">
                  <h5 className="text-white font-bold text-sm flex items-center gap-2">
                    <List className="w-4 h-4 text-indigo-400" />
                    Chapters ({videoInfo.chapters.length})
                  </h5>
                  <button onClick={() => setShowChapters(false)} className="text-slate-400 hover:text-white transition-colors">
                    <X className="w-4 h-4" />
                  </button>
                </div>
                <div className="max-h-64 overflow-y-auto divide-y divide-slate-700/40">
                  {videoInfo.chapters.map((ch, i) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-2.5 bg-slate-800/40 hover:bg-slate-800/70 transition-colors">
                      <span className="text-slate-500 text-xs font-mono w-6 text-right flex-shrink-0">{i + 1}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-white text-sm font-semibold truncate">{ch.title}</p>
                        <p className="text-slate-400 text-xs font-mono">{formatTime(ch.start_time)} → {formatTime(ch.end_time)} · {formatTime(ch.duration)}</p>
                      </div>
                      <button
                        onClick={() => handleChapterDownload(ch)}
                        disabled={downloadingChapter === ch.title}
                        className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/20 transition-all disabled:opacity-50"
                      >
                        {downloadingChapter === ch.title
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Download className="w-3.5 h-3.5" />}
                        Tải
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── Video Digest ───────────────────────────────────────── */}
            {videoInfo && (videoInfo.uploader || videoInfo.view_count || (videoInfo.tags && videoInfo.tags.length > 0) || videoInfo.has_subtitles !== undefined) && (
              <div className="mb-5">
                <DigestCard
                  info={videoInfo}
                  isVisible={showDigest}
                  onToggle={() => {
                    const next = !showDigest;
                    setShowDigest(next);
                    localStorage.setItem('vg_show_digest', String(next));
                  }}
                />
              </div>
            )}

            {/* ── Quick Actions (Phase 21) ─────────────────── */}
            {videoInfo && (
              <div className="mb-4">
                <QuickActionBar
                  platform={detectedPlatform}
                  videoInfo={videoInfo}
                  activePreset={activePreset}
                  onAction={handleQuickAction}
                  disabled={!!downloadingId}
                  userTier={session?.user?.user_metadata?.tier || 'free'}
                />
              </div>
            )}

            {/* ── Format Tabs or Fallback ──────────────────── */}
            {hasFormats ? (
              <>
                {/* Tab Switcher */}
                <div className="flex gap-1 p-1 bg-slate-800/60 rounded-xl mb-4 max-w-xs">
                  <button
                    onClick={() => setFormatTab('video')}
                    className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-bold transition-all ${formatTab === 'video' ? 'bg-gradient-to-r from-[#10b981] to-[#059669] text-white shadow-md' : 'text-slate-400 hover:text-white'}`}
                  >
                    <Video className="w-4 h-4" /> Video ({videoFormats.length + (showMergeOption ? 1 : 0)})
                  </button>
                  <button
                    onClick={() => setFormatTab('audio')}
                    className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-bold transition-all ${formatTab === 'audio' ? 'bg-gradient-to-r from-[#3b82f6] to-[#1d4ed8] text-white shadow-md' : 'text-slate-400 hover:text-white'}`}
                  >
                    <Music className="w-4 h-4" /> Âm thanh ({audioFormats.length})
                  </button>
                </div>

                {/* Format List */}
                <div className="flex flex-col gap-2.5">
                  {formatTab === 'video' && (
                    <>
                      {/* 4K Merge Option (if available) */}
                      {showMergeOption && (
                        <button
                          onClick={handleMergeDownload}
                          disabled={downloadingId === 'merge_4k'}
                          className="w-full flex items-center justify-between px-5 py-3.5 rounded-2xl bg-gradient-to-r from-[#FBBF24]/15 to-[#F59E0B]/15 border border-[#FBBF24]/40 hover:border-[#FBBF24]/70 text-white transition-all disabled:opacity-60 active:scale-[0.99] group"
                        >
                          <div className="flex items-center gap-3">
                            <Crown className="w-5 h-5 text-[#FBBF24]" />
                            <div className="text-left">
                              <div className="flex items-center gap-2">
                                <ResBadge label={maxMergeHeight >= 2160 ? '4K' : maxMergeHeight >= 1440 ? '2K' : `${maxMergeHeight}p`} height={maxMergeHeight} />
                                <span className="text-sm font-bold">Chất lượng cao nhất</span>
                                <span className="bg-violet-600 text-white text-xs px-1 rounded font-bold">PRO</span>
                              </div>
                              <p className="text-xs text-slate-400 mt-0.5">Video + Audio ghép • Cần xử lý</p>
                            </div>
                          </div>
                          {downloadingId === 'merge_4k' ? <Loader2 className="w-5 h-5 animate-spin text-[#FBBF24]" /> : <Download className="w-5 h-5 text-[#FBBF24] group-hover:scale-110 transition-transform" />}
                        </button>
                      )}

                      {/* Combined video+audio formats and Video-only formats (Requires merge) */}
                      {videoFormats.map((fmt, i) => {
                        // Check if this format is already available from the initial download
                        const isAlreadyDownloaded = fmt.requires_merge && videoInfo?.local_file_path && videoInfo?.downloaded_height >= fmt.height;
                        const displaySize = isAlreadyDownloaded && fmt.height === videoInfo?.downloaded_height ? videoInfo.file_size_mb : fmt.filesize_mb;
                        
                        return (
                        <button
                          key={`v-${i}`}
                          onClick={() => fmt.requires_merge ? handleMergeDownload(fmt.height) : handleFormatDownload(fmt)}
                          disabled={downloadingId === `merge_${fmt.height}`}
                          className={`w-full flex items-center justify-between px-5 py-3.5 rounded-2xl ${
                            isAlreadyDownloaded
                              ? 'bg-emerald-500/10 border border-emerald-500/40 hover:border-emerald-400/70'
                              : 'bg-slate-800/50 border border-slate-700/40 hover:border-emerald-500/50 hover:bg-slate-800/80'
                          } text-white transition-all disabled:opacity-60 active:scale-[0.99] group`}
                        >
                          <div className="flex items-center gap-3">
                            <Video className="w-5 h-5 text-emerald-400" />
                            <div className="text-left">
                              <div className="flex items-center gap-2">
                                <ResBadge label={fmt.label} height={fmt.height} />
                                <span className="text-sm font-bold">{fmt.resolution}</span>
                                <span className="text-xs text-slate-500 uppercase">{fmt.ext}</span>
                                {fmt.recommended && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                    ✓ MỌI THIẾT BỊ
                                  </span>
                                )}
                                {fmt.universal === false && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                                    VP9/AV1
                                  </span>
                                )}
                                {isAlreadyDownloaded ? (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                    SẴN SÀNG
                                  </span>
                                ) : fmt.requires_merge && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">
                                    GHÉP TỆP
                                  </span>
                                )}
                                {fmt.label?.includes('No Watermark') && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                    KHÔNG LOGO
                                  </span>
                                )}
                                {fmt.label?.includes('With Watermark') && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-orange-500/20 text-orange-300 border border-orange-500/30">
                                    CÓ LOGO
                                  </span>
                                )}
                              </div>
                              {displaySize > 0 && <p className="text-xs text-slate-400 mt-0.5">{fmt.size_estimated && !isAlreadyDownloaded ? '~' : ''}{displaySize.toFixed(1)} MB{isAlreadyDownloaded ? ' (đã tải)' : ''}</p>}
                              {fmt.universal === false && (
                                <p className="text-[11px] text-amber-400/80 mt-0.5">⚠ Định dạng VP9/AV1 — nếu máy không phát được, mở bằng VLC (miễn phí, mọi nền tảng)</p>
                              )}
                            </div>
                          </div>
                          {downloadingId === `merge_${fmt.height}` ? (
                            <Loader2 className="w-5 h-5 animate-spin text-emerald-400" />
                          ) : (
                            <Download className="w-5 h-5 text-emerald-400 group-hover:scale-110 transition-transform" />
                          )}
                        </button>
                      ); })}

                      {videoFormats.length === 0 && !showMergeOption && (
                        <div className="text-center py-6 text-slate-400 text-sm">
                          Không tìm thấy định dạng video riêng lẻ.
                          <button onClick={handleDefaultDownload} className="block mx-auto mt-3 px-6 py-2.5 rounded-xl bg-gradient-to-r from-[#10b981] to-[#059669] text-white font-bold text-sm">
                            <Download className="w-4 h-4 inline mr-1.5" />Tải video mặc định
                          </button>
                        </div>
                      )}
                    </>
                  )}

                  {formatTab === 'audio' && (
                    <>
                      {audioFormats.map((fmt, i) => (
                        <button
                          key={`a-${i}`}
                          onClick={() => fmt.requires_merge ? handleMergeDownload(fmt.ext) : handleFormatDownload(fmt)}
                          disabled={downloadingId === `merge_${fmt.ext}`}
                          className="w-full flex items-center justify-between px-5 py-3.5 rounded-2xl bg-slate-800/50 border border-slate-700/40 hover:border-blue-500/50 hover:bg-slate-800/80 text-white transition-all disabled:opacity-60 active:scale-[0.99] group"
                        >
                          <div className="flex items-center gap-3">
                            <Music className="w-5 h-5 text-blue-400" />
                            <div className="text-left">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-sm font-bold text-white">Âm thanh</span>
                              </div>
                              <div className="flex items-center gap-2">
                                <span className="px-1.5 py-0.5 rounded text-[10px] font-black bg-blue-500/20 text-blue-300 border border-blue-500/30 uppercase">
                                  {fmt.ext}
                                </span>
                                <span className="px-1.5 py-0.5 rounded text-[10px] font-black bg-purple-500/20 text-purple-300 border border-purple-500/30 uppercase">
                                  CHỈ ÂM THANH
                                </span>
                                <span className="text-xs font-semibold text-slate-400">{fmt.label}</span>
                              </div>
                            </div>
                          </div>
                          {downloadingId === `merge_${fmt.ext}` ? (
                            <Loader2 className="w-5 h-5 animate-spin text-blue-400" />
                          ) : (
                            <Download className="w-5 h-5 text-blue-400 group-hover:scale-110 transition-transform" />
                          )}
                        </button>
                      ))}
                      {audioFormats.length === 0 && (
                        <div className="text-center py-6 text-slate-400 text-sm">
                          <p className="mb-4">Không tìm thấy định dạng âm thanh riêng lẻ.</p>
                          <button
                            onClick={handleAudioDownload}
                            disabled={downloadingId === 'audio_mp3'}
                            className="w-full flex items-center justify-between px-6 py-4 rounded-2xl bg-[#1e293b] hover:bg-[#334155] border border-slate-700/50 text-white font-bold shadow-lg transition-all disabled:opacity-70 active:scale-[0.98]"
                          >
                            <div className="flex items-center gap-3">
                              <Music className="w-6 h-6 text-[#38bdf8]" />
                              <span className="text-base sm:text-lg">Tải Âm Thanh (MP3 320kbps)</span>
                            </div>
                            {downloadingId === 'audio_mp3' ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />}
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </>
            ) : (
              /* Fallback: no format list (TikTok/Douyin) */
              <div className="flex flex-col gap-3 max-w-lg mx-auto w-full">
                <button onClick={handleDefaultDownload} className="w-full flex items-center justify-between px-6 py-4 rounded-2xl bg-gradient-to-r from-[#10b981] to-[#059669] hover:from-[#059669] hover:to-[#047857] text-white font-bold shadow-lg transition-all active:scale-[0.98]">
                  <div className="flex items-center gap-3">
                    <Video className="w-6 h-6" />
                    <span className="text-base">{removeWatermark ? 'Tải Video (Không logo)' : 'Tải Video'}</span>
                  </div>
                  <Download className="w-5 h-5" />
                </button>
                <button
                  onClick={handleMergeDownload}
                  disabled={downloadingId === 'merge_4k'}
                  className="w-full flex items-center justify-between px-6 py-4 rounded-2xl bg-gradient-to-r from-[#FBBF24] to-[#F59E0B] hover:from-[#F59E0B] hover:to-[#D97706] text-[#012622] font-bold shadow-lg transition-all disabled:opacity-70 active:scale-[0.98]"
                >
                  <div className="flex items-center gap-3">
                    <Crown className="w-6 h-6" />
                    <span className="text-base">Tải Video (HD / 4K)</span>
                  </div>
                  {downloadingId === 'merge_4k' ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />}
                </button>
                <button
                  onClick={handleAudioDownload}
                  disabled={downloadingId === 'audio_mp3'}
                  className="w-full flex items-center justify-between px-6 py-4 rounded-2xl bg-[#1e293b] hover:bg-[#334155] border border-slate-700/50 text-white font-bold shadow-lg transition-all disabled:opacity-70 active:scale-[0.98]"
                >
                  <div className="flex items-center gap-3">
                    <Music className="w-6 h-6 text-[#38bdf8]" />
                    <span className="text-base">Tải Âm Thanh (MP3 320kbps)</span>
                  </div>
                  {downloadingId === 'audio_mp3' ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />}
                </button>
              </div>
            )}

            {/* ── Success Card (Phase 21) / Smart Actions (Phase 19) ── */}
            {showSuccessCard && successCardInfo ? (
              <div className="mt-4">
                <SuccessCard
                  jobInfo={successCardInfo}
                  onRetry={(srcUrl) => { setShowSuccessCard(false); if (srcUrl) setUrl(srcUrl); }}
                  onTrim={() => setShowTrimmer(true)}
                  onGif={() => setShowGifPanel(true)}
                  onProcessing={() => setShowProcessingHub(true)}
                  onSavePreset={(name) => createPreset({ name, platform: detectedPlatform, settings: { quality: successCardInfo?.quality || 'video' } })}
                  onClose={() => setShowSuccessCard(false)}
                  userTier={session?.user?.user_metadata?.tier || 'free'}
                />
              </div>
            ) : lastDownloadInfo ? (
              <div className="mt-4">
                <SmartActionsPanel
                  videoInfo={videoInfo}
                  mediaUrl={videoInfo?.direct_mp4_url || videoInfo?.original_url}
                />
              </div>
            ) : null}

            {/* ── Processing Hub (Phase 22) ─────────────────── */}
            {showProcessingHub && videoInfo && (
              <ProcessingHub
                videoInfo={videoInfo}
                localPath={videoInfo.local_file_path || null}
                sourceUrl={videoInfo.original_url || url}
                onClose={() => setShowProcessingHub(false)}
                userTier={session?.user?.user_metadata?.tier || 'free'}
                initialTab={processingHubTab}
              />
            )}

            {/* Cancel/Reset */}
            <div className="text-center mt-6">
              <button onClick={() => { setVideoInfo(null); setLastDownloadInfo(null); setShowSuccessCard(false); setSuccessCardInfo(null); }} className="px-8 py-2.5 rounded-full text-slate-400 font-semibold hover:text-white hover:bg-white/10 transition-colors text-sm border border-transparent hover:border-slate-700">
                Tải video khác
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Spotify Playlist / Album Track List ─────────── */}
      {spotifyData && (
        <div className="w-full max-w-3xl mb-12">
          <div className="bg-[#012622]/50 border border-[#1DB954]/30 backdrop-blur-md rounded-3xl p-5 sm:p-6 shadow-2xl overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-4 mb-6">
              {spotifyData.thumbnail && (
                <img src={spotifyData.thumbnail} alt="cover"
                  className="w-20 h-20 rounded-2xl object-cover flex-shrink-0 border border-slate-700/50 shadow-lg" />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="px-2 py-0.5 rounded-full text-[11px] font-black bg-[#1DB954]/20 text-[#1DB954] border border-[#1DB954]/40 uppercase tracking-wider">
                    {spotifyData.type === 'album' ? 'Album' : 'Playlist'} • Spotify
                  </span>
                </div>
                <h3 className="text-white font-bold text-xl line-clamp-1">
                  {spotifyData.playlist_name || spotifyData.album_name}
                </h3>
                {spotifyData.artist && (
                  <p className="text-slate-400 text-sm mt-0.5">{spotifyData.artist}</p>
                )}
                <p className="text-slate-500 text-xs mt-1">{spotifyData.tracks?.length || 0} bài nhạc</p>
              </div>
              <button onClick={() => setSpotifyData(null)}
                className="text-slate-500 hover:text-white transition-colors text-sm px-3 py-1.5 rounded-lg hover:bg-white/10">
                ✕
              </button>
            </div>
            
            {/* Download All / Selected (ZIP) Button */}
            {(spotifyData.tracks && spotifyData.tracks.length > 0) && (
              <div className="flex justify-between items-center px-2 mb-2 gap-2">
                {selectedTracks.size > 0 ? (
                  <span className="text-xs text-emerald-400 font-medium">
                    Đã chọn {selectedTracks.size}/{spotifyData.tracks.length} bài
                  </span>
                ) : (
                  <span className="text-xs text-slate-500">Chọn bài hoặc tải tất cả</span>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={handleDownloadAllZip}
                    disabled={isZipping}
                    className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold rounded-xl shadow-lg hover:scale-105 transition-all duration-300 disabled:opacity-50 disabled:hover:scale-100"
                  >
                    {isZipping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                    {isZipping
                      ? `Đang tải & Nén... ${zipProgress}%`
                      : selectedTracks.size > 0
                        ? `Tải ${selectedTracks.size} bài (.ZIP)`
                        : `Tải tất cả (.ZIP)`
                    }
                  </button>
                  {isZipping && (
                    <button
                      onClick={handleCancelZip}
                      className="flex items-center justify-center w-9 h-9 bg-red-500/20 text-red-400 hover:bg-red-500/40 hover:text-white rounded-xl transition-all"
                      title="Hủy nén ZIP"
                    >
                      <X className="w-5 h-5" />
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Track list as Table */}
            <div className="max-h-[480px] overflow-y-auto pr-1 custom-scrollbar bg-slate-800/40 rounded-xl border border-slate-700/30">
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead className="sticky top-0 bg-slate-900/90 backdrop-blur-md text-slate-400 text-xs font-semibold uppercase tracking-wider z-10">
                  <tr>
                    <th className="px-3 py-3 w-10">
                      <input
                        type="checkbox"
                        checked={spotifyData.tracks?.length > 0 && selectedTracks.size === spotifyData.tracks.length}
                        onChange={handleToggleAll}
                        className="w-4 h-4 accent-emerald-500 rounded cursor-pointer"
                        title="Chọn tất cả"
                      />
                    </th>
                    <th className="px-4 py-3 w-12">#</th>
                    <th className="px-4 py-3">Bài hát</th>
                    <th className="px-4 py-3">Ca sĩ</th>
                    <th className="px-4 py-3 w-20 text-center">Thời lượng</th>
                    <th className="px-4 py-3 w-28 text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/30">
                  {(spotifyData.tracks || []).map((track, i) => {
                    const key = track.search_query;
                    const dlState = trackDownloads[key];
                    const isSelected = selectedTracks.has(key);
                    return (
                      <tr key={i} className={`hover:bg-slate-700/30 transition-colors group ${isSelected ? 'bg-emerald-900/20' : ''}`}>
                        <td className="px-3 py-3">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => handleToggleTrack(key)}
                            className="w-4 h-4 accent-emerald-500 rounded cursor-pointer"
                          />
                        </td>
                        <td className="px-4 py-3 text-slate-500 font-medium">
                          {track.thumbnail ? (
                            <img src={track.thumbnail} alt="" className="w-8 h-8 rounded object-cover shadow-sm" />
                          ) : (
                            <div className="w-8 h-8 rounded bg-[#1DB954]/20 flex items-center justify-center">
                              <Music className="w-4 h-4 text-[#1DB954]" />
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-white font-medium max-w-[200px] truncate" title={track.name}>
                          {track.name}
                        </td>
                        <td className="px-4 py-3 text-slate-400 max-w-[150px] truncate" title={track.artist_str}>
                          {track.artist_str}
                        </td>
                        <td className="px-4 py-3 text-slate-500 text-center">
                          {track.duration > 0 ? `${Math.floor(track.duration / 60)}:${String(track.duration % 60).padStart(2, '0')}` : '--:--'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={() => handleSpotifyTrackDownload(track)}
                            disabled={dlState === 'loading' || dlState === 'done'}
                            className={`inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all border ${
                              dlState === 'done'
                                ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30 cursor-default'
                                : dlState === 'error'
                                ? 'bg-red-500/20 text-red-300 border-red-500/30'
                                : dlState === 'loading'
                                ? 'bg-slate-700 text-slate-400 border-slate-600 cursor-wait'
                                : 'bg-[#1DB954]/10 text-[#1DB954] border-[#1DB954]/30 hover:bg-[#1DB954]/20'
                            }`}
                          >
                            {dlState === 'loading' ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : dlState === 'done' ? (
                              <CheckCircle2 className="w-3.5 h-3.5" />
                            ) : (
                              <Download className="w-3.5 h-3.5" />
                            )}
                            {dlState === 'done' ? 'Xong' : dlState === 'error' ? 'Lỗi' : 'Tải MP3'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── Spotify Artist ───────────────────────────────── */}
      {artistData && (
        <div className="w-full max-w-3xl mb-12">
          <div className="bg-[#012622]/50 border border-[#1DB954]/30 backdrop-blur-md rounded-3xl p-5 sm:p-6 shadow-2xl overflow-hidden">
            {/* Artist header */}
            <div className="flex items-start gap-4 mb-4">
              {artistData.artist?.image ? (
                <img src={artistData.artist.image} alt="" className="w-16 h-16 sm:w-20 sm:h-20 rounded-full object-cover flex-shrink-0 border border-slate-700/50 shadow-lg" />
              ) : (
                <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-full bg-[#1DB954]/20 flex items-center justify-center flex-shrink-0">
                  <Music className="w-8 h-8 text-[#1DB954]" />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="px-2 py-0.5 rounded-full text-[11px] font-black bg-[#1DB954]/20 text-[#1DB954] border border-[#1DB954]/40 uppercase tracking-wider">
                    Nghệ sĩ • Spotify
                  </span>
                  {typeof artistData.artist?.popularity === 'number' && (
                    <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-700/60 text-slate-300 border border-slate-600/50">
                      Độ phổ biến {artistData.artist.popularity}/100
                    </span>
                  )}
                </div>
                <h3 className="text-white font-bold text-xl sm:text-2xl line-clamp-1 mt-1">{artistData.artist?.name}</h3>
                <p className="text-slate-500 text-xs mt-1">
                  {artistData.artist?.followers ? `${artistData.artist.followers.toLocaleString()} người theo dõi · ` : ''}
                  {artistData.summary?.top_tracks_count || 0} top tracks · {artistData.summary?.albums_count || 0} album · {artistData.summary?.singles_count || 0} single
                </p>
                {artistData.artist?.genres?.length > 0 && (
                  <p className="text-slate-600 text-[11px] mt-0.5 line-clamp-1">{artistData.artist.genres.join(', ')}</p>
                )}
                {artistData.artist?.external_url && (
                  <a href={artistData.artist.external_url} target="_blank" rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-[#1DB954] hover:underline mt-1">
                    <ExternalLink className="w-3 h-3" /> Mở trên Spotify
                  </a>
                )}
              </div>
              <button onClick={() => {
                  if (artistPreviewRef.current) { artistPreviewRef.current.pause(); artistPreviewRef.current = null; }
                  setArtistPreviewKey(null); setExpandedAlbum(null);
                  setArtistData(null); setSelectedTracks(new Set());
                }}
                className="text-slate-500 hover:text-white transition-colors text-sm px-3 py-1.5 rounded-lg hover:bg-white/10 flex-shrink-0">✕</button>
            </div>

            {/* Partial notice (no API key) */}
            {artistData.partial && (
              <div className="mb-4 text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-xl px-3 py-2">
                Hiện chỉ lấy được <b>Top tracks</b> cho nghệ sĩ này. Albums/Singles tạm chưa khả dụng.
              </div>
            )}

            {/* Action buttons */}
            <div className="flex flex-wrap gap-2 mb-3">
              {[
                { mode: 'top_tracks', label: 'Tải Top tracks', on: artistData.actions?.can_download_top_tracks },
                { mode: 'albums', label: 'Tải tất cả album', on: artistData.actions?.can_download_all_albums },
                { mode: 'singles', label: 'Tải tất cả single', on: artistData.actions?.can_download_all_singles },
                { mode: 'all_tracks', label: 'Tải tất cả bài', on: artistData.actions?.can_download_all_tracks },
              ].map(b => (
                <button key={b.mode} onClick={() => handleArtistDownload(b.mode)} disabled={isZipping || !b.on}
                  className={`flex items-center gap-1.5 px-3.5 py-2 text-sm font-bold rounded-xl transition-all ${
                    b.mode === 'top_tracks'
                      ? 'bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] hover:scale-105'
                      : 'bg-[#1DB954]/10 text-[#1DB954] border border-[#1DB954]/30 hover:bg-[#1DB954]/20'
                  } disabled:opacity-40 disabled:hover:scale-100 disabled:cursor-not-allowed`}>
                  {isZipping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}{b.label}
                </button>
              ))}
            </div>
            {isZipping && (
              <div className="mb-3 text-xs text-emerald-400 flex items-center gap-2">
                Đang tải & nén... {zipProgress}%
                <button onClick={handleCancelZip} className="text-red-400 hover:text-red-300 font-semibold">Hủy</button>
              </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 mb-3 border-b border-slate-700/40">
              {[
                { k: 'top', label: `Top tracks (${artistData.summary?.top_tracks_count || 0})` },
                { k: 'albums', label: `Albums (${artistData.summary?.albums_count || 0})` },
                { k: 'singles', label: `Singles (${artistData.summary?.singles_count || 0})` },
              ].map(t => (
                <button key={t.k} onClick={() => setArtistTab(t.k)}
                  className={`px-3 py-2 text-sm font-semibold transition-colors ${
                    artistTab === t.k ? 'text-[#1DB954] border-b-2 border-[#1DB954]' : 'text-slate-400 hover:text-slate-200'
                  }`}>{t.label}</button>
              ))}
            </div>

            {/* Top tracks */}
            {artistTab === 'top' && (
              (artistData.top_tracks || []).length === 0 ? (
                <p className="text-slate-500 text-sm py-8 text-center">Chưa lấy được top tracks của nghệ sĩ này.</p>
              ) : (() => {
                const topKeys = (artistData.top_tracks || []).map(t => t.search_query);
                const allTopSelected = topKeys.length > 0 && topKeys.every(k => selectedTracks.has(k));
                const toggleAllTop = () => setSelectedTracks(prev => {
                  const n = new Set(prev);
                  if (allTopSelected) topKeys.forEach(k => n.delete(k));
                  else topKeys.forEach(k => n.add(k));
                  return n;
                });
                return (
                  <>
                    <div className="flex items-center justify-between px-1 mb-2">
                      <button onClick={toggleAllTop} className="text-xs font-semibold text-[#1DB954] hover:underline">
                        {allTopSelected ? 'Bỏ chọn tất cả' : 'Chọn tất cả'}
                      </button>
                      {selectedTracks.size > 0 && (
                        <span className="text-xs text-emerald-400">Đã chọn {topKeys.filter(k => selectedTracks.has(k)).length}/{topKeys.length}</span>
                      )}
                    </div>
                    <div className="max-h-[420px] overflow-y-auto pr-1 custom-scrollbar bg-slate-800/40 rounded-xl border border-slate-700/30 divide-y divide-slate-700/30">
                      {(artistData.top_tracks || []).map((track, i) => {
                        const key = track.search_query;
                        const dlState = trackDownloads[key];
                        const isSelected = selectedTracks.has(key);
                        const pk = track.spotify_track_id || track.search_query;
                        const isPreviewing = artistPreviewKey === pk;
                        return (
                          <div key={i} className={`flex items-center gap-2.5 px-3 py-2.5 hover:bg-slate-700/30 transition-colors ${isSelected ? 'bg-emerald-900/20' : ''}`}>
                            <input type="checkbox" checked={isSelected} onChange={() => handleToggleTrack(key)}
                              className="w-4 h-4 accent-emerald-500 rounded cursor-pointer flex-shrink-0" />
                            <span className="text-slate-500 text-xs w-4 text-right flex-shrink-0 hidden sm:block">{i + 1}</span>
                            {track.thumbnail ? (
                              <img src={track.thumbnail} alt="" className="w-9 h-9 rounded object-cover flex-shrink-0" />
                            ) : (
                              <div className="w-9 h-9 rounded bg-[#1DB954]/20 flex items-center justify-center flex-shrink-0"><Music className="w-4 h-4 text-[#1DB954]" /></div>
                            )}
                            <div className="flex-1 min-w-0">
                              <p className="text-white text-sm font-medium truncate" title={track.name}>{track.name}</p>
                              <p className="text-slate-400 text-xs truncate" title={`${track.artist_str}${track.album_name ? ' · ' + track.album_name : ''}`}>
                                {track.artist_str}{track.album_name ? <span className="text-slate-600"> · {track.album_name}</span> : ''}
                              </p>
                            </div>
                            {track.preview_url && (
                              <button onClick={() => toggleArtistPreview(track)} title="Nghe thử 30s"
                                className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center border transition-colors ${
                                  isPreviewing ? 'bg-[#1DB954] text-[#012622] border-[#1DB954]' : 'bg-slate-700/50 text-slate-300 border-slate-600 hover:text-white'}`}>
                                {isPreviewing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                              </button>
                            )}
                            <span className="text-slate-500 text-xs flex-shrink-0 hidden sm:block">
                              {track.duration > 0 ? `${Math.floor(track.duration / 60)}:${String(track.duration % 60).padStart(2, '0')}` : '--:--'}
                            </span>
                            <button onClick={() => handleSpotifyTrackDownload(track)} disabled={dlState === 'loading' || dlState === 'done'}
                              className={`inline-flex items-center justify-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold border flex-shrink-0 ${
                                dlState === 'done' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30 cursor-default'
                                : dlState === 'error' ? 'bg-red-500/20 text-red-300 border-red-500/30'
                                : dlState === 'loading' ? 'bg-slate-700 text-slate-400 border-slate-600 cursor-wait'
                                : 'bg-[#1DB954]/10 text-[#1DB954] border-[#1DB954]/30 hover:bg-[#1DB954]/20'}`}>
                              {dlState === 'loading' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : dlState === 'done' ? <CheckCircle2 className="w-3.5 h-3.5" /> : <Download className="w-3.5 h-3.5" />}
                              <span className="hidden sm:inline">{dlState === 'done' ? 'Xong' : dlState === 'error' ? 'Lỗi' : 'MP3'}</span>
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </>
                );
              })()
            )}

            {/* Albums / Singles */}
            {(artistTab === 'albums' || artistTab === 'singles') && (() => {
              const list = (artistTab === 'albums' ? artistData.albums : artistData.singles) || [];
              if (artistData.partial) {
                return (
                  <p className="text-slate-500 text-sm py-8 text-center px-4">
                    {artistTab === 'albums' ? 'Danh sách album' : 'Danh sách single'} tạm chưa khả dụng cho nghệ sĩ này.
                    <br /><span className="text-slate-600 text-xs">(Cần Spotify API với tài khoản Premium của chủ app.)</span>
                  </p>
                );
              }
              if (list.length === 0) {
                return (
                  <p className="text-slate-500 text-sm py-8 text-center">
                    {artistTab === 'albums' ? 'Nghệ sĩ này chưa có album.' : 'Nghệ sĩ này chưa có single riêng.'}
                  </p>
                );
              }
              return (
                <div className="space-y-2 max-h-[440px] overflow-y-auto pr-1 custom-scrollbar">
                  {list.map((al, i) => {
                    const isOpen = expandedAlbum === al.album_id;
                    const cached = albumTracksCache[al.album_id];
                    const kind = artistTab === 'singles' ? (al.total_tracks >= 4 ? 'EP' : 'Single') : (al.album_type === 'compilation' ? 'Tuyển tập' : 'Album');
                    return (
                      <div key={i} className="rounded-xl bg-slate-800/40 border border-slate-700/30 overflow-hidden">
                        <div className="flex items-center gap-3 p-3">
                          {al.cover_image ? (
                            <img src={al.cover_image} alt="" className="w-12 h-12 rounded object-cover flex-shrink-0" />
                          ) : (
                            <div className="w-12 h-12 rounded bg-[#1DB954]/20 flex items-center justify-center flex-shrink-0"><Music className="w-5 h-5 text-[#1DB954]" /></div>
                          )}
                          <div className="flex-1 min-w-0">
                            <p className="text-white text-sm font-medium truncate" title={al.title}>{al.title}</p>
                            <p className="text-slate-500 text-xs">
                              <span className="text-[#1DB954]/80">{kind}</span> · {(al.release_date || '').slice(0, 4) || '—'} · {al.total_tracks} bài
                            </p>
                          </div>
                          <button onClick={() => toggleAlbumExpand(al)} title="Xem bài hát"
                            className="flex-shrink-0 px-2 py-1.5 rounded-lg text-xs font-bold text-slate-300 border border-slate-600 hover:bg-slate-700/50">
                            {isOpen ? '▴' : '▾'}
                          </button>
                          <button onClick={() => handleArtistAlbumDownload(al)} disabled={isZipping}
                            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold bg-[#1DB954]/10 text-[#1DB954] border border-[#1DB954]/30 hover:bg-[#1DB954]/20 disabled:opacity-40 flex-shrink-0">
                            <Download className="w-3.5 h-3.5" /><span className="hidden sm:inline">Tải</span>
                          </button>
                        </div>
                        {isOpen && (
                          <div className="px-3 pb-3 border-t border-slate-700/30 pt-2">
                            {cached?.state === 'loading' && (
                              <p className="text-slate-500 text-xs py-2 flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Đang tải tracklist...</p>
                            )}
                            {cached?.state === 'error' && (
                              <p className="text-slate-500 text-xs py-2">Chưa lấy được tracklist của album này.</p>
                            )}
                            {cached?.state === 'ok' && (
                              <ol className="text-xs text-slate-300 space-y-1 mt-1">
                                {cached.tracks.map((tr, j) => (
                                  <li key={j} className="flex items-center gap-2">
                                    <span className="text-slate-600 w-5 text-right">{j + 1}</span>
                                    <span className="flex-1 truncate" title={tr.name}>{tr.name}</span>
                                    <span className="text-slate-600">{tr.duration > 0 ? `${Math.floor(tr.duration / 60)}:${String(tr.duration % 60).padStart(2, '0')}` : ''}</span>
                                  </li>
                                ))}
                              </ol>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* ── Recent Downloads ─────────────────────────────── */}
      {recentDownloads.length > 0 && (
        <div className="w-full max-w-3xl">
          <div className="flex items-center justify-between mb-3 px-1">
            <h3 className="text-sm font-bold text-slate-300">Tải gần đây</h3>
            <button
              onClick={handleClearAllHistory}
              className="text-xs font-semibold text-red-400 hover:text-red-300 transition-colors"
            >
              Xóa tất cả
            </button>
          </div>
          <div className="space-y-2.5">
            {recentDownloads.map((job) => (
              <div key={job.id} className="flex flex-col sm:flex-row items-start sm:items-center gap-3 p-3.5 md:p-4 rounded-2xl bg-[#012622]/50 border border-slate-700/50 backdrop-blur-sm shadow-sm hover:bg-[#012622]/80 transition-colors">
                <div className="flex-1 min-w-0 w-full">
                  <h5 className="text-sm font-bold text-white truncate mb-1">{job.title || job.slugified_name || '...'}</h5>
                  <div className="flex items-center gap-2 text-xs font-medium">
                    {job.status === 'success' ? (
                      <span className="text-[#4ADE80] flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> OK</span>
                    ) : job.status === 'failed' ? (
                      <span className="text-red-400 flex items-center gap-1"><XCircle className="w-3 h-3" /> Lỗi</span>
                    ) : (
                      <span className="text-[#FBBF24] flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> ...</span>
                    )}
                  </div>
                </div>
                <div className="flex gap-2 w-full sm:w-auto">
                  {job.status === 'success' && job.direct_mp4_url ? (
                    <a href={`${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(job.direct_mp4_url)}&filename=${encodeURIComponent(job.title || 'video')}`}
                      className="flex-1 sm:flex-none flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-[#FDE047]/10 text-[#FDE047] font-bold hover:bg-[#FDE047]/20 transition-colors border border-[#FDE047]/30 text-xs">
                      <Download className="w-3.5 h-3.5" /> Tải lại
                    </a>
                  ) : (
                    <button disabled className="flex-1 sm:flex-none px-3 py-2 rounded-xl bg-slate-800 text-slate-500 font-bold cursor-not-allowed border border-slate-700 text-xs">Chờ</button>
                  )}
                  <button onClick={() => handleDeleteJob(job.id)} className="px-2.5 py-2 rounded-xl bg-slate-800 hover:bg-red-500/20 text-slate-400 hover:text-red-400 border border-slate-700 hover:border-red-500/30 transition-colors">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Threads preview (public post / profile) ─────────────────── */}
      {threadsData && (
        <div className="w-full max-w-3xl animate-in fade-in slide-in-from-bottom-2 duration-300">
          {/* Header: platform + source type + author */}
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-black text-white bg-black border border-slate-600">
              <span className="text-base leading-none">@</span> Threads
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-sky-300 bg-sky-500/10 border border-sky-500/20">
              {threadsData.source_type === 'profile' ? <><List className="w-3 h-3" /> Trang cá nhân</> : <><Clapperboard className="w-3 h-3" /> Bài viết</>}
            </span>
            {threadsData.author_handle && (
              <span className="px-3 py-1.5 rounded-xl text-xs font-semibold text-slate-300 bg-slate-800 border border-slate-700">@{threadsData.author_handle}</span>
            )}
          </div>

          {/* Resolved canonical URL + post ID (single post) */}
          {threadsData.source_type !== 'profile' && (threadsData.canonical_url || threadsData.post_id) && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-3 text-[11px] text-slate-500">
              {threadsData.canonical_url && (
                <a href={threadsData.canonical_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-400 hover:underline break-all">
                  <ExternalLink className="w-3 h-3 flex-shrink-0" />{threadsData.canonical_url}
                </a>
              )}
              {threadsData.post_id && <span className="text-slate-500">ID: {threadsData.post_id}</span>}
            </div>
          )}

          {/* Error / unsupported states — honest copy */}
          {threadsData.error_code ? (
            <div className="flex items-start gap-3 px-5 py-4 rounded-2xl bg-amber-500/10 border border-amber-500/30">
              <AlertCircle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-amber-200">
                  {THREADS_ERROR_COPY[threadsData.error_code] || threadsData.error_message || 'Không xử lý được nội dung Threads.'}
                </p>
                <p className="text-xs text-slate-400 mt-1">Chỉ hỗ trợ nội dung Threads công khai.</p>
              </div>
            </div>
          ) : threadsData.source_type === 'profile' ? (
            /* ── Profile: recent public post list ── */
            <div className="bg-slate-900/60 border border-slate-700/50 rounded-2xl p-4">
              <p className="text-xs text-slate-400 mb-3">Bài công khai gần đây — chọn để bóc tách media.</p>
              <div className="flex flex-col gap-2">
                {threadsData.posts?.map((post, i) => (
                  <div key={post.post_id || i} className="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-slate-800/60 border border-slate-700/40 hover:border-slate-600 transition-colors">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-slate-200 truncate">{post.caption_snippet || `Bài viết #${i + 1}`}</p>
                      <p className="text-[11px] text-slate-500 truncate">{post.post_id}{post.has_media ? ' · có media' : ''}</p>
                    </div>
                    {post.has_media && (
                      <span className="px-2 py-0.5 rounded-md text-[10px] font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">MEDIA</span>
                    )}
                    <button onClick={() => handleLoadThreadsPost(post.url)} className="px-3 py-1.5 rounded-lg text-xs font-bold text-[#012622] bg-gradient-to-r from-[#FB923C] to-[#FBBF24] hover:from-[#F97316] hover:to-[#F59E0B] active:scale-95 transition-all flex items-center gap-1.5 whitespace-nowrap">
                      <Zap className="w-3.5 h-3.5" /> Bóc tách
                    </button>
                    <a href={post.url} target="_blank" rel="noreferrer" className="p-1.5 text-slate-500 hover:text-sky-400 transition-colors" title="Mở trên Threads">
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            /* ── Single post: caption + media grid ── */
            <div className="bg-slate-900/60 border border-slate-700/50 rounded-2xl p-4">
              {threadsData.caption && (
                <p className="text-sm text-slate-300 mb-3 whitespace-pre-line line-clamp-4">{threadsData.caption}</p>
              )}
              {threadsData.timestamp && (
                <p className="text-[11px] text-slate-500 mb-3 flex items-center gap-1.5"><Clock className="w-3 h-3" /> {new Date(threadsData.timestamp).toLocaleString('vi-VN')}</p>
              )}
              {threadsData.downloadable && threadsData.media_items?.length ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {threadsData.media_items.map((item, i) => (
                      <div key={i} className="group relative rounded-xl overflow-hidden border border-slate-700/50 bg-slate-800 aspect-square">
                        {item.type === 'image' ? (
                          <img src={item.thumbnail || item.url} alt="" className="w-full h-full object-cover" loading="lazy" />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center bg-slate-800">
                            {item.thumbnail ? <img src={item.thumbnail} alt="" className="w-full h-full object-cover" loading="lazy" /> : <Video className="w-8 h-8 text-slate-500" />}
                            <span className="absolute top-2 left-2 px-2 py-0.5 rounded-md text-[10px] font-bold text-white bg-black/70 flex items-center gap-1"><Play className="w-2.5 h-2.5 fill-white" /> Video</span>
                          </div>
                        )}
                        <button onClick={() => handleThreadsMediaDownload(item, i)} className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/50 transition-colors opacity-0 group-hover:opacity-100" title="Tải media này">
                          <span className="px-3 py-1.5 rounded-lg text-xs font-bold text-[#012622] bg-[#FBBF24] flex items-center gap-1.5">
                            {item.type === 'image' ? <ImageDown className="w-3.5 h-3.5" /> : <Download className="w-3.5 h-3.5" />} Tải
                          </span>
                        </button>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs text-slate-400 mt-3 flex items-center gap-1.5"><CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> Đã tìm thấy {threadsData.media_items.length} media từ bài Threads.</p>
                </>
              ) : (
                <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-800/60 border border-slate-700/40">
                  <AlertCircle className="w-4 h-4 text-slate-400 flex-shrink-0" />
                  <p className="text-sm text-slate-300">Bài viết này không có media tải xuống được.</p>
                </div>
              )}
            </div>
          )}

          <p className="text-[11px] text-slate-500 mt-3 px-1">Một số bài Threads có thể không trích xuất ổn định. Chỉ hỗ trợ nội dung công khai.</p>
        </div>
      )}

      {/* Idle State — platform quickstart hints */}
      {!videoInfo && !spotifyData && !threadsData && recentDownloads.length === 0 && !isLoading && (
        <div className="w-full max-w-3xl mt-2">
          <div className="flex flex-wrap justify-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">
              <Sparkles className="w-3 h-3 flex-shrink-0" /> TikTok / Douyin → bản không watermark
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-red-400 bg-red-500/10 border border-red-500/20">
              <Video className="w-3 h-3 flex-shrink-0" /> YouTube → 4K · 1080p · MP3
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-[#1DB954] bg-[#1DB954]/10 border border-[#1DB954]/20">
              <Music className="w-3 h-3 flex-shrink-0" /> Spotify → MP3 320kbps
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-slate-200 bg-white/5 border border-white/10">
              <span className="text-sm leading-none font-black">@</span> Threads → bài & trang công khai
            </span>
          </div>
        </div>
      )}

    </div>
  );
}

// ── Video Digest Component ──────────────────────────────────────────────────
function DigestCard({ info, onToggle, isVisible }) {
  const dur = info.duration;
  const tags = (info.tags || []).slice(0, 5);
  const fmtDur = dur
    ? `${Math.floor(dur / 3600) > 0 ? Math.floor(dur / 3600) + ':' : ''}${String(Math.floor((dur % 3600) / 60)).padStart(2, '0')}:${String(dur % 60).padStart(2, '0')}`
    : null;

  const QUALITY_BITRATES = [
    { label: '4K', mbps: 20 }, { label: '1080p', mbps: 8 }, { label: '720p', mbps: 4 },
    { label: '480p', mbps: 1.5 }, { label: 'Audio', mbps: 0.128 },
  ];

  function estSize(durSec, bps) {
    const mb = durSec * bps * 0.125;
    if (mb >= 1024) return `~${(mb / 1024).toFixed(1)} GB`;
    return `~${Math.round(mb)} MB`;
  }

  function fmtViews(n) {
    if (!n) return null;
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
    return String(n);
  }

  function fmtDate(iso) {
    if (!iso) return null;
    try { return new Date(iso).toLocaleDateString('vi-VN'); } catch { return iso; }
  }

  return (
    <div className="rounded-2xl border border-slate-700/50 bg-slate-900/40 overflow-hidden">
      {/* Header toggle */}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-800/50 hover:bg-slate-800/80 transition-colors cursor-pointer"
      >
        <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">Thong tin video</span>
        <span className="text-[10px] text-slate-500">{isVisible ? '▲ Thu gọn' : '▼ Mở rộng'}</span>
      </button>

      {isVisible && (
        <div className="p-4 space-y-4">
          {/* Thumbnail */}
          {info.thumbnail_url && (
            <img
              src={info.thumbnail_url}
              alt=""
              className="w-full rounded-xl object-cover aspect-video bg-slate-800"
              loading="lazy"
            />
          )}

          {/* Title */}
          <div>
            <p className="text-white font-bold text-sm leading-snug">{info.title || 'Không có tiêu đề'}</p>
          </div>

          {/* Creator + Meta row */}
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
            {(info.uploader || info.channel_handle) && (
              <span className="text-slate-300 font-semibold">
                @{info.channel_handle || info.uploader}
                {info.uploader && info.channel_handle && info.uploader !== info.channel_handle && (
                  <span className="text-slate-500 font-normal ml-1">({info.uploader})</span>
                )}
              </span>
            )}
            {fmtDur && (
              <span className="flex items-center gap-1">⏱ {fmtDur}</span>
            )}
            {info.upload_date_iso && (
              <span>📅 {fmtDate(info.upload_date_iso)}</span>
            )}
            {info.view_count && (
              <span>👁 {fmtViews(info.view_count)} lượt xem</span>
            )}
            {info.language && (
              <span>🌐 {info.language.toUpperCase()}</span>
            )}
          </div>

          {/* Hashtags */}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {tags.map(tag => (
                <span key={tag} className="px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-[10px] text-slate-400">
                  #{tag}
                </span>
              ))}
            </div>
          )}

          {/* Indicators */}
          <div className="flex gap-2 flex-wrap">
            {info.has_subtitles ? (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold border bg-emerald-500/10 border-emerald-500/30 text-emerald-400" title={
                info.available_subtitle_languages?.length
                  ? `Ngôn ngữ: ${info.available_subtitle_languages.slice(0, 6).join(', ')}${info.available_subtitle_languages.length > 6 ? '…' : ''}`
                  : 'Có phụ đề'
              }>
                {info.subtitle_source === 'auto' ? '✓ Phụ đề tự động' : '✓ Có phụ đề'}
                {info.available_subtitle_languages?.length > 0 && (
                  <span className="ml-1 opacity-70">({info.available_subtitle_languages.slice(0, 3).join(', ')}{info.available_subtitle_languages.length > 3 ? '…' : ''})</span>
                )}
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold border bg-slate-800 border-slate-700 text-slate-500">
                ✕ Không có phụ đề
              </span>
            )}
            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold border bg-[#FBBF24]/10 border-[#FBBF24]/30 text-[#FBBF24]">
              ✓ Có thể tải
            </span>
          </div>

          {/* Estimated sizes */}
          {dur > 0 && (
            <div>
              <p className="text-[10px] text-slate-500 uppercase font-semibold mb-1.5 tracking-wider">Kích thước ước tính</p>
              <div className="grid grid-cols-3 gap-1.5">
                {QUALITY_BITRATES.map(({ label, mbps }) => (
                  <div key={label} className="flex justify-between items-center px-2 py-1 rounded-lg bg-slate-800/60 text-[10px]">
                    <span className="text-slate-400 font-semibold">{label}</span>
                    <span className="text-slate-300">{estSize(dur, mbps)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

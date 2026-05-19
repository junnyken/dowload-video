import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Download, CheckCircle2, XCircle,
  Loader2, AlertCircle, Link2,
  Zap, Music, Video, Crown, Trash2, Clock, X,
  ClipboardPaste, Play, Pause, Scissors, ImageDown,
  Upload, ExternalLink, SkipBack, SkipForward,
  Clapperboard, List, Sparkles
} from 'lucide-react';
import UpgradeModal from './UpgradeModal';
import JSZip from 'jszip';
import { saveAs } from 'file-saver';

const API_BASE = import.meta.env.VITE_API_URL || '';

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
  const [url, setUrl] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [videoInfo, setVideoInfo] = useState(null);
  const [spotifyData, setSpotifyData] = useState(null); // playlist / album track list
  const [trackDownloads, setTrackDownloads] = useState({}); // { search_query: 'loading' | 'done' | 'error' }
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [downloadingId, setDownloadingId] = useState(null);
  const [formatTab, setFormatTab] = useState('video');
  const [recentDownloads, setRecentDownloads] = useState([]);
  const [toastMessage, setToastMessage] = useState('');
  const [isZipping, setIsZipping] = useState(false);
  const [zipProgress, setZipProgress] = useState(0);
  const [removeWatermark, setRemoveWatermark] = useState(true);
  const [downloadSubs, setDownloadSubs] = useState(false);
  const [selectedTracks, setSelectedTracks] = useState(new Set());
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
    if (cleanUrl !== pasted.trim()) showToast('Đã tự động trích xuất URL!');
  };

  const isSpotifyPlaylistOrAlbum = (u) =>
    u.includes('open.spotify.com/playlist') || u.includes('open.spotify.com/album');

  const isSpotifyTrack = (u) => u.includes('open.spotify.com/track');

  const handleFetchLink = async () => {
    if (!url.trim()) { setError('Vui lòng nhập liên kết hợp lệ.'); return; }
    setIsLoading(true); setError(''); setVideoInfo(null); setSpotifyData(null); setFormatTab('video');

    // Route Spotify playlist / album to dedicated endpoint
    if (isSpotifyPlaylistOrAlbum(url.trim())) {
      try {
        const response = await fetch(`${API_BASE}/api/v1/fetch-spotify`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url.trim() }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Không thể tải danh sách nhạc Spotify');
        if (data.success) {
          setSpotifyData(data);
          setTrackDownloads({});
          showToast(`Tìm thấy ${data.tracks?.length || 0} bài nhạc!`);
        } else throw new Error('Không thể tải danh sách nhạc.');
      } catch (err) {
        setError(err.message || 'Lỗi khi tải danh sách Spotify.');
      } finally { setIsLoading(false); }
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/api/v1/fetch-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim(), quality: 'video', remove_watermark: removeWatermark, download_subs: downloadSubs }),
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('⏳ Bạn đã gửi quá nhiều yêu cầu. Vui lòng chờ 1 phút rồi thử lại.');
        }
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setShowUpgradeModal(true);
          throw new Error('Đã đạt giới hạn tải. Vui lòng nâng cấp VIP.');
        }
        throw new Error(data.detail || 'Không thể lấy thông tin video');
      }
      if (data.success) {
        setVideoInfo(data);
        showToast('Trích xuất thành công!');
        if (data.subtitle_url) {
           const a = document.createElement('a');
           a.href = data.subtitle_url; a.setAttribute('download', '');
           document.body.appendChild(a); a.click(); document.body.removeChild(a);
           showToast('Đang tải phụ đề...');
        }
      } else throw new Error('Không thể trích xuất thông tin video.');
    } catch (err) {
      setError(err.message || 'Đã xảy ra lỗi khi xử lý link.');
    } finally { setIsLoading(false); }
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
      const data = await response.json();
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

  const handleDownloadAllZip = async () => {
    if (!spotifyData?.tracks) return;

    const allTracks = spotifyData.tracks;
    const tracksToDownload = selectedTracks.size > 0
      ? allTracks.filter(t => selectedTracks.has(t.search_query))
      : allTracks;
    if (tracksToDownload.length === 0) return;

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
          body: JSON.stringify({ url: key, quality: 'mp3_128' }),
        });
        const data = await res.json();
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
        const safePlaylistName = (spotifyData.playlist_name || spotifyData.album_name || 'Playlist').replace(/[/\\?%*:|"<>]/g, '-');
        saveAs(content, `${safePlaylistName}.zip`);
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

  const handleCancelZip = () => {
    cancelZipRef.current = true;
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

    setDownloadingId(`merge_${param || '4k'}`);
    try {
      const response = await fetch(`${API_BASE}/api/v1/fetch-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: videoInfo.original_url || url.trim(),
          quality: qualityReq,
          remove_watermark: !isAudioParam,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 429) {
          throw new Error('⏳ Quá nhiều yêu cầu. Vui lòng chờ 1 phút rồi thử lại.');
        }
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setShowUpgradeModal(true); return;
        }
        throw new Error(data.detail || 'Không thể xử lý ghép tệp video');
      }
      if (data.success) {
        // Determine file path and extension from the actual file
        const dlPath = data.local_mp3_path || data.local_file_path;
        if (dlPath) {
          const fileExt = dlPath.split('.').pop() || (isAudioParam ? 'mp3' : 'mp4');
          const downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(dlPath)}&filename=${encodeURIComponent(data.title || 'video')}.${fileExt}`;
          const a = document.createElement('a');
          a.href = downloadUrl; a.setAttribute('download', '');
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          showToast('Bắt đầu tải về!');
        } else if (data.direct_mp4_url) {
          handleFormatDownload({ url: data.direct_mp4_url, ext: isAudioParam ? 'mp3' : 'mp4' });
        }
      }
    } catch (err) {
      setError(err.message || 'Đã xảy ra lỗi khi xử lý ghép tệp.');
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
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
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
      const data = await response.json();
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
      const data = await res.json();
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
      const data = await res.json();
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
      <UpgradeModal isOpen={showUpgradeModal} onClose={() => setShowUpgradeModal(false)} />

      {/* ── Input Area ──────────────────────────────────── */}
      <div className="relative group w-full max-w-3xl mb-10">
        <div className="absolute -inset-2 bg-gradient-to-r from-[#FDE047]/30 to-[#4ADE80]/30 rounded-[2rem] md:rounded-full blur-xl opacity-60 group-hover:opacity-100 transition duration-500" />
        <div className="relative bg-white flex flex-col md:flex-row items-center p-2.5 rounded-3xl md:rounded-full shadow-2xl shadow-[#FDE047]/10 border border-slate-100/80">
          <div className="flex-1 flex items-center gap-3 w-full px-5 md:px-6">
            <Link2 className="w-6 h-6 text-slate-600 flex-shrink-0" />
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onPaste={handleInputPaste}
              onKeyDown={(e) => e.key === 'Enter' && handleFetchLink()}
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
            className="w-full md:w-auto mt-3 md:mt-0 h-14 md:h-16 px-6 sm:px-10 rounded-2xl md:rounded-full bg-gradient-to-r from-[#FB923C] to-[#FBBF24] hover:from-[#F97316] hover:to-[#F59E0B] text-[#012622] font-black shadow-2xl shadow-[#FBBF24]/40 active:scale-95 transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2.5 whitespace-nowrap text-base md:text-lg uppercase tracking-wider drop-shadow-md"
          >
            {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-6 h-6 fill-[#012622]" />}
            <span>{isLoading ? 'ĐANG XỬ LÝ...' : 'BÓC TÁCH NGAY'}</span>
          </button>
        </div>
      </div>

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

      <div className="w-full max-w-3xl mb-8 flex flex-col sm:flex-row justify-center items-center gap-4 sm:gap-8 text-sm text-slate-300">
         <label className="flex items-center gap-2 cursor-pointer hover:text-white transition-colors">
            <input type="checkbox" checked={removeWatermark} onChange={e => setRemoveWatermark(e.target.checked)} className="w-4 h-4 accent-emerald-500 bg-slate-800 border-slate-600 rounded"/>
            <span className="font-medium">Xoá Logo (TikTok / Douyin)</span>
         </label>
         <label className="flex items-center gap-2 cursor-pointer hover:text-white transition-colors">
            <input type="checkbox" checked={downloadSubs} onChange={e => setDownloadSubs(e.target.checked)} className="w-4 h-4 accent-emerald-500 bg-slate-800 border-slate-600 rounded"/>
            <span className="font-medium">Tải Phụ Đề (.srt) nếu có</span>
         </label>
      </div>

      {/* ── Error ────────────────────────────────────────── */}
      {error && (
        <div className="max-w-2xl mx-auto mb-8 w-full px-4">
          <div className="flex items-center justify-center gap-3 text-red-400 bg-red-500/10 px-5 py-3 rounded-2xl border border-red-500/20 shadow-sm backdrop-blur-md">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            <p className="text-sm font-bold">{error}</p>
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
                              {displaySize > 0 && <p className="text-xs text-slate-400 mt-0.5">{displaySize.toFixed(1)} MB{isAlreadyDownloaded ? ' (đã tải)' : ''}</p>}
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
                    <span className="text-base">Tải Video (Không logo)</span>
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

            {/* Cancel/Reset */}
            <div className="text-center mt-6">
              <button onClick={() => setVideoInfo(null)} className="px-8 py-2.5 rounded-full text-slate-400 font-semibold hover:text-white hover:bg-white/10 transition-colors text-sm border border-transparent hover:border-slate-700">
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

      {/* Empty State */}
      {!videoInfo && !spotifyData && recentDownloads.length === 0 && !isLoading && (
        <div className="w-full max-w-3xl mt-8">
          <div className="flex flex-col items-center justify-center py-16 px-6 bg-[#012622]/50 border border-slate-700/50 backdrop-blur-md rounded-[2rem] shadow-sm text-center">
            <div className="w-16 h-16 rounded-full bg-[#012622] flex items-center justify-center mb-5 border border-slate-700/50">
              <Clock className="w-8 h-8 text-slate-400" />
            </div>
            <p className="text-slate-300 font-medium text-base">Dán link và bấm "Bóc tách ngay" để bắt đầu!</p>
          </div>
        </div>
      )}
    </div>
  );
}

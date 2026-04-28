import { useState, useEffect } from 'react';
import {
  Download, CheckCircle2, XCircle,
  Loader2, AlertCircle, Link2,
  Zap, Music, Video, Crown, Trash2, Clock
} from 'lucide-react';
import UpgradeModal from './UpgradeModal';

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
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [downloadingId, setDownloadingId] = useState(null);
  const [formatTab, setFormatTab] = useState('video');
  const [recentDownloads, setRecentDownloads] = useState([]);
  const [toastMessage, setToastMessage] = useState('');

  const showToast = (msg) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(''), 3000);
  };

  useEffect(() => {
    fetch('http://127.0.0.1:8000/api/v1/history?limit=5')
      .then(res => res.json())
      .then(data => {
        if (data.success && data.jobs) setRecentDownloads(data.jobs);
      }).catch(() => {});
  }, []);

  const handleDeleteJob = async (jobId) => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/v1/history/${jobId}`, { method: 'DELETE' });
      if (res.ok) {
        setRecentDownloads(prev => prev.filter(j => j.id !== jobId));
        showToast('Đã xóa thành công!');
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

  const handleFetchLink = async () => {
    if (!url.trim()) { setError('Vui lòng nhập liên kết hợp lệ.'); return; }
    setIsLoading(true); setError(''); setVideoInfo(null); setFormatTab('video');
    try {
      const response = await fetch('http://127.0.0.1:8000/api/v1/fetch-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim(), quality: 'video', remove_watermark: true }),
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setShowUpgradeModal(true);
          throw new Error('Đã đạt giới hạn tải. Vui lòng nâng cấp VIP.');
        }
        throw new Error(data.detail || 'Không thể lấy thông tin video');
      }
      if (data.success) {
        setVideoInfo(data);
        showToast('Trích xuất thành công!');
      } else throw new Error('Không thể trích xuất thông tin video.');
    } catch (err) {
      setError(err.message || 'Đã xảy ra lỗi khi xử lý link.');
    } finally { setIsLoading(false); }
  };

  // Direct download a format via proxy
  const handleFormatDownload = (fmt) => {
    const title = videoInfo?.title || 'video';
    const ext = fmt.ext || 'mp4';
    const downloadUrl = `http://127.0.0.1:8000/api/v1/proxy-download?url=${encodeURIComponent(fmt.url)}&filename=${encodeURIComponent(title)}&ext=${encodeURIComponent(ext)}`;
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
      const downloadUrl = `http://127.0.0.1:8000/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=${encodeURIComponent(videoInfo.title || 'video')}.${fileExt}`;
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
  const handleMergeDownload = async (param = null) => {
    let qualityReq = 'video_4k';
    const isAudioParam = param === 'm4a' || param === 'webm' || param === 'mp3' || param === 'ogg';
    if (isAudioParam) {
      qualityReq = param === 'mp3' ? 'mp3_320' : param === 'ogg' ? 'mp3_128' : `audio_${param}`;
    } else if (param) {
      qualityReq = `video_${param}`;
    }
    setDownloadingId(`merge_${param || '4k'}`);
    try {
      const response = await fetch('http://127.0.0.1:8000/api/v1/fetch-link', {
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
        if (response.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setShowUpgradeModal(true); return;
        }
        throw new Error(data.detail || 'Không thể xử lý ghép tệp video');
      }
      if (data.success) {
        // Determine file path and extension from the actual file
        const localPath = data.local_mp3_path || data.local_file_path;
        if (localPath) {
          const fileExt = localPath.split('.').pop() || (isAudioParam ? 'mp3' : 'mp4');
          const downloadUrl = `http://127.0.0.1:8000/api/v1/download-local?filepath=${encodeURIComponent(localPath)}&filename=${encodeURIComponent(data.title || 'video')}.${fileExt}`;
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
      const response = await fetch('http://127.0.0.1:8000/api/v1/fetch-link', {
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
          const downloadUrl = `http://127.0.0.1:8000/api/v1/download-local?filepath=${encodeURIComponent(path)}&filename=${encodeURIComponent(data.title || 'audio')}.mp3`;
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

  // Split formats
  const videoFormats = (videoInfo?.available_formats || []).filter(f => f.type === 'video');
  const audioFormats = (videoInfo?.available_formats || []).filter(f => f.type === 'audio');
  const hasFormats = videoFormats.length > 0 || audioFormats.length > 0;
  const maxMergeHeight = videoInfo?.max_merge_height || 0;
  const maxCombinedHeight = videoFormats.length > 0 ? Math.max(...videoFormats.map(f => f.height)) : 0;
  const showMergeOption = maxMergeHeight > maxCombinedHeight;

  return (
    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <Toast message={toastMessage} show={!!toastMessage} />
      <UpgradeModal isOpen={showUpgradeModal} onClose={() => setShowUpgradeModal(false)} />

      {/* ── Input Area ──────────────────────────────────── */}
      <div className="relative group" style={{ width: '100%', maxWidth: '768px', marginBottom: '40px' }}>
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
              className="w-full bg-transparent text-slate-900 placeholder-slate-500 text-base md:text-lg font-semibold focus:outline-none disabled:opacity-50 h-14"
            />
          </div>
          <button
            onClick={handleFetchLink}
            disabled={isLoading || !url.trim()}
            className="w-full md:w-auto mt-3 md:mt-0 h-14 md:h-16 px-10 rounded-2xl md:rounded-full bg-gradient-to-r from-[#FB923C] to-[#FBBF24] hover:from-[#F97316] hover:to-[#F59E0B] text-[#012622] font-black shadow-2xl shadow-[#FBBF24]/40 active:scale-95 transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2.5 whitespace-nowrap text-base md:text-lg uppercase tracking-wider drop-shadow-md"
          >
            {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-6 h-6 fill-[#012622]" />}
            <span>{isLoading ? 'ĐANG XỬ LÝ...' : 'BÓC TÁCH NGAY'}</span>
          </button>
        </div>
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
        <div style={{ width: '100%', maxWidth: '768px', marginBottom: '48px' }}>
          <div className="bg-[#012622]/50 border border-slate-700/50 backdrop-blur-md rounded-3xl p-6 md:p-8 shadow-2xl shadow-[#FDE047]/5 overflow-hidden">

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
                      {videoFormats.map((fmt, i) => (
                        <button
                          key={`v-${i}`}
                          onClick={() => fmt.requires_merge ? handleMergeDownload(fmt.height) : handleFormatDownload(fmt)}
                          disabled={downloadingId === `merge_${fmt.height}`}
                          className="w-full flex items-center justify-between px-5 py-3.5 rounded-2xl bg-slate-800/50 border border-slate-700/40 hover:border-emerald-500/50 hover:bg-slate-800/80 text-white transition-all disabled:opacity-60 active:scale-[0.99] group"
                        >
                          <div className="flex items-center gap-3">
                            <Video className="w-5 h-5 text-emerald-400" />
                            <div className="text-left">
                              <div className="flex items-center gap-2">
                                <ResBadge label={fmt.label} height={fmt.height} />
                                <span className="text-sm font-bold">{fmt.resolution}</span>
                                <span className="text-xs text-slate-500 uppercase">{fmt.ext}</span>
                                {fmt.requires_merge && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">
                                    GHÉP TỆP
                                  </span>
                                )}
                              </div>
                              {fmt.filesize_mb > 0 && <p className="text-xs text-slate-400 mt-0.5">{fmt.filesize_mb.toFixed(1)} MB</p>}
                            </div>
                          </div>
                          {downloadingId === `merge_${fmt.height}` ? (
                            <Loader2 className="w-5 h-5 animate-spin text-emerald-400" />
                          ) : (
                            <Download className="w-5 h-5 text-emerald-400 group-hover:scale-110 transition-transform" />
                          )}
                        </button>
                      ))}

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

      {/* ── Recent Downloads ─────────────────────────────── */}
      {recentDownloads.length > 0 && (
        <div style={{ width: '100%', maxWidth: '768px' }}>
          <h3 className="text-sm font-bold text-slate-300 mb-3 px-1">Tải gần đây</h3>
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
                    <a href={`http://127.0.0.1:8000/api/v1/proxy-download?url=${encodeURIComponent(job.direct_mp4_url)}&filename=${encodeURIComponent(job.title || 'video')}`}
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
      {!videoInfo && recentDownloads.length === 0 && !isLoading && (
        <div style={{ width: '100%', maxWidth: '768px', marginTop: '32px' }}>
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

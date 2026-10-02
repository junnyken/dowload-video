import { useState, useEffect } from 'react';
import { X } from 'lucide-react';

const TWO_HOURS = 2 * 60 * 60 * 1000;

const tools = [
  {
    id: 'trim',
    emoji: '✂️',
    label: 'Cắt clip',
    desc: 'Trim video nhanh',
    event: 'vidgrab:open-tool:trim',
  },
  {
    id: 'audio',
    emoji: '🎵',
    label: 'Trích MP3',
    desc: 'Xuất âm thanh',
    event: 'vidgrab:open-tool:audio',
  },
  {
    id: 'gif',
    emoji: '🎞️',
    label: 'Tạo GIF',
    desc: 'Xuất ảnh động',
    event: 'vidgrab:open-tool:gif',
  },
  {
    id: 'subtitle',
    emoji: '📝',
    label: 'Phụ đề',
    desc: 'Tải hoặc đốt phụ đề',
    event: 'vidgrab:open-tool:subtitle',
  },
  {
    id: 'cloud',
    emoji: '☁️',
    label: 'Lưu Cloud',
    hidden: true, // temporarily hidden, same as the result-card button
    desc: 'Drive/Dropbox',
    event: 'vidgrab:open-tool:cloud',
  },
  {
    id: 'full',
    emoji: '🖥️',
    label: 'Bản đầy đủ',
    desc: 'Mở tất cả công cụ',
    event: null,
  },
];

function Toast({ message, onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, 2500);
    return () => clearTimeout(t);
  }, [onDone]);

  return (
    <div className="fixed bottom-28 left-1/2 -translate-x-1/2 z-[80] px-4 py-2.5 rounded-2xl bg-surface backdrop-blur text-fg text-sm font-medium shadow-xl border border-line-strong whitespace-nowrap animate-fade-in">
      {message}
    </div>
  );
}

export default function MobileQuickTools({ show, onClose, onNavigate }) {
  const [visible, setVisible]       = useState(false);
  const [toast, setToast]           = useState('');
  const [recentDownload, setRecentDownload] = useState(null);

  // Sync animation state with show prop
  useEffect(() => {
    if (show) {
      setVisible(false);
      // Read recent download from localStorage
      try {
        const raw = localStorage.getItem('vg_last_download');
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed.timestamp && Date.now() - parsed.timestamp < TWO_HOURS) {
            setRecentDownload(parsed);
          } else {
            setRecentDownload(null);
          }
        } else {
          setRecentDownload(null);
        }
      } catch {
        setRecentDownload(null);
      }
      const t = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(t);
    } else {
      setVisible(false);
    }
  }, [show]);

  const handleClose = () => {
    setVisible(false);
    setTimeout(onClose, 300);
  };

  const handleTool = (tool) => {
    if (!recentDownload && tool.id !== 'full') {
      setToast('Tải video trước để dùng công cụ này');
      return;
    }

    handleClose();

    if (tool.id === 'full' || !tool.event) {
      if (onNavigate) onNavigate('landing', '/');
      return;
    }

    window.dispatchEvent(new CustomEvent(tool.event));
    if (onNavigate) onNavigate('landing', '/');
  };

  if (!show) return null;

  return (
    <>
      {/* Toast */}
      {toast && (
        <Toast message={toast} onDone={() => setToast('')} />
      )}

      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-[72] bg-black/60 transition-opacity duration-300 ${
          visible ? 'opacity-100' : 'opacity-0'
        }`}
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* Bottom sheet */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Công cụ nhanh"
        className={`fixed inset-x-0 bottom-0 z-[73] bg-surface/98 backdrop-blur-xl rounded-t-3xl shadow-[0_-8px_40px_rgba(0,0,0,0.4)] transition-transform duration-300 ease-out ${
          visible ? 'translate-y-0' : 'translate-y-full'
        }`}
      >
        {/* Drag handle */}
        <div className="flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full bg-line" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-line">
          <div>
            <h2 className="text-fg font-semibold text-base">Công cụ nhanh</h2>
            <p className="text-fg-muted text-xs mt-0.5">Chọn công cụ để xử lý video vừa tải</p>
          </div>
          <button
            onClick={handleClose}
            className="w-8 h-8 flex items-center justify-center rounded-full bg-surface-2 text-fg-muted hover:text-fg hover:bg-line transition-colors"
            aria-label="Đóng"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Recent download hint */}
        {recentDownload && (
          <div className="mx-5 mt-3 px-3 py-2 rounded-xl bg-accent-soft border border-accent/20 flex items-center gap-2">
            <span className="text-accent-text text-lg">⬇️</span>
            <div className="flex-1 min-w-0">
              <p className="text-accent-text text-xs font-semibold truncate">
                {recentDownload.title || 'Video vừa tải'}
              </p>
              <p className="text-fg-muted text-[10px] truncate">{recentDownload.url}</p>
            </div>
          </div>
        )}

        {/* Tool grid */}
        <div className="px-5 pt-4 pb-8 grid grid-cols-3 gap-3">
          {tools.filter((tool) => !tool.hidden).map((tool) => (
            <button
              key={tool.id}
              onClick={() => handleTool(tool)}
              className="flex flex-col items-center gap-2 py-4 px-2 rounded-2xl bg-surface-2/60 border border-line hover:bg-accent-soft hover:border-accent/30 transition-all duration-200 active:scale-95 group"
            >
              <span className="text-2xl">{tool.emoji}</span>
              <div className="text-center">
                <p className="text-fg text-xs font-semibold group-hover:text-accent-text transition-colors">
                  {tool.label}
                </p>
                <p className="text-fg-muted text-[10px] mt-0.5 leading-tight">{tool.desc}</p>
              </div>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

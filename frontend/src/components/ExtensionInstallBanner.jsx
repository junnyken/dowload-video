import { useState, useEffect } from 'react';
import { X, Zap } from 'lucide-react';

const DISMISS_KEY = 'ext-banner-dismissed-at';
const COOLDOWN_MS = 14 * 24 * 60 * 60 * 1000;

function isDesktop() {
  return window.innerWidth > 768 && !('ontouchstart' in window);
}

function hasExtension() {
  return !!(window.vidgrab_ext_version || window.__VIDGRAB_EXT__);
}

function hasDownloaded() {
  return parseInt(localStorage.getItem('vg_download_count') || '0', 10) >= 1;
}

export default function ExtensionInstallBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!isDesktop()) return;
    if (hasExtension()) return;
    if (!hasDownloaded()) return;
    const dismissedAt = parseInt(localStorage.getItem(DISMISS_KEY) || '0', 10);
    if (Date.now() - dismissedAt < COOLDOWN_MS) return;
    const t = setTimeout(() => setVisible(true), 2500);
    return () => clearTimeout(t);
  }, []);

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div className="fixed top-16 right-4 z-30 w-72">
      <div className="bg-[#0d2e29] border border-white/15 rounded-xl p-3.5 shadow-xl shadow-black/30 relative">
        <button
          onClick={dismiss}
          className="absolute top-2.5 right-2.5 text-white/30 hover:text-white/60 transition-colors"
          aria-label="Đóng"
        >
          <X className="w-3.5 h-3.5" />
        </button>
        <div className="flex items-center gap-2.5 pr-5">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/20 flex items-center justify-center flex-shrink-0">
            <Zap className="w-4 h-4 text-indigo-400" />
          </div>
          <div>
            <p className="text-xs font-semibold text-white">Cài extension để tải 1 chạm</p>
            <p className="text-[11px] text-white/40 mt-0.5">Không cần mở tab mới</p>
          </div>
        </div>
        <div className="flex gap-2 mt-3">
          <button
            onClick={() => {
              window.history.pushState({}, '', '/install');
              window.dispatchEvent(new PopStateEvent('popstate'));
              dismiss();
            }}
            className="flex-1 text-center text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white py-1.5 rounded-lg transition-colors"
          >
            Xem hướng dẫn
          </button>
          <button
            onClick={dismiss}
            className="text-xs text-white/30 hover:text-white/50 px-2 transition-colors"
          >
            Bỏ qua
          </button>
        </div>
      </div>
    </div>
  );
}

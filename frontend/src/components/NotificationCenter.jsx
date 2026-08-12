import { useState, useEffect, useRef } from 'react';
import {
  Bell,
  BellRing,
  X,
  Download,
  AlertCircle,
  Package,
  Clock,
  HardDrive,
} from 'lucide-react';
import { useNotifications } from '../context/NotificationContext';

function relativeTime(isoString) {
  const diffMs = Date.now() - new Date(isoString).getTime();
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return 'vừa xong';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} phút trước`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} giờ trước`;
  return `${Math.floor(h / 24)} ngày trước`;
}

const TYPE_ICON = {
  download_done: <Download className="w-4 h-4 text-green-400" />,
  download_failed: <AlertCircle className="w-4 h-4 text-red-400" />,
  batch_done: <Package className="w-4 h-4 text-indigo-400" />,
  job_expired: <Clock className="w-4 h-4 text-yellow-400" />,
  storage_warning: <HardDrive className="w-4 h-4 text-orange-400" />,
};

export default function NotificationCenter() {
  const { notifications, unreadCount, markAllRead, clearAll } = useNotifications();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  useEffect(() => {
    if (open && unreadCount > 0) markAllRead();
  }, [open, unreadCount, markAllRead]);

  const navigate = (url) => {
    if (url) {
      window.history.pushState({}, '', url);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
        aria-label="Thông báo"
      >
        {unreadCount > 0 ? (
          <BellRing className="w-5 h-5" />
        ) : (
          <Bell className="w-5 h-5" />
        )}
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 w-4 h-4 bg-red-500 rounded-full text-[10px] font-bold text-white flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 max-h-[420px] flex flex-col bg-[#0a2e2a] border border-white/10 rounded-xl shadow-2xl z-50">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 flex-shrink-0">
            <span className="font-semibold text-white text-sm">Thông báo</span>
            <div className="flex items-center gap-3">
              {notifications.length > 0 && (
                <button
                  onClick={clearAll}
                  className="text-[11px] text-white/40 hover:text-white/70 transition-colors"
                >
                  Xóa tất cả
                </button>
              )}
              <button
                onClick={() => setOpen(false)}
                className="text-white/40 hover:text-white/70 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* List */}
          <div className="overflow-y-auto flex-1 divide-y divide-white/5">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-white/30">
                <Bell className="w-8 h-8 mb-2 opacity-40" />
                <p className="text-sm">Chưa có thông báo nào</p>
              </div>
            ) : (
              notifications.slice(0, 30).map((n) => (
                <button
                  key={n.id}
                  onClick={() => navigate(n.url)}
                  className="w-full text-left px-4 py-3 hover:bg-white/5 transition-colors flex gap-3 items-start"
                >
                  <span className="flex-shrink-0 mt-0.5">
                    {TYPE_ICON[n.type] || <Bell className="w-4 h-4 text-white/40" />}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p
                      className={`text-sm font-medium truncate ${
                        n.read ? 'text-white/60' : 'text-white'
                      }`}
                    >
                      {n.title}
                    </p>
                    <p className="text-xs text-white/40 truncate mt-0.5">{n.body}</p>
                    <p className="text-[10px] text-white/25 mt-1">{relativeTime(n.createdAt)}</p>
                  </div>
                  {!n.read && (
                    <span className="w-2 h-2 rounded-full bg-indigo-500 flex-shrink-0 mt-1.5" />
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

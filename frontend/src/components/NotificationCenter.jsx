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
  download_done: <Download className="w-4 h-4 text-success" />,
  download_failed: <AlertCircle className="w-4 h-4 text-danger" />,
  batch_done: <Package className="w-4 h-4 text-fg-2" />,
  job_expired: <Clock className="w-4 h-4 text-warning" />,
  storage_warning: <HardDrive className="w-4 h-4 text-warning" />,
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
        className="relative p-2 rounded-lg text-fg-2 hover:text-fg hover:bg-surface-2 transition-colors"
        aria-label="Thông báo"
      >
        {unreadCount > 0 ? (
          <BellRing className="w-5 h-5" />
        ) : (
          <Bell className="w-5 h-5" />
        )}
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 w-4 h-4 bg-danger rounded-full text-[10px] font-bold text-danger-fg flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 max-h-[420px] flex flex-col bg-surface-2 border border-line rounded-xl shadow-2xl z-50">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-line flex-shrink-0">
            <span className="font-semibold text-fg text-sm">Thông báo</span>
            <div className="flex items-center gap-3">
              {notifications.length > 0 && (
                <button
                  onClick={clearAll}
                  className="text-[11px] text-fg-muted hover:text-fg-2 transition-colors"
                >
                  Xóa tất cả
                </button>
              )}
              <button
                onClick={() => setOpen(false)}
                className="text-fg-muted hover:text-fg-2 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* List */}
          <div className="overflow-y-auto flex-1 divide-y divide-line">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-fg-muted">
                <Bell className="w-8 h-8 mb-2 opacity-40" />
                <p className="text-sm">Chưa có thông báo nào</p>
              </div>
            ) : (
              notifications.slice(0, 30).map((n) => (
                <button
                  key={n.id}
                  onClick={() => navigate(n.url)}
                  className="w-full text-left px-4 py-3 hover:bg-surface-2 transition-colors flex gap-3 items-start"
                >
                  <span className="flex-shrink-0 mt-0.5">
                    {TYPE_ICON[n.type] || <Bell className="w-4 h-4 text-fg-muted" />}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p
                      className={`text-sm font-medium truncate ${
                        n.read ? 'text-fg-2' : 'text-fg'
                      }`}
                    >
                      {n.title}
                    </p>
                    <p className="text-xs text-fg-muted truncate mt-0.5">{n.body}</p>
                    <p className="text-[10px] text-fg-muted mt-1">{relativeTime(n.createdAt)}</p>
                  </div>
                  {!n.read && (
                    <span className="w-2 h-2 rounded-full bg-accent flex-shrink-0 mt-1.5" />
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

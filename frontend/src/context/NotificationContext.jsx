import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'vg_notifications';
const MAX_NOTIFICATIONS = 50;
const EXPIRE_MS = 7 * 24 * 60 * 60 * 1000;

function generateId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function loadFromStorage() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveToStorage(items) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {}
}

const NotificationContext = createContext(null);

export function NotificationProvider({ children }) {
  const [notifications, setNotifications] = useState(() => {
    const all = loadFromStorage();
    const cutoff = Date.now() - EXPIRE_MS;
    return all.filter((n) => new Date(n.createdAt).getTime() > cutoff);
  });

  useEffect(() => {
    saveToStorage(notifications);
  }, [notifications]);

  const unreadCount = notifications.filter((n) => !n.read).length;

  const addNotification = useCallback(({ type, title, body, url = null }) => {
    setNotifications((prev) => {
      const cutoff = Date.now() - 30000;
      const isDupe = prev.some(
        (n) => n.title === title && new Date(n.createdAt).getTime() > cutoff
      );
      if (isDupe) return prev;
      const next = [
        {
          id: generateId(),
          type,
          title,
          body,
          url,
          read: false,
          createdAt: new Date().toISOString(),
        },
        ...prev,
      ].slice(0, MAX_NOTIFICATIONS);
      return next;
    });
  }, []);

  const markRead = useCallback((id) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
  }, []);

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const clearAll = useCallback(() => setNotifications([]), []);

  return (
    <NotificationContext.Provider
      value={{ notifications, unreadCount, addNotification, markRead, markAllRead, clearAll }}
    >
      {children}
    </NotificationContext.Provider>
  );
}

export function useNotifications() {
  const ctx = useContext(NotificationContext);
  if (!ctx) throw new Error('useNotifications must be inside NotificationProvider');
  return ctx;
}

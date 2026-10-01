import { useCallback, useEffect, useState } from 'react';

// Theme lives on <html data-theme="light|dark">. index.html sets it before
// first paint; this hook only reads/updates it. Storage can throw (private
// mode, blocked cookies), so every access is guarded and the UI still works
// for the session without persistence.
const STORAGE_KEY = 'vg-theme';
const META_COLOR = { light: '#FAFAF9', dark: '#0E0E10' };

function readTheme() {
  try {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr === 'dark' || attr === 'light') return attr;
  } catch { /* fall through */ }
  return 'light';
}

function applyTheme(theme) {
  try {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', META_COLOR[theme]);
  } catch { /* non-DOM environment */ }
}

export function useTheme() {
  const [theme, setThemeState] = useState(readTheme);

  // Keep other tabs in sync.
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === STORAGE_KEY && (e.newValue === 'dark' || e.newValue === 'light')) {
        applyTheme(e.newValue);
        setThemeState(e.newValue);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const setTheme = useCallback((next) => {
    applyTheme(next);
    setThemeState(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* ignore */ }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(readTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}

import { useState, useEffect } from 'react';

const DISMISS_KEY = 'pwa-install-dismissed-at';
const COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000;

export function usePWAInstall() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [isInstalled, setIsInstalled] = useState(false);
  const [isIOS, setIsIOS] = useState(false);
  const [showBanner, setShowBanner] = useState(false);

  useEffect(() => {
    const standalone =
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true;
    if (standalone) {
      setIsInstalled(true);
      return;
    }

    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
    setIsIOS(ios);

    const dismissedAt = parseInt(localStorage.getItem(DISMISS_KEY) || '0', 10);
    const cooldownPassed = Date.now() - dismissedAt > COOLDOWN_MS;
    if (!cooldownPassed) return;

    const onBeforeInstall = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
      window.__pwaInstallPrompt = e;
      setShowBanner(true);
    };
    window.addEventListener('beforeinstallprompt', onBeforeInstall);
    window.addEventListener('appinstalled', () => {
      setIsInstalled(true);
      setShowBanner(false);
      setDeferredPrompt(null);
      window.__pwaInstallPrompt = null;
    });

    if (ios && cooldownPassed) {
      const t = setTimeout(() => setShowBanner(true), 4000);
      return () => {
        clearTimeout(t);
        window.removeEventListener('beforeinstallprompt', onBeforeInstall);
      };
    }

    return () => window.removeEventListener('beforeinstallprompt', onBeforeInstall);
  }, []);

  const triggerInstall = async () => {
    if (!deferredPrompt) return false;
    let outcome = 'dismissed';
    try {
      deferredPrompt.prompt();
      ({ outcome } = await deferredPrompt.userChoice);
    } finally {
      // A BeforeInstallPromptEvent can only be prompted once — clear it
      // whether the user accepted or dismissed, so the button doesn't
      // silently break on a second click.
      setShowBanner(false);
      setDeferredPrompt(null);
      window.__pwaInstallPrompt = null;
    }
    if (outcome === 'accepted') {
      setIsInstalled(true);
    }
    return outcome === 'accepted';
  };

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
    setShowBanner(false);
  };

  return { deferredPrompt, isInstalled, isIOS, showBanner, triggerInstall, dismiss };
}

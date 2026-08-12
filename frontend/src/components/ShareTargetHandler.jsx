import { useEffect } from 'react';

function extractURL(text) {
  if (!text) return null;
  const m = text.match(/https?:\/\/[^\s<>"{}|\\^[\]]+/);
  return m ? m[0] : null;
}

export default function ShareTargetHandler() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const shareURL =
      params.get('share_url') ||
      extractURL(params.get('share_text')) ||
      params.get('url');

    if (!shareURL) return;

    // Clear URL params to prevent re-trigger on refresh
    window.history.replaceState({}, '', window.location.pathname);

    // Store and fire event for DashboardContent to pick up
    try {
      sessionStorage.setItem('vg_pending_share_url', shareURL);
    } catch {}

    window.dispatchEvent(
      new CustomEvent('vidgrab:share-url', { detail: { url: shareURL } })
    );
  }, []);

  return null;
}

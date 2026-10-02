import { useEffect } from 'react';

function extractURL(text) {
  if (!text) return null;
  const m = text.match(/https?:\/\/[^\s<>"{}|\\^[\]]+/);
  return m ? m[0] : null;
}

export default function ShareTargetHandler() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    // Android often puts the link inside `text` (or `title`) and leaves `url`
    // empty, so look in all three. share_* are the old manifest param names.
    const shareURL =
      extractURL(params.get('url')) ||
      extractURL(params.get('text')) ||
      extractURL(params.get('title')) ||
      params.get('share_url') ||
      extractURL(params.get('share_text'));

    if (!shareURL) return;

    // Clear URL params to prevent re-trigger on refresh
    window.history.replaceState({}, '', window.location.pathname === '/share-target' ? '/' : window.location.pathname);

    // Store and fire event for DashboardContent to pick up
    try {
      sessionStorage.setItem('vg_pending_share_url', shareURL);
    } catch {}

    // Deferred: this effect runs before later siblings (MobileShareIntake)
    // have attached their listeners, so a synchronous dispatch was never heard.
    setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent('vidgrab:share-url', { detail: { url: shareURL } })
      );
    }, 0);
  }, []);

  return null;
}

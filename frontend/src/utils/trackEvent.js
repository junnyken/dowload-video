/**
 * Phase 14 — Client-side event tracking utility.
 * Fires events to POST /api/v1/events/track.
 * Fire-and-forget: never throws, never blocks UI.
 */

const API_BASE = import.meta.env.VITE_API_URL || '';
const ENDPOINT = `${API_BASE}/api/v1/events/track`;

// Read or generate a stable anonymous_id for this browser
function getAnonymousId() {
  const key = 'vg_anon_id';
  let id = localStorage.getItem(key);
  if (!id) {
    id = 'anon_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem(key, id);
  }
  return id;
}

/**
 * Fire an analytics event. Non-blocking, never rejects.
 * @param {string} eventName   - Event name from the catalog
 * @param {object} properties  - Arbitrary key-value context
 * @param {string} source      - 'web' | 'extension' | 'telegram_bot' | 'api'
 */
export async function trackEvent(eventName, properties = {}, source = 'web') {
  try {
    const payload = {
      event_name: eventName,
      anonymous_id: getAnonymousId(),
      properties: {
        ...properties,
        href: window.location.pathname,
        ua: navigator.userAgent.slice(0, 80),
      },
      experiment_variants: getActiveExperiments(),
    };
    // Fire without awaiting response — just queue and forget
    fetch(ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-VG-Source': source,
      },
      body: JSON.stringify(payload),
      // keepalive ensures event fires even if page unloads
      keepalive: true,
    }).catch(() => {});
  } catch (e) {
    // Tracking should never crash the app
  }
}

// Active experiment variants (populated once from /api/v1/events/experiments)
let _experiments = {};

export function setExperiments(config) {
  _experiments = config || {};
}

export function getActiveExperiments() {
  return _experiments;
}

export function getExperimentValue(key, defaultValue = null) {
  return _experiments[key] ?? defaultValue;
}

// ── Catalog of event names ────────────────────────────────────────────
export const EVENT = {
  LANDING_PAGE_VIEW:      'landing_page_view',
  PASTE_URL:              'paste_url',
  PLATFORM_DETECTED:      'platform_detected',
  FETCH_SUCCESS:          'fetch_success',
  FETCH_FAILED:           'fetch_failed',
  DOWNLOAD_STARTED:       'download_started',
  DOWNLOAD_SUCCESS:       'download_success',
  DOWNLOAD_FAILED:        'download_failed',
  BULK_STARTED:           'bulk_started',
  PAYWALL_SEEN:           'paywall_seen',
  UPGRADE_CLICKED:        'upgrade_clicked',
  EXTENSION_INSTALL_CLICK:'extension_install_click',
  TELEGRAM_BOT_LINKED:    'telegram_bot_linked',
  API_KEY_CREATED:        'api_key_created',
  PWA_INSTALLED:          'pwa_installed',
  NUDGE_SHOWN:            'nudge_shown',
  NUDGE_DISMISSED:        'nudge_dismissed',
  NUDGE_CLICKED:          'nudge_clicked',
};

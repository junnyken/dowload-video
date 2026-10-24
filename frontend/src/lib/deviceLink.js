/**
 * Device linking (owner 24/10/2026, backend app/core/device_link.py).
 *
 * An account works on a few devices (Free 2, Pro 5) and Free accounts on one
 * device share one allowance. A device is the X-VG-Device header (64 hex),
 * sent only on signed-in calls to our API.
 *
 * Which id: the VidGrab extension in this browser hands over its id (the
 * VidGrab app's machine id when the app is installed), so site + extension +
 * app on one PC count as ONE device. Without the extension the site makes its
 * own random id after a short wait. Until the id is decided, calls go without
 * the header (not linked), so a race never registers a second device.
 *
 * When the server answers 401 device_signed_out (this device was signed out
 * because the account is on too many devices), the person is signed out here
 * and onSignedOut listeners get the server's message.
 */
import { API_BASE } from './apiBase';

const KEY = 'vg_device_id';
const HEX64 = /^[0-9a-f]{64}$/;
const WAIT_FOR_EXTENSION_MS = 2000;

let deviceId = null;
const listeners = new Set();

function read() {
  try { const v = localStorage.getItem(KEY); return HEX64.test(v || '') ? v : null; } catch { return null; }
}
function write(v) {
  deviceId = v;
  try { localStorage.setItem(KEY, v); } catch { /* private mode: kept for this tab */ }
}
function randomId() {
  const b = new Uint8Array(32);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

export function currentDeviceId() { return deviceId; }

export function onSignedOut(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function isOurApi(url) {
  if (!url) return false;
  if (API_BASE) return url.startsWith(API_BASE);
  return url.startsWith('/api/') || url.startsWith(`${window.location.origin}/api/`);
}

function hasBearer(headers) {
  if (!headers) return false;
  if (typeof Headers !== 'undefined' && headers instanceof Headers) return /^Bearer /.test(headers.get('Authorization') || '');
  return Object.keys(headers).some((k) => k.toLowerCase() === 'authorization' && /^Bearer /.test(String(headers[k])));
}

async function checkSignedOut(resp) {
  try {
    const j = await resp.json();
    const d = j && j.detail;
    if (d && d.error_code === 'device_signed_out') listeners.forEach((fn) => { try { fn(d.user_message || ''); } catch { /* listener */ } });
  } catch { /* not JSON */ }
}

let installed = false;
export function installDeviceLink() {
  if (installed || typeof window === 'undefined' || !window.fetch) return;
  installed = true;
  deviceId = read();

  // The extension's web-bridge.js posts its id (and answers a request for it).
  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const d = event.data;
    if (d && d.__vg_source === 'extension' && d.type === 'VG_DEVICE_ID' && HEX64.test(d.id || '')) write(d.id);
  });
  window.postMessage({ __vg_source: 'webapp', type: 'VG_REQUEST_DEVICE_ID' }, window.location.origin);
  if (!deviceId) setTimeout(() => { if (!deviceId) write(randomId()); }, WAIT_FOR_EXTENSION_MS);

  const orig = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    let opts = init;
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (deviceId && isOurApi(url) && hasBearer(init && init.headers)) {
        const h = init.headers instanceof Headers ? Object.fromEntries(init.headers.entries()) : { ...init.headers };
        opts = { ...init, headers: { ...h, 'X-VG-Device': deviceId } };
      }
    } catch { opts = init; }
    const resp = await orig(input, opts);
    if (resp.status === 401) checkSignedOut(resp.clone());
    return resp;
  };
}

// Web Push subscription for the signed-in user (Channel Watch, task #6257).
// Same steps as BulkContent's after-batch prompt, but started by a click.
import { API_BASE } from './apiBase';

function keyBytes(raw) {
  const padding = '='.repeat((4 - (raw.length % 4)) % 4);
  const base64 = (raw + padding).replace(/-/g, '+').replace(/_/g, '/');
  const data = atob(base64);
  const out = new Uint8Array(data.length);
  for (let i = 0; i < data.length; i++) out[i] = data.charCodeAt(i);
  return out;
}

export function pushSupported() {
  return typeof window !== 'undefined'
    && 'Notification' in window && 'serviceWorker' in navigator && 'PushManager' in window;
}

/** Returns 'ok' | 'unsupported' | 'denied' | 'not_configured' | 'failed'. */
export async function enableWebPush(accessToken) {
  if (!pushSupported()) return 'unsupported';
  if (!accessToken) return 'failed';
  try {
    const vapidRes = await fetch(`${API_BASE}/api/v1/push/vapid-key`);
    if (!vapidRes.ok) return 'not_configured';
    const { public_key: rawKey } = await vapidRes.json();
    if (!rawKey) return 'not_configured';
    if (Notification.permission !== 'granted') {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') return 'denied';
    }
    const reg = await navigator.serviceWorker.ready;
    const appServerKey = keyBytes(rawKey);
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      const oldKey = sub.options?.applicationServerKey
        ? new Uint8Array(sub.options.applicationServerKey) : null;
      const sameKey = oldKey && oldKey.length === appServerKey.length
        && oldKey.every((b, i) => b === appServerKey[i]);
      if (!sameKey) { await sub.unsubscribe(); sub = null; }
    }
    if (!sub) {
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appServerKey });
    }
    const r = await fetch(`${API_BASE}/api/v1/push/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify(sub.toJSON()),
    });
    return r.ok ? 'ok' : 'failed';
  } catch {
    return 'failed';
  }
}

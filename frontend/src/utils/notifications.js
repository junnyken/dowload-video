/**
 * Browser notification helper.
 * All functions are no-ops when the Notification API is unavailable or denied.
 */

import i18n from '../i18n';

const ICON = '/icon-192.png';
const tc = (key, opts) => i18n.t(key, { ns: 'common', ...opts });

export function isSupported() {
  return typeof Notification !== 'undefined';
}

export function getPermission() {
  if (!isSupported()) return 'unsupported';
  return Notification.permission;
}

export async function requestPermission() {
  if (!isSupported()) return 'unsupported';
  if (Notification.permission === 'granted') return 'granted';
  return Notification.requestPermission();
}

export function notify(title, body = '', options = {}) {
  if (!isSupported() || Notification.permission !== 'granted') return null;
  try {
    const n = new Notification(title, {
      body,
      icon: ICON,
      badge: ICON,
      silent: false,
      ...options,
    });
    n.onclick = () => { window.focus(); n.close(); };
    return n;
  } catch {
    return null;
  }
}

export function notifyDownloadDone(title) {
  notify(tc('notify.downloadDone'), title || tc('notify.fileReady'), { tag: 'download-done' });
}

export function notifyDownloadFailed(title, reason) {
  notify(tc('notify.downloadFailed'), `${title || 'Job'}: ${reason || tc('notify.unknownError')}`, { tag: 'download-failed' });
}

export function notifyArchiveDone(title) {
  notify(tc('notify.archiveDone'), title || tc('notify.savedToArchive'), { tag: 'archive-done' });
}

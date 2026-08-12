/**
 * Browser notification helper.
 * All functions are no-ops when the Notification API is unavailable or denied.
 */

const ICON = '/icon-192.png';

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
  notify('✅ Tải xuống hoàn tất', title || 'File đã sẵn sàng tải về.', { tag: 'download-done' });
}

export function notifyDownloadFailed(title, reason) {
  notify('❌ Tải xuống thất bại', `${title || 'Job'}: ${reason || 'Lỗi không xác định.'}`, { tag: 'download-failed' });
}

export function notifyArchiveDone(title) {
  notify('📁 Archive hoàn tất', title || 'Đã lưu vào Archive.', { tag: 'archive-done' });
}

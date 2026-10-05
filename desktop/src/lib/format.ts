export function formatBytes(n: number | null | undefined): string {
  if (n == null || !isFinite(n) || n < 0) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
}

export function formatSpeed(bps: number | null | undefined): string {
  return bps == null ? '—' : `${formatBytes(bps)}/s`;
}

export function formatDuration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) return '';
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const p = (x: number) => String(x).padStart(2, '0');
  return h > 0 ? `${h}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`;
}

export function formatEta(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) return '—';
  if (sec < 60) return `${Math.round(sec)} giây`;
  if (sec < 3600) return `${Math.floor(sec / 60)} phút ${Math.round(sec % 60)} giây`;
  return `${Math.floor(sec / 3600)} giờ ${Math.floor((sec % 3600) / 60)} phút`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function platformLabel(p: string): string {
  const map: Record<string, string> = {
    youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', facebook: 'Facebook',
    twitter: 'X / Twitter', x: 'X / Twitter', threads: 'Threads', reddit: 'Reddit',
    vimeo: 'Vimeo', bilibili: 'Bilibili', douyin: 'Douyin', soundcloud: 'SoundCloud',
  };
  const k = (p || '').toLowerCase();
  return map[k] ?? (p ? p.charAt(0).toUpperCase() + p.slice(1) : 'Khác');
}

export function newId(): string {
  return crypto.randomUUID();
}

/** a < b for dotted numeric versions. */
export function versionLess(a: string, b: string): boolean {
  const pa = a.replace(/^v/, '').split('.').map((x) => parseInt(x, 10) || 0);
  const pb = b.replace(/^v/, '').split('.').map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0, y = pb[i] ?? 0;
    if (x !== y) return x < y;
  }
  return false;
}

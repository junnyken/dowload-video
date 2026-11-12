// Pure helpers for WatchPage channel grouping & bulk ZIP naming (Phase 33F).

const FORBIDDEN_RE = /[<>:"/\\|?*\x00-\x1f\x7f]/g;
const WIN_RESERVED_RE = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;

/**
 * Sanitize channel display name for a safe cross-platform ZIP filename:
 * - strips leading `@`
 * - replaces Windows/macOS forbidden filename chars with `_`
 * - collapses consecutive whitespace
 * - trims leading/trailing dots and spaces
 * - prefixes reserved Windows device names with `_`
 * - caps at 60 Unicode code points without splitting surrogate pairs (emojis)
 */
export function sanitizeZipChannelName(raw) {
  let s = String(raw || '').trim().replace(/^@+/, '');
  s = s.replace(FORBIDDEN_RE, '_').replace(/\s+/g, ' ');
  s = s.replace(/^[.\s]+|[.\s]+$/g, '');
  const cps = Array.from(s);
  if (cps.length > 60) {
    s = cps.slice(0, 60).join('').replace(/[.\s_]+$/g, '');
  }
  s = s.replace(/[.\s_]+$/g, '');
  if (!s) return 'channel';
  if (WIN_RESERVED_RE.test(s)) return `_${s}`;
  return s;
}

/**
 * Build `<tên kênh>_<yyyy-mm-dd>.zip` using UTC date (or local date when valid).
 */
export function buildChannelZipFilename(channelName, date = new Date()) {
  const safeName = sanitizeZipChannelName(channelName);
  const d = date instanceof Date && !Number.isNaN(date.getTime()) ? date : new Date();
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${safeName}_${yyyy}-${mm}-${dd}.zip`;
}

/**
 * Group inbox items by subscription/source while keeping first-seen channel order
 * and newest-first item order inside each channel group.
 */
export function groupWatchItemsByChannel(items) {
  if (!Array.isArray(items) || items.length === 0) return [];
  const map = new Map();
  for (const it of items) {
    if (!it) continue;
    const key = String(it.subscription_id || it.source_id || it.channel || 'unknown');
    let group = map.get(key);
    if (!group) {
      group = {
        key,
        subscriptionId: it.subscription_id || null,
        sourceId: it.source_id || null,
        channel: it.channel || it.platform || 'channel',
        platform: it.platform || '',
        items: [],
      };
      map.set(key, group);
    }
    group.items.push(it);
  }
  return Array.from(map.values());
}

/**
 * Determine whether the user's plan allows bulk ZIP download on WatchPage.
 * Prefers authoritative `status.bulk_zip_allowed` from `/api/v1/watch/status`,
 * falling back to client-side entitlement check.
 */
export function canShowWatchBulkZip(status, entitlementAllowed = false) {
  if (status && typeof status === 'object' && typeof status.bulk_zip_allowed === 'boolean') {
    return status.bulk_zip_allowed;
  }
  return Boolean(entitlementAllowed);
}

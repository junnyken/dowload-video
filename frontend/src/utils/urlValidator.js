/**
 * URL validation and platform detection utilities.
 * Runs client-side before any fetch — catches obvious mistakes early.
 */

const PLATFORM_PATTERNS = [
  { id: 'youtube',   label: 'YouTube',   re: /youtube\.com\/|youtu\.be\// },
  { id: 'tiktok',    label: 'TikTok',    re: /tiktok\.com\// },
  { id: 'instagram', label: 'Instagram', re: /instagram\.com\// },
  { id: 'facebook',  label: 'Facebook',  re: /facebook\.com\/|fb\.watch\// },
  { id: 'twitter',   label: 'X / Twitter', re: /twitter\.com\/|x\.com\// },
  { id: 'threads',   label: 'Threads',   re: /threads\.net\/|threads\.com\// },
  { id: 'spotify',   label: 'Spotify',   re: /spotify\.com\// },
  { id: 'douyin',    label: 'Douyin',    re: /douyin\.com\// },
];

/**
 * Returns { valid, platform, error } for a given URL string.
 *
 * @param {string} raw  - raw user input (may be untrimmed)
 * @returns {{ valid: boolean, platform: string|null, error: string|null }}
 */
export function validateUrl(raw) {
  const url = (raw || '').trim();

  if (!url) {
    return { valid: false, platform: null, error: 'Vui lòng nhập URL.' };
  }

  // Must start with http(s)://
  if (!/^https?:\/\//i.test(url)) {
    return {
      valid: false,
      platform: null,
      error: 'URL phải bắt đầu bằng https:// hoặc http://',
    };
  }

  // Basic parse check
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { valid: false, platform: null, error: 'URL không hợp lệ.' };
  }

  // Reject obviously internal/localhost hosts
  const host = parsed.hostname.toLowerCase();
  const BLOCKED = ['localhost', '127.0.0.1', '0.0.0.0', '::1'];
  if (BLOCKED.some(b => host === b || host.endsWith(`.${b}`))) {
    return { valid: false, platform: null, error: 'URL không được phép.' };
  }

  // Detect platform
  const match = PLATFORM_PATTERNS.find(p => p.re.test(url));
  const platform = match ? match.id : null;

  return { valid: true, platform, error: null };
}

/**
 * Normalize a URL: trim whitespace, strip trailing ?& noise, lowercase scheme/host.
 */
export function normalizeUrl(raw) {
  const url = (raw || '').trim();
  try {
    const p = new URL(url);
    // Remove empty query params
    p.searchParams.forEach((v, k) => { if (!v) p.searchParams.delete(k); });
    return p.toString();
  } catch {
    return url;
  }
}

/**
 * Get display label for a detected platform id.
 */
export function platformLabel(id) {
  return PLATFORM_PATTERNS.find(p => p.id === id)?.label ?? id ?? 'Unknown';
}

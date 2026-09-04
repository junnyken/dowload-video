/**
 * Has this person used VidGrab before?
 *
 * Shared by everything on the home screen that introduces the product, so
 * they all agree on who is being introduced to it. The hero and the
 * beginner's guide were each deciding this separately, which is how a
 * returning visitor ended up scrolling past both to reach the input.
 *
 * Two signals, both already written by the app:
 *   vg_used_before    — set on a successful link fetch (any download path)
 *   vg_download_count — set on a completed merge-download (older, narrower)
 *
 * The second is kept so people who used the tool before vg_used_before
 * shipped are recognised immediately rather than re-introduced once more.
 *
 * Storage blocked (private mode) reads as a first visit: showing the full
 * introduction to a returning user is a small annoyance, hiding it from a
 * genuinely new one is worse.
 */
export function hasUsedBefore() {
  try {
    if (localStorage.getItem('vg_used_before') === '1') return true;
    return parseInt(localStorage.getItem('vg_download_count') || '0', 10) >= 1;
  } catch {
    return false;
  }
}

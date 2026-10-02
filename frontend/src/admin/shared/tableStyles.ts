// Shared table class names so every admin table looks the same:
// sticky mono header on surface-2, hover row on surface-2, numbers right-aligned.

/** Card wrapper that scrolls horizontally instead of breaking the page. */
export const TABLE_CARD = 'overflow-hidden rounded-card border border-line bg-surface shadow-card'
export const TABLE_SCROLL = 'overflow-x-auto'
export const TABLE = 'w-full min-w-max text-sm'
export const TH = 'sticky top-0 z-10 bg-surface-2 px-3 py-2.5 text-left font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted first:pl-4 last:pr-4'
export const TH_NUM = `${TH} text-right`
export const TR = 'border-b border-line last:border-0 transition-colors hover:bg-surface-2'
export const TD = 'px-3 py-2.5 align-middle text-fg first:pl-4 last:pr-4'
export const TD_NUM = `${TD} text-right font-mono tabular-nums`
export const TD_MONO = `${TD} font-mono text-xs`

import { cn } from '../utils/cn'

// ─── Column definition ────────────────────────────────────────────────────────

export interface ColDef {
  /** Unique key — used as React key and for sort callbacks. */
  key:     string
  /** Header display text. Empty string renders a spacer cell. */
  label:   string
  /** Tailwind width class, e.g. `'min-w-[120px]'` or `'w-10'`. */
  width?:  string
  /** Text alignment within the column. Default `'left'`. */
  align?:  'left' | 'right' | 'center'
  /** Screen-reader only — hides visible text (use for action columns). */
  sr?:     boolean
  /** Show sort chevron when not null. Pass `'asc'`/`'desc'`/`null`. */
  sort?:   'asc' | 'desc' | null
}

// ─── Sort chevron icon ─────────────────────────────────────────────────────────

function SortIcon({ dir }: { dir: 'asc' | 'desc' | null }) {
  return (
    <span className="ml-1 inline-flex flex-col gap-px opacity-40">
      <svg
        viewBox="0 0 6 4"
        className={cn('h-[5px] w-[6px]', dir === 'asc' && 'opacity-100 text-blue-400')}
        fill="currentColor"
      >
        <path d="M3 0L6 4H0L3 0z" />
      </svg>
      <svg
        viewBox="0 0 6 4"
        className={cn('h-[5px] w-[6px]', dir === 'desc' && 'opacity-100 text-blue-400')}
        fill="currentColor"
      >
        <path d="M3 4L0 0H6L3 4z" />
      </svg>
    </span>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface TableHeaderProps {
  cols:       ColDef[]
  /** Makes `<thead>` sticky to the top of the table's scroll container. */
  sticky?:    boolean
  /** Called when a sortable column heading is clicked. */
  onSort?:    (key: string) => void
  className?: string
}

export function TableHeader({
  cols,
  sticky    = true,
  onSort,
  className,
}: TableHeaderProps) {
  return (
    <thead
      className={cn(
        sticky && 'sticky top-0 z-10 bg-slate-900',
        className,
      )}
    >
      <tr className="border-y border-slate-800">
        {cols.map((col, i) => {
          const isSortable = col.sort !== undefined && onSort
          const alignClass =
            col.align === 'right'  ? 'text-right'  :
            col.align === 'center' ? 'text-center' : 'text-left'

          return (
            <th
              key={col.key}
              scope="col"
              className={cn(
                'py-2 pr-3 font-mono text-[9px] font-semibold uppercase tracking-widest text-slate-600',
                'first:pl-4',
                alignClass,
                col.width,
                isSortable && 'cursor-pointer select-none hover:text-slate-400',
              )}
              onClick={isSortable ? () => onSort(col.key) : undefined}
              aria-sort={
                col.sort === 'asc'  ? 'ascending'  :
                col.sort === 'desc' ? 'descending' :
                isSortable          ? 'none'       : undefined
              }
            >
              {col.sr ? (
                <span className="sr-only">{col.label}</span>
              ) : (
                <span className="inline-flex items-center">
                  {col.label}
                  {isSortable && <SortIcon dir={col.sort ?? null} />}
                </span>
              )}
            </th>
          )
        })}
      </tr>
    </thead>
  )
}

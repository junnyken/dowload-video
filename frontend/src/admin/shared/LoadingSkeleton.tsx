import { cn } from '../utils/cn'

// ─── Base bone ────────────────────────────────────────────────────────────────

function Bone({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn('animate-pulse rounded bg-slate-800', className)}
    />
  )
}

// ─── Line — single text-height skeleton ───────────────────────────────────────

interface LineSkeletonProps {
  width?:     string  // Tailwind w-* class, default 'w-full'
  height?:    string  // Tailwind h-* class, default 'h-3'
  className?: string
}

export function LineSkeleton({
  width     = 'w-full',
  height    = 'h-3',
  className,
}: LineSkeletonProps) {
  return <Bone className={cn(height, width, className)} />
}

// ─── Block — rect placeholder (images, charts, empty sections) ────────────────

interface BlockSkeletonProps {
  height?:    string  // default 'h-20'
  className?: string
}

export function BlockSkeleton({ height = 'h-20', className }: BlockSkeletonProps) {
  return <Bone className={cn('w-full rounded-xl', height, className)} />
}

// ─── Avatar — circle (user avatars, platform icons) ───────────────────────────

type AvatarSize = 'xs' | 'sm' | 'md' | 'lg'

const AVATAR_SIZE: Record<AvatarSize, string> = {
  xs: 'h-5 w-5',
  sm: 'h-7 w-7',
  md: 'h-9 w-9',
  lg: 'h-12 w-12',
}

export function AvatarSkeleton({ size = 'sm', className }: { size?: AvatarSize; className?: string }) {
  return (
    <Bone
      className={cn('flex-shrink-0 rounded-full', AVATAR_SIZE[size], className)}
    />
  )
}

// ─── Stat card — number + label ───────────────────────────────────────────────

export function StatSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Bone className="h-7 w-16" />
      <Bone className="h-2.5 w-24" />
    </div>
  )
}

// ─── LoadingSkeleton — multi-line paragraph shimmer ───────────────────────────

interface LoadingSkeletonProps {
  lines?:     number   // default 3
  className?: string
}

export function LoadingSkeleton({ lines = 3, className }: LoadingSkeletonProps) {
  return (
    <div className={cn('flex flex-col gap-2', className)} aria-busy aria-label="Loading">
      {Array.from({ length: lines }).map((_, i) => (
        <Bone
          key={i}
          className={cn('h-3', i === lines - 1 ? 'w-2/3' : 'w-full')}
        />
      ))}
    </div>
  )
}

// ─── CardSkeleton — stat / content card placeholder ───────────────────────────

export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div
      aria-busy
      aria-label="Loading"
      className={cn(
        'animate-pulse rounded-2xl border border-slate-800 bg-slate-900/70 p-4',
        className,
      )}
    >
      <Bone className="mb-3 h-2.5 w-20" />
      <Bone className="mb-2 h-8 w-28" />
      <Bone className="h-2.5 w-36" />
    </div>
  )
}

// ─── TableSkeleton — row placeholders inside a table body ────────────────────

export function TableSkeleton({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div
      aria-busy
      aria-label="Loading"
      className={cn('divide-y divide-slate-800/50', className)}
    >
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex animate-pulse items-center gap-3 px-4 py-2.5">
          <Bone className="h-4 w-14 flex-shrink-0" />
          <Bone className="h-4 flex-1" />
          <Bone className="h-4 w-20 flex-shrink-0" />
          <Bone className="h-4 w-10 flex-shrink-0" />
        </div>
      ))}
    </div>
  )
}

// ─── PanelSkeleton — full card: header bar + N row shimmer ───────────────────

export function PanelSkeleton({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div
      aria-busy
      aria-label="Loading"
      className={cn(
        'animate-pulse overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <Bone className="h-2.5 w-24" />
        <Bone className="h-6 w-16 rounded-lg" />
      </div>
      {/* Row shimmer */}
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 border-b border-slate-800/50 px-4 py-3 last:border-0">
          <Bone className="h-4 w-14 flex-shrink-0" />
          <Bone className="h-4 flex-1" />
          <Bone className="h-5 w-16 rounded-full flex-shrink-0" />
          <Bone className="h-5 w-5 rounded flex-shrink-0" />
        </div>
      ))}
    </div>
  )
}

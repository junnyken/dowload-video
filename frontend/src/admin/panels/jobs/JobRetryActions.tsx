import { useState } from 'react'
import { cn } from '../../utils/cn'
import type { JobInspectorData, PhaseName } from './job.types'
import { PHASE_ORDER } from './job.types'

// ─── Inline confirm modal ──────────────────────────────────────────────────────

interface ConfirmModalProps {
  isOpen:       boolean
  title:        string
  description:  string
  confirmLabel: string
  destructive?: boolean
  onConfirm:    () => void
  onCancel:     () => void
}

function ConfirmModal({
  isOpen,
  title,
  description,
  confirmLabel,
  destructive,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  if (!isOpen) return null
  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-slate-950/80 backdrop-blur-sm"
        onClick={onCancel}
        aria-hidden
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
          onClick={e => e.stopPropagation()}
          role="dialog"
          aria-modal
        >
          <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-500">{description}</p>
          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={onCancel}
              className="rounded-lg border border-slate-700 px-4 py-1.5 text-xs font-medium text-slate-400 transition-colors hover:border-slate-600 hover:text-slate-200"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              className={cn(
                'rounded-lg px-4 py-1.5 text-xs font-semibold text-white transition-colors',
                destructive ? 'bg-red-700 hover:bg-red-600' : 'bg-blue-600 hover:bg-blue-500',
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

// ─── Action types ──────────────────────────────────────────────────────────────

type ActionKind = 'retry_full' | 'retry_phase' | 'cancel'

interface PendingAction {
  kind:   ActionKind
  phase?: PhaseName
}

// ─── Main actions bar ──────────────────────────────────────────────────────────

interface JobRetryActionsProps {
  job:              JobInspectorData
  onRetryFull?:     (jobId: string) => void
  onRetryFromPhase?: (jobId: string, phase: PhaseName) => void
  onCancel?:        (jobId: string) => void
}

export function JobRetryActions({
  job,
  onRetryFull,
  onRetryFromPhase,
  onCancel,
}: JobRetryActionsProps) {
  const [pending,     setPending]     = useState<PendingAction | null>(null)
  const [phaseTarget, setPhaseTarget] = useState<PhaseName>('queued')

  const canRetry  = job.currentStatus === 'failed' || job.currentStatus === 'stuck'
  const canCancel = job.currentStatus === 'running' || job.currentStatus === 'queued' || job.currentStatus === 'stuck'

  function request(kind: ActionKind) {
    setPending({ kind, phase: kind === 'retry_phase' ? phaseTarget : undefined })
  }

  function confirm() {
    if (!pending) return
    switch (pending.kind) {
      case 'retry_full':   onRetryFull?.(job.jobId);                     break
      case 'retry_phase':  onRetryFromPhase?.(job.jobId, phaseTarget);   break
      case 'cancel':       onCancel?.(job.jobId);                        break
    }
    setPending(null)
  }

  type ModalDef = Omit<ConfirmModalProps, 'isOpen' | 'onConfirm' | 'onCancel'>

  const MODAL_DEFS: Record<ActionKind, ModalDef> = {
    retry_full: {
      title:        'Retry Full Job',
      description:  `Re-queue ${job.jobId} from the beginning. All phases will re-run in sequence.`,
      confirmLabel: 'Retry Full',
    },
    retry_phase: {
      title:        `Retry from "${phaseTarget}"`,
      description:  `Resume ${job.jobId} starting at the ${phaseTarget} phase. Phases before it are skipped.`,
      confirmLabel: 'Retry from Phase',
    },
    cancel: {
      title:        'Cancel Job',
      description:  `Stop all in-progress work for ${job.jobId} and mark it cancelled. This cannot be undone.`,
      confirmLabel: 'Cancel Job',
      destructive:  true,
    },
  }

  const modal = pending ? MODAL_DEFS[pending.kind] : null

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 px-4 py-3">
        {/* Retry Full */}
        <button
          onClick={() => request('retry_full')}
          disabled={!canRetry}
          title={!canRetry ? `Cannot retry — job is ${job.currentStatus}` : undefined}
          className={cn(
            'flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors',
            'border-slate-700 text-slate-300 hover:border-blue-700 hover:bg-blue-950/30 hover:text-blue-300',
            'disabled:cursor-not-allowed disabled:opacity-35',
          )}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" className="h-3.5 w-3.5">
            <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Retry Full
        </button>

        {/* Divider */}
        <div className="h-4 w-px bg-slate-800" />

        {/* Phase picker + Retry from Phase */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-slate-700">From:</span>
          <select
            value={phaseTarget}
            onChange={e => setPhaseTarget(e.target.value as PhaseName)}
            disabled={!canRetry}
            className={cn(
              'rounded border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-slate-300',
              'outline-none transition-colors focus:border-blue-700',
              'disabled:cursor-not-allowed disabled:opacity-35',
            )}
          >
            {PHASE_ORDER.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <button
            onClick={() => request('retry_phase')}
            disabled={!canRetry}
            className={cn(
              'flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors',
              'border-slate-700 text-slate-300 hover:border-blue-700 hover:bg-blue-950/30 hover:text-blue-300',
              'disabled:cursor-not-allowed disabled:opacity-35',
            )}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" className="h-3.5 w-3.5">
              <path d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            Retry from Phase
          </button>
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Cancel Job */}
        <button
          onClick={() => request('cancel')}
          disabled={!canCancel}
          title={!canCancel ? `Cannot cancel — job is ${job.currentStatus}` : undefined}
          className={cn(
            'flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors',
            'border-slate-700 text-red-700 hover:border-red-900/60 hover:bg-red-950/30 hover:text-red-400',
            'disabled:cursor-not-allowed disabled:opacity-35',
          )}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" className="h-3.5 w-3.5">
            <path d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
          </svg>
          Cancel Job
        </button>
      </div>

      {/* Confirm dialog */}
      {modal && (
        <ConfirmModal
          isOpen={!!pending}
          title={modal.title}
          description={modal.description}
          confirmLabel={modal.confirmLabel}
          destructive={modal.destructive}
          onConfirm={confirm}
          onCancel={() => setPending(null)}
        />
      )}
    </>
  )
}

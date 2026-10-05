import { cn } from '../../utils/cn'
import type { StorageStatusResponse } from '../../api/cookies'
import { formatClock, formatStamp } from './cookieLifetime'

// ─── Storage line ("is it being saved?") — only real data ──────────────────────

export function describeStorage(st?: StorageStatusResponse, error?: boolean): { text: string; warn: boolean } {
  if (error) return { text: 'Kho cookie: không đọc được kho lúc này', warn: true }
  if (!st) return { text: 'Kho cookie: đang đọc…', warn: false }
  const parts = ['Kho cookie: Redis']
  if (st.total_cookies != null) parts.push(`${st.total_cookies} cookie`)
  if (!st.readable) {
    parts.push('Không đọc được trạng thái lưu bền')
    return { text: parts.join(' · '), warn: true }
  }
  let warn = false
  if (st.aof_enabled) parts.push('nhật ký AOF đang bật')
  if (st.rdb_last_save_time) parts.push(`lưu lần cuối ${formatStamp(st.rdb_last_save_time)}`)
  if (!st.aof_enabled && !st.rdb_last_save_time) {
    parts.push('chưa thấy dấu hiệu lưu bền')
    warn = true
  }
  if (st.rdb_last_bgsave_status && st.rdb_last_bgsave_status !== 'ok') {
    parts.push('lần lưu gần nhất báo lỗi')
    warn = true
  }
  return { text: parts.join(' · '), warn }
}

// ─── Toolbar ───────────────────────────────────────────────────────────────────

export interface BatchProgress {
  done: number
  total: number
  current?: string
  running: boolean
  summary?: string
}

interface Props {
  updatedAtMs: number
  refreshing: boolean
  onReload: () => void
  onTestAll: () => void
  onStop: () => void
  batch: BatchProgress | null
  storage: { text: string; warn: boolean }
}

export function CookieToolbar({ updatedAtMs, refreshing, onReload, onTestAll, onStop, batch, storage }: Props) {
  const running = !!batch?.running
  return (
    <div className="flex flex-col gap-2 rounded-card border border-line bg-surface px-4 py-3 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={onReload}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 rounded-control border border-line px-3 py-1.5 text-xs font-medium text-fg-2 transition-colors hover:bg-surface-2 disabled:opacity-50"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
            className={cn('h-3.5 w-3.5', refreshing && 'animate-spin')}>
            <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          Tải lại
        </button>
        <button
          onClick={onTestAll}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-control bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          Kiểm tra tất cả
        </button>
        {running && (
          <button onClick={onStop} className="rounded-control border border-line px-3 py-1.5 text-xs font-medium text-fg-2 hover:bg-surface-2">
            Dừng
          </button>
        )}
        <span className="ml-auto font-mono text-[11px] text-fg-muted" data-testid="updated-at">
          {updatedAtMs > 0 ? `cập nhật lúc ${formatClock(updatedAtMs)}` : 'chưa có dữ liệu'}
        </span>
      </div>

      {batch && (
        <div className="flex flex-col gap-1" data-testid="batch-progress">
          <div className="flex items-center justify-between text-[11px] text-fg-2">
            <span>
              {batch.running
                ? `Đang kiểm tra ${batch.done}/${batch.total}${batch.current ? ` · ${batch.current}` : ''}`
                : batch.summary ?? `Đã kiểm tra ${batch.done}/${batch.total}`}
            </span>
            <span className="font-mono text-fg-muted">{batch.done}/{batch.total}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${batch.total ? Math.round((batch.done / batch.total) * 100) : 0}%` }} />
          </div>
        </div>
      )}

      <p className={cn('text-[11px]', storage.warn ? 'text-warning' : 'text-fg-muted')} data-testid="storage-line">
        {storage.text}
      </p>
    </div>
  )
}

// ─── Confirm dialog ────────────────────────────────────────────────────────────

interface ConfirmProps {
  count: number
  skippedByCap: number
  cap: number
  onConfirm: () => void
  onCancel: () => void
}

export function TestAllConfirm({ count, skippedByCap, cap, onConfirm, onCancel }: ConfirmProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-fg/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-md rounded-card border border-line bg-surface p-5 shadow-card">
        <h2 className="text-sm font-semibold text-fg">Kiểm tra {count} cookie?</h2>
        <p className="mt-2 text-xs text-fg-2">
          Mỗi lần kiểm tra sẽ dùng chính tài khoản của cookie để gọi thật tới nền tảng.
          Các cookie được kiểm tra lần lượt, cách nhau khoảng 2 giây, và mỗi nền tảng
          có thời gian giãn cách riêng để tránh bị nền tảng khoá tài khoản.
        </p>
        <p className="mt-2 text-xs text-fg-muted">
          Cookie đang tắt và nền tảng chưa hỗ trợ sẽ được bỏ qua. Cookie không bị xoá dù kết quả ra sao.
        </p>
        {skippedByCap > 0 && (
          <p className="mt-2 text-xs text-warning">
            Mỗi lượt tối đa {cap} cookie; còn {skippedByCap} cookie sẽ chưa được kiểm tra lần này.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onCancel} className="rounded-control border border-line px-3 py-1.5 text-xs font-medium text-fg-2 hover:bg-surface-2">
            Huỷ
          </button>
          <button onClick={onConfirm} className="rounded-control bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-hover">
            Kiểm tra {count} cookie
          </button>
        </div>
      </div>
    </div>
  )
}

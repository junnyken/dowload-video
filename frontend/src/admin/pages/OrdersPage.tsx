import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

/* ── Types ─────────────────────────────────────────────────────────────────── */

interface CheckoutOrder {
  id: string
  user_id: string
  user_email?: string
  plan: string
  rail: 'vietqr' | 'paypal'
  status: 'pending' | 'paid' | 'expired' | 'canceled' | 'underpaid'
  amount: number
  currency: string
  code: string
  paid_amount: number
  paypal_order_id?: string
  provider_ref?: string | null
  admin_note?: string | null
  created_at: string
  updated_at?: string
  expires_at: string
  paid_at?: string | null
  activated_at?: string | null
}

interface OrdersResponse {
  orders: CheckoutOrder[]
  total: number
  page: number
  per_page: number
  total_pages: number
}

interface OrderStats {
  total: number
  pending: number
  paid: number
  expired: number
  canceled: number
  underpaid: number
  revenue_vnd: number
}

/* ── Constants ─────────────────────────────────────────────────────────────── */

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  pending:   { bg: 'bg-warning-soft', text: 'text-warning',  label: 'Chờ TT' },
  paid:      { bg: 'bg-success/10',   text: 'text-success',  label: 'Đã TT' },
  expired:   { bg: 'bg-surface-2',    text: 'text-fg-muted', label: 'Hết hạn' },
  canceled:  { bg: 'bg-surface-2',    text: 'text-fg-muted', label: 'Đã huỷ' },
  underpaid: { bg: 'bg-danger-soft',  text: 'text-danger',   label: 'Thiếu tiền' },
}

const RAIL_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  vietqr: { bg: 'bg-accent-soft', text: 'text-accent-text', label: 'VietQR' },
  paypal: { bg: 'bg-[#0070ba]/10', text: 'text-[#0070ba]', label: 'PayPal' },
}

const PLAN_LABELS: Record<string, string> = {
  pro_month: 'Pro Tháng',
  pro_year: 'Pro Năm',
}

const PER_PAGE_OPTIONS = [20, 50, 100]

/* ── Helpers ───────────────────────────────────────────────────────────────── */

function formatVnd(amount: number): string {
  return new Intl.NumberFormat('vi-VN').format(amount) + '₫'
}

function formatMoney(amount: number, currency: string): string {
  if (currency === 'VND') return formatVnd(amount)
  return '$' + (amount / 100).toFixed(2)
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('vi-VN', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
      timeZone: 'Asia/Ho_Chi_Minh',
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function formatRelative(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s trước`
  if (diff < 3600) return `${Math.floor(diff / 60)}p trước`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h trước`
  return `${Math.floor(diff / 86400)}d trước`
}

function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.pending
  return (
    <span className={cn('inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase', style.bg, style.text)}>
      {style.label}
    </span>
  )
}

function RailBadge({ rail }: { rail: string }) {
  const style = RAIL_STYLES[rail] || RAIL_STYLES.vietqr
  return (
    <span className={cn('inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold', style.bg, style.text)}>
      {style.label}
    </span>
  )
}

/* ── Component ─────────────────────────────────────────────────────────────── */

export default function OrdersPage() {
  const [orders, setOrders] = useState<CheckoutOrder[]>([])
  const [stats, setStats] = useState<OrderStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [railFilter, setRailFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')

  // Pagination
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [totalPages, setTotalPages] = useState(1)
  const [totalCount, setTotalCount] = useState(0)

  // Action states
  const [activatingId, setActivatingId] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState<{ id: string; text: string; ok: boolean } | null>(null)

  // Detail drawer
  const [selectedOrder, setSelectedOrder] = useState<CheckoutOrder | null>(null)

  const fetchOrders = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
      })
      if (statusFilter !== 'all') params.set('status', statusFilter)
      if (railFilter !== 'all') params.set('rail', railFilter)
      if (searchQuery.trim()) params.set('q', searchQuery.trim())

      const data = await adminFetch<OrdersResponse>(`/checkout/orders?${params}`)
      setOrders(data.orders)
      setTotalPages(data.total_pages)
      setTotalCount(data.total)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [page, perPage, statusFilter, railFilter, searchQuery])

  const fetchStats = useCallback(async () => {
    try {
      const data = await adminFetch<OrderStats>('/checkout/orders/stats')
      setStats(data)
    } catch { /* ignore — stats are optional */ }
  }, [])

  useEffect(() => { fetchOrders() }, [fetchOrders])
  useEffect(() => { fetchStats() }, [fetchStats])

  // Reset to page 1 when filters change
  useEffect(() => { setPage(1) }, [statusFilter, railFilter, searchQuery, perPage])

  const handleActivate = useCallback(async (orderId: string) => {
    const reason = prompt('Lý do kích hoạt thủ công (có thể bỏ trống):')
    if (reason === null) return // user cancelled prompt
    setActivatingId(orderId)
    setActionMsg(null)
    try {
      await adminPost(`/checkout/orders/${orderId}/activate`, { reason })
      setActionMsg({ id: orderId, text: '✓ Đã kích hoạt thành công', ok: true })
      fetchOrders()
      fetchStats()
    } catch (e) {
      setActionMsg({ id: orderId, text: `✗ ${(e as Error).message}`, ok: false })
    } finally {
      setActivatingId(null)
    }
  }, [fetchOrders, fetchStats])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Đơn hàng Checkout</h1>
          <p className="mt-0.5 text-xs text-fg-muted">
            Quản lý đơn thanh toán tự động VietQR & PayPal (bảng payment_orders tự khởi tạo khi truy cập)
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={async () => {
              try {
                await adminPost('/checkout/ensure-schema', {})
                setActionMsg({ id: 'schema', text: '✓ Đã khởi tạo bảng CSDL (Migration 042) thành công!', ok: true })
                fetchOrders()
                fetchStats()
              } catch (e) {
                setActionMsg({ id: 'schema', text: `✗ ${(e as Error).message}`, ok: false })
              }
            }}
            className="px-3 py-1.5 text-xs rounded-control border border-accent/40 bg-accent-soft font-medium text-accent-text hover:bg-accent/20"
          >
            ⚡ Khởi tạo bảng CSDL (Migration 042)
          </button>
          <button
            onClick={() => { fetchOrders(); fetchStats(); }}
            className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2"
          >
            ↺ Refresh
          </button>
        </div>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          {[
            { label: 'Tổng đơn',    value: stats.total.toLocaleString() },
            { label: 'Chờ TT',      value: stats.pending.toLocaleString(),   color: 'text-warning' },
            { label: 'Đã TT',       value: stats.paid.toLocaleString(),      color: 'text-success' },
            { label: 'Hết hạn',     value: stats.expired.toLocaleString() },
            { label: 'Đã huỷ',      value: stats.canceled.toLocaleString() },
            { label: 'Thiếu tiền',  value: stats.underpaid.toLocaleString(), color: 'text-danger' },
            { label: 'Doanh thu',   value: formatVnd(stats.revenue_vnd),     color: 'text-success' },
          ].map(c => (
            <div key={c.label} className="rounded-card border border-line bg-surface shadow-card p-3">
              <p className="font-mono text-[10px] text-fg-muted uppercase">{c.label}</p>
              <p className={cn('mt-0.5 font-mono text-lg font-bold', c.color || 'text-fg')}>{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Search */}
        <input
          type="text"
          placeholder="Tìm VGP code, email, user ID..."
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          className="flex-1 min-w-[200px] rounded border border-line bg-surface px-3 py-1.5 text-xs text-fg placeholder:text-fg-muted"
        />

        {/* Status filter */}
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-xs text-fg-2"
        >
          <option value="all">Tất cả trạng thái</option>
          <option value="pending">Chờ thanh toán</option>
          <option value="paid">Đã thanh toán</option>
          <option value="expired">Hết hạn</option>
          <option value="canceled">Đã huỷ</option>
          <option value="underpaid">Thiếu tiền</option>
        </select>

        {/* Rail filter */}
        <select
          value={railFilter}
          onChange={e => setRailFilter(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-xs text-fg-2"
        >
          <option value="all">Tất cả kênh</option>
          <option value="vietqr">VietQR</option>
          <option value="paypal">PayPal</option>
        </select>

        {/* Per page */}
        <select
          value={perPage}
          onChange={e => setPerPage(Number(e.target.value))}
          className="rounded border border-line bg-surface px-2 py-1.5 text-xs text-fg-2"
        >
          {PER_PAGE_OPTIONS.map(n => (
            <option key={n} value={n}>{n}/trang</option>
          ))}
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {/* Orders table */}
      <div className="rounded-card border border-line bg-surface shadow-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-muted font-mono border-b border-line bg-surface-2">
                <th className="px-3 py-2.5 text-left">Mã VGP</th>
                <th className="px-3 py-2.5 text-left">User</th>
                <th className="px-3 py-2.5 text-left">Gói</th>
                <th className="px-3 py-2.5 text-left">Kênh</th>
                <th className="px-3 py-2.5 text-right">Số tiền</th>
                <th className="px-3 py-2.5 text-right">Đã nhận</th>
                <th className="px-3 py-2.5 text-left">Trạng thái</th>
                <th className="px-3 py-2.5 text-left">Tạo lúc</th>
                <th className="px-3 py-2.5 text-center">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="px-3 py-12 text-center text-fg-muted">
                    <div className="flex items-center justify-center gap-2">
                      <span className="h-4 w-4 border-2 border-fg-muted border-t-transparent rounded-full animate-spin" />
                      Đang tải...
                    </div>
                  </td>
                </tr>
              )}
              {!loading && orders.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-12 text-center text-fg-muted">
                    Không có đơn hàng nào
                  </td>
                </tr>
              )}
              {!loading && orders.map(order => (
                <tr
                  key={order.id}
                  className="border-b border-line hover:bg-surface-2/50 transition-colors cursor-pointer"
                  onClick={() => setSelectedOrder(order)}
                >
                  <td className="px-3 py-2.5">
                    <span className="font-mono font-bold text-accent text-[11px]">{order.code}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="max-w-[180px] truncate">
                      <span className="text-fg-2 font-mono text-[10px]" title={order.user_id}>
                        {order.user_email || order.user_id.slice(0, 12) + '…'}
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="text-fg-2 font-medium">{PLAN_LABELS[order.plan] || order.plan}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <RailBadge rail={order.rail} />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span className="font-mono font-bold text-fg">{formatMoney(order.amount, order.currency)}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {order.paid_amount ? (
                      <span className={cn(
                        'font-mono font-bold',
                        order.paid_amount < order.amount ? 'text-danger' : 'text-success'
                      )}>
                        {formatMoney(order.paid_amount, order.currency)}
                      </span>
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={order.status} />
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="text-fg-muted" title={formatDate(order.created_at)}>
                      {formatRelative(order.created_at)}
                    </span>
                    {order.paid_at && (
                      <span className="block text-[10px] text-success">
                        TT: {formatRelative(order.paid_at)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-center" onClick={e => e.stopPropagation()}>
                    {(order.status === 'pending' || order.status === 'underpaid') && (
                      <button
                        onClick={() => handleActivate(order.id)}
                        disabled={activatingId === order.id}
                        className="rounded-control bg-success px-3 py-1 text-[10px] text-white font-bold
                                   hover:bg-success/80 disabled:opacity-40 transition-colors"
                      >
                        {activatingId === order.id ? 'Đang...' : '✓ Kích hoạt'}
                      </button>
                    )}
                    {order.status === 'paid' && order.activated_at && (
                      <span className="text-[10px] text-success font-mono">
                        ✓ {formatDate(order.activated_at)}
                      </span>
                    )}
                    {actionMsg?.id === order.id && (
                      <p className={cn('mt-1 text-[10px] font-mono', actionMsg.ok ? 'text-success' : 'text-danger')}>
                        {actionMsg.text}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-3 py-2.5 border-t border-line bg-surface-2">
            <span className="text-[10px] text-fg-muted font-mono">
              Hiện {orders.length}/{totalCount} đơn — Trang {page}/{totalPages}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage(1)}
                disabled={page <= 1}
                className="px-2 py-1 text-[10px] rounded border border-line bg-surface text-fg-2 disabled:opacity-30 hover:bg-surface-2"
              >
                ««
              </button>
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="px-2 py-1 text-[10px] rounded border border-line bg-surface text-fg-2 disabled:opacity-30 hover:bg-surface-2"
              >
                «
              </button>

              {/* Page numbers */}
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                let p: number
                if (totalPages <= 5) {
                  p = i + 1
                } else if (page <= 3) {
                  p = i + 1
                } else if (page >= totalPages - 2) {
                  p = totalPages - 4 + i
                } else {
                  p = page - 2 + i
                }
                return (
                  <button
                    key={p}
                    onClick={() => setPage(p)}
                    className={cn(
                      'px-2.5 py-1 text-[10px] rounded border font-mono',
                      p === page
                        ? 'border-accent bg-accent text-accent-fg font-bold'
                        : 'border-line bg-surface text-fg-2 hover:bg-surface-2',
                    )}
                  >
                    {p}
                  </button>
                )
              })}

              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="px-2 py-1 text-[10px] rounded border border-line bg-surface text-fg-2 disabled:opacity-30 hover:bg-surface-2"
              >
                »
              </button>
              <button
                onClick={() => setPage(totalPages)}
                disabled={page >= totalPages}
                className="px-2 py-1 text-[10px] rounded border border-line bg-surface text-fg-2 disabled:opacity-30 hover:bg-surface-2"
              >
                »»
              </button>
            </div>
          </div>
        )}
      </div>

      {/* SePay webhook info */}
      <div className="rounded-card border border-accent/20 bg-accent-soft p-4 space-y-2">
        <h3 className="text-xs font-semibold text-fg flex items-center gap-1.5">📡 Webhook SePay</h3>
        <div className="text-[10px] text-fg-muted space-y-1 font-mono">
          <p><span className="text-fg-2 font-bold">URL:</span> <code className="bg-surface px-1 rounded">https://dvid-api.vibe1.tinhgon.xyz/api/v1/payments/sepay/webhook</code></p>
          <p><span className="text-fg-2 font-bold">Method:</span> POST · <span className="text-fg-2 font-bold">Events:</span> In (tiền vào) · <span className="text-fg-2 font-bold">Auth:</span> HMAC-SHA256</p>
          <p><span className="text-fg-2 font-bold">Lọc nội dung:</span> VGP (chỉ nhận giao dịch có mã VGP)</p>
        </div>
      </div>

      {/* Order detail drawer */}
      {selectedOrder && (
        <div className="fixed inset-0 z-50 flex justify-end" onClick={() => setSelectedOrder(null)}>
          <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
          <div
            className="relative w-full max-w-md bg-surface border-l border-line shadow-2xl overflow-y-auto animate-in slide-in-from-right"
            onClick={e => e.stopPropagation()}
          >
            {/* Drawer header */}
            <div className="sticky top-0 z-10 flex items-center justify-between px-5 py-4 border-b border-line bg-surface">
              <div>
                <h2 className="text-sm font-bold text-fg">Chi tiết đơn hàng</h2>
                <p className="text-[10px] text-fg-muted font-mono mt-0.5">{selectedOrder.id}</p>
              </div>
              <button
                onClick={() => setSelectedOrder(null)}
                className="p-1.5 rounded-lg hover:bg-surface-2 text-fg-muted transition-colors"
              >
                ✕
              </button>
            </div>

            {/* Drawer body */}
            <div className="p-5 space-y-5">
              {/* Status + badges */}
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={selectedOrder.status} />
                <RailBadge rail={selectedOrder.rail} />
                <span className="px-2 py-0.5 rounded-full bg-surface-2 text-fg-2 text-[10px] font-bold border border-line">
                  {PLAN_LABELS[selectedOrder.plan] || selectedOrder.plan}
                </span>
              </div>

              {/* Info grid */}
              <div className="rounded-xl border border-line bg-surface-2 divide-y divide-line">
                <DetailRow label="Mã VGP" value={selectedOrder.code} mono accent />
                <DetailRow label="User ID" value={selectedOrder.user_id} mono small />
                {selectedOrder.user_email && (
                  <DetailRow label="Email" value={selectedOrder.user_email} />
                )}
                <DetailRow label="Số tiền" value={formatMoney(selectedOrder.amount, selectedOrder.currency)} bold />
                {selectedOrder.paid_amount ? (
                  <DetailRow
                    label="Đã nhận"
                    value={formatMoney(selectedOrder.paid_amount, selectedOrder.currency)}
                    color={selectedOrder.paid_amount < selectedOrder.amount ? 'text-danger' : 'text-success'}
                  />
                ) : null}
                {selectedOrder.provider_ref && (
                  <DetailRow label="Provider Ref" value={selectedOrder.provider_ref} mono small />
                )}
                {selectedOrder.paypal_order_id && (
                  <DetailRow label="PayPal Order" value={selectedOrder.paypal_order_id} mono small />
                )}
                <DetailRow label="Tạo lúc" value={formatDate(selectedOrder.created_at)} />
                <DetailRow label="Hết hạn" value={formatDate(selectedOrder.expires_at)} />
                {selectedOrder.paid_at && (
                  <DetailRow label="Thanh toán lúc" value={formatDate(selectedOrder.paid_at)} color="text-success" />
                )}
                {selectedOrder.activated_at && (
                  <DetailRow label="Kích hoạt lúc" value={formatDate(selectedOrder.activated_at)} color="text-success" />
                )}
                {selectedOrder.admin_note && (
                  <DetailRow label="Ghi chú" value={selectedOrder.admin_note} />
                )}
              </div>

              {/* Activate action */}
              {(selectedOrder.status === 'pending' || selectedOrder.status === 'underpaid') && (
                <div className="space-y-2">
                  <button
                    onClick={() => handleActivate(selectedOrder.id)}
                    disabled={activatingId === selectedOrder.id}
                    className="w-full rounded-control bg-success px-4 py-2 text-xs text-white font-bold
                               hover:bg-success/80 disabled:opacity-40 transition-colors"
                  >
                    {activatingId === selectedOrder.id ? 'Đang kích hoạt...' : '✓ Kích hoạt Pro thủ công'}
                  </button>
                  {actionMsg?.id === selectedOrder.id && (
                    <p className={cn('text-xs font-mono text-center', actionMsg.ok ? 'text-success' : 'text-danger')}>
                      {actionMsg.text}
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Detail row sub-component ──────────────────────────────────────────────── */

function DetailRow({
  label,
  value,
  mono = false,
  small = false,
  bold = false,
  accent = false,
  color = '',
}: {
  label: string
  value: string
  mono?: boolean
  small?: boolean
  bold?: boolean
  accent?: boolean
  color?: string
}) {
  return (
    <div className="px-4 py-2.5 flex items-center justify-between gap-3">
      <span className="text-[10px] text-fg-muted uppercase tracking-wide shrink-0">{label}</span>
      <span
        className={cn(
          'text-right break-all',
          mono && 'font-mono',
          small ? 'text-[10px]' : 'text-xs',
          bold && 'font-bold text-fg',
          accent && 'text-accent font-bold',
          color || (!bold && !accent ? 'text-fg-2' : ''),
        )}
      >
        {value}
      </span>
    </div>
  )
}

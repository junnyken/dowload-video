import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../utils/cn'
import type { CookieItem, CookieAction } from './cookie.types'

interface MenuItemProps {
  label: string
  description?: string
  iconPath: string
  onClick: () => void
  destructive?: boolean
  disabled?: boolean
}

function MenuItem({ label, description, iconPath, onClick, destructive, disabled }: MenuItemProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'flex w-full items-start gap-2.5 px-3 py-2 text-left text-xs transition-colors',
        disabled
          ? 'cursor-not-allowed opacity-35'
          : destructive
            ? 'text-danger hover:bg-danger-soft'
            : 'text-fg-2 hover:bg-surface',
      )}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mt-0.5 h-3.5 w-3.5 flex-shrink-0"
      >
        <path d={iconPath} />
      </svg>
      <div className="min-w-0">
        <p className="font-medium leading-tight">{label}</p>
        {description && (
          <p className={cn('text-[10px] leading-tight', destructive ? 'text-danger' : 'text-fg-muted')}>
            {description}
          </p>
        )}
      </div>
    </button>
  )
}

function Divider() {
  return <div className="my-1 border-t border-line" />
}

interface CookieActionsMenuProps {
  cookie: CookieItem
  onAction: (id: string, action: CookieAction) => void
}

export function CookieActionsMenu({ cookie, onAction }: CookieActionsMenuProps) {
  const [open, setOpen] = useState(false)
  const [menuPos, setMenuPos] = useState({ top: 0, right: 0 })
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // Position the fixed dropdown below the trigger button
  useLayoutEffect(() => {
    if (!open || !btnRef.current) return
    const rect = btnRef.current.getBoundingClientRect()
    setMenuPos({
      top: rect.bottom + 4,
      right: window.innerWidth - rect.right,
    })
  }, [open])

  useEffect(() => {
    if (!open) return
    function onOut(e: MouseEvent) {
      const t = e.target as Node
      if (
        menuRef.current && !menuRef.current.contains(t) &&
        btnRef.current  && !btnRef.current.contains(t)
      ) setOpen(false)
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    function onScroll() { setOpen(false) }
    document.addEventListener('mousedown', onOut)
    document.addEventListener('keydown', onEsc)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onOut)
      document.removeEventListener('keydown', onEsc)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  function act(action: CookieAction) {
    setOpen(false)
    onAction(cookie.id, action)
  }

  const isSoftBlocked = cookie.status === 'soft_blocked'
  const isHardBlocked = cookie.status === 'hard_blocked'
  const isDisabled    = cookie.status === 'disabled'
  const isExpired     = cookie.status === 'expired'
  // Expired-by-date cookies CAN be re-tested: the date is the cookie's own
  // claim and the platform may still accept it. Disabled ones stay as the
  // admin left them; unsupported platforms have no real test to run.
  const supported     = cookie.testSupported !== false
  const canTest       = !isDisabled && supported
  const testDesc      = isDisabled
    ? 'Đang tắt — hãy bật lại trước'
    : !supported
      ? 'Chưa hỗ trợ kiểm tra thật cho nền tảng này'
      : isExpired
        ? 'Hạn trong cookie đã qua — hỏi nền tảng xem còn dùng được không'
        : 'Hỏi nền tảng xem cookie còn dùng được không'

  const dropdown = open ? (
    <div
      ref={menuRef}
      style={{ position: 'fixed', top: menuPos.top, right: menuPos.right, zIndex: 9999 }}
      className="w-52 rounded-card border border-line bg-surface shadow-card py-1 shadow-2xl"
      onClick={e => e.stopPropagation()}
    >
          {/* Test */}
          <MenuItem
            label="Kiểm tra lại"
            description={testDesc}
            iconPath="M13 10V3L4 14h7v7l9-11h-7z"
            onClick={() => act('test')}
            disabled={!canTest}
          />

          {/* Cooldown resets */}
          {(isSoftBlocked || isHardBlocked) && (
            <>
              <Divider />
              {isSoftBlocked && (
                <MenuItem
                  label="Reset Soft Cooldown"
                  description="Clear 15-min soft block now"
                  iconPath="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  onClick={() => act('reset_soft')}
                />
              )}
              {isHardBlocked && (
                <MenuItem
                  label="Force-clear Hard Block"
                  description="Skip remaining 6h cooldown"
                  iconPath="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  onClick={() => act('reset_hard')}
                />
              )}
            </>
          )}

          {/* Rotate */}
          {!isExpired && !isDisabled && (
            <MenuItem
              label="Rotate to Next Slot"
              description="Force hand-off to next cookie"
              iconPath="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"
              onClick={() => act('rotate')}
              disabled={isSoftBlocked || isHardBlocked}
            />
          )}

          <Divider />

          {/* Enable / Disable toggle */}
          {isDisabled ? (
            <MenuItem
              label="Bật lại"
              description="Cho cookie vào kho dùng lại"
              iconPath="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              onClick={() => act('enable')}
            />
          ) : (
            <MenuItem
              label="Disable Cookie"
              description="Stop using until re-enabled"
              iconPath="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
              onClick={() => act('disable')}
              disabled={isExpired}
            />
          )}

          <Divider />

          <MenuItem
            label="Delete Cookie"
            description="Permanently remove from pool"
            iconPath="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
            onClick={() => act('delete')}
            destructive
          />
        </div>
  ) : null

  return (
    <div className="relative flex justify-center">
      <button
        ref={btnRef}
        onClick={e => { e.stopPropagation(); setOpen(o => !o) }}
        className={cn(
          'flex h-6 w-6 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg-2',
          open && 'bg-surface-2 text-fg-2',
        )}
        aria-label={`Actions for ${cookie.accountLabel}`}
      >
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5">
          <circle cx="12" cy="5"  r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="12" cy="19" r="1.5" />
        </svg>
      </button>
      {dropdown && createPortal(dropdown, document.body)}
    </div>
  )
}

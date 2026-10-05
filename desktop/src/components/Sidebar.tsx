import { Download, History, ListChecks, Settings as Cog, User } from 'lucide-react';
import { nav, go, openSignIn } from '../lib/ui';
import { queue, activeCount } from '../lib/queue';
import { auth } from '../lib/auth';
import type { Screen } from '../lib/types';

const ITEMS: { id: Screen; label: string; icon: typeof Download }[] = [
  { id: 'download', label: 'Tải xuống', icon: Download },
  { id: 'queue', label: 'Hàng đợi', icon: ListChecks },
  { id: 'history', label: 'Lịch sử', icon: History },
  { id: 'settings', label: 'Cài đặt', icon: Cog },
];

export function Sidebar() {
  const { screen } = nav.use();
  const active = activeCount(queue.use());
  const a = auth.use();
  return (
    <aside className="flex w-[208px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2.5 px-4 pb-4 pt-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-accent text-accent-fg" aria-hidden>
          <Download size={17} strokeWidth={2.6} />
        </div>
        <span className="text-base font-bold tracking-tight text-fg">VidGrab</span>
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 px-2.5" aria-label="Điều hướng chính">
        {ITEMS.map(({ id, label, icon: Icon }) => {
          const on = screen === id;
          return (
            <button
              key={id}
              type="button"
              aria-current={on ? 'page' : undefined}
              onClick={() => go(id)}
              className={`flex h-10 items-center gap-3 rounded-[10px] px-3 text-sm font-medium transition-colors ${
                on ? 'bg-accent-soft text-accent-text' : 'text-fg-2 hover:bg-surface-2 hover:text-fg'
              }`}
            >
              <Icon size={18} aria-hidden />
              <span className="flex-1 text-left">{label}</span>
              {id === 'queue' && active > 0 && (
                <span aria-label={`${active} mục đang chờ hoặc đang tải`} className="min-w-5 rounded-full bg-accent px-1.5 text-center text-xs font-semibold leading-5 text-accent-fg">
                  {active}
                </span>
              )}
            </button>
          );
        })}
      </nav>
      <div className="border-t border-line p-2.5">
        <button
          type="button"
          onClick={() => (a.status === 'in' ? go('settings') : openSignIn())}
          className="flex w-full items-center gap-2.5 rounded-[10px] p-2 text-left transition-colors hover:bg-surface-2"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-sm font-semibold text-fg-2">
            {a.status === 'in' && a.email ? a.email[0].toUpperCase() : <User size={16} aria-hidden />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium text-fg">{a.status === 'in' ? a.email : 'Đăng nhập'}</span>
            <span className="block truncate text-xs text-fg-muted">{a.status === 'in' ? 'Đã đăng nhập' : 'Đồng bộ với web'}</span>
          </span>
        </button>
      </div>
    </aside>
  );
}

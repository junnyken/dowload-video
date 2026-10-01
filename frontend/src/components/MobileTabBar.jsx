import { Home, Activity, History, Zap, UserCircle } from 'lucide-react';
import { useNotifications } from '../context/NotificationContext';

const navItems = [
  { id: 'landing',  label: 'Trang chủ',   icon: Home,       path: '/' },
  { id: 'active',   label: 'Đang xử lý',  icon: Activity,   path: '/active' },
  { id: 'history',  label: 'Lịch sử',     icon: History,    path: '/history' },
  { id: 'tools',    label: 'Công cụ',     icon: Zap,        path: null },
  { id: 'account',  label: 'Tài khoản',   icon: UserCircle, path: '/preferences' },
];

export default function MobileTabBar({ activeTab, onTabChange, activeJobCount = 0 }) {
  const { unreadCount } = useNotifications();

  return (
    <nav className="md:hidden fixed bottom-0 left-0 w-full z-50 bg-surface/95 backdrop-blur-xl border-t border-line px-2 py-2 pb-safe shadow-[0_-4px_20px_rgba(0,0,0,0.25)] flex justify-around items-center">
      {navItems.map((item) => {
        const Icon = item.icon;
        const isActive = activeTab === item.id;

        return (
          <button
            key={item.id}
            onClick={() => onTabChange(item.id)}
            className="flex flex-col items-center justify-center w-full py-1 rounded-xl transition-all duration-200"
            aria-label={item.label}
            aria-current={isActive ? 'page' : undefined}
          >
            <div
              className={`relative p-1.5 rounded-xl transition-all duration-300 ${
                isActive ? 'bg-accent-soft scale-110' : 'bg-transparent'
              }`}
            >
              <Icon
                className={`w-5 h-5 sm:w-6 sm:h-6 transition-colors ${
                  isActive ? 'text-accent-text' : 'text-fg-muted hover:text-fg-2'
                }`}
              />

              {/* Notification badge on Trang chủ */}
              {item.id === 'landing' && unreadCount > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-danger rounded-full text-[9px] font-bold text-danger-fg flex items-center justify-center">
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}

              {/* Active job count badge on Đang xử lý */}
              {item.id === 'active' && activeJobCount > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-accent rounded-full text-[9px] font-bold text-accent-fg flex items-center justify-center animate-pulse">
                  {activeJobCount > 9 ? '9+' : activeJobCount}
                </span>
              )}
            </div>

            <span
              className={`text-[10px] mt-0.5 font-medium transition-all ${
                isActive ? 'text-accent-text opacity-100' : 'text-fg-muted opacity-70'
              }`}
            >
              {item.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

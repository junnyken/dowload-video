import {
  LayoutDashboard,
  History,
  Settings,
  Layers,
  Shield
} from 'lucide-react';

const navItems = [
  { id: 'dashboard', label: 'Home', icon: LayoutDashboard },
  { id: 'bulk', label: 'Bulk', icon: Layers },
  { id: 'history', label: 'History', icon: History },
  { id: 'settings', label: 'Settings', icon: Settings },
  { id: 'admin', label: 'Admin', icon: Shield },
];

export default function MobileTabBar({ activeTab, onTabChange }) {
  return (
    <nav className="md:hidden fixed bottom-0 left-0 w-full z-50 bg-surface-light border-t border-border px-2 py-2 pb-safe shadow-[0_-4px_20px_rgba(0,0,0,0.1)] flex justify-around items-center">
      {navItems.map((item) => {
        const Icon = item.icon;
        const isActive = activeTab === item.id;
        return (
          <button
            key={item.id}
            onClick={() => onTabChange(item.id)}
            className={`
              flex flex-col items-center justify-center w-full py-1 rounded-xl transition-all duration-200
              ${isActive ? 'text-primary-light' : 'text-text-muted hover:text-text-secondary'}
            `}
          >
            <div className={`
              p-1.5 rounded-xl transition-all duration-300
              ${isActive ? 'bg-primary/15 scale-110' : 'bg-transparent'}
            `}>
              <Icon className="w-5 h-5 sm:w-6 sm:h-6" />
            </div>
            <span className={`text-[10px] mt-1 font-medium transition-all ${isActive ? 'opacity-100 translate-y-0' : 'opacity-70 translate-y-0'}`}>
              {item.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

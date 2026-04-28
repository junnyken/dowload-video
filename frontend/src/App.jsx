import { useState, useEffect } from 'react';
import { Video, Settings } from 'lucide-react';
import LandingPage from './components/LandingPage';
import SettingsContent from './components/SettingsContent';
import AdminDashboard from './pages/Admin/AdminDashboard';
import AdminLogin from './components/AdminLogin';

function App() {
  const [view, setView] = useState('landing');
  const [isAdminRoute, setIsAdminRoute] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  // Check URL path on mount
  useEffect(() => {
    const path = window.location.pathname;
    if (path === '/vid-admin') {
      setIsAdminRoute(true);
      setView('admin');
    }
    
    // Listen for history changes if needed
    const handlePopState = () => {
      if (window.location.pathname === '/vid-admin') {
        setIsAdminRoute(true);
        setView('admin');
      } else {
        setIsAdminRoute(false);
        setView('landing');
      }
    };
    
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigateTo = (newView, path = '/') => {
    setView(newView);
    window.history.pushState({}, '', path);
    if (path === '/vid-admin') {
      setIsAdminRoute(true);
    } else {
      setIsAdminRoute(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#012622] text-slate-100">
      {/* ── Top Navbar (Glassmorphism) ──────────────────── */}
      <nav className="fixed top-0 inset-x-0 z-50 backdrop-blur-xl bg-[#012622]/70 border-b border-slate-700/50">
        <div className="max-w-6xl mx-auto h-14 md:h-16 px-4 md:px-8 flex items-center justify-between">
          {/* Logo */}
          <button
            onClick={() => navigateTo('landing', '/')}
            className="flex items-center gap-2.5 group cursor-pointer"
          >
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#FBBF24] to-[#FB923C] flex items-center justify-center shadow-md shadow-[#FBBF24]/20 group-hover:shadow-lg transition-shadow">
              <Video className="w-5 h-5 text-[#012622]" />
            </div>
            <span className="text-lg font-extrabold text-white tracking-tight">
              VidGrab
            </span>
          </button>

          {/* Right Nav - Hidden from normal users! Only show Settings if they are Admin */}
          <div className="flex items-center gap-1">
            {isAuthenticated && (
              <button
                onClick={() => setView(view === 'settings' ? 'admin' : 'settings')}
                className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
                  view === 'settings'
                    ? 'bg-white/10 text-white'
                    : 'text-slate-400 hover:text-white hover:bg-white/5'
                }`}
              >
                <Settings className="w-4 h-4" />
                <span className="hidden sm:inline">Cài đặt API</span>
              </button>
            )}
            
            {/* If authenticated and not on admin view, show link to go back */}
            {isAuthenticated && view !== 'admin' && (
              <button
                onClick={() => navigateTo('admin', '/vid-admin')}
                className="ml-2 bg-primary hover:bg-primary-hover text-[#012622] px-3 py-1.5 rounded-lg text-xs font-bold transition-colors cursor-pointer"
              >
                Trở về Admin
              </button>
            )}
          </div>
        </div>
      </nav>

      {/* ── Main Content ────────────────────────────────── */}
      <main className="pt-14 md:pt-16">
        {view === 'landing' && <LandingPage />}
        
        {view === 'settings' && isAuthenticated && (
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <SettingsContent />
          </div>
        )}
        
        {view === 'admin' && (
          <div className="max-w-6xl mx-auto px-4 md:px-8 py-8 md:py-12">
            {!isAuthenticated ? (
              <AdminLogin onLogin={() => setIsAuthenticated(true)} />
            ) : (
              <AdminDashboard />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;

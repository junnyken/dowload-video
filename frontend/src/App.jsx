import { useState } from 'react';
import { Video, Settings, Shield } from 'lucide-react';
import LandingPage from './components/LandingPage';
import SettingsContent from './components/SettingsContent';
import AdminDashboard from './pages/Admin/AdminDashboard';

function App() {
  const [view, setView] = useState('landing');

  return (
    <div className="min-h-screen bg-[#000000] text-slate-100">
      {/* ── Top Navbar (Glassmorphism) ──────────────────── */}
      <nav className="fixed top-0 inset-x-0 z-50 backdrop-blur-2xl bg-black/60 border-b border-white/[0.08]">
        <div className="max-w-6xl mx-auto h-14 md:h-16 px-4 md:px-8 flex items-center justify-between">
          {/* Logo */}
          <button
            onClick={() => setView('landing')}
            className="flex items-center gap-2.5 group cursor-pointer"
          >
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#4F46E5] to-[#7C3AED] flex items-center justify-center shadow-md shadow-[#4F46E5]/20 group-hover:shadow-lg transition-shadow">
              <Video className="w-5 h-5 text-white" />
            </div>
            <span className="text-lg font-extrabold text-white tracking-tight">
              VidGrab
            </span>
          </button>

          {/* Right Nav */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setView(view === 'settings' ? 'landing' : 'settings')}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
                view === 'settings'
                  ? 'bg-white/10 text-white'
                  : 'text-slate-400 hover:text-white hover:bg-white/5'
              }`}
            >
              <Settings className="w-4 h-4" />
              <span className="hidden sm:inline">Cài đặt</span>
            </button>
            <button
              onClick={() => setView(view === 'admin' ? 'landing' : 'admin')}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
                view === 'admin'
                  ? 'bg-white/10 text-white'
                  : 'text-slate-400 hover:text-white hover:bg-white/5'
              }`}
            >
              <Shield className="w-4 h-4" />
              <span className="hidden sm:inline">Admin</span>
            </button>
          </div>
        </div>
      </nav>

      {/* ── Main Content ────────────────────────────────── */}
      <main className="pt-14 md:pt-16">
        {view === 'landing' && <LandingPage />}
        {view === 'settings' && (
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <SettingsContent />
          </div>
        )}
        {view === 'admin' && (
          <div className="max-w-6xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <AdminDashboard />
          </div>
        )}
      </main>
    </div>
  );
}

export default App;

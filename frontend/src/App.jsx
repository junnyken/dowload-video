import { useState, useEffect } from 'react';
import { Video, Search, Languages } from 'lucide-react';
import SearchPage from './pages/SearchPage';
import { AuthProvider, useAuth } from './context/AuthContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import LandingPage from './components/LandingPage';
import ExtensionPage from './components/ExtensionPage';
import SettingsContent from './components/SettingsContent';
import AdminDashboard from './pages/Admin/AdminDashboard';
import PrivacyPolicy from './pages/PrivacyPolicy';
import AdminLogin from './components/AdminLogin';
import AccountMenu from './components/AccountMenu';
import AuthModal from './components/auth/AuthModal';
import PreferencesContent from './components/PreferencesContent';
import UsageContent from './components/UsageContent';
import UserHistoryContent from './components/UserHistoryContent';
import PlaylistsPage from './pages/PlaylistsPage';
import AnalyticsPage from './pages/AnalyticsPage';
import ArchivePage from './pages/ArchivePage';
import SchedulePage from './pages/SchedulePage';
import WorkspaceSwitcher from './components/WorkspaceSwitcher';
import WorkspaceSettingsPage from './pages/WorkspaceSettingsPage';
import AuditLogPage from './pages/AuditLogPage';
import ApprovalQueuePage from './pages/ApprovalQueuePage';
import FeedbackModal from './components/FeedbackModal';
import ApiDocsPage from './pages/ApiDocsPage';
import ApiKeysPage from './pages/ApiKeysPage';
import LinkBotPage from './pages/LinkBotPage';
import InstallPage from './pages/InstallPage';
import PlatformsPage from './pages/PlatformsPage';
import PricingPage from './pages/PricingPage';
import BillingPage from './pages/BillingPage';
import TranscriptTranslatePage from './pages/TranscriptTranslatePage';
import TranscriptAsrPage from './pages/TranscriptAsrPage';
import ErrorBoundary from './components/ErrorBoundary';
import { NotificationProvider } from './context/NotificationContext';
import NotificationCenter from './components/NotificationCenter';
import ShareTargetHandler from './components/ShareTargetHandler';
import ExtensionInstallBanner from './components/ExtensionInstallBanner';
import PWAInstallBanner from './components/PWAInstallBanner';
import { usePWAInstall } from './hooks/usePWAInstall';
import MobileTabBar from './components/MobileTabBar';
import ActiveJobsMobile from './components/ActiveJobsMobile';
import MobileShareIntake from './components/MobileShareIntake';
import MobileQuickTools from './components/MobileQuickTools';

// ── Path → view mapping ──────────────────────────────────────────────
const PATH_MAP = {
  '/':                    'landing',
  '/extension':           'extension',
  '/preferences':         'preferences',
  '/usage':               'usage',
  '/history':             'history',
  '/playlists':           'playlists',
  '/analytics':           'analytics',
  '/archive':             'archive',
  '/schedule':            'schedule',
  '/transcript-translate': 'transcript-translate',
  '/transcript-asr':      'transcript-asr',
  '/workspace-settings':  'workspace-settings',
  '/audit':               'audit',
  '/approvals':           'approvals',
  '/vid-admin':           'admin',
  '/privacy':             'privacy',
  '/api-docs':            'api-docs',
  '/api-keys':            'api-keys',
  '/link-bot':            'link-bot',
  '/install':             'install',
  '/platforms':           'platforms',
  '/share-target':        'landing',
  '/pricing':             'pricing',
  '/billing':             'billing',
  '/active':              'active',
  '/search':              'search',
};

function AppInner() {
  const { isAuthenticated, loading } = useAuth();
  const [view, setView]           = useState('landing');
  const [isAdminRoute, setIsAdminRoute] = useState(false);
  const [adminAuth, setAdminAuth] = useState(() => !!sessionStorage.getItem('admin_token'));
  const [adminToken, setAdminToken] = useState(() => sessionStorage.getItem('admin_token') || null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [showFeedback, setShowFeedback]   = useState(false);
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [swUpdateReady, setSwUpdateReady] = useState(false);
  const [swRegistration, setSwRegistration] = useState(null);
  const [pwaInstallReady, setPwaInstallReady] = useState(!!window.__pwaInstallPrompt);
  const { isInstalled, isIOS, showBanner: showPwaBanner, triggerInstall, dismiss: dismissPWA } = usePWAInstall();
  const [showMobileTools, setShowMobileTools] = useState(false);
  const [activeJobCount, setActiveJobCount]   = useState(0);

  useEffect(() => {
    const goOnline  = () => setIsOnline(true);
    const goOffline = () => setIsOnline(false);
    window.addEventListener('online',  goOnline);
    window.addEventListener('offline', goOffline);
    return () => {
      window.removeEventListener('online',  goOnline);
      window.removeEventListener('offline', goOffline);
    };
  }, []);

  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.getRegistration().then((reg) => {
      if (!reg) return;
      setSwRegistration(reg);
      if (reg.waiting) setSwUpdateReady(true);
      reg.addEventListener('updatefound', () => {
        const newWorker = reg.installing;
        if (!newWorker) return;
        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
            setSwUpdateReady(true);
          }
        });
      });
    });
    const handleControllerChange = () => window.location.reload();
    navigator.serviceWorker.addEventListener('controllerchange', handleControllerChange);
    return () => navigator.serviceWorker.removeEventListener('controllerchange', handleControllerChange);
  }, []);

  const handleSwUpdate = () => {
    if (swRegistration && swRegistration.waiting) {
      swRegistration.waiting.postMessage({ type: 'SKIP_WAITING' });
    }
    setSwUpdateReady(false);
  };

  useEffect(() => {
    const onAvailable = () => setPwaInstallReady(true);
    const onInstalled = () => setPwaInstallReady(false);
    window.addEventListener('pwa-install-available', onAvailable);
    window.addEventListener('pwa-installed', onInstalled);
    return () => {
      window.removeEventListener('pwa-install-available', onAvailable);
      window.removeEventListener('pwa-installed', onInstalled);
    };
  }, []);

  const handlePwaInstall = async () => {
    if (!window.__pwaInstallPrompt) return;
    try {
      window.__pwaInstallPrompt.prompt();
      await window.__pwaInstallPrompt.userChoice;
    } finally {
      // Clear on both accept and dismiss — a BeforeInstallPromptEvent can
      // only be prompted once, so leaving it set breaks the button on retry.
      window.__pwaInstallPrompt = null;
      setPwaInstallReady(false);
    }
  };

  useEffect(() => {
    const path = window.location.pathname;
    const v = PATH_MAP[path] || 'landing';
    setView(v);
    setIsAdminRoute(v === 'admin');

    const handlePopState = () => {
      const v2 = PATH_MAP[window.location.pathname] || 'landing';
      setView(v2);
      setIsAdminRoute(v2 === 'admin');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Handle SW_NAVIGATE messages from service worker (push notification deep-link)
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    const handleSwMsg = (event) => {
      if (event.data?.type === 'SW_NAVIGATE') {
        const path = (event.data.url || '/').split('?')[0];
        const v = PATH_MAP[path] || 'landing';
        setView(v);
        window.history.pushState({}, '', event.data.url || '/');
      }
    };
    navigator.serviceWorker.addEventListener('message', handleSwMsg);
    return () => navigator.serviceWorker.removeEventListener('message', handleSwMsg);
  }, []);

  const navigateTo = (newView, path = '/') => {
    // Guard: authenticated-only views
    if (['preferences', 'usage', 'history', 'analytics', 'archive', 'schedule',
         'workspace-settings', 'audit', 'approvals', 'api-keys', 'active',
         'transcript-translate', 'transcript-asr'].includes(newView) && !isAuthenticated) {
      setShowAuthModal(true);
      return;
    }
    setView(newView);
    setIsAdminRoute(newView === 'admin');
    window.history.pushState({}, '', path);
  };

  const mobileActiveTab = (() => {
    if (view === 'active')      return 'active';
    if (view === 'history')     return 'history';
    if (view === 'preferences') return 'account';
    return 'landing';
  })();

  const handleMobileTabChange = (tabId) => {
    if (tabId === 'tools') { setShowMobileTools(true); return; }
    const map = {
      landing: ['landing',     '/'],
      active:  ['active',      '/active'],
      history: ['history',     '/history'],
      account: ['preferences', '/preferences'],
    };
    const [v, p] = map[tabId] || ['landing', '/'];
    navigateTo(v, p);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#012622] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-[#FBBF24] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <NotificationProvider>
    <div className="min-h-screen bg-[#012622] text-slate-100">
      <ShareTargetHandler />
      {/* ── Offline Banner ────────────────────────────────── */}
      {!isOnline && (
        <div className="fixed bottom-0 inset-x-0 z-[60] bg-red-900/95 backdrop-blur-sm border-t border-red-700/50 flex items-center justify-center gap-2 py-2 px-4 text-sm text-red-100">
          <span className="inline-block w-2 h-2 rounded-full bg-red-400 animate-pulse" />
          Đang offline — Kết nối internet để tiếp tục tải video
        </div>
      )}
      <ExtensionInstallBanner />
      {/* ── SW Update Banner ─────────────────────────────── */}
      {swUpdateReady && (
        <div className="fixed bottom-0 inset-x-0 z-[59] bg-[#1a3a2a]/95 backdrop-blur-sm border-t border-[#FBBF24]/30 flex items-center justify-center gap-3 py-2 px-4 text-sm text-slate-200">
          <span className="text-[#FBBF24] font-semibold">Phiên bản mới khả dụng</span>
          <button
            onClick={handleSwUpdate}
            className="px-3 py-1 rounded-lg bg-[#FBBF24] text-[#012622] text-xs font-bold hover:opacity-90 transition cursor-pointer"
          >
            Cập nhật ngay
          </button>
          <button
            onClick={() => setSwUpdateReady(false)}
            className="text-slate-400 hover:text-slate-200 text-xs cursor-pointer"
          >
            Bỏ qua
          </button>
        </div>
      )}
      {/* ── Top Navbar ───────────────────────────────────── */}
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
            <span className="text-lg font-extrabold text-white tracking-tight">VidGrab</span>
          </button>

          {/* Right Nav */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigateTo('search', '/search')}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors cursor-pointer ${
                view === 'search'
                  ? 'border-[#FBBF24]/50 bg-[#FBBF24]/10 text-[#FBBF24]'
                  : 'border-slate-600/50 text-slate-300 hover:bg-slate-700/40'
              }`}
              title="Tìm kiếm video"
            >
              <Search className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Tìm kiếm</span>
            </button>
            <button
              onClick={() => navigateTo('platforms', '/platforms')}
              className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-600/50 text-slate-300 text-xs font-medium hover:bg-slate-700/40 transition-colors cursor-pointer"
              title="Nền tảng được hỗ trợ"
            >
              Platforms
            </button>

            <button
              onClick={() => navigateTo('extension', '/extension')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#FBBF24]/40 text-[#FBBF24] text-xs font-bold hover:bg-[#FBBF24]/10 transition-colors cursor-pointer"
            >
              Extension
            </button>

            {isAuthenticated && (
              <>
                <WorkspaceSwitcher onNavigate={navigateTo} />
                <button
                  onClick={() => navigateTo('archive', '/archive')}
                  className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-600/50 text-slate-300 text-xs font-bold hover:bg-slate-700/50 hover:text-white transition-colors cursor-pointer"
                >
                  Archive
                </button>
                <button
                  onClick={() => navigateTo('schedule', '/schedule')}
                  className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-600/50 text-slate-300 text-xs font-bold hover:bg-slate-700/50 hover:text-white transition-colors cursor-pointer"
                >
                  Lịch Tải
                </button>
                <button
                  onClick={() => navigateTo('transcript-translate', '/transcript-translate')}
                  className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-600/50 text-slate-300 text-xs font-bold hover:bg-slate-700/50 hover:text-white transition-colors cursor-pointer"
                  title="Dịch transcript .srt/.vtt sang ngôn ngữ khác"
                >
                  <Languages className="w-3.5 h-3.5" />
                  Dịch Phụ Đề
                </button>
              </>
            )}

            {isAuthenticated ? (
              <>
                <NotificationCenter />
                <AccountMenu onNavigate={navigateTo} />
              </>
            ) : (
              <button
                onClick={() => setShowAuthModal(true)}
                className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-xs font-bold hover:opacity-90 transition cursor-pointer"
              >
                Đăng nhập
              </button>
            )}

            {pwaInstallReady && (
              <button
                onClick={handlePwaInstall}
                className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-600/50 text-slate-300 text-xs font-bold hover:bg-slate-700/50 hover:text-white transition-colors cursor-pointer"
                title="Cài ứng dụng VidGrab về máy"
              >
                Cài ứng dụng
              </button>
            )}
            {adminAuth && view !== 'admin' && (
              <button
                onClick={() => navigateTo('admin', '/vid-admin')}
                className="bg-primary hover:bg-primary-hover text-[#012622] px-3 py-1.5 rounded-lg text-xs font-bold transition-colors cursor-pointer"
              >
                Admin
              </button>
            )}
          </div>
        </div>
      </nav>

      {/* ── Main Content ─────────────────────────────────── */}
      <main className="pt-14 md:pt-16 pb-20 md:pb-0">
      <ErrorBoundary>
        {view === 'landing'      && <LandingPage />}
        {view === 'extension'    && <ExtensionPage />}

        {view === 'preferences' && isAuthenticated && (
          <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <PreferencesContent />
          </div>
        )}

        {view === 'usage' && isAuthenticated && (
          <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <UsageContent />
          </div>
        )}

        {view === 'history' && isAuthenticated && (
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <UserHistoryContent onNavigate={navigateTo} />
          </div>
        )}

        {view === 'playlists' && (
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <PlaylistsPage onNavigate={navigateTo} />
          </div>
        )}

        {view === 'analytics' && isAuthenticated && (
          <div className="max-w-5xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <AnalyticsPage />
          </div>
        )}

        {view === 'archive' && isAuthenticated && (
          <ArchivePage onNavigate={navigateTo} />
        )}

        {view === 'schedule' && isAuthenticated && (
          <SchedulePage onNavigate={navigateTo} />
        )}

        {view === 'transcript-translate' && isAuthenticated && (
          <TranscriptTranslatePage />
        )}

        {view === 'transcript-asr' && isAuthenticated && (
          <TranscriptAsrPage />
        )}

        {view === 'workspace-settings' && isAuthenticated && (
          <WorkspaceSettingsPage />
        )}

        {view === 'audit' && isAuthenticated && (
          <AuditLogPage />
        )}

        {view === 'approvals' && isAuthenticated && (
          <ApprovalQueuePage />
        )}

        {view === 'settings' && (
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <SettingsContent />
          </div>
        )}

        {view === 'admin' && (
          <div className="max-w-6xl mx-auto px-4 md:px-8 py-8 md:py-12">
            {!adminAuth ? (
              <AdminLogin onLogin={(token) => {
                setAdminAuth(true);
                setAdminToken(token);
                sessionStorage.setItem('admin_token', token);
              }} />
            ) : (
              <AdminDashboard token={adminToken} onLogout={() => {
                setAdminAuth(false);
                setAdminToken(null);
                sessionStorage.removeItem('admin_token');
              }} />
            )}
          </div>
        )}

        {view === 'privacy' && (
          <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <PrivacyPolicy />
          </div>
        )}

        {view === 'api-docs' && (
          <ApiDocsPage />
        )}

        {view === 'api-keys' && isAuthenticated && (
          <ApiKeysPage />
        )}

        {view === 'link-bot' && (
          <div className="max-w-lg mx-auto px-4 md:px-8 py-8 md:py-12">
            <LinkBotPage />
          </div>
        )}

        {view === 'install' && (
          <InstallPage />
        )}
        {view === 'platforms' && (
          <PlatformsPage />
        )}
        {/* pricing temporarily hidden — renders nothing; nav button removed */}
        {view === 'billing' && (
          <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <BillingPage onNavigate={navigateTo} />
          </div>
        )}

        {view === 'active' && isAuthenticated && (
          <ActiveJobsMobile onNavigate={navigateTo} onActiveCountChange={setActiveJobCount} />
        )}
        {view === 'search' && (
          <SearchPage onNavigate={navigateTo} />
        )}
      </ErrorBoundary>
      </main>

      {/* ── Feedback Button ──────────────────────────────── */}
      {!isAdminRoute && (
        <button
          onClick={() => setShowFeedback(true)}
          className="fixed bottom-24 right-4 z-40 md:bottom-6 md:right-6 flex items-center gap-2 px-4 py-2.5 rounded-full bg-[#1a4a42] border border-slate-600/50 text-slate-300 text-xs font-semibold hover:bg-[#FBBF24]/15 hover:border-[#FBBF24]/40 hover:text-[#FBBF24] shadow-lg transition-all duration-200 cursor-pointer"
          title="Góp ý & Phản hồi"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
          </svg>
          Góp ý
        </button>
      )}

      {/* ── Feedback Modal ───────────────────────────────── */}
      {showFeedback && (
        <FeedbackModal onClose={() => setShowFeedback(false)} />
      )}

      {/* ── Auth Modal ────────────────────────────────────── */}
      {showAuthModal && (
        <AuthModal onClose={() => setShowAuthModal(false)} />
      )}

      {showPwaBanner && !isInstalled && (
        <PWAInstallBanner isIOS={isIOS} onInstall={triggerInstall} onDismiss={dismissPWA} />
      )}

      {/* ── Mobile Tab Bar ───────────────────────────────── */}
      {!isAdminRoute && (
        <MobileTabBar
          activeTab={mobileActiveTab}
          onTabChange={handleMobileTabChange}
          activeJobCount={activeJobCount}
        />
      )}

      {/* ── Mobile Quick Tools Sheet ─────────────────────── */}
      <MobileQuickTools
        show={showMobileTools}
        onClose={() => setShowMobileTools(false)}
        onNavigate={navigateTo}
      />

      {/* ── Mobile Share Intake Overlay ──────────────────── */}
      <MobileShareIntake onNavigate={navigateTo} />
    </div>
    </NotificationProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <WorkspaceProvider>
        <AppInner />
      </WorkspaceProvider>
    </AuthProvider>
  );
}

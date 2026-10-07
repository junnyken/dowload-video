import { useState, useEffect } from 'react';
import { Video, Search, Languages, Menu, X } from 'lucide-react';
import SearchPage from './pages/SearchPage';
import { AuthProvider, useAuth } from './context/AuthContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import LandingPage from './components/LandingPage';
import ExtensionPage from './components/ExtensionPage';
import SettingsContent from './components/SettingsContent';
import ResetPasswordPage from './pages/ResetPasswordPage';
import DesktopLoginPage from './pages/DesktopLoginPage';
import PrivacyPolicy from './pages/PrivacyPolicy';
import AccountMenu from './components/AccountMenu';
import ThemeToggle from './components/ThemeToggle';
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
// PricingPage is not imported: the route that reached it renders nothing and
// was removed from PATH_MAP below. The file is kept — restoring the page means
// re-adding this import, the PATH_MAP entry, and a render branch.
import BillingPage from './pages/BillingPage';
import SubtitleHubPage from './pages/SubtitleHubPage';
import PlatformLandingPage from './pages/PlatformLandingPage';
import { platformPages } from './content/platformPages';
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
  '/phu-de':              'subtitle-hub',
  '/transcript-translate': 'transcript-translate',
  '/transcript-asr':      'transcript-asr',
  '/workspace-settings':  'workspace-settings',
  '/audit':               'audit',
  '/approvals':           'approvals',
  '/reset-password':      'reset-password',
  // Opened by the Windows app's "Đăng nhập qua trình duyệt" (task #6039).
  '/desktop-login':       'desktop-login',
  '/privacy':             'privacy',
  '/api-docs':            'api-docs',
  '/api-keys':            'api-keys',
  '/link-bot':            'link-bot',
  '/install':             'install',
  '/platforms':           'platforms',
  '/share-target':        'landing',
  // '/pricing' removed from the map on purpose. The view it pointed at
  // renders nothing (see the comment further down), so a bookmarked or
  // shared /pricing link showed a blank page. Unmapped paths fall back to
  // 'landing', which is a page.
  '/billing':             'billing',
  '/active':              'active',
  '/search':              'search',
};

// SEO landing pages per platform (copy: content/platformPages.js). View id = 'p:<slug>'.
for (const p of platformPages) PATH_MAP[`/${p.slug}`] = `p:${p.slug}`;

function AppInner() {
  const { isAuthenticated, loading } = useAuth();
  const [view, setView]           = useState('landing');
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
    // Reload only when an existing worker is replaced (a real update). On the
    // very first install there is nothing stale to refresh, and reloading then
    // wiped state such as a link just received via /share-target.
    const hadController = !!navigator.serviceWorker.controller;
    const handleControllerChange = () => { if (hadController) window.location.reload(); };
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

    const handlePopState = () => {
      const v2 = PATH_MAP[window.location.pathname] || 'landing';
      setView(v2);
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
         'transcript-translate', 'transcript-asr', 'subtitle-hub'].includes(newView) && !isAuthenticated) {
      setShowAuthModal(true);
      return;
    }
    setView(newView);
    window.history.pushState({}, '', path);
  };

  const [menuOpen, setMenuOpen] = useState(false);
  // Any navigation closes the collapsed menu; so does Escape.
  useEffect(() => { setMenuOpen(false); }, [view]);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [menuOpen]);
  const goTo = (v, path = '/') => { setMenuOpen(false); navigateTo(v, path); };

  const navItems = [
    { key: 'search', label: 'Tìm kiếm', title: 'Tìm kiếm video', icon: Search, active: view === 'search', onClick: () => goTo('search', '/search') },
    { key: 'platforms', label: 'Platforms', title: 'Nền tảng được hỗ trợ', active: view === 'platforms', onClick: () => goTo('platforms', '/platforms') },
    { key: 'extension', label: 'Extension', title: 'Tiện ích trình duyệt', active: view === 'extension', onClick: () => goTo('extension', '/extension') },
    ...(isAuthenticated ? [
      { key: 'archive', label: 'Archive', active: view === 'archive', onClick: () => goTo('archive', '/archive') },
      { key: 'schedule', label: 'Lịch Tải', active: view === 'schedule', onClick: () => goTo('schedule', '/schedule') },
      { key: 'subtitle-hub', label: 'Phụ đề & Phiên âm', title: 'Trích phụ đề, phiên âm AI và dịch phụ đề', icon: Languages, active: ['subtitle-hub', 'transcript-asr', 'transcript-translate'].includes(view), onClick: () => goTo('subtitle-hub', '/phu-de') },
    ] : []),
    ...(pwaInstallReady ? [
      { key: 'pwa', label: 'Cài ứng dụng', title: 'Cài ứng dụng VidGrab về máy', active: false, onClick: () => { setMenuOpen(false); handlePwaInstall(); } },
    ] : []),
  ];

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
      <div className="min-h-screen bg-canvas flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <NotificationProvider>
    <div className="min-h-screen bg-canvas text-fg">
      <ShareTargetHandler />
      {/* ── Offline Banner ────────────────────────────────── */}
      {!isOnline && (
        <div className="fixed bottom-0 inset-x-0 z-[60] bg-danger-soft backdrop-blur-sm border-t border-danger/50 flex items-center justify-center gap-2 py-2 px-4 text-sm text-danger">
          <span className="inline-block w-2 h-2 rounded-full bg-danger animate-pulse" />
          Đang offline — Kết nối internet để tiếp tục tải video
        </div>
      )}
      <ExtensionInstallBanner />
      {/* ── SW Update Banner ─────────────────────────────── */}
      {swUpdateReady && (
        <div className="fixed bottom-0 inset-x-0 z-[59] bg-surface-2/95 backdrop-blur-sm border-t border-accent/30 flex items-center justify-center gap-3 py-2 px-4 text-sm text-fg-2">
          <span className="text-accent-text font-semibold">Phiên bản mới khả dụng</span>
          <button
            onClick={handleSwUpdate}
            className="px-3 py-1 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:opacity-90 transition cursor-pointer"
          >
            Cập nhật ngay
          </button>
          <button
            onClick={() => setSwUpdateReady(false)}
            className="text-fg-muted hover:text-fg-2 text-xs cursor-pointer"
          >
            Bỏ qua
          </button>
        </div>
      )}
      {/* ── Top Navbar ───────────────────────────────────── */}
      <nav className="fixed top-0 inset-x-0 z-50 backdrop-blur-xl bg-surface/80 border-b border-line">
        <div className="max-w-6xl mx-auto h-14 md:h-16 px-4 md:px-8 flex items-center justify-between gap-3">
          {/* Logo */}
          <button
            onClick={() => goTo('landing', '/')}
            className="flex items-center gap-2.5 cursor-pointer flex-shrink-0"
          >
            <div className="w-8 h-8 rounded-control bg-accent flex items-center justify-center">
              <Video className="w-4 h-4 text-accent-fg" />
            </div>
            <span className="text-lg font-semibold text-fg tracking-tight">VidGrab</span>
          </button>

          {/* Inline links — desktop only (>= 1024px) */}
          <div className="hidden lg:flex items-stretch h-full gap-1 flex-1 justify-center">
            {navItems.map((item) => (
              <button
                key={item.key}
                onClick={item.onClick}
                title={item.title}
                aria-current={item.active ? 'page' : undefined}
                className={`relative inline-flex items-center gap-1.5 px-3 text-sm font-medium transition-colors cursor-pointer ${
                  item.active
                    ? 'text-fg after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-accent'
                    : 'text-fg-2 hover:text-fg'
                }`}
              >
                {item.icon && <item.icon className="w-3.5 h-3.5" />}
                {item.label}
              </button>
            ))}
          </div>

          {/* Right cluster */}
          <div className="flex items-center gap-2 flex-shrink-0">
            {isAuthenticated && (
              <div className="hidden lg:block">
                <WorkspaceSwitcher onNavigate={navigateTo} />
              </div>
            )}

            <ThemeToggle />

            {isAuthenticated ? (
              <>
                <NotificationCenter />
                <AccountMenu onNavigate={navigateTo} />
              </>
            ) : (
              <button
                onClick={() => setShowAuthModal(true)}
                className="px-3 py-1.5 rounded-control bg-accent text-accent-fg text-xs font-semibold hover:bg-accent-hover transition-colors cursor-pointer"
              >
                Đăng nhập
              </button>
            )}

            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label="Menu"
              aria-expanded={menuOpen}
              aria-controls="mobile-nav-sheet"
              className="lg:hidden inline-flex items-center justify-center w-8 h-8 rounded-lg border border-line text-fg-2 hover:bg-surface-2 hover:text-fg transition-colors cursor-pointer"
            >
              {menuOpen ? <X className="w-4 h-4" aria-hidden="true" /> : <Menu className="w-4 h-4" aria-hidden="true" />}
            </button>
          </div>
        </div>

        {/* Collapsed menu sheet (< 1024px) */}
        {menuOpen && (
          <div
            id="mobile-nav-sheet"
            className="lg:hidden absolute top-full inset-x-0 bg-surface border-b border-line shadow-lg max-h-[calc(100vh-3.5rem)] overflow-y-auto"
          >
            <div className="max-w-6xl mx-auto px-4 md:px-8 py-2 flex flex-col">
              {navItems.map((item) => (
                <button
                  key={item.key}
                  onClick={item.onClick}
                  aria-current={item.active ? 'page' : undefined}
                  className={`flex items-center gap-2.5 px-3 py-3 rounded-control text-sm font-medium text-left transition-colors cursor-pointer ${
                    item.active ? 'bg-accent-soft text-accent-text' : 'text-fg-2 hover:bg-surface-2 hover:text-fg'
                  }`}
                >
                  {item.icon && <item.icon className="w-4 h-4" />}
                  {item.label}
                </button>
              ))}
              {isAuthenticated && (
                <div className="empty:hidden px-3 py-3 border-t border-line mt-1">
                  <WorkspaceSwitcher onNavigate={goTo} />
                </div>
              )}
            </div>
          </div>
        )}
      </nav>

      {/* ── Main Content ─────────────────────────────────── */}
      <main className="pt-14 md:pt-16 pb-20 md:pb-0">
      <ErrorBoundary>
        {view === 'landing'      && <LandingPage />}
        {view.startsWith('p:')   && <PlatformLandingPage slug={view.slice(2)} />}
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

        {view === 'subtitle-hub' && isAuthenticated && (
          <SubtitleHubPage onNavigate={navigateTo} />
        )}

        {/* Legacy deep links render the same hub on the matching tab. */}
        {view === 'transcript-translate' && isAuthenticated && (
          <SubtitleHubPage initialTab="translate" onNavigate={navigateTo} />
        )}

        {view === 'transcript-asr' && isAuthenticated && (
          <SubtitleHubPage initialTab="asr" onNavigate={navigateTo} />
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

        {view === 'reset-password' && (
          <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <ResetPasswordPage />
          </div>
        )}

        {view === 'desktop-login' && (
          <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
            <DesktopLoginPage />
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
        {/* pricing temporarily hidden — renders nothing; nav button removed.
            The four call sites that still navigated here were repointed on
            2026-09-24: PaywallGate and SmartActionsPanel go to /billing, and
            BillingPage's two buttons open UpgradeModal. Re-exposing this page
            means restoring the /pricing entry in PATH_MAP above. */}
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
      {(
        <button
          onClick={() => setShowFeedback(true)}
          aria-label="Góp ý & Phản hồi"
          className="fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] right-3 z-40 md:bottom-6 md:right-6 flex items-center justify-center gap-2 w-10 h-10 sm:w-auto sm:h-auto sm:px-4 sm:py-2.5 rounded-full bg-surface-2 border border-line-strong text-fg-2 text-xs font-semibold hover:bg-accent-soft hover:border-accent/40 hover:text-accent-text shadow-lg transition-all duration-200 cursor-pointer"
          title="Góp ý & Phản hồi"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
          </svg>
          <span className="hidden sm:inline">Góp ý</span>
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
      {(
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

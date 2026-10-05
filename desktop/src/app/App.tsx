import { useEffect } from 'react';
import { Sidebar } from '../components/Sidebar';
import { ConfirmDialog, Toasts } from '../components/Toasts';
import { SignInModal } from '../components/SignInModal';
import { DownloadScreen } from '../screens/DownloadScreen';
import { QueueScreen } from '../screens/QueueScreen';
import { HistoryScreen } from '../screens/HistoryScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { nav } from '../lib/ui';
import { applyTheme, settings } from '../lib/settings';
import { initAuth } from '../lib/auth';
import { initQueue } from '../lib/queue';
import { initSync, refreshPending } from '../lib/sync';
import { mockMode } from '../lib/tauri';

let booted = false;

export function App() {
  const { screen, signIn } = nav.use();
  settings.use(); // re-render when theme changes (applyTheme is also called in updateSettings)

  useEffect(() => {
    applyTheme();
    const mq = matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', applyTheme);
    if (!booted) {
      booted = true;
      initSync();
      void initQueue();
      void initAuth();
      void refreshPending();
    }
    return () => mq.removeEventListener('change', applyTheme);
  }, []);

  return (
    <div className="flex h-full min-w-[900px] bg-canvas text-fg">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-hidden">
        {mockMode && (
          <div className="bg-warning-soft px-3 py-1 text-center text-xs text-warning">Chế độ mô phỏng trong trình duyệt (chỉ dành cho phát triển)</div>
        )}
        <div className={mockMode ? 'h-[calc(100%-26px)]' : 'h-full'}>
          {screen === 'download' && <DownloadScreen />}
          {screen === 'queue' && <QueueScreen />}
          {screen === 'history' && <HistoryScreen />}
          {screen === 'settings' && <SettingsScreen />}
        </div>
      </main>
      {signIn && <SignInModal />}
      <ConfirmDialog />
      <Toasts />
    </div>
  );
}

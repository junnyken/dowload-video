import { useState, useEffect } from 'react';
import {
  Download, Layers, History, Wand2,
  Sparkles, Smartphone, X, ShieldCheck, Puzzle
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { hasUsedBefore } from '../lib/returningUser';
import DashboardContent from './DashboardContent';
import BulkContent from './BulkContent';
import HistoryContent from './HistoryContent';
import FlowVeoCleanup from './FlowVeoCleanup';

// ── Platform Icons ──────────────────────────────────────────
const TikTokIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1v-3.5a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.15 15.2a6.34 6.34 0 0010.86 4.46V13.2a8.16 8.16 0 005.58 2.2v-3.45a4.85 4.85 0 01-3.77-1.49V6.69h3.77z"/></svg>;
const XIcon = () => <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>;
const FacebookIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M24 12.07C24 5.41 18.63 0 12 0S0 5.41 0 12.07c0 6.02 4.39 11.01 10.13 11.93v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.79-4.69 4.53-4.69 1.31 0 2.69.24 2.69.24v2.97h-1.51c-1.49 0-1.96.93-1.96 1.89v2.26h3.33l-.53 3.49h-2.8v8.44C19.61 23.08 24 18.09 24 12.07z"/></svg>;
const InstagramIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85 0 3.2-.01 3.58-.07 4.85-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.65.07-4.85.07-3.2 0-3.58-.01-4.85-.07-3.26-.15-4.77-1.7-4.92-4.92-.06-1.27-.07-1.65-.07-4.85 0-3.2.01-3.58.07-4.85C2.38 3.86 3.9 2.31 7.15 2.23 8.42 2.17 8.8 2.16 12 2.16zM12 0C8.74 0 8.33.01 7.05.07 2.7.27.27 2.7.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.2 4.36 2.62 6.78 6.98 6.98C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c4.35-.2 6.78-2.62 6.98-6.98.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.2-4.35-2.63-6.78-6.98-6.98C15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1018.16 12 6.16 6.16 0 0012 5.84zM12 16a4 4 0 110-8 4 4 0 010 8zm6.41-11.85a1.44 1.44 0 100 2.88 1.44 1.44 0 000-2.88z"/></svg>;
const YouTubeIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M23.5 6.19a3.02 3.02 0 00-2.12-2.14C19.54 3.5 12 3.5 12 3.5s-7.54 0-9.38.55A3.02 3.02 0 00.5 6.19 31.6 31.6 0 000 12a31.6 31.6 0 00.5 5.81 3.02 3.02 0 002.12 2.14c1.84.55 9.38.55 9.38.55s7.54 0 9.38-.55a3.02 3.02 0 002.12-2.14A31.6 31.6 0 0024 12a31.6 31.6 0 00-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/></svg>;
const SpotifyIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.84.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.02.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.239.54-.959.72-1.56.3z"/></svg>;
const DouyinIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1v-3.5a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.15 15.2a6.34 6.34 0 0010.86 4.46V13.2a8.16 8.16 0 005.58 2.2v-3.45a4.85 4.85 0 01-3.77-1.49V6.69h3.77z"/></svg>;
const RedditIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M12 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0zm5.01 4.744c.688 0 1.25.561 1.25 1.249a1.25 1.25 0 0 1-2.498.056l-2.597-.547-.8 3.747c1.824.07 3.48.632 4.674 1.488.308-.309.73-.491 1.207-.491.968 0 1.754.786 1.754 1.754 0 .716-.435 1.333-1.01 1.614a3.111 3.111 0 0 1 .042.52c0 2.694-3.13 4.87-7.004 4.87-3.874 0-7.004-2.176-7.004-4.87 0-.183.015-.366.043-.534A1.748 1.748 0 0 1 4.028 12c0-.968.786-1.754 1.754-1.754.463 0 .898.196 1.207.49 1.207-.883 2.878-1.43 4.744-1.487l.885-4.182a.342.342 0 0 1 .14-.197.35.35 0 0 1 .238-.042l2.906.617a1.214 1.214 0 0 1 1.108-.701zM9.25 12C8.561 12 8 12.562 8 13.25c0 .687.561 1.248 1.25 1.248.687 0 1.248-.561 1.248-1.249 0-.688-.561-1.249-1.249-1.249zm5.5 0c-.687 0-1.248.561-1.248 1.25 0 .687.561 1.248 1.249 1.248.688 0 1.249-.561 1.249-1.249 0-.687-.562-1.249-1.25-1.249zm-5.466 3.99a.327.327 0 0 0-.231.094.33.33 0 0 0 0 .463c.842.842 2.484.913 2.961.913.477 0 2.105-.056 2.961-.913a.361.361 0 0 0 .029-.463.33.33 0 0 0-.464 0c-.547.533-1.684.73-2.512.73-.828 0-1.979-.196-2.512-.73a.326.326 0 0 0-.232-.095z"/></svg>;
const PinterestIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M12 0C5.373 0 0 5.373 0 12c0 5.084 3.163 9.426 7.627 11.174-.105-.949-.2-2.405.042-3.441.218-.937 1.407-5.965 1.407-5.965s-.359-.719-.359-1.782c0-1.668.967-2.914 2.171-2.914 1.023 0 1.518.769 1.518 1.69 0 1.029-.655 2.568-.994 3.995-.283 1.194.599 2.169 1.777 2.169 2.133 0 3.772-2.249 3.772-5.495 0-2.873-2.064-4.882-5.012-4.882-3.414 0-5.418 2.561-5.418 5.207 0 1.031.397 2.138.893 2.738a.36.36 0 0 1 .083.345l-.333 1.36c-.053.22-.174.267-.402.161-1.499-.698-2.436-2.889-2.436-4.649 0-3.785 2.75-7.262 7.929-7.262 4.163 0 7.398 2.967 7.398 6.931 0 4.136-2.607 7.464-6.227 7.464-1.216 0-2.359-.632-2.75-1.378l-.748 2.853c-.271 1.043-1.002 2.35-1.492 3.146C9.57 23.812 10.763 24 12 24c6.627 0 12-5.373 12-12S18.627 0 12 0z"/></svg>;

const platforms = [
  { icon: TikTokIcon, label: 'TikTok', bg: 'bg-[#00f2fe]/10 text-black border-[#00f2fe]/20', special: true },
  { icon: DouyinIcon, label: 'Douyin', bg: 'bg-[#ff0050]/10 text-[#ff0050] border-[#ff0050]/20', special: true },
  { icon: YouTubeIcon, label: 'YouTube', bg: 'bg-[#FF0000]/10 text-[#FF0000] border-[#FF0000]/20' },
  { icon: FacebookIcon, label: 'Facebook', bg: 'bg-[#1877F2]/10 text-[#1877F2] border-[#1877F2]/20' },
  { icon: InstagramIcon, label: 'Instagram', bg: 'bg-gradient-to-tr from-[#f09433] via-[#e6683c] to-[#bc1888] text-white border-transparent' },
  { icon: SpotifyIcon, label: 'Spotify', bg: 'bg-[#1DB954]/10 text-[#1DB954] border-[#1DB954]/20' },
  { icon: XIcon, label: 'X / Twitter', bg: 'bg-white/5 text-white border-white/15' },
  { icon: RedditIcon, label: 'Reddit', bg: 'bg-[#FF4500]/10 text-[#FF4500] border-[#FF4500]/20' },
  { icon: PinterestIcon, label: 'Pinterest', bg: 'bg-[#E60023]/10 text-[#E60023] border-[#E60023]/20' },
];

const tabs = [
  { id: 'single',  label: 'Tải Video & Nhạc', shortLabel: 'Video & Nhạc', icon: Download },
  { id: 'bulk',    label: 'Hàng Loạt · Kênh', shortLabel: 'Hàng Loạt',   icon: Layers  },
  { id: 'flow',    label: 'Xóa Logo Video',       shortLabel: 'Xóa Logo',     icon: Wand2   },
  { id: 'history', label: 'Đã Tải',             shortLabel: 'Đã Tải',       icon: History },
];

export default function LandingPage() {
  const { isAuthenticated } = useAuth();

  // Read once, at mount: this must not flip mid-session and reflow the page
  // under someone who is reading it.
  const [returning] = useState(hasUsedBefore);
  const compactHero = isAuthenticated || returning;

  const [activeTab, setActiveTab] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.has('batch') ? 'bulk' : 'single';
  });

  // ── PWA Install Prompt ────────────────────────────────────
  const [showInstall, setShowInstall] = useState(false);
  const [showIosGuide, setShowIosGuide] = useState(false);
  const _isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const _isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const _7DAYS = 7 * 24 * 60 * 60 * 1000;
  const [installDismissed, setInstallDismissed] = useState(() => {
    const ts = localStorage.getItem('pwa-install-dismissed-at');
    if (!ts) return false;
    return Date.now() - parseInt(ts, 10) < _7DAYS;
  });

  const [shareShown, setShareShown] = useState(() => {
    try { return !!sessionStorage.getItem('vg_share_shown'); } catch { return false; }
  });

  useEffect(() => {
    // iOS: show manual guide if not already installed as standalone
    if (_isIOS && !_isStandalone && !installDismissed) {
      setShowIosGuide(true);
    }

    const handleAvailable = () => {
      if (!installDismissed) setShowInstall(true);
    };
    const handleInstalled = () => setShowInstall(false);

    // Check if prompt is already available
    if (window.__pwaInstallPrompt && !installDismissed) setShowInstall(true);

    window.addEventListener('pwa-install-available', handleAvailable);
    window.addEventListener('pwa-installed', handleInstalled);
    return () => {
      window.removeEventListener('pwa-install-available', handleAvailable);
      window.removeEventListener('pwa-installed', handleInstalled);
    };
  }, [installDismissed]);

  const handleInstall = async () => {
    const prompt = window.__pwaInstallPrompt;
    if (!prompt) return;
    prompt.prompt();
    const result = await prompt.userChoice;
    if (result.outcome === 'accepted') {
      setShowInstall(false);
    }
    window.__pwaInstallPrompt = null;
  };

  const dismissInstall = () => {
    setShowInstall(false);
    setShowIosGuide(false);
    setInstallDismissed(true);
    localStorage.setItem('pwa-install-dismissed-at', Date.now().toString());
  };

  return (
    <div className="min-h-screen relative overflow-hidden pb-24">
      {/* ── PWA Install Banner — disabled: App.jsx handles this via usePWAInstall hook ── */}
      {/* {showInstall && (
        <div className="fixed top-16 inset-x-0 z-40 flex justify-center px-4 animate-in slide-in-from-top duration-300">
          <div className="max-w-lg w-full flex items-center gap-3 px-4 py-3 bg-gradient-to-r from-[#012622] to-[#0a2a25] border border-[#A3E635]/40 rounded-2xl shadow-2xl backdrop-blur-xl">
            <div className="p-2 bg-[#A3E635]/10 rounded-xl">
              <Smartphone className="w-5 h-5 text-[#A3E635]" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-white">Cài đặt VidGrab</p>
              <p className="text-xs text-slate-400 truncate">Truy cập nhanh từ màn hình chính</p>
            </div>
            <button
              onClick={handleInstall}
              className="px-4 py-2 bg-[#A3E635] text-[#012622] text-xs font-extrabold rounded-xl hover:bg-[#bef264] transition-colors cursor-pointer whitespace-nowrap"
            >
              Cài đặt
            </button>
            <button onClick={dismissInstall} className="p-1.5 text-slate-500 hover:text-white transition-colors cursor-pointer">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )} */}
      {/* ── iOS "Add to Home Screen" Guide ────────────────── */}
      {showIosGuide && !showInstall && (
        <div className="fixed top-16 inset-x-0 z-40 flex justify-center px-4 animate-in slide-in-from-top duration-300">
          <div className="max-w-lg w-full flex items-center gap-3 px-4 py-3 bg-gradient-to-r from-[#012622] to-[#0a2a25] border border-[#A3E635]/40 rounded-2xl shadow-2xl backdrop-blur-xl">
            <div className="p-2 bg-[#A3E635]/10 rounded-xl">
              <Smartphone className="w-5 h-5 text-[#A3E635]" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-white">Cài VidGrab trên iPhone</p>
              <p className="text-xs text-slate-400">Nhấn <span className="text-[#A3E635] font-bold">⬆ Chia sẻ</span> → <span className="text-white font-semibold">Thêm vào Màn hình chính</span></p>
            </div>
            <button onClick={dismissInstall} className="p-1.5 text-slate-500 hover:text-white transition-colors cursor-pointer">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
      {/* Floating Support Button — tạm ẩn */}
      {/* <
        href="#"
        className="fixed bottom-8 right-4 md:bottom-6 md:right-8 bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] px-5 py-2.5 rounded-full shadow-xl flex items-center gap-2 hover:scale-105 transition-all duration-300 z-50 font-bold text-sm"
      >
        <Heart className="w-4 h-4 fill-[#012622]" />
        Ủng hộ
      </a> */}

      {/* Main container */}
      <div className={`relative z-10 w-full max-w-4xl mx-auto px-4 sm:px-6 flex flex-col items-center ${
        compactHero ? 'pt-6 md:pt-10' : 'pt-20 md:pt-28'
      }`}>

        {/* Hero Section — full pitch on a first visit, one line after that */}
        <section className={`w-full flex flex-col items-center text-center ${
          compactHero ? 'mb-3 md:mb-4' : 'mb-6 md:mb-10'
        }`}>
          {/* Context label — kept in both: it is what the product does, in one line */}
          <div className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/25 text-xs font-bold text-emerald-400 tracking-wide max-w-full ${
            compactHero ? 'mb-0' : 'mb-5'
          }`}>
            <Sparkles className="w-3 h-3 flex-shrink-0" />
            <span className="truncate">TikTok / Douyin không watermark · 30+ nền tảng</span>
          </div>

          {/* Everything below is the introduction. A returning visitor has
              read it; the platform list lives on /platforms and the extension
              stays in the nav, so nothing here is only reachable from the
              hero. */}
          {!compactHero && (
          <>
          <h1 className="text-4xl sm:text-5xl md:text-7xl font-black tracking-tighter leading-tight text-white mb-4">
            Bắt trọn video.{' '}
            <span className="bg-gradient-to-r from-[#FBBF24] to-[#FB923C] bg-clip-text text-transparent">
              Sạch, không logo.
            </span>
          </h1>


          {typeof window !== 'undefined' && !window.matchMedia('(display-mode: standalone)').matches && (
            <p className="text-xs text-white/30 text-center mt-2 sm:hidden">
              💡 Thêm vào màn hình chính để mở nhanh hơn
            </p>
          )}

          {/* Platform pills — icon + label, visible and scannable */}
          <div className="flex flex-wrap items-center justify-center gap-1.5 sm:gap-2 mb-4">
            {platforms.map((p, i) => (
              <div
                key={i}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold ${p.bg} hover:scale-105 transition-transform cursor-default${p.special ? ' ring-1 ring-emerald-500/50' : ''}`}
              >
                <p.icon />
                <span className="hidden sm:inline">{p.label}</span>
              </div>
            ))}
          </div>

          {/* Extension link — secondary, compact.
              It said that already, while being styled as the loudest button on
              the page: an amber gradient with a shadow and a hover scale, the
              same weight as BÓC TÁCH NGAY further down. Two primary CTAs, and
              the louder one was not the job people came here to do. Now it
              looks like the secondary link this comment always described.

              The Chrome mark used to be an <img> hotlinked from Wikimedia —
              a third-party request on the home screen that leaves a broken
              icon if it ever 403s. Puzzle is the browser-extension glyph and
              comes from the same lucide set as every other icon here (lucide
              1.11 has no Chrome icon, and hand-drawing a brand mark is worse
              than not using one). */}
          <a
            href="/extension"
            onClick={(e) => {
              e.preventDefault();
              window.history.pushState({}, '', '/extension');
              window.dispatchEvent(new PopStateEvent('popstate'));
            }}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-[#FBBF24] border border-[#FBBF24]/40 hover:bg-[#FBBF24]/10 rounded-full transition-colors"
          >
            <Puzzle className="w-4 h-4" />
            Cài Extension Chrome — TikTok sạch 1 click
          </a>
          </>
          )}

          {/* Product depth cues — kept in both. Two of these are navigation
              (Bulk, History), not decoration, and they are the only pointer
              to those tabs from a compact hero. */}
          <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1.5 mt-3 text-[11px] text-slate-600">
            <button
              onClick={() => setActiveTab('bulk')}
              className="inline-flex items-center gap-1.5 hover:text-slate-400 transition-colors"
            >
              <Layers className="w-3 h-3" />
              Hàng loạt · kênh · ZIP
            </button>
            <span aria-hidden="true">·</span>
            <span className="inline-flex items-center gap-1.5">
              <Download className="w-3 h-3" />
              4K · 1080p · MP3 320kbps
            </span>
            <span aria-hidden="true">·</span>
            <button
              onClick={() => setActiveTab('history')}
              className="inline-flex items-center gap-1.5 hover:text-slate-400 transition-colors"
            >
              <History className="w-3 h-3" />
              Lịch sử tải
            </button>
            <span aria-hidden="true">·</span>
            <span className="inline-flex items-center gap-1.5 text-emerald-500/70">
              <ShieldCheck className="w-3 h-3" />
              Không quảng cáo · không giới hạn giả
            </span>
          </div>
        </section>

        {/* Tab Switcher */}
        <div className="w-full flex justify-center mb-4 md:mb-6 px-4 sm:px-0">
          <div className="inline-flex max-w-full overflow-x-auto bg-[#012622]/50 rounded-2xl p-1.5 sm:p-2 shadow-md border border-slate-700/50 gap-1 sm:gap-2 backdrop-blur-md">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              const isHistory = tab.id === 'history';
              const isBulk = tab.id === 'bulk';
              const isFlow = tab.id === 'flow';
              const isSmall = isHistory || isFlow;
              const sizeClasses = isSmall
                ? 'text-xs sm:text-sm px-3 py-2 sm:px-5 sm:py-2.5'
                : 'text-sm sm:text-base md:text-lg px-4 py-3 sm:px-8 sm:py-4';
              const colorClasses = isActive
                ? 'bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/30'
                : isHistory
                  ? 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
                  : isFlow
                    ? 'text-emerald-500/60 hover:text-emerald-400 hover:bg-emerald-500/10'
                    : isBulk
                      ? 'text-slate-400 hover:text-white hover:bg-white/10'
                      : 'text-slate-200 hover:text-white hover:bg-white/10';
              const titleMap = {
                single:  'Tải 1 link — video, nhạc, phụ đề',
                bulk:    'Tải nhiều link, kênh, playlist cùng lúc',
                flow:    'Xóa logo watermark trên video Flow / Veo',
                history: 'Lịch sử các lượt tải gần đây',
              };
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  title={titleMap[tab.id]}
                  className={`flex items-center gap-2 rounded-xl font-bold transition-all duration-200 cursor-pointer whitespace-nowrap ${sizeClasses} ${colorClasses}`}
                >
                  <Icon className={isSmall ? 'w-4 h-4' : 'w-5 h-5'} />
                  <span className="hidden sm:inline">{tab.label}</span>
                  <span className="sm:hidden">{tab.shortLabel}</span>
                  {isFlow && !isActive && (
                    <span className="ml-0.5 text-[8px] font-extrabold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400 leading-none tracking-wide">
                      New
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Tab Content */}
        <div className="w-full">
          {activeTab === 'single'  && <DashboardContent />}
          {activeTab === 'bulk'    && <BulkContent />}
          {activeTab === 'flow'    && <FlowVeoCleanup />}
          {activeTab === 'history' && <HistoryContent />}
        </div>

        {/* Share prompt */}
        {false && !shareShown && (
          <div className="mt-4 flex items-center justify-between bg-white/5 border border-white/10 rounded-xl px-4 py-3">
            <p className="text-sm text-white/50">Tìm thấy hữu ích? Chia sẻ với bạn bè</p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  try { navigator.share?.({ title: 'VidGrab', url: 'https://dowloadvideo.io.vn' }); } catch {}
                  setShareShown(true);
                  try { sessionStorage.setItem('vg_share_shown', '1'); } catch {}
                }}
                className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg font-medium transition-colors flex-shrink-0"
              >
                Chia sẻ
              </button>
              <button
                onClick={() => { setShareShown(true); try { sessionStorage.setItem('vg_share_shown', '1'); } catch {} }}
                className="text-white/30 hover:text-white/50 text-xs transition-colors"
              >✕</button>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

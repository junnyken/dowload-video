import { useState } from 'react';
import {
  Download, Layers, History, Heart,
  Zap, Ban, ShieldCheck
} from 'lucide-react';
import DashboardContent from './DashboardContent';
import BulkContent from './BulkContent';
import HistoryContent from './HistoryContent';

// ── Platform Icons ──────────────────────────────────────────
const TikTokIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1v-3.5a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.15 15.2a6.34 6.34 0 0010.86 4.46V13.2a8.16 8.16 0 005.58 2.2v-3.45a4.85 4.85 0 01-3.77-1.49V6.69h3.77z"/></svg>;
const XIcon = () => <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>;
const FacebookIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M24 12.07C24 5.41 18.63 0 12 0S0 5.41 0 12.07c0 6.02 4.39 11.01 10.13 11.93v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.79-4.69 4.53-4.69 1.31 0 2.69.24 2.69.24v2.97h-1.51c-1.49 0-1.96.93-1.96 1.89v2.26h3.33l-.53 3.49h-2.8v8.44C19.61 23.08 24 18.09 24 12.07z"/></svg>;
const InstagramIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85 0 3.2-.01 3.58-.07 4.85-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.65.07-4.85.07-3.2 0-3.58-.01-4.85-.07-3.26-.15-4.77-1.7-4.92-4.92-.06-1.27-.07-1.65-.07-4.85 0-3.2.01-3.58.07-4.85C2.38 3.86 3.9 2.31 7.15 2.23 8.42 2.17 8.8 2.16 12 2.16zM12 0C8.74 0 8.33.01 7.05.07 2.7.27.27 2.7.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.2 4.36 2.62 6.78 6.98 6.98C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c4.35-.2 6.78-2.62 6.98-6.98.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.2-4.35-2.63-6.78-6.98-6.98C15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1018.16 12 6.16 6.16 0 0012 5.84zM12 16a4 4 0 110-8 4 4 0 010 8zm6.41-11.85a1.44 1.44 0 100 2.88 1.44 1.44 0 000-2.88z"/></svg>;
const YouTubeIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M23.5 6.19a3.02 3.02 0 00-2.12-2.14C19.54 3.5 12 3.5 12 3.5s-7.54 0-9.38.55A3.02 3.02 0 00.5 6.19 31.6 31.6 0 000 12a31.6 31.6 0 00.5 5.81 3.02 3.02 0 002.12 2.14c1.84.55 9.38.55 9.38.55s7.54 0 9.38-.55a3.02 3.02 0 002.12-2.14A31.6 31.6 0 0024 12a31.6 31.6 0 00-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z"/></svg>;
const SpotifyIcon = () => <svg viewBox="0 0 24 24" className="w-6 h-6" fill="currentColor"><path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.84.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.02.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.239.54-.959.72-1.56.3z"/></svg>;

const platforms = [
  { icon: TikTokIcon, label: 'TikTok', bg: 'bg-[#00f2fe]/10 text-black border-[#00f2fe]/20' },
  { icon: XIcon, label: 'X', bg: 'bg-black text-white border-black' },
  { icon: FacebookIcon, label: 'Facebook', bg: 'bg-[#1877F2]/10 text-[#1877F2] border-[#1877F2]/20' },
  { icon: InstagramIcon, label: 'Instagram', bg: 'bg-gradient-to-tr from-[#f09433] via-[#e6683c] to-[#bc1888] text-white border-transparent' },
  { icon: YouTubeIcon, label: 'YouTube', bg: 'bg-[#FF0000]/10 text-[#FF0000] border-[#FF0000]/20' },
  { icon: SpotifyIcon, label: 'Spotify', bg: 'bg-[#1DB954]/10 text-[#1DB954] border-[#1DB954]/20' },
];

const tabs = [
  { id: 'single', label: 'Tải Video & Nhạc', shortLabel: 'Video & Nhạc', icon: Download },
  { id: 'bulk', label: 'Tải Hàng Loạt', shortLabel: 'Hàng Loạt', icon: Layers },
  { id: 'history', label: 'Lịch Sử', shortLabel: 'Lịch Sử', icon: History },
];

export default function LandingPage() {
  const [activeTab, setActiveTab] = useState('single');

  return (
    <div className="min-h-screen relative overflow-hidden pb-24">
      {/* Floating Support Button */}
      <a
        href="#"
        className="fixed bottom-6 right-4 md:right-8 bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] px-5 py-2.5 rounded-full shadow-xl flex items-center gap-2 hover:scale-105 transition-all duration-300 z-50 font-bold text-sm"
      >
        <Heart className="w-4 h-4 fill-[#012622]" />
        Ủng hộ
      </a>

      {/* Main container */}
      <div className="relative z-10 w-full max-w-4xl mx-auto px-4 sm:px-6 pt-20 md:pt-28 flex flex-col items-center">

        {/* Hero Section */}
        <section className="w-full flex flex-col items-center text-center mb-12 md:mb-20">
          <div className="inline-flex items-center gap-2.5 px-6 py-3 mb-8 rounded-full bg-[#012622] border border-[#A3E635]/50 shadow-md text-sm font-black text-[#A3E635] uppercase tracking-widest max-w-full">
            <Zap className="w-5 h-5 text-[#A3E635] fill-[#A3E635] flex-shrink-0" />
            <span className="truncate">VŨ KHÍ TỐI THƯỢNG CHO CONTENT CREATOR</span>
          </div>

          <h1 className="text-4xl sm:text-5xl md:text-7xl font-black tracking-tighter leading-tight text-white mb-6">
            Bắt trọn video.{' '}
            <span className="bg-gradient-to-r from-[#FBBF24] to-[#FB923C] bg-clip-text text-transparent">
              Nét căng
            </span>
          </h1>

          <p className="max-w-2xl text-sm sm:text-base md:text-lg text-slate-300 font-medium leading-relaxed mb-10">
            Bóc tách video không logo và nhạc nền 320kbps từ TikTok, Douyin, YouTube chỉ trong 1 click. Giữ nguyên chất lượng gốc, sẵn sàng cho bạn sáng tạo.
          </p>

          {/* Feature Badges */}
          <div className="flex flex-wrap items-center justify-center gap-3 mb-10">
            <span className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-[#4ADE80]/10 text-[#4ADE80] text-sm font-semibold border border-[#4ADE80]/30">
              <Ban className="w-4 h-4" /> Không quảng cáo
            </span>
            <span className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-[#FDE047]/10 text-[#FDE047] text-sm font-semibold border border-[#FDE047]/30">
              <ShieldCheck className="w-4 h-4" /> An toàn & Bảo mật
            </span>
            <span className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-[#A3E635]/10 text-[#A3E635] text-sm font-semibold border border-[#A3E635]/30">
              <Zap className="w-4 h-4" /> Siêu tốc
            </span>
          </div>

          {/* Platform Icons */}
          <div className="flex flex-wrap items-center justify-center gap-4">
            {platforms.map((p, i) => (
              <div key={i} className={`w-12 h-12 md:w-14 md:h-14 rounded-full flex items-center justify-center border ${p.bg} hover:scale-110 transition-transform cursor-pointer`}>
                <p.icon />
              </div>
            ))}
          </div>
        </section>

        {/* Tab Switcher */}
        <div className="w-full flex justify-center mb-8 md:mb-12">
          <div className="inline-flex bg-[#012622]/50 rounded-2xl p-1.5 sm:p-2 shadow-md border border-slate-700/50 gap-1 sm:gap-2 backdrop-blur-md">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2.5 px-4 py-3 sm:px-8 sm:py-4 rounded-xl text-sm sm:text-base md:text-lg font-bold transition-all duration-200 cursor-pointer whitespace-nowrap ${
                    isActive
                      ? 'bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/30'
                      : 'text-slate-300 hover:text-white hover:bg-white/10'
                  }`}
                >
                  <Icon className="w-5 h-5" />
                  <span className="hidden sm:inline">{tab.label}</span>
                  <span className="sm:hidden">{tab.shortLabel}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Tab Content */}
        <div className="w-full">
          {activeTab === 'single' && <DashboardContent />}
          {activeTab === 'bulk' && <BulkContent />}
          {activeTab === 'history' && <HistoryContent />}
        </div>

      </div>
    </div>
  );
}

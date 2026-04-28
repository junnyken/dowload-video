import React from 'react';
import { Sparkles, Check, X, Shield, Zap, Infinity, Music, Crown } from 'lucide-react';

export default function UpgradeModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-in fade-in duration-200">
      <div className="relative w-full max-w-4xl bg-[#0a0a0a] border border-white/[0.10] rounded-3xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300">

        {/* Glow Effects */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-indigo-500/10 blur-[100px] rounded-full pointer-events-none" />
        <div className="absolute bottom-0 right-0 w-[300px] h-[300px] bg-violet-500/10 blur-[80px] rounded-full pointer-events-none" />

        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-2 text-slate-500 hover:text-white hover:bg-white/[0.08] rounded-full transition-colors z-10"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="relative p-8 md:p-10 text-center">

          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-[#4F46E5] to-[#7C3AED] mb-6 shadow-lg shadow-indigo-500/30">
            <Crown className="w-8 h-8 text-white" />
          </div>

          <h2 className="text-3xl font-bold text-white tracking-tight mb-3">
            Choose Your Plan
          </h2>

          <p className="text-sm text-slate-400 mb-10 max-w-xl mx-auto">
            Upgrade to unlock premium audio quality, unlimited bulk downloads, and batch zipping capabilities instantly.
          </p>

          <div className="grid md:grid-cols-2 gap-6 text-left">

            {/* Free Tier */}
            <div className="bg-white/[0.03] border border-white/[0.08] p-8 rounded-3xl relative overflow-hidden flex flex-col">
              <h3 className="text-xl font-bold text-white mb-2">Free</h3>
              <div className="text-3xl font-bold text-white mb-6">$0<span className="text-lg text-slate-500 font-normal">/mo</span></div>

              <ul className="space-y-4 mb-8 flex-1">
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-slate-500 mt-0.5" />
                  <span className="text-sm text-slate-400">5 downloads per day</span>
                </li>
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-slate-500 mt-0.5" />
                  <span className="text-sm text-slate-400">Basic API speeds</span>
                </li>
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-slate-500 mt-0.5" />
                  <span className="text-sm text-slate-400">128kbps Audio extraction</span>
                </li>
                <li className="flex items-start gap-3 opacity-50">
                  <X className="w-5 h-5 text-red-400 mt-0.5" />
                  <span className="text-sm text-slate-500 line-through">Batch Playlist Zipping</span>
                </li>
              </ul>

              <button
                onClick={onClose}
                className="w-full py-3.5 px-6 rounded-xl bg-white/[0.04] border border-white/[0.08] text-white font-bold text-sm hover:bg-white/[0.08] transition-all duration-200"
              >
                Current Plan
              </button>
            </div>

            {/* VIP Tier */}
            <div className="bg-gradient-to-b from-indigo-500/10 to-transparent border border-indigo-500/50 p-8 rounded-3xl relative overflow-hidden flex flex-col shadow-lg shadow-indigo-500/5">
              <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-[#4F46E5] to-[#7C3AED]" />
              <div className="absolute top-6 right-6 px-3 py-1 bg-indigo-500/20 text-indigo-300 text-xs font-bold uppercase tracking-wider rounded-lg border border-indigo-500/30">
                Most Popular
              </div>

              <h3 className="text-xl font-bold text-white mb-2">VIP Snaptube</h3>
              <div className="text-3xl font-bold text-white mb-6">$9.99<span className="text-lg text-slate-500 font-normal">/mo</span></div>

              <ul className="space-y-4 mb-8 flex-1">
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-indigo-400 mt-0.5" />
                  <span className="text-sm text-white font-medium">Unlimited daily downloads</span>
                </li>
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-indigo-400 mt-0.5" />
                  <span className="text-sm text-white font-medium">Ultra-fast proxy speeds</span>
                </li>
                <li className="flex items-start gap-3">
                  <Music className="w-5 h-5 text-indigo-400 mt-0.5" />
                  <span className="text-sm text-white font-medium">320kbps Studio Audio (Spotify/YT)</span>
                </li>
                <li className="flex items-start gap-3">
                  <Zap className="w-5 h-5 text-indigo-400 mt-0.5" />
                  <span className="text-sm text-white font-medium">1-Click Batch Playlist Zipping</span>
                </li>
              </ul>

              <button
                onClick={async () => {
                   try {
                     await fetch('http://localhost:8000/api/v1/payments/webhook', {
                       method: 'POST',
                       headers: { 'Content-Type': 'application/json' },
                       body: JSON.stringify({ user_id: '127.0.0.1', tier: 'vip', duration_days: 30, transaction_id: 'test_demo_123' })
                     });
                     alert("Mock Mua Hàng Thành Công! Reloading...");
                     window.location.reload();
                   } catch (e) {
                     alert("Vui lòng khởi động backend API server cổng 8000.");
                   }
                }}
                className="w-full py-3.5 px-6 rounded-xl bg-gradient-to-r from-[#4F46E5] to-[#7C3AED] text-white font-bold text-sm shadow-lg shadow-indigo-500/30 hover:shadow-indigo-500/50 hover:-translate-y-0.5 transition-all duration-200"
              >
                Upgrade to VIP
              </button>
            </div>

          </div>

          <p className="mt-8 text-xs text-slate-600">
            Secure processing. Cancel anytime.
          </p>
        </div>
      </div>
    </div>
  );
}

import React from 'react';
import { Sparkles, Check, X, Shield, Zap, Infinity, Music, Crown } from 'lucide-react';

export default function UpgradeModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-4xl bg-surface-card border border-primary/30 rounded-3xl shadow-2xl overflow-hidden shadow-primary/20 animate-in zoom-in-95 duration-300">
        
        {/* Glow Effects */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-primary/10 blur-[100px] rounded-full pointer-events-none" />
        <div className="absolute bottom-0 right-0 w-[300px] h-[300px] bg-accent/10 blur-[80px] rounded-full pointer-events-none" />

        {/* Close Button */}
        <button 
          onClick={onClose}
          className="absolute top-4 right-4 p-2 text-text-muted hover:text-text-primary hover:bg-surface rounded-full transition-colors z-10"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="relative p-8 md:p-10 text-center">
          
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary to-accent mb-6 shadow-lg shadow-primary/30">
            <Crown className="w-8 h-8 text-white" />
          </div>

          <h2 className="text-3xl font-bold text-text-primary tracking-tight mb-3">
            Choose Your Plan
          </h2>
          
          <p className="text-sm text-text-secondary mb-10 max-w-xl mx-auto">
            Upgrade to unlock premium audio quality, unlimited bulk downloads, and batch zipping capabilities instantly.
          </p>

          <div className="grid md:grid-cols-2 gap-6 text-left">
            
            {/* Free Tier */}
            <div className="bg-surface/30 border border-border p-8 rounded-3xl relative overflow-hidden flex flex-col">
              <h3 className="text-xl font-bold text-text-primary mb-2">Free</h3>
              <div className="text-3xl font-bold text-text-primary mb-6">$0<span className="text-lg text-text-muted font-normal">/mo</span></div>
              
              <ul className="space-y-4 mb-8 flex-1">
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-text-muted mt-0.5" />
                  <span className="text-sm text-text-secondary">5 downloads per day</span>
                </li>
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-text-muted mt-0.5" />
                  <span className="text-sm text-text-secondary">Basic API speeds</span>
                </li>
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-text-muted mt-0.5" />
                  <span className="text-sm text-text-secondary">128kbps Audio extraction</span>
                </li>
                <li className="flex items-start gap-3 opacity-50">
                  <X className="w-5 h-5 text-error mt-0.5" />
                  <span className="text-sm text-text-muted line-through">Batch Playlist Zipping</span>
                </li>
              </ul>
              
              <button
                onClick={onClose}
                className="w-full py-3.5 px-6 rounded-xl bg-surface border border-border text-text-primary font-bold text-sm hover:bg-surface-lighter transition-all duration-200"
              >
                Current Plan
              </button>
            </div>

            {/* VIP Tier */}
            <div className="bg-gradient-to-b from-primary/10 to-transparent border border-primary/50 p-8 rounded-3xl relative overflow-hidden flex flex-col shadow-lg shadow-primary/5">
              <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-primary to-accent" />
              <div className="absolute top-6 right-6 px-3 py-1 bg-primary/20 text-primary text-xs font-bold uppercase tracking-wider rounded-lg border border-primary/30">
                Most Popular
              </div>
              
              <h3 className="text-xl font-bold text-text-primary mb-2">VIP Snaptube</h3>
              <div className="text-3xl font-bold text-text-primary mb-6">$9.99<span className="text-lg text-text-muted font-normal">/mo</span></div>
              
              <ul className="space-y-4 mb-8 flex-1">
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-primary mt-0.5" />
                  <span className="text-sm text-text-primary font-medium">Unlimited daily downloads</span>
                </li>
                <li className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-primary mt-0.5" />
                  <span className="text-sm text-text-primary font-medium">Ultra-fast proxy speeds</span>
                </li>
                <li className="flex items-start gap-3">
                  <Music className="w-5 h-5 text-primary mt-0.5" />
                  <span className="text-sm text-text-primary font-medium">320kbps Studio Audio (Spotify/YT)</span>
                </li>
                <li className="flex items-start gap-3">
                  <Zap className="w-5 h-5 text-primary mt-0.5" />
                  <span className="text-sm text-text-primary font-medium">1-Click Batch Playlist Zipping</span>
                </li>
              </ul>
              
              <button
                onClick={async () => {
                   // Mock payment webhook trigger for testing
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
                className="w-full py-3.5 px-6 rounded-xl bg-gradient-to-r from-primary to-accent text-white font-bold text-sm shadow-lg shadow-primary/30 hover:shadow-primary/40 hover:-translate-y-0.5 transition-all duration-200"
              >
                Upgrade to VIP
              </button>
            </div>

          </div>

          <p className="mt-8 text-xs text-text-muted">
            Secure processing. Cancel anytime.
          </p>
        </div>
      </div>
    </div>
  );
}

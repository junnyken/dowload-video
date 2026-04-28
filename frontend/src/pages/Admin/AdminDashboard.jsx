import { useState, useEffect } from 'react';
import {
  Shield, CheckCircle, XCircle, AlertTriangle, Users, Download, Activity, ToggleLeft, ToggleRight, Key, Copy
} from 'lucide-react';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1/admin`;

export default function AdminDashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');

  const fetchStats = async () => {
    try {
      const res = await fetch(`${API}/stats`);
      const data = await res.json();
      if (data.success) {
        setStats(data);
      }
    } catch (e) {
      console.error('Failed to fetch admin stats', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
    const intv = setInterval(fetchStats, 10000);
    return () => clearInterval(intv);
  }, []);

  const toggleUserPlan = async (user_id, currentPlan) => {
    const newPlan = currentPlan === 'pro' ? 'free' : 'pro';
    try {
      await fetch(`${API}/update-user`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id, plan: newPlan })
      });
      fetchStats();
    } catch (e) {
      alert("Failed to update user");
    }
  };

  if (loading || !stats) {
    return <div className="p-8 text-text-muted flex gap-2"><Activity className="animate-pulse" /> Loading Admin Center...</div>;
  }

  const zenrowsCredits = stats.providers?.zenrows || 0;
  const scraperAPIcredits = stats.providers?.scraperapi || 0;

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      
      {/* ── Header ────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Shield className="w-6 h-6 text-error" />
            <h2 className="text-2xl font-bold text-text-primary tracking-tight">Admin Control Center</h2>
          </div>
          <p className="text-sm text-text-muted ml-9">Manage proxies, users, and inspect errors.</p>
        </div>
        <div className="flex bg-surface-lighter rounded-xl p-1 border border-border">
          <button
            onClick={() => setActiveTab('overview')}
            className={`px-4 py-2 text-sm font-semibold rounded-lg transition-colors ${activeTab === 'overview' ? 'bg-surface shadow text-primary' : 'text-text-muted hover:text-text-primary'}`}
          >
            Overview
          </button>
          <button
            onClick={() => setActiveTab('apikeys')}
            className={`px-4 py-2 text-sm font-semibold rounded-lg transition-colors flex items-center gap-2 ${activeTab === 'apikeys' ? 'bg-surface shadow text-primary' : 'text-text-muted hover:text-text-primary'}`}
          >
            <Key className="w-4 h-4" /> API Keys & Credits
          </button>
        </div>
      </div>

      {activeTab === 'overview' ? (
        <div className="space-y-8 animate-in fade-in duration-300">
          {/* ── Dashboard Stats ───────────────────────────── */}
          <div className="flex gap-6">
        <div className="p-6 bg-surface-card border border-border shadow-lg rounded-2xl flex-1 flex items-center gap-4">
          <div className="p-4 bg-primary/10 rounded-xl"><Download className="w-6 h-6 text-primary-light" /></div>
          <div>
            <p className="text-sm text-text-muted font-medium">Downloads Today</p>
            <p className="text-2xl font-bold text-text-primary">{stats.total_downloads_today}</p>
          </div>
        </div>
        <div className="p-6 bg-surface-card border border-border shadow-lg rounded-2xl flex-1 flex items-center gap-4">
          <div className="p-4 bg-accent/10 rounded-xl"><Users className="w-6 h-6 text-accent-light" /></div>
          <div>
            <p className="text-sm text-text-muted font-medium">Total Active Users</p>
            <p className="text-2xl font-bold text-text-primary">{stats.total_users}</p>
          </div>
        </div>
      </div>

      {/* ── User Management & Logs ────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        
        {/* User Table */}
        <div className="p-6 bg-surface-card border border-border shadow-lg rounded-2xl">
          <h3 className="text-base font-semibold mb-4 text-text-primary flex items-center gap-2"><Users className="w-4 h-4"/> User Quotas</h3>
          <div className="overflow-auto max-h-[400px]">
            <table className="w-full text-sm text-left">
              <thead className="bg-surface sticky top-0 text-xs uppercase text-text-muted">
                <tr>
                  <th className="px-4 py-3">Identifier</th>
                  <th className="px-4 py-3">DL Today</th>
                  <th className="px-4 py-3 text-right">Plan</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {stats.recent_users.map(u => (
                  <tr key={u.user_id} className="hover:bg-surface-lighter/20">
                    <td className="px-4 py-3 text-text-primary truncate max-w-[150px]">{u.user_id}</td>
                    <td className="px-4 py-3 text-text-secondary">{u.downloads_today}</td>
                    <td className="px-4 py-3 text-right">
                      <button 
                        onClick={() => toggleUserPlan(u.user_id, u.plan || 'free')}
                        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-xl font-bold text-xs transition-colors ${
                          (u.plan || 'free') === 'pro' ? 'bg-success/20 text-success hover:bg-success/30' : 'bg-surface border border-border text-text-muted hover:text-text-primary'
                        }`}
                      >
                        {(u.plan || 'free') === 'pro' ? <ToggleRight className="w-4 h-4"/> : <ToggleLeft className="w-4 h-4"/>}
                        {(u.plan || 'free').toUpperCase()}
                      </button>
                    </td>
                  </tr>
                ))}
                {stats.recent_users.length === 0 && (
                  <tr><td colSpan="3" className="text-center py-4 text-text-muted">No users found.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* System Logs */}
        <div className="p-6 bg-surface-card border border-border shadow-lg rounded-2xl flex flex-col">
          <h3 className="text-base font-semibold mb-4 text-text-primary flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-warning"/> System Logs (Failed Jobs)</h3>
          <div className="flex-1 overflow-auto max-h-[400px] space-y-3 bg-surface p-4 rounded-xl font-mono text-xs text-text-muted border border-border">
            {stats.failed_jobs.map(job => (
              <div key={job.id} className="border-b border-border/50 pb-2 mb-2 last:border-0">
                <span className="text-error mr-2">[{new Date(job.created_at).toLocaleTimeString()}]</span>
                <span className="text-text-secondary">{job.original_url}</span>
                <p className="mt-1 text-error/80 break-words">{job.error_message}</p>
              </div>
            ))}
            {stats.failed_jobs.length === 0 && <p className="text-success flex items-center gap-2"><CheckCircle className="w-4 h-4"/> All systems operational. No recent failed jobs.</p>}
          </div>
        </div>
        </div>
        </div>
      ) : (
        <div className="space-y-6 animate-in fade-in duration-300">
          {/* ── API Keys Management ─────────────────────── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <CreditCard 
              provider="ZenRows" 
              credits={zenrowsCredits} 
              threshold={100} 
              apiKey={stats.api_keys?.ZenRows}
            />
            <CreditCard 
              provider="ScraperAPI" 
              credits={scraperAPIcredits} 
              threshold={10} 
              apiKey={stats.api_keys?.ScraperAPI}
            />
            <CreditCard 
              provider="IPRoyal" 
              credits={"N/A"} 
              threshold={0} 
              apiKey={stats.api_keys?.IPRoyal}
              isProxy={true}
            />
          </div>
          
          <div className="p-6 bg-surface-card border border-border shadow-lg rounded-2xl">
            <h3 className="text-base font-semibold mb-2 text-text-primary flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-warning"/> Cảnh báo</h3>
            <p className="text-sm text-text-muted">
              Tính năng này chỉ dành cho Admin. Khi API key hết Credits (Credit &lt; 10), hệ thống sẽ gửi cảnh báo tới Telegram.
              Bạn có thể cập nhật các key này trong file <code>backend/.env</code>, sau đó khởi động lại tiến trình FastAPI (backend).
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function CreditCard({ provider, credits, threshold, apiKey, isProxy }) {
  const isLow = credits !== "N/A" && credits < threshold;
  
  return (
    <div className={`
      relative overflow-hidden p-6 rounded-2xl border shadow-lg transition-all duration-300 flex flex-col justify-between
      ${isLow ? 'bg-error/5 border-error/50 shadow-error/20' : 'bg-surface-card border-border shadow-primary/5 hover:border-success/30'}
    `}>
      <div>
        <h4 className="text-sm font-semibold uppercase tracking-wider text-text-muted mb-2">{provider} {isProxy ? 'Proxy' : 'Provider'}</h4>
        <div className="flex items-end gap-3">
          <span className={`text-5xl font-bold tracking-tighter ${isLow ? 'text-error' : 'text-text-primary'}`}>
            {typeof credits === 'number' ? credits.toLocaleString() : credits}
          </span>
          <span className="text-text-secondary pb-1">{isProxy ? 'status' : 'credits remaining'}</span>
        </div>
        
        {apiKey && (
          <div className="mt-4 p-3 bg-surface border border-border rounded-xl flex items-center justify-between group">
            <div className="overflow-hidden">
              <p className="text-[10px] text-text-muted uppercase font-bold mb-1">Current Key/URL</p>
              <p className="text-sm text-text-primary font-mono truncate">{apiKey.length > 30 ? apiKey.substring(0, 30) + '...' : apiKey}</p>
            </div>
            <button 
              onClick={() => { navigator.clipboard.writeText(apiKey); alert("Copied to clipboard!"); }}
              className="p-2 text-text-muted hover:text-primary transition-colors cursor-pointer"
              title="Copy Key"
            >
              <Copy className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
      
      {isLow && !isProxy && (
        <div className="mt-4 flex items-center gap-2 text-xs font-bold text-error bg-error/10 px-3 py-2 rounded-xl">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          Credits dangerously low! Please update the .env file.
        </div>
      )}
      {!isLow && !isProxy && (
        <div className="mt-4 flex items-center gap-2 text-xs font-medium text-success">
          <CheckCircle className="w-4 h-4 flex-shrink-0" />
          Healthy balance
        </div>
      )}
      {isProxy && apiKey && apiKey !== "Not Set" && (
         <div className="mt-4 flex items-center gap-2 text-xs font-medium text-success">
         <CheckCircle className="w-4 h-4 flex-shrink-0" />
         Proxy Configured
       </div>
      )}
    </div>
  );
}

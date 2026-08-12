import { useState, useEffect, useCallback } from 'react';
import { ClipboardCheck, Check, X, Clock, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useWorkspace } from '../context/WorkspaceContext';

const API = import.meta.env.VITE_API_URL || '';

const ACTION_LABELS = {
  bulk_download:    'Tải hàng loạt',
  schedule_create:  'Tạo lịch tải',
  webhook_create:   'Đăng ký Webhook',
  archive_delete:   'Xóa Archive item',
};

const STATUS_CONFIG = {
  awaiting_approval: { label: 'Chờ duyệt',  cls: 'text-amber-300 bg-amber-900/30 border-amber-700/40',  icon: <Clock    className="w-3 h-3" /> },
  approved:          { label: 'Đã duyệt',   cls: 'text-teal-300  bg-teal-900/30  border-teal-700/40',   icon: <Check    className="w-3 h-3" /> },
  rejected:          { label: 'Từ chối',    cls: 'text-red-300   bg-red-900/30   border-red-700/40',    icon: <X        className="w-3 h-3" /> },
};

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
}

export default function ApprovalQueuePage() {
  const { session } = useAuth();
  const { activeWorkspace, can } = useWorkspace();
  const [requests, setRequests]     = useState([]);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState('');
  const [tab, setTab]               = useState('pending');   // 'pending' | 'all'
  const [rejectModal, setRejectModal] = useState(null);      // { id } or null
  const [rejectReason, setRejectReason] = useState('');
  const [busy, setBusy]             = useState('');          // approval request id being processed

  const wsId = activeWorkspace?.id;
  const isAdmin = can('approvals.manage');

  const fetchRequests = useCallback(async () => {
    if (!wsId || !session?.access_token) return;
    setLoading(true);
    setError('');
    try {
      const endpoint = (tab === 'pending' && isAdmin)
        ? `${API}/api/v1/workspaces/${wsId}/approvals/pending`
        : `${API}/api/v1/workspaces/${wsId}/approvals${tab === 'all' ? '' : '?status=awaiting_approval'}`;
      const r = await fetch(endpoint, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!r.ok) { setError('Không thể tải danh sách phê duyệt.'); return; }
      const d = await r.json();
      setRequests(d.pending || d.approval_requests || []);
    } finally { setLoading(false); }
  }, [wsId, session, tab, isAdmin]);

  useEffect(() => { fetchRequests(); }, [fetchRequests]);

  const handleApprove = async (reqId) => {
    setBusy(reqId);
    try {
      const r = await fetch(`${API}/api/v1/workspaces/${wsId}/approvals/${reqId}/approve`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (r.ok) fetchRequests();
    } finally { setBusy(''); }
  };

  const handleReject = async () => {
    if (!rejectModal) return;
    setBusy(rejectModal);
    try {
      const r = await fetch(`${API}/api/v1/workspaces/${wsId}/approvals/${rejectModal}/reject`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: rejectReason }),
      });
      if (r.ok) { setRejectModal(null); setRejectReason(''); fetchRequests(); }
    } finally { setBusy(''); }
  };

  if (!activeWorkspace) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 text-center text-slate-400">
        Chưa có workspace nào.
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
      <div className="flex items-center gap-3 mb-6">
        <ClipboardCheck className="w-5 h-5 text-[#FBBF24]" />
        <h1 className="text-xl font-bold text-white">Phê duyệt</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 border-b border-slate-700/50">
        {[['pending', 'Chờ duyệt'], ['all', 'Tất cả']].map(([key, label]) => (
          <button
            key={key}
            onClick={() => { setTab(key); setRequests([]); }}
            className={`px-4 py-2.5 text-sm font-medium transition-colors cursor-pointer -mb-px border-b-2
              ${tab === key ? 'border-[#FBBF24] text-[#FBBF24]' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded-lg bg-red-900/30 border border-red-700/40 text-red-300 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="h-24 rounded-xl bg-slate-800/30 animate-pulse" />)}
        </div>
      ) : requests.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <ClipboardCheck className="w-8 h-8 mx-auto mb-3 opacity-40" />
          <p>{tab === 'pending' ? 'Không có yêu cầu nào chờ duyệt.' : 'Chưa có yêu cầu phê duyệt nào.'}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {requests.map(req => {
            const sc = STATUS_CONFIG[req.status] || STATUS_CONFIG.awaiting_approval;
            const actionLabel = ACTION_LABELS[req.action_type] || req.action_type;
            const note = req.action_payload?.requester_note;
            return (
              <div key={req.id} className="bg-slate-800/30 rounded-xl border border-slate-700/50 p-4">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span className="text-sm font-semibold text-white">{actionLabel}</span>
                      <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded border leading-none ${sc.cls}`}>
                        {sc.icon} {sc.label}
                      </span>
                    </div>
                    <p className="text-xs text-slate-400 mb-1">
                      {fmtDate(req.created_at)}
                      {req.requester_user_id && <span className="ml-2 font-mono opacity-60">{req.requester_user_id.slice(0, 8)}</span>}
                    </p>
                    {note && (
                      <p className="text-xs text-slate-300 italic mt-1 bg-slate-900/40 rounded px-2 py-1.5">{note}</p>
                    )}
                    {req.rejection_reason && (
                      <p className="text-xs text-red-300 mt-1">Lý do từ chối: {req.rejection_reason}</p>
                    )}
                  </div>

                  {isAdmin && req.status === 'awaiting_approval' && (
                    <div className="flex gap-2 flex-shrink-0">
                      <button
                        onClick={() => handleApprove(req.id)}
                        disabled={busy === req.id}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-900/40 text-teal-300 text-xs font-semibold hover:bg-teal-800/50 border border-teal-700/40 disabled:opacity-40 transition-colors cursor-pointer"
                      >
                        <Check className="w-3 h-3" /> Duyệt
                      </button>
                      <button
                        onClick={() => { setRejectModal(req.id); setRejectReason(''); }}
                        disabled={busy === req.id}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-900/30 text-red-300 text-xs font-semibold hover:bg-red-900/50 border border-red-700/40 disabled:opacity-40 transition-colors cursor-pointer"
                      >
                        <X className="w-3 h-3" /> Từ chối
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Reject modal */}
      {rejectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-md bg-[#021f1c] rounded-2xl border border-slate-700/60 shadow-2xl p-6">
            <h3 className="text-base font-semibold text-white mb-3">Lý do từ chối</h3>
            <textarea
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
              rows={3}
              placeholder="Nhập lý do (không bắt buộc)..."
              className="w-full bg-slate-900/60 border border-slate-600/50 rounded-xl px-3 py-2.5 text-white text-sm resize-none focus:outline-none focus:border-red-500/50 placeholder-slate-500 mb-4"
            />
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => { setRejectModal(null); setRejectReason(''); }}
                className="px-4 py-2 rounded-lg border border-slate-600/50 text-slate-300 text-sm hover:bg-slate-700/40 transition-colors cursor-pointer"
              >
                Huỷ
              </button>
              <button
                onClick={handleReject}
                disabled={!!busy}
                className="px-4 py-2 rounded-lg bg-red-900/40 text-red-300 border border-red-700/40 text-sm font-semibold hover:bg-red-900/60 disabled:opacity-40 transition-colors cursor-pointer"
              >
                {busy ? '...' : 'Xác nhận từ chối'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from 'react';
import { Shield, ChevronLeft, ChevronRight, Filter, Download } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useWorkspace } from '../context/WorkspaceContext';

const API = import.meta.env.VITE_API_URL || '';

const PAGE_SIZE = 50;

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
}

export default function AuditLogPage() {
  const { session } = useAuth();
  const { activeWorkspace, can } = useWorkspace();
  const [logs, setLogs]           = useState([]);
  const [total, setTotal]         = useState(0);
  const [page, setPage]           = useState(1);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState('');
  const [actionLabels, setActionLabels] = useState({});

  // Filters
  const [filterAction,    setFilterAction]    = useState('');
  const [filterResource,  setFilterResource]  = useState('');
  const [filterDateFrom,  setFilterDateFrom]  = useState('');
  const [filterDateTo,    setFilterDateTo]    = useState('');

  const wsId = activeWorkspace?.id;

  const fetchLogs = useCallback(async () => {
    if (!wsId || !session?.access_token || !can('audit.read')) return;
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (filterAction)   params.set('action',        filterAction);
      if (filterResource) params.set('resource_type', filterResource);
      if (filterDateFrom) params.set('date_from',     filterDateFrom);
      if (filterDateTo)   params.set('date_to',       filterDateTo);

      const r = await fetch(`${API}/api/v1/workspaces/${wsId}/audit?${params}`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!r.ok) { setError('Không thể tải audit log.'); return; }
      const d = await r.json();
      setLogs(d.logs || []);
      setTotal(d.total || 0);
      if (d.action_labels) setActionLabels(d.action_labels);
    } finally { setLoading(false); }
  }, [wsId, session, can, page, filterAction, filterResource, filterDateFrom, filterDateTo]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleFilter = () => { setPage(1); fetchLogs(); };

  if (!activeWorkspace) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12 text-center text-slate-400">
        Chưa có workspace nào.
      </div>
    );
  }
  if (!can('audit.read')) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-12 text-center">
        <Shield className="w-8 h-8 text-slate-500 mx-auto mb-3" />
        <p className="text-slate-400">Cần quyền Admin để xem audit log.</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 md:px-8 py-8 md:py-12">
      <div className="flex items-center gap-3 mb-6">
        <Shield className="w-5 h-5 text-[#FBBF24]" />
        <h1 className="text-xl font-bold text-white">Audit Log</h1>
        <span className="ml-auto text-xs text-slate-400">{total.toLocaleString()} sự kiện</span>
      </div>

      {/* Filters */}
      <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 p-4 mb-5">
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Hành động</label>
            <select
              value={filterAction}
              onChange={e => setFilterAction(e.target.value)}
              className="bg-slate-900/60 border border-slate-600/50 rounded-lg px-2.5 py-1.5 text-slate-300 text-xs focus:outline-none cursor-pointer"
            >
              <option value="">Tất cả</option>
              {Object.entries(actionLabels).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Loại resource</label>
            <select
              value={filterResource}
              onChange={e => setFilterResource(e.target.value)}
              className="bg-slate-900/60 border border-slate-600/50 rounded-lg px-2.5 py-1.5 text-slate-300 text-xs focus:outline-none cursor-pointer"
            >
              <option value="">Tất cả</option>
              <option value="archive_item">Archive Item</option>
              <option value="collection">Collection</option>
              <option value="scheduled_job">Lịch tải</option>
              <option value="member">Thành viên</option>
              <option value="invite">Lời mời</option>
              <option value="workspace">Workspace</option>
              <option value="export_job">Export</option>
              <option value="approval_request">Phê duyệt</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Từ ngày</label>
            <input type="date" value={filterDateFrom} onChange={e => setFilterDateFrom(e.target.value)}
              className="bg-slate-900/60 border border-slate-600/50 rounded-lg px-2.5 py-1.5 text-slate-300 text-xs focus:outline-none" />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Đến ngày</label>
            <input type="date" value={filterDateTo} onChange={e => setFilterDateTo(e.target.value)}
              className="bg-slate-900/60 border border-slate-600/50 rounded-lg px-2.5 py-1.5 text-slate-300 text-xs focus:outline-none" />
          </div>
          <button
            onClick={handleFilter}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#FBBF24]/20 text-[#FBBF24] text-xs font-semibold hover:bg-[#FBBF24]/30 transition-colors cursor-pointer"
          >
            <Filter className="w-3.5 h-3.5" /> Lọc
          </button>
        </div>
      </div>

      {/* Table */}
      {error ? (
        <div className="text-center py-12 text-red-400 text-sm">{error}</div>
      ) : loading ? (
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => <div key={i} className="h-12 rounded-xl bg-slate-800/30 animate-pulse" />)}
        </div>
      ) : logs.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Shield className="w-8 h-8 mx-auto mb-3 opacity-40" />
          <p>Không có sự kiện nào.</p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border border-slate-700/50">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700/50 bg-slate-800/40">
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium whitespace-nowrap">Thời gian</th>
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium whitespace-nowrap">Hành động</th>
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium whitespace-nowrap">Người thực hiện</th>
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium whitespace-nowrap">Resource</th>
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium whitespace-nowrap">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/30">
                {logs.map(log => (
                  <tr key={log.id} className="hover:bg-slate-800/20 transition-colors">
                    <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">{fmtDate(log.created_at)}</td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-white font-medium">{log.action_label || log.action}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-300 max-w-[140px] truncate">{log.actor_email || log.actor_user_id}</td>
                    <td className="px-4 py-3 text-xs text-slate-400 max-w-[120px]">
                      <span className="text-slate-500">{log.resource_type}/</span>
                      <span className="font-mono text-[10px]">{(log.resource_id || '').slice(0, 8)}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap font-mono">{log.ip_address || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-4 px-1">
            <p className="text-xs text-slate-400">
              Trang {page}/{totalPages} · {total} sự kiện
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="p-1.5 rounded-lg border border-slate-600/50 text-slate-400 hover:text-white hover:bg-slate-700/40 disabled:opacity-30 transition-colors cursor-pointer"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="p-1.5 rounded-lg border border-slate-600/50 text-slate-400 hover:text-white hover:bg-slate-700/40 disabled:opacity-30 transition-colors cursor-pointer"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

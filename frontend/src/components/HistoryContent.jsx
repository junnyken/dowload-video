import {
  History,
  Search,
  Filter,
  FileVideo,
} from 'lucide-react';

export default function HistoryContent() {
  return (
    <div className="space-y-6">
      {/* ── Header ────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-3 mb-1">
          <History className="w-5 h-5 text-pink-500" />
          <h2 className="text-xl font-bold text-slate-800 tracking-tight">
            Lịch sử tải xuống
          </h2>
        </div>
        <p className="text-sm text-slate-500 ml-8">
          Xem và quản lý tất cả các lần tải trước đây
        </p>
      </div>

      {/* ── Search & Filter Bar ───────────────────────── */}
      <div className="flex items-center gap-3">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Tìm theo URL hoặc Batch ID..."
            disabled
            className="
              w-full pl-10 pr-4 py-2.5 rounded-xl
              bg-white border border-slate-200
              text-slate-800 placeholder-slate-400
              text-sm
              focus:outline-none focus:ring-2 focus:ring-pink-500/30
              transition-all duration-200 shadow-sm
              disabled:opacity-50 disabled:cursor-not-allowed
            "
          />
        </div>
        <button
          disabled
          className="
            flex items-center gap-2
            px-4 py-2.5 rounded-xl
            bg-white border border-slate-200
            text-slate-600 text-sm font-medium shadow-sm
            hover:bg-slate-50
            transition-all duration-200
            disabled:opacity-50 disabled:cursor-not-allowed
          "
        >
          <Filter className="w-4 h-4" />
          Bộ lọc
        </button>
      </div>

      {/* ── Table Placeholder ─────────────────────────── */}
      <div className="rounded-2xl bg-white border border-slate-200 shadow-sm overflow-hidden">
        {/* Table Header */}
        <div className="hidden md:grid grid-cols-12 gap-4 px-6 py-3 bg-slate-50 border-b border-slate-200">
          <div className="col-span-4 text-xs font-semibold text-slate-500 uppercase tracking-wider">URL</div>
          <div className="col-span-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">Batch ID</div>
          <div className="col-span-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">Trạng thái</div>
          <div className="col-span-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">Ngày</div>
          <div className="col-span-2 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Hành động</div>
        </div>

        {/* Empty State */}
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="w-16 h-16 rounded-2xl bg-slate-50 flex items-center justify-center mb-4">
            <FileVideo className="w-7 h-7 text-slate-400" />
          </div>
          <p className="text-sm text-slate-600 font-medium">
            Chưa có lịch sử tải xuống
          </p>
          <p className="text-xs text-slate-400 mt-1">
            Các file đã tải xong sẽ hiển thị ở đây
          </p>
        </div>
      </div>
    </div>
  );
}

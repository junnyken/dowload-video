import { CloudCheck, CloudOff, CloudUpload, RefreshCw } from 'lucide-react';
import { syncState, syncNow } from '../lib/sync';
import { formatDate } from '../lib/format';
import { Spinner } from './ui';

export function syncText(s: ReturnType<typeof syncState.get>): string {
  switch (s.status) {
    case 'syncing': return 'Đang đồng bộ…';
    case 'ok': return s.lastAt ? `Đã đồng bộ lúc ${formatDate(s.lastAt)}` : 'Đã đồng bộ';
    case 'disabled': return 'Đồng bộ chưa bật trên máy chủ';
    case 'offline': return 'Chưa đồng bộ được, sẽ tự thử lại';
    case 'off': return 'Tự đồng bộ đang tắt';
    case 'signedout': return 'Đăng nhập để đồng bộ';
    default: return s.pending > 0 ? `${s.pending} mục chờ đồng bộ` : 'Sẵn sàng đồng bộ';
  }
}

export function SyncStatus({ showButton = true }: { showButton?: boolean }) {
  const s = syncState.use();
  const Icon = s.status === 'ok' ? CloudCheck : s.status === 'syncing' ? CloudUpload : CloudOff;
  const canSync = showButton && (s.status === 'ok' || s.status === 'idle' || s.status === 'offline');
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-fg-muted" role="status">
      {s.status === 'syncing' ? <Spinner /> : <Icon size={16} aria-hidden />}
      <span>{syncText(s)}</span>
      {canSync && (
        <button type="button" aria-label="Đồng bộ ngay" title="Đồng bộ ngay" onClick={() => void syncNow()} className="rounded-md p-1 text-fg-2 hover:bg-surface-2">
          <RefreshCw size={14} />
        </button>
      )}
    </span>
  );
}

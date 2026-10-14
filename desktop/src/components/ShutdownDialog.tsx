import { Power } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from './ui';
import { cancelShutdown, shutdownPrompt } from '../lib/shutdown';
import { api } from '../lib/tauri';
import { toast } from '../lib/ui';
import { errorMessage, toAppError } from '../lib/errors';

/** 60 s countdown before "Tắt máy khi tải xong" shuts Windows down (14/10). wording: BA review */
export function ShutdownDialog() {
  const p = shutdownPrompt.use();
  const [now, setNow] = useState(Date.now());
  const left = p ? Math.max(0, Math.ceil((p.deadline - now) / 1000)) : 0;

  useEffect(() => {
    if (!p) return undefined;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [p]);

  useEffect(() => {
    if (p && left === 0) void doShutdown();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p, left]);

  async function doShutdown() {
    cancelShutdown();
    try { await api.systemShutdown(); } catch (e) { toast('error', `Không tắt được máy: ${errorMessage(toAppError(e).code)}`); }
  }

  if (!p) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" role="alertdialog" aria-modal="true" aria-labelledby="vg-shutdown-title">
      <div className="w-[400px] rounded-xl border border-line bg-surface p-5 shadow-card">
        <div className="flex items-center gap-3">
          <Power size={22} className="text-danger" aria-hidden />
          <h2 id="vg-shutdown-title" className="text-base font-semibold text-fg">Đã tải xong — máy sẽ tắt sau {left} giây</h2>
        </div>
        <p className="mt-2 text-sm text-fg-2">Bạn đã bật “Tắt máy khi tải xong”. Hãy lưu công việc đang mở, hoặc bấm Huỷ để giữ máy chạy.</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="primary" onClick={cancelShutdown} autoFocus>Huỷ, không tắt máy</Button>
          <Button variant="danger" onClick={() => void doShutdown()}>Tắt ngay</Button>
        </div>
      </div>
    </div>
  );
}

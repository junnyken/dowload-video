import { useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import { Button } from './ui';
import { CopyLink } from './CopyLink';
import { checkUpdate, updateGate } from '../lib/update';
import { installLink } from '../lib/update-core';
import { WEBSITE_URL } from '../lib/config';
import { api } from '../lib/tauri';
import { toast } from '../lib/ui';

// Same list as src-tauri/src/validate.rs OPEN_URL_HOSTS.
const OPEN_URL_HOSTS = ['dvid.vibe1.tinhgon.xyz', 'dvid-api.vibe1.tinhgon.xyz'] as const;

/** Full-window block while this build is below the server's minimum
 * (PLAN-32E P1 step 3). Only shown when the server says so: GET /client/version
 * minSupported, or a 426 update_required answer. Wording: BA review. */
export function UpdateRequired() {
  const u = updateGate.use();
  const [checking, setChecking] = useState(false);
  if (!u.required) return null;
  const link = installLink(u.downloadUrl, WEBSITE_URL, OPEN_URL_HOSTS);

  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="upd-title"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-canvas/95 p-6">
      <div className="w-full max-w-md rounded-xl border border-line bg-surface p-6 shadow-lg">
        <h2 id="upd-title" className="text-lg font-semibold">Cần cập nhật VidGrab</h2>
        <p className="mt-2 text-sm text-fg-2">
          {u.detail || 'Ứng dụng VidGrab trên máy bạn đã cũ. Vui lòng cập nhật bản mới để tiếp tục tải.'}
        </p>
        <p className="mt-2 text-xs text-fg-muted">
          Bản tối thiểu: {u.minSupported || '—'}{u.latest ? ` · Bản mới nhất: ${u.latest}` : ''}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" icon={<Download size={15} />}
            onClick={() => { api.openUrl(link).catch(() => toast('error', 'Không mở được trình duyệt. Hãy sao chép liên kết bên dưới.')); }}>
            Tải bản mới
          </Button>
          <Button icon={<RefreshCw size={15} />} disabled={checking}
            onClick={async () => {
              setChecking(true);
              const r = await checkUpdate();
              setChecking(false);
              if (r.required) toast('info', 'Máy bạn vẫn đang dùng bản cũ.');
            }}>
            Kiểm tra lại
          </Button>
        </div>
        <div className="mt-3"><CopyLink url={link} /></div>
        <p className="mt-3 text-xs text-fg-muted">Video đã tải và danh sách kênh vẫn được giữ nguyên sau khi cập nhật.</p>
      </div>
    </div>
  );
}

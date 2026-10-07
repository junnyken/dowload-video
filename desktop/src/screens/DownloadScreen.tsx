import { useEffect, useState } from 'react';
import { ClipboardPaste, ListPlus, Search, Link2, Tv, X } from 'lucide-react';
import { Button, EmptyState, ScreenHeader } from '../components/ui';
import { QuotaBadge, QuotaNotice } from '../components/QuotaBadge';
import { ProbeCard, cardSelection } from '../components/ProbeCard';
import { analyze, clearInvalid, parseUrls, probes, removeCard, type Card } from '../lib/probes';
import { createStore } from '../lib/store';
import { ensureOutDir, settings } from '../lib/settings';
import { enqueue } from '../lib/queue';
import { api } from '../lib/tauri';
import { go, openInChannels, prefill, toast } from '../lib/ui';
import { isChannelOnDownloadScreen as looksLikeChannelUrl } from '../lib/urls';

const draft = createStore('');
const PLATFORMS = ['YouTube', 'TikTok', 'Douyin', 'Instagram', 'Facebook', 'X (Twitter)', 'Threads', 'Reddit', 'Vimeo'];

async function addToQueue(card: Card): Promise<boolean> {
  const st = settings.get();
  const { option, outDir } = cardSelection(card, st.defaultQuality, st.outDir);
  const r = card.result!;
  if (!outDir) return false;
  const est = option.format?.filesize ?? null;
  if (est) {
    try {
      const free = await api.diskFree(outDir);
      if (free < est * 1.1) {
        toast('error', 'Ổ đĩa không đủ dung lượng cho video này. Hãy chọn thư mục khác.');
        return false;
      }
    } catch { /* cannot measure: let the download decide */ }
  }
  enqueue({
    url: r.url, title: r.title, thumbnail: r.thumbnail, platform: r.platform, uploader: r.uploader, outDir,
    formatId: option.value === 'best' || option.value === 'audio' ? undefined : option.format?.id,
    audioOnly: option.value === 'audio', formatLabel: option.label, estSize: est,
    quality: option.value, startAt: card.serverOnly ? 'S0' : undefined,
  });
  removeCard(card.url);
  return true;
}

export function DownloadScreen() {
  const text = draft.use();
  const { cards, invalid } = probes.use();
  const pre = prefill.use();
  const [pasteMsg, setPasteMsg] = useState<string | null>(null);

  useEffect(() => { void ensureOutDir(); }, []);
  useEffect(() => {
    if (pre) {
      analyze(pre);
      prefill.set(null);
    }
  }, [pre]);

  function run() {
    if (!text.trim()) return;
    // Channel links are not single videos: keep them in the box (banner offers the Channels screen).
    const { valid, invalid: bad } = parseUrls(text);
    const channelUrls = valid.filter(looksLikeChannelUrl);
    analyze([...valid.filter((u) => !looksLikeChannelUrl(u)), ...bad].join('\n'));
    // Invalid lines are reported under the box (probes.invalid).
    draft.set(channelUrls.join('\n'));
    setPasteMsg(null);
  }

  async function paste() {
    try {
      const t = await navigator.clipboard.readText();
      if (!t.trim()) { setPasteMsg('Bộ nhớ tạm đang trống.'); return; }
      draft.set((text ? text.replace(/\s*$/, '\n') : '') + t.trim());
      setPasteMsg(null);
    } catch {
      setPasteMsg('Không đọc được bộ nhớ tạm. Hãy nhấn Ctrl+V vào ô bên trên.');
    }
  }

  const channelUrl = parseUrls(text).valid.find(looksLikeChannelUrl);
  const ready = cards.filter((c) => c.status === 'ready');
  async function addAll() {
    let n = 0;
    for (const c of ready) if (await addToQueue(c)) n++;
    if (n) toast('info', `Đã thêm ${n} video vào hàng đợi.`);
  }

  return (
    <div className="flex h-full flex-col">
      <ScreenHeader title="Tải xuống" subtitle="Dán một hoặc nhiều liên kết video, mỗi liên kết một dòng." actions={<QuotaBadge />} />
      <QuotaNotice />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        <div className="rounded-xl border border-line bg-surface p-3 shadow-card">
          <textarea
            aria-label="Liên kết video"
            value={text}
            onChange={(e) => draft.set(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } }}
            rows={3}
            spellCheck={false}
            placeholder={'https://www.youtube.com/watch?v=…\nhttps://www.tiktok.com/@tên/video/…'}
            className="w-full resize-none rounded-lg border border-line bg-canvas p-3 text-sm text-fg placeholder:text-fg-muted"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button icon={<ClipboardPaste size={16} />} onClick={paste}>Dán từ bộ nhớ tạm</Button>
            {text && <Button variant="ghost" icon={<X size={16} />} onClick={() => draft.set('')}>Xoá</Button>}
            <span className="hidden text-xs text-fg-muted min-[1000px]:inline">Ctrl + Enter để phân tích</span>
            <Button variant="primary" className="ml-auto" icon={<Search size={16} />} disabled={!text.trim()} onClick={run}>Phân tích</Button>
          </div>
          {channelUrl && (
            <div role="status" className="mt-2 flex items-center gap-3 rounded-lg bg-accent-soft px-3 py-2 text-[13px] text-fg">
              <Tv size={18} className="shrink-0 text-accent-text" aria-hidden />
              <p className="min-w-0 flex-1"><span className="font-medium">Đây là liên kết kênh</span> — mở ở mục Kênh để chọn video cần tải hoặc theo dõi video mới.</p>
              <Button size="sm" variant="primary" onClick={() => { draft.set(text.split(/[\s,;]+/).filter((t) => t && t !== channelUrl).join('\n')); openInChannels(channelUrl); }}>Mở ở mục Kênh</Button>
            </div>
          )}
          {pasteMsg && <p className="mt-2 text-[13px] text-warning" role="status">{pasteMsg}</p>}
          {invalid.length > 0 && (
            <div role="alert" className="mt-2 flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{invalid.length === 1 ? 'Dòng này không phải liên kết hợp lệ:' : `${invalid.length} dòng không phải liên kết hợp lệ:`}</p>
                <ul className="mt-0.5 list-disc pl-4 select-text">
                  {invalid.slice(0, 3).map((u, i) => <li key={i} className="truncate">{u}</li>)}
                  {invalid.length > 3 && <li>và {invalid.length - 3} dòng khác</li>}
                </ul>
                <p className="mt-1 text-fg-2">Liên kết cần bắt đầu bằng http:// hoặc https://</p>
              </div>
              <button type="button" aria-label="Ẩn cảnh báo" onClick={clearInvalid}><X size={14} /></button>
            </div>
          )}
        </div>

        {cards.length === 0 ? (
          <EmptyState icon={<Link2 size={24} />} title="Dán liên kết video để bắt đầu" text="Ứng dụng sẽ phân tích liên kết, cho bạn chọn chất lượng rồi tải thẳng về máy.">
            <div className="mt-2 flex max-w-md flex-wrap justify-center gap-1.5">
              {PLATFORMS.map((p) => <span key={p} className="rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-fg-2">{p}</span>)}
              <span className="rounded-full px-2.5 py-1 text-xs text-fg-muted">và nhiều trang khác</span>
            </div>
            <p className="mt-3 text-[13px] text-fg-muted">
              Muốn tải cả kênh?{' '}
              <button type="button" onClick={() => go('channels')} className="font-medium text-accent-text underline-offset-2 hover:underline">Vào mục Kênh</button>
            </p>
          </EmptyState>
        ) : (
          <section className="mt-4 flex flex-col gap-3" aria-label="Kết quả phân tích">
            <div className="flex items-center justify-between">
              <h2 className="text-[13px] font-semibold text-fg-2">{cards.length} liên kết</h2>
              {ready.length >= 2 && <Button size="sm" variant="primary" icon={<ListPlus size={15} />} onClick={addAll}>Thêm tất cả vào hàng đợi ({ready.length})</Button>}
            </div>
            {cards.map((c) => <ProbeCard key={c.url} card={c} onAdd={async (x) => { if (await addToQueue(x)) toast('info', 'Đã thêm vào hàng đợi.'); }} />)}
          </section>
        )}
      </div>
    </div>
  );
}

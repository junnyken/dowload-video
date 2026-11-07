import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

const CHROME_PATHS = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
];
const BROWSER = CHROME_PATHS.find((p) => fs.existsSync(p));
if (!BROWSER) {
  throw new Error('Could not find Chrome or Edge binary for headless screenshot rendering.');
}

const OUT_EN = path.join(ROOT, 'chrome-extension', 'store-assets', 'screenshots-5.20');
const OUT_VI = path.join(ROOT, 'chrome-extension', 'store-assets', 'screenshots-vi');
const STORE_ASSETS = path.join(ROOT, 'chrome-extension', 'store-assets');
fs.mkdirSync(OUT_EN, { recursive: true });
fs.mkdirSync(OUT_VI, { recursive: true });

const BASE_STYLE = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 1280px;
    height: 800px;
    overflow: hidden;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Inter, Roboto, sans-serif;
    background: #13111c;
    color: #f8fafc;
    -webkit-font-smoothing: antialiased;
  }
  .canvas {
    position: relative;
    width: 1280px;
    height: 800px;
    background:
      radial-gradient(circle at 16% 18%, rgba(234, 88, 12, 0.22) 0%, transparent 45%),
      radial-gradient(circle at 84% 78%, rgba(124, 58, 237, 0.24) 0%, transparent 48%),
      linear-gradient(135deg, #13111c 0%, #191624 55%, #14121d 100%);
    display: flex;
    padding: 42px 48px;
    gap: 36px;
    align-items: center;
    justify-content: space-between;
  }
  .grid-bg {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(to right, rgba(255,255,255,0.03) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(255,255,255,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
  }
  .left-col {
    position: relative;
    z-index: 2;
    width: 485px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    height: 100%;
    padding: 4px 0;
  }
  .right-col {
    position: relative;
    z-index: 2;
    flex: 1;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .brand-pill {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 7px 14px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.14);
    width: fit-content;
    font-size: 12.5px;
    font-weight: 700;
    color: #fdba74;
    letter-spacing: 0.02em;
  }
  .brand-dot {
    width: 22px;
    height: 22px;
    border-radius: 6px;
    background: linear-gradient(135deg, #ea580c, #f97316);
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-weight: 900;
    font-size: 11px;
  }
  h1 {
    font-size: 38px;
    line-height: 1.14;
    font-weight: 850;
    letter-spacing: -0.025em;
    color: #ffffff;
    margin-top: 14px;
  }
  h1 .accent {
    background: linear-gradient(90deg, #fb923c, #f97316, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .subtitle {
    margin-top: 12px;
    font-size: 15.5px;
    line-height: 1.52;
    color: #cbd5e1;
    font-weight: 450;
  }
  .steps-list {
    margin-top: 18px;
    display: flex;
    flex-direction: column;
    gap: 11px;
  }
  .step-card {
    display: flex;
    align-items: flex-start;
    gap: 13px;
    padding: 12px 15px;
    border-radius: 15px;
    background: rgba(255, 255, 255, 0.055);
    border: 1px solid rgba(255, 255, 255, 0.11);
  }
  .step-num {
    width: 27px;
    height: 27px;
    border-radius: 8px;
    background: linear-gradient(135deg, #ea580c, #d946ef);
    color: #fff;
    font-weight: 800;
    font-size: 12.5px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-top: 1px;
  }
  .step-body {
    flex: 1;
  }
  .step-title {
    font-size: 14px;
    font-weight: 750;
    color: #f8fafc;
    margin-bottom: 2px;
  }
  .step-desc {
    font-size: 12.5px;
    line-height: 1.42;
    color: #94a3b8;
  }
  .footer-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 12px;
    border-top: 1px solid rgba(255,255,255,0.09);
  }
  .brand-footer {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 16px;
    font-weight: 800;
    color: #fb923c;
    white-space: nowrap;
  }
  .chip-row {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .chip {
    padding: 4px 10px;
    border-radius: 999px;
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.12);
    font-size: 11px;
    font-weight: 600;
    color: #e2e8f0;
  }
  .stage {
    position: relative;
    width: 660px;
    height: 716px;
  }
  .web-card {
    position: absolute;
    background: #fbf9f5;
    color: #1c1917;
    border-radius: 18px;
    border: 1px solid #e7e2da;
    box-shadow: 0 22px 50px rgba(0, 0, 0, 0.52);
    overflow: hidden;
  }
  .web-topbar {
    background: #f3efe8;
    padding: 8px 14px;
    border-bottom: 1px solid #e5dec9;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .dots {
    display: flex;
    gap: 5px;
  }
  .dot { width: 9px; height: 9px; border-radius: 50%; }
  .dot.r { background: #ef4444; }
  .dot.y { background: #f59e0b; }
  .dot.g { background: #10b981; }
  .url-pill {
    flex: 1;
    background: #ffffff;
    border: 1px solid #ded8ce;
    border-radius: 7px;
    padding: 3px 10px;
    font-size: 11px;
    color: #57534e;
    font-family: Consolas, monospace;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .ext-card {
    position: absolute;
    background: #171721;
    color: #f8fafc;
    border-radius: 18px;
    border: 1.5px solid rgba(167, 139, 250, 0.42);
    box-shadow: 0 26px 60px rgba(0, 0, 0, 0.78), 0 0 0 1px rgba(255,255,255,0.06);
    padding: 15px 17px;
  }
  .ext-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
  }
  .ext-logo {
    display: flex;
    align-items: center;
    gap: 9px;
  }
  .ext-badge {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: linear-gradient(135deg, #7c3aed, #ea580c);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 11.5px;
    color: #fff;
  }
  .ext-tabs {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
    margin-top: 9px;
    background: #101018;
    padding: 4px;
    border-radius: 10px;
  }
  .ext-tab {
    text-align: center;
    padding: 5px 4px;
    border-radius: 7px;
    font-size: 11px;
    font-weight: 700;
    color: #94a3b8;
  }
  .ext-tab.active {
    background: linear-gradient(135deg, #7c3aed, #6d28d9);
    color: #fff;
  }
  .stream-box {
    margin-top: 10px;
    background: #1e1e2c;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 12px;
    padding: 10px;
  }
  .stream-row {
    background: #14141f;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 9px;
    padding: 8px 10px;
    margin-top: 7px;
  }
  .tag {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 5px;
    font-size: 10px;
    font-weight: 800;
    font-family: Consolas, monospace;
  }
  .tag.mp4 { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16,185,129,0.35); }
  .tag.hls { background: rgba(139, 92, 246, 0.22); color: #c4b5fd; border: 1px solid rgba(139,92,246,0.38); }
  .tag.nowm { background: rgba(234, 88, 12, 0.2); color: #fdba74; border: 1px solid rgba(234,88,12,0.38); }
  .btn-purple {
    background: linear-gradient(135deg, #7c3aed, #6d28d9);
    color: #fff;
    border-radius: 7px;
    padding: 6px 11px;
    font-size: 11px;
    font-weight: 700;
    text-align: center;
    display: inline-block;
  }
  .btn-orange {
    background: linear-gradient(135deg, #ea580c, #c2410c);
    color: #fff;
    border-radius: 9px;
    padding: 8px 13px;
    font-size: 11.5px;
    font-weight: 800;
    text-align: center;
  }
  .callout-badge {
    position: absolute;
    background: linear-gradient(135deg, #ea580c, #d946ef);
    color: #fff;
    padding: 6px 13px;
    border-radius: 999px;
    font-size: 11.5px;
    font-weight: 800;
    box-shadow: 0 10px 25px rgba(234, 88, 12, 0.45);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    z-index: 10;
  }
`;

function buildSlide1(lang) {
  const vi = lang === 'vi';
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${BASE_STYLE}</style></head><body>
  <div class="canvas">
    <div class="grid-bg"></div>
    <div class="left-col">
      <div>
        <div class="brand-pill">
          <span class="brand-dot">VG</span>
          <span>${vi ? 'TIỆN ÍCH CHROME + TRANG CHỦ WEB' : 'CHROME EXTENSION + WEB DASHBOARD'}</span>
        </div>
        <h1>
          ${vi ? 'Tải mọi video đang phát <span class="accent">chỉ trong 3 bước</span>' : 'Download the video a page plays <span class="accent">in 3 simple steps</span>'}
        </h1>
        <p class="subtitle">
          ${vi
            ? 'Tự động phát hiện luồng MP4, HLS (.m3u8) ngay trên tab bạn đang xem hoặc dán link trên trang chủ để tải bản Không Logo, Full HD 1080p, 4K & MP3 320kbps.'
            : 'Automatically detects MP4 and HLS (.m3u8) streams on the page you are viewing — or paste any link on the web dashboard for No-Watermark HD & MP3.'}
        </p>

        <div class="steps-list">
          <div class="step-card">
            <div class="step-num">1</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Mở trang có video hoặc Sao chép liên kết' : 'Open any video page or Copy the link'}</div>
              <div class="step-desc">${vi ? 'Xem video trên TikTok, Douyin, Facebook, Instagram, Vimeo... Tiện ích tự động bắt luồng phát.' : 'Browse TikTok, Douyin, Facebook, Instagram, Dailymotion... VidGrab detects playable streams automatically.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">2</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Bấm biểu tượng VidGrab hoặc Dán vào Trang chủ' : 'Click the VidGrab icon or Paste on Web'}</div>
              <div class="step-desc">${vi ? 'Xem ngay danh sách file MP4 / HLS kèm dung lượng, độ phân giải và tùy chọn Bỏ logo (Watermark).' : 'See all MP4 & HLS streams with resolution, duration, and one-click Remove Watermark option.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">3</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Chọn chất lượng 1080p / 4K / MP3 & Lưu về máy' : 'Pick 1080p / 4K / MP3 & Save to computer'}</div>
              <div class="step-desc">${vi ? 'Tải trực tiếp bằng trình duyệt cực nhanh, tự động ghép luồng HLS (.m3u8) thành file MP4 hoàn chỉnh.' : 'Saves directly in your browser and merges HLS (.m3u8) segments into a single ready-to-play MP4 file.'}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="footer-bar">
        <div class="brand-footer">VidGrab · dowloadvideo.io.vn</div>
        <div class="chip-row">
          <span class="chip">MP4 · 1080p · 4K</span>
          <span class="chip">HLS .m3u8 → MP4</span>
          <span class="chip">MP3 320kbps</span>
        </div>
      </div>
    </div>

    <div class="right-col">
      <div class="stage">
        <!-- Top Card: VidGrab Web Homepage (dowloadvideo.io.vn) — all 3 steps 100% visible -->
        <div class="web-card" style="top: 0; left: 0; width: 560px;">
          <div class="web-topbar">
            <div class="dots"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span></div>
            <div class="url-pill">
              <span>https://dowloadvideo.io.vn</span>
              <span style="color:#ea580c;font-weight:700;">★ ${vi ? 'Trang chủ Web đồng bộ' : 'Synced Web Dashboard'}</span>
            </div>
          </div>
          <div style="padding: 14px 18px 16px;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <div>
                <div style="font-size:18px;font-weight:850;color:#1c1917;line-height:1.2;">
                  ${vi ? 'Tải video TikTok / Douyin ' : 'Download TikTok / Douyin '}
                  <span style="color:#c2410c;">${vi ? 'không dính logo' : 'without watermark'}</span>
                </div>
              </div>
              <span style="padding:3px 9px;border-radius:999px;background:#fff7ed;border:1px solid #fed7aa;color:#c2410c;font-size:10px;font-weight:700;">
                ✨ 30+ platforms
              </span>
            </div>

            <!-- Web Input Box -->
            <div style="margin-top:10px;background:#ffffff;border:1.5px solid #d6d0c4;border-radius:12px;padding:8px 10px;">
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:11px;color:#57534e;font-family:Consolas,monospace;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                  https://www.tiktok.com/@creator/video/7429183920...
                </span>
                <span class="btn-orange" style="padding:6px 12px;font-size:10.5px;">
                  ⚡ ${vi ? 'BÓC TÁCH NGAY' : 'EXTRACT NOW'}
                </span>
              </div>
              <div style="display:flex;align-items:center;gap:12px;margin-top:7px;font-size:10.5px;color:#44403c;">
                <span style="display:inline-flex;align-items:center;gap:5px;font-weight:700;color:#c2410c;">
                  <span style="width:13px;height:13px;background:#ea580c;color:#fff;border-radius:3px;display:inline-flex;align-items:center;justify-content:center;font-size:9px;">✓</span>
                  ${vi ? 'Bỏ logo (TikTok / Douyin)' : 'Remove logo (TikTok / Douyin)'}
                </span>
                <span style="padding:1px 7px;background:#f5f2eb;border-radius:5px;border:1px solid #e2dcd0;">
                  ${vi ? 'Phụ đề: Tự động' : 'Subtitles: Auto'}
                </span>
                <span style="padding:1px 7px;background:#f5f2eb;border-radius:5px;border:1px solid #e2dcd0;">
                  1080p · 4K · MP3
                </span>
              </div>
            </div>

            <!-- 3-step visual mini cards on Web Homepage (ALL 3 CLEARLY VISIBLE) -->
            <div style="margin-top:10px;background:#f6f3ec;border:1px solid #e5dec9;border-radius:11px;padding:9px 10px;">
              <div style="font-size:10px;font-weight:800;color:#292524;margin-bottom:6px;font-family:Consolas,monospace;">
                ${vi ? '// Hướng dẫn nhanh 3 bước trên Trang chủ \\\\' : '// 3-Step Visual Guide on Homepage \\\\'}
              </div>
              <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;">
                <div style="background:#fff;border:1px solid #e5dec9;border-radius:8px;padding:7px 8px;">
                  <div style="font-size:10px;font-weight:800;color:#c2410c;">01 · ${vi ? 'Lấy link / Extension' : 'Copy / Extension'}</div>
                  <div style="font-size:9.5px;color:#57534e;margin-top:2px;">${vi ? 'Chia sẻ → Sao chép hoặc bấm icon Tiện ích' : 'Share → Copy link or click Extension'}</div>
                </div>
                <div style="background:#fff;border:1px solid #e5dec9;border-radius:8px;padding:7px 8px;">
                  <div style="font-size:10px;font-weight:800;color:#c2410c;">02 · ${vi ? 'Dán & Bóc tách' : 'Paste & Extract'}</div>
                  <div style="font-size:9.5px;color:#57534e;margin-top:2px;">${vi ? 'Tích chọn Bỏ logo & bấm Bóc tách ngay' : 'Toggle Remove Logo & click Extract'}</div>
                </div>
                <div style="background:#fff;border:1px solid #e5dec9;border-radius:8px;padding:7px 8px;">
                  <div style="font-size:10px;font-weight:800;color:#c2410c;">03 · ${vi ? 'Tải về 1080p / MP3' : 'Save 1080p / MP3'}</div>
                  <div style="font-size:9.5px;color:#57534e;margin-top:2px;">${vi ? 'Chọn MP4 1080p, 4K hoặc nhạc MP3 320k' : 'Pick MP4 1080p, 4K or MP3 320kbps'}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="callout-badge" style="top: 300px; left: 16px;">
          <span>🧩 ${vi ? 'Tiện ích bắt luồng trực tiếp trên trình duyệt ↓' : 'Extension catches live streams right in browser ↓'}</span>
        </div>

        <!-- Bottom-Right Card: Chrome Extension Popup -->
        <div class="ext-card" style="bottom: 0; right: 0; width: 455px;">
          <div class="ext-header">
            <div class="ext-logo">
              <div class="ext-badge">VG</div>
              <div>
                <div style="font-size:13px;font-weight:800;color:#fff;">VidGrab Extension <span style="color:#34d399;font-size:10px;">● ${vi ? 'Đang hoạt động' : 'Active'}</span></div>
                <div style="font-size:10px;color:#94a3b8;">${vi ? 'Tự động liệt kê video trang đang phát' : 'Lists MP4 & HLS streams the page loads'}</div>
              </div>
            </div>
            <span style="font-size:10.5px;padding:2px 8px;border-radius:6px;background:rgba(255,255,255,0.07);color:#cbd5e1;">v5.23</span>
          </div>

          <div class="ext-tabs">
            <div class="ext-tab active">⬇ ${vi ? 'Tải video' : 'Download'}</div>
            <div class="ext-tab">📡 ${vi ? 'Quét kênh' : 'Channel'}</div>
            <div class="ext-tab">🕘 ${vi ? 'Lịch sử' : 'History'}</div>
          </div>

          <div class="stream-box">
            <div style="display:flex;justify-content:space-between;font-size:10px;font-weight:800;color:#94a3b8;letter-spacing:0.04em;">
              <span>${vi ? 'VIDEO PHÁT HIỆN TRÊN TRANG NÀY (2)' : 'VIDEOS ON THIS PAGE (2)'}</span>
              <span style="color:#34d399;">✓ ${vi ? 'Sẵn sàng lưu' : 'Ready to save'}</span>
            </div>

            <div class="stream-row">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
                <div>
                  <span class="tag mp4">MP4 · 1080p HD</span>
                  <span class="tag nowm" style="margin-left:4px;">${vi ? 'Không logo' : 'No Watermark'}</span>
                </div>
                <span style="font-size:10.5px;color:#cbd5e1;font-family:Consolas,monospace;">18.4 MB · 00:48</span>
              </div>
              <div style="display:flex;gap:6px;">
                <div class="btn-purple" style="flex:1;">⬇ ${vi ? 'Tải về máy tính (MP4)' : 'Download to this computer (MP4)'}</div>
                <div style="padding:6px 10px;border-radius:7px;background:#262638;font-size:10.5px;color:#cbd5e1;">Copy link</div>
              </div>
            </div>

            <div class="stream-row">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
                <div>
                  <span class="tag hls">HLS (.m3u8)</span>
                  <span style="font-size:10.5px;color:#cbd5e1;margin-left:6px;">${vi ? 'Tự ghép sang MP4' : 'Auto-merge to MP4'}</span>
                </div>
                <span style="font-size:10.5px;color:#cbd5e1;font-family:Consolas,monospace;">02:15</span>
              </div>
              <div style="display:flex;gap:6px;">
                <div class="btn-purple" style="flex:1;">⬇ ${vi ? 'Ghép & Lưu thành MP4' : 'Download to this computer'}</div>
                <div style="padding:6px 10px;border-radius:7px;background:#262638;font-size:10.5px;color:#cbd5e1;">MP3 320k</div>
              </div>
            </div>
          </div>

          <div style="margin-top:9px;">
            <div class="btn-orange" style="width:100%;padding:9px;font-size:12px;">
              ⬇ ${vi ? 'Tải Video Ngay (1080p / 4K / MP3)' : 'Download Video Now (1080p / 4K / MP3)'}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  </body></html>`;
}

function buildSlide2(lang) {
  const vi = lang === 'vi';
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${BASE_STYLE}</style></head><body>
  <div class="canvas">
    <div class="grid-bg"></div>
    <div class="left-col">
      <div>
        <div class="brand-pill">
          <span class="brand-dot">02</span>
          <span>${vi ? 'XEM TRƯỚC & CHỌN ĐÚNG CHẤT LƯỢNG' : 'PREVIEW & PICK EXACT QUALITY'}</span>
        </div>
        <h1>
          ${vi ? 'Xem trước video, dung lượng & <span class="accent">đặt tên file tự động</span>' : 'Preview before you download & <span class="accent">custom file naming</span>'}
        </h1>
        <p class="subtitle">
          ${vi
            ? 'Kiểm tra đúng nội dung, độ phân giải (1080p, 4K, Không watermark) và dung lượng thật trước khi lưu. Tự động đặt tên file gọn gàng theo mẫu bạn chọn.'
            : 'See the video title, resolution, and exact file size first, then save it straight to your computer. Name files your way with a template.'}
        </p>

        <div class="steps-list">
          <div class="step-card">
            <div class="step-num">✓</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Xem trước nội dung & dung lượng thật (MB)' : 'Instant video preview & real file size'}</div>
              <div class="step-desc">${vi ? 'Tránh tải nhầm video quảng cáo trên trang. Hiển thị rõ độ phân giải, thời lượng và số MB.' : 'Never download the wrong clip. See title, resolution, duration, and exact MB size upfront.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">✓</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Bỏ Watermark TikTok/Douyin & Tách nhạc MP3 320kbps' : 'Clean No-Watermark MP4 & 320kbps MP3 Audio'}</div>
              <div class="step-desc">${vi ? 'Chọn tải video gốc sạch logo hoặc chỉ tách riêng âm thanh MP3 chất lượng cao kèm phụ đề.' : 'Save clean original videos without watermarks or extract crystal-clear 320kbps MP3 audio.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">✓</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Quy tắc đặt tên file thông minh' : 'Smart filename templates'}</div>
              <div class="step-desc">${vi ? 'Tự động lưu theo mẫu {platform} - {date} - {title}.mp4 để quản lý thư mục dễ dàng.' : 'Name files your way with {platform} - {date} - {title}.mp4 tokens.'}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="footer-bar">
        <div class="brand-footer">VidGrab · dowloadvideo.io.vn</div>
        <div class="chip-row">
          <span class="chip">${vi ? 'Xem trước trực tiếp' : 'Live Preview'}</span>
          <span class="chip">{platform} - {date} - {title}</span>
        </div>
      </div>
    </div>

    <div class="right-col">
      <div class="stage" style="display:flex;align-items:center;justify-content:center;">
        <div class="ext-card" style="position:relative;width:540px;padding:22px;">
          <div class="ext-header">
            <div class="ext-logo">
              <div class="ext-badge">VG</div>
              <div>
                <div style="font-size:15px;font-weight:800;color:#fff;">VidGrab — ${vi ? 'Xem trước & Chọn định dạng' : 'Preview & Quality Selector'}</div>
                <div style="font-size:11.5px;color:#94a3b8;">${vi ? 'Đã phân tích luồng phát trên trang' : 'Stream inspected on active tab'}</div>
              </div>
            </div>
            <span class="tag mp4" style="font-size:11px;padding:4px 10px;">✓ 1080p Full HD</span>
          </div>

          <div style="margin-top:14px;background:linear-gradient(135deg,#1e1b4b,#31102f);border:1px solid rgba(255,255,255,0.14);border-radius:14px;padding:16px;display:flex;gap:16px;align-items:center;">
            <div style="width:140px;height:92px;border-radius:10px;background:linear-gradient(135deg,#ea580c,#7c3aed);display:flex;flex-direction:column;align-items:center;justify-content:center;position:relative;flex-shrink:0;">
              <div style="width:36px;height:36px;border-radius:50%;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;font-size:16px;">▶</div>
              <span style="position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,0.75);padding:2px 6px;border-radius:4px;font-size:10px;font-family:Consolas,monospace;">01:24</span>
            </div>
            <div style="flex:1;">
              <div style="display:flex;gap:6px;margin-bottom:6px;">
                <span class="tag nowm">${vi ? 'Sạch Watermark' : 'No Watermark'}</span>
                <span class="tag mp4">MP4 · H.264</span>
              </div>
              <div style="font-size:14.5px;font-weight:800;color:#fff;line-height:1.35;">
                "Sunset Timelapse & Mountain Aerial Reel"
              </div>
              <div style="font-size:12px;color:#cbd5e1;margin-top:5px;">
                ${vi ? 'Dung lượng thật:' : 'Verified size:'} <strong style="color:#34d399;">24.6 MB</strong> · 1920×1080 (60fps)
              </div>
            </div>
          </div>

          <div style="margin-top:14px;">
            <div style="font-size:11px;font-weight:800;color:#94a3b8;margin-bottom:8px;letter-spacing:0.04em;">
              ${vi ? 'CHỌN ĐỊNH DẠNG & CHẤT LƯỢNG TẢI VỀ' : 'CHOOSE OUTPUT FORMAT & QUALITY'}
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
              <div style="padding:11px 13px;border-radius:12px;background:rgba(234,88,12,0.16);border:1.5px solid #ea580c;display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <div style="font-size:13px;font-weight:800;color:#fff;">🎬 MP4 · 1080p HD</div>
                  <div style="font-size:11px;color:#fdba74;">${vi ? 'Bản gốc không logo · 24.6 MB' : 'Clean No-Logo · 24.6 MB'}</div>
                </div>
                <span style="background:#ea580c;color:#fff;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;">✓</span>
              </div>
              <div style="padding:11px 13px;border-radius:12px;background:#1e1e2c;border:1px solid rgba(255,255,255,0.1);display:flex;justify-content:space-between;align-items:center;">
                <div>
                  <div style="font-size:13px;font-weight:750;color:#e2e8f0;">🎵 MP3 · 320kbps</div>
                  <div style="font-size:11px;color:#94a3b8;">${vi ? 'Chỉ âm thanh · 3.1 MB' : 'Audio only · 3.1 MB'}</div>
                </div>
              </div>
            </div>
          </div>

          <div style="margin-top:14px;background:#12121b;border:1px solid rgba(255,255,255,0.09);border-radius:12px;padding:11px 14px;">
            <div style="display:flex;justify-content:space-between;font-size:11px;color:#94a3b8;margin-bottom:5px;">
              <span>${vi ? 'MẪU ĐẶT TÊN TỆP TỰ ĐỘNG' : 'CUSTOM FILENAME TEMPLATE'}</span>
              <span style="color:#a78bfa;font-family:Consolas,monospace;">{platform} - {date} - {title}</span>
            </div>
            <div style="font-size:12px;font-family:Consolas,monospace;color:#34d399;">
              📄 tiktok - 2026-11-07 - Sunset Timelapse Mountain Aerial Reel.mp4
            </div>
          </div>

          <div class="btn-orange" style="margin-top:14px;width:100%;padding:12px;font-size:13.5px;">
            ⬇ ${vi ? 'Lưu Tệp Về Máy Tính Ngay (24.6 MB)' : 'Save File Straight to Computer (24.6 MB)'}
          </div>
        </div>
      </div>
    </div>
  </div>
  </body></html>`;
}

function buildSlide3(lang) {
  const vi = lang === 'vi';
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${BASE_STYLE}</style></head><body>
  <div class="canvas">
    <div class="grid-bg"></div>
    <div class="left-col">
      <div>
        <div class="brand-pill">
          <span class="brand-dot">03</span>
          <span>${vi ? 'QUÉT KÊNH & TẢI HÀNG LOẠT' : 'CHANNEL SCANNER & BULK DOWNLOAD'}</span>
        </div>
        <h1>
          ${vi ? 'Quét toàn bộ video từ <span class="accent">Kênh & Trang cá nhân</span>' : 'Pick videos from <span class="accent">a whole channel</span>'}
        </h1>
        <p class="subtitle">
          ${vi
            ? 'Mở bất kỳ trang cá nhân hoặc kênh nào, bấm Quét Kênh để tự động gom danh sách video. Tích chọn các clip bạn muốn và tải hàng loạt về máy tính.'
            : 'Scroll a channel in your browser, tick the videos you want, and download them all to your computer or pack them as a ZIP.'}
        </p>

        <div class="steps-list">
          <div class="step-card">
            <div class="step-num">1</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Tự động cuộn & thu thập danh sách video' : 'Auto-scroll & collect channel videos'}</div>
              <div class="step-desc">${vi ? 'Chuyển sang tab "Kênh", bấm Bắt đầu quét. Tiện ích tự động gom toàn bộ video kèm tiêu đề & ảnh thu nhỏ.' : 'Switch to the "Channel" tab and click Start Scanning. VidGrab collects videos as the feed loads.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">2</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Chọn nhanh 10, 50 video hoặc Chọn tất cả' : 'Select All, First 10, or pick individual clips'}</div>
              <div class="step-desc">${vi ? 'Chủ động tích chọn những video cần lấy hoặc bỏ qua các video đã tải trước đó.' : 'Filter and tick the exact clips you need with one-click batch selection pills.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">3</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Tải trực tiếp về máy hoặc gửi sang Web nén .ZIP' : 'Download straight to PC or pack as .ZIP on Web'}</div>
              <div class="step-desc">${vi ? 'Tải tuần tự từng file về thư mục máy tính hoặc đẩy danh sách sang trang chủ VidGrab để nén .ZIP.' : 'Save files sequentially in your browser or send the batch to VidGrab Web for ZIP packaging.'}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="footer-bar">
        <div class="brand-footer">VidGrab · dowloadvideo.io.vn</div>
        <div class="chip-row">
          <span class="chip">${vi ? 'Quét kênh tự động' : 'Channel Auto-Scan'}</span>
          <span class="chip">${vi ? 'Tải hàng loạt' : 'Batch Download'}</span>
        </div>
      </div>
    </div>

    <div class="right-col">
      <div class="stage" style="display:flex;align-items:center;justify-content:center;">
        <div class="ext-card" style="position:relative;width:540px;padding:20px;">
          <div class="ext-header">
            <div class="ext-logo">
              <div class="ext-badge">VG</div>
              <div>
                <div style="font-size:15px;font-weight:800;color:#fff;">VidGrab · ${vi ? 'Trình Quét Kênh Hàng Loạt' : 'Channel & Profile Scanner'}</div>
                <div style="font-size:11.5px;color:#34d399;">https://www.tiktok.com/@studio_creative</div>
              </div>
            </div>
            <span style="background:rgba(16,185,129,0.18);color:#34d399;border:1px solid rgba(16,185,129,0.35);padding:4px 10px;border-radius:999px;font-size:12px;font-weight:800;">
              24 ${vi ? 'video đã tìm thấy' : 'videos collected'}
            </span>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:13px;">
            <div class="btn-purple" style="padding:10px;font-size:12px;">
              🔍 ${vi ? 'Đang quét kênh (Bấm để dừng)' : 'Start Scanning Channel'}
            </div>
            <div style="background:linear-gradient(135deg,#059669,#10b981);color:#fff;border-radius:8px;padding:10px;font-size:12px;font-weight:800;text-align:center;">
              ⚡ ${vi ? 'Gửi 24 video sang Web tải .ZIP' : 'Send 24 videos to download'}
            </div>
          </div>

          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:13px;font-size:11.5px;">
            <div style="display:flex;gap:6px;">
              <span style="padding:4px 10px;border-radius:6px;background:#27273a;color:#fff;font-weight:700;">${vi ? 'Chọn tất cả' : 'Select all'}</span>
              <span style="padding:4px 10px;border-radius:6px;background:#1e1e2d;color:#cbd5e1;">${vi ? 'Bỏ chọn' : 'Deselect'}</span>
              <span style="padding:4px 10px;border-radius:6px;background:#1e1e2d;color:#fdba74;font-weight:700;">${vi ? '10 video đầu' : 'First 10'}</span>
            </div>
            <span style="color:#cbd5e1;font-weight:700;">24 / 24 ${vi ? 'đã chọn' : 'selected'}</span>
          </div>

          <div style="margin-top:10px;display:flex;flex-direction:column;gap:8px;">
            ${[
              { c: '#7c3aed', t: vi ? 'Hướng dẫn quay dựng ánh sáng Studio #01' : 'Studio Lighting Setup Breakdown #01', d: '01:12 · 1080p HD' },
              { c: '#ea580c', t: vi ? 'Mẹo chỉnh màu video không bị vỡ nét #02' : 'Color Grading Workflow Without Compression #02', d: '00:54 · 1080p HD' },
              { c: '#10b981', t: vi ? 'Top 5 góc máy chuyển cảnh mượt mà #03' : '5 Seamless Camera Transitions Tutorial #03', d: '01:45 · 1080p HD' },
              { c: '#ec4899', t: vi ? 'Hậu trường quay sản phẩm thực tế #04' : 'Behind The Scenes Commercial Shoot #04', d: '00:39 · 1080p HD' },
            ].map((item) => `
              <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 12px;border-radius:11px;background:#14141f;border:1px solid rgba(255,255,255,0.08);">
                <div style="display:flex;align-items:center;gap:11px;">
                  <span style="width:18px;height:18px;border-radius:5px;background:#7c3aed;color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:900;">✓</span>
                  <div style="width:38px;height:38px;border-radius:8px;background:${item.c};display:flex;align-items:center;justify-content:center;font-size:14px;">▶</div>
                  <div>
                    <div style="font-size:12.5px;font-weight:700;color:#f8fafc;">${item.t}</div>
                    <div style="font-size:11px;color:#94a3b8;font-family:Consolas,monospace;">${item.d}</div>
                  </div>
                </div>
                <span class="btn-purple" style="padding:6px 12px;font-size:11px;">⬇ ${vi ? 'Tải về' : 'Download'}</span>
              </div>
            `).join('')}
          </div>

          <div class="btn-orange" style="margin-top:13px;width:100%;padding:12px;font-size:13.5px;">
            ⬇ ${vi ? 'Tải Tất Cả 24 Video Đã Chọn Về Máy Tính' : 'Download 24 Videos to Computer'}
          </div>
        </div>
      </div>
    </div>
  </div>
  </body></html>`;
}

function buildSlide4(lang) {
  const vi = lang === 'vi';
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${BASE_STYLE}</style></head><body>
  <div class="canvas">
    <div class="grid-bg"></div>
    <div class="left-col">
      <div>
        <div class="brand-pill">
          <span class="brand-dot">04</span>
          <span>${vi ? 'TẢI NGẦM & GHÉP LUỒNG HLS TỰ ĐỘNG' : 'BACKGROUND QUEUE & HLS MERGER'}</span>
        </div>
        <h1>
          ${vi ? 'Tải ngầm đa luồng ngay cả khi <span class="accent">đã đóng cửa sổ</span>' : 'Downloads keep going <span class="accent">in the background</span>'}
        </h1>
        <p class="subtitle">
          ${vi
            ? 'Tải song song nhiều video cùng lúc với thanh tiến trình thời gian thực. Tự động nối lại khi mạng chập chờn và ghép các phân đoạn HLS (.m3u8) thành file MP4 chuẩn.'
            : 'Two at a time, with progress for every video — close the popup and they continue. Interrupted downloads resume automatically.'}
        </p>

        <div class="steps-list">
          <div class="step-card">
            <div class="step-num">⚡</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Đóng popup tiện ích video vẫn tải tiếp' : 'Close the popup — downloads never stop'}</div>
              <div class="step-desc">${vi ? 'Tiến trình chạy ngầm trong trình duyệt giúp bạn thoải mái lướt sang tab khác mà không sợ đứt quãng.' : 'Background worker handles the queue so you can keep browsing other tabs freely.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">🔄</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Tự động ghép luồng HLS (.m3u8) sang MP4' : 'Built-in HLS (.m3u8) to MP4 muxer'}</div>
              <div class="step-desc">${vi ? 'Tải song song từng phân đoạn video và đóng gói thành 1 tệp MP4 duy nhất phát được trên mọi thiết bị.' : 'Fetches HLS segments in parallel and muxes them into a standard MP4 right inside your browser.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">🛡️</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Tự động tiếp tục khi gián đoạn mạng' : 'Auto-resume interrupted downloads'}</div>
              <div class="step-desc">${vi ? 'Lưu lịch sử tải rõ ràng, thông báo ngay khi toàn bộ danh sách đã lưu xong về máy.' : 'Tracks every completed file in History and notifies you when batch jobs finish.'}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="footer-bar">
        <div class="brand-footer">VidGrab · dowloadvideo.io.vn</div>
        <div class="chip-row">
          <span class="chip">${vi ? 'Chạy ngầm 100%' : 'Background Worker'}</span>
          <span class="chip">${vi ? 'Tự nối lại file' : 'Auto-Resume'}</span>
        </div>
      </div>
    </div>

    <div class="right-col">
      <div class="stage" style="display:flex;align-items:center;justify-content:center;">
        <div class="ext-card" style="position:relative;width:540px;padding:22px;">
          <div class="ext-header">
            <div class="ext-logo">
              <div class="ext-badge">VG</div>
              <div>
                <div style="font-size:15px;font-weight:800;color:#fff;">VidGrab · ${vi ? 'Tiến Trình Tải Ngầm' : 'Active Background Queue'}</div>
                <div style="font-size:11.5px;color:#34d399;">${vi ? 'Bạn có thể đóng cửa sổ này, quá trình tải vẫn tiếp tục' : 'You can close this window — downloads continue in background'}</div>
              </div>
            </div>
          </div>

          <div style="margin-top:14px;display:flex;flex-direction:column;gap:10px;">
            <div style="padding:12px 14px;border-radius:12px;background:#14141f;border:1px solid rgba(16,185,129,0.35);">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:13px;font-weight:750;color:#fff;">01 · Travel_Vlog_DaNang_1080p.mp4</span>
                <span style="font-size:11.5px;font-weight:800;color:#34d399;">✓ ${vi ? 'Đã lưu về máy (32.4 MB)' : 'Saved to PC (32.4 MB)'}</span>
              </div>
            </div>

            <div style="padding:12px 14px;border-radius:12px;background:#14141f;border:1px solid rgba(16,185,129,0.35);">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:13px;font-weight:750;color:#fff;">02 · Product_Review_NoWatermark.mp4</span>
                <span style="font-size:11.5px;font-weight:800;color:#34d399;">✓ ${vi ? 'Đã lưu về máy (18.9 MB)' : 'Saved to PC (18.9 MB)'}</span>
              </div>
            </div>

            <div style="padding:13px 14px;border-radius:12px;background:#1c1b29;border:1.5px solid #ea580c;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;">
                <span style="font-size:13px;font-weight:800;color:#fff;">03 · Masterclass_HLS_Stream_1080p.mp4</span>
                <span style="font-size:11.5px;font-weight:800;color:#fdba74;">${vi ? 'Đang ghép HLS · 82%' : 'Merging HLS · 82%'}</span>
              </div>
              <div style="width:100%;height:8px;border-radius:999px;background:#101018;overflow:hidden;">
                <div style="width:82%;height:100%;background:linear-gradient(90deg,#ea580c,#a855f7);border-radius:999px;"></div>
              </div>
              <div style="display:flex;justify-content:space-between;font-size:10.5px;color:#94a3b8;margin-top:6px;font-family:Consolas,monospace;">
                <span>${vi ? 'Phân đoạn: 148 / 180 segments' : 'Segments: 148 / 180 fetched'}</span>
                <span>41.2 MB / 50.1 MB · 11.4 MB/s</span>
              </div>
            </div>

            <div style="padding:12px 14px;border-radius:12px;background:#14141f;border:1px solid rgba(255,255,255,0.08);">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <span style="font-size:13px;font-weight:700;color:#e2e8f0;">04 · Podcast_Episode_Audio_320kbps.mp3</span>
                <span style="font-size:11.5px;font-weight:700;color:#c4b5fd;">${vi ? 'Đang tải · 45%' : 'Downloading · 45%'}</span>
              </div>
              <div style="width:100%;height:6px;border-radius:999px;background:#101018;overflow:hidden;">
                <div style="width:45%;height:100%;background:#7c3aed;border-radius:999px;"></div>
              </div>
            </div>
          </div>

          <div style="margin-top:14px;padding:12px 14px;border-radius:12px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.3);display:flex;justify-content:space-between;align-items:center;">
            <div>
              <div style="font-size:12.5px;font-weight:800;color:#34d399;">🎉 ${vi ? 'Hoàn tất 10 / 10 video trong hàng đợi!' : 'Finished: 10 / 10 videos saved to this computer!'}</div>
              <div style="font-size:11px;color:#cbd5e1;">${vi ? 'Tất cả tệp đã nằm trong thư mục Downloads của bạn.' : 'All files are ready in your browser Downloads folder.'}</div>
            </div>
            <span style="padding:6px 12px;border-radius:8px;background:#262638;font-size:11px;font-weight:700;color:#fff;">${vi ? 'Mở thư mục' : 'Open folder'}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
  </body></html>`;
}

function buildSlide5(lang) {
  const vi = lang === 'vi';
  // 13x13 realistic QR pattern
  const qrCells = [];
  for (let r = 0; r < 13; r++) {
    for (let c = 0; c < 13; c++) {
      const inTL = r < 4 && c < 4;
      const inTR = r < 4 && c > 8;
      const inBL = r > 8 && c < 4;
      let dark = false;
      if (inTL || inTR || inBL) {
        const lr = inBL ? r - 9 : r;
        const lc = inTR ? c - 9 : c;
        dark = lr === 0 || lr === 3 || lc === 0 || lc === 3 || (lr >= 1 && lr <= 2 && lc >= 1 && lc <= 2);
      } else {
        dark = ((r * 17 + c * 31 + r * c) % 5) < 2;
      }
      qrCells.push(`<div style="background:${dark ? '#111827' : '#ffffff'};border-radius:1.5px;"></div>`);
    }
  }

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${BASE_STYLE}</style></head><body>
  <div class="canvas">
    <div class="grid-bg"></div>
    <div class="left-col">
      <div>
        <div class="brand-pill">
          <span class="brand-dot">05</span>
          <span>${vi ? 'GỬI SANG ĐIỆN THOẠI & QUAY TAB' : 'SEND TO PHONE & RECORD TAB'}</span>
        </div>
        <h1>
          ${vi ? 'Quét mã QR gửi sang <span class="accent">Điện thoại trong 1 chạm</span>' : 'Send a video to your <span class="accent">phone with a QR code</span>'}
        </h1>
        <p class="subtitle">
          ${vi
            ? 'Đang xem video trên máy tính nhưng muốn lưu về điện thoại? Chỉ cần bấm "Gửi sang ĐT" và đưa camera lên quét mã QR. Hỗ trợ thêm chế độ Ghi hình Tab (WebM) tiện lợi.'
            : 'Scan the QR code to open the same video on your phone. Protected (DRM) and live videos are clearly marked, never downloaded.'}
        </p>

        <div class="steps-list">
          <div class="step-card">
            <div class="step-num">📱</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Quét QR mở ngay video trên iPhone / Android' : 'Instant QR hand-off to iOS & Android'}</div>
              <div class="step-desc">${vi ? 'Không cần tự gửi link qua tin nhắn. Quét mã QR là điện thoại mở thẳng trang tải video đó.' : 'No more messaging links to yourself. Scan the QR code to open and save the video on mobile.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">⏺</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Chế độ Ghi hình Tab (Record Tab sang WebM)' : 'Optional Tab Recording (Save to local WebM)'}</div>
              <div class="step-desc">${vi ? 'Khi trình phát không cung cấp tệp tải trực tiếp, bạn có thể ghi lại đoạn video đang phát thành tệp WebM.' : 'When a player has no direct file link, record the active tab stream locally into a WebM video.'}</div>
            </div>
          </div>
          <div class="step-card">
            <div class="step-num">🔒</div>
            <div class="step-body">
              <div class="step-title">${vi ? 'Minh bạch bản quyền & Đồng bộ tài khoản 30 ngày' : 'Honest DRM detection & 30-day Web account sync'}</div>
              <div class="step-desc">${vi ? 'Tự động báo rõ các luồng DRM/Live không hỗ trợ. Đồng bộ lịch sử & gói Pro với dowloadvideo.io.vn.' : 'Protected (DRM) and live streams are clearly marked. Sync your quota & history with VidGrab Web.'}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="footer-bar">
        <div class="brand-footer">VidGrab · dowloadvideo.io.vn</div>
        <div class="chip-row">
          <span class="chip">QR to Phone</span>
          <span class="chip">Record Tab</span>
          <span class="chip">Privacy First</span>
        </div>
      </div>
    </div>

    <div class="right-col">
      <div class="stage" style="display:flex;align-items:center;justify-content:center;">
        <div class="ext-card" style="position:relative;width:530px;padding:22px;">
          <div class="ext-header">
            <div class="ext-logo">
              <div class="ext-badge">VG</div>
              <div>
                <div style="font-size:15px;font-weight:800;color:#fff;">VidGrab · ${vi ? 'Tiện Ích Mở Rộng Thông Minh' : 'Smart Companion Tools'}</div>
                <div style="font-size:11.5px;color:#94a3b8;">${vi ? 'Xem trước · Gửi qua QR · Ghi hình Tab' : 'Preview · Send to Phone · Record Tab'}</div>
              </div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px;">
            <div style="padding:9px;border-radius:10px;background:#1e1e2c;border:1px solid rgba(255,255,255,0.08);text-align:center;font-size:12px;font-weight:700;color:#cbd5e1;">
              👁 ${vi ? 'Xem trước' : 'Preview'}
            </div>
            <div style="padding:9px;border-radius:10px;background:rgba(234,88,12,0.18);border:1.5px solid #ea580c;text-align:center;font-size:12px;font-weight:800;color:#fdba74;">
              📱 ${vi ? 'Gửi sang ĐT' : 'Send to phone'}
            </div>
            <div style="padding:9px;border-radius:10px;background:#1e1e2c;border:1px solid rgba(255,255,255,0.08);text-align:center;font-size:12px;font-weight:700;color:#cbd5e1;">
              ⏺ ${vi ? 'Ghi hình tab' : 'Record tab'}
            </div>
          </div>

          <div style="margin-top:14px;background:#ffffff;color:#1c1917;border-radius:16px;padding:20px;display:flex;align-items:center;gap:20px;">
            <div style="width:148px;height:148px;border:3px solid #111827;border-radius:12px;padding:9px;display:grid;grid-template-columns:repeat(13,1fr);grid-template-rows:repeat(13,1fr);gap:1.5px;flex-shrink:0;background:#fff;">
              ${qrCells.join('')}
            </div>
            <div style="flex:1;">
              <div style="display:inline-block;padding:3px 9px;border-radius:999px;background:#ffedd5;color:#c2410c;font-size:10.5px;font-weight:800;margin-bottom:6px;">
                📱 ${vi ? 'QUÉT BẰNG CAMERA ĐIỆN THOẠI' : 'SCAN WITH PHONE CAMERA'}
              </div>
              <div style="font-size:16px;font-weight:850;color:#111827;line-height:1.3;">
                ${vi ? 'Mở & lưu video này trực tiếp trên điện thoại của bạn' : 'Open & save this exact video right on your phone'}
              </div>
              <div style="font-size:12.5px;color:#4b5563;margin-top:6px;line-height:1.45;">
                ${vi
                  ? 'Camera iPhone hoặc Android quét mã sẽ mở thẳng trang tải VidGrab kèm sẵn link video đang xem.'
                  : 'Scan with your phone camera — VidGrab opens with this video so you can download it right on your phone.'}
              </div>
            </div>
          </div>

          <div style="margin-top:14px;padding:11px 14px;border-radius:12px;background:#12121b;border:1px solid rgba(255,255,255,0.08);display:flex;justify-content:space-between;align-items:center;font-size:11.5px;color:#94a3b8;">
            <span>✓ ${vi ? 'Đã đồng bộ tài khoản với dowloadvideo.io.vn' : 'Synced with dowloadvideo.io.vn'}</span>
            <span style="color:#34d399;font-weight:700;">● ${vi ? 'Bảo mật & Không quảng cáo' : 'Private & Ad-Free'}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
  </body></html>`;
}

const SLIDES = [
  { file: '1-video-on-page.png', builder: buildSlide1 },
  { file: '2-preview.png',       builder: buildSlide2 },
  { file: '3-channel-list.png',  builder: buildSlide3 },
  { file: '4-progress.png',      builder: buildSlide4 },
  { file: '5-send-to-phone.png', builder: buildSlide5 },
];

function renderHtmlToPng(htmlContent, outputPngPath, winW = 1420, winH = 980) {
  const tmpHtml = path.join(ROOT, 'scripts', `_tmp_slide_${Date.now()}.html`);
  fs.writeFileSync(tmpHtml, htmlContent, 'utf8');
  try {
    const fileUrl = 'file:///' + tmpHtml.replace(/\\/g, '/');
    execFileSync(BROWSER, [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      '--force-device-scale-factor=1',
      `--window-size=${winW},${winH}`,
      `--screenshot=${outputPngPath}`,
      fileUrl,
    ], { stdio: 'ignore' });
  } finally {
    if (fs.existsSync(tmpHtml)) fs.unlinkSync(tmpHtml);
  }
}

for (const slide of SLIDES) {
  const enPath = path.join(OUT_EN, slide.file);
  const viPath = path.join(OUT_VI, slide.file);
  renderHtmlToPng(slide.builder('en'), enPath);
  renderHtmlToPng(slide.builder('vi'), viPath);
}

console.log('Rendered all 10 slides (EN + VI)!');

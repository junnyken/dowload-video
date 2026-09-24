# VidGrab — Tình trạng sản phẩm & đầu vào lập kế hoạch

> Đo ngày **24-09-2026**. Mọi con số dưới đây lấy từ mã nguồn trên `github/main`
> hoặc từ bản đang chạy, không lấy từ tài liệu cũ.
>
> **Đọc kèm:** `FEATURES.md` (bản đồ tính năng chi tiết — nhưng phần trạng thái ghi
> *cập nhật 2026-06-30*, xem mục 8 để biết chỗ nào đã sai) và `docs/AI-HANDOFF.md`
> (bối cảnh kỹ thuật + bẫy cho AI).
>
> **Mục 9 liệt kê những gì CHƯA được kiểm chứng.** Đừng lập kế hoạch dựa trên
> mục đó mà không đo lại.

---

## 1. Tóm tắt

VidGrab là web app **tải video/nhạc từ mạng xã hội**, kèm bộ công cụ xử lý media
sau khi tải. Có Chrome Extension (MV3) và Telegram bot. Mô hình freemium 5 hạng.

Sức mạnh thật nằm ở **độ phủ nền tảng và hạ tầng chống chặn** — không phải ở
tính năng xử lý video. Xem mục 6 để biết vì sao, và vì sao điều đó quyết định
hướng đi tiếp.

---

## 2. Tình trạng vận hành

Có **hai bản triển khai song song**, dễ nhầm:

| | Oracle VPS | Vibe Host |
|---|---|---|
| Domain | `dowloadvideo.io.vn` | FE `dvid.cmc-1.vibenode.matbao.ai`<br>BE `dvid-api.cmc-1.vibenode.matbao.ai` |
| Nguồn | GitLab | **GitHub** `junnyken/dowload-video` |
| Triển khai | `./deploy-vps.sh` | dashboard Vibe Host / MCP `vays` |

Đo trên Vibe Host ngày 24-09:

| | |
|---|---|
| Frontend | version **33**, vừa deploy hôm nay |
| Backend | version **101**, deploy 07-09 — **đi sau frontend** |
| `app_version` API | `1.6.0` |
| `platform-status` | `all_healthy: true` (5 nền tảng có báo: douyin, facebook, tiktok, twitter, xiaohongshu) |
| Giới hạn tốc độ | 60 req/phút/IP; 5 req/phút cho `/fetch-link` và `/bulk-download` |

**Rủi ro đang mở:** backend chạy code cũ hơn frontend 17 ngày. Không gây lỗi
hiện tại, nhưng khoảng cách càng xa càng dễ lệch hợp đồng API.

### Quy mô mã nguồn

| | |
|---|---|
| Backend | 175 file `.py`, **338 route**, **40 router** |
| Frontend | 153 file `.jsx/.js/.tsx` |
| Test | 62 file, **1497 passed / 15 skipped / 0 failed** |
| Service module | 37 |
| Hàng đợi Celery | `celery`, `downloads`, `bulk`, `light`, `media`, `analysis` |

---

## 3. Nền tảng hỗ trợ — 21

TikTok · Douyin · Facebook · Threads · Pinterest · SoundCloud · Spotify ·
YouTube · Instagram · Twitter/X · Reddit · Bilibili · Xiaohongshu · Lemon8 ·
Snapchat · VK · Twitch · Rumble · Odysee · Dailymotion · Podcast RSS

Mỗi nền tảng khai báo riêng khả năng: video, audio, ảnh, carousel, story, reels,
playlist, album, channel, profile, board, subreddit, thread, clip, VOD, phụ đề,
GIF — nên "hỗ trợ TikTok" và "hỗ trợ Spotify" không cùng nghĩa.

Chỉ 5 nền tảng có health check sống. **16 nền tảng còn lại không có tín hiệu
sức khoẻ nào** — hỏng thì chỉ biết khi người dùng báo. Đây là khoảng trống đáng
kể với một sản phẩm mà giá trị cốt lõi là "tải được".

### Hạ tầng chống chặn (phần khó sao chép nhất)

- **Cookie pool trên Redis** — nhiều cookie mỗi nền tảng, xoay vòng LRU, tự khoá
  15 phút khi gặp 429 và 6 tiếng khi gặp challenge, theo dõi hạn dùng, chấm điểm
  từng cookie, quản lý qua admin (16 endpoint). Biến môi trường chỉ là đường lui.
- **Proxy pool** + `youtube_proxy_health`
- **Cobalt API** nhiều instance, xoay vòng
- **Apify** cho ca khó
- Watchdog, circuit breaker, admission control, fairness control, delayed queue

---

## 4. Tính năng theo nhóm

**Tải về** — URL đơn · hàng loạt · kênh/playlist/profile · xem trước container
trước khi tải · ZIP không ghi đĩa (`/zip-stream`) · lịch tải · tiếp tục job dở ·
tìm kiếm video trong app · resolve input linh hoạt

**Xử lý media** — cắt clip · tạo GIF · ghép video · tách/gắn âm thanh · chèn
watermark · **xoá logo/watermark khỏi khung hình** · trích chapter · phụ đề
(tải, định dạng, QA) · dịch phụ đề · ASR (nhận dạng giọng nói)

**AI** — smart trim · smart clips · smart GIF · smart metadata · smart summary ·
phân tích media. Có hạn mức theo hạng (free 0/ngày → enterprise không giới hạn).

**Tổ chức** — Archive (ghim file khỏi bị xoá) · lịch sử · preset · đổi tên hàng
loạt · ghi chú theo video · gợi ý archive/lịch chạy

**Đội nhóm** — workspace · lời mời · phân quyền (owner/admin/editor/viewer) ·
audit log · luồng duyệt · export

**Tích hợp** — Chrome Extension MV3 · Telegram bot · webhook có ký HMAC ·
API key công khai · Partner API (`vgp_` keys) · multi-tenant · white-label

**Thanh toán** — 5 hạng · Stripe Customer Portal · usage/payment events ·
PaywallGate · QuotaBar

**Quản trị** — **28 tab**: Overview · Platforms · Cookies · Proxy · Queue ·
Jobs · Analytics · YouTube Gate · Ops Signals · Queue Health · Anomalies · Users ·
Config · Playbooks · Automation · Tenants · API Keys · Webhooks · Usage ·
AI Analysis · Billing · Presets · Access · Audit Log · Monitor · Manage ·
Enterprise · System

---

## 5. Bảng phân hạng (đọc thẳng từ `PLAN_DEFS`)

Hạng thật: `free`, `pro`, `team`, `api`, `enterprise`.
Bí danh: `starter`→free, `growth`→pro.

| tính năng | free | pro | team | api | enterprise |
|---|:--:|:--:|:--:|:--:|:--:|
| youtube_video | — | ✓ | ✓ | ✓ | ✓ |
| bulk_zip | — | ✓ | ✓ | ✓ | ✓ |
| spotify_full | — | ✓ | ✓ | ✓ | ✓ |
| cloud_save | — | ✓ | ✓ | ✓ | ✓ |
| chapters | — | ✓ | ✓ | ✓ | ✓ |
| **logo_inpaint** | — | ✓ | ✓ | ✓ | ✓ |
| ai_tools | — | ✓ | ✓ | ✓ | ✓ |
| api_key | — | ✓ | ✓ | ✓ | ✓ |
| webhook | — | ✓ | ✓ | ✓ | ✓ |
| priority_queue | — | ✓ | ✓ | ✓ | ✓ |
| team_workspace | — | — | ✓ | — | ✓ |
| partner_api | — | — | — | ✓ | ✓ |
| sso | — | — | — | — | ✓ |
| white_label | — | — | — | — | ✓ |

| giới hạn | free | pro | team | api | enterprise |
|---|:--:|:--:|:--:|:--:|:--:|
| tải/ngày | 10 | 100 | 500 | 1000 | ∞ |
| lô tối đa | 5 | 100 | 200 | 500 | ∞ |
| job đồng thời | 2 | 10 | 30 | 50 | ∞ |
| chất lượng tối đa | 1080p | 2160p | 2160p | 2160p | 2160p |
| lịch sử (ngày) | 7 | 90 | 90 | 90 | ∞ |
| AI/ngày | 0 | 50 | 200 | 100 | ∞ |
| API/tháng | 0 | 3000 | 10000 | 1000 | ∞ |
| ghế | 1 | 1 | 5 | 1 | ∞ |

**Bẫy đã gặp thật:** cấp hạng mà không đặt `billing_status` thì tài khoản vẫn bị
tính là `free`. Luật ở `entitlements.py`: *"none, on a paid tier → free (no
billing record at all)"*. Tài khoản admin `enterprise` tạo bởi migration
`021_create_admin_account.sql` dính đúng lỗi này — enterprise trên giấy nhưng mọi
cổng trả phí đều từ chối. **Migration cấp tier phải đặt luôn `billing_status`.**

---

## 6. Điểm mạnh thật sự — và chỗ yếu

### Mạnh

1. **Độ phủ 21 nền tảng** — rào cản sao chép cao, vì mỗi nền tảng là một cuộc
   chạy đua riêng với cơ chế chặn của họ.
2. **Hạ tầng chống chặn** (mục 3) — đây mới là tài sản, không phải UI. Cookie
   pool có xoay vòng/tự khoá/chấm điểm là thứ đối thủ nhỏ không có.
3. **Xử lý theo lô và theo container** — tải cả kênh/playlist/album, xem trước
   trước khi tải. Khác hẳn các tool "dán 1 link".
4. **Bề rộng tính năng quanh video** — tải xong còn cắt, ghép, GIF, phụ đề, dịch,
   xoá logo. Giữ người dùng trong app thay vì đẩy sang tool khác.
5. **Sẵn sàng cho B2B** — multi-tenant, partner API, white-label, webhook HMAC,
   audit log đã có mã.

### Yếu

1. **Không thấy được mình hỏng.** 16/21 nền tảng không có health check. Với sản
   phẩm mà giá trị là "tải được", đây là điểm yếu lớn nhất.
2. **Backend đi sau frontend 17 ngày** và deploy phụ thuộc một node duy nhất
   (`cmc-1` vừa bảo trì hơn một ngày, không deploy được gì trong thời gian đó).
3. **Chất lượng xoá logo chưa được chỉnh trên dữ liệu thật** — `dilate_px=12` là
   hằng số đoán sẵn, xem `docs/AI-HANDOFF.md` §5.
4. **Nhiều thứ đã build nhưng chưa ai xác nhận chạy** — xem mục 9.
5. **Phụ thuộc bên thứ ba** cho phần cốt lõi: Cobalt, Apify, ScraperAPI, proxy.
   Chi phí biến đổi theo lưu lượng, chưa thấy tài liệu về biên lợi nhuận.

---

## 7. Nợ kỹ thuật đã đo được

| chỗ | vấn đề |
|---|---|
| `frontend/src/pages/AnalyticsPage.jsx` | Ghép URL đôi `${t}https://dvid-api...` → hỏng chắc chắn |
| `frontend/src/pages/ApiDocsPage.jsx:208` | `href` tương đối → 502 trên layout 2 tên miền |
| `frontend/nginx.conf` | Khối `location /api/` proxy sang hostname `backend` không tồn tại → luôn 502. Là đường chết, nên bỏ hoặc trỏ đúng |
| `requirements.txt` | `opencv-contrib-python-headless` **không ghim phiên bản**; engine xoá logo cần `cv2.xphoto.INPAINT_SHIFTMAP` |
| `flow_cleanup.py` | `crop` và `blur` viết thẳng trong route handler → không test được |
| `POST /flow-cleanup/process` | Chạy đồng bộ tới 300s trong request; có hàng đợi `media` nhưng nhánh inpaint chưa dùng |
| `021_create_admin_account.sql` | Cấp tier không cấp `billing_status` (mục 5) |
| Lịch sử git | Hai dòng **không chung tổ tiên** (GitLab vs GitHub) — xem `docs/AI-HANDOFF.md` §2b |

---

## 8. `FEATURES.md` sai ở đâu

| `FEATURES.md` nói | Thực tế 24-09 |
|---|---|
| "Logo inpaint UI — **chưa expose web UI**" | SAI. Có ở 2 nơi, vừa sửa 4 lỗi, đã deploy |
| "Logo Inpaint UI" ở mục *ưu tiên thấp #14* | SAI. Đã xong |
| "User tier enforcement — **không enforce thực**" | SAI. `_require_pro` trả 401/402 thật, đo được |
| "Phase 20 Billing — **chưa deploy**" | Một phần sai. `useEntitlement`, `PaywallGate`, `BillingPage` đang chạy |

Các mục khác trong §8/§9 của `FEATURES.md` **chưa được kiểm chứng lại** — không
phải đã xác nhận, cũng không phải đã sai.

---

## 9. Những gì CHƯA được kiểm chứng

Ghi rõ để người lập kế hoạch không tưởng nhầm là đã xong:

- **Chỉ tính năng xoá logo được kiểm sâu** trong đợt 23–24/09. Mọi tính năng khác
  chỉ mới xác nhận *có mã và có route*, chưa ai chạy thật đầu-cuối trong đợt này.
- **Chuỗi xoá logo đầu-cuối** (tải → chọn vùng → xem trước → xử lý → tải kết quả)
  **chưa có ai chạy trọn** với tài khoản thật. Mã đã lên tới trình duyệt, đã đo.
- **16/21 nền tảng** không có tín hiệu sức khoẻ → không biết còn tải được không.
- **Các Phase 16–24** mà `FEATURES.md` liệt kê là "built, chưa deploy" — chưa
  kiểm lại cái nào thật sự đang chạy.
- **Chi phí vận hành** (Cobalt/Apify/ScraperAPI/proxy) — không có số liệu.
- **Lưu lượng và số người dùng thật** — không có số liệu.

---

## 10. Đầu vào cho việc lập kế hoạch

Không phải kế hoạch. Là những câu hỏi và dữ kiện mà một kế hoạch tốt phải trả lời.

### Câu hỏi cần chốt trước khi lên roadmap

1. **Sản phẩm bán cho ai?** Người dùng lẻ (freemium) hay doanh nghiệp (partner
   API, white-label)? Mã đã có cả hai nhưng đầu tư tiếp thì phải chọn.
2. **Giá trị cốt lõi là "tải được" hay "làm được gì sau khi tải"?** Quyết định
   này đổi hẳn thứ tự ưu tiên giữa mục 3 và mục 4.
3. **Biên lợi nhuận mỗi lượt tải là bao nhiêu?** Chưa có số. Không có nó thì
   không định giá hạng được, cũng không biết nên chặn hay mở hạng free.
4. **Có đo được người dùng rời đi ở bước nào không?** Chưa thấy funnel.

### Dữ kiện nên cân nhắc

- Điểm yếu #1 (không thấy được mình hỏng) **ảnh hưởng trực tiếp tới giá trị cốt
  lõi**. Nếu giá trị là "tải được", thì giám sát 21 nền tảng đáng ưu tiên hơn
  mọi tính năng mới.
- Nhiều tính năng đã có mã nhưng chưa xác nhận chạy (mục 9). **Kiểm kê cái đang
  có thường rẻ hơn và đáng tin hơn xây cái mới** — và làm xong mới biết thật sự
  còn thiếu gì.
- Hạ tầng chống chặn là tài sản khó sao chép nhất, nhưng cũng là chỗ tốn tiền
  nhất và dễ hỏng nhất. Bất kỳ kế hoạch nào cũng phải tính chi phí duy trì nó.
- Deploy đang phụ thuộc một node. Nếu sản phẩm nghiêm túc đi thương mại, đây là
  rủi ro phải xử lý trước khi bán cho khách doanh nghiệp.

### Ba hướng, kèm đánh đổi

| hướng | được | mất |
|---|---|---|
| **Củng cố cốt lõi** — giám sát nền tảng, kiểm kê tính năng đã có, tách deploy khỏi một node | Giảm rủi ro mất giá trị cốt lõi; biết mình đang có gì | Không có gì mới để quảng bá |
| **Đào sâu xử lý media** — hoàn thiện xoá logo, phụ đề, AI clip | Tăng giá trị mỗi người dùng; khác biệt hoá | Không giải quyết điểm yếu #1; cạnh tranh với tool chuyên dụng |
| **Đẩy B2B** — partner API, white-label, multi-tenant | Doanh thu/khách cao hơn | Đòi hỏi độ tin cậy mà hiện chưa chứng minh được (mục 9) |

Kế hoạch tốt nhiều khả năng **không chọn một hướng** mà xếp thứ tự: việc nào mở
khoá được việc nào.

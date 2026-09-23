# VidGrab — Bàn giao cho AI tiếp theo

> Viết ngày **23-09-2026**. Người viết: Claude Opus 5, sau một đợt sửa tính năng Xoá Logo.
> Đối tượng đọc: **một AI khác, không có quyền truy cập repo**, sắp được giao việc nâng cấp
> hoặc xây tiếp tool này.
>
> Mục đích của file: đưa bạn vào đúng bối cảnh, và **chỉ ra những chỗ tài liệu cũ đã sai**
> để bạn không làm theo. File này KHÔNG chép lại `FEATURES.md` — hãy đọc cả hai.

---

## 1. Tool là gì

**VidGrab** — web app tải video/nhạc từ ~20 nền tảng (TikTok, Douyin, Facebook, YouTube,
Bilibili, Spotify, SoundCloud, Threads, Pinterest, Instagram, Twitter/X, Reddit,
XiaoHongShu, Twitch, VK, Dailymotion, Odysee, Rumble, Lemon8, Podcast RSS…), kèm bộ công
cụ xử lý media sau khi tải: cắt clip, tạo GIF, ghép video, tách âm thanh, phụ đề, chèn
watermark, và **xoá logo/watermark khỏi khung hình**.

Có thêm Chrome Extension (MV3) và Telegram bot. Sản phẩm có phân hạng người dùng
(free / pro / team / enterprise / api) và một số tính năng bị khoá sau hạng Pro.

---

## 2. Chạy ở đâu — có HAI bản, đừng nhầm

| | Oracle VPS | Vibe Host |
|---|---|---|
| Domain | `dowloadvideo.io.vn` | frontend `dvid.cmc-1.vibenode.matbao.ai`<br>backend `dvid-api.cmc-1.vibenode.matbao.ai` |
| Triển khai | `./deploy-vps.sh`, SSH alias `vidgrab` | qua MCP `vays`, build từ nhánh `github/main` |
| Nguồn | GitLab `git.matbao.support` | **GitHub** `github.com/junnyken/dowload-video` |
| Tài liệu | `docs/RUNBOOK.md` | không có runbook riêng |

**Quan trọng:** trên Vibe Host, frontend và backend là **hai project ở hai tên miền khác
nhau**. Mọi lời gọi API trong frontend BẮT BUỘC đi qua `API_BASE` (`frontend/src/lib/apiBase.js`),
lấy từ `VITE_API_URL` lúc build. Đường dẫn tương đối (`fetch('/api/v1/...')`) sẽ **502** vì
nginx của frontend proxy `/api/` sang hostname `backend` vốn không tồn tại ở layout này.
Log nginx của chính server xác nhận: `backend could not be resolved (3: Host not found)`.

Trên layout `docker-compose` (một origin) thì đường tương đối lại đúng. Nên lỗi này **không
lộ ra khi chạy local** — đó là lý do nó sống sót lâu.

---

## 2b. ⚠️ Repo có HAI dòng lịch sử KHÔNG liên quan nhau — đọc trước khi `git` bất cứ thứ gì

Đây là bẫy tốn thời gian nhất, và người viết file này đã dính:

```
remote origin  → gitlab (git.matbao.support)   gốc 8deb7ca…
remote github  → github.com/junnyken/dowload-video   gốc 7e4f15d…

git merge-base main github/main  →  RỖNG
```

Hai nhánh **không có tổ tiên chung**. Nhánh `main` local (dòng GitLab) vượt `github/main`
437 commit, đồng thời **thiếu 147 commit** mà GitHub có.

**`github/main` mới là dòng đang phục vụ người dùng** — Vibe Host build từ đó.

Hệ quả bắt buộc nhớ:

- `git pull github main` khi đang ở `main` local sẽ **thất bại im lặng nếu bạn dùng `-q`**
  (git từ chối merge lịch sử không liên quan). Đừng dùng `-q` với `pull` trong repo này.
- Trước khi sửa gì, kiểm `git merge-base --is-ancestor <commit-github> HEAD` để chắc mình
  đang ở đúng dòng. Số liệu hai dòng lệch nhau thật: 338 vs 341 route, 62 vs 37 file test.
- Nhánh tạo từ dòng này **không merge được** vào dòng kia.

---

## 3. Stack & quy mô (đo ngày 23-09-2026)

| | |
|---|---|
| Frontend | React 19, Vite 8, TailwindCSS 4 — 153 file `.jsx/.js/.tsx` |
| Backend | FastAPI 0.115, Python 3.12 — 175 file `.py`, **338 route**, 40 router |
| Hàng đợi | Celery 5.6 + Redis 7 — queue `celery`, `downloads`, `bulk`, `light`, `media`, `analysis` |
| CSDL | Supabase (PostgreSQL) |
| Engine | yt-dlp, FFmpeg, OpenCV (contrib), Cobalt API v11 |
| Test | 62 file, **1497 passed / 15 skipped / 0 failed** |
| `app_version` | `1.6.0` (đọc từ `/api/v1/api-info` trên bản đang chạy) |

Chạy test: `cd backend && venv/bin/python -m pytest tests/ -q`
Build frontend: `cd frontend && VITE_API_URL=<backend-url> npm run build`

---

## 4. Đọc tài liệu nào — VÀ CHỖ NÀO ĐÃ SAI

| File | Dùng để | Cảnh báo |
|---|---|---|
| `FEATURES.md` (630 dòng) | Bản đồ tính năng đầy đủ nhất, có mục "Quick Reference cho AI" | **Phần trạng thái ghi cập nhật 2026-06-30 → đã cũ.** Xem bảng đính chính ngay dưới. |
| `docs/ARCH.md` | Stack + sơ đồ service + cây thư mục | Còn đúng về đại thể |
| `docs/RUNBOOK.md` | Vận hành, sự cố, rollback, backup | **Chỉ áp dụng cho Oracle VPS**, không áp dụng Vibe Host |
| `docs/qa-flow-cleanup.md` (458 dòng) | Checklist QA/UAT riêng cho tính năng Xoá Logo | Viết cho trang `FlowVeoCleanup` độc lập; luồng trong Dashboard khác |
| `docs/PRD.md`, `docs/TASKS.md` | Bối cảnh sản phẩm | Ngắn |
| `docs/ROADMAP_MINI_SPEC_2026-08.md` (505 dòng) | Lộ trình mini-spec tháng 8 | Chưa kiểm chứng lại |
| `docs/FEATURE_SPEC_transcript_translate.md` (478 dòng) | Đặc tả dịch phụ đề | Chưa kiểm chứng lại |
| `docs/ADMIN_DASHBOARD_DESIGN.md` (1127 dòng) | Thiết kế trang admin | Chưa kiểm chứng lại |

### Đính chính `FEATURES.md` — đã đo lại ngày 23-09

| `FEATURES.md` nói | Thực tế |
|---|---|
| "Logo inpaint UI — Endpoint có, **chưa expose web UI**" | **SAI.** UI đã có ở HAI nơi: nút "Xoá Logo" trong `DashboardContent.jsx` và trang riêng `FlowVeoCleanup.jsx` |
| "Logo Inpaint UI" nằm ở mục *ưu tiên thấp #14* | **SAI.** Đã build xong, vừa sửa 4 lỗi chặn |
| "User tier enforcement — Schema có, **không enforce thực**" | **SAI.** Có enforce: `_require_pro` trong `flow_cleanup.py` trả 401/402/503 thật, đo được trên bản chạy |
| "Phase 20 Billing — Built, **chưa deploy**" | **Một phần sai.** `useEntitlement`, `PaywallGate`, `BillingPage` đã có và đang chạy; cổng hạng Pro hoạt động |

Những mục khác trong §8/§9 của `FEATURES.md` tôi **chưa kiểm chứng lại** — đừng coi chúng
là đã xác nhận, cũng đừng coi là sai.

---

## 5. Tính năng Xoá Logo — trạng thái thật

Đây là phần vừa được làm, nên đáng tin nhất trong file này.

### Kiến trúc

```
frontend/src/components/DashboardContent.jsx   ← nút "Xoá Logo" (luồng chính, từ video đã tải)
frontend/src/components/FlowVeoCleanup.jsx     ← trang riêng (luồng upload file)
        │
        ▼
backend/app/api/flow_cleanup.py    ← 7 endpoint, gate sau hạng Pro
backend/app/api/flow_inpaint.py    ← ENGINE: SHIFTMAP + bù chuyển động theo thời gian
```

Endpoint: `POST /upload`, `GET /frame/{id}`, `POST /preview-frame`,
`GET /preview-clean/{id}`, `POST /process`, `POST /from-local`, `POST /no-logo`.
Tất cả trừ 2 endpoint `GET` ảnh đều sau `_require_pro`.

4 phương pháp: `natural` (mặc định — SHIFTMAP tổng hợp kết cấu + lấy nền thật từ khung lân
cận khi có chuyển động), `telea` (mờ, nhanh), `crop` (cắt cạnh chứa logo), `blur` (che mờ).

**Phạm vi:** chỉ logo NHÌN THẤY trên khung hình. SynthID và watermark vô hình **không** bị
đụng tới — đừng viết copy hay tiêu chí nghiệm thu nào ngụ ý ngược lại.

### 4 lỗi vừa sửa (đã trên `main`, commit `681ede3` và trước đó)

1. **Nút "Xoá Logo" không gửi token → 401 với mọi người.** `DashboardContent` gọi 3 endpoint
   chỉ với `Content-Type`. Hỏng kể cả tài khoản Pro, vì chỗ kiểm tra hạng chạy trong trình
   duyệt (`useEntitlement`) nên panel vẫn mở rồi lời gọi đầu tiên mới trượt.
2. **Ảnh xem trước + nút tải kết quả trả 502** — 3 URL tương đối trong `FlowVeoCleanup.jsx`.
3. **Cổng Pro đổ lỗi hạ tầng cho khách**: `except Exception: tier="free"` khiến mỗi lần
   Supabase chớp là người đã trả tiền nhận 402 "nâng cấp Pro". Nay trả 503
   `entitlement_check_failed`, vẫn chặn nhưng báo đúng và cho thử lại.
4. **nginx chặn 10MB** trong khi backend cho 500MB (chỉ dính layout compose).

### Test

`backend/tests/test_flow_inpaint_engine.py` (13 test, ~62s) là **bộ test đầu tiên thật sự
chạy engine**. Trước đó `test_inpaint_smoke.py` nghe như test engine nhưng chỉ kiểm bảng mã
lỗi và validate vùng — engine có 0 coverage.

Cách chấm điểm: mỗi clip sinh **hai lần** từ cùng nguồn tất định (một bản sạch = ảnh gốc,
một bản có hộp logo), engine chạy trên bản bẩn, rồi so với bản sạch **trong vùng logo**.

### Nút chỉnh chất lượng lớn nhất: `dilate_px`

Engine nở mặt nạ thêm `dilate_px=12` px trước khi lấp. Đây là hằng số đoán sẵn — engine
không đo logo nó đang xử lý. Số đo với ảnh gốc:

```
logo viền SẮC:  nở  0px → 92% sát gốc   |  nở 12px → 81%
logo viền MỀM:  nở  0px → 42%           |  6px → 69%  |  12px → 64%  |  20px → 57%
```

Chạy đối chứng tách biến: cho **cùng mức nở = 0**, `natural` và `telea` ra **y hệt nhau**
(MAE 12,9 / 92%) ⇒ chênh lệch giữa hai phương pháp là do NỞ MẶT NẠ, không phải do
SHIFTMAP/temporal. Nhánh temporal chỉ lấp được **0,6–1,7%** diện tích lỗ (38–104 px trên tổng 6052 px sau khi nở)
và chỉ chạy 10/30 khung.

**`dilate_px` vẫn giữ 12, CỐ Ý.** Fixture tổng hợp có viền sắc nhân tạo nên thiên vị việc
giảm nở; cần video thật mới chốt được. Công cụ đo đã có:

```bash
backend/venv/bin/python scripts/tune-logo-dilation.py <video> --preset lower-right
```

Nó báo seam/texture/flicker + xuất ảnh crop. Đã tự kiểm: các chỉ số phân biệt được "còn
viền" vs "hết viền", nhưng **không phân giải nổi 6 vs 12** (xếp ngược với ảnh gốc).

---

## 6. Đang kẹt gì (tính tới 23-09-2026)

**Bản vá đã ở trên `main` nhưng CHƯA deploy được.** Vibe Host node `cmc-1` fail ở tầng điều
phối: `get_build_logs` trả `status: error` mà **cả 11 chặng đều `pending`** — pipeline chưa
khởi động nổi, chưa chạm `Source Validation`. Đã fail ~8 lượt. Sự cố cùng chữ ký đã xảy ra
31-08 và 21-09, cả hai lần **tự khỏi** không cần thao tác gì.

Nên bản đang chạy cho người dùng vẫn là code cũ (bundle `BqOEdgXN.js`, container từ 21/09).

Thêm một chốt: `redeploy_project` trả `ENV_REQUIRED` đòi biến môi trường, quét **toàn bộ**
`docker-compose.yml` (42 biến của mọi service) rồi đòi mọi biến chưa đặt — **kể cả biến
service đó không dùng**. Form không cho để trống.

---

## 7. Việc chưa làm

### Liên quan Xoá Logo

| # | Việc | Ghi chú |
|---|---|---|
| 1 | **Xác minh end-to-end trên bản chạy** | Chưa ai chạy trọn chuỗi tải video → chọn góc → xem trước → xử lý → tải về với tài khoản Pro thật. Cần phiên đăng nhập, không tự động hoá được. |
| 2 | **Chốt `dilate_px` bằng video thật** | Xem §5. Cần footage có logo khó xoá. |
| 3 | **`crop` và `blur` chưa có test** | Hai nhánh này viết thẳng trong route handler `process_flow_cleanup`, không tách thành hàm nên không gọi test được. Muốn test phải tách hàm trước. |
| 4 | **Chọn vùng tự do (custom region)** | Model `ProcessRequest` nhận `region` tuỳ ý, nhưng UI Dashboard chỉ cho 4 góc cố định. `FlowVeoCleanup` có kéo thả. |
| 5 | **Tự dò vị trí logo** | Hiện 100% do người dùng chỉ. Không có bước phát hiện tự động. |
| 6 | **Xử lý chạy đồng bộ trong request** | `POST /process` chờ tới 300s ngay trong request. Có `media` queue Celery nhưng nhánh inpaint chưa dùng. Video dài dễ chạm timeout gateway. |

### Lỗi đã phát hiện, chưa sửa (ngoài phạm vi đợt vừa rồi)

| Chỗ | Lỗi |
|---|---|
| `frontend/src/pages/AnalyticsPage.jsx` | Ghép URL đôi: `` `${t}https://dvid-api.../api/v1/user/analytics` `` → chắc chắn hỏng |
| `frontend/src/pages/ApiDocsPage.jsx:208` | `href="/api/v1/error-codes"` tương đối → 502 trên Vibe Host |
| `frontend/nginx.conf` | Khối `location /api/` proxy sang hostname `backend` — vô dụng trên Vibe Host, luôn 502. Nên bỏ hoặc trỏ đúng. |
| `opencv-contrib-python-headless` | Không ghim phiên bản trong `requirements.txt`. Engine phụ thuộc `cv2.xphoto.INPAINT_SHIFTMAP`; bản mới bỏ đi là mất nhánh SHIFTMAP (có fallback, đã test). |

### Việc lớn hơn

Xem `FEATURES.md` §8 và §9 — nhưng **đọc kèm bảng đính chính ở §4 file này**.

---

## 8. Bẫy đã đo được — đọc trước khi mất thời gian

1. **Đừng dùng heuristic MÀU để kiểm tra "logo đã xoá chưa".** `testsrc2` của ffmpeg có sẵn
   dải magenta; pan qua đó là vùng lại đầy magenta dù engine làm đúng hoàn hảo. Tôi đã suýt
   báo engine hỏng ("khung tệ nhất còn 63,9% logo") vì cái này. Dùng **ảnh gốc** làm chuẩn.

2. **`gradients` lavfi KHÔNG tất định** — reseed mỗi lần render, hai bản khác hẳn nhau
   (đo được: MAE ngoài vùng logo = 86/255). Một "ground truth" dựng trên nó cho kết quả
   5,6% trông y như lỗi engine. `testsrc2` thì tất định (MAE ngoài vùng = 0,000). Nền tĩnh
   thì tự sinh ảnh bằng numpy rồi `-loop 1 -i bg.png`.

3. **Luôn tự kiểm fixture**: đo MAE ở phần NGOÀI vùng logo, phải ≈ 0. Nếu không thì ground
   truth là rác. Để cái này thành `assert` trong test, đừng kiểm tay một lần.

4. **MAE thiên vị làm mờ.** Nền mượt thì TELEA luôn ăn điểm so với thuật toán tổng hợp kết
   cấu. Dùng MAE làm **sàn chống hồi quy**, KHÔNG dùng để xếp hạng chất lượng.

5. **Test engine phải tự kiểm bằng đột biến**: chèn lỗi (bỏ bước fill / fill ra màu đen /
   tắt nhánh temporal) rồi xác nhận test ĐỎ, sau đó khôi phục. Không làm bước này thì không
   biết test có bắt được gì không.

6. **Người dùng chạy BUNDLE, không chạy src.** Sửa frontend xong phải build với
   `VITE_API_URL` đúng rồi `grep` chuỗi cần tìm trong file `dist/assets/*.js`. `frontend/Dockerfile`
   khai `ARG VITE_API_URL=""` — **mặc định rỗng**, nên nếu biến không được truyền lúc build
   thì bundle ra URL tương đối và **toàn bộ app 502, không có thông báo lỗi nào**.

7. **`ADMIN_ALLOWED_IPS` không có giá trị giả nào an toàn.** Code khớp **chính xác chuỗi**,
   không hiểu CIDR: `*`, `0.0.0.0/0`, `not-configured` đều khiến MỌI IP thật bị 403 và mất
   trang admin. Chỉ để rỗng (= cho tất cả), hoặc điền IP thật.

8. **Biến `*_COOKIES_B64` nhận base64, không nhận cookie thô.** Giá trị sai định dạng thì
   `base64.b64decode` ném lỗi → trả `None` → coi như không có cookie (an toàn). Nhưng tránh
   `-` và `!!`: chúng giải mã "thành công" ra chuỗi rỗng rồi GHI RA file cookie rỗng.

9. **Kiểm mình đang ở dòng lịch sử nào trước khi đo bất cứ số nào.** Xem §2b. Tôi đã khảo
   sát nhầm dòng và viết ra 4 con số sai trong chính file này trước khi phát hiện.

10. **Sửa một chỗ thì quét cả repo.** Luồng inpaint có HAI bản (`DashboardContent` và
   `FlowVeoCleanup`). Đợt trước sửa `DashboardContent` mà bỏ quên `FlowVeoCleanup`; đợt này
   ngược lại. Đã thêm chốt chặn: `TestFrontendSendsCredentials` trong
   `backend/tests/test_flow_cleanup_access.py` tự đọc route nào có `_require_pro` rồi soi
   frontend — endpoint mới gắn cổng là tự động được bảo vệ.

---

## 9. Quy ước khi làm việc trên repo này

- Commit message viết dài, giải thích **tại sao** và **đo được gì**, không chỉ "fix bug".
  Xem `git log` để bắt nhịp.
- Không tuyên bố "xong" khi chưa đo. Bộ test xanh không đồng nghĩa tính năng chạy được —
  4 lỗi ở §5 đều lọt qua 1497 test.
- Chuỗi hiển thị cho người dùng viết bằng tiếng Việt.
- Đổi logic production (ngưỡng, luật, mặc định) thì báo và xin xác nhận trước, kèm số đo
  và cỡ mẫu — đừng đổi dựa trên cảm nhận.

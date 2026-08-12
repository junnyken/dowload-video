# VidGrab — Roadmap tính năng (MINI-SPEC format) — 08/2026

> Sinh theo `MINI_SPEC_PLAYBOOK.md`. Dựa trên: (1) audit kỹ thuật trực tiếp trên bản deploy VAYS hôm nay,
> (2) gap analysis nội bộ đã có sẵn ở `FEATURES.md` §8-9 (cập nhật 2026-06-30), (3) nghiên cứu đối thủ thị trường 2025-2026.

---

## 1. Trạng thái xác thực hôm nay (live trên VAYS)

Đã verify trực tiếp (không suy đoán) trên `dvid-api.cmc-1.vibenode.matbao.ai` + `dvid.cmc-1.vibenode.matbao.ai`:

| Hạng mục | Trước | Sau | Bằng chứng |
|---|---|---|---|
| YouTube | Tắt (`YOUTUBE_ENABLED=false` mặc định) | **Bật, tải thật thành công** | `POST /fetch-link` trả về file thật 720p/28.58MB + đầy đủ metadata/subtitle |
| Celery worker | Không chạy (container chỉ có uvicorn) | Chạy nền trong cùng container, đã consume queue `downloads/bulk/light/media/analysis/celery` | log `celery@... ready` |
| Celery beat (15 tác vụ định kỳ: hết hạn job, flush analytics, hết hạn subscription...) | Không chạy → tác vụ nền im lặng không chạy | Chạy nền cùng container | log `beat: Starting...` |
| Redis | Chưa có | VAYS Redis 7.2, auth qua user `default` | kết nối thành công |
| CORS frontend↔backend | Chặn (`Failed to fetch`) | Cho qua qua `FRONTEND_URL` env | header `access-control-allow-origin` đúng |
| `/api/v1/platform-status` | — | `all_healthy: true`, 11 platform đều `constrained/no_cookies` (không phải lỗi — chỉ là chưa có cookie riêng nên giới hạn tốc độ, đúng thiết kế) | response thật |

**Giới hạn còn lại của deploy này (VAYS single-container, không phải docker-compose 8-container gốc):** không có cookie pool nạp sẵn (Twitter/IG/TikTok...), không có proxy IPRoyal, không có bgutil-pot/cobalt-api sidecar. Đây là lý do platform-status báo `constrained` thay vì `optimal` — không phải bug.

---

## 2. Vị thế cạnh tranh (tóm tắt nghiên cứu thị trường 2025-2026)

**Bối cảnh thị trường đang dịch chuyển mạnh:**
- Y2mate — đối thủ lớn nhất mảng YouTube — **bị IFPI buộc đóng cửa vĩnh viễn 10/2025** vì vi phạm bản quyền. SnapTik tăng ads mạnh 2025 làm mất UX. 4K Video Downloader chuyển bắt buộc trả phí 02/2026.
- → Định vị "công cụ hợp pháp, minh bạch, cho nội dung riêng tư/được phép" (không phải piracy) không còn là lựa chọn đạo đức mà là **yêu cầu sống còn của mô hình kinh doanh**.

**Bảng-chuẩn (table-stakes) 2026** — mọi đối thủ nghiêm túc đều đã có: xoá watermark, xuất MP4+MP3, không cần đăng nhập, đa nền tảng cơ bản, extension + web app song song. VidGrab đã đạt đủ bảng-chuẩn này.

**Yếu tố tạo khác biệt thật sự (differentiating), theo nghiên cứu:**
1. **Độ bền trước anti-bot crackdown** — yt-dlp giữ ~98% success rate nhờ patch 2 tuần/lần; cobalt.tools (đối thủ open-source được yêu thích nhất) lại hay fail với YouTube. → **Đây là moat cạnh tranh thật, không phải tính năng phụ.**
2. **AI-native**: auto-caption, AI clip/highlight, dịch transcript, AI audio — 4K Downloader mới thêm 2026, chưa phổ biến. VidGrab **đã build Phase 18 (Smart trim/clip/GIF/summary) nhưng chưa wire** — cơ hội đi trước.
3. **Batch/bulk + API cho dev/automation** — hầu hết công cụ free (ssstik, snaptik, sssinstagram) **không có** batch thật; chỉ app desktop mới có. VidGrab **đã có** channel/bulk download + Partner API (Phase 16) built nhưng chưa deploy — đây là khoảng trống thị trường VidGrab đã đứng sẵn, chỉ cần launch.
4. **Trust/minh bạch** — cobalt.tools thắng vì open-source, không ads, không tracking — đối trọng với làn sóng "fake download button/malware" đang bị người dùng phàn nàn nhiều.

**Kết luận định vị:** VidGrab không thiếu tính năng nền tảng (đã ngang bảng-chuẩn), cái thiếu là (a) **launch những gì đã xây nhưng chưa deploy** (Phase 16 API, Phase 18 AI) — đúng vào 2 khoảng trống thị trường rõ nhất, và (b) **độ tin cậy** (job resume, circuit breaker) — vì đây là moat thật của ngành, không phải nice-to-have.

---

## 3. Roadmap ưu tiên (đối chiếu nội bộ × thị trường)

| # | Hạng mục | Nguồn | Ưu tiên | Effort |
|---|---|---|:---:|:---:|
| 1 | Bật YouTube | Nội bộ #1 | ✅ **Đã xong hôm nay** | — |
| 2 | Job resume sau Celery restart | Nội bộ #5 + thị trường (reliability = moat) | 🔴 P0 | Trung bình — **MINI-SPEC bên dưới** |
| 3 | Deploy Phase 18 AI Media (smart clip/trim/GIF/summary) | Nội bộ #13 + thị trường (AI-native = differentiator mới nổi) | 🔴 P0 | Trung bình — **MINI-SPEC bên dưới** |
| 4 | Deploy Phase 16 Partner API + Multi-tenant | Nội bộ #11 + thị trường (batch/API = khoảng trống đối thủ) | 🟠 P1 | Cao — **MINI-SPEC bên dưới** |
| 5 | Cookie Twitter/X + Instagram pool | Nội bộ #2,#3 | 🟠 P1 | Thấp (config, không cần MINI-SPEC riêng) |
| 6 | Subtitle download hoàn thiện | Nội bộ #4 | 🟡 P2 | Trung bình |
| 7 | Deploy Phase 20 Billing/Stripe | Nội bộ #6 | 🟡 P2 | Trung bình |
| 8 | Deploy Phase 19 PWA | Nội bộ #7 | 🟢 P3 | Trung bình |
| 9 | Admin RBAC (bỏ hardcoded password) | Nội bộ #10 | 🟠 P1 (bảo mật) | Trung bình |

Mục 5, 6, 8, 9 đã có mô tả đủ rõ trong `FEATURES.md` §9 — không lặp lại thành MINI-SPEC ở đây để tránh trùng lặp tài liệu; khi bắt tay làm, sinh MINI-SPEC riêng từng cái theo skeleton mục 9 của Playbook.

---

## 4. MINI-SPEC R25 — Job Resume & Reliability Hardening

**Name:** Job Resume & Reliability Hardening sau Celery restart
**Parent phase:** Post-launch reliability (không thuộc phase build tính năng mới)
**Author:** AI (phối hợp Thiên Triều) · **Date:** 2026-08-12

### Context
- Bắt buộc đọc: `backend/app/core/job_lease.py` (đã có logic partial), `backend/app/core/recovery.py`, `FEATURES.md` §5.2 (Periodic Tasks), §8 mục 3.
- Trạng thái hiện tại: khi Celery worker restart (deploy mới, OOM, crash), job đang `processing` bị bỏ dở, không tự resume — người dùng thấy job "kẹt" vĩnh viễn cho tới khi hết hạn.
- Quyết định kiến trúc phải giữ nguyên: không đổi state machine `download_jobs.status` hiện có; không đổi hợp đồng API `/api/v1/jobs/{batch_id}`.

### Goal
Sau khi worker restart (kể cả restart do redeploy), mọi job đang `processing` phải tự động được phát hiện và resume hoặc chuyển sang `failed` có lý do rõ ràng — không còn job "kẹt" im lặng.

### Constraints (Guardrails)
1. Không viết lại `job_lease.py` từ đầu — chỉ hoàn thiện phần đã có (additive).
2. Không đổi ý nghĩa các `job_stage` hiện có.
3. Không tự bịa trạng thái mới nếu chưa có bằng chứng cần thiết.
4. Nếu không xác định được job có resume được không → degrade về `failed` với `error_message` rõ ràng, không giả định thành công.
5. Không bỏ qua audit trail — mọi lần resume/fail phải ghi log + (nếu có) Telegram alert như cơ chế hiện có.
6. Tương thích với deploy single-container (VAYS) lẫn docker-compose gốc — không giả định có nhiều worker instance.

### Scope
- **A. Domain model:** audit `download_jobs.job_stage` các giá trị hiện có (`stale` đã tồn tại theo query trong log runtime hôm nay: `status=eq.processing&job_stage=eq.stale`); xác nhận đã đủ hay cần thêm `resuming`.
- **B. Service/engine:** hoàn thiện `job_lease.py` — lease timeout detection đã có, cần nối vào `[Recovery:startup]` hook (đã thấy chạy mỗi lần container start, log "clean — no stuck jobs") để nó **thực sự resume** thay vì chỉ report.
- **C. API contract:** không cần endpoint mới — job resume là background, chỉ cần `/jobs/{batch_id}` phản ánh đúng trạng thái sau resume.
- **D. UI surfaces:** `history` page hiển thị đúng trạng thái mới nếu job chuyển `failed` với lý do — không cần UI mới.
- **E. Tests:** unit cho lease-expiry detection; integration giả lập kill worker giữa chừng rồi restart, verify job không kẹt.

### Audit Before Build
- Đã xác nhận (log runtime hôm nay): app có `[Recovery:startup]` chạy đúng lúc container start, hiện chỉ "clean — no stuck jobs" (chưa test trường hợp có job thật kẹt).
- Gap xác nhận: `job_lease.py` tồn tại nhưng theo `FEATURES.md` là "partial" — cần đọc kỹ để biết chính xác phần nào thiếu trước khi build (không đoán).

### Design Choice
- Tái dùng toàn bộ `job_lease.py` + `[Recovery:startup]` hook đã có, chỉ nối phần "detect" với phần "act" (resume `process_video_task` từ điểm `job_stage` cuối cùng, hoặc fail rõ ràng nếu không resume được).
- Không dùng thêm state machine mới — bám theo `job_stage=stale` đã tồn tại.

### Test Plan
- Unit: lease timeout mapping đúng threshold.
- Integration: kill -9 worker container giữa 1 job `processing`, restart, verify job không còn ở trạng thái processing quá lease timeout.
- Regression: job đã `completed` không bị resume nhầm.
- Live verification: 1 lần trên VAYS deploy thật (restart backend giữa lúc có job chạy, quan sát kết quả).

### Success Criteria
- Không còn job nào ở `processing` quá `lease_timeout` mà không có hành động (resume hoặc fail rõ lý do).
- `[Recovery:startup]` log số job thực sự đã resume/fail (không còn luôn luôn "0 stuck jobs" mặc định).

---

## 5. MINI-SPEC R26 — Wire Phase 18 AI Media vào Web UI

**Name:** Kích hoạt Smart Clip/Trim/GIF/Summary (Phase 18) cho người dùng thật
**Parent phase:** Differentiation vs. đối thủ (AI-native features)
**Author:** AI (phối hợp Thiên Triều) · **Date:** 2026-08-12

### Context
- Bắt buộc đọc: `FEATURES.md` §8 "Phase 18 — AI Media" (đã build, chưa wired), `backend/app/services/smart_clips.py`, `smart_gif.py`, `smart_summary.py`, `smart_trim.py`, frontend `SmartActionsPanel.jsx` (theo FEATURES.md đã tồn tại).
- Trạng thái hiện tại: backend service + celery queue `analysis` đã có (đã xác nhận queue này đang được worker consume live hôm nay); tier gating free/pro/enterprise đã thiết kế; nhưng UI chưa expose cho người dùng thật.
- Quyết định giữ nguyên: không đổi FFmpeg heuristic đã build; không đổi tier gating logic đã thiết kế (free 5/day → pro 50/day → enterprise unlimited).

### Goal
Người dùng thật bấm được Smart Clip / Trim / GIF / Summary ngay trên web app sau khi tải xong 1 video — không cần biết endpoint API.

### Constraints (Guardrails)
1. Không rebuild FFmpeg heuristic — chỉ wire UI → API đã có.
2. Không đổi tier limit đã thiết kế (5/50/unlimited) trừ khi có yêu cầu nghiệp vụ mới.
3. Nếu quota hết → thông báo rõ ràng (`needs_upgrade`), không âm thầm fail hoặc âm thầm cho free vượt quota.
4. Không bypass gate tier khi chưa có billing thật (Phase 20 chưa deploy) — tạm thời tier mặc định là gì cần audit trước khi bật cho public.
5. Giữ nguyên `celery-analysis` queue routing đã có, không tạo queue song song.

### Scope
- **A. Domain model:** audit bảng job/task liên quan AI media đã có sẵn migration chưa (theo FEATURES.md có nhắc `phase18_ai_analysis.sql` — cần audit đã apply trên Supabase live chưa).
- **B. Service/engine:** đã có — chỉ audit lại còn tương thích sau các thay đổi gần đây không.
- **C. API contract:** audit endpoint hiện có cho smart clip/trim/gif/summary trong `routes.py`/tasks — bám đúng response shape đã thiết kế.
- **D. UI surfaces:** wire `SmartActionsPanel.jsx` vào flow sau khi 1 job hoàn thành (nút hành động cạnh kết quả tải), hiển thị trạng thái xử lý + kết quả.
- **E. Tests:** integration full flow tải → chọn Smart Action → nhận kết quả; regression tier quota không bị vượt.

### Audit Before Build
- Cần xác nhận: migration `013_phase18_ai_analysis.sql` (thấy trong `database/migrations/`) đã apply trên Supabase production chưa — audit bằng cách query schema thật trước khi bật UI, tránh 500 lỗi cột thiếu (đã thấy pattern lỗi tương tự hôm nay với `download_jobs.url`).

### Design Choice
- Tái dùng toàn bộ backend Phase 18 đã build; chỉ làm cầu nối UI + audit migration/tier trước khi public.
- Rollout theo cờ tính năng (feature flag kiểu `YOUTUBE_ENABLED` đã có pattern sẵn) để bật dần, không big-bang.

### Test Plan
- Unit: tier quota calculation.
- Integration: 1 video thật → smart trim → nhận file kết quả thật (live verification bắt buộc, không mock).
- Regression: user free vượt 5/day bị chặn đúng, không silent fail.

### Success Criteria
- Người dùng thấy và dùng được ít nhất 1 trong 4 tính năng AI Media (ưu tiên Smart Clip vì gần nhất với core use-case tải video) trên production, có tier gating hoạt động thật.
- Không có 500 lỗi do migration thiếu.

---

## 6. MINI-SPEC R27 — Launch Phase 16 Partner API (đứng vào khoảng trống thị trường)

**Name:** Deploy Partner API / Multi-tenant (Phase 16) ra bên ngoài
**Parent phase:** Đi vào khoảng trống thị trường (batch/API — đối thủ free hầu như không có)
**Author:** AI (phối hợp Thiên Triều) · **Date:** 2026-08-12

### Context
- Bắt buộc đọc: `FEATURES.md` §8 Phase 16 (`vgp_` API keys, 9 endpoint, HMAC webhook, white-label), `docker-compose.enterprise.yml`, `backend/app/api/partner.py`, `backend/app/core/partner_auth.py`, `backend/app/core/tenant.py`.
- Trạng thái hiện tại: built, chưa deploy. Theo nghiên cứu thị trường, đây chính xác là khoảng trống — ssstik/snaptik/sssinstagram (đối thủ free phổ biến nhất) **không có** API cho dev/automation.
- Quyết định giữ nguyên: không đổi schema `vgp_` key hiện có; không đổi HMAC webhook contract đã thiết kế.

### Goal
Có ít nhất 1 partner/đối tác thật gọi được Partner API thành công end-to-end (tạo key → gọi tải → nhận webhook) trên môi trường production.

### Constraints (Guardrails)
1. Không rebuild multi-tenant schema — chỉ audit + deploy phần đã có.
2. Không public API key tự động cho mọi user — cần cơ chế duyệt/cấp key có kiểm soát (ít nhất là admin cấp tay ở giai đoạn đầu).
3. Rate limit/quota theo tenant phải hoạt động thật trước khi mời đối tác ngoài dùng — không launch "quota chỉ có UI, chưa enforce".
4. Không launch multi-tenant enterprise đầy đủ (`docker-compose.enterprise.yml`) trong 1 bước — tách nhỏ: trước tiên chỉ bật Partner API trên deploy hiện có, multi-tenant/white-label để sau.
5. Nếu thiếu bằng chứng key đã hoạt động đúng (test thật) → không công bố "đã có Partner API" ra ngoài.

### Scope
- **A. Domain model:** audit bảng tenant/partner key đã migrate trên Supabase production chưa.
- **B. Service/engine:** audit `partner_auth.py` (`vgp_` key validation) hoạt động đúng trên deploy hiện tại (đã set `SCRAPERAPI_API_KEY` v.v. hôm nay — audit các biến môi trường Phase 16 cần mà chưa set, ví dụ HMAC secret).
- **C. API contract:** liệt kê đủ 9 endpoint theo `FEATURES.md`, xác nhận từng cái hoạt động thật trên deploy hiện tại (không phải chỉ code tồn tại).
- **D. UI surfaces:** cần tối thiểu 1 trang "API Keys" cho user tự xem/tạo key (nếu chưa có self-service theo `FEATURES.md` §8 "Public API Key ... không có self-service, ❌ chưa build" — đây là gap con cần làm thêm, không phải chỉ deploy).
- **E. Tests:** integration full flow key→call→webhook; regression rate-limit theo tenant.

### Audit Before Build
- Gap đã biết từ `FEATURES.md`: **"Public API Key: Credits endpoint có, không có self-service — ❌ chưa build"**. Nghĩa là Phase 16 API tồn tại ở tầng backend/admin nhưng **chưa có UI tự-cấp-key cho partner** — đây là phần thật sự cần build mới, không chỉ "deploy lại cái đã có". MINI-SPEC này phải tách rõ 2 việc: (1) deploy/audit backend đã built, (2) build mới UI self-service — ưu tiên (1) trước, (2) có thể tách MINI-SPEC con riêng nếu effort lớn hơn dự kiến.

### Design Choice
- Giai đoạn 1 (MINI-SPEC này): audit + deploy backend Phase 16 đã có trên môi trường hiện tại, cấp key thủ công qua admin cho 1 đối tác pilot, verify end-to-end thật.
- Giai đoạn 2 (MINI-SPEC riêng, follow-up): UI self-service tạo key.
- Không launch marketing "có Partner API" cho tới khi giai đoạn 1 verify sống.

### Test Plan
- Unit: HMAC signature validation.
- Integration: tạo `vgp_` key thật → gọi endpoint tải → nhận webhook thật.
- Regression: tenant A không thấy được data tenant B (kiểm tra lại pattern IDOR — dự án SocialHub cùng team từng có nhiều lỗi IDOR loại này, ưu tiên test kỹ).
- Live verification: 1 pilot partner thật.

### Success Criteria
- 1 đối tác pilot gọi được toàn bộ flow (key → tải → webhook) thành công trên production, có log/audit trail.
- Không phát sinh cross-tenant data leak (test riêng, không giả định an toàn).

---

## 7. Follow-ups / Out-of-scope (ghi nhận, chưa làm)

- Cookie pool Twitter/X + Instagram mở rộng — config-only, làm trước khi 3 MINI-SPEC trên nếu cần gấp (effort thấp nhất).
- Subtitle download hoàn thiện, Billing/Stripe (Phase 20), PWA (Phase 19), Admin RBAC — đã có mô tả đủ trong `FEATURES.md` §9, sinh MINI-SPEC riêng khi tới lượt.
- Mở rộng platform mới (Vimeo, Weibo, Naver TV...) — chưa có tín hiệu nhu cầu thật từ user, không ưu tiên tới khi có bằng chứng.
- Chrome Extension lên Chrome Web Store chính thức — effort cao (phải migrate `webRequest` → MV3 `declarativeNetRequest`), cân nhắc riêng.

# Phương án liên tục khi host vibe1 chết (Phase 34A-2, task #6332)

Mục tiêu: quyết định cùng owner (cổng G2 cần "có phương án dự phòng"). Tài liệu này chỉ so sánh, **chưa dựng gì**. Mục "chưa kiểm" = chưa thử thật.

## Rủi ro đã quan sát
Một host duy nhất. Sáng 13/10/2026 vibe1 suy giảm 09:40-11:30 (khoảng 2 giờ): web, API, app, tiện ích cùng mất kết nối, deploy lỗi "fetch failed 6/6", log công cụ báo `wings_error`; không có cảnh báo, owner tự phát hiện (`docs/PHASE-34-0-EVIDENCE.md`). Hiện chạy trên vibe1: backend (`dowload-video-backend`, 1,6 CPU / 4 GB), web (`dowload-video-frontend`), Redis. CSDL ở Supabase (ngoài host). Hai máy Cobalt (`dvid-cobalt`, `dvid-cobalt-2`) dùng chung 1 IP ra ngoài. Bộ cài app nằm trên GitHub (`junnyken/vidgrab-releases`), nên **tải app mới vẫn được khi vibe1 chết**; app đã cài vẫn dùng yt-dlp nội bộ cho một phần nền tảng (mức độ: chưa kiểm).

## So sánh

| | A. Giám sát + runbook (hiện có) | B. Máy dự phòng nguội, nhà cung cấp KHÁC | C. Tự chuyển máy có kiểm sức khoẻ |
|---|---|---|---|
| Làm gì | UptimeRobot 5 phút + trang trạng thái + `incident-playbook.md` | Một máy ở nhà cung cấp khác, đã cài sẵn, **tắt**; sự cố thì bật và đổi DNS | Hai máy chạy; bộ cân bằng tải/DNS tự bỏ máy hỏng |
| Phát hiện | <= 10 phút (có) | như A | tự động |
| Thời gian khôi phục | Phụ thuộc nhà cung cấp (13/10: ~2 giờ) | ước 30-90 phút nếu đã diễn tập; **chưa đo** | vài phút (chưa kiểm) |
| Dữ liệu mất | Không (chờ máy sống lại) | Hàng đợi Redis + bộ đếm; CSDL Supabase còn nguyên | như B, phức tạp hơn nếu chia Redis |
| Chi phí | 0 | tiền máy + thời gian duy trì; **cần kiểm giá hiện tại** | tiền máy thứ hai chạy liên tục + tiền dịch vụ chuyển máy; **cần kiểm giá hiện tại** |
| Việc owner | Đọc cảnh báo, gọi nhà cung cấp | Làm theo checklist dưới đây | Gần như không, nhưng phải cấu hình đúng một lần |

## B. Máy dự phòng nguội: cần sao chép gì
1. **Mã nguồn / image**: repo `junnyken/dowload-video` (riêng tư) nhánh `main` đã build được; ghi lại commit đang chạy. Cần quyền kéo repo (deploy key) ở nhà cung cấp mới. Image build tại chỗ từ `docker-compose.yml` hoặc `docker-compose.small.yml` (nếu máy nhỏ).
2. **Danh sách biến môi trường (chỉ TÊN, giá trị lấy từ kho mật khẩu của owner)**: nhóm Supabase (`SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_KEY`), admin (`ADMIN_PASSWORD`, `ADMIN_TOKEN`, `ADMIN_ALLOWED_IPS`), nhà cung cấp (`APIFY_TOKEN`, `GEMINI_API_KEY`, `COBALT_API_KEY`, `COBALT_API_URL`, `TELEGRAM_BOT_TOKEN`), Redis (`REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), khoá ký lượt tải (`VIDGRAB_CLAIM_*`), cờ tính năng (`ASR_*`, `WATCH_*`, `CHINA_ACCESS_*`, `DESKTOP_*`), `VITE_API_URL` lúc build web, `VIDGRAB_ENV_REV`. Danh sách đầy đủ phải trích từ môi trường vibe1 hiện tại (công cụ `list_env` chỉ trả tên): **chưa kiểm đầy đủ**. Script `scripts/backup.sh` có sẵn để ghi tên biến nhưng viết cho Oracle VPS (chưa kiểm trên Vibe Host).
3. **Redis**: cấu hình `--appendonly yes --maxmemory ... --maxmemory-policy volatile-lru`; bản sao `dump.rdb`/AOF định kỳ ra ngoài host. Chưa có sao lưu tự động ra ngoài vibe1 (việc của 34A-2 phần sao lưu); cấu hình admin và kho cookie nằm trong Redis nên **phải có bản sao riêng**.
4. **Máy Cobalt**: dựng 1 máy Cobalt cùng nhà cung cấp mới (IP khác, nên tăng dự phòng thật) hoặc để app lùi về yt-dlp.
5. **Tên miền**: web/API hiện ở `*.vibe1.tinhgon.xyz` (không do owner quản trị DNS, chưa kiểm). Muốn đổi DNS được phải có **tên miền riêng** trỏ bằng CNAME/A, TTL thấp (≈ 300 giây). Tiện ích Chrome và app đang trỏ cứng `dvid-api.vibe1.tinhgon.xyz`: app/tiện ích cũ **sẽ không tự đổi** sang tên miền mới nếu không có bản cập nhật. Đây là điểm yếu lớn nhất của B.
6. **Các bước chuyển khi sự cố**: (1) xác nhận vibe1 chết > ngưỡng đã thoả thuận; (2) bật máy dự phòng, kéo bản sao Redis mới nhất; (3) điền env, build, chạy; (4) kiểm `/health`; (5) đổi DNS; (6) cập nhật trang trạng thái; (7) khi vibe1 sống lại, **tắt một bên** để tránh hai nơi cùng ghi.
7. Diễn tập 1 lần vào môi trường sạch và đo thời gian trước khi tin con số ước tính.

## C. Tự chuyển máy
Ví dụ: Cloudflare Load Balancing hoặc DNS failover có health check. Cần: 2 máy luôn bật (hoặc 1 máy nóng + 1 nguội tự bật), tên miền riêng đặt sau dịch vụ đó, Redis dùng chung hoặc chấp nhận mất hàng đợi khi chuyển. Giá dịch vụ chuyển máy và giá máy thứ hai: **cần kiểm giá hiện tại** trên trang giá của nhà cung cấp (chưa kiểm, không ghi số ước lượng).

## Khuyến nghị cho quy mô hiện tại (beta, vài chục người, G0 -> G1)
- **Giữ A ngay**, bổ sung hai việc nhỏ không cần owner đặt env: sao lưu Redis/cấu hình ra ngoài vibe1 và một lần khôi phục diễn tập (đã nằm trong 34A-2).
- **Đừng dựng B/C trước G1**: chi phí và việc bảo trì lớn hơn thiệt hại của ~2 giờ ngừng khi chưa quảng bá. B không dùng được nếu chưa có **tên miền riêng** — nên chốt việc này trước.
- **Xét B trước G2** (tăng trưởng, cần uptime >= 99%/14 ngày): 2 giờ ngừng như 13/10 một mình đã chiếm ~0,6% của 14 ngày. Nếu uptime đo thật dưới 99% hoặc vibe1 lặp lại sự cố, chọn B; C chỉ khi có doanh thu (G3).
- **Luật owner "không thêm cài đặt chỉ có env"**: B và C đều đòi owner/Claude đổi DNS và env ở nơi khác — mọi bước phải thành checklist có sẵn, không bắt owner nhớ biến. Hiện còn ba công tắc khẩn cấp chỉ có qua env/API, chưa có nút Admin: `DESKTOP_UPDATER_ENABLED`, `WATCH_KILL_SWITCH`, công tắc dừng nền tảng Trung Quốc (chỉ API). Đề xuất đưa thành nút trong Admin trước khi mở rộng (việc riêng, chưa tạo task).

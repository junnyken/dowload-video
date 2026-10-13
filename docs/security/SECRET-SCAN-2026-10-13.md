# Quét bí mật lịch sử git — 13/10/2026 (Phase 34B, task #6330)

Công cụ: gitleaks 8.30.1 (bản phát hành chính thức, kiểm checksum), chế độ che 100% giá trị, quét `--all` = 768 commit của cả hai dòng lịch sử (GitHub `github/*` và GitLab `origin/*`). Bổ sung: liệt kê theo tên file mọi đường dẫn từng được commit (`.env`, cookie, khoá, cơ sở dữ liệu…). Không có giá trị bí mật nào được in, ghi log hay commit trong quá trình này.

## Kết quả: 18 phát hiện gitleaks + 2 file theo tên

### Phải xử lý (bí mật thật nằm trong lịch sử repo công khai)

| # | Gì | Ở đâu (commit) | Còn trong bản hiện tại? | Việc cần làm |
|---|---|---|---|---|
| 1 | `ZENROWS_API_KEY` (40 ký tự, giá trị thật) | `README.md` dòng 37 — 8deb7ca5 (28/04), 7e4f15d5 (12/08) | Không (nay là chỗ trống `your_…`) | **Owner thu hồi khoá trên trang ZenRows** (coi như đã lộ) |
| 2 | `SCRAPERAPI_KEY` (32 ký tự, giá trị thật) | `README.md` dòng 38 — 8deb7ca5, 7e4f15d5 | Không | **Owner thu hồi khoá trên trang ScraperAPI** (ScraperAPI đã thôi dùng từ 08/10) |
| 3 | Cookie đăng nhập Google (`.google.com`, `.accounts.google.com`) của tài khoản phụ | `tools/cookies_randallhughes6080.txt` — bde256a (20/05), gỡ khỏi theo dõi ở 54cd5ff (02/07) | Không | Coi tài khoản đó là đã lộ; không dùng lại |
| 4 | `tools/throwaway_accounts.json` (323 dòng thông tin tài khoản phụ) | bde256a, gỡ ở 54cd5ff | Không | Coi mọi tài khoản trong đó là đã lộ |

Gỡ khỏi bản hiện tại KHÔNG xoá khỏi lịch sử: ai clone repo công khai vẫn đọc được. Hai cách triệt để: (a) chuyển repo sang riêng tư (ngăn người mới), và/hoặc (b) viết lại lịch sử + force-push (ảnh hưởng mọi bản clone, máy Vibe Host build từ repo — cần kế hoạch riêng). Dù chọn cách nào, **thu hồi khoá (#1, #2) là bắt buộc** vì có thể đã bị sao chép.

### Không phải bí mật (công khai theo thiết kế hoặc dữ liệu thử)

| Phát hiện | Lý do an toàn |
|---|---|
| Khoá Supabase `anon` trong `desktop/src/lib/config.ts`, `frontend/.env` (cũ), `frontend/dist/*.js` (cũ) | Khoá `anon` vốn công khai trong ứng dụng; quyền do RLS quyết. Bản cũ thuộc project Supabase khác (ref `mrehhw…`). Rủi ro thật là RLS (đã ghi nhận riêng), không phải khoá. |
| reCAPTCHA site key `6Lf…` trong `tools/create_throwaway.py` | Site key công khai trên mọi trang có reCAPTCHA |
| Token/JWT trong `backend/tests/*`, `docs/*`, `browser_login.rs` | Giá trị giả dùng cho test / tài liệu |
| `backend/downloader.db` | File rỗng (0 byte) |

## Việc tiếp theo (task #6330)
1. Owner thu hồi ZenRows + ScraperAPI cũ (2 phút mỗi trang).
2. Đổi khoá ký lượt tải k1 theo `docs/desktop/P3-KEY-ROTATION.md` (khoá k1 từng hiện trong một phiên làm việc với AI — coi như lộ).
3. Owner chốt: repo riêng tư / repo phát hành công khai riêng / viết lại lịch sử.
4. Đưa gitleaks vào kiểm trước khi push (chặn bí mật mới).

# Tools — Tiện ích quản lý hệ thống

## create_throwaway.py — Tạo tài khoản throwaway tự động

Tự động tạo tài khoản Google/YouTube throwaway dùng 5sim.net để nhận OTP,
Playwright để điền form, và tự upload cookie vào Cookie Pool.

### Cài đặt

```bash
cd tools
pip install playwright faker requests python-dotenv
playwright install chromium

# Linux server (không có màn hình thật) — cần Xvfb:
sudo apt-get install -y xvfb
Xvfb :99 -screen 0 1280x800x24 -ac &
export DISPLAY=:99
```

---

## Cấu hình proxy — BẮT BUỘC nếu chạy từ server/quán CF

> **Tại sao cần proxy?**  
> IP data-center (server, quán CF) bị Google đánh điểm thấp → Google yêu cầu
> **QR code verification** thay vì SMS OTP. QR code cần thiết bị thật, không tự động hóa được.  
> IP nhà (residential) được tin tưởng → Google gửi SMS bình thường.

### Mode A — Chạy tại nhà (miễn phí, khuyến nghị)

Không cần cấu hình proxy gì cả. Chạy thẳng trên máy nhà:

```bash
# Windows / Mac: không cần Xvfb, browser hiện ra tự nhiên
python create_throwaway.py --platform youtube \
    --captcha-key CAP-xxxxx

# Linux tại nhà: cần Xvfb nếu không có màn hình
Xvfb :99 -screen 0 1280x800x24 -ac &
DISPLAY=:99 python3 create_throwaway.py --platform youtube \
    --captcha-key CAP-xxxxx
```

---

### Mode B — SSH tunnel từ quán CF về nhà (miễn phí)

Dùng máy nhà làm proxy — traffic qua IP nhà, chạy script ở quán CF.

**Bước 1: Trên máy nhà** — expose SSH port ra ngoài (nếu không có IP tĩnh):

```bash
# Cài ngrok (miễn phí tại ngrok.com), rồi:
ngrok tcp 22
# → Nhận URL kiểu: tcp://0.tcp.ap.ngrok.io:XXXXX
```

> Nếu router nhà có IP tĩnh hoặc đã port-forward 22: bỏ qua bước ngrok.

**Bước 2: Tại quán CF / workspace** — tạo SOCKS5 tunnel:

```bash
# Thay <user>@<host>:<port> theo thông tin máy nhà
ssh -D 1080 -N -f your_user@0.tcp.ap.ngrok.io -p XXXXX

# Kiểm tra tunnel đang chạy:
curl --proxy socks5://localhost:1080 https://api.ipify.org  
# → phải hiện IP nhà
```

**Bước 3: Set trong `.env`**:

```env
HOME_SSH_PROXY=socks5://localhost:1080
```

Hoặc dùng thẳng flag:

```bash
DISPLAY=:99 python3 create_throwaway.py --platform youtube \
    --proxy socks5://localhost:1080 \
    --captcha-key CAP-xxxxx
```

---

### Mode C — Webshare.io Residential (có phí)

> **⚠️ Kết quả thực tế**: Webshare **free tier / datacenter proxy** bị chặn hoàn toàn
> tới `accounts.google.com` — Chromium không thể tải trang đăng ký. Đây là chính sách
> anti-abuse của Webshare, không phải lỗi script.  
> **Phải mua gói Residential** (~$4/GB) để vượt qua giới hạn này.

Webshare.io có **10 proxy miễn phí** nhưng là **datacenter**. Chạy `--test-proxy` để kiểm tra trước:

**Bước 1: Đăng ký Webshare.io**

1. Vào [webshare.io](https://webshare.io) → Sign up miễn phí
2. Dashboard → **Proxy** → **Rotating Endpoint**
3. Copy username và password

**Bước 2: Set trong `.env`**:

```env
WEBSHARE_USERNAME=your_webshare_username
WEBSHARE_PASSWORD=your_webshare_password
```

Script tự build endpoint: `http://username:password@p.webshare.io:80`

---

### Cấu hình `.env` đầy đủ

```env
# ── BẮT BUỘC ──────────────────────────────────────────────────
FIVESIM_API_KEY=eyJhbGci...          # Đăng ký tại 5sim.net

# ── CAPTCHA solver (chọn 1) ───────────────────────────────────
CAPSOLVER_API_KEY=CAP-xxxxx          # capsolver.com (~$6 nạp)
# TWOCAPTCHA_API_KEY=xxxxx           # 2captcha.com (alternative)

# ── PROXY (chọn 1 mode, để trống nếu chạy tại nhà) ───────────

# Mode B: SSH tunnel về nhà
# HOME_SSH_PROXY=socks5://localhost:1080

# Mode C: Webshare.io
# WEBSHARE_USERNAME=your_username
# WEBSHARE_PASSWORD=your_password

# Hoặc custom proxy bất kỳ
# THROWAWAY_PROXY=http://user:pass@host:port

# ── Upload vào Cookie Pool ────────────────────────────────────
ADMIN_API_URL=https://dowload-video.mk.dev.matbao.ai
ADMIN_API_KEY=your_admin_key
```

---

### Chạy script

```bash
# ✅ Làm trước tiên: kiểm tra proxy có kết nối được Google không
python3 create_throwaway.py --test-proxy
# Exit 0 = OK, Exit 1 = bị chặn → cần đổi proxy

# Xem số dư 5sim + giá proxy
python3 create_throwaway.py --list-balance

# Tạo 1 tài khoản (proxy tự động theo .env)
python3 create_throwaway.py --platform youtube \
    --captcha-key CAP-xxxxx

# Tạo 3 tài khoản liên tiếp
python3 create_throwaway.py --platform youtube --count 3 \
    --captcha-key CAP-xxxxx

# Force dùng proxy cụ thể (ghi đè .env)
python3 create_throwaway.py --platform youtube \
    --proxy "http://user:pass@p.webshare.io:80" \
    --captcha-key CAP-xxxxx

# Chạy headless hoàn toàn (không hiện browser)
DISPLAY=:99 python3 create_throwaway.py --platform youtube \
    --captcha-key CAP-xxxxx --headless
```

---

### Quy trình hoạt động

```
1. resolve_proxy()  → Chọn proxy tự động (Webshare / SSH / direct)
2. 5sim.net         → Mua số điện thoại Vietnam ($0.08/số)
3. Playwright       → Mở Chrome, warm-up session tại google.com
4. State machine    → Điền form tự động (name → birthday → username → password)
5. reCAPTCHA bypass → grecaptcha.enterprise.execute() trong browser
6. Phone verify     → Nhập số điện thoại, đợi SMS OTP từ 5sim (2 phút)
7. OTP              → Nhập mã, hoàn tất tạo tài khoản
8. Cookie export    → Lưu cookies.txt (cần có SID/HSID để valid)
9. Admin API        → Upload vào Cookie Pool tự động
```

---

### Chi phí ước tính (per account)

| Dịch vụ | Chi phí |
|---------|---------|
| 5sim.net số điện thoại | $0.08 - $0.20 |
| CapSolver CAPTCHA | ~$0.003 |
| Webshare proxy (nếu dùng) | ~$0.01 - $0.05 |
| **Tổng** | **~$0.10 - $0.25** |

---

### Troubleshooting

| Lỗi | Nguyên nhân | Fix |
|-----|-------------|-----|
| `--test-proxy` exit 1 (Webshare) | Webshare datacenter chặn accounts.google.com | Mua Webshare Residential hoặc dùng Mode A/B |
| `Google yêu cầu xác minh QR code` | IP data-center | Dùng Mode A (nhà) hoặc Mode B (SSH tunnel) |
| `Google restarted flow sau password` | IP bị đánh giá thấp | Dùng residential proxy |
| `Không nhận được OTP` | Số bị block hoặc timeout | 5sim tự hoàn tiền |
| `Timeout khi load page` | Proxy chậm | Thử proxy khác |
| `Session cookies missing` | Flow chưa hoàn tất | Xem screenshot tại `/tmp/throwaway_debug_*` |
| `Browser does not support socks5 proxy authentication` | Chromium không hỗ trợ SOCKS5 auth | Dùng `http://` proxy thay vì `socks5://` |

### Lưu ý bảo mật

- File `throwaway_accounts.json` chứa email + password — đã gitignore
- File `cookies_USERNAME.txt` — đã gitignore  
- Nên tạo 1-2 account/ngày, không tạo hàng loạt → tránh IP block

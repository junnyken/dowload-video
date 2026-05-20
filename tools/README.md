# Tools — Tiện ích quản lý hệ thống

## create_throwaway.py — Tạo tài khoản throwaway tự động

Tự động tạo tài khoản Google/YouTube throwaway dùng 5sim.net để nhận OTP,
Playwright để điền form, và tự upload cookie vào Cookie Pool.

### Cài đặt

```bash
cd tools
pip install playwright faker requests python-dotenv
playwright install chromium
```

### Cấu hình `.env` (trong thư mục gốc dự án)

```env
# Bắt buộc
FIVESIM_API_KEY=your_key_here        # Đăng ký tại 5sim.net (~$0.15-0.30/số)

# Tùy chọn — giải CAPTCHA tự động
TWOCAPTCHA_API_KEY=your_key_here     # Đăng ký tại 2captcha.com (~$3/1000 CAPTCHA)

# Upload vào Cookie Pool admin API
ADMIN_API_URL=https://dowload-video.mk.dev.matbao.ai
ADMIN_API_KEY=your_admin_key
```

### Xem số dư và giá

```bash
python create_throwaway.py --list-balance
```

### Tạo 1 tài khoản YouTube (browser hiện ra, tự giải CAPTCHA)

```bash
python create_throwaway.py --platform youtube
```

### Tạo 3 tài khoản liên tiếp

```bash
python create_throwaway.py --platform youtube --count 3
```

### Tạo với country cụ thể (rẻ hơn)

```bash
python create_throwaway.py --platform youtube --country vietnam
```

### Chạy hoàn toàn tự động (không cần người dùng)

```bash
python create_throwaway.py --platform youtube --count 2 \
    --captcha-key YOUR_2CAPTCHA_KEY --headless
```

### Upload cookie thủ công (file cookies.txt có sẵn)

```bash
python create_throwaway.py --upload-cookie /path/to/cookies.txt --platform youtube
```

### Quy trình hoạt động

```
1. 5sim.net → Mua số điện thoại (country rẻ nhất)
2. Faker     → Tạo tên/ngày sinh/username/password ngẫu nhiên
3. Playwright → Mở Google Signup, điền form tự động
4. 5sim.net → Poll OTP từ SMS (timeout 2 phút)
5. 2captcha → Giải CAPTCHA (nếu có key) | Thủ công (nếu không có key)
6. Export   → Lưu cookies.txt định dạng Netscape
7. Admin API → Upload vào Cookie Pool tự động
8. Log      → Lưu thông tin tài khoản vào throwaway_accounts.json
```

### Chi phí ước tính (per account)

| Dịch vụ | Chi phí |
|---------|---------|
| 5sim.net số điện thoại | $0.10 - $0.30 |
| 2captcha CAPTCHA | $0.003 |
| **Tổng** | **~$0.10 - $0.30** |

### Lưu ý

- File `throwaway_accounts.json` chứa email + password — **giữ bí mật**
- Cookies backup trong `cookies_USERNAME.txt` tự động tạo
- Google có thể yêu cầu phone verification tùy IP — dùng residential proxy
- Tỉ lệ thành công: ~60-80% (semi-auto), ~40-60% (full-auto với headless)
- Nên tạo 1-2 account/ngày, không tạo hàng loạt cùng lúc → tránh IP block

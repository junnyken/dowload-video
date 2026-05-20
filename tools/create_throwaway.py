#!/usr/bin/env python3
"""
Throwaway Account Creator
=========================
Tự động tạo tài khoản throwaway cho YouTube/Google
sử dụng 5sim.net để nhận OTP + Playwright để tạo tài khoản.

Yêu cầu:
  pip install playwright faker requests python-dotenv
  playwright install chromium

Chạy:
  python create_throwaway.py --platform youtube --count 2
  python create_throwaway.py --platform youtube --count 1 --captcha-key 2captcha_api_key
  python create_throwaway.py --list-balance    # Xem số dư 5sim
  python create_throwaway.py --upload-cookie cookies.txt --platform youtube  # Upload thủ công

Biến môi trường (.env):
  FIVESIM_API_KEY=your_key_here
  TWOCAPTCHA_API_KEY=your_key_here (optional)
  ADMIN_API_URL=http://localhost:8000  (hoặc URL backend)
  ADMIN_API_KEY=your_admin_key
"""

import argparse
import json
import os
import random
import re
import string
import sys
import time
from pathlib import Path
from typing import Optional

import requests

try:
    from faker import Faker
except ImportError:
    print("❌ Cần cài faker: pip install faker")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
FIVESIM_API_KEY = os.getenv("FIVESIM_API_KEY", "")
TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")
ADMIN_API_URL = os.getenv("ADMIN_API_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

fake = Faker("en_US")

PLATFORM_TO_FIVESIM = {
    "youtube": "google",
    "google": "google",
    "tiktok": "tiktok",
    "facebook": "facebook",
    "instagram": "instagram",
}

# ─────────────────────────────────────────────
# 5sim.net API
# ─────────────────────────────────────────────

class FiveSimClient:
    BASE = "https://5sim.net/v1"

    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })

    def get_balance(self) -> float:
        r = self.session.get(f"{self.BASE}/user/profile")
        r.raise_for_status()
        return float(r.json().get("balance", 0))

    def get_prices(self, service: str, country: str = "vietnam") -> dict:
        """Lấy giá và số lượng available cho service tại country."""
        r = self.session.get(f"{self.BASE}/guest/products/{country}/any")
        r.raise_for_status()
        data = r.json()
        return {country: {service: data.get(service, {})}} if service in data else {}

    def get_all_country_prices(self, service: str) -> list:
        """Lấy giá của service từ tất cả country, sort theo giá."""
        countries = ["vietnam", "russia", "ukraine", "philippines", "india", "indonesia"]
        rows = []
        for c in countries:
            try:
                r = self.session.get(f"{self.BASE}/guest/products/{c}/any")
                r.raise_for_status()
                data = r.json()
                if service in data:
                    info = data[service]
                    rows.append((c, float(info.get("Price", 999)), int(info.get("Qty", 0))))
            except Exception:
                pass
        rows.sort(key=lambda x: x[1])
        return rows

    def buy_number(self, service: str, country: str = "vietnam") -> dict:
        """
        Mua số điện thoại để nhận OTP.
        country: vietnam, russia, india, ukraine, etc.
        Returns: {id, phone, status, ...}
        """
        url = f"{self.BASE}/user/buy/activation/{country}/any/{service}"
        r = self.session.get(url)
        r.raise_for_status()
        return r.json()

    def check_sms(self, order_id: int, timeout: int = 120) -> Optional[str]:
        """
        Poll OTP từ số đã mua. Trả về mã OTP hoặc None nếu timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.session.get(f"{self.BASE}/user/check/{order_id}")
            r.raise_for_status()
            data = r.json()
            status = data.get("status", "")
            sms_list = data.get("sms", [])

            if sms_list:
                sms_text = sms_list[-1].get("text", "")
                code = _extract_otp(sms_text)
                if code:
                    print(f"  ✉  OTP nhận được: {code}")
                    return code

            if status in ("canceled", "finished", "timeout"):
                print(f"  ✗  Số bị hủy/timeout (status={status})")
                return None

            print(f"  ⏳ Chờ SMS... ({int(deadline - time.time())}s còn lại)")
            time.sleep(8)

        return None

    def cancel_order(self, order_id: int) -> None:
        try:
            self.session.get(f"{self.BASE}/user/cancel/{order_id}")
        except Exception:
            pass

    def finish_order(self, order_id: int) -> None:
        try:
            self.session.get(f"{self.BASE}/user/finish/{order_id}")
        except Exception:
            pass

    def find_cheapest_country(self, service: str) -> str:
        """Tìm country rẻ nhất có sẵn số."""
        try:
            rows = self.get_all_country_prices(service)
            for country, cost, qty in rows:
                if qty > 0:
                    print(f"  🌍 Country rẻ nhất: {country} (${cost:.3f}, {qty} available)")
                    return country
        except Exception:
            pass
        return "vietnam"


def _extract_otp(text: str) -> Optional[str]:
    """Trích OTP từ nội dung SMS."""
    patterns = [
        r'\b(\d{6})\b',
        r'\b(\d{5})\b',
        r'G-(\d{6})',
        r'code[:\s]+(\d+)',
        r'verification[:\s]+(\d+)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# ─────────────────────────────────────────────
# Profile generator
# ─────────────────────────────────────────────

def generate_profile() -> dict:
    """Tạo thông tin người dùng ngẫu nhiên."""
    first = fake.first_name()
    last = fake.last_name()
    year = random.randint(1985, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)

    # Username: tên + số ngẫu nhiên
    username_base = f"{first.lower()}{last.lower()}{random.randint(100, 9999)}"
    username_base = re.sub(r"[^a-z0-9]", "", username_base)

    # Password mạnh
    chars = string.ascii_letters + string.digits + "!@#$%"
    password = "".join(random.choices(chars, k=16))
    # Đảm bảo có chữ hoa, số, ký tự đặc biệt
    password = password[:13] + "A1!"

    return {
        "first_name": first,
        "last_name": last,
        "username": username_base,
        "password": password,
        "birthday_year": str(year),
        "birthday_month": str(month),
        "birthday_day": str(day),
    }


# ─────────────────────────────────────────────
# CAPTCHA solvers
# ─────────────────────────────────────────────

GOOGLE_ENTERPRISE_KEY = "6Lf-_ekqAAAAAO4AXrJISaHw4_bW76NcfwhLN7Is"


def solve_captcha(site_key: str, page_url: str, api_key: str, service: str = "capsolver",
                  is_enterprise: bool = False) -> Optional[str]:
    """Giải reCAPTCHA — tự động chọn service."""
    if service == "capsolver" or (api_key and api_key.startswith("CAP-")):
        return _solve_capsolver(site_key, page_url, api_key, is_enterprise)
    return _solve_2captcha(site_key, page_url, api_key, is_enterprise)


def _solve_capsolver(site_key: str, page_url: str, api_key: str,
                     is_enterprise: bool = False) -> Optional[str]:
    """Giải reCAPTCHA v2/Enterprise qua CapSolver API."""
    try:
        task_type = "ReCaptchaV2EnterpriseTaskProxyless" if is_enterprise else "ReCaptchaV2TaskProxyless"
        print(f"  🤖 CapSolver: tạo task ({task_type})...")
        r = requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": api_key,
            "task": {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": site_key,
                "isInvisible": is_enterprise,
            },
        }, timeout=15)
        data = r.json()
        if data.get("errorId", 0) != 0:
            print(f"  ✗ CapSolver error: {data.get('errorDescription')}")
            return None

        task_id = data["taskId"]
        print(f"  🤖 CapSolver taskId: {task_id}, đang giải...")

        for _ in range(30):  # max 150s
            time.sleep(5)
            r2 = requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": api_key,
                "taskId": task_id,
            }, timeout=15)
            d2 = r2.json()
            status = d2.get("status", "")
            if status == "ready":
                token = d2["solution"]["gRecaptchaResponse"]
                print("  ✓ CapSolver: giải thành công!")
                return token
            if d2.get("errorId", 0) != 0:
                print(f"  ✗ CapSolver failed: {d2.get('errorDescription')}")
                return None

        print("  ✗ CapSolver timeout")
        return None
    except Exception as e:
        print(f"  ✗ CapSolver exception: {e}")
        return None


def _solve_2captcha(site_key: str, page_url: str, api_key: str,
                    is_enterprise: bool = False) -> Optional[str]:
    """Giải reCAPTCHA v2 qua 2captcha.com API (~$3/1000, người ~20s)."""
    try:
        r = requests.post("https://2captcha.com/in.php", data={
            "key": api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "enterprise": 1 if is_enterprise else 0,
            "json": 1,
        }, timeout=15)
        data = r.json()
        if data.get("status") != 1:
            print(f"  ✗ 2captcha submit lỗi: {data}")
            return None

        task_id = data["request"]
        print(f"  🤖 2captcha task ID: {task_id}, chờ giải...")

        for _ in range(24):  # max 120s
            time.sleep(5)
            r2 = requests.get("https://2captcha.com/res.php", params={
                "key": api_key, "action": "get", "id": task_id, "json": 1,
            }, timeout=15)
            d2 = r2.json()
            if d2.get("status") == 1:
                return d2["request"]
            if d2.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                print(f"  ✗ 2captcha error: {d2}")
                return None

        print("  ✗ 2captcha timeout")
        return None
    except Exception as e:
        print(f"  ✗ 2captcha exception: {e}")
        return None


# ─────────────────────────────────────────────
# Playwright — Google account creation
# ─────────────────────────────────────────────

def create_google_account(
    profile: dict,
    phone: str,
    fivesim: FiveSimClient,
    order_id: int,
    captcha_key: Optional[str] = None,
    headless: bool = False,
) -> Optional[Path]:
    """
    Mở trình duyệt, tạo tài khoản Google, trả về Path của cookies.txt.
    headless=False: hiện browser để user nhìn thấy + handle CAPTCHA thủ công.
    headless=True: chạy background (cần captcha_key).
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        print("❌ Cần cài playwright: pip install playwright && playwright install chromium")
        return None

    cookie_file = Path(f"/tmp/throwaway_{profile['username']}_cookies.txt")

    with sync_playwright() as pw:
        # Nếu Xvfb đang chạy trên :99 → dùng full browser (fingerprint tốt hơn headless-shell)
        _display = os.environ.get("DISPLAY", "")
        _use_headless = headless
        if not _use_headless and not _display:
            _use_headless = True  # không có display, buộc headless

        browser = pw.chromium.launch(
            headless=_use_headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-gpu" if _use_headless else "",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # Stealth: ẩn webdriver fingerprint
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = {runtime: {}};
        """)
        page = context.new_page()

        try:
            debug_dir = Path(f"/tmp/throwaway_debug_{profile['username']}")
            debug_dir.mkdir(exist_ok=True)

            # Warm-up: visit google.com để build session signals trước khi signup
            print("  🌡️  Warm-up session...")
            page.goto("https://www.google.com", wait_until="networkidle", timeout=20_000)
            time.sleep(random.uniform(2, 4))
            page.goto("https://accounts.google.com", wait_until="networkidle", timeout=20_000)
            time.sleep(random.uniform(1, 2))

            def snap(label: str) -> None:
                """Chụp screenshot + lưu HTML + URL để debug."""
                try:
                    path = debug_dir / f"{label}.png"
                    page.screenshot(path=str(path), full_page=True)
                    content = page.content()
                    (debug_dir / f"{label}.html").write_text(content)
                    print(f"    📸 {path} | URL: {page.url[:80]}")
                except Exception:
                    pass

            def click_next() -> None:
                """Click nút Next/Continue."""
                for sel in ['button:has-text("Next")', 'button:has-text("Continue")',
                             'button[type="submit"]']:
                    btn = page.locator(sel)
                    if btn.count() > 0:
                        btn.first.click()
                        return

            def wait_stable(timeout: int = 8000) -> None:
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout)
                except Exception:
                    time.sleep(1)

            def submit_with_captcha_retry(step_label: str) -> bool:
                """
                Click Next. Nếu URL không đổi (invisible reCAPTCHA chặn):
                  1. Thử gọi grecaptcha.enterprise.execute() trong browser (token gắn với session)
                  2. Nếu thất bại, fallback sang CapSolver ProxylessTask
                Returns True nếu page advance, False nếu fail.
                """
                import urllib.parse as _up
                before_url = page.url
                click_next()
                wait_stable(12_000)
                after_url = page.url
                snap(f"{step_label}_after_next")

                if after_url != before_url:
                    return True  # advance thành công

                print(f"  ⚠️  Bị chặn tại {step_label}, bypass reCAPTCHA Enterprise...")

                # Lấy site key
                anchor = page.locator('iframe[src*="recaptcha"][src*="anchor"]')
                iframe_src = (anchor.first.get_attribute("src") or "") if anchor.count() > 0 else ""
                _qs = _up.parse_qs(_up.urlparse(iframe_src).query)
                detected_key = (_qs.get("k") or [GOOGLE_ENTERPRISE_KEY])[0]
                is_enterprise = "enterprise" in iframe_src

                # ── Phương án 1: Execute grecaptcha trong browser (token gắn session) ──
                token = None
                try:
                    print(f"  🔑 Thử grecaptcha.enterprise.execute() trong browser...")
                    token = page.evaluate(
                        "(key) => typeof grecaptcha !== 'undefined' && grecaptcha.enterprise "
                        "? grecaptcha.enterprise.execute(key, {action: 'signup'}).catch(() => null)"
                        ": Promise.resolve(null)",
                        detected_key
                    )
                    if token:
                        print(f"  ✓ Token từ browser session!")
                except Exception as e:
                    print(f"  ⚠️  grecaptcha.execute() thất bại: {e}")
                    token = None

                # ── Phương án 2: CapSolver ProxylessTask (fallback) ──
                if not token and captcha_key:
                    print(f"  🤖 Fallback sang CapSolver...")
                    token = solve_captcha(detected_key, page.url, captcha_key, is_enterprise=is_enterprise)

                if not token:
                    print(f"  ✗ Không lấy được token tại {step_label}")
                    return False

                # Inject token vào form + submit
                page.evaluate("""(t) => {
                    document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {
                        el.value = t;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                    });
                    try {
                        let clients = window.___grecaptcha_cfg.clients;
                        Object.values(clients).forEach(c => {
                            Object.values(c).forEach(obj => {
                                if (obj && typeof obj.callback === 'function') obj.callback(t);
                            });
                        });
                    } catch(e) {}
                }""", token)
                time.sleep(2)
                click_next()
                wait_stable(12_000)
                snap(f"{step_label}_after_captcha")

                if page.url != before_url:
                    print(f"  ✓ Bypass thành công!")
                    return True

                print(f"  ✗ Vẫn bị chặn sau token inject tại {step_label}")
                return False

            print(f"\n  🌐 Mở Google account creation...")
            page.goto(
                "https://accounts.google.com/signup/v2/createaccount"
                "?flowName=GlifWebSignIn&flowEntry=SignUp",
                wait_until="networkidle",
                timeout=30_000,
            )
            snap("01_start")

            # ── Bước 1: Tên ──────────────────────────────────
            print(f"  ✏  Nhập tên: {profile['first_name']} {profile['last_name']}")
            page.fill('input[name="firstName"]', profile["first_name"])
            page.fill('input[name="lastName"]', profile["last_name"])
            click_next()
            wait_stable()
            snap("02_after_name")

            # ── Bước 2: Ngày sinh & giới tính ────────────────
            print(f"  🎂 Nhập ngày sinh: {profile['birthday_day']}/{profile['birthday_month']}/{profile['birthday_year']}")

            # Đợi birthday page load xong
            try:
                page.wait_for_selector('input[name="day"], input[aria-label="Day"]',
                                       state="visible", timeout=10_000)
                time.sleep(1.5)
            except Exception:
                pass

            # Month: Material Design dropdown — click, chờ list mở, click option
            month_names = ["January","February","March","April","May","June",
                           "July","August","September","October","November","December"]
            try:
                month_num = int(profile["birthday_month"])
                # Click dropdown để mở
                page.locator('div[id="month"]').click(timeout=5000)
                time.sleep(1.2)
                # Thử click option theo data-value, fallback theo text
                done = False
                for sel in [f'li[data-value="{month_num}"]',
                             f'li:has-text("{month_names[month_num-1]}")']:
                    opt = page.locator(sel)
                    if opt.count() > 0:
                        opt.first.click(force=True, timeout=3000)
                        done = True
                        break
                if not done:
                    page.keyboard.press("Escape")
                else:
                    time.sleep(0.5)
            except Exception:
                pass

            # Day: click + type (không dùng fill vì Google component có custom binding)
            try:
                day_field = page.locator('input[name="day"]').first
                day_field.click()
                time.sleep(0.3)
                page.keyboard.press("Control+a")
                page.keyboard.type(profile["birthday_day"])
                time.sleep(0.3)
            except Exception:
                try:
                    page.locator('input[aria-label="Day"]').first.type(profile["birthday_day"])
                except Exception:
                    pass

            # Year: click + type
            try:
                year_field = page.locator('input[name="year"]').first
                year_field.click()
                time.sleep(0.3)
                page.keyboard.press("Control+a")
                page.keyboard.type(profile["birthday_year"])
                time.sleep(0.3)
            except Exception:
                try:
                    page.locator('input[aria-label="Year"]').first.type(profile["birthday_year"])
                except Exception:
                    pass

            # Gender: Material dropdown — click "Rather not say" hoặc "Male"
            try:
                gender_div = page.locator('div[id="gender"]')
                if gender_div.count() > 0:
                    gender_div.click(timeout=3000)
                    time.sleep(0.8)
                    # Chọn "Rather not say" (data-value thường là 9 hoặc "other")
                    for g_sel in ['li:has-text("Rather not say")', 'li:has-text("Male")',
                                  'li[data-value="1"]']:
                        go = page.locator(g_sel)
                        if go.count() > 0:
                            go.first.click(force=True, timeout=2000)
                            break
                    time.sleep(0.3)
            except Exception:
                pass

            snap("02b_birthday_filled")

            # Submit birthday — retry với CapSolver token nếu bị invisible reCAPTCHA chặn
            if not submit_with_captcha_retry("birthday"):
                return None

            # ── Bước 3: Username ──────────────────────────────
            print(f"  👤 Chọn username: {profile['username']}")
            try:
                for txt in ["Create your own Gmail address", "Create your own"]:
                    el = page.locator(f"text={txt}")
                    if el.count() > 0:
                        el.first.click()
                        time.sleep(0.5)
                        break
            except Exception:
                pass

            for sel in ['input[name="Username"]', 'input[aria-label*="username" i]',
                         'input[autocomplete="username"]']:
                try:
                    field = page.locator(sel)
                    if field.count() > 0:
                        field.first.fill(profile["username"])
                        break
                except Exception:
                    pass

            snap("04_before_username_submit")
            if not submit_with_captcha_retry("username"):
                return None

            # ── Bước 4: Password ──────────────────────────────
            print(f"  🔐 Nhập password...")
            pwd_filled = False
            for sel in ['input[name="Passwd"]', 'input[name="password"]',
                         'input[type="password"]', 'input[aria-label*="password" i]']:
                try:
                    field = page.locator(sel)
                    if field.count() > 0:
                        field.first.fill(profile["password"])
                        try:
                            for csel in ['input[name="PasswdAgain"]', 'input[name="confirm"]']:
                                cf = page.locator(csel)
                                if cf.count() > 0:
                                    cf.first.fill(profile["password"])
                                    break
                        except Exception:
                            pass
                        pwd_filled = True
                        break
                except Exception:
                    pass

            if not pwd_filled:
                snap("04_password_not_found")
                print(f"  ✗ Không tìm thấy ô password. Debug: {debug_dir}")
                return None

            if not submit_with_captcha_retry("password"):
                return None

            # ── Bước 5: Số điện thoại (nhiều dạng URL) ───────────
            # Google dùng mophoneverification hoặc crossflowverification
            snap("05_check_phone_step")
            phone_needed = (
                "phoneverification" in page.url.lower() or
                "crossflow" in page.url.lower() or
                page.locator('input[name="phoneNumberId"], input[type="tel"]').count() > 0
            )
            if phone_needed:
                # Scroll để tìm input điện thoại
                try:
                    page.wait_for_selector(
                        'input[name="phoneNumberId"], input[type="tel"]',
                        state="visible", timeout=8_000
                    )
                except Exception:
                    pass
                phone_field = page.locator('input[name="phoneNumberId"], input[type="tel"]')
                if phone_field.count() > 0:
                    print(f"  📞 Nhập số điện thoại: {phone}")
                    phone_field.first.click()
                    time.sleep(0.3)
                    page.keyboard.type(phone)
                    if not submit_with_captcha_retry("phone"):
                        # Thử click Send / Get code
                        for btn_sel in ['button:has-text("Send")', 'button:has-text("Get code")',
                                        'button:has-text("Next")']:
                            b = page.locator(btn_sel)
                            if b.count() > 0:
                                b.first.click()
                                wait_stable(15_000)
                                break
                    snap("06_after_phone")

                    # ── Bước 6: OTP ───────────────────────────────
                    print(f"  ⏳ Đợi OTP từ 5sim...")
                    otp = fivesim.check_sms(order_id, timeout=120)
                    if not otp:
                        snap("06b_otp_timeout")
                        print("  ✗ Không nhận được OTP")
                        return None

                    for csel in ['input[name="code"]', 'input[id="code"]',
                                 'input[aria-label*="code" i]', 'input[type="tel"]']:
                        try:
                            otp_f = page.locator(csel)
                            if otp_f.count() > 0:
                                otp_f.first.click()
                                page.keyboard.type(otp)
                                break
                        except Exception:
                            pass
                    if not submit_with_captcha_retry("otp"):
                        click_next()
                        wait_stable(15_000)
                    fivesim.finish_order(order_id)
                    snap("07_after_otp")

            # ── Bước 8: Đồng ý Terms ──────────────────────────
            for agree_sel in ['button:has-text("I agree")', 'button:has-text("Agree")',
                               'button:has-text("Accept")']:
                try:
                    btn = page.locator(agree_sel)
                    if btn.count() > 0:
                        btn.first.click()
                        wait_stable()
                        break
                except Exception:
                    pass
            snap("08_after_agree")

            # ── Kiểm tra thành công ───────────────────────────
            current_url = page.url
            print(f"  📍 URL hiện tại: {current_url}")

            success_domains = ("myaccount.google.com", "mail.google.com",
                                "accounts.google.com/v3/signin/complete",
                                "accounts.google.com/signin/v2/challenge/complete")
            if not any(d in current_url for d in success_domains):
                snap("09_maybe_success_or_error")
                print(f"  ⚠️  URL không rõ ràng. Kiểm tra screenshot: {debug_dir}")

            if not _use_headless:
                try:
                    print("\n  ⏸  Kiểm tra tài khoản đã tạo chưa. Nhấn Enter để export cookies...")
                    input()
                except EOFError:
                    time.sleep(3)  # không có stdin, chờ 3s rồi tiếp

            # ── Export cookies ────────────────────────────────
            print("  🍪 Export cookies...")
            playwright_cookies = context.cookies()
            netscape_cookies = _playwright_cookies_to_netscape(playwright_cookies)
            cookie_file.write_text(netscape_cookies)
            print(f"  ✓ Cookies lưu tại: {cookie_file}")

            return cookie_file

        except PwTimeout as e:
            print(f"  ✗ Timeout: {e}")
            return None
        except Exception as e:
            print(f"  ✗ Lỗi: {e}")
            if not headless:
                print("     Nhấn Enter để đóng browser...")
                input()
            return None
            return None
        finally:
            context.close()
            browser.close()


def _playwright_cookies_to_netscape(cookies: list) -> str:
    """Chuyển Playwright cookies sang định dạng Netscape cookies.txt."""
    lines = ["# Netscape HTTP Cookie File\n"]
    for c in cookies:
        domain = c.get("domain", "")
        if not domain.startswith(".") and not domain.startswith("#"):
            domain = "." + domain
        secure = "TRUE" if c.get("secure") else "FALSE"
        http_only = "TRUE"
        expires = int(c.get("expires", 0))
        if expires < 0:
            expires = 0
        name = c.get("name", "")
        value = c.get("value", "")
        path = c.get("path", "/")
        lines.append(f"{domain}\t{http_only}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
    return "".join(lines)


# ─────────────────────────────────────────────
# Upload cookie to admin API
# ─────────────────────────────────────────────

def upload_cookie_to_pool(cookie_file: Path, platform: str) -> bool:
    """Upload cookies.txt lên Cookie Pool qua admin API."""
    if not ADMIN_API_URL:
        print("  ⚠️  ADMIN_API_URL chưa set, bỏ qua upload tự động.")
        return False

    headers = {}
    if ADMIN_API_KEY:
        headers["X-Admin-Key"] = ADMIN_API_KEY

    try:
        with open(cookie_file, "rb") as f:
            r = requests.post(
                f"{ADMIN_API_URL}/admin/cookies/upload",
                files={"file": (cookie_file.name, f, "text/plain")},
                data={"platform": platform},
                headers=headers,
                timeout=15,
            )

        if r.status_code == 200:
            data = r.json()
            print(f"  ✓ Upload thành công! Pool size: {data.get('pool_size', '?')} cookies")
            return True
        else:
            print(f"  ✗ Upload lỗi: {r.status_code} — {r.text}")
            return False
    except Exception as e:
        print(f"  ✗ Kết nối admin API thất bại: {e}")
        return False


# ─────────────────────────────────────────────
# Save profile log
# ─────────────────────────────────────────────

def save_profile_log(profile: dict, phone: str, platform: str, success: bool) -> None:
    log_path = Path(__file__).parent / "throwaway_accounts.json"
    entries = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text())
        except Exception:
            entries = []

    entries.append({
        "platform": platform,
        "email": f"{profile['username']}@gmail.com",
        "password": profile["password"],
        "phone": phone,
        "birthday": f"{profile['birthday_day']}/{profile['birthday_month']}/{profile['birthday_year']}",
        "success": success,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    log_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"  📝 Lưu thông tin vào {log_path}")


# ─────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────

def create_one_account(
    platform: str,
    fivesim: FiveSimClient,
    captcha_key: Optional[str],
    headless: bool,
    country: str = "auto",
) -> bool:
    service = PLATFORM_TO_FIVESIM.get(platform, "google")
    profile = generate_profile()

    print(f"\n{'='*55}")
    print(f"  Tạo tài khoản #{profile['username']} ({platform})")
    print(f"  Email: {profile['username']}@gmail.com")
    print(f"  Password: {profile['password']}")
    print(f"  Sinh: {profile['birthday_day']}/{profile['birthday_month']}/{profile['birthday_year']}")
    print(f"{'='*55}")

    # Tìm country rẻ nhất nếu auto
    if country == "auto":
        country = fivesim.find_cheapest_country(service)

    # Mua số điện thoại
    print(f"\n  📱 Mua số điện thoại ({service}, {country})...")
    try:
        order = fivesim.buy_number(service, country)
    except Exception as e:
        print(f"  ✗ Không mua được số: {e}")
        return False

    order_id = order["id"]
    phone = order.get("phone", "")
    print(f"  ✓ Số điện thoại: {phone} (order: {order_id})")

    # Tạo tài khoản
    cookie_file = create_google_account(
        profile=profile,
        phone=phone,
        fivesim=fivesim,
        order_id=order_id,
        captcha_key=captcha_key,
        headless=headless,
    )

    success = cookie_file is not None and cookie_file.exists()
    save_profile_log(profile, phone, platform, success)

    if success:
        print(f"\n  🎉 Tài khoản tạo thành công!")

        # Upload vào pool
        print(f"  ☁️  Upload cookies vào {platform} pool...")
        upload_cookie_to_pool(cookie_file, platform)

        # Giữ bản backup
        backup = Path(__file__).parent / f"cookies_{profile['username']}.txt"
        backup.write_bytes(cookie_file.read_bytes())
        print(f"  💾 Backup: {backup}")
    else:
        print(f"\n  ✗ Tạo tài khoản thất bại")
        fivesim.cancel_order(order_id)

    return success


def upload_cookie_manual(cookie_path: str, platform: str) -> None:
    """Upload cookie file thủ công."""
    f = Path(cookie_path)
    if not f.exists():
        print(f"❌ File không tồn tại: {cookie_path}")
        return
    print(f"📤 Upload {f.name} → {platform} pool...")
    ok = upload_cookie_to_pool(f, platform)
    if ok:
        print("✅ Xong!")
    else:
        print("❌ Upload thất bại. Kiểm tra ADMIN_API_URL và ADMIN_API_KEY.")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tự động tạo tài khoản throwaway + upload vào Cookie Pool"
    )
    parser.add_argument("--platform", default="youtube",
                        choices=list(PLATFORM_TO_FIVESIM.keys()),
                        help="Nền tảng cần tạo tài khoản (default: youtube)")
    parser.add_argument("--count", type=int, default=1,
                        help="Số tài khoản cần tạo")
    parser.add_argument("--country", default="auto",
                        help="Country cho 5sim (auto=chọn rẻ nhất, vd: vietnam, russia)")
    parser.add_argument("--captcha-key", default=TWOCAPTCHA_API_KEY or None,
                        help="2captcha.com API key (bỏ trống = giải CAPTCHA thủ công)")
    parser.add_argument("--headless", action="store_true",
                        help="Chạy browser ẩn (cần --captcha-key)")
    parser.add_argument("--list-balance", action="store_true",
                        help="Xem số dư 5sim.net")
    parser.add_argument("--upload-cookie",
                        help="Upload cookie file thủ công (dùng với --platform)")
    parser.add_argument("--fivesim-key", default=FIVESIM_API_KEY,
                        help="5sim.net API key (hoặc set FIVESIM_API_KEY)")
    args = parser.parse_args()

    # ── Upload thủ công ───────────────────────────────────────
    if args.upload_cookie:
        upload_cookie_manual(args.upload_cookie, args.platform)
        return

    # ── Kiểm tra API key ──────────────────────────────────────
    if not args.fivesim_key:
        print("❌ Cần FIVESIM_API_KEY. Set trong .env hoặc --fivesim-key")
        print("   Đăng ký tại: https://5sim.net")
        sys.exit(1)

    fivesim = FiveSimClient(args.fivesim_key)

    # ── Xem số dư ────────────────────────────────────────────
    if args.list_balance:
        try:
            bal = fivesim.get_balance()
            print(f"💰 Số dư 5sim.net: ${bal:.4f}")

            print("\nGiá số điện thoại Google (top countries):")
            rows = fivesim.get_all_country_prices("google")
            for country, cost, count in rows[:15]:
                print(f"  {country:<15} ${cost:.3f}  ({count} available)")
        except Exception as e:
            print(f"❌ Lỗi: {e}")
        return

    # ── Auto-detect headless: server không có display → bắt buộc headless ────
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not has_display and not args.headless:
        print("ℹ️  Không có màn hình, tự chuyển sang headless mode.")
        args.headless = True

    # ── Tạo tài khoản ────────────────────────────────────────
    bal = fivesim.get_balance()
    captcha_mode = "CapSolver (auto)" if args.captcha_key and args.captcha_key.startswith("CAP-") \
        else "2captcha (auto)" if args.captcha_key \
        else "bỏ qua (nếu CAPTCHA xuất hiện sẽ fail)"
    print(f"💰 Số dư 5sim.net: ${bal:.4f}")
    print(f"🎯 Nền tảng: {args.platform}")
    print(f"🔢 Số tài khoản: {args.count}")
    print(f"🌍 Country: {args.country}")
    print(f"🤖 CAPTCHA: {captcha_mode}")
    print(f"👀 Browser: {'headless (ẩn)' if args.headless else 'hiện (có màn hình)'}")
    print()

    ok = 0
    fail = 0
    for i in range(args.count):
        print(f"\n[{i+1}/{args.count}] Bắt đầu tạo tài khoản...")
        success = create_one_account(
            platform=args.platform,
            fivesim=fivesim,
            captcha_key=args.captcha_key,
            headless=args.headless,
            country=args.country,
        )
        if success:
            ok += 1
        else:
            fail += 1

        if i < args.count - 1:
            delay = random.randint(30, 60)
            print(f"\n  ⏸  Đợi {delay}s trước tài khoản tiếp theo...")
            time.sleep(delay)

    print(f"\n{'='*55}")
    print(f"  Kết quả: ✅ {ok} thành công | ❌ {fail} thất bại")
    print(f"  Xem log: {Path(__file__).parent / 'throwaway_accounts.json'}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()

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

# ── Proxy configuration (chọn 1 trong 3 mode) ─────────────────
# Mode 1 — Webshare.io (đăng ký free tại webshare.io)
WEBSHARE_USERNAME = os.getenv("WEBSHARE_USERNAME", "")
WEBSHARE_PASSWORD = os.getenv("WEBSHARE_PASSWORD", "")

# Mode 2 — SSH tunnel về nhà: chạy `ssh -D 1080 -N user@home-ip` trước
# Để trống nếu không dùng, hoặc set: socks5://localhost:1080
HOME_SSH_PROXY = os.getenv("HOME_SSH_PROXY", "")

# Mode 3 — Custom proxy URL (ghi đè tất cả)
THROWAWAY_PROXY = os.getenv("THROWAWAY_PROXY", "")


def resolve_proxy(cli_proxy: Optional[str] = None) -> Optional[str]:
    """
    Tự động chọn proxy theo priority:
      1. --proxy CLI arg (cao nhất)
      2. THROWAWAY_PROXY env var
      3. WEBSHARE_USERNAME + WEBSHARE_PASSWORD → rotating endpoint
      4. HOME_SSH_PROXY env var (sau khi chạy ssh -D 1080)
      5. None → dùng IP trực tiếp (phải là IP nhà / residential)
    """
    if cli_proxy:
        return cli_proxy
    if THROWAWAY_PROXY:
        return THROWAWAY_PROXY
    if WEBSHARE_USERNAME and WEBSHARE_PASSWORD:
        # Webshare rotating proxy — HTTP port 80 (Chromium doesn't support authenticated SOCKS5)
        return f"http://{WEBSHARE_USERNAME}-rotate:{WEBSHARE_PASSWORD}@p.webshare.io:80"
    if HOME_SSH_PROXY:
        return HOME_SSH_PROXY
    return None


def proxy_label(proxy: Optional[str]) -> str:
    """Hiển thị proxy label an toàn (ẩn password)."""
    if not proxy:
        return "không — chạy trực tiếp (cần IP nhà/residential)"
    if "webshare.io" in proxy:
        host = proxy.split("@")[-1] if "@" in proxy else proxy
        return f"Webshare.io ({host})"
    if "localhost" in proxy or "127.0.0.1" in proxy:
        return f"SSH tunnel về nhà ({proxy})"
    host = proxy.split("@")[-1] if "@" in proxy else proxy
    return f"Custom ({host})"


def _build_playwright_proxy(proxy: str, sticky_session: Optional[str] = None) -> dict:
    """Chuyển proxy URL thành Playwright proxy dict.
    sticky_session: nếu có, thêm _session-TOKEN vào username (IPRoyal sticky IP).
    """
    import urllib.parse as _urlparse
    _p = _urlparse.urlparse(proxy)
    _default_port = 1080 if _p.scheme in ("socks5", "socks4") else 80
    cfg: dict = {"server": f"{_p.scheme}://{_p.hostname}:{_p.port or _default_port}"}
    if _p.username:
        username = _urlparse.unquote(_p.username)
        if sticky_session and "iproyal" in (proxy.lower()):
            # IPRoyal sticky session: username_session-TOKEN_lifetime-10m
            username = f"{username}_session-{sticky_session}_lifetime-10m"
        cfg["username"] = username
    if _p.password:
        cfg["password"] = _urlparse.unquote(_p.password)
    return cfg


def _make_sticky_token() -> str:
    """Tạo random sticky session token cho IPRoyal."""
    import random, string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


def test_proxy_connectivity(proxy: Optional[str]) -> bool:
    """
    Kiểm tra proxy có kết nối được tới accounts.google.com không.
    Webshare datacenter proxy thường bị chặn bởi Google/Webshare.
    Returns True nếu kết nối OK.
    """
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return True  # không test được, giả sử OK

    print(f"\n🔍 Kiểm tra kết nối proxy → accounts.google.com...")
    if proxy:
        print(f"   Proxy: {proxy_label(proxy)}")

    _unsupported_msg = "socks5 proxy authentication"

    try:
        with sync_playwright() as pw:
            launch_kwargs: dict = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                         "--disable-quic", "--disable-http2"],
            }
            if proxy:
                launch_kwargs["proxy"] = _build_playwright_proxy(proxy)

            try:
                browser = pw.chromium.launch(**launch_kwargs)
            except Exception as e:
                err = str(e)
                if _unsupported_msg in err:
                    print(f"   ❌ Chromium không hỗ trợ SOCKS5 proxy có xác thực")
                    print(f"      → Dùng HTTP proxy: thay socks5:// thành http://")
                    return False
                raise

            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = ctx.new_page()
            try:
                page.goto("https://accounts.google.com", wait_until="commit", timeout=20_000)
                print(f"   ✅ Kết nối OK — URL: {page.url[:60]}")
                browser.close()
                return True
            except Exception:
                print(f"   ❌ Không kết nối được accounts.google.com qua proxy này")
                if proxy and "webshare.io" in proxy:
                    print(f"   ℹ️  Webshare datacenter proxy chặn kết nối Google accounts.")
                    print(f"      → Nâng cấp lên Webshare Residential ($4/GB trở lên)")
                    print(f"      → Hoặc dùng SSH tunnel về nhà (Mode B, miễn phí)")
                browser.close()
                return False
    except Exception as e:
        print(f"   ⚠️  Không thể khởi động trình duyệt để test: {e}")
        return False

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
        countries = [
            "vietnam", "indonesia", "philippines", "india", "russia",
            "ukraine", "kenya", "myanmar", "cambodia", "thailand",
        ]
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

    def find_best_country(self, service: str) -> tuple:
        """
        Chọn country tốt nhất để tránh device verification.
        Ưu tiên Indonesia/Philippines (ASEAN, hay nhận SMS OTP thay vì QR code).
        Trả về (country_name, price, qty).
        """
        # Thứ tự ưu tiên: non-Vietnam trước để thử trigger SMS OTP thật
        priority = ["indonesia", "philippines", "india", "myanmar",
                    "cambodia", "kenya", "ukraine", "russia", "vietnam"]
        try:
            rows = self.get_all_country_prices(service)
            price_map = {c: (cost, qty) for c, cost, qty in rows if qty > 0}

            for country in priority:
                if country in price_map:
                    cost, qty = price_map[country]
                    print(f"  🌍 Country được chọn: {country} (${cost:.3f}, {qty} available)")
                    return country, cost, qty

            # fallback: cheapest available
            for c, cost, qty in rows:
                if qty > 0:
                    print(f"  🌍 Country fallback: {c} (${cost:.3f}, {qty} available)")
                    return c, cost, qty
        except Exception:
            pass
        return "vietnam", 0.08, 0


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

_PW_ADJ  = ["Blue","Red","Dark","Fast","Bright","Cool","Wild","Sharp","Smart","Deep",
             "Bold","Calm","Keen","Pure","Warm","Soft","Lone","High","Free","Long",
             "Iron","Gold","Star","Clay","Jade","Ash","Oak","Elm","Bay","Zen"]
_PW_NOUN = ["Tiger","Eagle","Ocean","Cloud","River","Storm","Forest","Moon","Fire","Stone",
             "Arrow","Bridge","Castle","Dragon","Empire","Falcon","Garden","Harbor","Island","Jungle",
             "Knight","Legend","Marble","Nebula","Oracle","Pillar","Quest","Ranger","Spark","Tower"]
_PW_SYM  = ["!", "@", "#", "$", "%"]


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

    # Password: AdjectiveNoun + 2-digit + symbol — dễ nhớ, trông người thật
    # Ví dụ: BlueTiger47!, DarkOcean83@, WildEagle12#
    adj = random.choice(_PW_ADJ)
    noun = random.choice(_PW_NOUN)
    num = random.randint(10, 99)
    sym = random.choice(_PW_SYM)
    password = f"{adj}{noun}{num}{sym}"

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
    """Giải reCAPTCHA v3 Enterprise qua CapSolver API."""
    try:
        # Google signup dùng reCAPTCHA v3 Enterprise — dùng ReCaptchaV3TaskProxyless
        # với pageAction="signup" để CapSolver worker generate token với risk score thật
        if is_enterprise:
            task_type = "ReCaptchaV3TaskProxyless"
            task_payload = {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": site_key,
                "pageAction": "signup",
                "isEnterprise": True,
            }
        else:
            task_type = "ReCaptchaV2TaskProxyless"
            task_payload = {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": site_key,
            }
        print(f"  🤖 CapSolver: tạo task ({task_type})...")
        r = requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": api_key,
            "task": task_payload,
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
    proxy: Optional[str] = None,
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

        launch_kwargs: dict = {
            "headless": _use_headless,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-quic",
                "--disable-http2",           # required for HTTP proxy CONNECT tunnels
                "--disable-background-networking",
                "--disable-sync",
                "--no-first-run",
                "--disable-gpu" if _use_headless else "",
            ],
        }
        if proxy:
            _proxy_cfg = _build_playwright_proxy(proxy)
            launch_kwargs["proxy"] = _proxy_cfg
            print(f"  🌐 Proxy: {_proxy_cfg['server']}")
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # Stealth: patch all known Playwright/Chromium automation signals
        context.add_init_script("""
            // navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            // plugins — headless Chrome has 0 plugins, real browsers have some
            Object.defineProperty(navigator, 'plugins', {get: () => {
                const arr = [
                    {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format'},
                    {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:''},
                    {name:'Native Client',filename:'internal-nacl-plugin',description:''},
                ];
                arr.__proto__ = PluginArray.prototype;
                return arr;
            }});
            // languages
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            // chrome runtime — headless missing this
            if (!window.chrome) window.chrome = {};
            if (!window.chrome.runtime) window.chrome.runtime = {};
            // permissions — headless returns 'denied' for notifications
            const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
            window.navigator.permissions.query = (params) =>
                params.name === 'notifications'
                    ? Promise.resolve({state: Notification.permission})
                    : origQuery(params);
            // hide automation-specific properties
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)
        page = context.new_page()

        try:
            debug_dir = Path(f"/tmp/throwaway_debug_{profile['username']}")
            debug_dir.mkdir(exist_ok=True)

            # Warm-up: visit google.com để build session signals trước khi signup
            # Dùng timeout dài hơn khi có proxy (proxy có thể chậm hơn direct)
            _goto_timeout = 45_000 if proxy else 20_000
            print("  🌡️  Warm-up session...")
            try:
                page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=_goto_timeout)
            except Exception:
                pass  # warm-up không bắt buộc phải thành công
            time.sleep(random.uniform(2, 4))
            try:
                page.goto("https://accounts.google.com", wait_until="domcontentloaded", timeout=_goto_timeout)
            except Exception:
                pass
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

                def _inject_and_submit(t: str) -> bool:
                    """Inject token vào form, click Next nếu cần, trả về True nếu URL thay đổi."""
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
                    }""", t)
                    # Đợi page tự transition sau callback (Google thường tự submit)
                    time.sleep(3)
                    # Nếu URL đã thay đổi (callback tự submit), không cần click
                    if page.url != before_url:
                        wait_stable(8_000)
                        snap(f"{step_label}_after_captcha")
                        return True
                    # URL chưa thay đổi → click Next
                    try:
                        click_next()
                    except Exception:
                        pass  # button có thể bị detached nếu transition đang xảy ra
                    wait_stable(12_000)
                    snap(f"{step_label}_after_captcha")
                    return page.url != before_url

                # ── Phương án 2: CapSolver ProxylessTask (nếu không có browser token) ──
                if not token and captcha_key:
                    print(f"  🤖 Fallback sang CapSolver API...")
                    token = solve_captcha(detected_key, page.url, captcha_key, is_enterprise=is_enterprise)

                if not token:
                    print(f"  ✗ Không lấy được token tại {step_label}")
                    return False

                # Thử browser token trước
                if _inject_and_submit(token):
                    print(f"  ✓ Bypass thành công!")
                    return True

                # Browser token bị Google reject → thử CapSolver API token (risk score cao hơn)
                if captcha_key:
                    print(f"  ⚠️  Browser token bị reject, thử CapSolver API (risk score thật)...")
                    api_token = solve_captcha(detected_key, page.url, captcha_key, is_enterprise=is_enterprise)
                    if api_token and _inject_and_submit(api_token):
                        print(f"  ✓ CapSolver bypass thành công!")
                        return True

                print(f"  ✗ Vẫn bị chặn sau cả 2 token tại {step_label}")
                return False

            # ─── Helpers: step-specific fill actions ───────────
            month_names = ["January","February","March","April","May","June",
                           "July","August","September","October","November","December"]

            def fill_birthday_fields() -> None:
                try:
                    page.wait_for_selector('input[name="day"]', state="visible", timeout=8_000)
                    time.sleep(1)
                except Exception:
                    pass
                try:
                    month_num = int(profile["birthday_month"])
                    page.locator('div[id="month"]').click(timeout=5000)
                    time.sleep(1.2)
                    for sel in [f'li[data-value="{month_num}"]',
                                f'li:has-text("{month_names[month_num-1]}")']:
                        opt = page.locator(sel)
                        if opt.count() > 0:
                            opt.first.click(force=True, timeout=3000)
                            break
                    time.sleep(0.5)
                except Exception:
                    pass
                for name_attr, val in [("day", profile["birthday_day"]),
                                       ("year", profile["birthday_year"])]:
                    try:
                        f = page.locator(f'input[name="{name_attr}"]').first
                        f.click()
                        time.sleep(0.3)
                        page.keyboard.press("Control+a")
                        page.keyboard.type(val)
                        time.sleep(0.3)
                    except Exception:
                        pass
                try:
                    gender_div = page.locator('div[id="gender"]')
                    if gender_div.count() > 0:
                        gender_div.click(timeout=3000)
                        time.sleep(0.8)
                        for g_sel in ['li:has-text("Rather not say")', 'li:has-text("Male")',
                                      'li[data-value="1"]']:
                            go = page.locator(g_sel)
                            if go.count() > 0:
                                go.first.click(force=True, timeout=2000)
                                break
                        time.sleep(0.3)
                except Exception:
                    pass

            SUCCESS_DOMAINS = (
                "myaccount.google.com", "mail.google.com",
                "accounts.google.com/ManageAccount",
                "accounts.google.com/v3/signin/complete",
                "accounts.google.com/signin/v2/challenge/complete",
            )

            def detect_step() -> str:
                """Detect current signup step from URL and DOM."""
                url = page.url
                # "done" only if we're NOT still inside the signup/lifecycle flow
                if (any(d in url for d in SUCCESS_DOMAINS)
                        and "signup" not in url.lower()
                        and "lifecycle" not in url.lower()):
                    return "done"
                # OTP input check before phone (both may have tel input)
                if page.locator('input[name="code"]').count() > 0:
                    return "otp"
                if ("phoneverification" in url.lower() or
                        "crossflowverification" in url.lower() or
                        page.locator('input[name="phoneNumberId"]').count() > 0):
                    return "phone"
                for agree_text in ("I agree", "Agree", "Accept"):
                    if page.locator(f'button:has-text("{agree_text}")').count() > 0:
                        return "agree"
                if page.locator('input[name="day"]').count() > 0:
                    return "birthday"
                if (page.locator('input[name="Passwd"]').count() > 0 or
                        page.locator('input[name="PasswdAgain"]').count() > 0):
                    return "password"
                if page.locator('input[name="Username"]').count() > 0:
                    return "username"
                if page.locator('input[name="firstName"]').count() > 0:
                    return "name"
                for skip_text in ("Skip", "Not now", "No thanks"):
                    if page.locator(f'button:has-text("{skip_text}")').count() > 0:
                        return "skip"
                return "unknown"

            # ─── Open signup page (retry on tunnel errors — proxy IP rotation) ─
            print(f"\n  🌐 Mở Google account creation...")
            # Mobile entry point → Google dùng SMS verification thay vì QR code
            _signup_url = ("https://accounts.google.com/signup/v2/createaccount"
                           "?flowName=GlifWebSignIn&flowEntry=SignUp&service=mail")
            for _goto_try in range(3):
                try:
                    page.goto(_signup_url, wait_until="domcontentloaded", timeout=_goto_timeout)
                    break
                except Exception as _ge:
                    if "ERR_TUNNEL_CONNECTION_FAILED" in str(_ge) and _goto_try < 2:
                        print(f"  ⚠️  Proxy tunnel failed (lần {_goto_try+1}/3), thử lại...")
                        time.sleep(3)
                    else:
                        raise
            snap("01_start")
            time.sleep(2)  # let JS-triggered redirects settle
            wait_stable(5_000)

            # ─── State machine: handles Google's flow restarts ──
            # Google sometimes resets back to earlier steps (e.g. name) after
            # password bypass; the state machine detects & re-fills as needed.
            step_counts: dict = {}
            phone_done = False
            otp_done = False
            passed_password = False  # detect loop restart

            for _iter in range(20):
                step = detect_step()
                step_counts[step] = step_counts.get(step, 0) + 1
                snap(f"step_{_iter:02d}_{step}")

                if step == "done":
                    print(f"  ✅ Tài khoản tạo thành công!")
                    break

                # Flow restart detection: Google reset to name/birthday AFTER password.
                # Allow 1 restart attempt (sometimes succeeds on 2nd pass with stealth browser).
                # If restarted twice → escalated to QR verification, abort.
                if passed_password and step in ("name", "birthday"):
                    restart_count = step_counts.get("_restart", 0) + 1
                    step_counts["_restart"] = restart_count
                    if restart_count >= 2:
                        print(f"  ✗ Google reset flow {restart_count} lần — bot detection nặng")
                        print(f"     Thử chạy lại sau vài phút hoặc đổi session IPRoyal")
                        return None
                    print(f"  ⚠️  Google restarted flow tại '{step}' (lần {restart_count}) — thử tiếp...")
                    passed_password = False  # reset, cho phép đi lại qua password step

                # Abort if stuck at the same step too many times
                if step_counts[step] > 3:
                    print(f"  ✗ Stuck tại bước '{step}' — dừng lại")
                    return None

                if step == "name":
                    print(f"  ✏  Nhập tên: {profile['first_name']} {profile['last_name']}")
                    for name_attr, val in [("firstName", profile["first_name"]),
                                           ("lastName", profile["last_name"])]:
                        try:
                            f = page.locator(f'input[name="{name_attr}"]')
                            if f.count() > 0:
                                f.first.fill(val)
                        except Exception:
                            pass
                    submit_with_captcha_retry(f"name_{_iter}")

                elif step == "birthday":
                    print(f"  🎂 Ngày sinh: {profile['birthday_day']}/{profile['birthday_month']}/{profile['birthday_year']}")
                    fill_birthday_fields()
                    submit_with_captcha_retry(f"birthday_{_iter}")

                elif step == "username":
                    print(f"  👤 Username: {profile['username']}")
                    for txt in ["Create your own Gmail address", "Create your own"]:
                        try:
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
                            f = page.locator(sel)
                            if f.count() > 0:
                                f.first.fill(profile["username"])
                                break
                        except Exception:
                            pass
                    submit_with_captcha_retry(f"username_{_iter}")

                elif step == "password":
                    print(f"  🔐 Nhập password...")
                    filled = False
                    for sel in ['input[name="Passwd"]', 'input[name="password"]',
                                'input[type="password"]', 'input[aria-label*="password" i]']:
                        try:
                            f = page.locator(sel)
                            if f.count() > 0:
                                f.first.fill(profile["password"])
                                for csel in ['input[name="PasswdAgain"]', 'input[name="confirm"]']:
                                    cf = page.locator(csel)
                                    if cf.count() > 0:
                                        cf.first.fill(profile["password"])
                                        break
                                filled = True
                                break
                        except Exception:
                            pass
                    if not filled:
                        snap(f"step_{_iter:02d}_pwd_not_found")
                        print(f"  ✗ Không tìm thấy ô password")
                        return None
                    passed_password = True
                    submit_with_captcha_retry(f"password_{_iter}")

                elif step == "phone" and not phone_done:
                    phone_done = True
                    # Convert international → local format for common ASEAN prefixes
                    _cc_map = {
                        "+84": "0",   # Vietnam
                        "+62": "0",   # Indonesia
                        "+63": "0",   # Philippines
                        "+91": "0",   # India
                        "+95": "0",   # Myanmar
                        "+855": "0",  # Cambodia
                        "+66": "0",   # Thailand
                    }
                    phone_local = phone
                    for prefix, local_prefix in _cc_map.items():
                        if phone_local.startswith(prefix):
                            phone_local = local_prefix + phone_local[len(prefix):]
                            break
                    print(f"  📞 Nhập số điện thoại: {phone_local} (gốc: {phone})")
                    try:
                        page.wait_for_selector(
                            'input[name="phoneNumberId"]',
                            state="visible", timeout=8_000
                        )
                    except Exception:
                        pass
                    pf = page.locator('input[name="phoneNumberId"]')
                    if pf.count() > 0:
                        pf.first.click()
                        time.sleep(0.5)
                        page.keyboard.press("Control+a")
                        page.keyboard.type(phone_local)
                        time.sleep(0.5)
                    else:
                        # QR code page — thử click "Use phone number" để chuyển sang SMS flow
                        snap(f"step_{_iter:02d}_no_phone_input")
                        switched = False
                        for switch_text in ["Use phone number", "phone number", "Send SMS",
                                            "Verify by SMS", "Enter phone number"]:
                            el = page.locator(f"text={switch_text}")
                            if el.count() > 0:
                                el.first.click()
                                time.sleep(2)
                                # Kiểm tra lại nếu đã có input
                                pf2 = page.locator('input[name="phoneNumberId"]')
                                if pf2.count() > 0:
                                    pf2.first.click()
                                    page.keyboard.press("Control+a")
                                    page.keyboard.type(phone_local)
                                    switched = True
                                    break
                        if not switched:
                            print(f"  ✗ Google yêu cầu QR code — không thể bypass tự động")
                            print(f"     (Google phát hiện headless browser, cần session sạch hơn)")
                            return None
                    # Click Next/Send directly (not via captcha retry)
                    for btn_sel in ['button:has-text("Next")', 'button:has-text("Send")',
                                    'button:has-text("Get code")', 'button[type="submit"]']:
                        b = page.locator(btn_sel)
                        if b.count() > 0:
                            b.first.click()
                            break
                    wait_stable(15_000)

                elif step == "otp" and not otp_done:
                    otp_done = True
                    print(f"  ⏳ Đợi OTP từ 5sim...")
                    otp = fivesim.check_sms(order_id, timeout=120)
                    if not otp:
                        snap(f"step_{_iter:02d}_otp_timeout")
                        print("  ✗ Không nhận được OTP")
                        return None
                    print(f"  ✓ OTP: {otp}")
                    for csel in ['input[name="code"]', 'input[id="code"]',
                                 'input[aria-label*="code" i]']:
                        try:
                            otp_f = page.locator(csel)
                            if otp_f.count() > 0:
                                otp_f.first.click()
                                page.keyboard.type(otp)
                                break
                        except Exception:
                            pass
                    if not submit_with_captcha_retry(f"otp_{_iter}"):
                        click_next()
                        wait_stable(15_000)
                    fivesim.finish_order(order_id)

                elif step == "agree":
                    print(f"  ✅ Đồng ý terms...")
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

                elif step == "skip":
                    for skip_sel in ['button:has-text("Skip")', 'button:has-text("Not now")',
                                     'button:has-text("No thanks")']:
                        try:
                            btn = page.locator(skip_sel)
                            if btn.count() > 0:
                                btn.first.click()
                                wait_stable()
                                break
                        except Exception:
                            pass

                else:  # unknown
                    print(f"  ❓ Bước không xác định, thử Next... URL: {page.url[:60]}")
                    advanced = False
                    for sel in ['button:has-text("Next")', 'button:has-text("Continue")',
                                'button[type="submit"]']:
                        if page.locator(sel).count() > 0:
                            page.locator(sel).first.click()
                            wait_stable(8_000)
                            advanced = True
                            break
                    if not advanced:
                        print(f"  ⚠️  Không tìm thấy nút tiếp theo, dừng lại")
                        break

            # ─── Navigate to YouTube để lấy đủ SID/HSID/SAPISID ──────
            # accounts.google.com chỉ có __Host-GAPS, thiếu SID/HSID
            # yt-dlp cần SID/__Secure-1PSID — chỉ set khi visit youtube.com
            print(f"  🎬 Navigate sang YouTube để lấy full session cookies...")
            try:
                page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=30_000)
                time.sleep(3)
                snap("youtube_session")
            except Exception as e:
                print(f"  ⚠️  YouTube navigate lỗi (vẫn tiếp tục): {e}")

            # ─── Check session cookies (confirm account created) ──
            playwright_cookies = context.cookies()
            session_names = {"SID", "HSID", "SSID", "APISID", "SAPISID",
                             "__Secure-1PSID", "__Secure-3PSID"}
            has_session = any(
                c.get("name", "").upper() in session_names or
                c.get("name", "") in session_names
                for c in playwright_cookies
            )

            current_url = page.url
            print(f"  📍 URL cuối: {current_url[:80]}")
            if has_session:
                yt_cookies = [c["name"] for c in playwright_cookies if c["name"] in session_names]
                print(f"  ✅ Session cookies OK: {yt_cookies}")
            else:
                print(f"  ⚠️  Không có session cookies — tài khoản chưa tạo xong")

            if not _use_headless:
                try:
                    status = "✓ đăng nhập" if has_session else "✗ chưa đăng nhập"
                    print(f"\n  ⏸  Trạng thái: {status}. Nhấn Enter để export cookies...")
                    input()
                except EOFError:
                    time.sleep(3)

            # ─── Export cookies ───────────────────────────────
            print("  🍪 Export cookies...")
            netscape_cookies = _playwright_cookies_to_netscape(playwright_cookies)
            cookie_file.write_text(netscape_cookies)
            print(f"  ✓ Cookies lưu tại: {cookie_file}")

            if not has_session:
                return None  # mark as failed — no usable session

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

def _detect_exit_ip(proxy: Optional[str] = None) -> dict:
    """Lấy exit IP + country từ ipinfo.io qua proxy."""
    try:
        import urllib.request as _req
        _url = "https://ipinfo.io/json"
        if proxy:
            _handler = __import__("urllib.request", fromlist=["ProxyHandler"]).ProxyHandler(
                {"http": proxy, "https": proxy}
            )
            _opener = _req.build_opener(_handler)
            resp = _opener.open(_url, timeout=10).read().decode()
        else:
            resp = _req.urlopen(_url, timeout=10).read().decode()
        info = json.loads(resp)
        return {"ip": info.get("ip", "?"), "country": info.get("country", "?"), "city": info.get("city", "?")}
    except Exception:
        return {"ip": "?", "country": "?", "city": "?"}


def _push_account_to_backend(entry: dict) -> None:
    """Fire-and-forget: push account entry to backend POST /throwaway/accounts."""
    if not ADMIN_API_URL:
        return
    try:
        headers = {"Content-Type": "application/json"}
        if ADMIN_API_KEY:
            headers["X-Admin-Token"] = ADMIN_API_KEY
        requests.post(
            f"{ADMIN_API_URL}/api/v1/admin/throwaway/accounts",
            json=entry,
            headers=headers,
            timeout=5,
        )
    except Exception:
        pass  # non-blocking — local JSON file is the fallback


def save_profile_log(profile: dict, phone: str, platform: str, success: bool,
                     proxy: Optional[str] = None) -> None:
    log_path = Path(__file__).parent / "throwaway_accounts.json"
    entries = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text())
        except Exception:
            entries = []

    exit_ip_info = _detect_exit_ip(proxy) if success else {"ip": "?", "country": "?", "city": "?"}
    if success and exit_ip_info["ip"] != "?":
        print(f"  🌐 Exit IP: {exit_ip_info['ip']} ({exit_ip_info['country']}, {exit_ip_info['city']})")

    entry = {
        "platform": platform,
        "email": f"{profile['username']}@gmail.com",
        "password": profile["password"],
        "phone": phone,
        "birthday": f"{profile['birthday_day']}/{profile['birthday_month']}/{profile['birthday_year']}",
        "success": success,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "exit_ip": exit_ip_info["ip"],
        "exit_country": exit_ip_info["country"],
        "exit_city": exit_ip_info["city"],
    }
    entries.append(entry)
    log_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"  📝 Lưu thông tin vào {log_path}")

    # Also push to backend Redis store (fire and forget — non-blocking)
    _push_account_to_backend(entry)


# ─────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────

def _verify_account_login(email: str, password: str, proxy: Optional[str] = None) -> bool:
    """
    Thử login bằng email/password để xác minh account thật sự tồn tại.
    Google có thể silently delete accounts tạo bằng bot/datacenter IP.
    Returns True nếu login thành công.
    """
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return True  # không verify được, giả sử OK

    try:
        with sync_playwright() as pw:
            launch_kwargs: dict = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                         "--disable-quic", "--disable-http2"],
            }
            if proxy:
                launch_kwargs["proxy"] = _build_playwright_proxy(proxy)
            browser = pw.chromium.launch(**launch_kwargs)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-US",
            )
            page = ctx.new_page()

            try:
                page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded", timeout=30_000)
                time.sleep(1)

                # Nhập email
                email_field = page.locator('input[type="email"]')
                if email_field.count() == 0:
                    browser.close()
                    return False
                email_field.first.fill(email)
                page.keyboard.press("Enter")
                time.sleep(2)

                # Kiểm tra "Couldn't find your Google Account"
                error_texts = ["Couldn't find", "couldn't find", "No account found",
                               "Không tìm thấy", "không tìm thấy"]
                for txt in error_texts:
                    if page.locator(f"text={txt}").count() > 0 or txt.lower() in page.content().lower():
                        print(f"  ✗ Google: tài khoản không tồn tại")
                        browser.close()
                        return False

                # Nhập password nếu có field
                pwd_field = page.locator('input[type="password"]')
                if pwd_field.count() > 0:
                    pwd_field.first.fill(password)
                    page.keyboard.press("Enter")
                    time.sleep(3)

                    # Kiểm tra login thành công
                    success_indicators = ["myaccount.google.com", "mail.google.com",
                                          "youtube.com", "ManageAccount"]
                    if any(s in page.url for s in success_indicators):
                        print(f"  ✅ Login thành công!")
                        browser.close()
                        return True

                    # Kiểm tra wrong password (account tồn tại nhưng pw sai)
                    wrong_pw_texts = ["Wrong password", "wrong password", "Sai mật khẩu"]
                    for txt in wrong_pw_texts:
                        if txt.lower() in page.content().lower():
                            print(f"  ⚠️  Password sai — nhưng account CÓ tồn tại")
                            browser.close()
                            return True  # account tồn tại

                # Nếu vẫn ở trang login — account có tồn tại (chỉ chưa điền đủ)
                if "accounts.google.com" in page.url and "error" not in page.url.lower():
                    browser.close()
                    return True

                browser.close()
                return False

            except Exception as e:
                print(f"  ⚠️  Verify lỗi: {e}")
                browser.close()
                return False
    except Exception:
        return True  # không verify được, giả sử OK


def create_one_account(
    platform: str,
    fivesim: FiveSimClient,
    captcha_key: Optional[str],
    headless: bool,
    country: str = "auto",
    proxy: Optional[str] = None,
    rotate_country: bool = False,
) -> bool:
    service = PLATFORM_TO_FIVESIM.get(platform, "google")
    profile = generate_profile()

    print(f"\n{'='*55}")
    print(f"  Tạo tài khoản #{profile['username']} ({platform})")
    print(f"  Email: {profile['username']}@gmail.com")
    print(f"  Password: {profile['password']}")
    print(f"  Sinh: {profile['birthday_day']}/{profile['birthday_month']}/{profile['birthday_year']}")
    print(f"{'='*55}")

    # Kiểm tra proxy trước khi mua số (tránh mất tiền 5sim)
    if not test_proxy_connectivity(proxy):
        print(f"\n  ❌ Proxy không kết nối được Google — hủy, không mua số điện thoại")
        return False

    # ── Country rotation priority list (same as find_best_country) ──
    _ROTATE_PRIORITY = [
        "indonesia", "philippines", "india", "myanmar",
        "cambodia", "kenya", "ukraine", "russia", "vietnam",
    ]

    def _resolve_country_list() -> list:
        """Build ordered list of countries to try."""
        if country == "auto" or rotate_country:
            # Fetch all prices once, build map of available countries
            try:
                rows = fivesim.get_all_country_prices(service)
                price_map = {c: (cost, qty) for c, cost, qty in rows if qty > 0}
            except Exception:
                price_map = {}

            if country == "auto" or country not in price_map:
                # Priority order, skip zero-qty countries
                ordered = [(c, *price_map[c]) for c in _ROTATE_PRIORITY if c in price_map]
                # Append any remaining available countries not in priority list
                for c, cost, qty in (rows if 'rows' in dir() else []):
                    if c not in _ROTATE_PRIORITY and qty > 0:
                        ordered.append((c, cost, qty))
                return ordered if ordered else [("vietnam", 0.08, 0)]
            else:
                # Specific country requested — still rotate if flag set
                first = price_map.get(country, (0.08, 0))
                others = [(c, *price_map[c]) for c in _ROTATE_PRIORITY
                          if c in price_map and c != country]
                return [(country, first[0], first[1])] + others
        elif country == "cheapest":
            try:
                rows = fivesim.get_all_country_prices(service)
                return [(c, cost, qty) for c, cost, qty in rows if qty > 0]
            except Exception:
                return [("vietnam", 0.08, 0)]
        else:
            # Fixed country
            return [(country, 0.08, 1)]

    country_list = _resolve_country_list()
    max_country_attempts = 3 if rotate_country else 1

    last_order_id: Optional[int] = None
    cookie_file = None
    chosen_country = country_list[0][0] if country_list else "vietnam"
    chosen_phone = ""

    for attempt_idx, country_entry in enumerate(country_list[:max_country_attempts]):
        chosen_country, chosen_cost, _chosen_qty = country_entry

        if attempt_idx == 0:
            print(f"  🌍 Country được chọn: {chosen_country} (${chosen_cost:.3f})")
        else:
            print(f"  ⟳ Thử country tiếp theo: {chosen_country} (${chosen_cost:.3f})...")

        # Cancel previous order if we're rotating after a phone-step failure
        if last_order_id is not None:
            fivesim.cancel_order(last_order_id)
            last_order_id = None

        # Mua số điện thoại
        print(f"\n  📱 Mua số điện thoại ({service}, {chosen_country})...")
        try:
            order = fivesim.buy_number(service, chosen_country)
        except Exception as e:
            print(f"  ✗ Không mua được số tại {chosen_country}: {e}")
            if rotate_country and attempt_idx < max_country_attempts - 1:
                continue
            return False

        last_order_id = order["id"]
        chosen_phone = order.get("phone", "")
        print(f"  ✓ Số điện thoại: {chosen_phone} (order: {last_order_id})")

        # Tạo tài khoản
        cookie_file = create_google_account(
            profile=profile,
            phone=chosen_phone,
            fivesim=fivesim,
            order_id=last_order_id,
            captcha_key=captcha_key,
            headless=headless,
            proxy=proxy,
        )

        if cookie_file is not None:
            # Success or at least passed phone step — stop rotating
            break

        # cookie_file is None: browser failed (possibly at/before phone step)
        if rotate_country and attempt_idx < max_country_attempts - 1:
            # Order was already cancelled/finished inside create_google_account
            # but cancel again to be safe
            fivesim.cancel_order(last_order_id)
            last_order_id = None
            continue
        # No more rotations
        break

    phone = chosen_phone
    cookie_exists = cookie_file is not None and cookie_file.exists()

    # Verify account thật: thử login lại bằng email/password
    # Tránh false-positive khi Google silently delete account sau signup
    account_verified = False
    if cookie_exists:
        print(f"\n  🔍 Xác minh tài khoản tồn tại thật...")
        account_verified = _verify_account_login(
            email=f"{profile['username']}@gmail.com",
            password=profile["password"],
            proxy=proxy,
        )
        if not account_verified:
            print(f"  ❌ Tài khoản không login được — Google đã xóa (bot detection)")
            print(f"     Cần proxy residential chất lượng cao hơn hoặc thử lại sau")
            cookie_exists = False

    success = cookie_exists and account_verified
    save_profile_log(profile, phone, platform, success, proxy=proxy)

    if success:
        print(f"\n  🎉 Tài khoản tạo + xác minh thành công!")

        # Upload vào pool
        print(f"  ☁️  Upload cookies vào {platform} pool...")
        upload_cookie_to_pool(cookie_file, platform)

        # Giữ bản backup
        backup = Path(__file__).parent / f"cookies_{profile['username']}.txt"
        backup.write_bytes(cookie_file.read_bytes())
        print(f"  💾 Backup: {backup}")
    else:
        print(f"\n  ✗ Tạo tài khoản thất bại")
        if last_order_id is not None:
            fivesim.cancel_order(last_order_id)

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
# Throwaway log viewer
# ─────────────────────────────────────────────

def print_throwaway_log() -> None:
    """In bảng throwaway_accounts.json ra stdout."""
    log_path = Path(__file__).parent / "throwaway_accounts.json"
    if not log_path.exists():
        print(f"⚠️  Chưa có file log: {log_path}")
        return

    try:
        entries = json.loads(log_path.read_text())
    except Exception as e:
        print(f"❌ Không đọc được log: {e}")
        return

    if not entries:
        print("(Chưa có tài khoản nào được ghi log)")
        return

    # Column widths
    col_email    = max(len("email"),    max(len(e.get("email", "") or "")    for e in entries))
    col_password = max(len("password"), max(len(e.get("password", "") or "") for e in entries))
    col_phone    = max(len("phone"),    max(len(e.get("phone", "") or "")    for e in entries))
    col_success  = len("success")
    col_created  = max(len("created_at"), max(len(e.get("created_at", "") or "") for e in entries))
    col_ip       = max(len("exit_ip"),  max(len(e.get("exit_ip", "") or "")  for e in entries))

    sep = (f"+-{'-'*col_email}-+-{'-'*col_password}-+-{'-'*col_phone}-+"
           f"-{'-'*col_success}-+-{'-'*col_created}-+-{'-'*col_ip}-+")

    header = (f"| {'email':<{col_email}} | {'password':<{col_password}} |"
              f" {'phone':<{col_phone}} | {'success':<{col_success}} |"
              f" {'created_at':<{col_created}} | {'exit_ip':<{col_ip}} |")

    print(sep)
    print(header)
    print(sep)

    for e in entries:
        email    = e.get("email", "") or ""
        password = e.get("password", "") or ""
        phone    = e.get("phone", "") or ""
        success  = "yes" if e.get("success") else "no"
        created  = e.get("created_at", "") or ""
        exit_ip  = e.get("exit_ip", "") or ""
        row = (f"| {email:<{col_email}} | {password:<{col_password}} |"
               f" {phone:<{col_phone}} | {success:<{col_success}} |"
               f" {created:<{col_created}} | {exit_ip:<{col_ip}} |")
        print(row)

    print(sep)
    print(f"\nTong cong: {len(entries)} ban ghi  |  Log: {log_path}")


# ─────────────────────────────────────────────
# Daily daemon
# ─────────────────────────────────────────────

def _seconds_to_next_midnight(random_offset_max: int = 3600) -> tuple:
    """
    Tinh so giay den nua dem hom nay + random offset (0..random_offset_max giay).
    Tra ve (seconds_to_sleep, next_run_time_str).
    """
    import datetime
    now = time.time()
    dt_now = datetime.datetime.fromtimestamp(now)
    # Nua dem hom nay (00:00:00 ngay mai)
    dt_midnight = (dt_now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    offset = random.randint(0, random_offset_max)
    dt_next_run = dt_midnight + datetime.timedelta(seconds=offset)
    secs = max(0, (dt_next_run - dt_now).total_seconds())
    return secs, dt_next_run.strftime("%Y-%m-%d %H:%M:%S")


def run_daily_daemon(
    platform: str,
    count: int,
    fivesim: FiveSimClient,
    captcha_key: Optional[str],
    headless: bool,
    country: str,
    proxy: Optional[str],
    rotate_country: bool,
) -> None:
    """
    Daemon mode: tao N tai khoan moi ngay lien tuc den khi bi huy.
    Sau moi batch thanh cong, ngu den nua dem + random offset 0-3600s.
    Khi batch that bai hoan toan, thu lai sau 6h.
    """
    RETRY_INTERVAL_SECS = 6 * 3600  # 6 gio khi that bai

    iteration = 0
    while True:
        iteration += 1
        print(f"\n{'='*55}")
        print(f"  [Ngay {iteration}] Bat dau tao {count} tai khoan {platform}...")
        print(f"{'='*55}")

        ok = 0
        fail = 0
        for i in range(count):
            print(f"\n[{i+1}/{count}] Bat dau tao tai khoan...")
            success = create_one_account(
                platform=platform,
                fivesim=fivesim,
                captcha_key=captcha_key,
                headless=headless,
                country=country,
                proxy=proxy,
                rotate_country=rotate_country,
            )
            if success:
                ok += 1
            else:
                fail += 1

            if i < count - 1:
                delay = random.randint(30, 60)
                print(f"\n  Doi {delay}s truoc tai khoan tiep theo...")
                time.sleep(delay)

        print(f"\n  Ket qua batch: {ok} thanh cong | {fail} that bai")

        if ok == 0:
            # Toan bo that bai -> thu lai sau 6h
            retry_dt = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(time.time() + RETRY_INTERVAL_SECS)
            )
            hours = RETRY_INTERVAL_SECS // 3600
            minutes = (RETRY_INTERVAL_SECS % 3600) // 60
            print(f"  Toan bo that bai — thu lai sau {hours}h. Hen: {retry_dt}")
            time.sleep(RETRY_INTERVAL_SECS)
        else:
            # It nhat 1 thanh cong -> ngu den nua dem + random
            sleep_secs, next_run_str = _seconds_to_next_midnight(random_offset_max=3600)
            hours = int(sleep_secs) // 3600
            minutes = (int(sleep_secs) % 3600) // 60
            print(f"  Nghi den {next_run_str} ({hours}h {minutes}m)...")
            time.sleep(sleep_secs)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tu dong tao tai khoan throwaway + upload vao Cookie Pool"
    )
    parser.add_argument("--platform", default="youtube",
                        choices=list(PLATFORM_TO_FIVESIM.keys()),
                        help="Nen tang can tao tai khoan (default: youtube)")
    parser.add_argument("--count", type=int, default=1,
                        help="So tai khoan can tao")
    parser.add_argument("--country", default="auto",
                        help="Country cho 5sim. 'auto'=uu tien Indonesia/non-VN (SMS OTP), "
                             "'cheapest'=re nhat, hoac ten nuoc: vietnam, indonesia, philippines")
    parser.add_argument("--captcha-key", default=TWOCAPTCHA_API_KEY or None,
                        help="2captcha.com API key (bo trong = giai CAPTCHA thu cong)")
    parser.add_argument("--headless", action="store_true",
                        help="Chay browser an (can --captcha-key)")
    parser.add_argument("--list-balance", action="store_true",
                        help="Xem so du 5sim.net")
    parser.add_argument("--upload-cookie",
                        help="Upload cookie file thu cong (dung voi --platform)")
    parser.add_argument("--fivesim-key", default=FIVESIM_API_KEY,
                        help="5sim.net API key (hoac set FIVESIM_API_KEY)")
    parser.add_argument("--proxy", default=THROWAWAY_PROXY or None,
                        help="Residential proxy URL (vd: http://user:pass@host:port). "
                             "Bat buoc neu IP data-center bi Google chan SMS.")
    parser.add_argument("--test-proxy", action="store_true",
                        help="Chi kiem tra proxy co ket noi duoc accounts.google.com khong, roi thoat.")
    parser.add_argument("--rotate-country", action="store_true",
                        help="Khi tao that bai o buoc phone, tu dong thu country tiep theo "
                             "trong danh sach uu tien (toi da 3 country / tai khoan).")
    parser.add_argument("--daily", type=int, default=0, metavar="N",
                        help="Daemon mode: tao N tai khoan moi ngay lien tuc. "
                             "Nghi den nua dem sau moi batch thanh cong.")
    parser.add_argument("--throwaway-log", action="store_true",
                        help="In bang throwaway_accounts.json va thoat (danh cho admin UI).")
    args = parser.parse_args()

    # ── In log va thoat ───────────────────────────────────────
    if args.throwaway_log:
        print_throwaway_log()
        return

    # ── Upload thu cong ───────────────────────────────────────
    if args.upload_cookie:
        upload_cookie_manual(args.upload_cookie, args.platform)
        return

    # ── Test proxy connectivity ────────────────────────────────
    if args.test_proxy:
        active_proxy = resolve_proxy(args.proxy)
        ok = test_proxy_connectivity(active_proxy)
        sys.exit(0 if ok else 1)

    # ── Kiem tra API key ──────────────────────────────────────
    if not args.fivesim_key:
        print("Can FIVESIM_API_KEY. Set trong .env hoac --fivesim-key")
        print("   Dang ky tai: https://5sim.net")
        sys.exit(1)

    fivesim = FiveSimClient(args.fivesim_key)

    # ── Xem so du ────────────────────────────────────────────
    if args.list_balance:
        try:
            bal = fivesim.get_balance()
            print(f"So du 5sim.net: ${bal:.4f}")

            print("\nGia so dien thoai Google (top countries):")
            rows = fivesim.get_all_country_prices("google")
            for country, cost, count in rows[:15]:
                print(f"  {country:<15} ${cost:.3f}  ({count} available)")
        except Exception as e:
            print(f"Loi: {e}")
        return

    # ── Auto-detect headless: server khong co display -> bat buoc headless ────
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not has_display and not args.headless:
        print("Khong co man hinh, tu chuyen sang headless mode.")
        args.headless = True

    active_proxy = resolve_proxy(args.proxy)
    captcha_mode = "CapSolver (auto)" if args.captcha_key and args.captcha_key.startswith("CAP-") \
        else "2captcha (auto)" if args.captcha_key \
        else "bo qua (neu CAPTCHA xuat hien se fail)"

    # ── Daemon mode (--daily N) ───────────────────────────────
    if args.daily > 0:
        try:
            bal = fivesim.get_balance()
        except Exception:
            bal = 0.0
        print(f"So du 5sim.net: ${bal:.4f}")
        print(f"Nen tang: {args.platform} | So TK/ngay: {args.daily} | Country: {args.country}")
        print(f"Rotate country: {'bat' if args.rotate_country else 'tat'} | Proxy: {proxy_label(active_proxy)}")
        print(f"Bat dau daemon mode... (Ctrl+C de dung)")
        run_daily_daemon(
            platform=args.platform,
            count=args.daily,
            fivesim=fivesim,
            captcha_key=args.captcha_key,
            headless=args.headless,
            country=args.country,
            proxy=active_proxy,
            rotate_country=args.rotate_country,
        )
        return

    # ── Tao tai khoan ────────────────────────────────────────
    bal = fivesim.get_balance()
    print(f"So du 5sim.net: ${bal:.4f}")
    print(f"Nen tang: {args.platform}")
    print(f"So tai khoan: {args.count}")
    print(f"Country: {args.country}")
    print(f"CAPTCHA: {captcha_mode}")
    print(f"Proxy: {proxy_label(active_proxy)}")
    print(f"Browser: {'headless (an)' if args.headless else 'hien (co man hinh)'}")
    print(f"Rotate country: {'bat' if args.rotate_country else 'tat'}")
    print()

    ok = 0
    fail = 0
    for i in range(args.count):
        print(f"\n[{i+1}/{args.count}] Bat dau tao tai khoan...")
        success = create_one_account(
            platform=args.platform,
            fivesim=fivesim,
            captcha_key=args.captcha_key,
            headless=args.headless,
            country=args.country,
            proxy=active_proxy,
            rotate_country=args.rotate_country,
        )
        if success:
            ok += 1
        else:
            fail += 1

        if i < args.count - 1:
            delay = random.randint(30, 60)
            print(f"\n  Doi {delay}s truoc tai khoan tiep theo...")
            time.sleep(delay)

    print(f"\n{'='*55}")
    print(f"  Ket qua: {ok} thanh cong | {fail} that bai")
    print(f"  Xem log: {Path(__file__).parent / 'throwaway_accounts.json'}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()

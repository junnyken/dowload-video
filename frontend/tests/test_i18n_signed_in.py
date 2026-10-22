"""Signed-in screens in English, in a real Chromium against the production build.

A fake Supabase session is put in localStorage (supabase-js reads it without a
network call while it has not expired); Supabase and the API answer 503, so every screen shows its empty / error state. Checks, per signed-in
page under /en: no Vietnamese words on screen, no raw i18n keys, no JavaScript
error; and that the same page in Vietnamese still renders.
Run: npm run build && python3 tests/test_i18n_signed_in.py
"""
import base64, json, re, sys, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import test_i18n_browser as base_test

PAGES = ["/usage", "/history", "/playlists", "/analytics", "/archive", "/schedule", "/watch", "/phu-de",
         "/transcript-translate", "/transcript-asr", "/workspace-settings", "/audit", "/approvals",
         "/api-docs", "/api-keys", "/link-bot", "/billing", "/search", "/preferences", "/active",
         "/reset-password", "/desktop-login"]
VI = re.compile(r"\S*[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]\S*", re.I)
KEY = re.compile(r"\b[a-z]+[A-Z]\w*\.[a-z]\w+\b|\b\w+_(one|other)\b")
ALLOWED = ("Tiếng Việt", "Cốc Cốc",
           # /api-docs error sample: the API really returns these Vietnamese messages
           "Nền tảng tạm thời không phản hồi.", "Thử lại sau vài phút.")


def fake_session():
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    exp = int(time.time()) + 3600
    user = {"id": "00000000-0000-4000-8000-000000000001", "email": "test@example.com", "aud": "authenticated",
            "role": "authenticated", "app_metadata": {}, "user_metadata": {"display_name": "Test"}}
    jwt = f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': user['id'], 'exp': exp, 'role': 'authenticated'})}.sig"
    return {"access_token": jwt, "refresh_token": "r", "token_type": "bearer", "expires_in": 3600, "expires_at": exp, "user": user}


def main():
    srv, base = base_test.serve()
    check = base_test.check
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=base_test.CHROME, args=["--headless=new", "--no-sandbox"])
            ctx = b.new_context()
            ctx.route(re.compile(r"https://.*supabase\.co/.*"), lambda r: r.fulfill(status=503, body="{}"))
            # the API (VITE_API_URL, another origin): a server that answers 503
            ctx.route(lambda u: "/api/" in u and not u.startswith(base), lambda r: r.fulfill(status=503, content_type="application/json", body='{"detail":"unavailable"}'))
            session = json.dumps(fake_session())
            ctx.add_init_script(f"try {{ localStorage.setItem('sb-wtwnbagcqyedindtcadv-auth-token', {json.dumps(session)}); }} catch (e) {{}}")
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)[:150]))
            pg.goto(f"{base}/en", wait_until="networkidle")
            pg.wait_for_timeout(800)
            check("signed in (fake session): no Sign in button", pg.get_by_role("button", name="Sign in").count() == 0)
            for path in PAGES:
                errors.clear()
                pg.goto(f"{base}/en{path}", wait_until="networkidle")
                pg.wait_for_timeout(700)
                txt = pg.evaluate("document.body.innerText")
                for a in ALLOWED:
                    txt = txt.replace(a, "")
                vi = sorted(set(VI.findall(txt)))[:8]
                keys = sorted(set(m.group(0) for m in KEY.finditer(txt)))[:5]
                check(f"/en{path}: html lang en", pg.evaluate("document.documentElement.lang") == "en")
                check(f"/en{path}: all English", not vi and not keys, f"vi={vi} keys={keys}" if vi or keys else "")
                check(f"/en{path}: no JavaScript error", not errors, "; ".join(errors))
            for path in PAGES[:6]:
                errors.clear()
                pg.goto(f"{base}{path}", wait_until="networkidle")
                pg.wait_for_timeout(500)
                txt = pg.evaluate("document.body.innerText")
                keys = sorted(set(m.group(0) for m in KEY.finditer(txt)))[:5]
                check(f"{path} (vi): renders, no raw keys, no error", len(txt) > 50 and not keys and not errors, f"{keys} {errors}")
            b.close()
    finally:
        srv.shutdown()
    print(f"\n{sum(base_test.res)}/{len(base_test.res)} passed")
    sys.exit(0 if all(base_test.res) else 1)


if __name__ == "__main__":
    main()

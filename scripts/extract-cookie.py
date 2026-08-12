#!/usr/bin/env python3
"""
Extract YouTube cookies from local Chrome/Firefox browser.

Priority:
  1. yt-dlp internal API (handles OS-level decryption automatically)
  2. browser-cookie3 (fallback)

Usage:
  python3 extract-cookie.py [output_path] [browser]

Output: prints output path to stdout on success, exits 1 on failure.
"""
import sys
import os

OUTPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/vidgrab_yt_cookies.txt"
BROWSER = (sys.argv[2] if len(sys.argv) > 2 else "chrome").lower()


def _has_youtube_cookies(path: str) -> bool:
    """Check file exists, is non-trivial, and has actual YouTube auth cookies."""
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 200:
        return False
    with open(path, errors="ignore") as f:
        content = f.read()
    # Must have at least one of the critical YouTube session cookies
    required = {"SID", "SSID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"}
    return any(name in content for name in required)


def try_ytdlp(output_path: str, browser: str) -> bool:
    try:
        import yt_dlp
        from yt_dlp import cookies as ytcookies

        jar = ytcookies.YoutubeDLCookieJar(output_path)

        # API signature changed across versions — try newest first
        for sig in [
            lambda: ytcookies.extract_cookies_from_browser(
                jar, browser, logger=None, keyring=None, profile=None
            ),
            lambda: ytcookies.extract_cookies_from_browser(jar, browser, logger=None),
            lambda: ytcookies.extract_cookies_from_browser(jar, browser),
        ]:
            try:
                sig()
                break
            except TypeError:
                continue

        jar.save(ignore_discard=True, ignore_expires=True)
        return _has_youtube_cookies(output_path)

    except ImportError:
        print("yt-dlp not installed", file=sys.stderr)
        return False
    except Exception as e:
        print(f"yt-dlp extraction failed: {e}", file=sys.stderr)
        return False


def try_browser_cookie3(output_path: str, browser: str) -> bool:
    try:
        import browser_cookie3
        import http.cookiejar

        fn = getattr(browser_cookie3, browser, None)
        if not fn:
            print(f"browser_cookie3: browser '{browser}' not supported", file=sys.stderr)
            return False

        cj = fn(domain_name=".youtube.com")

        jar = http.cookiejar.MozillaCookieJar(output_path)
        # Write Netscape header
        with open(output_path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
        for cookie in cj:
            jar.set_cookie(cookie)
        jar.save(ignore_discard=True, ignore_expires=True)

        return _has_youtube_cookies(output_path)

    except ImportError:
        print("browser_cookie3 not installed (pip install browser-cookie3)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"browser_cookie3 failed: {e}", file=sys.stderr)
        return False


# ── Main ─────────────────────────────────────────────────────────────────

if try_ytdlp(OUTPUT_PATH, BROWSER):
    print(OUTPUT_PATH)
    sys.exit(0)

print("yt-dlp method failed, trying browser-cookie3...", file=sys.stderr)

if try_browser_cookie3(OUTPUT_PATH, BROWSER):
    print(OUTPUT_PATH)
    sys.exit(0)

print(
    "\n[extract-cookie] Both methods failed.\n"
    "Manual alternative:\n"
    "  1. Install Chrome extension 'Get cookies.txt LOCALLY'\n"
    "  2. Open youtube.com → click extension → Export\n"
    "  3. Run: ./scripts/refresh-cookie.sh --file /path/to/cookies.txt\n",
    file=sys.stderr,
)
sys.exit(1)

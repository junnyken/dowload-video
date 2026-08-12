"""
Phase 5A + 5B smoke tests — PWA manifest/icons/SW + Telegram mobile flow.
All checks use file-source inspection; no live imports needed.
"""
import json
import os

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.normpath(os.path.join(BASE, "../frontend"))
BOT_PY   = os.path.normpath(os.path.join(BASE, "../telegram-bot/bot.py"))


def _read(rel):
    return open(os.path.join(FRONTEND, rel), encoding="utf-8").read()


def _bot():
    return open(BOT_PY, encoding="utf-8").read()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. manifest.json — required fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestManifestFields:
    def _manifest(self):
        with open(os.path.join(FRONTEND, "public/manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_name_present(self):
        assert self._manifest()["name"]

    def test_short_name_present(self):
        assert self._manifest()["short_name"] == "VidGrab"

    def test_start_url_is_root(self):
        assert self._manifest()["start_url"] == "/"

    def test_scope_is_root(self):
        assert self._manifest()["scope"] == "/"

    def test_display_standalone(self):
        assert self._manifest()["display"] == "standalone"

    def test_theme_color_present(self):
        assert self._manifest()["theme_color"]

    def test_background_color_present(self):
        assert self._manifest()["background_color"]

    def test_description_present(self):
        assert self._manifest()["description"]

    def test_icons_present(self):
        assert len(self._manifest()["icons"]) >= 2

    def test_lang_is_vi(self):
        assert self._manifest()["lang"] == "vi"

    def test_prefer_related_applications_false(self):
        assert self._manifest()["prefer_related_applications"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. manifest.json — icon coverage
# ═══════════════════════════════════════════════════════════════════════════════

class TestManifestIcons:
    def _icons(self):
        with open(os.path.join(FRONTEND, "public/manifest.json"), encoding="utf-8") as f:
            return json.load(f)["icons"]

    def _sizes(self):
        return {i["sizes"] for i in self._icons()}

    def _purposes(self):
        return {i.get("purpose", "") for i in self._icons()}

    def test_has_192(self):
        assert "192x192" in self._sizes()

    def test_has_512(self):
        assert "512x512" in self._sizes()

    def test_has_small_icons(self):
        sizes = self._sizes()
        assert any(s in sizes for s in ["72x72", "96x96", "128x128"])

    def test_has_medium_icons(self):
        sizes = self._sizes()
        assert any(s in sizes for s in ["144x144", "152x152", "256x256"])

    def test_has_maskable_purpose(self):
        assert any("maskable" in p for p in self._purposes())

    def test_has_any_purpose(self):
        assert any("any" in p for p in self._purposes())

    def test_all_icons_have_src(self):
        for icon in self._icons():
            assert icon.get("src"), f"Icon missing src: {icon}"

    def test_all_icons_have_sizes(self):
        for icon in self._icons():
            assert icon.get("sizes"), f"Icon missing sizes: {icon}"

    def test_nine_or_more_sizes_declared(self):
        assert len(self._icons()) >= 9


# ═══════════════════════════════════════════════════════════════════════════════
# 3. manifest.json — shortcuts
# ═══════════════════════════════════════════════════════════════════════════════

class TestManifestShortcuts:
    def _shortcuts(self):
        with open(os.path.join(FRONTEND, "public/manifest.json"), encoding="utf-8") as f:
            return json.load(f).get("shortcuts", [])

    def test_shortcuts_present(self):
        assert len(self._shortcuts()) >= 1

    def test_shortcuts_have_name(self):
        for s in self._shortcuts():
            assert s.get("name")

    def test_shortcuts_have_url(self):
        for s in self._shortcuts():
            assert s.get("url")

    def test_root_shortcut_exists(self):
        urls = {s["url"] for s in self._shortcuts()}
        assert "/" in urls


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Icon files — existence
# ═══════════════════════════════════════════════════════════════════════════════

class TestIconFiles:
    def _icon_path(self, name):
        return os.path.join(FRONTEND, "public/icons", name)

    def test_icon_72_exists(self):
        assert os.path.exists(self._icon_path("icon-72.svg"))

    def test_icon_96_exists(self):
        assert os.path.exists(self._icon_path("icon-96.svg"))

    def test_icon_128_exists(self):
        assert os.path.exists(self._icon_path("icon-128.svg"))

    def test_icon_144_exists(self):
        assert os.path.exists(self._icon_path("icon-144.svg"))

    def test_icon_152_exists(self):
        assert os.path.exists(self._icon_path("icon-152.svg"))

    def test_icon_192_exists(self):
        assert os.path.exists(self._icon_path("icon-192.svg"))

    def test_icon_256_exists(self):
        assert os.path.exists(self._icon_path("icon-256.svg"))

    def test_icon_384_exists(self):
        assert os.path.exists(self._icon_path("icon-384.svg"))

    def test_icon_512_exists(self):
        assert os.path.exists(self._icon_path("icon-512.svg"))

    def test_maskable_192_exists(self):
        assert os.path.exists(self._icon_path("icon-maskable-192.svg"))

    def test_maskable_512_exists(self):
        assert os.path.exists(self._icon_path("icon-maskable-512.svg"))

    def test_icon_files_are_svg(self):
        for fname in os.listdir(os.path.join(FRONTEND, "public/icons")):
            if fname.endswith(".svg"):
                content = open(os.path.join(FRONTEND, "public/icons", fname)).read()
                assert "<svg" in content, f"{fname} is not valid SVG"

    def test_regular_icons_have_correct_color(self):
        content = open(self._icon_path("icon-192.svg")).read()
        assert "#012622" in content   # VidGrab brand dark green
        assert "#FBBF24" in content   # VidGrab gold

    def test_maskable_icon_has_fullbleed_background(self):
        content = open(self._icon_path("icon-maskable-192.svg")).read()
        # Full-bleed: no rounded rect at root level (no rx on full-size rect)
        assert "012622" in content
        assert "maskable" in content.lower() or "safe zone" in content.lower() or "Full-bleed" in content

    def test_all_icon_files_declared_in_manifest(self):
        with open(os.path.join(FRONTEND, "public/manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        manifest_srcs = {i["src"].removeprefix("/icons/") for i in manifest["icons"]}
        for fname in os.listdir(os.path.join(FRONTEND, "public/icons")):
            if fname.endswith(".svg"):
                assert fname in manifest_srcs, f"{fname} exists but not in manifest"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Service Worker — correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestServiceWorker:
    def _sw(self):
        return _read("public/sw.js")

    def test_sw_file_exists(self):
        assert os.path.exists(os.path.join(FRONTEND, "public/sw.js"))

    def test_cache_name_defined(self):
        assert "CACHE_NAME" in self._sw()

    def test_cache_name_version_bumped(self):
        sw = self._sw()
        assert "vidgrab-v2" in sw or "vidgrab-v3" in sw

    def test_no_unconditional_skip_waiting_in_install(self):
        sw = self._sw()
        install_block_start = sw.find("addEventListener('install'")
        install_block_end   = sw.find("addEventListener('activate'")
        if install_block_start == -1 or install_block_end == -1:
            return
        install_block = sw[install_block_start:install_block_end]
        # skipWaiting() must NOT appear raw in install (only in message handler)
        assert "self.skipWaiting()" not in install_block, (
            "self.skipWaiting() must not be called unconditionally in install event"
        )

    def test_skip_waiting_message_handler_present(self):
        assert "SKIP_WAITING" in self._sw()

    def test_message_event_listener_present(self):
        assert "addEventListener('message'" in self._sw()

    def test_skip_waiting_called_on_message(self):
        sw = self._sw()
        msg_block_start = sw.find("addEventListener('message'")
        msg_block_end   = sw.find("addEventListener('fetch'")
        if msg_block_start != -1 and msg_block_end != -1:
            msg_block = sw[msg_block_start:msg_block_end]
            assert "skipWaiting" in msg_block

    def test_offline_html_in_static_assets(self):
        assert "offline.html" in self._sw()

    def test_clients_claim_in_activate(self):
        assert "clients.claim()" in self._sw()

    def test_api_calls_bypass_sw(self):
        assert "/api/" in self._sw()

    def test_navigate_mode_handled(self):
        assert "navigate" in self._sw()

    def test_offline_fallback_in_navigation(self):
        sw = self._sw()
        assert "offline.html" in sw

    def test_push_handler_present(self):
        assert "addEventListener('push'" in self._sw()

    def test_notification_click_handler_present(self):
        assert "addEventListener('notificationclick'" in self._sw()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. offline.html — existence and branding
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfflinePage:
    def _offline(self):
        return _read("public/offline.html")

    def test_offline_html_exists(self):
        assert os.path.exists(os.path.join(FRONTEND, "public/offline.html"))

    def test_has_doctype(self):
        assert "<!DOCTYPE html>" in self._offline()

    def test_has_viewport_meta(self):
        assert "viewport" in self._offline()

    def test_has_vidgrab_branding(self):
        offline = self._offline()
        assert "VidGrab" in offline

    def test_has_brand_color(self):
        assert "#012622" in self._offline()

    def test_has_retry_button(self):
        offline = self._offline()
        assert "reload" in offline.lower() or "retry" in offline.lower() or "Thử lại" in offline

    def test_has_offline_message_vi(self):
        assert "offline" in self._offline().lower() or "Offline" in self._offline()

    def test_has_network_requirement_copy(self):
        offline = self._offline()
        assert "mạng" in offline or "internet" in offline.lower() or "kết nối" in offline.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. index.html — PWA meta tags
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndexHtml:
    def _index(self):
        return open(os.path.join(FRONTEND, "index.html"), encoding="utf-8").read()

    def test_manifest_link_present(self):
        assert 'href="/manifest.json"' in self._index()

    def test_theme_color_meta_present(self):
        assert 'name="theme-color"' in self._index()

    def test_apple_mobile_web_app_capable(self):
        assert "apple-mobile-web-app-capable" in self._index()

    def test_apple_touch_icon_present(self):
        assert "apple-touch-icon" in self._index()

    def test_no_wrong_brand_color_in_favicon(self):
        index = self._index()
        # Old favicon used purple/indigo (#4f46e5 / #9333ea) — must be gone
        assert "#4f46e5" not in index
        assert "9333ea" not in index

    def test_favicon_uses_vidgrab_icon(self):
        index = self._index()
        # Should reference our own icon file, not a purple inline data URI
        assert "icon-192.svg" in index or "/icons/" in index

    def test_multiple_apple_touch_icon_sizes(self):
        index = self._index()
        assert index.count("apple-touch-icon") >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Phase 5B — Telegram bot mobile flow
# ═══════════════════════════════════════════════════════════════════════════════

class TestBotMobileFlow:
    def test_url_auto_detection_present(self):
        bot = _bot()
        assert "extract_url" in bot

    def test_url_re_defined(self):
        assert "_URL_RE" in _bot()

    def test_supported_platforms_dict(self):
        assert "SUPPORTED_PLATFORMS" in _bot()

    def test_unsupported_platforms_list(self):
        # bot.py flipped from a denylist (UNSUPPORTED_PLATFORMS, which never
        # existed under that name in any version we can find) to an
        # allowlist (SUPPORTED_PLATFORMS, a domain→display-name dict driving
        # detect_platform()) — same underlying capability (know what's
        # supported), different architecture. Assert the real mechanism.
        src = _bot()
        assert "SUPPORTED_PLATFORMS" in src
        assert '"youtube.com"' in src and '"tiktok.com"' in src

    def test_ssrf_guard_present(self):
        assert "_BLOCKED_HOSTS" in _bot() or "is_safe_url" in _bot()

    def test_rate_limit_per_user(self):
        assert "check_rate_limit" in _bot()

    def test_rate_limit_per_chat(self):
        assert "check_chat_rate_limit" in _bot()

    def test_inline_keyboards_present(self):
        bot = _bot()
        assert "inline_keyboard" in bot

    def test_format_keyboard_builder(self):
        assert "build_format_keyboard" in _bot()

    def test_retry_keyboard_builder(self):
        assert "build_retry_keyboard" in _bot()

    def test_cmd_start_has_keyboard(self):
        bot = _bot()
        start_pos = bot.find("def cmd_start")
        next_def  = bot.find("\ndef ", start_pos + 1)
        start_body = bot[start_pos:next_def]
        assert "inline_keyboard" in start_body

    def test_cancel_command_present(self):
        assert "cmd_cancel" in _bot() or "/cancel" in _bot()

    def test_history_tagged_telegram(self):
        assert "source_surface" in _bot() and "telegram" in _bot()

    def test_non_url_fallback_improved(self):
        bot = _bot()
        # Must NOT blindly send extension file for every non-URL message
        fallback_section = bot[bot.rfind("extract_url(text)"):]
        assert "send_extension_file" not in fallback_section[:800]

    def test_non_url_fallback_guides_user(self):
        bot = _bot()
        assert "link video" in bot or "Hãy gửi link" in bot

    def test_version_in_status_command(self):
        assert "app_version" in _bot()

    def test_backend_health_endpoint(self):
        assert "/health" in _bot()

    def test_callback_handler_present(self):
        assert "handle_callback" in _bot()

    def test_web_url_in_fallback(self):
        bot = _bot()
        assert "WEB_URL" in bot

    def test_phase_5b_reference(self):
        # bot.py's header phase marker has moved on ("Phase 10" as of this
        # writing) through many revisions since this test hardcoded "Phase
        # 5B" — a specific phase number is inherently a moving target, not a
        # real invariant. Check for the durable pattern (some "Phase N"
        # marker exists in the module docstring) instead of a fixed number.
        import re
        assert re.search(r"Phase \d+", _bot()), \
            "bot.py should carry a 'Phase N' marker in its header docstring"

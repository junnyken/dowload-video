"""
Phase 32A UI — "Phụ đề và Phiên âm" hub (/phu-de) and admin "Phiên âm (ASR)",
driven in a real browser against a built bundle with every /api/ call mocked.

Build first with Supabase vars so the auth client can boot (no network is used):
  VITE_SUPABASE_URL=https://test.supabase.co VITE_SUPABASE_ANON_KEY=anon \
    npx vite build --outDir "$ASR_DIST" --emptyOutDir
Run: ASR_DIST=<that dir> python tests/test_asr_hub_browser.py
(ASR_DIST defaults to ./dist, which must then have been built with those vars.)
Optional: ASR_SHOTS=<dir> to save screenshots, FONTCONFIG_FILE / ASR_CHROME for the browser.
"""
import functools, http.server, json, os, socketserver, sys, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

DIST = Path(os.environ.get("ASR_DIST") or Path(__file__).resolve().parent.parent / "dist")
SHOTS = Path(os.environ["ASR_SHOTS"]) if os.environ.get("ASR_SHOTS") else None
res = []


def check(n, ok, d=""):
    res.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + n + (f"   [{d}]" if d else ""), flush=True)


class SPA(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (DIST / self.path.lstrip("/").split("?")[0]).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *a):
        pass


# Every error_code the backend can answer, and the Vietnamese text the UI must show.
JOB_ERROR_CODES = {
    "provider_bad_output": "trả kết quả không hợp lệ",
    "provider_transient": "đang quá tải",
    "provider_error": "gặp lỗi",
    "no_speech": "Không nhận diện được lời thoại",
    "timeout": "quá thời gian cho phép",
    "interrupted": "gián đoạn giữa chừng",
    "stuck_timeout": "bị treo",
    "source_missing": "hết hạn hoặc bị xoá",
    "too_long": "vượt giới hạn thời lượng",
    "audio_extract_failed": "Không tách được âm thanh",
    "internal_error": "Lỗi hệ thống",
    "queue_unavailable": "Không xếp được job",
}
CREATE_ERRORS = [  # (status, body, expected Vietnamese fragment)
    (503, {"detail": "x", "error_code": "asr_disabled"}, "đang tạm tắt"),
    (503, {"detail": "x", "error_code": "asr_paused"}, "tạm dừng bởi quản trị viên"),
    (503, {"detail": "x", "error_code": "spend_ceiling_reached"}, "hết ngân sách"),
    (503, {"detail": "x", "error_code": "budget_unavailable"}, "Không kiểm tra được ngân sách"),
    (503, {"detail": "x", "error_code": "provider_unavailable"}, "chưa sẵn sàng"),
    (503, {"detail": "x", "error_code": "queue_unavailable"}, "Không xếp được job"),
    (507, {"detail": {"error_code": "disk_full", "user_message": "Hệ thống tạm hết dung lượng lưu trữ."}}, "hết dung lượng"),
    (422, {"detail": "Video dài 50.0 phút, vượt giới hạn 45 phút."}, "vượt giới hạn 45 phút"),
    (422, {"detail": "Vượt quá hạn mức tạo phụ đề tự động trong ngày (120 phút/ngày)."}, "Vượt quá hạn mức"),
]

QUOTA_OK = {"enabled": True, "unavailable_reason": None, "minutes_used": 37.5, "minutes_limit": 120,
            "per_job_max_minutes": 45, "reset_at_utc": "2030-01-16T00:00:00+00:00"}


def jobs_all_states():
    base = [
        ("q", "queued", None), ("e", "extracting_audio", None), ("t", "transcribing", None),
        ("d", "done", None), ("c", "failed", "cancelled"),
    ] + [(f"f-{c}", "failed", c) for c in JOB_ERROR_CODES]
    return [{"id": i, "video_title": f"Video {i}", "status": s, "duration_sec": 125.0, "detected_language": "vi" if s == "done" else None,
             "progress_pct": 40 if s in ("extracting_audio", "transcribing") else 0, "error": None, "error_code": c} for i, s, c in base]


def shot(pg, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def new_page(browser, base, *, quota, jobs, width=1100, create=None, admin=None):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    state = {"posts": [], "deletes": [], "downloads": []}

    def api(route):
        req = route.request
        url, m = req.url, req.method
        def j(body, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        if "/transcript-asr/quota" in url:
            return j(quota) if not isinstance(quota, int) else j({"detail": "x"}, quota)
        if "/transcript-asr/jobs" in url and "/download" in url:
            state["downloads"].append(url)
            return route.fulfill(status=200, body="1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", content_type="text/plain")
        if "/transcript-asr/jobs" in url and m == "GET":
            return j({"jobs": jobs})
        if "/transcript-asr/jobs" in url and m == "POST":
            state["posts"].append(req.post_data)
            status, body = create
            return j(body, status)
        if "/transcript-asr/jobs" in url and m == "DELETE":
            state["deletes"].append(url)
            return j({"deleted": True})
        if "/api/v1/history" in url:
            return j({"jobs": [{"id": "h1", "title": "Bài giảng 1", "platform": "youtube", "local_file_id": "a.mp4"}]})
        if "/transcript-translate/jobs" in url:
            return j({"jobs": []})
        if admin and "/api/v1/admin/" in url:
            return admin(url, m, req, j)
        return j({})

    ctx.route("**/api/**", api)
    ctx.route("**/auth/v1/**", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
    ctx.add_init_script("""
      localStorage.setItem('sb-test-auth-token', JSON.stringify({
        access_token:'t', refresh_token:'r', token_type:'bearer', expires_in:99999,
        expires_at: Math.floor(Date.now()/1000)+99999,
        user:{id:'u1', email:'a@b.c', aud:'authenticated', app_metadata:{}, user_metadata:{}, created_at:'2030-01-01T00:00:00Z'}}));
      localStorage.setItem('vg_admin_session', JSON.stringify({email:'t@t', role:'admin', sessionToken:'tok',
        expiresAt: new Date(Date.now()+3600e3).toISOString()}));
    """)
    return pg, errs, state


def main():
    handler = functools.partial(SPA, directory=str(DIST))
    chrome = os.environ.get("ASR_CHROME")
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chromium", args=["--no-sandbox"]) if not chrome else \
                p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])

            # 1. Enabled hub, jobs in every state, error codes in Vietnamese.
            pg, errs, st = new_page(b, base, quota=QUOTA_OK, jobs=jobs_all_states(), create=(200, {}))
            pg.goto(base + "/phu-de?tab=asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            body = pg.inner_text("body")
            check("hub heading + three tabs", "Phụ đề và Phiên âm" in body and all(t in body for t in ("Trích phụ đề", "Phiên âm AI", "Dịch")))
            check("remaining minutes shown", "Còn 82,5 phút" in body, body[body.find("Còn"):][:50])
            check("per-job cap shown", "tối đa 45 phút" in body)
            for lbl in ("Đang chờ", "Đang tách âm thanh", "Đang nhận diện giọng nói", "Hoàn tất", "Đã huỷ", "Thất bại"):
                check(f"status label '{lbl}'", lbl in body)
            for code, frag in JOB_ERROR_CODES.items():
                check(f"error_code {code} -> Vietnamese", frag in body, frag)
            check("create button visible when enabled", pg.get_by_text("Chọn video đã tải để tạo phụ đề").count() == 1)
            check("no page error (enabled)", not errs, "; ".join(errs)[:80])
            shot(pg, "hub-enabled-desktop")

            # Download format chooser + cancel + delete.
            pg.locator("table >> text=Video d").first.wait_for()
            pg.locator("table button[aria-label^='Tải phụ đề Video d']").click()
            check("format chooser lists SRT/VTT/TXT", all(pg.get_by_role("dialog").get_by_text(f, exact=True).count() for f in ("SRT", "VTT", "TXT")))
            shot(pg, "hub-download-chooser")
            pg.get_by_role("dialog").get_by_text("VTT", exact=True).click(); pg.wait_for_timeout(500)
            check("download requested with format=vtt", any("format=vtt" in u for u in st["downloads"]), str(st["downloads"]))
            pg.locator("table button[aria-label^='Huỷ job Video q']").click()
            check("cancel asks for confirmation (refund wording)", "hoàn lại hạn mức" in pg.get_by_role("dialog").inner_text())
            shot(pg, "hub-cancel-confirm")
            pg.get_by_role("dialog").get_by_role("button", name="Huỷ job").click(); pg.wait_for_timeout(400)
            check("DELETE sent on confirm", len(st["deletes"]) == 1)
            pg.locator("table button[aria-label^='Dịch phụ đề Video d']").click()
            check("translate hand-off dialog", pg.get_by_label("Ngôn ngữ đích").count() == 1)
            pg.close()

            # 2. Create error codes are shown in Vietnamese.
            for status, bodyj, frag in CREATE_ERRORS:
                pg, errs, st = new_page(b, base, quota=QUOTA_OK, jobs=[], create=(status, bodyj))
                pg.goto(base + "/transcript-asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
                pg.get_by_text("Chọn video đã tải để tạo phụ đề").click()
                pg.get_by_text("Bài giảng 1").click(); pg.wait_for_timeout(500)
                alert = pg.get_by_role("alert").first.inner_text() if pg.get_by_role("alert").count() else ""
                check(f"create {status} {bodyj.get('error_code') or 'detail'} -> '{frag}'", frag in alert, alert[:70])
                if bodyj.get("error_code") == "asr_paused":
                    shot(pg, "hub-create-error")
                pg.close()

            # 3. Disabled: clear state, no create button, existing jobs still listed.
            pg, errs, _ = new_page(b, base, quota={**QUOTA_OK, "enabled": False, "unavailable_reason": "asr_disabled"},
                                   jobs=jobs_all_states()[3:4], create=(200, {}))
            pg.goto(base + "/phu-de?tab=asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            body = pg.inner_text("body")
            check("disabled message", "Tính năng đang tạm tắt" in body)
            check("create button hidden when disabled", pg.get_by_text("Chọn video đã tải để tạo phụ đề").count() == 0)
            check("existing job still listed when disabled", "Video d" in body)
            shot(pg, "hub-disabled")
            pg.close()

            # 4. Quota exhausted: warning, no create button.
            pg, errs, _ = new_page(b, base, quota={**QUOTA_OK, "minutes_used": 120}, jobs=[], create=(200, {}))
            pg.goto(base + "/phu-de?tab=asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            body = pg.inner_text("body")
            check("exhausted message", "Đã hết hạn mức phiên âm hôm nay" in body)
            check("create button hidden when exhausted", pg.get_by_text("Chọn video đã tải để tạo phụ đề").count() == 0)
            shot(pg, "hub-quota-exhausted")
            pg.close()

            # 4b. minutes_used null: never fake a number; quota fetch failure hides the button.
            pg, errs, _ = new_page(b, base, quota={**QUOTA_OK, "minutes_used": None}, jobs=[], create=(200, {}))
            pg.goto(base + "/phu-de?tab=asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            body = pg.inner_text("body")
            check("unknown usage says so, not 0", "Không đọc được số phút đã dùng" in body and "Còn 0" not in body)
            pg.close()
            pg, errs, _ = new_page(b, base, quota=500, jobs=[], create=(200, {}))
            pg.goto(base + "/phu-de?tab=asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            check("quota error state hides create", "Không kiểm tra được hạn mức" in pg.inner_text("body")
                  and pg.get_by_text("Chọn video đã tải để tạo phụ đề").count() == 0)
            pg.close()

            # 5. Old deep links still land on the right tab; extract + translate tabs render.
            pg, errs, _ = new_page(b, base, quota=QUOTA_OK, jobs=[], create=(200, {}))
            pg.goto(base + "/transcript-translate", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            check("/transcript-translate opens Dịch tab", pg.get_by_role("tab", name="Dịch").get_attribute("aria-selected") == "true")
            pg.goto(base + "/transcript-asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            check("/transcript-asr opens Phiên âm AI tab", pg.get_by_role("tab", name="Phiên âm AI").get_attribute("aria-selected") == "true")
            pg.goto(base + "/phu-de", wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
            check("/phu-de defaults to Trích phụ đề", "Về trang chủ để dán link video" in pg.inner_text("body"))
            shot(pg, "hub-extract")
            pg.get_by_role("tab", name="Dịch").click(); pg.wait_for_timeout(600)
            shot(pg, "hub-translate")
            check("no page error (tabs)", not errs, "; ".join(errs)[:80])
            pg.close()

            # 6. Mobile 390px: no horizontal overflow, cards instead of table, touch targets >= 44px.
            pg, errs, _ = new_page(b, base, quota=QUOTA_OK, jobs=jobs_all_states()[:6], create=(200, {}), width=390)
            pg.goto(base + "/phu-de?tab=asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            check("390px: no horizontal scroll", pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
            check("390px: cards shown, table hidden", pg.locator("[data-testid=jobs-cards]").is_visible() and not pg.locator("[data-testid=jobs-table]").is_visible())
            small = pg.evaluate("""() => [...document.querySelectorAll('[data-testid=asr-panel] button, [role=tab]')]
                .filter(b => b.offsetParent && b.getBoundingClientRect().height < 43.5).map(b => b.innerText.slice(0,20))""")
            check("390px: touch targets >= 44px", not small, str(small))
            shot(pg, "hub-mobile-390")
            pg.close()

            # 7. Admin page.
            summary = {"day_utc": "2030-01-15", "enabled": True, "provider": "gemini", "model": "gemini-x", "price_per_min_usd": 0.003,
                       "price_verified": False, "killswitch": False, "spend_today_usd": 0.42, "spend_ceiling_usd": 3.0,
                       "minutes_reserved_today": 140.5, "minutes_cap_today": 600, "minutes_in_jobs_today": 140.5,
                       "per_user_daily_minutes_limit": 120, "jobs_today": 9, "jobs_by_status": {"done": 6, "failed": 2, "queued": 1},
                       "failure_rate": 0.25, "failures_by_code": {"no_speech": 1, "timeout": 1}, "avg_processing_sec": 31.2,
                       "avg_turnaround_sec": None, "per_provider": {"gemini": {"jobs": 9, "done": 6, "failed": 2, "est_cost_usd": 0.42, "actual_cost_usd": 0.0}},
                       "redis_ok": True, "db_ok": True}
            sel = {"mode": "ok", "kill": []}

            def admin(url, m, req, j):
                if url.endswith("/admin/asr/summary"):
                    return j({**summary, "killswitch": bool(sel["kill"] and sel["kill"][-1])})
                if url.endswith("/admin/asr/killswitch"):
                    sel["kill"].append(json.loads(req.post_data)["on"]); return j({"success": True, "killswitch": sel["kill"][-1]})
                if url.endswith("/admin/asr/selftest"):
                    sel["body"] = json.loads(req.post_data)
                    if sel["mode"] == "ok":
                        return j({"ok": True, "provider": "gemini", "model": "gemini-x", "clip_sec": 60, "language": "vi", "segment_count": 2,
                                  "segments": [{"start": 0, "end": 2.5, "text": "Xin chào"}, {"start": 2.5, "end": 5, "text": "Tạm biệt"}],
                                  "srt_preview": "1\n00:00:00,000 --> 00:00:02,500\nXin chào\n", "timings_ms": {"extract_ms": 120, "provider_ms": 4300, "total_ms": 4500},
                                  "cost_estimate_usd": 0.003, "counted_against_ceiling": True})
                    return j({"ok": False, "error_code": "provider_bad_output", "detail": "Dịch vụ nhận dạng giọng nói trả kết quả không hợp lệ, chưa thể tạo phụ đề.",
                              "cost_estimate_usd": 0.003, "raw_output": "{not json at all"}, 502)
                return j({})

            pg, errs, _ = new_page(b, base, quota=QUOTA_OK, jobs=[], create=(200, {}), admin=admin)
            pg.goto(base + "/vid-admin/asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1800)
            body = pg.inner_text("body")
            check("admin heading + stat cards", "Phiên âm (ASR)" in body and "chi phí hôm nay" in body.lower() and "$0.4200" in body)
            check("admin unknown shows dash (avg turnaround)", pg.locator("[data-testid=asr-stats] >> text=Chờ + xử lý").locator("xpath=..").inner_text().count("—") >= 1)
            check("selftest cost warning visible", "tốn tiền thật" in pg.locator("[data-testid=selftest-cost-warning]").inner_text())
            check("file_id help explains where to copy", "Sao chép địa chỉ liên kết" in body)
            shot(pg, "admin-summary")
            pg.get_by_role("button", name="Tạm dừng phiên âm").click()
            check("kill switch asks for confirm", pg.get_by_role("alertdialog").count() == 1)
            shot(pg, "admin-killswitch-confirm")
            pg.get_by_role("alertdialog").get_by_role("button", name="Tạm dừng").click(); pg.wait_for_timeout(700)
            check("kill switch POSTed on=true", sel["kill"] == [True], str(sel["kill"]))
            check("button flips to re-enable", pg.get_by_role("button", name="Bật lại phiên âm").count() == 1)
            pg.get_by_label("File ID của video").fill("abc.mp4")
            pg.get_by_label("Ngôn ngữ (không bắt buộc)").fill("vi")
            pg.get_by_role("button", name="Chạy thử (tốn phí)").click(); pg.wait_for_timeout(700)
            check("selftest ok shows SRT + timings + cost", pg.locator("[data-testid=selftest-ok]").is_visible()
                  and "Xin chào" in pg.inner_text("[data-testid=selftest-ok]") and "4.300 ms" in pg.inner_text("[data-testid=selftest-ok]"))
            check("selftest body carries file_id+language", sel["body"].get("file_id") == "abc.mp4" and sel["body"].get("language") == "vi")
            shot(pg, "admin-selftest-ok")
            sel["mode"] = "err"
            pg.get_by_role("button", name="Chạy thử (tốn phí)").click(); pg.wait_for_timeout(700)
            check("selftest error shows raw_output", "{not json at all" in pg.inner_text("[data-testid=selftest-error]"))
            shot(pg, "admin-selftest-error")
            check("no page error (admin)", not errs, "; ".join(errs)[:80])
            pg.close()

            # 7b. Operator sees data but no controls; Redis down shows dashes.
            pg, errs, _ = new_page(b, base, quota=QUOTA_OK, jobs=[], create=(200, {}),
                                   admin=lambda url, m, req, j: j({**summary, "redis_ok": False, "spend_today_usd": None, "killswitch": None,
                                                                   "minutes_reserved_today": None}) if url.endswith("summary") else j({}))
            pg.add_init_script("""localStorage.setItem('vg_admin_session', JSON.stringify({email:'t@t', role:'operator', sessionToken:'tok',
              expiresAt: new Date(Date.now()+3600e3).toISOString()}))""")
            pg.goto(base + "/vid-admin/asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1800)
            body = pg.inner_text("body")
            check("operator: no kill switch / selftest controls", pg.get_by_role("button", name="Tạm dừng phiên âm").count() == 0
                  and "Cần quyền admin" in body and pg.get_by_label("File ID của video").count() == 0)
            check("redis down: warning + dash instead of fake zero", "Không đọc được Redis" in body and "$0.0000" not in body)
            shot(pg, "admin-operator-redis-down")
            pg.close()

            # 8. Admin on mobile.
            pg, errs, _ = new_page(b, base, quota=QUOTA_OK, jobs=[], create=(200, {}), width=390, admin=admin)
            pg.goto(base + "/vid-admin/asr", wait_until="domcontentloaded"); pg.wait_for_timeout(1800)
            check("390px admin: no horizontal scroll", pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
            shot(pg, "admin-mobile-390")
            pg.close()
            b.close()

    ok = sum(res)
    print(f"\n{ok}/{len(res)} checks passed")
    sys.exit(0 if ok == len(res) else 1)


if __name__ == "__main__":
    main()

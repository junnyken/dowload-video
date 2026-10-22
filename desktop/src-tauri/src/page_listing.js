// Channel listing inside the hidden page window (page_listing.rs).
// Evaluated every 2 s by Rust; keeps its state on window.__vgList and runs
// until it has LIMIT videos, the list stops growing, or MAXMS pass. Only reads
// the page and scrolls it. The answer goes out through document.title in
// chunks ("VGL:<i>/<n>:<part>") because Chromium caps titles at 4096 chars.
// %KIND%, %LIMIT%, %MAXMS% and %SHOW% are replaced by Rust. SHOW = the user
// sees the window (0.16.3 "Mở trang để kiểm tra"): no early verify/login
// answer, they may solve it in the window.
(() => {
  const KIND = "%KIND%";
  const LIMIT = %LIMIT%;
  const MAXMS = %MAXMS%;
  const SHOW = %SHOW%;
  const S = window.__vgList || (window.__vgList = { t0: Date.now(), items: new Map(), last: 0, still: 0, parts: null });
  // Answer ready: send every chunk again on each tick, so a missed title change costs one tick.
  const emit = () => S.parts.forEach((p, i) => setTimeout(() => { document.title = "VGL:" + (i + 1) + "/" + S.parts.length + ":" + p; }, i * 150));
  if (S.parts) return emit();
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return ""; } };
  const clip = (t) => (t || "").replace(/\s+/g, " ").trim().slice(0, 200);
  // 0.18.4: why a Douyin grid stayed empty — the page's own list requests
  // (count, HTTP status, bytes), the WebView2 signs, cookie count. No URLs,
  // no cookie values.
  const douyinDiag = () => {
    let posts = [];
    try { posts = performance.getEntriesByType("resource").filter((e) => /\/aweme\/v1\/web\/aweme\/post\//.test(e.name)); } catch (e) { /* none */ }
    const st = posts.map((e) => e.responseStatus || 0).join("/") || "-";
    const bytes = posts.reduce((n, e) => n + (e.transferSize || 0), 0);
    let brands = "?";
    try { brands = ((navigator.userAgentData && navigator.userAgentData.brands) || []).some((b) => /webview/i.test(b.brand)) ? "wv" : "edge"; } catch (e) { /* none */ }
    const wv = !!(window.chrome && window.chrome.webview);
    const ck = (document.cookie || "").split(";").filter((c) => c.trim()).length;
    return "post=" + posts.length + " st=" + st + " b=" + bytes + " ua=" + brands + " cw=" + (wv ? 1 : 0) + " ck=" + ck + " login=" + (document.querySelector('[data-e2e="login-button"], #login-pannel') ? 1 : 0);
  };
  const send = (obj) => {
    const text = JSON.stringify(obj);
    S.parts = [];
    for (let i = 0; i < text.length; i += 3000) S.parts.push(text.slice(i, i + 3000));
    emit();
  };
  const elapsed = Date.now() - S.t0;
  let meta = S.meta || {};

  if (KIND === "tiktok") {
    try {
      const el = document.getElementById("__UNIVERSAL_DATA_FOR_REHYDRATION__");
      if (el && !meta.id) {
        const d = JSON.parse(el.textContent).__DEFAULT_SCOPE__["webapp.user-detail"];
        const u = d && d.userInfo && d.userInfo.user;
        if (u && u.secUid) meta = S.meta = { id: u.secUid, title: u.nickname || u.uniqueId || "", uploader: u.uniqueId || "", thumbnail: u.avatarLarger || u.avatarMedium || "", status: d.statusCode || 0 };
        else if (d && d.statusCode) S.status = d.statusCode;
      }
    } catch (e) { /* page not ready */ }
    document.querySelectorAll('a[href*="/video/"]').forEach((a) => {
      const href = abs(a.getAttribute("href"));
      const m = href.match(/^https:\/\/www\.tiktok\.com\/@[^/?#]+\/video\/(\d{6,25})/);
      if (!m || S.items.has(m[1])) return;
      const img = a.querySelector("img");
      S.items.set(m[1], { id: m[1], url: m[0], title: clip(img && img.alt), thumbnail: (img && img.src) || "" });
    });
    const verify = document.querySelector('[id*="captcha"], [class*="captcha"]');
    if (verify) S.verify = true;
    if (verify && !SHOW && S.items.size === 0 && elapsed > 10000) return send({ meta, error: "verify" });
  } else if (KIND === "facebook") {
    // 0.19.1: signed out, Facebook shows ~10 reels then a "Xem thêm trên
    // Facebook" sign-in dialog and locks the page's scroll. Closing it and
    // unlocking the scroll lets the grid load on (measured 22/10 on a public
    // profile: 10 → 29 reels, the whole tab). Signed in, there is no dialog.
    const close = document.querySelector('[role="dialog"] [aria-label="Đóng"], [role="dialog"] [aria-label="Close"]');
    if (close) { close.click(); S.unblocked = (S.unblocked || 0) + 1; }
    for (const el of [document.documentElement, document.body]) {
      if (el && getComputedStyle(el).overflowY === "hidden") el.style.setProperty("overflow", "auto", "important");
    }
    document.querySelectorAll('a[href*="/videos/"], a[href*="/reel/"], a[href*="/watch/?v="], a[href*="/watch?v="]').forEach((a) => {
      const href = abs(a.getAttribute("href"));
      const m = href.match(/\/(?:videos|reel)\/(\d{6,25})/) || href.match(/[?&]v=(\d{6,25})/);
      if (!m || S.items.has(m[1])) return;
      const url = /\/reel\//.test(href) ? "https://www.facebook.com/reel/" + m[1] : "https://www.facebook.com/watch/?v=" + m[1];
      const img = a.querySelector("img");
      S.items.set(m[1], { id: m[1], url, title: clip(a.getAttribute("aria-label") || (img && img.alt) || a.textContent), thumbnail: (img && img.src) || "" });
    });
    if (!meta.title) {
      const h = document.querySelector("h1");
      const t = ((h && h.textContent) || document.title.replace(/\s*\|\s*Facebook\s*$/, ""))
        .replace(/\s*(Tài khoản đã xác minh|Verified account)\s*$/i, "");
      if (t && !/^(Facebook|Log in|Đăng nhập)/i.test(t)) meta = S.meta = { title: clip(t) };
    }
    const login = document.querySelector('form#login_form, form[action*="/login"], input[name="pass"]');
    if (login) S.login = true;
    if (login && !SHOW && S.items.size === 0 && elapsed > 10000) return send({ meta, error: "login" });
  } else if (KIND === "douyin") {
    // Same signals as the extension (content.js, 5.9.1): video links in the
    // grid, ".route-scroll-container" scrolls (the body is locked), and
    // Douyin prints "暂时没有更多了" under the last video. Image posts
    // (/note/) are left out, like the server's listing.
    document.querySelectorAll('a[href*="/video/"]').forEach((a) => {
      const href = abs(a.getAttribute("href"));
      const m = href.match(/^https:\/\/www\.douyin\.com\/video\/(\d{15,25})/);
      if (!m || S.items.has(m[1])) return;
      const img = a.querySelector("img");
      const text = clip((a.textContent || "").replace(/(^|\s)[\d.,]+[万w]?(?=\s|$)/g, " "));
      S.items.set(m[1], { id: m[1], url: "https://www.douyin.com/video/" + m[1], title: clip(img && img.alt) || text, thumbnail: (img && img.src) || "" });
    });
    if (!meta.title) {
      const h = document.querySelector("h1");
      const t = h && clip(h.textContent);
      if (t) meta = S.meta = { title: t };
    }
    if (/暂时没有更多了|没有更多了/.test(((document.body && document.body.innerText) || "").slice(-4000))) S.end = true;
    const verify = document.querySelector('#captcha_container, .captcha_verify_container, [id*="captcha"], [class*="captcha"]');
    if (verify) S.verify = true;
    if (verify && !SHOW && S.items.size === 0 && elapsed > 15000) return send({ meta, error: "verify" });
    // 0.18.3: a fresh session can get the header but no grid (owner's test
    // 21/10). Reload once after 20 s (the first visit has set Douyin's
    // cookies by then); still nothing 25 s after that → answer "empty" now
    // instead of waiting the whole MAXMS. A shown window waits: the user
    // may be logging in / solving a check.
    if (!SHOW && S.items.size === 0) {
      let reloaded = false;
      try { reloaded = sessionStorage.getItem("__vgReloaded") === "1"; } catch (e) { /* storage off */ }
      if (!reloaded && elapsed > 20000) {
        try { sessionStorage.setItem("__vgReloaded", "1"); } catch (e) { /* storage off */ }
        location.reload();
        return;
      }
      if (reloaded && elapsed > 25000) return send({ meta, items: [], error: S.verify ? "verify" : "empty", diag: douyinDiag() });
    }
  }

  if (S.items.size === S.last) S.still += 1; else { S.still = 0; S.last = S.items.size; }
  // Douyin fetches each next page slowly: wait 8 still ticks (16 s) unless the end line showed.
  // Facebook loads the next rows of a reels tab in a few seconds: 6 still ticks.
  const stillMax = KIND === "douyin" ? (S.end ? 2 : 8) : KIND === "facebook" ? 6 : 4;
  if (S.items.size >= LIMIT || (S.items.size > 0 && S.still >= stillMax) || elapsed > MAXMS) {
    const why = S.items.size ? null : S.verify ? "verify" : S.login ? "login" : "empty";
    const diag = KIND === "douyin" && !S.items.size ? douyinDiag() : undefined;
    return send({ meta, status: S.status || meta.status || 0, items: Array.from(S.items.values()).slice(0, LIMIT), error: why, diag });
  }
  // 0.19.2: Facebook's reels grid sits above other sections; a blind 2-screen
  // step jumps past its end and the next row never loads (measured signed
  // out: blind 40 reels, last reel to the bottom 50). Same as the extension.
  if (KIND === "facebook") {
    const links = document.querySelectorAll('a[href*="/reel/"], a[href*="/videos/"], a[href*="/watch/?v="]');
    const last = links[links.length - 1];
    if (last) { last.scrollIntoView({ block: "end" }); window.scrollBy(0, window.innerHeight * 0.6); return; }
  }
  const box = KIND === "douyin" && document.querySelector(".route-scroll-container");
  if (box && box.scrollHeight > box.clientHeight + 50) box.scrollBy(0, Math.max(800, box.clientHeight * 2));
  else window.scrollBy(0, Math.max(800, window.innerHeight * 2));
})();

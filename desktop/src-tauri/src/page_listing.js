// Channel listing inside the hidden page window (page_listing.rs).
// Evaluated every 2 s by Rust; keeps its state on window.__vgList and runs
// until it has LIMIT videos, the list stops growing, or 35 s pass. Only reads
// the page and scrolls it. The answer goes out through document.title in
// chunks ("VGL:<i>/<n>:<part>") because Chromium caps titles at 4096 chars.
// %KIND% and %LIMIT% are replaced by Rust.
(() => {
  const KIND = "%KIND%";
  const LIMIT = %LIMIT%;
  const S = window.__vgList || (window.__vgList = { t0: Date.now(), items: new Map(), last: 0, still: 0, parts: null });
  // Answer ready: send every chunk again on each tick, so a missed title change costs one tick.
  const emit = () => S.parts.forEach((p, i) => setTimeout(() => { document.title = "VGL:" + (i + 1) + "/" + S.parts.length + ":" + p; }, i * 150));
  if (S.parts) return emit();
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return ""; } };
  const clip = (t) => (t || "").replace(/\s+/g, " ").trim().slice(0, 200);
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
        if (u && u.secUid) meta = S.meta = { id: u.secUid, title: u.nickname || u.uniqueId || "", uploader: u.uniqueId || "", thumbnail: u.avatarLarger || u.avatarMedium || "" };
        else if (d && d.statusCode && d.statusCode !== 0) return send({ error: "not_found" });
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
    if (verify && S.items.size === 0 && elapsed > 10000) return send({ error: "verify" });
  } else if (KIND === "facebook") {
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
    if (login && S.items.size === 0 && elapsed > 10000) return send({ error: "login" });
  }

  if (S.items.size === S.last) S.still += 1; else { S.still = 0; S.last = S.items.size; }
  if (S.items.size >= LIMIT || (S.items.size > 0 && S.still >= 4) || elapsed > 35000) {
    return send({ meta, items: Array.from(S.items.values()).slice(0, LIMIT), error: S.items.size ? null : "empty" });
  }
  window.scrollBy(0, Math.max(800, window.innerHeight * 2));
})();

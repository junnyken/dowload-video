// Runs before Douyin's own scripts in the Douyin page window (page_window.rs,
// 0.18.4). The owner's guest Chrome scrolls a whole channel (174 videos,
// 21/10) while the same profile in WebView2 shows the header and an empty
// grid, with or without InPrivate: Douyin tells an embedded browser apart.
// This hides the two signs a page script can read; the page still gets no
// IPC (the window has no capability).
//  - window.chrome.webview: only WebView2 has it;
//  - navigator.userAgentData brands "Microsoft Edge WebView2" → "Microsoft Edge"
//    (also in getHighEntropyValues).
(() => {
  try {
    if (window.chrome && "webview" in window.chrome) {
      Object.defineProperty(window.chrome, "webview", { get: () => undefined, configurable: true, enumerable: false });
    }
  } catch (e) { /* not configurable: leave it */ }
  try {
    const d = navigator.userAgentData;
    const isWv = (b) => /webview/i.test((b && b.brand) || "");
    if (d && Array.isArray(d.brands) && d.brands.some(isWv)) {
      const clean = (list) => {
        const out = [];
        for (const b of list || []) {
          const nb = isWv(b) ? { brand: "Microsoft Edge", version: b.version } : b;
          if (!out.some((x) => x.brand === nb.brand)) out.push(nb);
        }
        return out;
      };
      const brands = Object.freeze(clean(d.brands));
      const proto = Object.getPrototypeOf(d);
      const high = proto.getHighEntropyValues;
      Object.defineProperty(proto, "brands", { get: () => brands, configurable: true });
      proto.getHighEntropyValues = function (hints) {
        return high.call(this, hints).then((v) => Object.assign({}, v, {
          brands: clean(v.brands),
          fullVersionList: v.fullVersionList ? clean(v.fullVersionList) : v.fullVersionList,
        }));
      };
      const json = proto.toJSON;
      if (json) proto.toJSON = function () { return Object.assign({}, json.call(this), { brands }); };
    }
  } catch (e) { /* leave the brands as they are */ }
})();

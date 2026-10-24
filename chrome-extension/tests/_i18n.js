/**
 * Test helper: a chrome.i18n.getMessage() backed by _locales/<locale>/messages.json,
 * the same substitution rules as Chrome ($name$ -> placeholders[name].content -> $1..$9, $$ -> $).
 * Tests that run slices of popup.js / background.js / content.js in a vm
 * context hand them `chrome.i18n` + the same one-line `t` the sources define.
 */
const fs = require('fs');
const path = require('path');

function chromeI18n(locale = 'vi') {
  const file = path.join(__dirname, '..', '_locales', locale, 'messages.json');
  const msgs = JSON.parse(fs.readFileSync(file, 'utf8'));
  return {
    getUILanguage: () => locale,
    getMessage(key, subs) {
      const m = msgs[key];
      if (!m) return '';
      const list = subs == null ? [] : Array.isArray(subs) ? subs : [subs];
      const ph = {};
      for (const [name, p] of Object.entries(m.placeholders || {})) {
        const idx = Number(String(p.content).replace('$', '')) - 1;
        ph[name.toLowerCase()] = list[idx] == null ? '' : String(list[idx]);
      }
      return m.message.replace(/\$\$|\$([A-Za-z0-9_@]+)\$/g, (all, name) => (all === '$$' ? '$' : (ph[name.toLowerCase()] ?? all)));
    },
  };
}

/** Source text of the `t` helper the extension files define (for vm slices). */
const T_SOURCE = "const t = (key, ...subs) => { try { return chrome.i18n.getMessage(key, subs.map((s) => String(s))) || key; } catch { return key; } };";

module.exports = { chromeI18n, T_SOURCE };

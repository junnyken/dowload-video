/**
 * What the extension depends on outside its own code (5.22.3, owner 27/10).
 * Run with: node tests/test_web_contract.js
 *
 *  [1] The site's build must not rename `__vg_source`: frontend/vite.config.js mangles every
 *      property matching /^_/, and the live bundle posted {jn:"webapp", ...} — web-bridge.js
 *      ignored it, so a signed-in site never signed the extension in (and the device id never
 *      came back). The name must be in terser's mangle.properties.reserved.
 *  [2] Every notification icon is a real PNG: assets/icon.png is an SVG file, and
 *      chrome.notifications.create failed with "Unable to download all specified images" —
 *      no "done" / "failed" notice was ever shown.
 */
const fs = require('fs');
const path = require('path');

let passed = 0, failed = 0;
function assert(cond, name) { if (cond) { console.log(`  ✓ ${name}`); passed++; } else { console.error(`  ✗ ${name}`); failed++; } }

const root = path.join(__dirname, '..', '..');
const vite = fs.readFileSync(path.join(root, 'frontend', 'vite.config.js'), 'utf8');
const regex = (vite.match(/properties:\s*\{[^}]*regex:\s*\/(.+?)\/[a-z]*,/s) || [])[1];
const reserved = (vite.match(/reserved:\s*\[([^\]]*)\]/) || [])[1] || '';
if (regex && new RegExp(regex).test('__vg_source')) {
  assert(/['"]__vg_source['"]/.test(reserved), '[1] vite.config.js mangles /' + regex + '/ and reserves __vg_source');
} else {
  assert(true, '[1] vite.config.js does not mangle __vg_source');
}
const bridge = fs.readFileSync(path.join(__dirname, '..', 'web-bridge.js'), 'utf8');
assert(bridge.includes("__vg_source !== 'webapp'") && bridge.includes("__vg_source: 'extension'"), '[1] web-bridge.js speaks __vg_source');

const bg = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const icons = [...new Set([...bg.matchAll(/iconUrl:\s*'([^']+)'/g)].map((m) => m[1]))];
assert(icons.length > 0, '[2] background.js creates notifications with an icon');
for (const icon of icons) {
  const head = fs.readFileSync(path.join(__dirname, '..', icon)).subarray(0, 8);
  assert(head.equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])), `[2] ${icon} is a PNG`);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

/**
 * Phase 12 — Extension + PWA Tests
 * Run with: node tests/test_phase12_extension.js
 * (No external test runner needed — plain assertions)
 */

const fs = require('fs');
const path = require('path');

let passed = 0;
let failed = 0;

function assert(condition, name) {
  if (condition) {
    console.log(`  ✓ ${name}`);
    passed++;
  } else {
    console.error(`  ✗ ${name}`);
    failed++;
  }
}

// ── 1. Manifest validation ───────────────────────────────────────────
console.log('\n[1] Manifest validation');
const manifestPath = path.join(__dirname, '..', 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

assert(manifest.manifest_version === 3, 'manifest_version is 3');
assert(typeof manifest.version === 'string' && /^\d+\.\d+\.\d+$/.test(manifest.version), 'version semver format');
assert(manifest.icons && manifest.icons['16'] && manifest.icons['32'] && manifest.icons['48'] && manifest.icons['128'], 'icons: 16, 32, 48, 128 present');
assert(!manifest.host_permissions.includes('https://dowloadvideo.io.vn/*'), 'legacy domain removed from host_permissions');
assert(manifest.host_permissions.includes('*://*.youtube.com/*'), 'youtube.com still in host_permissions');
assert(manifest.host_permissions.includes('*://*.tiktok.com/*'), 'tiktok.com still in host_permissions');
assert(manifest.permissions.includes('activeTab'), 'activeTab permission present');
assert(manifest.permissions.includes('storage'), 'storage permission present');
assert(manifest.permissions.includes('alarms'), 'alarms permission present (SW keepalive)');
assert(manifest.background && manifest.background.service_worker === 'background.js', 'MV3 service worker set');

// ── 2. Icon files existence ──────────────────────────────────────────
console.log('\n[2] Icon file existence');
const assetsDir = path.join(__dirname, '..', 'assets');
for (const size of ['16', '32', '48', '128']) {
  const iconFile = path.join(assetsDir, `icon${size}.png`);
  assert(fs.existsSync(iconFile), `icon${size}.png exists`);
}

// ── 3. Onboarding in background.js ──────────────────────────────────
console.log('\n[3] Background.js first-install detection');
const bgSrc = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
assert(bgSrc.includes("details.reason === 'install'"), "onInstalled detects 'install' reason");
assert(bgSrc.includes('vg_onboarding_done'), 'vg_onboarding_done set on first install');
assert(bgSrc.includes('vg_install_time'), 'vg_install_time recorded on first install');

// ── 4. Onboarding in popup.js ────────────────────────────────────────
console.log('\n[4] Popup.js onboarding logic');
const popupJs = fs.readFileSync(path.join(__dirname, '..', 'popup.js'), 'utf8');
assert(popupJs.includes('checkOnboarding'), 'checkOnboarding() called in popup.js');
assert(popupJs.includes('showOnboarding'), 'showOnboarding() defined');
assert(popupJs.includes('hideOnboarding'), 'hideOnboarding() defined');
assert(popupJs.includes('vg_onboarding_done'), 'reads vg_onboarding_done from storage');
assert(popupJs.includes('obNext'), 'obNext() for step navigation');
assert(popupJs.includes('obFinish'), 'obFinish() closes overlay + sets done');

// ── 5. Unsupported page state in popup.js ────────────────────────────
console.log('\n[5] Unsupported page state');
assert(popupJs.includes('showUnsupportedPage'), 'showUnsupportedPage() defined');
assert(popupJs.includes('unsupported-page-state'), 'references #unsupported-page-state panel');

// ── 6. Onboarding overlay in popup.html ──────────────────────────────
console.log('\n[6] Popup.html onboarding overlay');
const popupHtml = fs.readFileSync(path.join(__dirname, '..', 'popup.html'), 'utf8');
assert(popupHtml.includes('id="onboarding-overlay"'), 'onboarding-overlay element exists');
assert(popupHtml.includes('id="ob-step-1"'), 'step 1 panel exists');
assert(popupHtml.includes('id="ob-step-2"'), 'step 2 panel exists');
assert(popupHtml.includes('id="ob-step-3"'), 'step 3 panel exists');
assert(popupHtml.includes('id="unsupported-page-state"'), 'unsupported-page-state panel exists');
assert(popupHtml.includes('youtube.com'), 'supported platforms listed in unsupported state');
assert(popupHtml.includes('v5.0.0'), 'footer version updated to 5.0.0');

// ── 7. Store submission checklist ────────────────────────────────────
console.log('\n[7] STORE_SUBMISSION.md completeness');
const storeDoc = path.join(__dirname, '..', 'STORE_SUBMISSION.md');
if (fs.existsSync(storeDoc)) {
  const storeSrc = fs.readFileSync(storeDoc, 'utf8');
  assert(storeSrc.length > 500, 'STORE_SUBMISSION.md has substantial content');
} else {
  assert(false, 'STORE_SUBMISSION.md exists');
}

// ── Summary ──────────────────────────────────────────────────────────
console.log(`\n── Summary: ${passed} passed, ${failed} failed ──\n`);
if (failed > 0) process.exit(1);

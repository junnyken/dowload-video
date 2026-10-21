// 0.18.4: src-tauri/src/douyin_init.js hides the WebView2 signs from Douyin's scripts.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../src-tauri/src/douyin_init.js', import.meta.url), 'utf8');

function env() {
  class UAData {
    get brands() { return [{ brand: 'Microsoft Edge WebView2', version: '131' }, { brand: 'Chromium', version: '131' }, { brand: 'Not_A Brand', version: '24' }]; }
    getHighEntropyValues() {
      return Promise.resolve({ platform: 'Windows', fullVersionList: [{ brand: 'Microsoft Edge WebView2', version: '131.0.1' }, { brand: 'Microsoft Edge', version: '131.0.1' }] });
    }
  }
  const window = { chrome: { webview: { postMessage() {} }, runtime: {} } };
  const navigator = { userAgentData: new UAData() };
  new Function('window', 'navigator', SRC)(window, navigator);
  return { window, navigator };
}

test('window.chrome.webview reads undefined, the rest of window.chrome stays', () => {
  const { window } = env();
  assert.equal(window.chrome.webview, undefined);
  assert.ok(window.chrome.runtime);
});

test('brands say Microsoft Edge, never WebView2, no duplicates', async () => {
  const { navigator } = env();
  assert.deepEqual(navigator.userAgentData.brands.map((b: { brand: string }) => b.brand), ['Microsoft Edge', 'Chromium', 'Not_A Brand']);
  const hi = await navigator.userAgentData.getHighEntropyValues(['fullVersionList']);
  assert.deepEqual(hi.fullVersionList.map((b: { brand: string }) => b.brand), ['Microsoft Edge']);
  assert.equal(hi.platform, 'Windows');
});

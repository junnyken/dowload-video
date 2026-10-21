// The page script (src-tauri/src/page_listing.js) on a fake Douyin profile:
// links collected across scrolls, the inner container scrolled, the end line
// stops it, the answer arrives in VGL chunks. No browser: a tiny DOM stub.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../src-tauri/src/page_listing.js', import.meta.url), 'utf8');

type Link = { href: string; alt: string; text: string };

function page(batches: Link[][], opts: { end?: boolean; container?: boolean } = {}) {
  let shown = 1; // batches rendered so far
  const timers: (() => void)[] = [];
  const box = {
    scrollHeight: 5000, clientHeight: 800, scrolled: 0,
    scrollBy() { this.scrolled++; shown = Math.min(batches.length, shown + 1); },
  };
  const win: Record<string, unknown> = {
    innerHeight: 800,
    scrollBy() { (win as { winScrolled: number }).winScrolled++; shown = Math.min(batches.length, shown + 1); },
    winScrolled: 0,
  };
  const doc = {
    title: '',
    body: { get innerText() { return opts.end && shown === batches.length ? '… 暂时没有更多了' : '…'; } },
    getElementById: () => null,
    querySelector(sel: string) {
      if (sel === '.route-scroll-container') return opts.container === false ? null : box;
      if (sel === 'h1') return { textContent: '  凌晨睡不着 ' };
      return null;
    },
    querySelectorAll(sel: string) {
      if (!sel.includes('/video/')) return [];
      return batches.slice(0, shown).flat().map((l) => ({
        getAttribute: () => l.href, textContent: l.text,
        querySelector: () => (l.alt ? { alt: l.alt, src: 'https://p3.douyinpic.com/c.jpg' } : null),
      }));
    },
  };
  const titles: string[] = [];
  const run = (limit: number, maxMs = 150000) => {
    const code = SRC.replaceAll('%KIND%', 'douyin').replaceAll('%LIMIT%', String(limit))
      .replaceAll('%MAXMS%', String(maxMs)).replaceAll('%SHOW%', 'false');
    new Function('window', 'document', 'location', 'setTimeout', code)(
      win, doc, { href: 'https://www.douyin.com/user/MS4wX' }, (f: () => void) => timers.push(f));
    while (timers.length) { timers.shift()!(); titles.push(doc.title); }
  };
  return { run, box, win, titles, answer: () => {
    const parts = titles.filter((t) => t.startsWith('VGL:'));
    if (!parts.length) return null;
    const n = Number(/^VGL:\d+\/(\d+):/.exec(parts[0])![1]);
    const byI = new Map(parts.map((t) => { const m = /^VGL:(\d+)\/\d+:(.*)$/s.exec(t)!; return [Number(m[1]), m[2]]; }));
    return byI.size === n ? JSON.parse([...Array(n)].map((_, i) => byI.get(i + 1)).join('')) : null;
  } };
}

const link = (id: string, alt = `clip ${id}`): Link => ({ href: `//www.douyin.com/video/${id}`, alt, text: '1.2万' });
const ID = (i: number) => String(7600000000000000000n + BigInt(i));

test('collects every scroll page, scrolls the inner container, stops at the end line', () => {
  const batches = [0, 1, 2].map((b) => Array.from({ length: 18 }, (_, i) => link(ID(b * 18 + i))));
  const p = page(batches, { end: true });
  for (let tick = 0; tick < 20 && !p.answer(); tick++) p.run(500);
  const a = p.answer();
  assert.ok(a, 'an answer was sent');
  assert.equal(a.items.length, 54);
  assert.equal(a.items[0].url, `https://www.douyin.com/video/${ID(0)}`);
  assert.equal(a.items[0].title, `clip ${ID(0)}`);
  assert.equal(a.meta.title, '凌晨睡不着');
  assert.equal(a.error, null);
  assert.ok(p.box.scrolled >= 2);
  assert.equal(p.win.winScrolled, 0);
});

test('stops at LIMIT', () => {
  const p = page([Array.from({ length: 30 }, (_, i) => link(ID(i)))]);
  p.run(10);
  assert.equal(p.answer().items.length, 10);
});

test('image posts and other hosts are left out; title falls back to the text without counts', () => {
  const p = page([[
    { href: '/note/7600000000000000999', alt: 'a slide post', text: '' },
    { href: 'https://evil.example/video/7600000000000000998', alt: 'x', text: '' },
    { href: `/video/${ID(1)}`, alt: '', text: '  深夜 故事 3.4万 ' },
  ]], { end: true });
  for (let tick = 0; tick < 6 && !p.answer(); tick++) p.run(500);
  assert.deepEqual(p.answer().items.map((i: { id: string; title: string }) => [i.id, i.title]), [[ID(1), '深夜 故事']]);
});

test('no container: the window scrolls instead', () => {
  const p = page([[link(ID(1))], [link(ID(2))]], { container: false });
  p.run(500);
  assert.equal(p.win.winScrolled, 1);
});

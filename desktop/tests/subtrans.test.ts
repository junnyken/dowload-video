import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickSource, translateSubtitle, SubtransError, type SubtransDeps } from '../src/lib/subtrans-core.ts';

test('source subtitle: Vietnamese already there, English first, else the first one', () => {
  assert.equal(pickSource(['en', 'vi']), 'has_vi');
  assert.equal(pickSource(['vi-VN']), 'has_vi');
  assert.equal(pickSource(['ja', 'en-US']), 'en-US');
  assert.equal(pickSource(['ja', 'ko']), 'ja');
  assert.equal(pickSource(['video']), 'video'); // "vi" prefix is not Vietnamese
  assert.equal(pickSource([]), null);
});

function deps(over: Partial<SubtransDeps> & { jobs?: unknown[][] } = {}) {
  const calls: { path: string; init: RequestInit }[] = [];
  const written: [string, string, string][] = [];
  const polls = over.jobs ?? [[{ id: 'j1', status: 'processing', progress_pct: 40 }], [{ id: 'j1', status: 'done' }]];
  let poll = 0;
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
  const d: SubtransDeps = {
    list: async () => ['en'],
    read: async () => '1\n00:00:01,000 --> 00:00:02,000\nHello\n',
    write: async (f, l, c) => { written.push([f, l, c]); return 'C:\\v\\a.vi.srt'; },
    token: async () => 'tok',
    sleep: async () => {},
    fetch: async (path, init) => {
      calls.push({ path, init });
      if (path.endsWith('/upload')) return json(200, { jobs: [{ id: 'j1' }], rejected: [] });
      if (path.endsWith('/jobs')) return json(200, { jobs: polls[Math.min(poll++, polls.length - 1)] });
      if (path.endsWith('/download')) return new Response('1\n00:00:01,000 --> 00:00:02,000\nXin chào\n');
      return json(404, {});
    },
    ...over,
  };
  return { d, calls, written };
}

test('uploads the English srt, waits, saves the translation as .vi.srt', async () => {
  const progress: number[] = [];
  const { d, calls, written } = deps({ onProgress: (p) => progress.push(p) });
  assert.equal(await translateSubtitle('C:\\v\\a.mp4', d), 'C:\\v\\a.vi.srt');
  const up = calls[0];
  assert.equal(up.path, '/api/v1/transcript-translate/upload');
  assert.equal((up.init.headers as Record<string, string>).Authorization, 'Bearer tok');
  const form = up.init.body as FormData;
  assert.equal(form.get('target_lang'), 'vi');
  assert.equal((form.get('files') as File).name, 'a.en.srt');
  assert.deepEqual(progress, [40]);
  assert.deepEqual(written, [['C:\\v\\a.mp4', 'vi', '1\n00:00:01,000 --> 00:00:02,000\nXin chào\n']]);
});

async function code(p: Promise<unknown>) {
  try { await p; return 'ok'; } catch (e) { assert.ok(e instanceof SubtransError, String(e)); return e.code; }
}

test('refusals say why and never write', async () => {
  assert.equal(await code(translateSubtitle('a.mp4', deps({ token: async () => null }).d)), 'login');
  assert.equal(await code(translateSubtitle('a.mp4', deps({ list: async () => [] }).d)), 'no_subtitle');
  assert.equal(await code(translateSubtitle('a.mp4', deps({ list: async () => ['vi'] }).d)), 'has_vi');
  const quota = deps({ fetch: async () => new Response(JSON.stringify({ jobs: [], rejected: [{ reason: 'Vượt quá hạn mức' }] }), { status: 200 }) });
  try { await translateSubtitle('a.mp4', quota.d); assert.fail(); } catch (e) { assert.equal((e as Error).message, 'Vượt quá hạn mức'); }
  const failed = deps({ jobs: [[{ id: 'j1', status: 'failed', error: 'AI lỗi' }]] });
  assert.equal(await code(translateSubtitle('a.mp4', failed.d)), 'failed');
  assert.equal(failed.written.length, 0);
  const stuck = deps({ jobs: [[{ id: 'j1', status: 'processing' }]] });
  assert.equal(await code(translateSubtitle('a.mp4', stuck.d)), 'timeout');
  const exists = deps({ write: async () => { throw { message: 'subtitle file already exists' }; } });
  assert.equal(await code(translateSubtitle('a.mp4', exists.d)), 'exists');
});

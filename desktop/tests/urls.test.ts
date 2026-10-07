// Run: npm test   (node's built-in runner, no dependencies)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  douyinVideoId, isChannelOnDownloadScreen, isDouyinChannelUrl, isDouyinShortUrl, isDouyinUrl, isDouyinVideoUrl, looksLikeChannelUrl,
} from '../src/lib/urls.ts';

test('Douyin profile links are channel links', () => {
  for (const u of [
    'https://www.douyin.com/user/MS4wLjABAAAAabc-_123',
    'https://douyin.com/user/MS4wLjABAAAAabc?from_tab_name=main',
    'https://www.iesdouyin.com/share/user/MS4wLjABAAAAabc?sec_uid=MS4wLjABAAAAabc',
    'https://v.douyin.com/iAbCdEf/',
    'https://v.douyin.com/iAbCdEf',
  ]) {
    assert.equal(looksLikeChannelUrl(u), true, u);
    assert.equal(isDouyinChannelUrl(u), true, u);
    assert.equal(isDouyinUrl(u), true, u);
  }
});

test('Douyin video links are not channel links', () => {
  for (const u of ['https://www.douyin.com/video/7311111111111111111', 'https://www.iesdouyin.com/share/video/7311111111111111111/', 'https://www.douyin.com/']) {
    assert.equal(looksLikeChannelUrl(u), false, u);
    assert.equal(isDouyinVideoUrl(u), true, u);
  }
  assert.equal(douyinVideoId('https://www.douyin.com/video/7311111111111111111'), '7311111111111111111');
  assert.equal(douyinVideoId('https://www.douyin.com/user/abc'), null);
  assert.equal(douyinVideoId('https://www.douyin.com/jingxuan?modal_id=7689009727547895282'), '7689009727547895282');
  assert.equal(isDouyinVideoUrl('https://www.douyin.com/jingxuan?modal_id=7689009727547895282'), true);
});

test('short links: channel on Channels screen, video on Download screen', () => {
  const u = 'https://v.douyin.com/iAbCdEf/';
  assert.equal(isDouyinShortUrl(u), true);
  assert.equal(looksLikeChannelUrl(u), true);
  assert.equal(isChannelOnDownloadScreen(u), false);
  assert.equal(isDouyinVideoUrl(u), true);
  assert.equal(isChannelOnDownloadScreen('https://www.douyin.com/user/abc'), true);
  assert.equal(isChannelOnDownloadScreen('https://www.youtube.com/@x'), true);
});

test('lookalike hosts and other schemes are rejected', () => {
  for (const u of ['https://evildouyin.com/user/abc', 'https://douyin.com.evil.io/user/abc', 'https://v.douyin.com.evil.io/abc', 'ftp://www.douyin.com/user/abc', 'not a url', 'https://tiktokv.com/user/abc']) {
    assert.equal(isDouyinUrl(u), false, u);
    assert.equal(looksLikeChannelUrl(u), false, u);
  }
});

test('existing YouTube / TikTok rules are unchanged', () => {
  assert.equal(looksLikeChannelUrl('https://www.youtube.com/@name'), true);
  assert.equal(looksLikeChannelUrl('https://www.youtube.com/channel/UC123'), true);
  assert.equal(looksLikeChannelUrl('https://www.youtube.com/playlist?list=PL1'), true);
  assert.equal(looksLikeChannelUrl('https://www.youtube.com/watch?v=abc'), false);
  assert.equal(looksLikeChannelUrl('https://www.tiktok.com/@user'), true);
  assert.equal(looksLikeChannelUrl('https://www.tiktok.com/@user/video/1'), false);
});

test('share text: words around the link are not reported as invalid lines', async () => {
  const { parseUrls } = await import('../src/lib/urls.ts');
  const text = '7.97 V@L.Jv 06/03 reO:/ :2pm 《隐秘的力量》 https://v.douyin.com/hrADJ8V82a0/ 复制此链接，打开Dou音搜索，直接观看视频！';
  const r = parseUrls(text);
  assert.deepEqual(r.valid, ['https://v.douyin.com/hrADJ8V82a0/']);
  assert.deepEqual(r.invalid, []);
  assert.deepEqual(parseUrls('hello world').invalid, ['hello', 'world']);
  assert.deepEqual(parseUrls('https://a.com/x htps://bad').invalid, []);
  assert.deepEqual(parseUrls('https://a.com/x https:/bad').invalid, ['https:/bad']);
});

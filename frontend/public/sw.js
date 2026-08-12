/**
 * VidGrab Service Worker — PWA Offline Support
 * ==============================================
 * Strategy: Cache-First for static assets, Network-First for navigation.
 * Bump CACHE_NAME version on every deploy to purge stale caches.
 */

const CACHE_NAME = 'vidgrab-v3';
const HISTORY_CACHE_NAME = 'vidgrab-history-v1';
const HISTORY_TTL_MS = 5 * 60 * 1000;
const HISTORY_URLS = ['/api/v1/history', '/api/v1/user/history', '/api/v1/mobile/recent-jobs'];

const STATIC_ASSETS = [
  '/',
  '/offline.html',
  '/manifest.json',
  '/icons/icon-72.svg',
  '/icons/icon-96.svg',
  '/icons/icon-128.svg',
  '/icons/icon-144.svg',
  '/icons/icon-152.svg',
  '/icons/icon-192.svg',
  '/icons/icon-256.svg',
  '/icons/icon-384.svg',
  '/icons/icon-512.svg',
  '/icons/icon-maskable-192.svg',
  '/icons/icon-maskable-512.svg',
];

// ── Install: Pre-cache static shell ─────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Pre-caching static assets');
      // addAll fails atomically — if any asset 404s the install fails
      return cache.addAll(STATIC_ASSETS);
    })
  );
  // skipWaiting is deferred — triggered only via the SKIP_WAITING message below.
  // This keeps the update banner visible until the user explicitly clicks "Cập nhật ngay".
});

// ── Activate: Clean up old-version caches ───────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME && name !== HISTORY_CACHE_NAME)
          .map((name) => {
            console.log(`[SW] Deleting old cache: ${name}`);
            return caches.delete(name);
          })
      );
    })
  );
  // Take control of all open clients immediately after activation
  self.clients.claim();
});

// ── Message: Controlled skip-waiting ────────────────────────────────
// App.jsx sends { type: 'SKIP_WAITING' } when the user clicks "Cập nhật ngay".
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    console.log('[SW] SKIP_WAITING received — activating new SW');
    self.skipWaiting();
  }
});

// ── Fetch: Smart caching strategy ───────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') return;

  // Skip Chrome extension requests
  if (url.protocol === 'chrome-extension:') return;

  // History API: stale-while-revalidate with TTL
  const isHistoryUrl = HISTORY_URLS.some((h) => url.pathname.startsWith(h));
  if (isHistoryUrl) {
    event.respondWith(
      caches.open(HISTORY_CACHE_NAME).then(async (cache) => {
        const cachedResponse = await cache.match(request);
        const now = Date.now();

        if (cachedResponse) {
          const timestamp = parseInt(cachedResponse.headers.get('X-Cache-Timestamp') || '0', 10);
          const isFresh = now - timestamp < HISTORY_TTL_MS;

          if (isFresh) {
            // Serve from cache and update in background (stale-while-revalidate)
            event.waitUntil(
              fetch(request).then((networkResponse) => {
                if (networkResponse.ok) {
                  const headers = new Headers(networkResponse.headers);
                  headers.set('X-Cache-Timestamp', String(Date.now()));
                  return networkResponse.clone().arrayBuffer().then((body) => {
                    cache.put(request, new Response(body, { status: networkResponse.status, statusText: networkResponse.statusText, headers }));
                  });
                }
              }).catch(() => {})
            );
            return cachedResponse;
          }
        }

        // No fresh cache — try network
        try {
          const networkResponse = await fetch(request);
          if (networkResponse.ok) {
            const headers = new Headers(networkResponse.headers);
            headers.set('X-Cache-Timestamp', String(Date.now()));
            const body = await networkResponse.clone().arrayBuffer();
            await cache.put(request, new Response(body, { status: networkResponse.status, statusText: networkResponse.statusText, headers }));
          }
          return networkResponse;
        } catch (err) {
          // Network failed — return stale cache if available
          if (cachedResponse) {
            const headers = new Headers(cachedResponse.headers);
            headers.set('X-Served-From-Cache', 'true');
            const body = await cachedResponse.clone().arrayBuffer();
            return new Response(body, { status: cachedResponse.status, statusText: cachedResponse.statusText, headers });
          }
          // No cache at all — return offline error
          return new Response(JSON.stringify({ error: 'offline' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          });
        }
      })
    );
    return;
  }

  // Skip API calls — always go to network
  if (url.pathname.startsWith('/api/')) return;

  // Navigation requests: Network-first, fallback to cached shell, then offline page
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        })
        .catch(() => {
          return caches.match(request)
            .then((cached) => cached || caches.match('/'))
            .then((shell) => shell || caches.match('/offline.html'));
        })
    );
    return;
  }

  // Static assets (JS, CSS, images, fonts): Cache-first, fall back to network
  if (
    url.pathname.match(/\.(js|css|svg|png|jpg|jpeg|webp|woff2?|ttf|ico)$/) ||
    url.hostname === 'fonts.googleapis.com' ||
    url.hostname === 'fonts.gstatic.com'
  ) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }
});

// ── Push Notifications ──────────────────────────────────────────────
self.addEventListener('push', (event) => {
  let data = { title: 'VidGrab', body: 'Thông báo mới.', url: '/' };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (e) {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body:    data.body,
      icon:    '/icons/icon-192.svg',
      badge:   '/icons/icon-96.svg',
      tag:     data.tag || 'vidgrab-batch',
      data:    { url: data.url },
      vibrate: [200, 100, 200],
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetPath = event.notification.data?.url || '/';
  const fullUrl = new URL(targetPath, self.location.origin).href;
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // Find any open VidGrab window and navigate it to target
      for (const client of clientList) {
        try {
          if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
            client.focus();
            // Navigate the existing window to the target URL
            if (typeof client.navigate === 'function') return client.navigate(fullUrl);
            // Fallback: postMessage for App.jsx to handle
            client.postMessage({ type: 'SW_NAVIGATE', url: targetPath });
            return;
          }
        } catch (_) {}
      }
      return clients.openWindow(fullUrl);
    })
  );
});

// ── Background Sync ─────────────────────────────────────────────────
self.addEventListener('sync', (event) => {
  if (event.tag === 'retry-failed-jobs') event.waitUntil(Promise.resolve());
});

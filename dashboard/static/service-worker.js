// JARVIS dashboard service worker.
//
// Intentionally minimal: this app is entirely live (WebSocket status,
// real-time voice, chat feed, remote command execution). Caching the
// dashboard HTML or API responses would risk serving a stale PIN/IP
// or stale data, so this worker does NOT cache the app shell or any
// /api /ws requests — it only exists to satisfy the PWA installability
// requirement (Chrome/Android requires an active service worker with
// a fetch handler before it will offer "Install app").
//
// The one thing it does cache is the static, content-addressed assets
// (icons, the CryptoJS bundle) purely as a speed optimization — those
// never change without a filename change.

const STATIC_CACHE = 'jarvis-static-v1';
const STATIC_ASSETS = [
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/crypto.js',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS).catch(() => {}))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never touch API calls, websockets, login, or the dynamic HTML shell —
  // those must always hit the network fresh.
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/ws') ||
    url.pathname === '/' ||
    url.pathname === '/login' ||
    url.pathname === '/auto-login' ||
    url.pathname.startsWith('/uploads/') ||
    url.pathname === '/camera' ||
    url.pathname === '/screen'
  ) {
    return; // let the browser handle it normally
  }

  if (STATIC_ASSETS.some((p) => url.pathname === p)) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});

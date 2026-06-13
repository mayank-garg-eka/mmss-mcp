const CACHE = 'maatri-v1';
const SHELL = ['/dashboard', '/static/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  if (url.pathname.startsWith('/api/')) {
    // Network-first for API — fall back to cache, flag offline
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(async () => {
          const cached = await caches.match(e.request);
          if (cached) {
            const body = await cached.json();
            return new Response(JSON.stringify(body), {
              status: 200,
              headers: { 'Content-Type': 'application/json', 'X-Offline': 'true' },
            });
          }
          return new Response('[]', {
            status: 200,
            headers: { 'Content-Type': 'application/json', 'X-Offline': 'true' },
          });
        })
    );
    return;
  }

  // Cache-first for everything else
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});

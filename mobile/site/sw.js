const CACHE_NAME = "cg-signal-mobile-v26";
const SHELL = ["./", "./index.html", "./styles.css?v=20261011-v26", "./app.js?v=20261011-v26", "./domain.mjs", "./feed-list.mjs", "./sw.js?v=20261011-v26", "./manifest.json", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  event.respondWith(
    fetch(event.request)
      .then(async (response) => {
        if (response.ok) {
          try {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(event.request, response.clone());
          } catch {
            // Keep the successful online response even when storage is unavailable.
          }
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(event.request);
        if (!cached) return Response.error();
        if (!url.pathname.endsWith("/feed.json")) return cached;
        // A cached 200 is not evidence that the online publisher was reached.
        const headers = new Headers(cached.headers);
        headers.set("X-CG-Signal-Offline", "1");
        return new Response(cached.body, { status: cached.status, headers });
      }),
  );
});

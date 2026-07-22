// Service worker - required for the site to qualify as an installable PWA, and now
// also caches static assets (CSS/JS/icons) for fast repeat loads and basic offline
// resilience. Deliberately does NOT cache page navigations, the API, or anything
// outside /static/ - the dashboard/login state must always come straight from the
// network so the app never shows stale inbox data or a stale logged-in/out state.
const CACHE_VERSION = "v2";
const STATIC_CACHE = "camilius-static-" + CACHE_VERSION;

const PRECACHE_URLS = [
    "/static/style.css",
    "/static/htmx.min.js",
    "/static/site-i18n.js",
    "/static/pickers.js",
    "/static/manifest.json",
    "/static/icon-192.png",
    "/static/icon-512.png",
    "/static/icon-512-maskable.png",
];

self.addEventListener("install", (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS).catch(() => {}))
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    const url = new URL(request.url);
    const isStaticAsset = request.method === "GET"
        && url.origin === self.location.origin
        && url.pathname.startsWith("/static/");

    if (!isStaticAsset) {
        event.respondWith(fetch(request));
        return;
    }

    // Stale-while-revalidate: answer instantly from cache when we have it (fast
    // repeat loads, works offline), but always refetch in the background so a new
    // deploy's cache-busted (?v=) files are picked up for the next visit.
    event.respondWith(
        caches.match(request).then((cached) => {
            const network = fetch(request)
                .then((response) => {
                    if (response.ok) {
                        const copy = response.clone();
                        caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
                    }
                    return response;
                })
                .catch(() => cached);
            return cached || network;
        })
    );
});

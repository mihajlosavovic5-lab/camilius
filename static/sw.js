// Minimal service worker - required for the site to qualify as an installable app.
// Deliberately does not cache anything: every request just passes straight through
// to the network, so the app always shows the live dashboard/login state, never a
// stale cached version.
self.addEventListener("install", () => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
    event.respondWith(fetch(event.request));
});

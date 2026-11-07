/* Monosage's service worker, served from /sw.js so its scope is the whole site.
 *
 * Push: shows the notification the server sent (aira/notify/webpush.py), unless a Monosage window is focused
 * (that page shows it itself). A click focuses an open Monosage window and moves it to the notification's page,
 * or opens one.
 *
 * No caching: while the device is online the worker answers nothing, so every page and file comes straight from
 * the server (revalidated by ETag) and a deploy is seen at once. Offline, opening a Monosage page shows a short
 * "You are offline" page instead of the browser's error. A new sw.js (any change to VERSION) removes the caches an
 * older version kept.
 */
"use strict";

const VERSION = "2026-11-07.2";
const OFFLINE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Monosage</title></head>
<body style="font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#0c4932;color:#fff;text-align:center">
<main><p style="font-size:42px;margin:0;color:#d7f066">M</p><h1 style="font-size:22px">You are offline</h1><p>Monosage opens again when this device is back online.</p></main></body></html>`;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(names.filter((name) => name.startsWith("monosage-")).map((name) => caches.delete(name)))).then(() => self.clients.claim()),
  );
});

// Only a Monosage page opened while offline: the root and an app's own address such as /stocks.
function offlinePage(request) {
  const url = new URL(request.url);
  return url.origin === self.location.origin && request.method === "GET" && request.mode === "navigate"
    && /^\/(?:[a-z0-9-]+\/?)?$/.test(url.pathname);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (!offlinePage(request) || self.navigator.onLine !== false) return; // the browser handles it as if there were no worker
  event.respondWith(fetch(request).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = {}; }
  const title = data.title || "Monosage";
  const options = {
    body: data.body || "You have a new notification.",
    tag: data.tag || undefined,
    renotify: Boolean(data.tag),
    icon: "/static/icon-192.png",
    badge: "/static/icon-badge.png",
    data: { url: data.url || "/", id: data.id || "" },
  };
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const focused = windows.find((client) => client.focused && client.visibilityState === "visible");
    if (focused) {
      // The person is looking at Monosage: the page shows its own alert and updates its counts.
      focused.postMessage({ type: "aira-push", data });
      return;
    }
    await self.registration.showNotification(title, options);
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (target.origin !== self.location.origin) return; // only this site's own pages
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
    if (open) {
      await open.focus();
      open.postMessage({ type: "aira-open", url: target.pathname + target.search + target.hash });
      return;
    }
    await self.clients.openWindow(target.href);
  })());
});

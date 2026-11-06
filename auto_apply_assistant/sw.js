/* Monosage's service worker, served from /sw.js so its scope is the whole site.
 *
 * Push: shows the notification the server sent (aira/notify/webpush.py), unless a Monosage window is focused
 * (that page shows it itself). A click focuses an open Monosage window and moves it to the notification's page,
 * or opens one.
 *
 * Caching, carefully: the network always comes first, so after a deploy the browser gets the new files at once
 * (the server revalidates every file by ETag). Only when the network fails does a copy of the page shell from this
 * version's cache answer, so Monosage opens offline and says so. The API is never cached. A new sw.js (any change
 * to VERSION) deletes the caches of older versions when it takes over.
 */
"use strict";

const VERSION = "2026-11-06.1";
const CACHE = `monosage-shell-${VERSION}`;
const SHELL = ["/", "/static/app.css", "/static/app.js", "/static/icon-192.png", "/manifest.webmanifest"];
const OFFLINE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Monosage</title></head>
<body style="font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#0c4932;color:#fff;text-align:center">
<main><p style="font-size:42px;margin:0;color:#d7f066">M</p><h1 style="font-size:22px">You are offline</h1><p>Monosage opens again when this device is back online.</p></main></body></html>`;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => Promise.all(SHELL.map((path) => cache.add(new Request(path, { cache: "reload" })).catch(() => null)))).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(names.filter((name) => name.startsWith("monosage-shell-") && name !== CACHE).map((name) => caches.delete(name)))).then(() => self.clients.claim()),
  );
});

// What this worker answers: the page itself (the root and an app's own address such as /stocks, one shell), its
// static files and the manifest. Everything else (the API, public links, other pages) goes straight to the network.
function kindOf(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || request.method !== "GET") return "";
  if (request.mode === "navigate") return /^\/(?:[a-z0-9-]+\/?)?$/.test(url.pathname) ? "shell" : "";
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") return "file";
  return "";
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const kind = kindOf(request);
  if (!kind) return; // the browser handles it as if there were no service worker
  const key = kind === "shell" ? "/" : request;
  event.respondWith((async () => {
    try {
      const fresh = await fetch(request);
      if (fresh.ok && fresh.type === "basic") {
        const copy = fresh.clone();
        caches.open(CACHE).then((cache) => cache.put(key, copy)).catch(() => null);
      }
      return fresh;
    } catch (error) {
      const kept = await (await caches.open(CACHE)).match(key);
      if (kept) return kept;
      if (kind === "shell") return new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } });
      throw error;
    }
  })());
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

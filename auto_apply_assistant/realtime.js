/* Live updates: one Server-Sent Events stream per browser, shared by its tabs, and polling where there is none.
 *
 * The server publishes short hints (aira/notify/realtime.py): a topic ("chat", "dating", "trivia", "notify"),
 * the person it is for and ids, never content. A page that shows something live says what it listens to and how
 * to refresh itself:
 *
 *   const stop = Aira.realtime.live(["chat"], refresh, 2000, node);   // like Aira.suite.live(refresh, 2000, node)
 *
 * While the stream is up, refresh runs when a hint arrives (and on a slow safety poll); while it is down (the
 * hosted build, a browser without EventSource, a refused or dropped stream) it polls every 2000 ms as before.
 * One tab holds the stream (a Web Lock) and passes hints to the others (BroadcastChannel), so ten open tabs use
 * one connection; when that tab closes another takes over and resumes from the last event id.
 */
"use strict";

(() => {
  const Aira = window.Aira;
  const RT = (Aira.realtime = { mode: "off", connected: false });
  const listeners = new Set(); // { topics: Set | null, fn }
  const watchers = new Set(); // called when the mode changes
  const STREAM = "/api/notify/stream";
  const GRACE_MS = 6000; // a reconnecting stream gets this long before pages fall back to polling
  let source = null;
  let channel = null;
  let started = false;
  let failures = 0;
  let graceTimer = 0;
  let retryTimer = 0;

  const me = () => (Aira.life && Aira.life.me) || null;
  // A household shares the account's stream: keep the hints for the person using this tab (or for everyone).
  function mine(event) {
    if (!event.person) return true;
    const person = me();
    if (!person) return true;
    if (event.person === `p:${person.id}`) return true;
    return event.person === "owner" && Boolean(person.owner);
  }

  function deliver(event) {
    if (!event || !mine(event)) return;
    for (const listener of [...listeners]) {
      if (event.topic === "resync" || !listener.topics || listener.topics.has(event.topic)) {
        try { listener.fn(event); } catch (error) { console.error(error); }
      }
    }
  }

  function setMode(mode) {
    if (RT.mode === mode) return;
    RT.mode = mode;
    RT.connected = mode === "sse";
    for (const watch of [...watchers]) { try { watch(mode); } catch (error) { console.error(error); } }
    if (channel && source) channel.postMessage({ type: "mode", mode });
  }

  function available() {
    return "EventSource" in window && !Aira.cloud && !RT.disabled;
  }

  // A stream never ends, so anything that waits for the network to settle (a browser's "network idle", the UI
  // tests) would wait forever if it opened with the page. It opens once the page has loaded and its own requests
  // have been quiet for RT.quietMs; until then pages poll as they always did.
  let inflight = 0;
  RT.quietMs = 1000;
  if (typeof window.fetch === "function") {
    const realFetch = window.fetch.bind(window);
    window.fetch = (...args) => {
      inflight += 1;
      let reply;
      try { reply = realFetch(...args); } catch (error) { inflight -= 1; throw error; }
      return reply.finally(() => { inflight -= 1; });
    };
  }
  function whenQuiet(fn) {
    let since = 0;
    const check = () => {
      const now = Date.now();
      if (document.readyState === "complete" && inflight === 0) {
        if (!since) since = now;
        if (now - since >= RT.quietMs) { fn(); return; }
      } else since = 0;
      setTimeout(check, 200);
    };
    check();
  }

  function open() {
    if (source || !available()) return;
    const last = Aira.recall("rtLast", "");
    source = new EventSource(`${STREAM}${last ? `?last=${encodeURIComponent(last)}` : ""}`, { withCredentials: true });
    source.onopen = () => { failures = 0; clearTimeout(graceTimer); setMode("sse"); };
    source.onmessage = (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { return; }
      if (message.lastEventId) Aira.remember("rtLast", message.lastEventId);
      deliver(event);
      if (channel) channel.postMessage({ type: "event", event });
    };
    source.onerror = () => {
      if (source && source.readyState === EventSource.CLOSED) {
        // Refused (signed out, too many streams, live updates off here): poll, and try again later.
        source.close();
        source = null;
        setMode("poll");
        failures += 1;
        clearTimeout(retryTimer);
        retryTimer = setTimeout(open, Math.min(300000, 15000 * 2 ** Math.min(failures, 5)));
        return;
      }
      // The stream dropped (a restart, the server's periodic end): the browser reconnects with Last-Event-ID.
      clearTimeout(graceTimer);
      graceTimer = setTimeout(() => { if (!source || source.readyState !== EventSource.OPEN) setMode("poll"); }, GRACE_MS);
    };
  }

  function start() {
    if (started) return;
    started = true;
    if (!available()) { setMode("poll"); return; }
    setMode("poll"); // until the stream is up
    if ("BroadcastChannel" in window) {
      channel = new BroadcastChannel("aira-live");
      channel.onmessage = (message) => {
        const data = message.data || {};
        if (source) { // the tab with the stream answers a new tab's question and ignores the rest
          if (data.type === "ask") channel.postMessage({ type: "mode", mode: RT.mode });
          return;
        }
        if (data.type === "event") deliver(data.event);
        else if (data.type === "mode") setMode(data.mode);
      };
    }
    if (channel && navigator.locks && navigator.locks.request) {
      // Held for as long as this tab lives: the next tab in line opens the stream when it closes.
      navigator.locks.request("aira-live-stream", () => new Promise(() => { whenQuiet(open); if (channel) channel.postMessage({ type: "mode", mode: RT.mode }); }))
        .catch(() => whenQuiet(open));
      // A tab that is not holding the stream hears the holder's mode from its next message.
      channel.postMessage({ type: "ask" });
    } else {
      whenQuiet(open);
    }
  }
  RT.start = start;

  // fn(event) for hints on these topics (null: every topic); returns a function that stops listening.
  RT.on = (topics, fn) => {
    start();
    const listener = { topics: topics && topics.length ? new Set(topics) : null, fn };
    listeners.add(listener);
    return () => listeners.delete(listener);
  };
  RT.onMode = (fn) => { watchers.add(fn); return () => watchers.delete(fn); };

  // Run fn now and then on hints, at liveMs while the stream is up and at fallbackMs while it is not. Not tied to
  // a page (the life pulse). Returns a handle with stop().
  RT.every = (fn, { topics = null, fallbackMs = 20000, liveMs = 60000, delayMs = 400 } = {}) => {
    let timer = 0;
    let soon = 0;
    let stopped = false;
    const run = () => { if (!stopped) { try { fn(); } catch (error) { console.error(error); } } };
    const schedule = () => { clearInterval(timer); timer = setInterval(run, RT.connected ? liveMs : fallbackMs); };
    const off = RT.on(topics, () => { clearTimeout(soon); soon = setTimeout(run, delayMs); });
    const offMode = RT.onMode(() => schedule());
    schedule();
    return { stop() { stopped = true; clearInterval(timer); clearTimeout(soon); off(); offMode(); } };
  };

  // A page's live data, like Aira.suite.live(fn, fallbackMs, node): stops when the page is drawn again or node
  // leaves it, skips while the tab is hidden and runs once when it shows again. With the stream up it runs on the
  // page's topics and every safetyMs (default 30 s) in case a hint was missed.
  RT.live = (topics, fn, fallbackMs = 5000, node = null, { safetyMs = 30000 } = {}) => {
    const here = location.hash.split("?")[0];
    const draw = Aira.router ? Aira.router.renders : 0;
    let timer = 0;
    let stopped = false;
    let running = false;
    let again = false;
    const gone = () => location.hash.split("?")[0] !== here || (Aira.router && Aira.router.renders !== draw) || (node && !node.isConnected);
    const run = async () => {
      if (stopped) return;
      if (gone()) { stop(); return; }
      if (document.hidden) return;
      if (running) { again = true; return; }
      running = true;
      try { await fn(); } catch { /* the next round tries again */ }
      running = false;
      if (again) { again = false; run(); }
    };
    const schedule = () => { clearInterval(timer); timer = setInterval(run, Math.max(1500, RT.connected ? safetyMs : fallbackMs)); };
    const off = RT.on(topics, () => setTimeout(run, 60));
    const offMode = RT.onMode(() => schedule());
    const visible = () => { if (!document.hidden) run(); };
    document.addEventListener("visibilitychange", visible);
    function stop() {
      stopped = true;
      clearInterval(timer);
      off();
      offMode();
      document.removeEventListener("visibilitychange", visible);
    }
    schedule();
    return stop;
  };

  // The stream starts with the signed-in app (the first page drawn after sign-in).
  (Aira.afterRoute = Aira.afterRoute || []).push(() => { if (Aira.state.user) start(); });
})();

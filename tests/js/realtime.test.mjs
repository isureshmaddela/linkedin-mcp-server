// Logic tests for auto_apply_assistant/realtime.js (plain node, no dependencies): the live stream and its fallback.
import { readFileSync } from "node:fs";
import vm from "node:vm";

let passed = 0;
let failed = 0;
const check = (name, condition) => {
  if (condition) passed += 1;
  else { failed += 1; console.log(`FAIL ${name}`); }
};

const SOURCE = readFileSync(new URL("../../auto_apply_assistant/realtime.js", import.meta.url), "utf8");

function world({ cloud = false, eventSource = true, loading = false } = {}) {
  const timers = [];
  let nextId = 1;
  const store = new Map();
  const sources = [];
  class FakeEventSource {
    constructor(url) { this.url = url; this.readyState = 0; sources.push(this); }
    close() { this.readyState = 2; }
  }
  FakeEventSource.CONNECTING = 0; FakeEventSource.OPEN = 1; FakeEventSource.CLOSED = 2;
  const Aira = {
    state: { user: { email: "a@b.c" } },
    router: { renders: 1 },
    life: { me: { id: "me1", owner: true } },
    cloud: cloud ? {} : undefined,
    remember: (key, value) => store.set(key, value),
    recall: (key, fallback) => (store.has(key) ? store.get(key) : fallback),
  };
  const listeners = {};
  const window = {
    Aira,
    ...(eventSource ? { EventSource: FakeEventSource } : {}),
  };
  const context = {
    window,
    Aira,
    EventSource: eventSource ? FakeEventSource : undefined,
    navigator: {},
    location: { hash: "#/social?tab=chats" },
    document: { hidden: false, readyState: loading ? "loading" : "complete", addEventListener: (n, f) => { listeners[n] = f; }, removeEventListener: () => {} },
    Date,
    console,
    setInterval: (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms, every: true }); return id; },
    clearInterval: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    setTimeout: (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms, every: false }); return id; },
    clearTimeout: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    URLSearchParams,
    JSON,
    encodeURIComponent,
  };
  if (!eventSource) delete context.EventSource;
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  Aira.realtime.quietMs = 0; // the page has settled: the stream may open at once
  const runTimeouts = async () => {
    for (const timer of [...timers].filter((t) => !t.every)) { context.clearTimeout(timer.id); await timer.fn(); }
  };
  const intervals = () => timers.filter((t) => t.every).map((t) => t.ms);
  return { Aira, context, sources, timers, intervals, runTimeouts, store, start: () => Aira.afterRoute.forEach((hook) => hook()) };
}

const message = (event, id = "") => ({ data: JSON.stringify(event), lastEventId: id });

// 1. The stream: hints for this page's topic and this person run the refresh; the poll slows while it is up.
{
  const w = world();
  w.start();
  const calls = [];
  const stop = w.Aira.realtime.live(["chat"], async () => { calls.push(1); }, 2000);
  check("one stream opened", w.sources.length === 1 && w.sources[0].url === "/api/notify/stream");
  check("polls at the fallback until the stream is up", w.intervals().includes(2000));
  w.sources[0].readyState = 1;
  w.sources[0].onopen();
  check("mode is sse once open", w.Aira.realtime.mode === "sse" && w.Aira.realtime.connected);
  check("the poll slows to the safety interval", w.intervals().includes(30000) && !w.intervals().includes(2000));
  w.sources[0].onmessage(message({ topic: "chat", person: "p:me1", chat: "c1" }, "ep-5"));
  await w.runTimeouts();
  check("a hint for this person and topic refreshes", calls.length === 1);
  check("the last event id is kept for the next tab", w.store.get("rtLast") === "ep-5");
  w.sources[0].onmessage(message({ topic: "dating", person: "p:me1" }));
  w.sources[0].onmessage(message({ topic: "chat", person: "p:someone-else" }));
  await w.runTimeouts();
  check("other topics and other people are ignored", calls.length === 1);
  w.sources[0].onmessage(message({ topic: "chat", person: "owner" }));
  w.sources[0].onmessage(message({ topic: "resync", person: "" }));
  await w.runTimeouts();
  check("the owner's hints and a resync refresh", calls.length === 3);
  // a refused stream (closed by the browser): back to polling, and a later retry resumes from the last id
  w.sources[0].readyState = 2;
  w.sources[0].onerror();
  check("a refused stream falls back to polling", w.Aira.realtime.mode === "poll" && w.intervals().includes(2000));
  const retry = w.timers.find((t) => !t.every && t.ms >= 15000);
  check("it tries again later", Boolean(retry));
  retry.fn();
  check("the new stream resumes from the last id", w.sources.length === 2 && w.sources[1].url === "/api/notify/stream?last=ep-5");
  stop();
  check("stop clears its poll", !w.intervals().includes(2000) || w.intervals().length === 0);
}

// 2. A page that is drawn again stops its own loop.
{
  const w = world();
  w.start();
  const calls = [];
  w.Aira.realtime.live(["trivia"], async () => { calls.push(1); }, 1500, null, { safetyMs: 5000 });
  w.Aira.router.renders = 2; // another page
  const poll = w.timers.find((t) => t.every);
  await poll.fn();
  check("a redrawn page stops polling", calls.length === 0 && !w.timers.some((t) => t.id === poll.id));
}

// 3. The hosted build (and a browser without EventSource) never opens a stream and keeps polling.
for (const options of [{ cloud: true }, { eventSource: false }]) {
  const w = world(options);
  w.start();
  w.Aira.realtime.live(["chat"], async () => {}, 2000);
  check(`no stream ${JSON.stringify(options)}`, w.sources.length === 0 && w.Aira.realtime.mode === "poll");
  check(`polls at the fallback ${JSON.stringify(options)}`, w.intervals().includes(2000));
}

// 4. every(): the life pulse runs on any hint, every minute while live, every 20 s without.
{
  const w = world();
  w.start();
  let beats = 0;
  w.Aira.realtime.every(() => { beats += 1; }, { fallbackMs: 20000, liveMs: 60000, delayMs: 10 });
  check("the pulse polls every 20 s first", w.intervals().includes(20000));
  w.sources[0].readyState = 1;
  w.sources[0].onopen();
  check("and every minute once live", w.intervals().includes(60000) && !w.intervals().includes(20000));
  w.sources[0].onmessage(message({ topic: "notify", person: "p:me1" }));
  await w.runTimeouts();
  check("a hint beats it at once", beats === 1);
}

// 5. The stream waits until the page has loaded (a stream that opened with the page would keep it from settling).
{
  const w = world({ loading: true });
  w.start();
  w.Aira.realtime.live(["chat"], async () => {}, 2000);
  check("no stream while the page loads", w.sources.length === 0 && w.Aira.realtime.mode === "poll");
  w.context.document.readyState = "complete";
  await w.runTimeouts();
  check("the stream opens once the page has settled", w.sources.length === 1);
}

console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exitCode = 1;

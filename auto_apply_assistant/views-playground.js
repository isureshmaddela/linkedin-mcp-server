/* API Playground — the flagship search console (spec 7.2)
 *
 * Aira.renderPlayground(content, route) mounts the Playground into `content`. views-airaapi.js calls it for
 * #/airaapi?tab=playground, so the sub-navigation and page heading belong to that file; this one owns everything
 * below them: the engine picker, the catalog-driven request form, options, live code, the run, the result viewer,
 * the history and saved-search rails, compare, monitor and export.
 *
 * It is built on the shared kit (Aira.serp, views-serp-kit.js) and talks only to /api/airaapi/*. The request lives in
 * the URL hash (Aira.playgroundLink builds one), so any run can be shared, bookmarked or reloaded.
 */
"use strict";
(() => {
  const { h, fmt, ui, toast } = Aira;
  const API = "/api/airaapi";
  const DEFAULT_ENGINE = "google_jobs";
  const PAGE_KEYS = new Set(["tab", "engine", "record", "run"]);
  const OPTION_KEYS = ["no_cache", "async", "zero_trace", "allow_unknown", "output", "json_restrictor"];
  const isMac = /Mac|iPhone|iPad/.test((navigator.platform || "") + (navigator.userAgent || ""));
  const RUN_HINT = isMac ? "⌘ ↵" : "Ctrl ↵";
  const MONITOR_INTERVALS = [[15, "Every 15 minutes"], [30, "Every 30 minutes"], [60, "Every hour"], [180, "Every 3 hours"], [360, "Every 6 hours"], [720, "Every 12 hours"], [1440, "Every day"], [10080, "Every week"]];

  const isEmpty = (value) => value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
  const serp = () => Aira.serp;
  const plural = (n, word) => `${Number(n).toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

  // ===========================================================================
  // Icons (the kit keeps its own set private)
  // ===========================================================================

  const PATHS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    star: '<path d="m12 3.5 2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9l-5.2 2.8 1-5.9L3.5 9.7l5.9-.8z"/>',
    play: '<path d="M8 5.5v13l11-6.5z"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/>',
    download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    pin: '<path d="m15 4 5 5-3 1-3.5 3.5.5 4-1.5 1.5-3.5-3.5L5 20l-1-1 4.5-4.5L5 11l1.5-1.5 4 .5L14 6.5z"/>',
    bookmark: '<path d="M6 4h12v17l-6-4.5L6 21z"/>',
    bell: '<path d="M6 17V11a6 6 0 1 1 12 0v6l1.5 2h-15z"/><path d="M10 21h4"/>',
    diff: '<path d="M7 4v11M7 15l-3-3M7 15l3-3M17 20V9M17 9l-3 3M17 9l3 3"/>',
    history: '<path d="M4 12a8 8 0 1 0 2.5-5.8L4 8.5"/><path d="M4 4v4.5h4.5M12 8v4.5l3 2"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2 5.3"/><path d="M20 4v7h-7"/>',
    code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    share: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.2 10.8 7.6-3.6M8.2 13.2l7.6 3.6"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    load: '<path d="M4 12h12M11 6l-7 6 7 6M20 4v16"/>',
    alert: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5M12 17.5v.5"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>',
  };
  function icon(name) {
    return h("span", { class: "sk-icon", "aria-hidden": "true", html: `<svg viewBox="0 0 24 24" focusable="false">${PATHS[name] || ""}</svg>` });
  }
  /** Button with an optional leading icon. Icon-only buttons need `label` as their accessible name. */
  function btn(label, { icon: iconName, kind = "secondary", sm = true, onClick, title, iconOnly = false, attrs = {} } = {}) {
    return h("button", { type: "button", class: `btn ${kind}${sm ? " sm" : ""}${iconOnly ? " icon" : ""}`, title: title || (iconOnly ? label : null), "aria-label": iconOnly ? label : null, onclick: onClick, ...attrs },
      iconName ? icon(iconName) : null, iconOnly ? null : h("span", { text: label }));
  }

  // ===========================================================================
  // Network
  // ===========================================================================

  class CallError extends Error {
    constructor(message, status, data, requestId) {
      super(message);
      this.status = status;
      this.code = (data && data.code) || "";
      this.recordId = (data && data.record_id) || "";
      this.requestId = requestId || "";
    }
  }
  /** fetch wrapper that keeps the error code and record id (Aira.api drops them). */
  async function call(path, { method = "GET", body } = {}) {
    let response;
    try {
      response = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json", "X-Requested-With": "aira", Accept: "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "same-origin",
      });
    } catch {
      throw new CallError("Aira is unreachable. Check that the server is running.", 0, {}, "");
    }
    const data = await response.json().catch(() => ({}));
    const requestId = response.headers.get("X-Request-ID") || "";
    if (response.status === 401) { Aira.showAuth(); throw new CallError("Your session ended. Sign in again.", 401, data, requestId); }
    if (!response.ok) throw new CallError(data.error || `Request failed (${response.status})`, response.status, data, requestId);
    return data;
  }

  const catalog = { list: null, entries: new Map() };
  async function loadEngines() {
    if (!catalog.list) catalog.list = await call(`${API}/engines`);
    return catalog.list;
  }
  async function loadEntry(id) {
    if (!catalog.entries.has(id)) catalog.entries.set(id, await call(`${API}/engines/${encodeURIComponent(id)}`));
    return catalog.entries.get(id);
  }
  const engineSummary = (id) => (catalog.list ? catalog.list.engines.find((item) => item.id === id) : null) || null;
  const engineName = (id) => (engineSummary(id) || {}).name || serp().humanize(id);

  // ===========================================================================
  // Request state in the URL, remembered requests, example values
  // ===========================================================================

  /** Query object for #/airaapi: params keep their own names unless they collide with page keys, then they get "p.". */
  function stateToQuery(engine, params, options, record) {
    const query = { tab: "playground", engine };
    for (const [name, value] of Object.entries(params || {})) {
      if (isEmpty(value)) continue;
      const key = PAGE_KEYS.has(name) || name.startsWith("p.") || name.startsWith("o.") ? `p.${name}` : name;
      query[key] = typeof value === "object" ? JSON.stringify(value) : String(value);
    }
    for (const [name, value] of Object.entries(options || {})) {
      if (isEmpty(value) || value === false || (name === "output" && value === "json")) continue;
      query[`o.${name}`] = String(value);
    }
    if (record) query.record = record;
    return query;
  }
  function queryToState(query) {
    const params = {};
    const options = {};
    for (const [key, value] of Object.entries(query || {})) {
      if (PAGE_KEYS.has(key)) continue;
      if (key.startsWith("o.")) {
        const name = key.slice(2);
        if (OPTION_KEYS.includes(name)) options[name] = ["no_cache", "async", "zero_trace", "allow_unknown"].includes(name) ? value === "1" || value === "true" : value;
      } else if (key.startsWith("p.")) params[key.slice(2)] = value;
      else params[key] = value;
    }
    return { engine: query && query.engine ? query.engine : "", params, options, record: query && query.record ? query.record : "" };
  }
  function hashFor(query) {
    const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => !isEmpty(v)));
    return `#/airaapi?${new URLSearchParams(clean).toString()}`;
  }
  /** Link other views use to open a request in the Playground (Engines, Locations, History, Careers, Docs). */
  Aira.playgroundLink = (engine, params = {}, options = {}) => hashFor(stateToQuery(engine, params, options, ""));

  /** A run that skipped the cache was a one-off choice; loading its request back must not make every later run cost a credit. */
  const withoutCacheSkip = (options) => { const { no_cache: _skipped, ...rest } = options || {}; return rest; };

  const sameParams = (a, b) => {
    const norm = (obj) => JSON.stringify(Object.entries(obj || {}).filter(([, v]) => !isEmpty(v)).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)]).sort(([x], [y]) => (x < y ? -1 : 1)));
    return norm(a) === norm(b);
  };

  const MEMORY_LIMIT = 30;
  function rememberedParams(engine) {
    const all = Aira.recall("playground.params", {});
    return isObject(all[engine]) ? all[engine] : null;
  }
  function rememberParams(engine, params) {
    const all = Aira.recall("playground.params", {});
    delete all[engine];
    if (!isEmpty(params) && Object.keys(params).length) all[engine] = params;
    const keys = Object.keys(all);
    for (const stale of keys.slice(0, Math.max(0, keys.length - MEMORY_LIMIT))) delete all[stale];
    Aira.remember("playground.params", all);
  }
  const favorites = () => { const v = Aira.recall("playground.favorites", []); return Array.isArray(v) ? v : []; };
  const recents = () => { const v = Aira.recall("playground.recents", []); return Array.isArray(v) ? v : []; };

  // ---- example values --------------------------------------------------------------------------------------------------

  const placeholder = (name) => `REPLACE_WITH_${String(name).toUpperCase()}`;
  const QUERY_BY_CATEGORY = {
    jobs: "solution architect", web: "solution architect salary", maps_local: "coffee", travel: "Bali", shopping: "standing desk",
    scholar_patents: "machine learning", news: "cloud computing", images: "mountain lake", video: "kubernetes tutorial",
    ai: "Explain event-driven architecture in three bullet points", finance: "GOOGL:NASDAQ", trends: "AWS,Azure,Google Cloud",
    app_store: "notes", reviews: "coffee", marketplace: "standing desk", social: "nasa", utility: "solution architect",
  };
  const NAME_SAMPLES = {
    asin: "B073JYC4XM", v: "dQw4w9WgXcQ", channel_id: "UC_x5XG1OV2P6uZZ5FSM9Ttw", author_id: "LSsXyncAAAAJ", case_id: "16467529",
    patent_id: "patent/US11734097B1/en", advertiser_id: "AR17828074650563772417", creative_id: "CR07110512387970564097",
    data_id: "0x89c259af336b3341:0xa4969e07ce3108de", contributor_id: "100670032020503558592", ll: "@40.7455096,-74.0083012,14z",
    geo: "US", promotion_id: "A1C1U2DDI9B2GB", rid: "r/central-park-boathouse-new-york-2", find_loc: "San Francisco, CA",
    image_url: "https://i.imgur.com/HBrB8p0.png", url: "https://example.com/", location: "Austin, Texas, United States", departure_id: "AUS",
    arrival_id: "CDG", mauthors: "geoffrey hinton",
  };
  const PER_ENGINE = {
    google_flights: { departure_id: "AUS", arrival_id: "CDG", outbound_date: { days: 45 }, return_date: { days: 52 } },
    google_hotels: { q: "Bali Resorts", check_in_date: { days: 45 }, check_out_date: { days: 48 } },
    flightradar24: { airport: "AUS" },
    google_flights_deals: { departure_id: "AUS" },
    google_hotels_price_calendar: { start_date: { days: 30 }, end_date: { days: 60 } },
    google_lens: { url: "https://i.imgur.com/HBrB8p0.png" },
    google_play_product: { product_id: "com.spotify.music" },
    google_ads_transparency_center: { text: "nike.com" },
    google_autocomplete: { q: "how to become a" },
    google_trends_trending_now: { geo: "US" },
    apple_product: { product_id: "534220544" },
    apple_reviews: { product_id: "534220544" },
    yelp_place: { place_id: "ED7A7vDdg8yLNKJTSVHHmg" },
    yelp_reviews: { place_id: "ED7A7vDdg8yLNKJTSVHHmg" },
    facebook_profile: { profile_id: "Meta" },
    instagram_profile: { profile_id: "serpapicom" },
    youtube_channel: { channel_id: "UC_x5XG1OV2P6uZZ5FSM9Ttw" },
    google_maps_autocomplete: { q: "pizza", ll: "@40.7455096,-74.0083012,14z" },
    google_local_services: { q: "electrician" },
    google_ads: { q: "cloud migration", location: "United States" },
    extract: { url: "https://example.com/" },
    google_jobs: { q: "solution architect" },
    google_jobs_listing: { q: placeholder("job_id") },
  };
  const isoDay = (offset) => { const d = new Date(); d.setUTCDate(d.getUTCDate() + offset); return d.toISOString().slice(0, 10); };

  /** Values that make an engine's required parameters valid. Identifiers and tokens come out as REPLACE_WITH_* markers. */
  function exampleFor(entry) {
    const params = entry.params || [];
    const byName = new Map(params.map((p) => [p.name, p]));
    const wanted = [];
    for (const p of params) if (p.required) wanted.push(p.name);
    for (const group of entry.requires_any_of || []) if (!group.some((name) => wanted.includes(name))) wanted.push(group[0]);
    if (!wanted.length) {
      // nothing is strictly required (google_news takes a query or a topic token): give the primary query a value
      const primary = ["q", "query", "search_query", "p", "text", "k", "_nkw", "term"].find((name) => byName.has(name));
      if (primary) wanted.push(primary);
    }
    const overrides = PER_ENGINE[entry.id] || {};
    for (const name of Object.keys(overrides)) if (byName.has(name) && !wanted.includes(name)) wanted.push(name);
    const out = {};
    for (const name of wanted) {
      const p = byName.get(name);
      if (!p) continue;
      let value = overrides[name];
      if (isObject(value) && "days" in value) value = serp().logic.normalizeDate(isoDay(value.days), serp().logic.dateFormatOf(p));
      if (value === undefined) {
        if (p.type === "enum" || (p.type === "csv" && Array.isArray(p.options) && p.options.length)) {
          const options = (p.options || []).map((o) => String(o.value));
          value = p.default !== null && p.default !== undefined && options.includes(String(p.default)) ? String(p.default) : options[0];
        } else if (p.type === "boolean") value = p.default === true ? false : true;
        else if (p.type === "integer" || p.type === "number") value = p.default !== null && p.default !== undefined ? p.default : 1;
        else if (p.type === "date") value = serp().logic.normalizeDate(isoDay(45), serp().logic.dateFormatOf(p));
        else if (NAME_SAMPLES[name] !== undefined && name !== "location") value = NAME_SAMPLES[name];
        else if (name === "location") value = NAME_SAMPLES.location;
        else if (["q", "query", "search_query", "p", "text", "k", "_nkw", "term"].includes(name)) value = QUERY_BY_CATEGORY[entry.category] || "coffee";
        else value = placeholder(name);
      }
      if (value !== undefined && value !== null) out[name] = value;
    }
    return out;
  }
  function usesPlaceholders(values) {
    return Object.values(values || {}).some((value) => typeof value === "string" && value.startsWith("REPLACE_WITH_"));
  }

  /** What the person's Aira profile suggests for a new request; only ever used where it is honestly a role search. */
  function profileDefaults(entry) {
    if (entry.id !== "google_jobs" && entry.id !== "aira_jobs") return null;
    const profile = Aira.state.profile || {};
    const candidate = profile.candidate_profile || {};
    const role = (profile.career_profiles && profile.career_profiles[candidate.career_profile]) || candidate.current_title || "";
    return role ? { q: role } : null;
  }

  /** Short, readable summary of a parameter set: the primary query first. */
  const PRIMARY = ["q", "query", "search_query", "p", "text", "k", "_nkw", "term", "url", "image_url", "product_id", "place_id", "data_id", "v", "channel_id", "asin"];
  function primaryValue(params) {
    for (const key of PRIMARY) if (!isEmpty((params || {})[key])) return String(params[key]);
    const first = Object.values(params || {}).find((v) => !isEmpty(v));
    return first === undefined ? "" : String(first);
  }
  function summarize(params, max = 3) {
    const entries = Object.entries(params || {}).filter(([, v]) => !isEmpty(v));
    entries.sort(([a], [b]) => (PRIMARY.indexOf(a) === -1 ? 99 : PRIMARY.indexOf(a)) - (PRIMARY.indexOf(b) === -1 ? 99 : PRIMARY.indexOf(b)));
    return entries.slice(0, max).map(([k, v]) => `${PRIMARY.includes(k) ? "" : `${k}: `}${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ") + (entries.length > max ? ` +${entries.length - max}` : "");
  }

  // ===========================================================================
  // Small widgets: tab bar, popup menu, announcer
  // ===========================================================================

  /** Tab list that keeps every pane alive. items: [{id,label}]. Returns {node, select(id), badge(id,text)}. */
  function tabBar(items, active, onSelect, label) {
    const bar = h("div", { class: "tabs pg-tabs", role: "tablist", "aria-label": label });
    const buttons = new Map();
    const badges = new Map();
    for (const item of items) {
      const badge = h("span", { class: "pg-tab-n", hidden: true });
      const button = h("button", { type: "button", role: "tab", id: `pg-tab-${item.id}`, "aria-controls": `pg-pane-${item.id}`, "aria-selected": "false", tabindex: "-1", onclick: () => select(item.id, true) }, h("span", { text: item.label }), badge);
      buttons.set(item.id, button);
      badges.set(item.id, badge);
      bar.append(button);
    }
    function select(id, fire = false) {
      for (const [key, button] of buttons) { const on = key === id; button.setAttribute("aria-selected", String(on)); button.tabIndex = on ? 0 : -1; }
      if (fire) onSelect(id);
    }
    bar.addEventListener("keydown", (event) => {
      const ids = [...buttons.keys()];
      const index = ids.findIndex((id) => buttons.get(id) === document.activeElement);
      if (index === -1) return;
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % ids.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + ids.length) % ids.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = ids.length - 1;
      else return;
      event.preventDefault();
      buttons.get(ids[next]).focus();
      select(ids[next], true);
    });
    select(active);
    return {
      node: bar,
      select: (id) => select(id, false),
      badge: (id, text) => { const node = badges.get(id); if (!node) return; node.textContent = text === undefined || text === null || text === "" ? "" : String(text); node.hidden = !node.textContent; },
    };
  }

  /** Button that opens a small action menu. Returns the wrapper node. */
  function menuButton(trigger, items) {
    const wrap = h("div", { class: "pg-menu-wrap" }, trigger);
    let menu = null;
    const close = (restore = false) => {
      if (!menu) return;
      menu.remove();
      menu = null;
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", outside, true);
      if (restore) trigger.focus();
    };
    const outside = (event) => { if (!wrap.contains(event.target)) close(); };
    const open = () => {
      const list = typeof items === "function" ? items() : items;
      menu = h("div", { class: "pg-menu", role: "menu", "aria-label": trigger.getAttribute("aria-label") || trigger.textContent },
        list.map((item) => h("button", { type: "button", role: "menuitem", class: "pg-menu-item", disabled: item.disabled, title: item.title, tabindex: "-1", onclick: () => { close(true); item.onClick(); } }, item.icon ? icon(item.icon) : null, h("span", { text: item.label }))));
      wrap.append(menu);
      trigger.setAttribute("aria-expanded", "true");
      document.addEventListener("pointerdown", outside, true);
      const first = menu.querySelector("button:not([disabled])");
      if (first) first.focus();
    };
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.addEventListener("click", () => (menu ? close(true) : open()));
    wrap.addEventListener("keydown", (event) => {
      if (!menu) { if (event.key === "ArrowDown" && event.target === trigger) { event.preventDefault(); open(); } return; }
      const entries = [...menu.querySelectorAll("button:not([disabled])")];
      const index = entries.indexOf(document.activeElement);
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
      else if (event.key === "ArrowDown") { event.preventDefault(); entries[(index + 1) % entries.length].focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); entries[(index - 1 + entries.length) % entries.length].focus(); }
      else if (event.key === "Home") { event.preventDefault(); entries[0].focus(); }
      else if (event.key === "End") { event.preventDefault(); entries[entries.length - 1].focus(); }
      else if (event.key === "Tab") close();
    });
    return wrap;
  }

  // ===========================================================================
  // Playground
  // ===========================================================================

  let active = null;

  function createPlayground(content, route) {
    const S = {
      overview: null, engineId: "", entry: null, form: null, record: null, viewer: null,
      running: false, runToken: 0, selectToken: 0, pollTimer: null, leftTab: Aira.recall("playground.tab", "params"),
      history: [], historyTotal: 0, historyEngineOnly: Aira.recall("playground.historyEngineOnly", true), historyPinned: false, historyLoaded: false,
      saved: [], monitors: [], savedLoaded: false, label: "", urlTimer: null, startedAt: 0,
    };
    const api = { dispose, start, content };
    const alive = () => active === api && content.isConnected;
    const dom = {};
    const timers = new Set();
    const cleanups = [];

    // ---- helpers -----------------------------------------------------------------------------------------------------

    function announce(message) { dom.live.textContent = ""; setTimeout(() => { dom.live.textContent = message; }, 30); }
    function setTimer(fn, ms) { const id = setTimeout(() => { timers.delete(id); fn(); }, ms); timers.add(id); return id; }
    function formParams() { return S.form ? S.form.getValues() : {}; }
    function optionValues() { return S.options ? S.options.getValues() : {}; }
    const configured = () => !S.overview || S.overview.configured !== false;
    const account = () => (S.overview && S.overview.account) || null;
    const cacheMinutes = () => { const n = Number(Aira.state.settings && Aira.state.settings.airaapi_cache_ttl_minutes); return Number.isFinite(n) && n >= 0 ? n : 60; };

    function creditCost(params = formParams()) {
      if (S.engineId === "extract" && params.mode === "crawl") {
        const spec = (S.entry.params || []).find((p) => p.name === "max_pages") || {};
        return clamp(Number(params.max_pages || spec.default || 1) || 1, 1, 1000);
      }
      return 1;
    }
    /** An identical live search inside the cache window means the next run costs nothing. */
    function predictedCache(params = formParams(), options = optionValues()) {
      if (options.no_cache || options.async || cacheMinutes() === 0) return null;
      const cutoff = Date.now() - cacheMinutes() * 60000;
      for (const item of S.history) {
        if (item.engine !== S.engineId || item.status === "error" || item.cache_hit || item.status === "running") continue;
        const when = new Date(item.created_at).getTime();
        if (when >= cutoff && sameParams(item.params, params)) return item;
      }
      return null;
    }

    // ---- URL state ---------------------------------------------------------------------------------------------------

    function syncUrl() {
      if (!alive() || !S.engineId) return;
      const current = Aira.router.current;
      if (!current || current.path[0] !== "airaapi") return;
      const query = stateToQuery(S.engineId, formParams(), optionValues(), S.record && S.record.engine === S.engineId ? S.record.id : "");
      const target = hashFor(query);
      if (location.hash !== target) history.replaceState(null, "", target);
      Aira.router.current = { path: current.path, query };
    }
    function syncUrlSoon() { clearTimeout(S.urlTimer); S.urlTimer = setTimeout(syncUrl, 250); timers.add(S.urlTimer); }
    function shareUrl() {
      const query = stateToQuery(S.engineId, formParams(), optionValues(), "");
      return `${location.origin}${location.pathname}${hashFor(query)}`;
    }

    // ---- build -------------------------------------------------------------------------------------------------------

    function build() {
      dom.live = h("div", { class: "visually-hidden", role: "status", "aria-live": "polite" });

      // engine bar
      dom.pickButton = h("button", { type: "button", class: "pg-pick", "aria-haspopup": "listbox", "aria-expanded": "false", "aria-label": "Choose engine", onclick: () => openPicker() });
      dom.recents = h("div", { class: "pg-recents", role: "group", "aria-label": "Recent engines" });
      dom.shortcutsButton = btn("Keyboard shortcuts", { icon: "keyboard", kind: "ghost", iconOnly: true, onClick: showShortcuts });
      dom.bar = h("div", { class: "pg-bar" }, dom.pickButton, dom.recents, dom.shortcutsButton);

      dom.setup = h("div", { class: "pg-setup", hidden: true, role: "note" },
        icon("key"),
        h("div", {}, h("strong", { text: "Connect a SerpApi key to run searches" }), h("p", { text: "You can already pick an engine, build a request and copy the code. Running needs the key, which Aira keeps encrypted on this computer." })),
        h("a", { class: "btn sm", href: "#/airaapi?tab=account", text: "Connect key" }));

      // request panel
      dom.engineInfo = h("div", { class: "pg-eng" });
      const tabs = [{ id: "params", label: "Parameters" }, { id: "options", label: "Options" }, { id: "code", label: "Code" }, { id: "history", label: "History" }, { id: "saved", label: "Saved" }];
      if (!tabs.some((t) => t.id === S.leftTab)) S.leftTab = "params";
      dom.tabs = tabBar(tabs, S.leftTab, (id) => setLeftTab(id), "Request sections");

      dom.paramsHost = h("div", { class: "pg-form-host" });
      dom.exampleButton = btn("Fill example", { icon: "spark", kind: "ghost", onClick: fillExample, title: "Fill the required parameters with example values" });
      dom.clearButton = btn("Clear", { icon: "close", kind: "ghost", onClick: clearForm, title: "Clear every parameter" });
      dom.paramsPane = h("div", { class: "pg-pane", id: "pg-pane-params", role: "tabpanel", "aria-labelledby": "pg-tab-params" },
        h("div", { class: "pg-pane-tools" }, dom.exampleButton, dom.clearButton), dom.paramsHost);

      S.options = serp().optionsForm({}, { onChange: () => { paintOptionsBadge(); paintCost(); syncUrlSoon(); } });
      dom.label = h("input", { type: "text", maxlength: 80, autocomplete: "off", placeholder: "For example: Austin architects, week 43", oninput: () => { S.label = dom.label.value; } });
      dom.optionsPane = h("div", { class: "pg-pane", id: "pg-pane-options", role: "tabpanel", "aria-labelledby": "pg-tab-options", hidden: true },
        ui.field("Label (optional)", dom.label, "Shown in History so you can find this run again."),
        S.options.node,
        h("p", { class: "sk-hint", text: "Options change how the request is sent, not what is searched. They are not part of the engine's parameters." }));

      dom.codeHost = h("div", { class: "pg-code-host" });
      dom.codePane = h("div", { class: "pg-pane", id: "pg-pane-code", role: "tabpanel", "aria-labelledby": "pg-tab-code", hidden: true }, dom.codeHost);

      dom.historyPane = h("div", { class: "pg-pane", id: "pg-pane-history", role: "tabpanel", "aria-labelledby": "pg-tab-history", hidden: true });
      dom.savedPane = h("div", { class: "pg-pane", id: "pg-pane-saved", role: "tabpanel", "aria-labelledby": "pg-tab-saved", hidden: true });
      buildHistoryPane();
      buildSavedPane();

      // action bar
      dom.runButton = h("button", { type: "button", class: "btn pg-run", onclick: () => run() }, icon("play"), h("span", { class: "pg-run-label", text: "Run search" }), h("span", { class: "pg-run-key", "aria-hidden": "true", text: RUN_HINT }));
      dom.runButton.setAttribute("aria-keyshortcuts", isMac ? "Meta+Enter" : "Control+Enter");
      dom.cost = h("div", { class: "pg-cost", "aria-live": "polite" });
      dom.plan = h("div", { class: "pg-plan" });
      dom.actions = h("div", { class: "pg-actions" }, h("div", { class: "pg-actions-row" }, dom.runButton, dom.cost), dom.plan);

      dom.request = h("section", { class: "panel pg-req", "aria-label": "Request" },
        dom.engineInfo, dom.tabs.node,
        h("div", { class: "pg-scroll" }, dom.paramsPane, dom.optionsPane, dom.codePane, dom.historyPane, dom.savedPane),
        dom.actions);

      // result panel
      dom.resHead = h("div", { class: "pg-res-head", hidden: true });
      dom.resBanner = h("div", { class: "pg-res-banner" });
      dom.resBody = h("div", { class: "pg-res-body" });
      dom.result = h("section", { class: "panel pg-res", "aria-label": "Result", tabindex: "-1" }, dom.resHead, dom.resBanner, dom.resBody);

      dom.root = h("div", { class: "pg" }, dom.live, dom.bar, dom.setup, h("div", { class: "pg-grid" }, dom.request, dom.result));
      content.replaceChildren(dom.root);
      showEmpty();
      paintRecents();
      paintOverview();
    }

    function setLeftTab(id, { focus = false } = {}) {
      S.leftTab = id;
      Aira.remember("playground.tab", id);
      dom.tabs.select(id);
      for (const [key, pane] of [["params", dom.paramsPane], ["options", dom.optionsPane], ["code", dom.codePane], ["history", dom.historyPane], ["saved", dom.savedPane]]) pane.hidden = key !== id;
      if (id === "history" && !S.historyLoaded) loadHistory();
      if (id === "saved" && !S.savedLoaded) loadSaved();
      if (id === "code") paintCode();
      if (focus) { const tab = document.getElementById(`pg-tab-${id}`); if (tab) tab.focus(); }
    }

    // ---- engine selection ----------------------------------------------------------------------------------------------

    async function selectEngine(id, { params, options, silent = false } = {}) {
      const token = ++S.selectToken;
      let entry;
      try { entry = await loadEntry(id); }
      catch (error) { if (alive()) Aira.reportError(error); return false; }
      if (!alive() || token !== S.selectToken) return false;
      const previous = S.engineId;
      S.engineId = id;
      S.entry = entry;
      const initial = params || rememberedParams(id) || profileDefaults(entry) || {};
      S.form = serp().paramForm(entry, initial, { compact: true, onChange: onFormChange, onSubmit: () => run() });
      dom.paramsHost.replaceChildren(S.form.node);
      if (options) S.options.setValues(options);
      else if (previous !== id) S.options.setValues({ ...optionValues(), allow_unknown: false });
      pushRecent(id);
      Aira.remember("playground.engine", id);
      paintEngineInfo();
      paintRecents();
      paintOptionsBadge();
      paintCode();
      paintCost();
      paintDirty();
      if (previous !== id) { S.historyLoaded = false; if (S.leftTab === "history") loadHistory(); }
      syncUrl();
      if (!silent) { announce(`${entry.name} selected. ${plural(entry.params.length, "parameter")}.`); }
      return true;
    }

    function onFormChange() {
      rememberParams(S.engineId, formParams());
      paintCode();
      paintCost();
      paintDirty();
      syncUrlSoon();
    }
    function pushRecent(id) {
      const list = [id, ...recents().filter((item) => item !== id)].slice(0, 8);
      Aira.remember("playground.recents", list);
    }
    function toggleFavorite(id) {
      const list = favorites();
      const next = list.includes(id) ? list.filter((item) => item !== id) : [id, ...list];
      Aira.remember("playground.favorites", next);
      paintEngineInfo();
      paintRecents();
      announce(`${engineName(id)} ${next.includes(id) ? "added to" : "removed from"} favorites`);
      return next.includes(id);
    }

    function fillExample() {
      const values = exampleFor(S.entry);
      S.form.setValues({ ...formParams(), ...values });
      onFormChange();
      if (usesPlaceholders(values)) toast("Identifier and token fields got REPLACE_WITH_ markers. Copy the real value from an earlier result.", "", { timeout: 7000 });
      announce("Example values filled in.");
      S.form.focusFirst();
    }
    function clearForm() {
      S.form.reset();
      onFormChange();
      announce("Parameters cleared.");
      S.form.focusFirst();
    }

    // ---- painting: engine header, recents, cost, code ------------------------------------------------------------------

    function paintEngineInfo() {
      const entry = S.entry;
      if (!entry) return;
      const fav = favorites().includes(entry.id);
      const flags = [];
      if (entry.deprecated) flags.push(h("span", { class: "sk-chip warn", text: "Deprecated" }));
      if (entry.unlisted) flags.push(h("span", { class: "sk-chip warn", text: "Unlisted", title: "Not shown in the public SerpApi engine list" }));
      const pagination = entry.pagination && entry.pagination.kind ? entry.pagination.kind.replaceAll("_", " ") : "";
      const more = h("button", { type: "button", class: "pg-more", "aria-expanded": "false", text: "More", onclick: (event) => {
        const open = event.currentTarget.getAttribute("aria-expanded") !== "true";
        event.currentTarget.setAttribute("aria-expanded", String(open));
        event.currentTarget.textContent = open ? "Less" : "More";
        desc.classList.toggle("open", open);
        detail.hidden = !open;
      } });
      const desc = h("p", { class: "pg-eng-desc", text: entry.description });
      const detail = h("div", { class: "pg-eng-detail", hidden: true },
        entry.aira_use ? h("p", {}, h("strong", { text: "In Aira: " }), entry.aira_use) : null,
        entry.pagination && entry.pagination.notes ? h("p", { class: "sk-hint" }, h("strong", { text: "Paging: " }), entry.pagination.notes) : null,
        (entry.result_keys || []).length ? h("div", { class: "sk-chips" }, h("span", { class: "sk-hint", text: "Returns" }), ...entry.result_keys.slice(0, 14).map((key) => h("code", { class: "pg-key", text: key }))) : null);
      dom.engineInfo.replaceChildren(
        h("div", { class: "pg-eng-top" },
          serp().engineBadge(entry),
          h("code", { class: "pg-eng-id", text: entry.id }),
          h("span", { class: "pg-eng-spacer" }),
          h("button", { type: "button", class: `pg-star${fav ? " on" : ""}`, "aria-pressed": String(fav), "aria-label": `Favorite ${entry.name}`, title: fav ? "Remove from favorites" : "Add to favorites", onclick: () => toggleFavorite(entry.id) }, icon("star")),
          entry.docs_url ? h("a", { class: "btn ghost sm icon", href: entry.docs_url, target: "_blank", rel: "noopener noreferrer", "aria-label": `${entry.name} documentation (opens in a new tab)`, title: "Documentation" }, icon("external")) : null),
        h("div", { class: "pg-eng-body" }, desc, more),
        detail,
        h("div", { class: "sk-chips" }, serp().chip(entry.family), serp().chip(serp().humanize(entry.category)), serp().chip(plural(entry.params.length, "parameter")), pagination ? serp().chip(`paging: ${pagination}`) : null, ...flags));
      // the picker button
      dom.pickButton.replaceChildren(serp().engineBadge(entry), h("span", { class: "pg-pick-id", text: entry.id }), h("span", { class: "pg-pick-chev" }, icon("down")));
      dom.pickButton.setAttribute("aria-label", `Engine: ${entry.name}. Change engine`);
      requestAnimationFrame(() => {
        const clipped = desc.scrollHeight > desc.clientHeight + 1;
        more.hidden = !clipped && !(entry.aira_use || (entry.result_keys || []).length);
      });
    }

    function paintRecents() {
      const favs = favorites();
      const ids = [...favs, ...recents().filter((id) => !favs.includes(id))].filter((id) => engineSummary(id)).slice(0, 8);
      dom.recents.replaceChildren(...ids.map((id) => h("button", { type: "button", class: `pg-chip${id === S.engineId ? " on" : ""}`, "aria-pressed": String(id === S.engineId), title: `${engineName(id)} (${id})`, onclick: () => { if (id !== S.engineId) selectEngine(id).then((ok) => ok && S.form && S.form.focusFirst()); } }, favs.includes(id) ? icon("star") : null, h("span", { text: engineName(id) }))));
    }

    function paintOverview() {
      dom.setup.hidden = configured();
      paintCost();
    }

    function paintCost() {
      if (!S.entry) return;
      const params = formParams();
      const options = optionValues();
      const cost = creditCost(params);
      const hit = predictedCache(params, options);
      const info = account();
      const left = info && Number.isFinite(Number(info.total_searches_left)) ? Number(info.total_searches_left) : null;
      let chip;
      if (hit) {
        chip = h("span", { class: "sk-credit cache", title: `An identical search ran ${fmt.ago(hit.created_at)} and is still inside the ${cacheMinutes()} minute cache window, so this run is served locally.` }, icon("check"), h("span", { text: "Likely cached · 0 credits" }));
      } else {
        const over = left !== null && cost > left;
        chip = h("span", { class: `sk-credit${over ? " over" : ""}`, title: options.no_cache ? "Cache skipped: this always calls the engine." : `Identical searches inside the ${cacheMinutes()} minute cache window cost nothing.` }, h("span", { text: `${cost} credit${cost === 1 ? "" : "s"}${options.no_cache ? " · fresh" : ""}` }));
      }
      dom.cost.replaceChildren(chip);
      if (info && left !== null) {
        const used = Number(info.this_month_usage);
        dom.plan.textContent = `${left.toLocaleString()} searches left${Number.isFinite(used) ? ` · ${used.toLocaleString()} used this month` : ""}`;
        dom.plan.hidden = false;
      } else dom.plan.hidden = true;
    }

    function paintCode() {
      if (!S.entry || S.leftTab !== "code") return;
      const params = formParams();
      if (!dom.snippets) { dom.snippets = serp().snippetPanel(S.entry, params); dom.codeHost.replaceChildren(dom.snippets); }
      else dom.snippets.update(S.entry, params);
    }
    function paintOptionsBadge() {
      const count = Object.keys(optionValues()).filter((key) => !(key === "output" && optionValues()[key] === "json")).length;
      dom.tabs.badge("options", count || "");
    }
    function paintDirty() {
      if (!dom.dirty) return;
      const record = S.record;
      dom.dirty.hidden = !(record && record.engine === S.engineId && !sameParams(record.params, formParams()));
    }

    // ---- result pane states ---------------------------------------------------------------------------------------------

    function showEmpty() {
      dom.resHead.hidden = true;
      dom.resBanner.replaceChildren();
      const needs = S.entry ? (S.entry.params || []).filter((p) => p.required).map((p) => p.label || p.name) : [];
      dom.resBody.replaceChildren(h("div", { class: "pg-empty" },
        h("div", { class: "pg-empty-art", "aria-hidden": "true" }, icon("search")),
        h("h2", { text: "Ready when you are" }),
        h("p", { text: S.entry ? `Fill in the parameters for ${S.entry.name}, then press Run. Results appear here as cards, JSON, a table, the raw response and ready-to-paste code.` : "Choose an engine to start." }),
        needs.length ? h("p", { class: "sk-hint", text: `Needs ${needs.join(", ")}.` }) : null,
        h("div", { class: "btn-row", style: { justifyContent: "center" } },
          S.entry ? btn("Fill example", { icon: "spark", onClick: () => { setLeftTab("params"); fillExample(); } }) : null,
          btn("Browse engines", { icon: "search", kind: "ghost", onClick: () => openPicker() })),
        h("p", { class: "sk-hint" }, "Run with ", h("span", { class: "kbd", text: RUN_HINT }), "  ·  switch engine with ", h("span", { class: "kbd", text: isMac ? "⌘ K" : "Ctrl K" }))));
    }

    function showLoading() {
      dom.resHead.hidden = true;
      dom.resBanner.replaceChildren();
      S.startedAt = Date.now();
      const elapsed = h("span", { "aria-hidden": "true", text: "0.0 s" });
      const tick = () => { if (!S.running || !alive()) return; elapsed.textContent = `${((Date.now() - S.startedAt) / 1000).toFixed(1)} s`; setTimer(tick, 200); };
      dom.resBody.replaceChildren(h("div", { class: "pg-loading", role: "status" },
        h("div", { class: "pg-loading-head" }, h("span", { class: "sk-pulse", "aria-hidden": "true" }), h("strong", { text: `Searching ${S.entry ? S.entry.name : "the engine"}` }), elapsed),
        h("p", { class: "sk-hint", text: "Aira checks its cache first. A live search usually takes a few seconds; some engines take longer." }),
        ui.skeleton(4),
        h("div", { class: "btn-row" }, btn("Stop waiting", { kind: "ghost", onClick: stopWaiting, title: "The search keeps running and still lands in History" }))));
      tick();
    }
    function stopWaiting() {
      S.runToken += 1;
      S.running = false;
      paintRunning();
      if (S.record) showRecord(S.record); else showEmpty();
      toast("Stopped waiting. If the search finishes, it will be in History.", "");
    }

    function calloutFor(error) {
      const code = error.code || "";
      const status = error.status;
      let title = "The search did not run";
      let text = error.message;
      let action = null;
      if (code === "not_configured") {
        title = "No SerpApi key is connected";
        text = "Connect your SerpApi key to run live searches. Cached results and the Docs tab work without it.";
        action = h("a", { class: "btn sm", href: "#/airaapi?tab=account", text: "Connect key" });
      } else if (code === "invalid_api_key") {
        title = "SerpApi rejected the key";
        text = "The stored key was refused. Replace it on the Account tab; Aira checks a key before keeping it.";
        action = h("a", { class: "btn sm", href: "#/airaapi?tab=account", text: "Update key" });
      } else if (code === "out_of_searches" || status === 402) {
        title = "Out of searches";
        text = `${error.message} Cached identical searches still work, and extra credits can be added on SerpApi.`;
        action = h("a", { class: "btn secondary sm", href: "https://serpapi.com/manage-api-key", target: "_blank", rel: "noopener noreferrer", text: "Open SerpApi billing" });
      } else if (code === "rate_limited" || status === 429) {
        title = "Slow down for a moment";
        text = `${error.message} Aira limits how fast searches go out; try again shortly.`;
      } else if (status === 0) title = "Aira is unreachable";
      else if (status === 400 || status === 422) title = "Check the request";
      return h("div", { class: "pg-callout", role: "alert" }, icon("alert"), h("div", {}, h("strong", { text: title }), h("p", { text }), error.requestId ? h("p", { class: "sk-hint", text: `Request ${error.requestId}` }) : null), action);
    }

    async function showFailure(error) {
      let record = null;
      if (error.recordId) { try { record = await call(`${API}/searches/${error.recordId}`); } catch { record = null; } }
      if (!alive()) return;
      if (record) {
        showRecord(record, { failure: error });
        refreshHistorySoon();
      } else {
        dom.resHead.hidden = true;
        dom.resBanner.replaceChildren();
        dom.resBody.replaceChildren(calloutFor(error));
      }
      announce(`Search failed. ${error.message}`);
    }

    function showRecord(record, { failure } = {}) {
      S.record = record;
      buildResultHeader(record);
      const notes = [];
      if (failure) notes.push(calloutFor(failure));
      if (record.cache_hit) {
        notes.push(h("div", { class: "pg-banner cache" }, icon("check"),
          h("span", { text: `Served from your local cache. An identical search ran within the last ${cacheMinutes()} minutes, so no search was used.` }),
          btn("Run fresh", { icon: "refresh", onClick: () => run({ fresh: true }), title: "Skip the cache and call the engine (uses a credit)" })));
      }
      if (record.note) notes.push(h("div", { class: "pg-banner" }, icon("info"), h("span", { text: record.note })));
      if (record.result_truncated) notes.push(h("div", { class: "pg-banner warn" }, icon("alert"), h("span", { text: "The response was larger than the stored-result limit, so only the first part is shown. Raise the limit in settings to keep full bodies." })));
      dom.resBanner.replaceChildren(...notes);
      const page = record.output === "html" || record.output === "md";
      S.viewer = serp().resultViewer(record, { mode: page ? "html" : undefined, onMode: () => {} });
      dom.resBody.replaceChildren(S.viewer.node);
      paintDirty();
    }

    function buildResultHeader(record) {
      const engine = engineSummary(record.engine) || { id: record.engine, name: record.engine_name || record.engine };
      dom.dirty = h("span", { class: "pg-dirty", hidden: true, title: "The form no longer matches the request that produced this result." }, icon("sliders"), h("span", { text: "Form changed since this run" }));
      const meta = [
        record.created_at ? h("span", { title: fmt.datetime(record.created_at), text: fmt.ago(record.created_at) }) : null,
        record.duration_ms !== undefined && record.duration_ms !== null && !record.cache_hit ? h("span", { text: `${Number(record.duration_ms).toLocaleString()} ms` }) : null,
        record.result_count !== undefined && record.result_count !== null ? h("span", { text: plural(record.result_count, "result") }) : null,
        record.result_bytes ? h("span", { text: fmt.bytes(record.result_bytes) }) : null,
        record.source && record.source !== "playground" ? h("span", { text: record.source }) : null,
      ].filter(Boolean);
      const idChip = h("button", { type: "button", class: "pg-id", title: "Copy search ID", "aria-label": `Copy search ID ${record.id}`, onclick: () => serp().copyText(record.id, "Search ID copied") }, icon("copy"), h("code", { text: record.id.slice(0, 8) }));
      const pinned = !!record.pinned;
      const pinButton = btn(pinned ? "Pinned" : "Pin", { icon: "pin", onClick: () => togglePin(), attrs: { "aria-pressed": String(pinned) }, title: pinned ? "Unpin this search (pinned searches are never pruned)" : "Pin this search so retention never removes it" });
      const exportMenu = menuButton(btn("Export", { icon: "download" }), () => [
        { label: "JSON file", icon: "download", onClick: () => exportAs("json") },
        { label: "CSV (flattened list)", icon: "list", onClick: () => exportAs("csv"), disabled: record.output !== "json" },
        { label: "HTML report", icon: "download", onClick: () => exportAs("html") },
        { label: "cURL script", icon: "code", onClick: () => exportAs("curl") },
      ]);
      const shareMenu = menuButton(btn("Share", { icon: "share" }), () => [
        { label: "Copy link to this request", icon: "link", onClick: () => serp().copyText(shareUrl(), "Link copied") },
        { label: "Copy cURL (API)", icon: "code", onClick: () => serp().copyText(serp().codeSnippets(record.engine, record.params).curl, "cURL copied") },
        { label: "Copy cURL (SerpApi direct)", icon: "code", onClick: () => serp().copyText(serp().codeSnippets(record.engine, record.params, { baseUrl: "https://serpapi.com", apiKey: "YOUR_SERPAPI_API_KEY" }).curl, "cURL copied") },
        { label: "Copy result JSON", icon: "copy", disabled: !record.result, onClick: () => serp().copyText(JSON.stringify(record.result, null, 2), "JSON copied") },
      ]);
      dom.resHead.hidden = false;
      dom.resHead.replaceChildren(
        h("div", { class: "pg-res-title" }, serp().engineBadge(engine), serp().statusBadge(record), serp().creditChip(record), dom.dirty),
        h("div", { class: "pg-res-meta" }, meta.flatMap((node, i) => [i ? h("span", { class: "sk-dot", "aria-hidden": "true", text: "·" }) : null, node]), idChip),
        h("div", { class: "pg-toolbar", role: "toolbar", "aria-label": "Result actions" },
          btn("Edit", { icon: "load", onClick: () => editRequest(record), title: "Load this run's parameters into the form", attrs: { "aria-label": "Edit request: load this run's parameters into the form" } }),
          pinButton,
          btn("Save", { icon: "bookmark", onClick: () => openSaveDialog({ record }), title: "Save as a saved search" }),
          btn("Monitor", { icon: "bell", onClick: () => openSaveDialog({ record, monitor: true }), title: "Re-run on a schedule and be told when results change" }),
          btn("Compare", { icon: "diff", onClick: () => openCompare(record), disabled: record.output !== "json" || record.status === "error", title: "Compare with an earlier run" }),
          exportMenu, shareMenu));
    }

    async function togglePin() {
      const record = S.record;
      if (!record) return;
      try {
        const updated = await call(`${API}/searches/${record.id}`, { method: "PATCH", body: { pinned: !record.pinned } });
        record.pinned = !!(updated && updated.pinned !== undefined ? updated.pinned : !record.pinned);
        buildResultHeader(record);
        paintDirty();
        refreshHistorySoon();
        announce(record.pinned ? "Search pinned." : "Search unpinned.");
      } catch (error) { Aira.reportError(error); }
    }
    async function exportAs(format) {
      const record = S.record;
      if (!record) return;
      const ext = { json: "json", csv: "csv", html: "html", curl: "sh" }[format];
      try { await Aira.download(`${API}/searches/${record.id}/export?format=${format}`, `aira-${record.engine}-${record.id.slice(0, 8)}.${ext}`); }
      catch (error) { Aira.reportError(error); }
    }
    function editRequest(record) {
      const go = async () => {
        if (record.engine !== S.engineId) { const ok = await selectEngine(record.engine, { params: record.params, options: withoutCacheSkip(record.options) }); if (!ok) return; }
        else { S.form.setValues(record.params || {}); if (record.options) S.options.setValues(withoutCacheSkip(record.options)); onFormChange(); }
        setLeftTab("params");
        S.form.focusFirst();
        announce("Parameters loaded.");
      };
      go();
    }

    // ---- run -------------------------------------------------------------------------------------------------------------

    function paintRunning() {
      dom.runButton.disabled = S.running;
      dom.runButton.setAttribute("aria-busy", String(S.running));
      dom.runButton.querySelector(".pg-run-label").textContent = S.running ? "Running…" : "Run search";
    }

    async function run({ fresh = false } = {}) {
      if (S.running || !S.entry) return;
      const problems = S.form.validate();
      if (problems.length) {
        setLeftTab("params");
        announce(`${plural(problems.length, "problem")} to fix before running.`);
        return;
      }
      const params = S.form.getValues();
      // values that came from a shared link or a history row never fired onChange; running them is using them
      rememberParams(S.engineId, params);
      const options = { ...optionValues() };
      if (fresh) options.no_cache = true;
      const cost = creditCost(params);
      if (cost > 1 && !(await serp().confirmCost(cost, `This ${S.entry.name} request`))) return;
      const token = ++S.runToken;
      clearTimeout(S.pollTimer);
      S.running = true;
      paintRunning();
      showLoading();
      if (window.innerWidth < 1100) dom.result.scrollIntoView({ block: "start", behavior: "smooth" });
      try {
        const record = await call(`${API}/search`, { method: "POST", body: { engine: S.engineId, params, options, label: S.label.trim() } });
        if (!alive() || token !== S.runToken) { refreshHistorySoon(); return; }
        afterRecord(record);
      } catch (error) {
        if (!alive() || token !== S.runToken) return;
        await showFailure(error);
        if (error.code === "not_configured") { S.overview = { ...(S.overview || {}), configured: false }; paintOverview(); }
      } finally {
        if (alive() && token === S.runToken) { S.running = false; paintRunning(); }
      }
    }

    /** Show a record that just came back from the server (search, rerun, saved run) and do the bookkeeping. */
    function afterRecord(record) {
      showRecord(record);
      syncUrl();
      refreshHistorySoon();
      refreshAccountSoon(record);
      announce(record.status === "running" ? "Search started. Waiting for the result." : `${record.engine_name || record.engine} finished: ${plural(record.result_count || 0, "result")}${record.cache_hit ? ", served from cache" : ""}.`);
      if (record.status === "running") pollRecord(record.id, 0);
    }
    function pollRecord(id, attempt) {
      clearTimeout(S.pollTimer);
      if (attempt > 60) return;
      S.pollTimer = setTimer(async () => {
        if (!alive() || !S.record || S.record.id !== id) return;
        try {
          const next = await call(`${API}/searches/${id}`);
          if (!alive() || !S.record || S.record.id !== id) return;
          if (next.status === "running") { pollRecord(id, attempt + 1); return; }
          showRecord(next);
          refreshHistorySoon();
          announce(`Search finished with ${plural(next.result_count || 0, "result")}.`);
        } catch (error) { Aira.reportError(error); }
      }, 2000);
    }
    async function refreshAccountSoon(record) {
      if (record.cache_hit || record.status === "error") return;
      try {
        const info = await call(`${API}/account?refresh=1`);
        if (alive()) { S.overview = { ...(S.overview || {}), configured: true, account: info }; paintOverview(); }
      } catch { /* the plan line is a convenience */ }
    }

    async function openRecord(id, { selectEngineToo = false } = {}) {
      try {
        const record = await call(`${API}/searches/${id}`);
        if (!alive()) return;
        if (selectEngineToo && record.engine !== S.engineId) await selectEngine(record.engine, { params: record.params, options: withoutCacheSkip(record.options), silent: true });
        showRecord(record);
        syncUrl();
        if (record.status === "running") pollRecord(record.id, 0);
        if (window.innerWidth < 1100) dom.result.scrollIntoView({ block: "start", behavior: "smooth" });
      } catch (error) { Aira.reportError(error); }
    }
    async function rerun(id) {
      if (S.running) return;
      const token = ++S.runToken;
      S.running = true;
      paintRunning();
      showLoading();
      try {
        const record = await call(`${API}/searches/${id}/rerun`, { method: "POST", body: {} });
        if (!alive() || token !== S.runToken) return;
        if (record.engine !== S.engineId) await selectEngine(record.engine, { params: record.params, options: withoutCacheSkip(record.options), silent: true });
        afterRecord(record);
      } catch (error) {
        if (alive() && token === S.runToken) await showFailure(error);
      } finally {
        if (alive() && token === S.runToken) { S.running = false; paintRunning(); }
      }
    }

    // ---- engine picker ---------------------------------------------------------------------------------------------------

    /** Relevance of an engine to the typed words, or -1 when a word matches nothing. Names and ids beat family, parameters and prose. */
    function rank(engine, tokens) {
      const id = engine.id.toLowerCase();
      const name = engine.name.toLowerCase();
      const words = `${name} ${id.replaceAll("_", " ")}`.split(/[\s/&-]+/);
      const labels = `${engine.family} ${engine.category.replaceAll("_", " ")}`.toLowerCase();
      const params = (engine.required || []).join(" ").toLowerCase();
      const desc = `${engine.description || ""} ${engine.aira_use || ""}`.toLowerCase();
      let score = 0;
      for (const token of tokens) {
        if (id === token || name === token) score += 100;
        else if (words.some((word) => word.startsWith(token))) score += 80;
        else if (id.includes(token) || name.includes(token)) score += 65;
        else if (labels.includes(token)) score += 40;
        else if (params.includes(token)) score += 30;
        else if (desc.includes(token)) score += 15;
        else return -1;
      }
      return score - (engine.deprecated ? 5 : 0) - (engine.unlisted ? 3 : 0) - Math.min(engine.name.length, 40) / 100;
    }

    function openPicker() {
      if (dom.picker || !catalog.list) return;
      const all = catalog.list.engines;
      const listId = "pg-pick-list";
      let query = "";
      let category = "";
      let rows = [];
      let cursor = 0;

      const input = h("input", { type: "search", class: "pg-pick-input", role: "combobox", "aria-expanded": "true", "aria-controls": listId, "aria-autocomplete": "list", "aria-label": "Search engines", placeholder: `Search ${all.length} engines by name, id or need`, autocomplete: "off", spellcheck: "false" });
      const status = h("div", { class: "visually-hidden", role: "status", "aria-live": "polite" });
      const list = h("div", { class: "pg-pick-list", id: listId, role: "listbox", "aria-label": "Engines" });
      const chips = h("div", { class: "pg-pick-cats", role: "group", "aria-label": "Filter by category" });
      const popover = h("div", { class: "pg-popover", role: "dialog", "aria-label": "Choose an engine" },
        h("div", { class: "pg-pick-head" }, icon("search"), input, btn("Close engine list", { icon: "close", kind: "ghost", iconOnly: true, onClick: () => close(true) })),
        chips, list, status,
        h("div", { class: "pg-pick-foot" }, h("span", {}, h("span", { class: "kbd", text: "↑↓" }), " move  ", h("span", { class: "kbd", text: "↵" }), " open  ", h("span", { class: "kbd", text: "Alt F" }), " favorite  ", h("span", { class: "kbd", text: "Esc" }), " close")));

      const select = async (id) => {
        close(false);
        const ok = id === S.engineId ? true : await selectEngine(id);
        if (ok) { setLeftTab("params"); if (S.form) S.form.focusFirst(); }
      };

      const optionRow = (engine, index) => {
        const favorite = favorites().includes(engine.id);
        const row = h("div", { class: "pg-opt", role: "option", id: `pg-opt-${index}`, "aria-selected": "false", dataset: { id: engine.id, index: String(index) }, onclick: () => select(engine.id) },
          serp().engineBadge(engine),
          h("code", { class: "pg-opt-id", text: engine.id }),
          engine.id === S.engineId ? h("span", { class: "sk-chip tone", text: "Current" }) : null,
          engine.deprecated ? h("span", { class: "sk-chip warn", text: "Deprecated" }) : null,
          h("span", { class: "pg-opt-meta", text: `${engine.param_count} params${engine.required.length ? ` · needs ${engine.required.join(", ")}` : ""}` }),
          h("button", { type: "button", class: `pg-star${favorite ? " on" : ""}`, tabindex: "-1", "aria-pressed": String(favorite), "aria-label": `Favorite ${engine.name}`, title: favorite ? "Remove from favorites" : "Add to favorites", onclick: (event) => { event.stopPropagation(); toggleFavorite(engine.id); paint(); input.focus(); } }, icon("star")),
          h("span", { class: "pg-opt-desc", text: engine.description }));
        return row;
      };

      const sections = () => {
        const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
        const pool = all.filter((engine) => !category || engine.category === category);
        if (tokens.length) {
          const ranked = pool.map((engine) => [rank(engine, tokens), engine]).filter(([score]) => score >= 0).sort((a, b) => b[0] - a[0] || a[1].name.localeCompare(b[1].name)).map(([, engine]) => engine);
          return [{ label: `${ranked.length.toLocaleString()} ${ranked.length === 1 ? "match" : "matches"}`, items: ranked }];
        }
        const out = [];
        const byId = new Map(pool.map((engine) => [engine.id, engine]));
        const favs = favorites().map((id) => byId.get(id)).filter(Boolean);
        if (favs.length) out.push({ label: "Favorites", items: favs });
        const recent = recents().filter((id) => !favorites().includes(id)).map((id) => byId.get(id)).filter(Boolean).slice(0, 5);
        if (recent.length) out.push({ label: "Recent", items: recent });
        const families = new Map();
        for (const engine of pool) { if (!families.has(engine.family)) families.set(engine.family, []); families.get(engine.family).push(engine); }
        const order = [...families.keys()].sort((a, b) => (a === "Google" ? -1 : b === "Google" ? 1 : families.get(b).length - families.get(a).length || a.localeCompare(b)));
        for (const family of order) out.push({ label: family, items: families.get(family) });
        return out;
      };

      const paint = () => {
        const groups = sections();
        rows = [];
        const nodes = [];
        for (const group of groups) {
          const heading = h("div", { class: "pg-group", role: "presentation" }, h("span", { text: group.label }), h("span", { class: "pg-group-n", text: String(group.items.length) }));
          const body = group.items.map((engine) => { const row = optionRow(engine, rows.length); rows.push(row); return row; });
          nodes.push(h("div", { role: "group", "aria-label": group.label }, heading, ...body));
        }
        if (!rows.length) nodes.push(h("div", { class: "pg-pick-none" }, h("strong", { text: "No engine matches" }), h("p", { text: "Try a product name such as maps or shopping, or an id such as google_trends." })));
        list.replaceChildren(...nodes);
        cursor = clamp(cursor, 0, Math.max(0, rows.length - 1));
        if (!query) { const current = rows.findIndex((row) => row.dataset.id === S.engineId); if (current >= 0 && !cursorTouched) cursor = current; }
        mark(!cursorTouched && !query);
        status.textContent = rows.length ? `${plural(rows.length, "engine")}` : "No engines match";
      };
      let cursorTouched = false;
      const mark = (scroll = true) => {
        rows.forEach((row, index) => row.setAttribute("aria-selected", String(index === cursor)));
        const row = rows[cursor];
        if (row) { input.setAttribute("aria-activedescendant", row.id); if (scroll) row.scrollIntoView({ block: "nearest" }); }
        else input.removeAttribute("aria-activedescendant");
      };
      const paintChips = () => {
        const cats = [{ id: "", label: "All", count: all.length }, ...catalog.list.categories];
        chips.replaceChildren(...cats.map((cat) => h("button", { type: "button", class: `pg-chip${cat.id === category ? " on" : ""}`, "aria-pressed": String(cat.id === category), onclick: () => { category = cat.id; cursor = 0; cursorTouched = false; paintChips(); paint(); input.focus(); } }, h("span", { text: cat.label }), h("small", { text: String(cat.count) }))));
      };

      input.addEventListener("input", () => { query = input.value; cursor = 0; cursorTouched = true; paint(); });
      input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") { event.preventDefault(); cursorTouched = true; cursor = Math.min(rows.length - 1, cursor + 1); mark(); }
        else if (event.key === "ArrowUp") { event.preventDefault(); cursorTouched = true; cursor = Math.max(0, cursor - 1); mark(); }
        else if (event.key === "PageDown") { event.preventDefault(); cursorTouched = true; cursor = Math.min(rows.length - 1, cursor + 8); mark(); }
        else if (event.key === "PageUp") { event.preventDefault(); cursorTouched = true; cursor = Math.max(0, cursor - 8); mark(); }
        else if (event.key === "Enter") { event.preventDefault(); if (rows[cursor]) select(rows[cursor].dataset.id); }
        else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
        else if (event.altKey && event.key.toLowerCase() === "f") { event.preventDefault(); if (rows[cursor]) { toggleFavorite(rows[cursor].dataset.id); const keep = rows[cursor].dataset.id; paint(); const again = rows.findIndex((row) => row.dataset.id === keep); if (again >= 0) { cursor = again; mark(); } } }
      });
      popover.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); close(true); } });
      const onOutside = (event) => { if (!popover.contains(event.target) && !dom.pickButton.contains(event.target)) close(false); };
      function close(restore) {
        if (dom.picker !== popover) return;
        document.removeEventListener("pointerdown", onOutside, true);
        document.body.classList.remove("pg-sheet-open");
        popover.remove();
        dom.picker = null;
        dom.pickButton.setAttribute("aria-expanded", "false");
        if (restore) dom.pickButton.focus();
      }
      dom.picker = popover;
      dom.closePicker = close;
      dom.pickButton.setAttribute("aria-expanded", "true");
      dom.bar.append(popover);
      if (window.innerWidth <= 700) document.body.classList.add("pg-sheet-open");
      paintChips();
      paint();
      input.focus();
      setTimeout(() => document.addEventListener("pointerdown", onOutside, true), 0);
    }
    function closePicker(restore) { if (dom.picker && dom.closePicker) dom.closePicker(restore); }

    // ---- history rail -------------------------------------------------------------------------------------------------------

    function buildHistoryPane() {
      dom.historyList = h("div", { class: "pg-list", role: "list", "aria-label": "Recent searches" });
      dom.historyCount = h("span", { class: "sk-hint", role: "status" });
      const thisEngine = serp().toggleSwitch("This engine only", S.historyEngineOnly, (on) => { S.historyEngineOnly = on; Aira.remember("playground.historyEngineOnly", on); loadHistory(); }, { id: "pg-hist-engine" });
      const pinnedOnly = serp().toggleSwitch("Pinned only", false, (on) => { S.historyPinned = on; loadHistory(); }, { id: "pg-hist-pinned" });
      dom.historyPane.append(
        h("div", { class: "pg-pane-tools wrap" }, thisEngine.node, pinnedOnly.node, h("a", { class: "btn ghost sm", href: "#/airaapi?tab=history" }, icon("history"), h("span", { text: "Open full history" }))),
        dom.historyCount, dom.historyList);
    }
    async function loadHistory() {
      if (!alive()) return;
      const query = new URLSearchParams({ limit: "30" });
      if (S.historyEngineOnly && S.engineId) query.set("engine", S.engineId);
      if (S.historyPinned) query.set("pinned", "1");
      if (!S.historyLoaded) dom.historyList.replaceChildren(ui.skeleton(3));
      try {
        const data = await call(`${API}/searches?${query}`);
        if (!alive()) return;
        S.history = data.items || [];
        S.historyTotal = data.total || S.history.length;
        S.historyLoaded = true;
        paintHistory();
        paintCost();
      } catch (error) {
        if (alive()) dom.historyList.replaceChildren(ui.error(error, loadHistory));
      }
    }
    function refreshHistorySoon() { setTimer(loadHistory, 150); }

    function paintHistory() {
      dom.tabs.badge("history", S.historyTotal > 99 ? "99+" : S.historyTotal || "");
      dom.historyCount.textContent = S.historyTotal ? `${plural(S.historyTotal, "search")} stored${S.history.length < S.historyTotal ? `, newest ${S.history.length} shown` : ""}` : "";
      if (!S.history.length) {
        dom.historyList.replaceChildren(ui.empty("Nothing here yet", S.historyEngineOnly ? "Searches you run with this engine appear here. Switch off 'This engine only' to see every engine." : "Searches appear here as soon as you run them."));
        return;
      }
      dom.historyList.replaceChildren(...S.history.map(historyRow));
    }
    function historyRow(item) {
      const current = S.record && S.record.id === item.id;
      const comparable = S.record && S.record.id !== item.id && S.record.engine === item.engine && item.status !== "error" && item.output === "json" && S.record.output === "json";
      return h("div", { class: `pg-item${current ? " current" : ""}`, role: "listitem" },
        h("button", { type: "button", class: "pg-item-main", "aria-current": current ? "true" : null, onclick: () => openRecord(item.id, { selectEngineToo: true }), "aria-label": `Open ${item.engine_name || item.engine}: ${primaryValue(item.params) || "no query"}` },
          h("span", { class: "pg-item-top" }, h("strong", { text: item.engine_name || engineName(item.engine) }), serp().statusBadge(item), item.pinned ? h("span", { class: "pg-pinned", title: "Pinned", "aria-label": "Pinned" }, icon("pin")) : null, h("time", { datetime: item.created_at, text: fmt.ago(item.created_at) })),
          h("span", { class: "pg-item-sum", text: item.label || summarize(item.params) || "No parameters" }),
          h("span", { class: "pg-item-meta" }, serp().creditChip(item), item.result_count !== null && item.result_count !== undefined ? h("span", { text: plural(item.result_count, "result") }) : null, item.duration_ms && !item.cache_hit ? h("span", { text: `${Number(item.duration_ms).toLocaleString()} ms` }) : null, item.source !== "playground" ? h("span", { class: "badge source", text: item.source }) : null)),
        h("div", { class: "pg-item-actions" },
          btn("Use parameters", { icon: "load", kind: "ghost", iconOnly: true, onClick: () => editRequest(item) }),
          btn("Re-run", { icon: "refresh", kind: "ghost", iconOnly: true, onClick: () => rerun(item.id), title: "Re-run live (uses a credit)" }),
          comparable ? btn("Compare with the open result", { icon: "diff", kind: "ghost", iconOnly: true, onClick: () => openCompare(S.record, item.id) }) : null));
    }

    // ---- saved searches ---------------------------------------------------------------------------------------------------

    function buildSavedPane() {
      dom.savedList = h("div", { class: "pg-list", role: "list", "aria-label": "Saved searches" });
      dom.savedPane.append(
        h("div", { class: "pg-pane-tools wrap" },
          btn("Save current request", { icon: "bookmark", onClick: () => saveCurrent() }),
          h("a", { class: "btn ghost sm", href: "#/airaapi?tab=monitors" }, icon("bell"), h("span", { text: "Manage monitors" }))),
        dom.savedList);
    }
    async function loadSaved() {
      if (!alive()) return;
      if (!S.savedLoaded) dom.savedList.replaceChildren(ui.skeleton(3));
      try {
        const [saved, monitors] = await Promise.all([call(`${API}/saved`), call(`${API}/monitors`)]);
        if (!alive()) return;
        // rank-tracker keywords are run and changed from the Rank tracker tab, not saved or monitored here
        S.saved = (saved.saved || []).filter((item) => !item.rank_tracker);
        S.monitors = (monitors.monitors || []).filter((item) => !item.rank_tracker);
        S.savedLoaded = true;
        paintSaved();
      } catch (error) {
        if (alive()) dom.savedList.replaceChildren(ui.error(error, loadSaved));
      }
    }
    function savedMatch(engine, params) { return S.saved.find((item) => item.engine === engine && sameParams(item.params, params)) || null; }
    function monitorFor(savedId) { return S.monitors.find((item) => item.saved_id === savedId) || null; }

    function paintSaved() {
      dom.tabs.badge("saved", S.saved.length || "");
      if (!S.saved.length) {
        dom.savedList.replaceChildren(ui.empty("No saved searches", "Save a request to run it again in one click, or add a monitor to be told when its results change."));
        return;
      }
      dom.savedList.replaceChildren(...S.saved.map((item) => {
        const monitor = monitorFor(item.id);
        return h("div", { class: "pg-item", role: "listitem" },
          h("button", { type: "button", class: "pg-item-main", onclick: () => loadSavedIntoForm(item), "aria-label": `Load saved search ${item.name}` },
            h("span", { class: "pg-item-top" }, h("strong", { text: item.name }), item.pinned ? h("span", { class: "pg-pinned", "aria-label": "Pinned" }, icon("pin")) : null, h("time", { datetime: item.last_run_at || item.created_at, text: item.last_run_at ? `ran ${fmt.ago(item.last_run_at)}` : `saved ${fmt.ago(item.created_at)}` })),
            h("span", { class: "pg-item-sum", text: `${item.engine_name || engineName(item.engine)} · ${summarize(item.params)}` }),
            h("span", { class: "pg-item-meta" },
              ...(item.tags || []).map((tag) => serp().chip(tag)),
              monitor ? h("span", { class: `sk-chip ${monitor.enabled ? "good" : ""}`, title: monitor.last_change_summary || "No run yet" }, icon("bell"), h("span", { text: monitor.enabled ? `Monitored ${monitorLabel(monitor.interval_minutes).toLowerCase()}` : "Monitor paused" })) : null)),
          h("div", { class: "pg-item-actions" },
            btn("Run now", { icon: "play", kind: "ghost", iconOnly: true, onClick: () => runSaved(item) }),
            monitor ? null : btn("Add a monitor", { icon: "bell", kind: "ghost", iconOnly: true, onClick: () => openSaveDialog({ saved: item, monitor: true }) }),
            btn("Delete saved search", { icon: "trash", kind: "ghost", iconOnly: true, onClick: () => deleteSaved(item) })));
      }));
    }
    async function loadSavedIntoForm(item) {
      if (item.engine !== S.engineId) { const ok = await selectEngine(item.engine, { params: item.params }); if (!ok) return; }
      else { S.form.setValues(item.params); onFormChange(); }
      setLeftTab("params");
      S.form.focusFirst();
      announce(`Loaded ${item.name}.`);
    }
    async function runSaved(item) {
      if (S.running) return;
      const token = ++S.runToken;
      S.running = true;
      paintRunning();
      showLoading();
      try {
        const record = await call(`${API}/saved/${item.id}/run`, { method: "POST", body: {} });
        if (!alive() || token !== S.runToken) return;
        if (record.engine !== S.engineId) await selectEngine(record.engine, { params: record.params, options: withoutCacheSkip(record.options), silent: true });
        afterRecord(record);
        loadSaved();
      } catch (error) {
        if (alive() && token === S.runToken) await showFailure(error);
      } finally {
        if (alive() && token === S.runToken) { S.running = false; paintRunning(); }
      }
    }
    async function deleteSaved(item) {
      const monitor = monitorFor(item.id);
      const yes = await Aira.confirm({ title: "Delete saved search?", message: `"${item.name}" will be removed${monitor ? ", along with its monitor" : ""}. Searches already run stay in History.`, confirmLabel: "Delete", danger: true });
      if (!yes) return;
      try { await call(`${API}/saved/${item.id}`, { method: "DELETE" }); toast("Saved search deleted.", "success"); await loadSaved(); }
      catch (error) { Aira.reportError(error); }
    }
    function saveCurrent() {
      const problems = S.form.validate();
      if (problems.length) { setLeftTab("params"); return; }
      openSaveDialog({ params: S.form.getValues(), engine: S.engineId });
    }

    // ---- save and monitor dialog -------------------------------------------------------------------------------------------

    const monitorLabel = (minutes) => (MONITOR_INTERVALS.find(([value]) => value === minutes) || [0, `Every ${minutes} minutes`])[1];

    /**
     * One dialog for "save this search" and "watch it". Sources: a result record, the live form, or an existing saved
     * search (to attach a monitor). The estimate is the number of live searches a month the schedule would use.
     */
    function openSaveDialog({ record, params, engine, saved, monitor = false }) {
      const engineId = saved ? saved.engine : record ? record.engine : engine;
      const useParams = saved ? saved.params : record ? record.params : params;
      const existing = saved || savedMatch(engineId, useParams);
      const existingMonitor = existing ? monitorFor(existing.id) : null;
      const perRun = engineId === "extract" && useParams.mode === "crawl" ? clamp(Number(useParams.max_pages) || 1, 1, 1000) : 1;
      const defaultName = (record && record.label) || `${primaryValue(useParams) || "Search"} · ${engineName(engineId)}`.slice(0, 80);
      const name = h("input", { type: "text", maxlength: 80, value: defaultName, autocomplete: "off", required: true });
      const tags = h("input", { type: "text", maxlength: 120, autocomplete: "off", placeholder: "job-search, austin" });
      const watch = serp().toggleSwitch("Watch this search for changes", monitor && !existingMonitor, () => paintEstimate(), { hint: "Runs it on a schedule, compares with the last run and notifies you in Aira when results change.", id: "pg-watch" });
      const interval = ui.select(MONITOR_INTERVALS, 1440, { "aria-label": "How often" });
      interval.value = "1440";
      const notify = serp().toggleSwitch("Notify me when results change", true, () => {}, { id: "pg-notify" });
      const estimate = h("div", { class: "pg-estimate", "aria-live": "polite" });
      const problem = h("div", { class: "sk-err", role: "alert", hidden: true });
      const monitorBox = h("div", { class: "pg-monitor-box" }, ui.field("How often", interval), notify.node, estimate);
      // attached only while the switch is on: hidden controls would still be the focus trap's first stop
      const monitorSlot = h("div");
      function paintEstimate() {
        const on = watch.get() && !existingMonitor;
        monitorSlot.replaceChildren(...(on ? [monitorBox] : []));
        if (!on) return;
        const minutes = Number(interval.value);
        const monthly = Math.ceil((30 * 24 * 60) / minutes) * perRun;
        const info = account();
        const lines = [h("strong", { text: `About ${monthly.toLocaleString()} search${monthly === 1 ? "" : "es"} a month` }), h("span", { text: ` (${Number.isInteger(monthly / 30) ? monthly / 30 : (monthly / 30).toFixed(monthly >= 60 ? 0 : 1)} a day). Monitor runs always call the engine live, so each one uses a credit.` })];
        const nodes = [h("p", {}, lines)];
        if (info && Number(info.searches_per_month) > 0) {
          const share = Math.round((monthly / Number(info.searches_per_month)) * 100);
          const left = Number(info.total_searches_left);
          const tooMuch = Number.isFinite(left) && monthly > left;
          nodes.push(h("p", { class: tooMuch ? "pg-warn" : "sk-hint", text: `${share}% of your ${Number(info.searches_per_month).toLocaleString()}-search monthly plan${tooMuch ? `, more than the ${left.toLocaleString()} left now` : ""}.` }));
        } else nodes.push(h("p", { class: "sk-hint", text: "Connect a SerpApi key on the Account tab to compare this with your plan." }));
        estimate.replaceChildren(...nodes);
      }
      interval.addEventListener("change", paintEstimate);
      const body = h("div", { class: "pg-dialog" },
        h("p", { class: "muted", text: `${engineName(engineId)} · ${summarize(useParams, 4) || "no parameters"}` }),
        existing ? h("div", { class: "sk-note" }, icon("info"), h("span", { text: `Already saved as "${existing.name}".${existingMonitor ? ` It is monitored ${monitorLabel(existingMonitor.interval_minutes).toLowerCase()}; manage it on the Monitors tab.` : ""}` })) : h("div", { class: "pg-dialog-fields" }, ui.field("Name", name), ui.field("Tags (optional)", tags, "Comma separated.")),
        existingMonitor ? null : watch.node, monitorSlot, problem);
      paintEstimate();
      const dialog = Aira.modal({
        title: existing && !monitor ? "Saved search" : monitor ? "Monitor this search" : "Save this search",
        body,
        actions: [
          { label: "Cancel", kind: "ghost" },
          { label: existing ? (existingMonitor || !watch.get() ? "Done" : "Start monitor") : watch.get() ? "Save and monitor" : "Save search", kind: "", close: false, onClick: async (close) => {
            problem.hidden = true;
            try {
              let target = existing;
              if (!target) {
                if (!name.value.trim()) { problem.textContent = "Give the saved search a name."; problem.hidden = false; name.focus(); return false; }
                target = await call(`${API}/saved`, { method: "POST", body: { name: name.value.trim(), engine: engineId, params: useParams, tags: tags.value, from_search_id: record && record.status === "ok" ? record.id : "" } });
              }
              let created = null;
              if (watch.get() && !existingMonitor) created = await call(`${API}/monitors`, { method: "POST", body: { saved_id: target.id, interval_minutes: Number(interval.value), notify_on_change: notify.get() } });
              close();
              toast(created ? `Saved and monitoring ${monitorLabel(created.interval_minutes).toLowerCase()}.` : existing ? "Nothing to change." : "Search saved.", "success");
              await loadSaved();
              announce(created ? "Monitor started." : "Search saved.");
            } catch (error) {
              problem.textContent = error.message;
              problem.hidden = false;
              return false;
            }
          } },
        ],
      });
      // the primary label follows the switch
      const primary = dialog.box.querySelector(".actions .btn:not(.ghost)");
      const relabel = () => { if (!primary) return; const on = watch.get() && !existingMonitor; primary.textContent = existing ? (on ? "Start monitor" : "Done") : on ? "Save and monitor" : "Save search"; };
      watch.node.addEventListener("change", relabel);
      relabel();
      // Enter in a text field saves, like any one-question form
      for (const field of [name, tags]) field.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.isComposing && primary) { event.preventDefault(); primary.click(); } });
    }

    // ---- compare -----------------------------------------------------------------------------------------------------------

    async function openCompare(record, otherId = "") {
      if (record.output !== "json") { toast("Only JSON searches can be compared.", "error"); return; }
      let candidates = [];
      try {
        const data = await call(`${API}/searches?engine=${encodeURIComponent(record.engine)}&limit=60`);
        candidates = (data.items || []).filter((item) => item.id !== record.id && item.status !== "error" && item.output === "json" && item.status !== "running");
      } catch (error) { Aira.reportError(error); return; }
      if (!candidates.length) { toast("There is no other finished run of this engine to compare with yet. Run it again later and compare.", ""); return; }
      const olderFirst = (item) => new Date(item.created_at).getTime() <= new Date(record.created_at).getTime();
      const sameKind = candidates.filter((item) => sameParams(item.params, record.params));
      const choice = candidates.find((item) => item.id === otherId) || sameKind.find(olderFirst) || sameKind[0] || candidates.find(olderFirst) || candidates[0];
      const select = h("select", { "aria-label": "Run to compare with", class: "pg-compare-select" }, candidates.map((item) => h("option", { value: item.id, selected: item.id === choice.id, text: `${fmt.datetime(item.created_at)} · ${summarize(item.params, 2) || item.engine_name}${item.cache_hit ? " · cached" : ""}${sameParams(item.params, record.params) ? "" : " · different parameters"}` })));
      const result = h("div", { class: "pg-diff", "aria-live": "polite" });
      const load = async () => {
        result.replaceChildren(ui.skeleton(3));
        const other = candidates.find((item) => item.id === select.value);
        const [earlier, later] = new Date(other.created_at) <= new Date(record.created_at) ? [other, record] : [record, other];
        try {
          const diff = await call(`${API}/diff?a=${earlier.id}&b=${later.id}`);
          result.replaceChildren(diffView(diff, earlier, later));
        } catch (error) { result.replaceChildren(ui.error(error, load)); }
      };
      select.addEventListener("change", load);
      Aira.drawer({
        title: "Compare runs",
        subtitle: `${record.engine_name || engineName(record.engine)} · the open result against`,
        body: h("div", {}, h("div", { class: "pg-compare-pick" }, select), result),
      });
      load();
    }

    function diffView(diff, earlier, later) {
      const counts = diff.counts || {};
      const wrap = h("div", { class: "pg-diff-body" });
      wrap.append(h("div", { class: "pg-diff-sum" },
        h("strong", { text: diff.summary }),
        h("div", { class: "sk-chips" }, h("span", { class: "sk-chip good", text: `+${counts.added || 0} added` }), h("span", { class: "sk-chip warn", text: `−${counts.removed || 0} removed` }), h("span", { class: "sk-chip", text: `${counts.changed || 0} changed` })),
        h("p", { class: "sk-hint", text: `Earlier ${fmt.datetime(earlier.created_at)} → later ${fmt.datetime(later.created_at)}${diff.list_key ? ` · compared by ${diff.list_key}` : ""}` })));
      if (!diff.changed_any) { wrap.append(h("div", { class: "sk-note" }, icon("check"), h("span", { text: "The two results are identical apart from timestamps and ids." }))); return wrap; }
      const items = (list, label, tone) => {
        if (!list || !list.length) return null;
        return serp().section(label, { count: list.length, tone, body: h("div", { class: "pg-diff-list" }, list.slice(0, 60).map((item) => h("details", { class: "pg-diff-item" },
          h("summary", {}, h("span", { text: serp().logic.titleOfItem(item) || (typeof item === "string" ? item : "Item") }), item && item.link ? h("small", { text: (() => { try { return new URL(item.link).hostname.replace(/^www\./, ""); } catch { return ""; } })() }) : null),
          serp().jsonTree(item, { maxDepth: 1, search: false, label: label })))) });
      };
      wrap.append(...[items(diff.added, "Added", "good"), items(diff.removed, "Removed", "warn")].filter(Boolean));
      if (diff.changed && diff.changed.length) {
        const shorten = (value) => { const text = value === null || value === undefined ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value); return text.length > 140 ? `${text.slice(0, 140)}…` : text; };
        wrap.append(serp().section("Changed", { count: diff.changed.length, body: h("div", { class: "table-wrap" }, h("table", { class: "table pg-diff-table" },
          h("thead", {}, h("tr", {}, h("th", { text: "Where" }), h("th", { text: "Before" }), h("th", { text: "After" }))),
          h("tbody", {}, diff.changed.slice(0, 120).map((row) => h("tr", {}, h("td", {}, h("code", { text: row.path })), h("td", { class: "pg-before", text: shorten(row.before) }), h("td", { class: "pg-after", text: shorten(row.after) })))))) }));
      }
      return wrap;
    }

    // ---- shortcuts ---------------------------------------------------------------------------------------------------------

    function showShortcuts() {
      const mod = isMac ? "⌘" : "Ctrl";
      const rows = [[`${mod} Enter`, "Run the search"], [`${mod} K  or  E`, "Choose another engine"], ["Alt 1 to 5", "Parameters, Options, Code, History, Saved"], ["↑ ↓ in the engine list", "Move; Enter opens, Alt F favorites"], ["← → on the result tabs", "Switch between Visual, JSON, Table, HTML, Raw, Metadata, Snippets"], ["Esc", "Close the engine list or a dialog"]];
      Aira.modal({ title: "Playground shortcuts", body: h("table", { class: "table" }, h("tbody", {}, rows.map(([key, label]) => h("tr", {}, h("td", {}, h("span", { class: "kbd", text: key })), h("td", { text: label }))))), actions: [{ label: "Close", kind: "" }] });
    }

    function onKeydown(event) {
      if (!alive()) { if (!content.isConnected) dispose(); return; }
      const modal = document.querySelector("#overlay .modal-backdrop, #overlay .drawer");
      const key = event.key;
      const mod = event.ctrlKey || event.metaKey;
      if (mod && key === "Enter" && !event.defaultPrevented) { if (!modal) { event.preventDefault(); run(); } return; }
      if (mod && !event.shiftKey && !event.altKey && key.toLowerCase() === "k") { event.preventDefault(); if (!modal) { if (dom.picker) closePicker(true); else openPicker(); } return; }
      if (modal) return;
      if (event.altKey && !mod && /^[1-5]$/.test(key)) { event.preventDefault(); setLeftTab(["params", "options", "code", "history", "saved"][Number(key) - 1], { focus: true }); return; }
      const tag = (event.target.tagName || "").toLowerCase();
      const typing = ["input", "textarea", "select"].includes(tag) || event.target.isContentEditable;
      if (!typing && !mod && !event.altKey && key.toLowerCase() === "e") { event.preventDefault(); openPicker(); }
    }

    // ---- lifecycle ---------------------------------------------------------------------------------------------------------

    async function start() {
      content.replaceChildren(ui.skeleton(5));
      try {
        await loadEngines();
      } catch (error) {
        if (alive()) content.replaceChildren(ui.error(error, () => Aira.renderPlayground(content, route)));
        return;
      }
      if (!alive()) return;
      try { S.overview = await call(`${API}/overview`); } catch { S.overview = null; }
      if (!alive()) return;
      build();
      document.addEventListener("keydown", onKeydown);
      // leaving the page (a different API tab or section) disposes the mount once the router has replaced the content
      const onHash = () => setTimeout(() => { if (active === api && !content.isConnected) dispose(); }, 0);
      window.addEventListener("hashchange", onHash);
      cleanups.push(() => { document.removeEventListener("keydown", onKeydown); window.removeEventListener("hashchange", onHash); });
      const state = queryToState(route.query);
      const known = (id) => !!engineSummary(id);
      let engineId = state.engine;
      let fromLink = !!engineId;
      if (engineId && !known(engineId)) { toast(`Unknown engine "${engineId}". Showing a default instead.`, "error"); engineId = ""; fromLink = false; }
      if (!engineId) { const last = Aira.recall("playground.engine", ""); engineId = known(last) ? last : known(DEFAULT_ENGINE) ? DEFAULT_ENGINE : catalog.list.engines[0].id; }
      const ok = await selectEngine(engineId, { params: fromLink ? state.params : undefined, options: fromLink ? state.options : undefined, silent: true });
      if (!ok || !alive()) return;
      showEmpty();
      if (state.record) await openRecord(state.record);
      loadHistory();
      loadSaved();
    }

    function dispose() {
      for (const id of timers) clearTimeout(id);
      timers.clear();
      clearTimeout(S.pollTimer);
      clearTimeout(S.urlTimer);
      S.runToken += 1;
      S.selectToken += 1;
      if (dom.closePicker) dom.closePicker(false);
      document.body.classList.remove("pg-sheet-open");
      for (const fn of cleanups) fn();
      cleanups.length = 0;
    }

    return api;
  }

  Aira.renderPlayground = async (content, route) => {
    if (active) active.dispose();
    const playground = createPlayground(content, route || { path: ["airaapi"], query: {} });
    active = playground;
    await playground.start();
  };

  // Exposed for the node-free tests in the browser harness and for other views that build requests.
  Aira.playground = { exampleFor, stateToQuery, queryToState, summarize };
})();

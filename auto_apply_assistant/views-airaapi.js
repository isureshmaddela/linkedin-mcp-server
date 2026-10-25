/* Aira — API console: overview, engines, history, locations, monitors, account, keys, docs and status.
 *
 * The console is one view (#/airaapi) with a tab per page (?tab=). Every tab is a function in Aira.airaapi.tabs
 * taking (content, ctx) with ctx = {route, query, tab, content, setQuery(patch)}. The Playground tab hands its
 * content node to Aira.renderPlayground (views-playground.js); another view can add or replace a tab the same way:
 *
 *   Aira.airaapi.tabs.mytab = async (content, ctx) => { ... }   // and add it to Aira.airaapi.TABS
 *
 * Links between pages are plain hashes built with Aira.airaapi.link() and Aira.airaapi.playgroundLink() (which
 * defers to Aira.playgroundLink), so any view can point at a tab, an engine page or a stored search. Aira.airaapi.openRecord(id)
 * opens the detail drawer of a stored search from anywhere.
 *
 * Everything the server sends is put on the page with textContent (the h() helper); the SerpApi key is never
 * read back, rendered or kept in the DOM after a save.
 */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, router } = Aira;
  const serp = Aira.serp;
  const BASE = "/api/airaapi";
  const CAREERS = "/api/careers";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const SERPAPI_LINKS = {
    dashboard: "https://serpapi.com/dashboard",
    plan: "https://serpapi.com/plan",
    pricing: "https://serpapi.com/pricing",
    status: "https://serpapi.com/status",
    incidents: "https://status.serpapi.com",
    releases: "https://serpapi.com/release-notes",
    integrations: "https://serpapi.com/integrations",
  };

  const TABS = [
    ["overview", "Overview"],
    ["playground", "Playground"],
    ["engines", "Engines"],
    ["history", "History"],
    ["locations", "Locations"],
    ["monitors", "Monitors & saved"],
    ["account", "Account"],
    ["keys", "API keys"],
    ["docs", "Docs"],
    ["status", "Status"],
  ];
  const TAB_IDS = TABS.map(([id]) => id);
  const tabs = {};

  // ===========================================================================
  // Small helpers
  // ===========================================================================

  const svg = (tag, attrs = {}, ...children) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "text") node.textContent = value;
      else node.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) if (child) node.append(child);
    return node;
  };

  const ICON_PATHS = {
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    pin: '<path d="M12 17v5M8 3h8l-1 6 3 4H6l3-4z"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>',
    compare: '<path d="M7 4v16M17 4v16M3 8h8M13 16h8"/>',
    play: '<path d="M8 5.5v13l11-6.5z"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    left: '<path d="m15 6-6 6 6 6"/>',
    right: '<path d="m9 6 6 6-6 6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
    book: '<path d="M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h10"/>',
    pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    shield: '<path d="M12 3 5 6v6c0 4.2 2.9 7.3 7 9 4.1-1.7 7-4.8 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    save: '<path d="M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6"/>',
    filter: '<path d="M4 5h16l-6 8v6l-4-2v-4z"/>',
  };
  function ico(name) {
    return h("span", { class: "sk-icon", "aria-hidden": "true", html: `<svg viewBox="0 0 24 24" focusable="false">${ICON_PATHS[name] || ""}</svg>` });
  }

  /** Button or link with an optional icon. kind: "" (primary), "secondary", "ghost", "danger". */
  function btn(label, { icon, kind = "secondary", sm = false, onClick, title, href, external = false, disabled = false, aria } = {}) {
    const attrs = { class: `btn ${kind}${sm ? " sm" : ""}`.replace(/\s+/g, " ").trim(), title: title || null, "aria-label": aria || null, onclick: onClick || null };
    const children = [icon ? ico(icon) : null, label ? h("span", { text: label }) : null];
    if (href) {
      attrs.href = href;
      if (external) { attrs.target = "_blank"; attrs.rel = "noopener noreferrer"; }
      return h("a", attrs, ...children);
    }
    attrs.type = "button";
    attrs.disabled = disabled || null;
    return h("button", attrs, ...children);
  }
  const externalLink = (url, text, attrs = {}) => h("a", { href: url, target: "_blank", rel: "noopener noreferrer", ...attrs }, text, h("span", { class: "visually-hidden", text: " (opens in a new tab)" }));

  /** Runs fn while the button shows a busy state; keeps its icon and label (Aira.withBusy would flatten them). */
  async function run(button, fn) {
    if (button && button.getAttribute("aria-busy") === "true") return undefined;
    if (button) { button.setAttribute("aria-busy", "true"); button.disabled = true; }
    try { return await fn(); }
    catch (error) { Aira.reportError(error); return false; }
    finally { if (button) { button.removeAttribute("aria-busy"); button.disabled = false; } }
  }

  /** replaceChildren that skips empty slots (the native method would print "null"). */
  const fill = (node, ...children) => node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));

  const clean = (obj) => Object.fromEntries(Object.entries(obj || {}).filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== false));
  const link = (tab, query = {}) => `#/airaapi?${new URLSearchParams({ tab, ...clean(query) })}`;
  function playgroundLink(engine, params = {}, options = {}) {
    // The Playground owns how a request is written into the URL; use its builder once it has loaded.
    if (typeof Aira.playgroundLink === "function") return Aira.playgroundLink(engine, params, options);
    const flat = {};
    for (const [key, value] of Object.entries(params || {})) {
      if (value === null || value === undefined || value === "") continue;
      flat[key] = typeof value === "object" ? JSON.stringify(value) : String(value);
    }
    return link("playground", { engine, ...flat });
  }
  const qs = (params) => new URLSearchParams(clean(params)).toString();

  /** Replaces the query string of the current page without re-rendering it (typing in a filter must not rebuild the page). */
  function setQuery(patch) {
    const current = router.parse().query;
    const next = clean({ ...current, ...patch });
    for (const [key, value] of Object.entries(patch)) if (value === "" || value === null || value === undefined || value === false) delete next[key];
    const tab = next.tab || "overview";
    delete next.tab;
    history.replaceState(null, "", `#/airaapi?${new URLSearchParams({ tab, ...next })}`);
    if (router.current) router.current.query = { tab, ...next };
  }

  const num = (n) => (n === null || n === undefined || Number.isNaN(Number(n)) ? "–" : Number(n).toLocaleString());
  const compactNum = (n) => new Intl.NumberFormat([], { notation: "compact", maximumFractionDigits: 1 }).format(Number(n) || 0);
  const ms = (n) => (!n ? "–" : n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(n < 10000 ? 1 : 0)} s`);
  const percent = (ratio, digits = 0) => (ratio === null || ratio === undefined ? "–" : `${(ratio * 100).toFixed(digits)}%`);
  const pluralize = (n, word, many) => `${num(n)} ${n === 1 ? word : many || `${word}s`}`;
  const dollars = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString([], { style: "currency", currency: "USD", maximumFractionDigits: 0 }));
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

  function relativeFuture(iso) {
    if (!iso) return "–";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const seconds = (date.getTime() - Date.now()) / 1000;
    if (seconds <= 30) return "due now";
    if (seconds < 3600) return `in ${Math.max(1, Math.round(seconds / 60))} min`;
    if (seconds < 86400) return `in ${Math.round(seconds / 3600)} h`;
    return `in ${Math.round(seconds / 86400)} d`;
  }
  function uptimeText(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days) return `${days} d ${hours} h`;
    if (hours) return `${hours} h ${minutes} min`;
    return `${minutes} min`;
  }
  const timeNode = (iso, { future = false } = {}) => (iso
    ? h("time", { datetime: iso, title: fmt.datetime(iso), text: future ? relativeFuture(iso) : fmt.ago(iso) })
    : h("span", { class: "sk-dim", text: "never" }));
  const intervalLabel = (minutes) => {
    const m = Number(minutes);
    if (!m) return "–";
    if (m % 10080 === 0) return m === 10080 ? "Weekly" : `Every ${m / 10080} weeks`;
    if (m % 1440 === 0) return m === 1440 ? "Daily" : `Every ${m / 1440} days`;
    if (m % 60 === 0) return m === 60 ? "Hourly" : `Every ${m / 60} h`;
    return `Every ${m} min`;
  };

  /** Runs fn every ms while node is attached to the page; stops by itself once the view is gone. */
  function every(ms, node, fn) {
    const timer = setInterval(() => {
      if (!node.isConnected) { clearInterval(timer); return; }
      if (document.hidden) return;
      fn();
    }, ms);
    return () => clearInterval(timer);
  }
  const debounce = (fn, wait) => {
    let timer = null;
    const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
    wrapped.cancel = () => clearTimeout(timer);
    return wrapped;
  };

  // ---- records ---------------------------------------------------------------------------------------------------

  const MAIN_PARAM_KEYS = ["q", "query", "search_query", "keywords", "text", "k", "p", "search_term", "product_id", "place_id", "data_id", "video_id", "url", "id", "departure_id", "title"];
  function querySummary(record) {
    const params = (record && record.params) || {};
    for (const key of MAIN_PARAM_KEYS) if (typeof params[key] === "string" && params[key]) return params[key];
    for (const key of MAIN_PARAM_KEYS) if (typeof params[key] === "number") return String(params[key]);
    const first = Object.entries(params).find(([, v]) => typeof v === "string" && v);
    return first ? first[1] : "";
  }
  function recordTitle(record) {
    return (record.label && !/^Monitor: /.test(record.label) ? record.label : "") || querySummary(record) || record.label || record.engine_name || record.engine;
  }
  function paramChips(record, max = 4) {
    const params = (record && record.params) || {};
    const main = MAIN_PARAM_KEYS.find((key) => typeof params[key] === "string" && params[key]);
    const entries = Object.entries(params).filter(([key, value]) => key !== main && value !== "" && value !== null && value !== undefined);
    const chips = entries.slice(0, max).map(([key, value]) => serp.chip(`${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value).slice(0, 40)}`));
    if (entries.length > max) chips.push(serp.chip(`+${entries.length - max} more`));
    return chips;
  }
  /** A match excerpt from the server: matched words arrive between \u0002 and \u0003 and become <mark> text nodes (never parsed as HTML). */
  function excerptNode(excerpt) {
    const parts = String(excerpt).split(/[\u0002\u0003]/);
    return h("div", { class: "aa-excerpt", title: "Found inside the stored result" }, ico("search"),
      h("span", {}, ...parts.map((part, index) => (index % 2 ? h("mark", { text: part }) : part))));
  }

  // ---- engines ---------------------------------------------------------------------------------------------------

  const engineState = { list: null, pending: null, detail: new Map() };
  /** The engine catalog summary ({engines, categories, families, total}); fetched once and shared by every tab. */
  function loadEngines({ force = false } = {}) {
    if (force) { engineState.list = null; engineState.pending = null; }
    if (engineState.list) return Promise.resolve(engineState.list);
    if (!engineState.pending) {
      engineState.pending = api(`${BASE}/engines`).then((data) => { engineState.list = data; return data; }).finally(() => { engineState.pending = null; });
    }
    return engineState.pending;
  }
  async function loadEngine(id) {
    if (!engineState.detail.has(id)) engineState.detail.set(id, await api(`${BASE}/engines/${encodeURIComponent(id)}`));
    return engineState.detail.get(id);
  }
  const engineById = (id) => (engineState.list ? engineState.list.engines.find((e) => e.id === id) : null);
  const engineLabel = (id) => { const found = engineById(id); return found ? found.name : serp.humanize(id); };
  const categoryLabel = (id) => { const found = engineState.list && engineState.list.categories.find((c) => c.id === id); return found ? found.label : serp.humanize(id); };

  /** <select> of every engine, grouped by category. */
  function engineSelect(list, value, { any = "", attrs = {} } = {}) {
    const select = h("select", attrs);
    if (any) select.append(h("option", { value: "", text: any }));
    const byCategory = new Map();
    for (const engine of list.engines) {
      if (!byCategory.has(engine.category)) byCategory.set(engine.category, []);
      byCategory.get(engine.category).push(engine);
    }
    for (const category of list.categories) {
      const engines = byCategory.get(category.id);
      if (!engines) continue;
      select.append(h("optgroup", { label: category.label }, ...engines.map((e) => h("option", { value: e.id, text: e.name + (e.deprecated ? " (deprecated)" : ""), selected: e.id === value }))));
    }
    if (value) select.value = value;
    return select;
  }

  // ===========================================================================
  // Shared states and panels
  // ===========================================================================

  function setupPanel({ compact = false } = {}) {
    const step = (n, title, body) => h("li", { class: "aa-step" }, h("span", { class: "aa-step-n", "aria-hidden": "true", text: String(n) }), h("div", {}, h("strong", { text: title }), h("p", {}, body)));
    return h("section", { class: `aa-setup${compact ? " compact" : ""}`, "aria-labelledby": "aaSetupTitle" },
      h("div", { class: "aa-setup-head" },
        h("span", { class: "aa-setup-mark", "aria-hidden": "true" }, ico("key")),
        h("div", {},
          h("h2", { id: "aaSetupTitle", text: "Connect your SerpApi key" }),
          h("p", { class: "muted", text: "The console runs searches through your own SerpApi account. Until a key is connected you can still browse engines, read the docs and look up locations." }))),
      h("ol", { class: "aa-steps" },
        step(1, "Get a key", ["Sign in at ", externalLink(SERPAPI_LINKS.dashboard, "serpapi.com"), " and copy the private API key from your dashboard. The free plan includes 250 searches a month."]),
        step(2, "Paste it into Aira", "Aira checks the key with SerpApi first, then stores it encrypted on this computer. It is never shown again, never logged and never sent to the browser."),
        step(3, "Run a search", "Open the Playground, pick any of the engines and press Ctrl+Enter. Identical searches inside the cache window are free.")),
      h("div", { class: "btn-row" },
        btn("Connect key", { icon: "key", kind: "", href: link("account") }),
        btn("Browse engines", { icon: "search", href: link("engines") }),
        btn("Read the docs", { icon: "book", kind: "ghost", href: link("docs") })),
      h("p", { class: "sk-hint", text: "Prefer a server-wide key? Set SERPAPI_API_KEY in .env and restart Aira. A key saved in Account takes precedence for that account." }));
  }

  /** An error as a card with the next step for the cases the console can recover from. */
  function failure(error, retry) {
    const code = error && error.code;
    if (code === "not_configured") return setupPanel();
    const box = ui.error(error, retry);
    const hints = {
      invalid_api_key: ["SerpApi rejected the stored key. ", h("a", { href: link("account"), text: "Replace it in Account" }), "."],
      out_of_searches: ["The SerpApi account has no searches left. Cached results still work. ", h("a", { href: link("account"), text: "Check the plan in Account" }), "."],
      rate_limited: ["SerpApi is throttling this account. Wait a few minutes before the next live search."],
      local_rate_limit: ["Aira's own hourly limit stopped this search. You can raise it in ", h("a", { href: link("account"), text: "Account, Local limits" }), "."],
    };
    if (hints[code]) box.insertBefore(h("p", { class: "muted aa-hint" }, ...hints[code]), box.querySelector(".btn-row"));
    else if (error && error.status === 0) box.insertBefore(h("p", { class: "muted aa-hint", text: "Check that Aira is still running on this computer, then try again." }), box.querySelector(".btn-row"));
    return box;
  }

  function panel(title, { sub, aside, body, cls = "" } = {}) {
    return h("section", { class: `panel ${cls}`.trim() },
      h("div", { class: "panel-head" }, h("div", {}, h("h3", { text: title }), sub ? h("small", { class: "muted", text: sub }) : null), aside || null),
      body);
  }
  const panelBody = (...children) => h("div", { class: "panel-body" }, ...children);

  /** Loading placeholder shaped like content, not a spinner. */
  const loading = (rows = 4) => ui.skeleton(rows);

  /** Horizontal meter with text equivalents; tone changes with how much is left. */
  function meter({ value, max, label, tone, valueText }) {
    const ratio = max > 0 ? clamp(value / max, 0, 1) : 0;
    return h("div", { class: `aa-meter ${tone || ""}`.trim(), role: "meter", "aria-valuemin": "0", "aria-valuemax": String(max || 0), "aria-valuenow": String(value || 0), "aria-label": label, "aria-valuetext": valueText || `${num(value)} of ${num(max)}` },
      h("i", { style: { width: `${(ratio * 100).toFixed(1)}%` } }));
  }
  const leftTone = (left, max) => { const r = max > 0 ? left / max : 1; return r <= 0.05 ? "danger" : r <= 0.2 ? "warn" : ""; };

  function statTile({ label, value, sub, href, tone }) {
    const attrs = { class: `stat aa-stat ${tone || ""}`.trim() };
    const kids = [h("span", { class: "label", text: label }), h("span", { class: "value", text: value }), sub ? h("span", { class: "sub", text: sub }) : null];
    return href ? h("a", { ...attrs, href }, ...kids) : h("div", attrs, ...kids);
  }

  /** Small key/value rows used by the detail drawer and the cards. */
  function factList(pairs) {
    return h("dl", { class: "kv aa-facts" }, ...pairs.filter(([, v]) => v !== undefined && v !== null && v !== "").flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : String(v))]));
  }
  const monoCopy = (text, label) => h("span", { class: "aa-copyable" }, h("code", { text }), serp.copyButton(text, { label: label || "Copy", message: "Copied" }));

  /** Table of text columns with a caption for screen readers; on narrow screens every row becomes a labelled card. */
  function simpleTable(caption, head, rows, { cls = "" } = {}) {
    const labels = head.map((cell) => cell.label || cell);
    const stack = !/aa-matrix/.test(cls);
    return h("div", { class: "table-wrap" }, h("table", { class: `table ${stack ? "aa-stackable" : ""} ${cls}`.replace(/\s+/g, " ").trim() },
      h("caption", { class: "visually-hidden", text: caption }),
      h("thead", {}, h("tr", {}, ...head.map((cell) => h("th", { scope: "col", class: cell.num ? "num" : null, text: cell.label || cell })))),
      h("tbody", {}, ...rows.map((cells) => h("tr", {}, ...cells.map((cell, index) => h("td", { class: head[index] && head[index].num ? "num" : null, "data-label": labels[index] || null }, cell instanceof Node ? cell : String(cell ?? ""))))))));
  }

  // ===========================================================================
  // Shell
  // ===========================================================================

  Aira.airaapi = { TABS, tabs, link, playgroundLink, loadEngines, openRecord: (id, siblings) => openDetail(id, siblings) };

  tabs.playground = async (content, ctx) => {
    if (typeof Aira.renderPlayground !== "function") {
      content.replaceChildren(ui.empty("The Playground did not load", "Reload the page. If it keeps happening, check the browser console for a script error.", btn("Reload", { kind: "", onClick: () => location.reload() })));
      return;
    }
    const host = h("div", { class: "aa-playground" });
    content.replaceChildren(host);
    await Aira.renderPlayground(host, ctx.route);
  };

  Aira.views.airaapi = async (main, route) => {
    const tab = TAB_IDS.includes(route.query.tab) ? route.query.tab : "overview";
    Aira.closeDrawer(); // a drawer from the previous tab must not survive navigation
    const label = (TABS.find(([id]) => id === tab) || TABS[0])[1];
    document.title = `${label} · API · Aira`;
    const crumbs = document.getElementById("crumbs");
    if (crumbs) crumbs.replaceChildren(h("span", { text: "API" }), h("span", { class: "muted", "aria-hidden": "true", text: "/" }), h("span", { text: label }));

    const content = h("div", { class: "aa-content", id: "aaContent", role: "region", "aria-label": label, tabindex: "-1" });
    const nav = h("nav", { class: "subnav aa-subnav", "aria-label": "API sections" },
      ...TABS.map(([id, text]) => h("a", { href: link(id), "aria-current": id === tab ? "page" : "false", text })));
    main.replaceChildren(
      h("div", { class: "page-head aa-head" },
        h("div", {}, h("h1", { text: "API" }), h("p", { text: "Every SerpApi engine plus Aira's own free Aira Jobs engine behind one console: search, inspect, compare, monitor and call them from your own code through a local, cached, SerpApi-compatible endpoint." }))),
      nav, content);
    const active = nav.querySelector('[aria-current="page"]');
    if (active && active.scrollIntoView) active.scrollIntoView({ inline: "center", block: "nearest" });

    const ctx = { route, query: route.query, tab, content, setQuery };
    const render = tabs[tab];
    if (!render) {
      content.replaceChildren(ui.empty("This page is not available", "Choose another section from the navigation above.", btn("Back to Overview", { href: link("overview"), kind: "" })));
      return;
    }
    content.replaceChildren(loading(5));
    try { await render(content, ctx); }
    catch (error) {
      console.error(error);
      content.replaceChildren(failure(error, () => router.render()));
    }
  };

  // ===========================================================================
  // Shared: JSON path picker (an X-Ray style json_restrictor builder)
  // ===========================================================================

  /** Paths a json_restrictor can address: top-level keys, and fields of the first item of each list. */
  function restrictorPaths(result) {
    const out = [];
    if (!result || typeof result !== "object") return out;
    for (const [key, value] of Object.entries(result)) {
      if (["search_metadata", "search_parameters"].includes(key)) continue;
      if (Array.isArray(value)) {
        const sample = value.find((item) => item && typeof item === "object" && !Array.isArray(item));
        out.push({ path: key, depth: 0, kind: "list", note: `${value.length} item${value.length === 1 ? "" : "s"}` });
        if (sample) for (const [field, inner] of Object.entries(sample)) out.push({ path: `${key}[].${field}`, depth: 1, kind: Array.isArray(inner) ? "list" : typeof inner === "object" && inner ? "object" : "value", note: preview(inner) });
      } else if (value && typeof value === "object") {
        out.push({ path: key, depth: 0, kind: "object", note: `${Object.keys(value).length} fields` });
        for (const [field, inner] of Object.entries(value).slice(0, 40)) out.push({ path: `${key}.${field}`, depth: 1, kind: Array.isArray(inner) ? "list" : typeof inner === "object" && inner ? "object" : "value", note: preview(inner) });
      } else out.push({ path: key, depth: 0, kind: "value", note: preview(value) });
    }
    return out;
  }
  function preview(value) {
    if (value === null || value === undefined) return "empty";
    if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? "" : "s"}`;
    if (typeof value === "object") return `${Object.keys(value).length} fields`;
    const text = String(value);
    return text.length > 48 ? `${text.slice(0, 47)}…` : text;
  }

  function restrictorBuilder(record) {
    const paths = restrictorPaths(record.result);
    const picked = new Set();
    const output = h("input", { type: "text", readonly: true, class: "sk-mono", "aria-label": "json_restrictor value", placeholder: "Tick the fields you want to keep" });
    const sizeNote = h("p", { class: "sk-hint", role: "status" });
    const update = () => {
      const value = [...picked].join(",");
      output.value = value;
      sizeNote.textContent = picked.size ? `Keeps ${picked.size} path${picked.size === 1 ? "" : "s"}. Add json_restrictor=${value.length > 70 ? `${value.slice(0, 70)}…` : value} to the request to shrink the response.` : "json_restrictor trims the response to the paths you choose, which makes it smaller and faster to download.";
    };
    update();
    const filter = h("input", { type: "search", placeholder: "Filter paths", "aria-label": "Filter paths" });
    const list = h("ul", { class: "aa-paths" });
    const paint = () => {
      const needle = filter.value.trim().toLowerCase();
      list.replaceChildren(...paths.filter((p) => !needle || p.path.toLowerCase().includes(needle) || p.note.toLowerCase().includes(needle)).map((p) => {
        const box = h("input", { type: "checkbox", checked: picked.has(p.path), "aria-label": p.path });
        box.addEventListener("change", () => { if (box.checked) picked.add(p.path); else picked.delete(p.path); update(); });
        return h("li", { class: `d${p.depth}` }, h("label", {}, box, h("code", { text: p.path }), h("span", { class: "aa-path-note", text: p.note })));
      }));
      if (!list.children.length) list.append(h("li", { class: "sk-dim", text: "No path matches." }));
    };
    filter.addEventListener("input", paint);
    paint();
    return h("div", { class: "aa-restrictor" },
      h("div", { class: "aa-restrictor-out" }, output, serp.copyButton(() => output.value, { label: "Copy json_restrictor", message: "json_restrictor copied", text: true })),
      sizeNote, filter, list);
  }

  // ===========================================================================
  // Shared: X-Ray (pixel positions from SerpApi's archive mapped to JSON paths)
  // ===========================================================================

  const XRAY_ENGINES = ["google", "google_ads"];
  const XRAY_TONES = 8;

  function openXray(record) {
    const host = h("div", { class: "aa-xray" }, loading(5));
    const modal = Aira.modal({ title: `X-Ray: ${recordTitle(record)}`, wide: true, body: host, actions: [{ label: "Close", kind: "" }] });
    modal.box.classList.add("aa-xray-modal");
    const load = async (refresh = false) => {
      host.replaceChildren(loading(5));
      try {
        const data = await api(`${BASE}/searches/${record.id}/xray${refresh ? "?refresh=1" : ""}`);
        if (!host.isConnected) return;
        paintXray(host, record, data, () => load(true));
      } catch (error) { host.replaceChildren(failure(error, () => load(refresh))); }
    };
    load();
  }

  function paintXray(host, record, data, remeasure) {
    const elements = data.elements || [];
    const info = h("p", { class: "sk-hint aa-xr-info", role: "status", text: `${pluralize(elements.length, "element")} measured on a ${num(data.viewport.width)}×${num(data.viewport.height)} viewport${data.time_taken ? ` in ${data.time_taken.toFixed(1)} s` : ""}. ${data.cached ? "Stored copy" : "Read from SerpApi's archive just now"}; X-Ray never uses a search.${data.capped ? " Only the first elements are shown." : ""}` });
    const again = btn("Measure again", { icon: "refresh", sm: true, kind: "ghost", title: "Read the pixel positions from SerpApi's archive again (free)", onClick: (event) => run(event.currentTarget, remeasure) });
    if (!elements.length) {
      host.replaceChildren(ui.empty("Nothing was measured", data.error ? `SerpApi could not measure this page: ${data.error}` : "SerpApi returned no pixel positions for this search. Pixel Position is in beta and covers organic results, local results, the answer box and other X-Ray elements.", again));
      return;
    }
    const sections = [...new Set(elements.map((e) => e.section))];
    const tone = (section) => `t${sections.indexOf(section) % XRAY_TONES}`;
    const hidden = new Set();
    const width = Math.max(data.viewport.width || 0, ...elements.map((e) => e.rect.x + e.rect.width)) || 1280;
    const height = Math.max(data.viewport.page_height || 0, ...elements.map((e) => e.rect.y + e.rect.height)) + 24;
    let selected = -1;

    // -- page map: the measured page at its real size, scaled down to fit
    const stage = h("div", { class: "aa-xr-stage", style: { width: `${width}px`, height: `${height}px` } });
    const sizer = h("div", { class: "aa-xr-sizer" }, stage);
    const map = h("div", { class: "aa-xr-map", tabindex: "-1", "aria-label": "Page map" }, sizer);
    const frameHost = h("div", { class: "aa-xr-page", "aria-hidden": "true" });
    stage.append(frameHost);
    for (const parent of data.parents || []) {
      stage.append(h("div", { class: "aa-xr-parent", style: { left: `${parent.rect.x}px`, top: `${parent.rect.y}px`, width: `${parent.rect.width}px`, height: `${parent.rect.height}px` } }, h("span", { text: parent.path })));
    }
    const boxes = elements.map((e, index) => {
      const box = h("button", { type: "button", class: `aa-xr-box ${tone(e.section)}${e.rect.hidden ? " is-hidden" : ""}`, tabindex: "-1", title: `${e.path}\n${e.label}`, "aria-label": `${e.path}: ${e.label}`, style: { left: `${e.rect.x}px`, top: `${e.rect.y}px`, width: `${Math.max(e.rect.width, 4)}px`, height: `${Math.max(e.rect.height, 4)}px` } });
      box.addEventListener("click", () => select(index, { scrollList: true }));
      box.addEventListener("mouseenter", () => rows[index].classList.add("hover"));
      box.addEventListener("mouseleave", () => rows[index].classList.remove("hover"));
      stage.append(box);
      return box;
    });
    const fit = () => {
      const scale = Math.min(1, (map.clientWidth || width) / width);
      stage.style.transform = `scale(${scale})`;
      sizer.style.width = `${width * scale}px`;
      sizer.style.height = `${height * scale}px`;
    };
    if (typeof ResizeObserver === "function") new ResizeObserver(fit).observe(map);
    requestAnimationFrame(fit);

    // -- the stored HTML page under the boxes (optional; it is the page SerpApi measured)
    const showPage = serp.toggleSwitch("Show the stored page", false, async (on) => {
      if (!on) { frameHost.replaceChildren(); map.classList.remove("with-page"); return; }
      try {
        if (typeof record.html !== "string" || !record.html) {
          const full = await api(`${BASE}/searches/${record.id}?html=1`);
          if (typeof full.html !== "string" || !full.html) throw new Error("No stored HTML page is available for this search.");
          record.html = full.html;
        }
        frameHost.replaceChildren(h("iframe", { sandbox: "", srcdoc: serp.cleanHtml(record.html), referrerpolicy: "no-referrer", title: "Stored page", tabindex: "-1", style: { width: `${width}px`, height: `${height}px` } }));
        map.classList.add("with-page");
      } catch (error) { showPage.set(false); Aira.reportError(error); }
    }, { hint: data.html_available ? "Scripts off, no network. Positions come from SerpApi's renderer, so the page can drift slightly." : "No stored page for this search" });
    if (!data.html_available) showPage.input.disabled = true;

    // -- legend: one switch per section
    const legend = h("div", { class: "sk-chips aa-xr-legend", role: "group", "aria-label": "Sections" }, ...sections.map((section) => {
      const count = elements.filter((e) => e.section === section).length;
      const chip = h("button", { type: "button", class: `sk-chip aa-xr-chip ${tone(section)}`, "aria-pressed": "true", title: `Show or hide ${section}` }, h("i", { "aria-hidden": "true" }), `${section} ${count}`);
      chip.addEventListener("click", () => {
        if (hidden.has(section)) hidden.delete(section); else hidden.add(section);
        chip.setAttribute("aria-pressed", String(!hidden.has(section)));
        elements.forEach((e, index) => { const off = hidden.has(e.section); boxes[index].hidden = off; rows[index].hidden = off; });
      });
      return chip;
    }));

    // -- element list and detail
    const filter = h("input", { type: "search", class: "sk-filter", placeholder: "Filter by path or text", "aria-label": "Filter elements" });
    const list = h("ul", { class: "aa-xr-list", role: "listbox", "aria-label": "Measured elements", tabindex: "0" });
    const rows = elements.map((e, index) => {
      const row = h("li", { role: "option", "aria-selected": "false", id: `aaXr${index}`, class: tone(e.section) }, h("i", { "aria-hidden": "true" }), h("code", { text: e.path }), h("span", { text: e.label === e.path ? "" : e.label }));
      row.addEventListener("click", () => select(index, { scrollMap: true }));
      row.addEventListener("mouseenter", () => boxes[index].classList.add("hover"));
      row.addEventListener("mouseleave", () => boxes[index].classList.remove("hover"));
      list.append(row);
      return row;
    });
    const detail = h("div", { class: "aa-xr-detail", "aria-live": "polite" }, h("p", { class: "sk-dim", text: "Pick a box on the page or an element in the list to see its JSON path." }));
    const visible = () => rows.map((row, index) => (row.hidden ? -1 : index)).filter((index) => index >= 0);
    function select(index, { scrollList = false, scrollMap = false } = {}) {
      if (selected >= 0) { boxes[selected].classList.remove("on"); rows[selected].classList.remove("on"); rows[selected].setAttribute("aria-selected", "false"); }
      selected = index;
      const e = elements[index];
      boxes[index].classList.add("on");
      rows[index].classList.add("on");
      rows[index].setAttribute("aria-selected", "true");
      list.setAttribute("aria-activedescendant", rows[index].id);
      if (scrollList) rows[index].scrollIntoView({ block: "nearest" });
      if (scrollMap) boxes[index].scrollIntoView({ block: "center", inline: "nearest" });
      const restrictor = e.path.replace(/\[\d+\]/g, "[]");
      detail.replaceChildren(...[
        h("div", { class: "aa-xr-path" }, h("code", { text: e.path }), serp.copyButton(e.path, { label: "Copy JSON path", message: "Path copied", text: true })),
        factList([
          ["Section", e.section],
          ["Position", `x ${num(Math.round(e.rect.x))}, y ${num(Math.round(e.rect.y))}`],
          ["Size", `${num(Math.round(e.rect.width))} × ${num(Math.round(e.rect.height))} px`],
          ["Above the fold", e.rect.y < (data.viewport.height || 0) ? "yes" : "no"],
          ...(e.rect.hidden ? [["Visibility", "hidden on the page when measured"]] : []),
          ["json_restrictor", monoCopy(restrictor, "Copy json_restrictor path")],
        ]),
        serp.kvList(e.fields || {}, { rawKeys: true }),
      ].filter(Boolean));
    }
    list.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const order = visible();
      if (!order.length) return;
      const at = order.indexOf(selected);
      const next = event.key === "Home" ? order[0] : event.key === "End" ? order[order.length - 1]
        : order[Math.max(0, Math.min(order.length - 1, at + (event.key === "ArrowDown" ? 1 : -1)))] ?? order[0];
      select(next, { scrollList: true, scrollMap: true });
    });
    filter.addEventListener("input", () => {
      const needle = filter.value.trim().toLowerCase();
      elements.forEach((e, index) => {
        const off = hidden.has(e.section) || (needle && !`${e.path} ${e.label}`.toLowerCase().includes(needle));
        rows[index].hidden = off;
        boxes[index].classList.toggle("dim", Boolean(needle) && off && !hidden.has(e.section));
      });
    });

    host.replaceChildren(...[
      h("div", { class: "aa-xr-bar" }, legend, h("div", { class: "btn-row" }, showPage.node, again)),
      info,
      data.error ? h("div", { class: "sk-note warn" }, ico("shield"), h("span", { text: `SerpApi reported a problem while measuring: ${data.error}` })) : null,
      h("div", { class: "aa-xr-grid" }, map,
        h("div", { class: "aa-xr-side" }, h("div", { class: "sk-filter-row" }, ico("search"), filter), list, detail)),
    ].filter(Boolean));
    select(0);
  }

  // ===========================================================================
  // Shared: search detail drawer, compare and diff
  // ===========================================================================

  async function openDetail(id, siblings = [], { onChange } = {}) {
    let record;
    const body = h("div", { class: "aa-detail" }, loading(4));
    const index = siblings.indexOf(id);
    const go = (offset) => { const next = siblings[index + offset]; if (next) openDetail(next, siblings, { onChange }); };
    const nav = siblings.length > 1 ? [
      btn("", { icon: "left", kind: "ghost", sm: true, aria: "Previous search", title: "Previous search", disabled: index <= 0, onClick: () => go(-1) }),
      btn("", { icon: "right", kind: "ghost", sm: true, aria: "Next search", title: "Next search", disabled: index < 0 || index >= siblings.length - 1, onClick: () => go(1) }),
    ] : [];
    const drawer = Aira.drawer({ title: "Search", subtitle: h("span", { class: "sk-dim", text: "Loading…" }), body, headActions: nav, onClose: () => { if (router.parse().query.search === id) setQuery({ search: "" }); } });
    if (router.current && router.current.query.tab === "history") setQuery({ search: id });
    try { record = await api(`${BASE}/searches/${id}`); }
    catch (error) {
      body.replaceChildren(failure(error, () => { drawer.close(); openDetail(id, siblings, { onChange }); }));
      return;
    }
    paintDetail(drawer, body, record, siblings, { onChange, reload: () => { drawer.close(); openDetail(id, siblings, { onChange }); } });
  }

  function paintDetail(drawer, body, record, siblings, { onChange, reload }) {
    const title = drawer.panel.querySelector(".drawer-head h2");
    const sub = drawer.panel.querySelector(".drawer-head .sub");
    title.textContent = recordTitle(record);
    drawer.panel.setAttribute("aria-label", `${record.engine_name || record.engine}: ${recordTitle(record)}`);
    sub.replaceChildren(serp.engineBadge({ id: record.engine, name: record.engine_name }), " ", serp.statusBadge(record), " ", serp.creditChip(record), " ", h("span", { class: "sk-dim" }, timeNode(record.created_at)));

    const actions = drawer.panel.querySelector(".drawer-head .btn-row");
    const pinButton = btn(record.pinned ? "Pinned" : "Pin", { icon: "pin", sm: true, kind: record.pinned ? "" : "secondary", onClick: async (event) => {
      await run(event.currentTarget, async () => {
        const updated = await api(`${BASE}/searches/${record.id}`, { method: "PATCH", body: { pinned: !record.pinned } });
        record.pinned = updated.pinned;
        pinButton.classList.toggle("secondary", !record.pinned);
        pinButton.lastChild.textContent = record.pinned ? "Pinned" : "Pin";
        toast(record.pinned ? "Pinned. Retention will keep this search." : "Unpinned.", "success", { timeout: 2200 });
        if (onChange) onChange();
      });
    } });
    const closeButton = actions.querySelector('button[aria-label="Close"]');
    actions.insertBefore(pinButton, closeButton);

    const resultViewer = serp.resultViewer(record, { snippets: { engine: record.engine, params: record.params } });

    const rerun = btn("Run again", { icon: "refresh", sm: true, onClick: async (event) => {
      await run(event.currentTarget, async () => {
        const fresh = await api(`${BASE}/searches/${record.id}/rerun`, { method: "POST", body: {} });
        toast(fresh.status === "ok" ? `Ran again: ${fresh.result_count} results.` : "The search was queued.", "success");
        if (onChange) onChange();
        openDetail(fresh.id, [fresh.id, ...siblings.filter((s) => s !== fresh.id)], { onChange });
      });
    } });
    const exportSelect = h("select", { class: "aa-export", "aria-label": "Export this search" },
      h("option", { value: "", text: "Export…" }),
      ...[["json", "JSON"], ["csv", "CSV (main list)"], ["xlsx", "Excel workbook (main list)"], ["md", "Markdown table"], ["html", "HTML page"], ["curl", "cURL command"]].map(([value, text]) => h("option", { value, text })));
    exportSelect.addEventListener("change", async () => {
      const format = exportSelect.value;
      exportSelect.value = "";
      if (!format) return;
      try { await Aira.download(`${BASE}/searches/${record.id}/export?format=${format}`, `aira-${record.engine}-${record.id.slice(0, 8)}.${format === "curl" ? "sh" : format}`); }
      catch (error) { Aira.reportError(error); }
    });
    const openPlayground = btn("Open in Playground", { icon: "play", sm: true, href: playgroundLink(record.engine, record.params), onClick: () => drawer.close() });
    const compare = btn("Compare with…", { icon: "compare", sm: true, disabled: record.status !== "ok" || !record.result, title: record.status !== "ok" ? "Only successful searches can be compared" : "", onClick: () => comparePicker(record, onChange) });
    const restrictor = btn("json_restrictor", { icon: "filter", sm: true, disabled: !record.result, title: "Pick fields and build a json_restrictor value", onClick: () => {
      Aira.modal({ title: "Build a json_restrictor", wide: true, body: restrictorBuilder(record), actions: [{ label: "Done", kind: "" }] });
    } });
    const xrayReady = XRAY_ENGINES.includes(record.engine) && record.status === "ok" && record.serpapi_id;
    const xrayButton = XRAY_ENGINES.includes(record.engine) ? btn("X-Ray", { icon: "eye", sm: true, disabled: !xrayReady, title: xrayReady ? "See where each result sits on the page and which JSON path holds it" : "X-Ray needs a successful search that SerpApi archived", onClick: () => openXray(record) }) : null;
    const saveJobs = ["google_jobs", "aira_jobs"].includes(record.engine) && record.result ? btn("Save jobs to Aira", { icon: "download", sm: true, title: "Import the job postings in this search into Jobs, where they are scored against your resume", onClick: async (event) => {
      await run(event.currentTarget, async () => {
        const { jobs } = await api(`${CAREERS}/records/${record.id}/jobs`);
        const fresh = jobs.filter((job) => !job.already_in_aira && job.importable !== false);
        if (!jobs.length) { toast("This search holds no job postings.", "error"); return; }
        if (!fresh.length) { toast(`All ${pluralize(jobs.length, "job")} are already in Aira.`, "success"); return; }
        const ok = await Aira.confirm({ title: `Save ${pluralize(fresh.length, "job")} to Aira?`, message: `${jobs.length > fresh.length ? `${pluralize(jobs.length - fresh.length, "job")} in this search ${jobs.length - fresh.length === 1 ? "is" : "are"} already saved or cannot be imported. ` : ""}The others are added to Jobs and scored against your resume. Nothing is applied for you.`, confirmLabel: "Save" });
        if (!ok) return;
        let created = 0, merged = 0, failed = 0;
        for (const job of fresh) {
          try { const out = await api(`${CAREERS}/jobs/import`, { method: "POST", body: { job }, silent: true }); if (out.created) created += 1; else merged += 1; } catch { failed += 1; }
        }
        toast(`${[created ? `${pluralize(created, "new job")} saved` : "", merged ? `${merged} merged into jobs you already had` : "", failed ? `${failed} failed` : ""].filter(Boolean).join(", ")}.`, failed ? "error" : "success");
      });
    } }) : null;
    const remove = btn("Delete", { icon: "trash", sm: true, kind: "danger secondary", onClick: async () => {
      const ok = await Aira.confirm({ title: "Delete this search?", message: record.pinned ? "This search is pinned. Deleting removes its stored result for good." : "Its stored result is removed for good. Running it again uses a search.", confirmLabel: "Delete", danger: true });
      if (!ok) return;
      try { await api(`${BASE}/searches/${record.id}`, { method: "DELETE" }); toast("Search deleted.", "success"); drawer.close(); if (onChange) onChange(); }
      catch (error) { Aira.reportError(error); }
    } });

    const notes = [];
    if (record.status === "error") notes.push(h("div", { class: "sk-note error", role: "alert" }, ico("shield"), h("span", {}, h("strong", { text: "This search failed. " }), record.error || "SerpApi returned an error.", " Failed searches are not counted against the plan.")));
    if (record.cache_hit) notes.push(h("div", { class: "sk-note" }, ico("check"), h("span", { text: "Served from Aira's local cache: no search was used. The data is as old as the original run." })));
    if (record.result_truncated) notes.push(h("div", { class: "sk-note warn" }, ico("shield"), h("span", { text: "The response was larger than the stored-result limit, so it was returned once but not kept. Raise the limit in Account, Local limits." })));
    if (record.note) notes.push(h("div", { class: "sk-note" }, ico("check"), h("span", { text: record.note })));

    const facts = factList([
      ["Aira id", monoCopy(record.id, "Copy Aira id")],
      ["SerpApi id", record.serpapi_id ? monoCopy(record.serpapi_id, "Copy SerpApi search id") : "none (served locally or failed before reaching SerpApi)"],
      ["Source", fmt.title(String(record.source).replaceAll("_", " "))],
      ["HTTP status", record.http_status || "–"],
      ["Duration", record.cache_hit ? "0 ms (cache)" : ms(record.duration_ms)],
      ["Results", record.result_count === undefined ? "–" : num(record.result_count) + (record.main_result_key ? ` in ${record.main_result_key}` : "")],
      ["Size", record.result_bytes ? fmt.bytes(record.result_bytes) : "–"],
      ["Output", String(record.output || "json").toUpperCase()],
      ["Created", fmt.datetime(record.created_at)],
    ]);
    const params = Object.keys(record.params || {}).length
      ? h("div", { class: "sk-chips aa-params" }, ...Object.entries(record.params).map(([k, v]) => serp.chip(`${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)))
      : h("p", { class: "sk-dim", text: "No parameters." });

    body.replaceChildren(
      h("div", { class: "aa-detail-actions btn-row" }, rerun, openPlayground, compare, xrayButton, restrictor, saveJobs, exportSelect, remove),
      ...notes,
      h("div", { class: "aa-detail-grid" }, panel("Parameters", { body: panelBody(params) }), panel("Details", { body: panelBody(facts) })),
      h("div", { class: "aa-detail-viewer" }, resultViewer.node));
    body.parentElement.scrollTop = 0;
    if (record.status === "running") pollRunning(drawer, body, record, siblings, { onChange, reload });
  }

  /** An async search that is still running: ask the server (which polls SerpApi's archive) with a growing delay. */
  function pollRunning(drawer, body, record, siblings, ctx) {
    let delay = 2500;
    const tick = async () => {
      if (!drawer.panel.isConnected) return;
      try {
        const fresh = await api(`${BASE}/searches/${record.id}`, { silent: true });
        if (fresh.status !== "running") { paintDetail(drawer, body, fresh, siblings, ctx); if (ctx.onChange) ctx.onChange(); return; }
      } catch { /* try again */ }
      delay = Math.min(delay * 1.5, 12000);
      setTimeout(tick, delay);
    };
    setTimeout(tick, delay);
  }

  /** Lets the person pick another successful run of the same engine, then shows the difference. */
  async function comparePicker(record, onChange) {
    const select = h("select", { id: "aaCompareWith", "aria-label": "Compare with" });
    const info = h("p", { class: "muted", text: "Loading earlier runs of this engine…" });
    const modal = Aira.modal({
      title: "Compare with another run",
      body: h("div", {}, info, ui.field("Other run", select, "Pick a run of the same engine. The older one is treated as the baseline.")),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Compare", close: false, onClick: async (close) => {
          if (!select.value) { toast("Choose a run to compare with.", "error"); return false; }
          close();
          await showDiff(record.id, select.value, onChange);
          return undefined;
        } },
      ],
    });
    try {
      const data = await api(`${BASE}/searches?${qs({ engine: record.engine, status: "ok", limit: 50 })}`);
      const others = data.items.filter((item) => item.id !== record.id && item.output === "json" && !item.result_truncated);
      if (!others.length) { info.textContent = "There is no other successful JSON run of this engine yet. Run a search again from the Playground, then compare."; select.disabled = true; return; }
      info.textContent = `${others.length} earlier or later run${others.length === 1 ? "" : "s"} of ${record.engine_name || record.engine} found.`;
      select.replaceChildren(...others.map((item) => h("option", { value: item.id, text: `${fmt.datetime(item.created_at)} · ${recordTitle(item)} · ${num(item.result_count)} results${item.cache_hit ? " · cached" : ""}` })));
    } catch (error) { modal.close(); Aira.reportError(error); }
  }

  /** Two stored runs side by side: what was added, removed, moved or changed. */
  async function showDiff(aId, bId, onChange) {
    let diff;
    try { diff = await api(`${BASE}/diff?${qs({ a: aId, b: bId })}`); }
    catch (error) { Aira.reportError(error); return; }
    const filterBox = h("input", { type: "search", placeholder: "Filter by path or title", "aria-label": "Filter differences" });
    const results = h("div", { class: "aa-diff-results" });
    let view = "all";
    const itemNode = (item, tone) => {
      const title = serp.logic.titleOfItem(item) || (typeof item === "string" ? item : JSON.stringify(item).slice(0, 120));
      const url = serp.logic.safeLink(item && (item.link || item.url || item.product_link));
      return h("li", { class: `aa-diff-item ${tone}` },
        h("span", { class: "aa-diff-mark", "aria-hidden": "true", text: tone === "added" ? "+" : "−" }),
        h("div", {}, url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: title }) : h("strong", { text: title }),
          item && typeof item === "object" && (item.snippet || item.description || item.source) ? h("p", { class: "sk-dim", text: String(item.snippet || item.description || item.source).slice(0, 200) }) : null,
          h("span", { class: "visually-hidden", text: tone === "added" ? " added" : " removed" })));
    };
    const changeRow = (change) => {
      const rank = /\.rank$/.test(change.path);
      const delta = rank ? Number(change.before) - Number(change.after) : 0;
      return h("tr", {},
        h("td", {}, h("code", { text: change.path })),
        h("td", {}, h("span", { class: "aa-diff-before", text: shortValue(change.before) })),
        h("td", {}, h("span", { class: "aa-diff-after", text: shortValue(change.after) }), rank && delta ? h("span", { class: `aa-rank ${delta > 0 ? "up" : "down"}`, text: delta > 0 ? ` ▲ ${delta}` : ` ▼ ${-delta}` }) : null));
    };
    const paint = () => {
      const needle = filterBox.value.trim().toLowerCase();
      const match = (value) => !needle || JSON.stringify(value).toLowerCase().includes(needle);
      const added = diff.added.filter(match), removed = diff.removed.filter(match), changed = diff.changed.filter(match);
      const parts = [];
      if (["all", "added"].includes(view)) parts.push(serp.section("Added", { count: added.length, tone: "added", body: added.length ? h("ul", { class: "aa-diff-list" }, ...added.map((i) => itemNode(i, "added"))) : h("p", { class: "sk-dim", text: "Nothing new in this view." }) }));
      if (["all", "removed"].includes(view)) parts.push(serp.section("Removed", { count: removed.length, tone: "removed", body: removed.length ? h("ul", { class: "aa-diff-list" }, ...removed.map((i) => itemNode(i, "removed"))) : h("p", { class: "sk-dim", text: "Nothing disappeared in this view." }) }));
      if (["all", "changed"].includes(view)) parts.push(serp.section("Changed", { count: changed.length, body: changed.length ? h("div", { class: "table-wrap" }, h("table", { class: "table aa-diff-table" }, h("caption", { class: "visually-hidden", text: "Changed values" }), h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Path" }), h("th", { scope: "col", text: "Before" }), h("th", { scope: "col", text: "After" }))), h("tbody", {}, ...changed.map(changeRow)))) : h("p", { class: "sk-dim", text: "No changed values in this view." }) }));
      results.replaceChildren(...parts);
    };
    const seg = serp.segmented([["all", "All"], ["added", `Added ${diff.counts.added}`], ["removed", `Removed ${diff.counts.removed}`], ["changed", `Changed ${diff.counts.changed}`]], "all", (next) => { view = next; paint(); }, { label: "Show" });
    filterBox.addEventListener("input", paint);
    const side = (record, label) => h("div", { class: "aa-diff-side" },
      h("span", { class: "aa-diff-label", text: label }),
      h("strong", { text: recordTitle(record) }),
      h("span", { class: "sk-dim", text: `${fmt.datetime(record.created_at)} · ${num(record.result_count)} results` }));
    const body = h("div", { class: "aa-diff" },
      h("div", { class: "aa-diff-head" }, side(diff.a, "Baseline"), h("span", { class: "aa-diff-arrow", "aria-hidden": "true" }, ico("right")), side(diff.b, "Newer")),
      h("div", { class: `sk-note ${diff.changed_any ? "warn" : ""}`, role: "status" }, ico(diff.changed_any ? "pulse" : "check"), h("span", { text: diff.changed_any ? `${diff.summary}.` : "These two runs are identical." })),
      h("div", { class: "aa-diff-tools" }, seg.node, filterBox),
      results);
    paint();
    Aira.modal({ title: "Compare runs", wide: true, body, actions: [
      { label: "Swap", kind: "ghost", close: false, onClick: async (close) => { close(); await showDiff(diff.b.id, diff.a.id, onChange); } },
      { label: "Open newer run", kind: "secondary", close: false, onClick: (close) => { close(); openDetail(diff.b.id, [diff.b.id, diff.a.id], { onChange }); } },
      { label: "Close", kind: "" },
    ] });
  }
  function shortValue(value) {
    if (value === null || value === undefined) return "–";
    if (typeof value === "object") return JSON.stringify(value).slice(0, 160);
    const text = String(value);
    return text.length > 160 ? `${text.slice(0, 159)}…` : text;
  }

  // ===========================================================================
  // Overview
  // ===========================================================================

  const SOURCE_LABELS = { playground: "Playground", careers: "Careers", api: "API (/v1)", monitor: "Monitors", intel: "Intel reports", rerun: "Re-runs" };
  const sourceLabel = (source) => SOURCE_LABELS[source] || fmt.title(String(source || "").replaceAll("_", " "));

  /** Ring gauge: the arc is the share of the allowance that is left. */
  function ringGauge({ left, total, tone }) {
    const size = 168, stroke = 14, r = (size - stroke) / 2, c = 2 * Math.PI * r;
    const ratio = total > 0 ? clamp(left / total, 0, 1) : 0;
    const label = total > 0 ? `${num(left)} of ${num(total)} plan searches left this month` : `${num(left)} searches left`;
    const node = svg("svg", { class: `aa-ring ${tone || ""}`.trim(), viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": label },
      svg("circle", { class: "track", cx: size / 2, cy: size / 2, r, "stroke-width": stroke, fill: "none" }),
      svg("circle", { class: "arc", cx: size / 2, cy: size / 2, r, "stroke-width": stroke, fill: "none", "stroke-linecap": "round", "stroke-dasharray": `${(c * ratio).toFixed(2)} ${c.toFixed(2)}`, transform: `rotate(-90 ${size / 2} ${size / 2})` }),
      svg("text", { class: "big", x: size / 2, y: size / 2 + 2, "text-anchor": "middle", text: num(left) }),
      svg("text", { class: "small", x: size / 2, y: size / 2 + 22, "text-anchor": "middle", text: total > 0 ? `of ${num(total)} left` : "searches left" }));
    return node;
  }

  function heroPanel(data) {
    const account = data.account;
    const keyText = data.key && data.key.last4 ? `Key ending ${data.key.last4} · ${data.key.source === "env" ? "from SERPAPI_API_KEY" : "stored encrypted in Aira"}` : "";
    if (!account) {
      return h("section", { class: "aa-hero warn", "aria-labelledby": "aaHeroTitle" },
        h("div", { class: "aa-hero-main" },
          h("span", { class: "aa-eyebrow", text: "Key connected" }),
          h("h2", { id: "aaHeroTitle", text: "SerpApi account could not be read" }),
          h("p", { text: data.account_error || "SerpApi did not answer. Cached searches still work." }),
          keyText ? h("p", { class: "aa-hero-quiet", text: keyText }) : null,
          h("div", { class: "btn-row" }, btn("Check again", { icon: "refresh", kind: "secondary", onClick: () => router.render() }), btn("Replace key", { icon: "key", kind: "secondary", href: link("account") }))));
    }
    const total = account.searches_per_month || 0;
    const left = account.plan_searches_left ?? account.total_searches_left ?? 0;
    const tone = leftTone(left, total);
    const hourly = account.hourly_rate_limit || 0;
    const fact = (label, value, sub) => h("div", { class: "aa-fact" }, h("dt", { text: label }), h("dd", {}, value, sub ? h("small", { text: sub }) : null));
    return h("section", { class: `aa-hero ${tone}`.trim(), "aria-labelledby": "aaHeroTitle" },
      h("div", { class: "aa-hero-main" },
        h("span", { class: "aa-eyebrow", text: `SerpApi plan${account.account_status ? ` · ${account.account_status}` : ""}` }),
        h("h2", { id: "aaHeroTitle", text: account.plan_name || "Your plan" }),
        h("p", { text: [account.plan_monthly_price !== null && account.plan_monthly_price !== undefined ? `${dollars(account.plan_monthly_price)} per month` : "", account.plan_renewal_date ? `renews ${fmt.date(account.plan_renewal_date)}` : "", account.account_email || ""].filter(Boolean).join(" · ") }),
        keyText ? h("p", { class: "aa-hero-quiet", text: keyText }) : null,
        h("div", { class: "btn-row" }, btn("Open Playground", { icon: "play", kind: "secondary", href: link("playground") }), btn("Account & plan", { kind: "secondary", href: link("account") }))),
      h("div", { class: "aa-hero-gauge" }, ringGauge({ left, total, tone })),
      h("dl", { class: "aa-hero-facts" },
        fact("Used this month", num(account.this_month_usage), total ? `of ${num(total)}` : ""),
        fact("Extra credits", num(account.extra_credits)),
        fact("Total left", num(account.total_searches_left)),
        fact("This hour", num(account.this_hour_searches), hourly ? `of ${num(hourly)}` : ""),
        fact("Last hour", num(account.last_hour_searches))));
  }

  function quotaWarnings(data) {
    const out = [];
    const a = data.account;
    if (!a) return out;
    const total = a.searches_per_month || 0;
    const left = a.plan_searches_left ?? 0;
    if (total && left / total <= 0.05) out.push(["error", `Only ${num(left)} of your ${num(total)} monthly searches are left. Cached searches are free; keep monitors paused until the plan renews or add credits at SerpApi.`]);
    else if (total && left / total <= 0.2) out.push(["warn", `${num(left)} of your ${num(total)} monthly searches are left (${Math.round((left / total) * 100)}%). Aira's cache and the cost estimator below can help you stretch them.`]);
    const hourly = a.hourly_rate_limit || 0;
    if (hourly && (a.this_hour_searches || 0) / hourly >= 0.8) out.push(["warn", `This hour you have used ${num(a.this_hour_searches)} of ${num(hourly)} searches. SerpApi answers 429 beyond the limit; spread requests out or wait for the hour to roll over.`]);
    return out;
  }

  /** Stacked daily bars (live, cached, errors) with a tooltip, arrow-key access and a table twin. */
  function usageBars(byDay) {
    const rows = byDay.map((d) => ({ day: d.day, live: Math.max(0, d.searches - d.cached - d.errors), cached: d.cached, errors: d.errors, total: d.searches }));
    const total = rows.reduce((sum, r) => sum + r.total, 0);
    if (!total) return ui.empty("No searches in the last 30 days", "Run one in the Playground and it shows up here with its cache and error status.", btn("Open Playground", { icon: "play", kind: "", href: link("playground") }));
    const series = [["live", "Live searches", "var(--sk-s1)"], ["cached", "Cache hits (free)", "var(--sk-s3)"], ["errors", "Errors", "var(--sk-s2)"]];
    const frame = h("div", { class: "aa-bars-frame" });
    const tip = h("div", { class: "sk-tip", hidden: true, role: "presentation" });
    let active = -1;
    const draw = () => {
      const width = Math.max(260, Math.floor(frame.clientWidth || 640));
      const height = 210, padL = 36, padR = 6, padT = 8, padB = 24;
      const max = Math.max(1, ...rows.map((r) => r.total));
      const ticks = serp.logic.niceTicks(0, max, 4).filter((t) => Number.isInteger(t));
      const top = ticks.length ? Math.max(max, ticks[ticks.length - 1]) : max;
      const inner = width - padL - padR, slot = inner / rows.length, barW = Math.max(3, slot * 0.72);
      const y = (v) => padT + (height - padT - padB) * (1 - v / top);
      const node = svg("svg", { class: "aa-bars", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", tabindex: "0", "aria-label": `Searches per day for the last 30 days, ${num(total)} in total. Use the arrow keys to read each day.` });
      for (const t of ticks) node.append(svg("line", { class: "sk-gridline", x1: padL, x2: width - padR, y1: y(t), y2: y(t) }), svg("text", { class: "sk-axis", x: padL - 6, y: y(t) + 3.5, "text-anchor": "end", text: compactNum(t) }));
      rows.forEach((r, i) => {
        const x = padL + slot * i + (slot - barW) / 2;
        let base = 0;
        for (const [key, , color] of series) {
          if (!r[key]) continue;
          const y0 = y(base + r[key]), y1 = y(base);
          node.append(svg("rect", { class: "aa-bar", x: x.toFixed(1), y: y0.toFixed(1), width: barW.toFixed(1), height: Math.max(1, y1 - y0).toFixed(1), rx: 1.5, fill: color, "data-i": i }));
          base += r[key];
        }
      });
      [0, Math.floor((rows.length - 1) / 2), rows.length - 1].forEach((i, k) => node.append(svg("text", { class: "sk-axis", x: padL + slot * i + slot / 2, y: height - 7, "text-anchor": k === 0 ? "start" : k === 2 ? "end" : "middle", text: rows[i].day.slice(5) })));
      const cross = svg("rect", { class: "aa-bar-focus", x: 0, y: padT, width: slot, height: height - padT - padB, style: "display:none" });
      node.prepend(cross);
      const show = (i) => {
        active = clamp(i, 0, rows.length - 1);
        const r = rows[active];
        cross.setAttribute("x", (padL + slot * active).toFixed(1));
        cross.style.display = "";
        tip.replaceChildren(h("div", { class: "sk-tip-label", text: r.day }), h("div", { class: "sk-tip-row" }, h("b", { text: num(r.total) }), h("span", { text: pluralize(r.total, "search") })),
          ...series.filter(([key]) => r[key]).map(([key, label, color], k) => h("div", { class: "sk-tip-row" }, h("i", { class: "sk-key", style: { background: color } }), h("b", { text: num(r[key]) }), h("span", { text: label }))));
        tip.hidden = false;
        const left = clamp(padL + slot * active + slot / 2, 8, width - 8);
        tip.style.left = `${left}px`;
        tip.classList.toggle("flip", left > width * 0.62);
      };
      const hide = () => { tip.hidden = true; cross.style.display = "none"; active = -1; };
      node.addEventListener("pointermove", (event) => { const rect = node.getBoundingClientRect(); show(Math.floor(((event.clientX - rect.left) / rect.width * width - padL) / slot)); });
      node.addEventListener("pointerleave", hide);
      node.addEventListener("focus", () => show(active < 0 ? rows.length - 1 : active));
      node.addEventListener("blur", hide);
      node.addEventListener("keydown", (event) => {
        if (event.key === "ArrowRight") { event.preventDefault(); show((active < 0 ? -1 : active) + 1); }
        else if (event.key === "ArrowLeft") { event.preventDefault(); show((active < 0 ? rows.length : active) - 1); }
        else if (event.key === "Home") { event.preventDefault(); show(0); }
        else if (event.key === "End") { event.preventDefault(); show(rows.length - 1); }
        else if (event.key === "Escape") hide();
      });
      frame.replaceChildren(node, tip);
    };
    if (typeof ResizeObserver !== "undefined") {
      let last = 0;
      new ResizeObserver(() => { const w = Math.floor(frame.clientWidth); if (w && w !== last) { last = w; draw(); } }).observe(frame);
    }
    queueMicrotask(draw);
    const table = h("details", { class: "sk-chart-data" }, h("summary", { text: "View data table" }),
      simpleTable("Searches per day, last 30 days", ["Day", { label: "Live", num: true }, { label: "Cached", num: true }, { label: "Errors", num: true }, { label: "Total", num: true }], rows.map((r) => [r.day, num(r.live), num(r.cached), num(r.errors), num(r.total)])));
    return h("figure", { class: "sk-chart aa-usage" }, frame,
      h("ul", { class: "sk-legend" }, ...series.map(([, label, color]) => h("li", {}, h("i", { class: "sk-key", style: { background: color } }), h("span", { text: label })))), table);
  }

  /** Usage as one CSV (SerpApi's dashboard has charts only): a row per day, per engine and per source, last 30 days. */
  function exportUsage(usage) {
    const rows = [
      ...(usage.by_day || []).map((d) => ({ section: "day", name: d.day, searches: d.searches, cached: d.cached, errors: d.errors, avg_ms: "" })),
      ...(usage.by_engine || []).map((e) => ({ section: "engine", name: e.engine, searches: e.searches, cached: "", errors: e.errors, avg_ms: e.avg_ms })),
      ...(usage.by_source || []).map((r) => ({ section: "source", name: r.source, searches: r.searches, cached: "", errors: "", avg_ms: "" })),
    ];
    serp.downloadText("aira-api-usage-30-days.csv", serp.logic.rowsToCsv(["section", "name", "searches", "cached", "errors", "avg_ms"].map((key) => ({ key })), rows), "text/csv");
    toast(`Exported ${pluralize(rows.length, "usage row")}.`, "success", { timeout: 2200 });
  }

  function enginesPanel(usage) {
    const rows = usage.by_engine || [];
    const top = Math.max(1, ...rows.map((r) => r.searches));
    return panel("Top engines", { sub: "last 30 days", aside: btn("All history", { kind: "ghost", sm: true, href: link("history") }), body: rows.length
      ? h("ul", { class: "aa-rank-list" }, ...rows.map((r) => h("li", {},
        h("a", { href: link("history", { engine: r.engine }), class: "aa-rank-row", title: `Show ${r.name} searches in History` },
          h("span", { class: "aa-rank-name", text: r.name }),
          h("span", { class: "aa-rank-track", "aria-hidden": "true" }, h("i", { style: { width: `${Math.max(3, (r.searches / top) * 100)}%` } })),
          h("span", { class: "aa-rank-n", text: num(r.searches) }),
          h("span", { class: "aa-rank-sub", text: `${ms(r.avg_ms)}${r.errors ? ` · ${pluralize(r.errors, "error")}` : ""}` })))))
      : ui.empty("No engine activity yet", "Once you run searches, the busiest engines and their latency appear here.") });
  }

  function ratesPanel(usage) {
    const cached = usage.by_day.reduce((sum, d) => sum + d.cached, 0);
    const total = usage.by_day.reduce((sum, d) => sum + d.searches, 0);
    const errors = usage.by_day.reduce((sum, d) => sum + d.errors, 0);
    const rate = (label, ratio, help, tone, href) => h("a", { class: "aa-rate", href }, h("span", { class: "aa-rate-top" }, h("strong", { text: label }), h("span", { class: "aa-rate-val", text: total ? percent(ratio, ratio < 0.1 && ratio > 0 ? 1 : 0) : "–" })), meter({ value: Math.round((ratio || 0) * 1000), max: 1000, label, tone, valueText: percent(ratio, 1) }), h("small", { text: help }));
    return panel("Cache and errors", { sub: "last 30 days", body: panelBody(
      h("div", { class: "aa-rates" },
        rate("Cache hit rate", usage.cache_hit_rate, total ? `${pluralize(cached, "search")} answered locally for free.` : "No searches yet.", "good", link("history", { cache: "1" })),
        rate("Error rate", usage.error_rate, total ? `${pluralize(errors, "failed search")}. Failed searches are never billed.` : "No searches yet.", usage.error_rate > 0.1 ? "danger" : "warn", link("history", { status: "error" }))),
      h("dl", { class: "aa-mini-facts" },
        h("div", {}, h("dt", { text: "Average latency" }), h("dd", { text: ms(usage.avg_ms) })),
        h("div", {}, h("dt", { text: "Credits used" }), h("dd", { text: num(usage.credits_used_30d) })),
        h("div", {}, h("dt", { text: "Searches saved" }), h("dd", { text: num(cached) })))) });
  }

  function sourcesPanel(usage) {
    const rows = usage.by_source || [];
    const total = rows.reduce((sum, r) => sum + r.searches, 0);
    return panel("Where searches come from", { sub: "last 30 days", body: rows.length
      ? panelBody(h("ul", { class: "aa-source-list" }, ...rows.map((r) => h("li", {}, h("a", { href: link("history", { source: r.source }) }, h("span", { text: sourceLabel(r.source) }), h("span", { class: "aa-source-bar", "aria-hidden": "true" }, h("i", { style: { width: `${(r.searches / total) * 100}%` } })), h("b", { text: num(r.searches) }))))))
      : panelBody(h("p", { class: "sk-dim", text: "Playground runs, Careers searches, monitors and /v1 API calls are counted separately once they happen." })) });
  }

  function recentPanel(recent) {
    const ids = recent.map((r) => r.id);
    return panel("Recent searches", { aside: btn("Open History", { kind: "ghost", sm: true, href: link("history") }), body: recent.length
      ? h("div", { class: "list aa-recent" }, ...recent.map((r) => h("button", { type: "button", class: "row aa-recent-row", onclick: () => openDetail(r.id, ids) },
        h("span", {},
          h("span", { class: "title", text: recordTitle(r) }),
          h("span", { class: "meta" }, serp.engineBadge({ id: r.engine, name: r.engine_name }), ...paramChips(r, 2), h("span", { class: "sk-dim" }, `${sourceLabel(r.source)} · `, timeNode(r.created_at)))),
        h("span", { class: "aside" }, serp.statusBadge(r), serp.creditChip(r)))))
      : ui.empty("No searches yet", "Searches you run from the Playground, Careers, monitors or the /v1 API are listed here.", btn("Open Playground", { icon: "play", kind: "", href: link("playground") })) });
  }

  function plannerPanel(data) {
    const usage = data.usage, account = data.account;
    const pace = Math.round(usage.last_7_days / 7);
    const hitDefault = usage.by_day.some((d) => d.searches) ? Math.round(usage.cache_hit_rate * 100) : 30;
    const perDay = h("input", { type: "number", min: "0", max: "100000", step: "1", value: String(pace || 20), id: "aaPlanPerDay", inputmode: "numeric" });
    const hit = h("input", { type: "range", min: "0", max: "100", step: "5", value: String(hitDefault), id: "aaPlanHit" });
    const hitText = h("output", { for: "aaPlanHit", text: `${hitDefault}%` });
    const result = h("div", { class: "aa-plan-result", role: "status", "aria-live": "polite" });
    const compute = () => {
      const day = Math.max(0, Number(perDay.value) || 0);
      const rate = Number(hit.value) / 100;
      hitText.textContent = `${hit.value}%`;
      const monthly = Math.round(day * 30 * (1 - rate));
      const rows = [h("p", { class: "aa-plan-big" }, h("strong", { text: num(monthly) }), " billable searches a month")];
      rows.push(h("p", { class: "sk-dim", text: `${num(day * 30)} requests a month, ${Math.round(rate * 100)}% answered from the cache for free.` }));
      if (account && account.searches_per_month) {
        const share = monthly / account.searches_per_month;
        const left = account.total_searches_left ?? account.plan_searches_left ?? 0;
        const days = monthly > 0 ? Math.floor(left / (monthly / 30)) : null;
        rows.push(h("p", {}, `That is ${(share * 100).toFixed(share < 0.1 ? 1 : 0)}% of your ${num(account.searches_per_month)}-search plan. `, days === null ? "Your remaining searches would not be touched." : days >= 60 ? `Your ${num(left)} remaining searches would last more than two months.` : `Your ${num(left)} remaining searches would last about ${days} day${days === 1 ? "" : "s"}.`));
        if (share > 1) rows.push(h("div", { class: "sk-note warn" }, ico("shield"), h("span", { text: "This pace is above the monthly allowance. Raise the cache hit rate, lower the interval of monitors or upgrade the plan." })));
      } else rows.push(h("p", { class: "sk-dim", text: "Connect a key to compare this with your plan." }));
      result.replaceChildren(...rows);
    };
    perDay.addEventListener("input", compute);
    hit.addEventListener("input", compute);
    compute();
    return panel("Cost estimator", { sub: "plan your monthly burn", body: panelBody(
      h("div", { class: "form-grid aa-plan-form" }, ui.field("Searches per day", perDay, pace ? `Your last 7 days averaged ${num(pace)} a day.` : "No history yet, so this starts at 20."), h("div", { class: "field" }, h("label", { for: "aaPlanHit" }, "Cache hit rate ", hitText), hit, h("span", { class: "hint", text: "Identical searches inside the cache window cost nothing." }))),
      result) });
  }

  function monitorPanel(data) {
    const m = data.monitors || { active: 0, due_next: null };
    return panel("Monitors and saved searches", { body: panelBody(
      h("div", { class: "aa-health" },
        h("div", { class: "aa-health-item" }, h("strong", { text: num(m.active) }), h("span", { text: m.active === 1 ? "active monitor" : "active monitors" })),
        h("div", { class: "aa-health-item" }, h("strong", { text: m.due_next ? relativeFuture(m.due_next) : "–" }), h("span", { text: "next run" })),
        h("div", { class: "aa-health-item" }, h("strong", { text: num(data.saved) }), h("span", { text: data.saved === 1 ? "saved search" : "saved searches" }))),
      h("p", { class: "sk-dim", text: m.active ? "Monitors re-run a saved search on a schedule and notify you when the main result list changes." : "A monitor re-runs a saved search on a schedule and raises a notification when the results change. Rank tracking and job alerts use it." }),
      h("div", { class: "btn-row" }, btn(m.active ? "Manage monitors" : "Set up a monitor", { icon: "bell", href: link("monitors") }))) });
  }

  function actionsPanel(data) {
    const items = [
      ["Open Playground", "Try any engine with a generated form", link("playground"), "play"],
      [`Browse ${num(data.engines)} engines`, "Filter by family, then open one", link("engines"), "search"],
      ["Create an API key", "Call Aira from scripts through /v1", link("keys"), "key"],
      ["Look up a location", "Canonical names for the location parameter", link("locations"), "search"],
      ["Search jobs", "Google Jobs with every filter in Careers", "#/careers?tab=jobs", "play"],
      ["Read the docs", "Reference, SDK notes and migration guide", link("docs"), "book"],
    ];
    return panel("Quick actions", { body: h("div", { class: "aa-actions" }, ...items.map(([title, text, href, icon]) => h("a", { class: "aa-action", href }, ico(icon), h("span", {}, h("strong", { text: title }), h("small", { text }))))) });
  }

  tabs.overview = async (content) => {
    const data = await api(`${BASE}/overview`);
    loadEngines().catch(() => {});
    const warnings = quotaWarnings(data).map(([tone, text]) => h("div", { class: `sk-note ${tone}`, role: tone === "error" ? "alert" : "status" }, ico("shield"), h("span", { text })));
    const usage = data.usage;
    const total30 = usage.last_30_days;
    const stats = h("div", { class: "stats aa-stats" },
      statTile({ label: "Today", value: num(usage.today), sub: "searches" , href: link("history", { from: new Date().toISOString().slice(0, 10) }) }),
      statTile({ label: "Last 7 days", value: num(usage.last_7_days), sub: `${num(Math.round(usage.last_7_days / 7))} a day` }),
      statTile({ label: "Last 30 days", value: num(total30), sub: `${num(usage.credits_used_30d)} credits used`, href: link("history") }),
      statTile({ label: "Cache hit rate", value: total30 ? percent(usage.cache_hit_rate) : "–", sub: "answered for free", href: link("history", { cache: "1" }) }),
      statTile({ label: "Error rate", value: total30 ? percent(usage.error_rate) : "–", sub: "never billed", tone: usage.error_rate > 0.1 ? "bad" : "", href: link("history", { status: "error" }) }),
      statTile({ label: "Avg latency", value: ms(usage.avg_ms), sub: "live searches" }));

    const throughput = h("div", { class: "aa-throughput" });
    const hourlyPanel = panel("Hourly throughput", { sub: "this hour", body: panelBody(throughput) });
    const a = data.account;
    const paintThroughput = (statusData) => {
      const rows = [];
      if (a && a.hourly_rate_limit) {
        const used = a.this_hour_searches || 0;
        rows.push(h("div", { class: "aa-tp" }, h("div", { class: "aa-tp-top" }, h("strong", { text: "SerpApi account" }), h("span", { text: `${num(used)} of ${num(a.hourly_rate_limit)}` })), meter({ value: used, max: a.hourly_rate_limit, label: "Searches this hour against the SerpApi hourly limit", tone: used / a.hourly_rate_limit >= 0.8 ? "warn" : "" }), h("small", { class: "muted", text: `Last hour: ${num(a.last_hour_searches)}. SerpApi guarantees this throughput and answers 429 above it.` })));
      } else if (!data.configured) rows.push(h("p", { class: "sk-dim", text: "Connect a key to see the SerpApi hourly limit." }));
      if (statusData) {
        const t = statusData.throttle;
        rows.push(t.hourly_limit
          ? h("div", { class: "aa-tp" }, h("div", { class: "aa-tp-top" }, h("strong", { text: "Aira local cap" }), h("span", { text: `${num(t.used_this_hour)} of ${num(t.hourly_limit)}` })), meter({ value: t.used_this_hour, max: t.hourly_limit, label: "Live searches this hour against Aira's local hourly cap", tone: t.used_this_hour / t.hourly_limit >= 0.8 ? "warn" : "" }), h("small", { class: "muted", text: "Your own cap protects the plan. Change it in Account, Local limits." }))
          : h("div", { class: "aa-tp" }, h("div", { class: "aa-tp-top" }, h("strong", { text: "Aira local cap" }), h("span", { text: `${num(t.used_this_hour)} live this hour` })), h("small", { class: "muted" }, "No local cap is set. ", h("a", { href: link("account"), text: "Add one" }), " to stop runaway scripts before they use up the plan.")));
        if (statusData.queue && (statusData.queue.running || statusData.queue.async_pending)) rows.push(h("p", { class: "sk-dim", text: `${statusData.queue.running} running now, ${statusData.queue.async_pending} async pending.` }));
      } else rows.push(h("div", { class: "skeleton", style: { height: "34px" } }));
      throughput.replaceChildren(...rows);
    };
    paintThroughput(null);
    api(`${BASE}/status`, { silent: true }).then(paintThroughput).catch(() => paintThroughput({ throttle: { hourly_limit: 0, used_this_hour: 0 }, queue: null }));

    const chips = h("div", { class: "sk-chips big aa-cats" }, ...(data.categories || []).map((c) => h("a", { class: "sk-chip link", href: link("engines", { category: c.id }) }, c.label, h("small", { text: String(c.count) }))));
    content.replaceChildren(
      data.configured ? heroPanel(data) : setupPanel(), ...warnings, stats,
      h("div", { class: "aa-cols" },
        h("div", { class: "aa-stack" }, panel("Searches, last 30 days", { sub: "live, cached and failed", aside: btn("Export CSV", { icon: "download", kind: "ghost", sm: true, title: "Daily searches, per-engine and per-source usage as one CSV", onClick: () => exportUsage(usage) }), body: panelBody(usageBars(usage.by_day)) }), enginesPanel(usage)),
        h("div", { class: "aa-stack" }, hourlyPanel, ratesPanel(usage), sourcesPanel(usage))),
      h("div", { class: "aa-cols" }, recentPanel(data.recent || []), h("div", { class: "aa-stack" }, monitorPanel(data), plannerPanel(data))),
      h("div", { class: "aa-cols even" }, actionsPanel(data), panel("Engine families", { sub: `${num(data.engines)} engines`, body: panelBody(chips) })));
  };

  // ===========================================================================
  // Engines
  // ===========================================================================

  const paginationText = (p) => {
    if (!p || !p.kind || p.kind === "none") return "";
    const size = p.page_size ? ` · ${p.page_size} per page` : "";
    return `Paginates with ${p.param || p.kind}${size}`;
  };

  function engineCard(engine) {
    const required = engine.required.slice(0, 4);
    const groups = (engine.requires_any_of || []).filter((g) => g.length > 1);
    const flags = [
      engine.deprecated ? h("span", { class: "sk-chip warn", title: "SerpApi has deprecated this engine. It may stop working.", text: "Deprecated" }) : null,
      engine.unlisted ? h("span", { class: "sk-chip", title: "Documented by SerpApi but not listed in their public engine index.", text: "Unlisted" }) : null,
      engine.provider === "aira" ? h("span", { class: "sk-chip good", title: "Answered by Aira itself from free public job boards: no SerpApi key and no credits.", text: "Aira · free, no key" }) : null,
    ];
    return h("article", { class: `aa-engine${engine.deprecated ? " deprecated" : ""}`, "aria-labelledby": `eng-${engine.id}` },
      h("header", {}, serp.engineBadge(engine), h("code", { class: "aa-engine-id", text: engine.id })),
      h("h4", { id: `eng-${engine.id}`, class: "visually-hidden", text: engine.name }),
      h("p", { class: "aa-engine-desc", text: engine.description }),
      h("div", { class: "sk-chips" }, ...flags,
        serp.chip(`${engine.param_count} parameter${engine.param_count === 1 ? "" : "s"}`),
        ...required.map((name) => serp.chip(`${name} required`, "tone")),
        ...groups.map((g) => serp.chip(`${g.join(" or ")} required`, "tone")),
        serp.chip(paginationText(engine.pagination))),
      h("div", { class: "aa-engine-actions" },
        btn("Playground", { icon: "play", sm: true, kind: "", href: playgroundLink(engine.id), aria: `Open ${engine.name} in the Playground` }),
        btn("Docs", { icon: "book", sm: true, href: link("docs", { engine: engine.id }), aria: `Read the ${engine.name} docs` }),
        engine.docs_url && engine.provider !== "aira" ? h("a", { class: "btn ghost sm", href: engine.docs_url, target: "_blank", rel: "noopener noreferrer", title: "SerpApi's own page for this engine", "aria-label": `${engine.name} on serpapi.com (opens in a new tab)` }, ico("external")) : null));
  }

  tabs.engines = async (content, ctx) => {
    const catalogData = await loadEngines();
    const q0 = ctx.query;
    const state = { q: q0.q || "", category: q0.category || "", family: q0.family || "", hideOld: q0.hide === "1" };
    const search = h("input", { type: "search", id: "aaEngineSearch", placeholder: "Search by name, id or what it returns", value: state.q, autocomplete: "off", "aria-label": "Search engines", "aria-controls": "aaEngineResults" });
    const family = h("select", { id: "aaEngineFamily" }, h("option", { value: "", text: "Every provider" }), ...catalogData.families.map((f) => h("option", { value: f.id, text: `${f.id} (${f.count})`, selected: f.id === state.family })));
    const hide = serp.toggleSwitch("Hide deprecated and unlisted", state.hideOld, (on) => { state.hideOld = on; sync(); });
    const chipBar = h("div", { class: "aa-chipbar", role: "group", "aria-label": "Category" });
    const results = h("div", { id: "aaEngineResults" });
    const count = h("p", { class: "aa-count", role: "status", "aria-live": "polite" });
    const paintChips = () => {
      const all = [{ id: "", label: "All", count: catalogData.total }, ...catalogData.categories];
      chipBar.replaceChildren(...all.map((c) => h("button", { type: "button", class: "msgr-chip aa-chip", "aria-pressed": String(state.category === c.id), onclick: () => { state.category = c.id; sync(); } }, c.label, h("span", { class: "msgr-chip-count", text: String(c.count) }))));
    };
    const visible = () => {
      const needle = state.q.trim().toLowerCase();
      return catalogData.engines.filter((e) => {
        if (state.category && e.category !== state.category) return false;
        if (state.family && e.family !== state.family) return false;
        if (state.hideOld && (e.deprecated || e.unlisted)) return false;
        if (!needle) return true;
        return `${e.id} ${e.name} ${e.description} ${e.family} ${e.aira_use}`.toLowerCase().includes(needle);
      });
    };
    function sync() {
      paintChips();
      const found = visible();
      setQuery({ q: state.q, category: state.category, family: state.family, hide: state.hideOld ? "1" : "" });
      count.textContent = found.length === catalogData.total ? `${num(found.length)} engines` : `${num(found.length)} of ${num(catalogData.total)} engines`;
      if (!found.length) {
        results.replaceChildren(ui.empty("No engine matches", "Try a shorter word, or clear the category and provider filters.", btn("Clear filters", { kind: "", onClick: () => { state.q = ""; state.category = ""; state.family = ""; search.value = ""; family.value = ""; sync(); } })));
        return;
      }
      const groups = new Map();
      for (const engine of found) { if (!groups.has(engine.category)) groups.set(engine.category, []); groups.get(engine.category).push(engine); }
      const order = catalogData.categories.map((c) => c.id).filter((id) => groups.has(id));
      results.replaceChildren(...order.map((id) => h("section", { class: "aa-engine-group", "aria-label": categoryLabel(id) },
        h("h3", {}, categoryLabel(id), h("span", { class: "sk-count", text: String(groups.get(id).length) })),
        h("div", { class: "aa-engine-grid" }, ...groups.get(id).map(engineCard)))));
    }
    search.addEventListener("input", debounce(() => { state.q = search.value; sync(); }, 120));
    family.addEventListener("change", () => { state.family = family.value; sync(); });

    content.replaceChildren(
      h("div", { class: "aa-toolbar" },
        h("div", { class: "aa-toolbar-main" },
          h("div", { class: "sk-filter-row" }, ico("search"), Object.assign(search, { className: "sk-filter" })),
          ui.field("Provider", family)),
        h("div", { class: "aa-toolbar-side" }, hide.node, btn("Compare engines", { icon: "compare", onClick: () => compareEngines(catalogData, state.q) }))),
      chipBar, count, results);
    sync();
  };

  // ---- side-by-side engine comparison ----------------------------------------------------------------------------

  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const SUGGESTED = ["google", "bing", "duckduckgo", "google_light"];

  function compareEngines(catalogData, initialQuery = "") {
    const eligible = catalogData.engines.filter((e) => !e.deprecated && (e.required.includes("q") || (e.requires_any_of || []).some((g) => g.includes("q"))) && !e.required.some((name) => name !== "q"));
    const chosen = SUGGESTED.filter((id) => eligible.some((e) => e.id === id)).slice(0, 3);
    const MAX = 4;
    const query = h("input", { type: "text", id: "aaCmpQuery", placeholder: "coffee beans, kubernetes jobs, …", value: initialQuery, maxlength: "300", autocomplete: "off" });
    const picked = h("div", { class: "sk-chips aa-cmp-picked", "aria-label": "Engines to compare" });
    const add = engineSelect({ engines: eligible, categories: catalogData.categories }, "", { any: "Add an engine…", attrs: { "aria-label": "Add an engine to the comparison" } });
    const cost = h("p", { class: "sk-hint", role: "status" });
    const output = h("div", { class: "aa-cmp-out" });
    const runButton = btn("Run comparison", { icon: "play", kind: "" });
    const paintPicked = () => {
      picked.replaceChildren(...chosen.map((id) => h("span", { class: "sk-tag" }, h("span", { text: engineLabel(id) }), h("button", { type: "button", "aria-label": `Remove ${engineLabel(id)}`, onclick: () => { chosen.splice(chosen.indexOf(id), 1); paintPicked(); } }, ico("close")))));
      if (!chosen.length) picked.append(h("span", { class: "sk-dim", text: "Choose at least two engines." }));
      cost.textContent = `Uses up to ${chosen.length} search${chosen.length === 1 ? "" : "es"} of your plan. Results already in the cache cost nothing.`;
      runButton.disabled = chosen.length < 2;
      add.disabled = chosen.length >= MAX;
    };
    add.addEventListener("change", () => { if (add.value && !chosen.includes(add.value) && chosen.length < MAX) { chosen.push(add.value); paintPicked(); } add.value = ""; });
    runButton.addEventListener("click", () => run(runButton, async () => {
      const text = query.value.trim();
      if (!text) { toast("Type a query to compare.", "error"); query.focus(); return; }
      if (!(await serp.confirmCost(chosen.length, "Comparing engines"))) return;
      output.replaceChildren(loading(3));
      const settled = await Promise.allSettled(chosen.map((id) => api(`${BASE}/search`, { method: "POST", body: { engine: id, params: { q: text }, label: `Compare: ${text}` } })));
      renderComparison(output, chosen, settled);
    }));
    paintPicked();
    const body = h("div", { class: "aa-cmp" },
      h("p", { class: "muted", text: "Run one query on several engines and see where they agree. Only engines whose single required parameter is the query are offered." }),
      h("div", { class: "form-grid" }, ui.field("Query", query), ui.field("Engines", add, `Up to ${MAX}. Suggested: Google, Bing, DuckDuckGo.`)),
      picked, cost, h("div", { class: "btn-row" }, runButton), output);
    Aira.drawer({ title: "Compare engines", subtitle: "Same query, side by side, with overlap", body });
    setTimeout(() => query.focus(), 30);
  }

  function renderComparison(output, ids, settled) {
    const columns = settled.map((outcome, i) => {
      if (outcome.status === "rejected") return { id: ids[i], error: outcome.reason && outcome.reason.message ? outcome.reason.message : "The search failed.", items: [] };
      const record = outcome.value;
      const list = record.result && record.main_result_key && Array.isArray(record.result[record.main_result_key]) ? record.result[record.main_result_key] : [];
      return { id: ids[i], record, items: list.slice(0, 10).map((item) => ({ title: serp.logic.titleOfItem(item) || "Untitled", link: serp.logic.safeLink(item.link || item.url || item.product_link), host: hostOf(item.link || item.url || "") })) };
    });
    const hostSets = columns.map((c) => new Set(c.items.map((i) => i.host).filter(Boolean)));
    const hostCount = new Map();
    for (const set of hostSets) for (const host of set) hostCount.set(host, (hostCount.get(host) || 0) + 1);
    const okColumns = columns.filter((c) => c.record);
    const shared = [...hostCount].filter(([, n]) => n === okColumns.length && okColumns.length > 1).map(([host]) => host);
    const jaccard = (a, b) => { const union = new Set([...a, ...b]).size; return union ? [...a].filter((x) => b.has(x)).length / union : 0; };
    const matrix = okColumns.length > 1 ? simpleTable("Overlap between engines by site", ["", ...okColumns.map((c) => engineLabel(c.id))], okColumns.map((row, r) => [engineLabel(row.id), ...okColumns.map((col, c) => (r === c ? "–" : percent(jaccard(hostSets[columns.indexOf(row)], hostSets[columns.indexOf(col)]))))]), { cls: "aa-matrix" }) : null;
    fill(output,
      okColumns.length > 1 ? h("div", { class: "sk-note", role: "status" }, ico("check"), h("span", { text: shared.length ? `${pluralize(shared.length, "site")} appear on every engine: ${shared.slice(0, 6).join(", ")}${shared.length > 6 ? ", …" : ""}.` : "No site appears on every engine." })) : null,
      h("div", { class: "aa-cmp-cols" }, ...columns.map((c) => h("section", { class: "aa-cmp-col", "aria-label": engineLabel(c.id) },
        h("header", {}, serp.engineBadge({ id: c.id, name: engineLabel(c.id) }), c.record ? h("div", { class: "sk-chips" }, serp.statusBadge(c.record), serp.creditChip(c.record), serp.chip(c.record.cache_hit ? "cached" : ms(c.record.duration_ms))) : null),
        c.error ? h("div", { class: "sk-note error", role: "alert" }, ico("shield"), h("span", { text: c.error })) : null,
        c.record && !c.items.length ? h("p", { class: "sk-dim", text: c.record.note || "This engine returned no results for the query." }) : null,
        h("ol", { class: "aa-cmp-list" }, ...c.items.map((item, index) => h("li", {},
          h("span", { class: "sk-pos", text: String(index + 1) }),
          h("div", {}, item.link ? h("a", { href: item.link, target: "_blank", rel: "noopener noreferrer", text: item.title }) : h("strong", { text: item.title }),
            h("div", { class: "sk-dim" }, item.host || "", item.host && (hostCount.get(item.host) || 0) > 1 ? h("span", { class: "sk-chip good", text: `on ${hostCount.get(item.host)} of ${okColumns.length}` }) : null)))))
        , c.record ? btn("Open full result", { sm: true, kind: "ghost", onClick: () => openDetail(c.record.id, columns.filter((x) => x.record).map((x) => x.record.id)) }) : null))),
      matrix);
  }

  // ===========================================================================
  // History and archive
  // ===========================================================================

  const HISTORY_SORTS = {
    search: (r) => recordTitle(r).toLowerCase(),
    status: (r) => r.status + (r.cache_hit ? "~cache" : ""),
    source: (r) => r.source,
    results: (r) => r.result_count || 0,
    credits: (r) => r.credits || 0,
    duration: (r) => r.duration_ms || 0,
    when: (r) => r.created_at,
  };
  const PAGE_SIZES = [25, 50, 100, 200];

  tabs.history = async (content, ctx) => {
    const list = await loadEngines();
    const q = ctx.query;
    const state = {
      q: q.q || "", engine: q.engine || "", status: q.status || "", source: q.source || "", cache: q.cache || "", pinned: q.pinned || "",
      from: q.from || "", to: q.to || "", inside: q.inside === "1" || (q.inside === undefined && Aira.recall("airaapi.history.inside", false) === true),
      limit: PAGE_SIZES.includes(Number(Aira.recall("airaapi.history.limit", 25))) ? Number(Aira.recall("airaapi.history.limit", 25)) : 25,
      offset: 0, sort: "when", dir: "desc",
    };
    let items = [], total = 0, lastIndex = null, loadToken = 0;
    const selected = new Set();

    // -- filters
    const searchHint = () => state.inside ? "Search queries, labels and the stored results themselves" : "Search queries, labels, engines and errors";
    const search = h("input", { type: "search", id: "aaHistSearch", placeholder: searchHint(), value: state.q, autocomplete: "off" });
    const inside = serp.toggleSwitch("Inside results", state.inside, (on) => {
      state.inside = on;
      Aira.remember("airaapi.history.inside", on);
      search.placeholder = searchHint();
      state.offset = 0;
      setQuery({ inside: on ? "1" : "" });
      if (state.q.trim()) load();
    }, { id: "aaHistInside", hint: "Titles, snippets and links of every stored result" });
    const engine = engineSelect(list, state.engine, { any: "Any engine", attrs: { id: "aaHistEngine" } });
    const status = ui.select([["", "Any status"], ["ok", "Success"], ["error", "Error"], ["running", "Running"]], state.status, { id: "aaHistStatus" });
    const source = ui.select([["", "Any source"], ...Object.entries(SOURCE_LABELS)], state.source, { id: "aaHistSource" });
    const cache = ui.select([["", "Live and cached"], ["0", "Live only"], ["1", "Cached only"]], state.cache, { id: "aaHistCache" });
    const pinned = ui.select([["", "Pinned or not"], ["1", "Pinned only"], ["0", "Not pinned"]], state.pinned, { id: "aaHistPinned" });
    const from = h("input", { type: "date", id: "aaHistFrom", value: state.from });
    const to = h("input", { type: "date", id: "aaHistTo", value: state.to });
    const pageSize = ui.select(PAGE_SIZES.map((n) => [String(n), `${n} per page`]), String(state.limit), { id: "aaHistSize", "aria-label": "Searches per page" });
    const filterEls = { q: search, engine, status, source, cache, pinned, from, to };
    const apply = () => {
      for (const [key, el] of Object.entries(filterEls)) state[key] = el.value;
      state.offset = 0;
      setQuery({ q: state.q, engine: state.engine, status: state.status, source: state.source, cache: state.cache, pinned: state.pinned, from: state.from, to: state.to });
      load();
    };
    const debounced = debounce(apply, 250);
    search.addEventListener("input", debounced);
    for (const el of [engine, status, source, cache, pinned, from, to]) el.addEventListener("change", apply);
    pageSize.addEventListener("change", () => { state.limit = Number(pageSize.value); Aira.remember("airaapi.history.limit", state.limit); state.offset = 0; load(); });
    const activeFilters = () => Object.keys(filterEls).filter((k) => state[k] !== "");
    const reset = btn("Reset filters", { kind: "ghost", sm: true, onClick: () => { for (const el of Object.values(filterEls)) el.value = ""; apply(); search.focus(); } });
    const finder = h("input", { type: "text", id: "aaHistFind", placeholder: "Paste an Aira or SerpApi search id", autocomplete: "off", spellcheck: "false" });
    const findButton = btn("Find", { kind: "secondary", sm: true, icon: "search" });
    const find = async () => {
      const id = finder.value.trim();
      if (!id) { finder.focus(); return; }
      if (/^[0-9a-f]{32}$/i.test(id)) { openDetail(id.toLowerCase(), items.map((r) => r.id), { onChange: load }); return; }
      let offset = 0, scanned = 0;
      while (offset < 3000) {
        const page = await api(`${BASE}/searches?${qs({ limit: 200, offset })}`);
        const hit = page.items.find((r) => r.serpapi_id === id);
        if (hit) { openDetail(hit.id, page.items.map((r) => r.id), { onChange: load }); return; }
        scanned += page.items.length;
        offset += 200;
        if (offset >= page.total) break;
      }
      toast(`No stored search has the id ${id.length > 24 ? `${id.slice(0, 24)}…` : id}. Aira only keeps searches it ran itself (${num(scanned)} checked), and old ones are removed by the retention setting.`, "error", { timeout: 9000 });
    };
    findButton.addEventListener("click", () => run(findButton, find));
    finder.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run(findButton, find); } });

    // The search box is always visible; the other eight controls fold away on
    // phones so the results start near the top (open by default on wider screens).
    const moreSummary = h("summary", { class: "aa-filters-summary" });
    const paintMoreSummary = () => {
      const active = activeFilters().filter((k) => k !== "q").length;
      moreSummary.replaceChildren(ico("filter"), h("span", { text: "More filters" }), ...(active ? [h("span", { class: "badge", text: `${active} active` })] : []));
    };
    paintMoreSummary();
    const more = h("details", { class: "aa-filters-more", open: window.matchMedia("(min-width: 641px)").matches }, moreSummary,
      h("div", { class: "aa-filters-grid" },
        ui.field("Engine", engine), ui.field("Status", status), ui.field("Source", source), ui.field("Cache", cache), ui.field("Pinned", pinned), ui.field("From", from), ui.field("To", to),
        h("div", { class: "field aa-find" }, h("label", { for: "aaHistFind", text: "Find by search id (archive lookup)" }), h("div", { class: "aa-find-row" }, finder, findButton)),
        reset));
    for (const el of [engine, status, source, cache, pinned, from, to]) el.addEventListener("change", paintMoreSummary);
    reset.addEventListener("click", paintMoreSummary);
    const filters = h("section", { class: "filters aa-filters", "aria-label": "Filter history" },
      h("div", { class: "field grow" }, h("label", { for: "aaHistSearch", text: "Search" }), h("div", { class: "aa-search-row" }, h("div", { class: "sk-filter-row" }, ico("search"), Object.assign(search, { className: "sk-filter" })), inside.node)),
      more);

    // -- running queue
    const queue = h("section", { class: "aa-queue", hidden: true, "aria-label": "Async searches in progress" });
    let queueTimer = null, queueDelay = 3500;
    const paintQueue = (running) => {
      queue.hidden = !running.length;
      if (!running.length) return;
      queue.replaceChildren(h("div", { class: "aa-queue-head" }, h("span", { class: "sk-pulse", "aria-hidden": "true" }), h("strong", { text: `${pluralize(running.length, "search")} running` }), h("small", { class: "muted", text: "Aira checks SerpApi's archive with a growing delay and shows the result here when it is ready." })),
        h("ul", {}, ...running.slice(0, 6).map((r) => h("li", {}, h("button", { type: "button", class: "aa-link", onclick: () => openDetail(r.id, running.map((x) => x.id), { onChange: load }) }, recordTitle(r)), h("span", { class: "sk-dim", text: ` · ${r.engine_name} · started ` }), timeNode(r.created_at)))));
    };
    const pollQueue = async (running) => {
      clearTimeout(queueTimer);
      if (!running.length || !content.isConnected) return;
      queueTimer = setTimeout(async () => {
        if (!content.isConnected) return;
        let finished = 0;
        for (const r of running) {
          try { const fresh = await api(`${BASE}/searches/${r.id}`, { silent: true }); if (fresh.status !== "running") { finished += 1; toast(`Search finished: ${recordTitle(fresh)}`, fresh.status === "ok" ? "success" : "error", { timeout: 4000 }); } } catch { /* retry next round */ }
        }
        queueDelay = finished ? 3500 : Math.min(queueDelay * 1.5, 15000);
        if (finished) load(); else pollQueue(running);
      }, queueDelay);
    };

    // -- bulk bar and table
    const bulk = h("div", { class: "aa-bulk", hidden: true, role: "region", "aria-label": "Selected searches" });
    const tableHost = h("div", { class: "aa-table-host" });
    const pager = h("div", { class: "pager aa-pager" });
    const summary = h("p", { class: "aa-count", role: "status", "aria-live": "polite" });
    const sortHint = h("p", { class: "sk-hint", hidden: true, text: "Sorting applies to the searches on this page. Change the page size to sort more at once." });

    const selectedRecords = () => items.filter((r) => selected.has(r.id));
    const paintBulk = () => {
      const records = selectedRecords();
      bulk.hidden = !records.length;
      if (!records.length) return;
      const two = records.length === 2 && records.every((r) => r.status === "ok" && r.output === "json" && !r.result_truncated);
      const allPinned = records.every((r) => r.pinned);
      bulk.replaceChildren(
        h("strong", { class: "aa-bulk-n", text: `${records.length} selected` }),
        btn("Compare", { icon: "compare", sm: true, disabled: !two, title: two ? "Compare the two selected runs" : "Select exactly two successful JSON searches", onClick: () => { const [a, b] = [...records].sort((x, y) => x.created_at.localeCompare(y.created_at)); showDiff(a.id, b.id, load); } }),
        btn(allPinned ? "Unpin" : "Pin", { icon: "pin", sm: true, onClick: (event) => run(event.currentTarget, async () => { await Promise.all(records.map((r) => api(`${BASE}/searches/${r.id}`, { method: "PATCH", body: { pinned: !allPinned }, silent: true }))); toast(allPinned ? "Unpinned." : "Pinned. Retention will keep these.", "success", { timeout: 2200 }); selected.clear(); load(); }) }),
        btn("Export CSV", { icon: "download", sm: true, onClick: () => exportRows(records, "aira-selected-searches.csv") }),
        btn("Delete", { icon: "trash", sm: true, kind: "danger secondary", onClick: async (event) => {
          const pinnedCount = records.filter((r) => r.pinned).length;
          const ok = await Aira.confirm({ title: `Delete ${pluralize(records.length, "search")}?`, message: `${pinnedCount ? `${pluralize(pinnedCount, "of them is", "of them are")} pinned. ` : ""}Their stored results are removed for good. Running them again uses searches.`, confirmLabel: "Delete", danger: true });
          if (!ok) return;
          await run(event.currentTarget, async () => { await Promise.all(records.map((r) => api(`${BASE}/searches/${r.id}`, { method: "DELETE", silent: true }))); toast(`${pluralize(records.length, "search")} deleted.`, "success"); selected.clear(); load(); });
        } }),
        btn("Clear selection", { kind: "ghost", sm: true, onClick: () => { selected.clear(); paintTable(); paintBulk(); } }));
    };

    function exportRows(records, filename) {
      const columns = ["id", "serpapi_id", "engine", "query", "label", "status", "source", "created_at", "duration_ms", "http_status", "result_count", "credits", "cache_hit", "pinned", "error"].map((key) => ({ key }));
      const rows = records.map((r) => ({ ...r, query: querySummary(r), cache_hit: r.cache_hit ? "yes" : "no", pinned: r.pinned ? "yes" : "no" }));
      serp.downloadText(filename, serp.logic.rowsToCsv(columns, rows), "text/csv");
      toast(`Exported ${pluralize(records.length, "search")}.`, "success", { timeout: 2200 });
    }
    async function exportFiltered(button) {
      await run(button, async () => {
        const rows = [];
        for (let offset = 0; offset < Math.min(total, 5000); offset += 200) {
          const page = await api(`${BASE}/searches?${qs({ ...serverFilters(), limit: 200, offset })}`, { silent: true });
          rows.push(...page.items);
        }
        exportRows(rows, "aira-search-history.csv");
      });
    }

    const sortHeader = (key, label, { cls = "", num: isNum = false } = {}) => {
      const on = state.sort === key;
      const th = h("th", { scope: "col", class: `${cls}${isNum ? " num" : ""}`.trim(), "aria-sort": on ? (state.dir === "asc" ? "ascending" : "descending") : "none" },
        h("button", { type: "button", class: "aa-sort", onclick: () => { if (state.sort === key) state.dir = state.dir === "asc" ? "desc" : "asc"; else { state.sort = key; state.dir = key === "search" || key === "source" || key === "status" ? "asc" : "desc"; } paintTable(); } }, label, h("span", { class: "aa-sort-mark", "aria-hidden": "true", text: on ? (state.dir === "asc" ? "▲" : "▼") : "" })));
      return th;
    };

    const sorted = () => {
      const copy = [...items];
      const fn = HISTORY_SORTS[state.sort];
      copy.sort((a, b) => { const x = fn(a), y = fn(b); return (x < y ? -1 : x > y ? 1 : 0) * (state.dir === "asc" ? 1 : -1); });
      return copy;
    };

    function paintTable() {
      const rows = sorted();
      sortHint.hidden = state.sort === "when" && state.dir === "desc";
      const ids = rows.map((r) => r.id);
      const allBox = h("input", { type: "checkbox", "aria-label": "Select every search on this page", checked: rows.length > 0 && rows.every((r) => selected.has(r.id)) });
      allBox.addEventListener("change", () => { for (const r of rows) { if (allBox.checked) selected.add(r.id); else selected.delete(r.id); } paintTable(); paintBulk(); });
      const body = rows.map((r, index) => {
        const box = h("input", { type: "checkbox", checked: selected.has(r.id), "aria-label": `Select ${recordTitle(r)}` });
        box.addEventListener("click", (event) => {
          if (event.shiftKey && lastIndex !== null) { const [lo, hi] = [Math.min(lastIndex, index), Math.max(lastIndex, index)]; for (let i = lo; i <= hi; i += 1) selected.add(rows[i].id); paintTable(); paintBulk(); return; }
          lastIndex = index;
        });
        box.addEventListener("change", () => { if (box.checked) selected.add(r.id); else selected.delete(r.id); tr.classList.toggle("selected", box.checked); paintBulk(); });
        const pinBtn = h("button", { type: "button", class: `aa-icon-btn${r.pinned ? " on" : ""}`, "aria-pressed": String(r.pinned), "aria-label": r.pinned ? "Unpin" : "Pin", title: r.pinned ? "Unpin" : "Pin so retention keeps it" }, ico("pin"));
        pinBtn.addEventListener("click", async () => { try { const updated = await api(`${BASE}/searches/${r.id}`, { method: "PATCH", body: { pinned: !r.pinned } }); r.pinned = updated.pinned; pinBtn.classList.toggle("on", r.pinned); pinBtn.setAttribute("aria-pressed", String(r.pinned)); pinBtn.title = r.pinned ? "Unpin" : "Pin so retention keeps it"; pinBtn.setAttribute("aria-label", r.pinned ? "Unpin" : "Pin"); if (state.pinned) load(); } catch (error) { Aira.reportError(error); } });
        const rerun = h("button", { type: "button", class: "aa-icon-btn", "aria-label": `Run ${recordTitle(r)} again`, title: "Run again (uses a search unless it is still cached)" }, ico("refresh"));
        rerun.addEventListener("click", () => run(rerun, async () => { const fresh = await api(`${BASE}/searches/${r.id}/rerun`, { method: "POST", body: {} }); toast(fresh.cache_hit ? "Answered from the cache: no search used." : "Ran again.", "success", { timeout: 2600 }); load(); }));
        const open = () => openDetail(r.id, ids, { onChange: load });
        const tr = h("tr", { class: `${selected.has(r.id) ? "selected " : ""}${r.status === "error" ? "is-error" : ""}`.trim(), "data-id": r.id },
          h("td", { class: "aa-check" }, box),
          h("td", { class: "aa-main" },
            h("button", { type: "button", class: "aa-title", onclick: open }, recordTitle(r)),
            h("div", { class: "aa-sub" }, serp.engineBadge({ id: r.engine, name: r.engine_name }), ...paramChips(r, 3)),
            r.status === "error" && r.error ? h("div", { class: "aa-err", text: r.error }) : null,
            r.match_excerpt ? excerptNode(r.match_excerpt) : null,
            h("div", { class: "aa-mobile-meta" }, serp.statusBadge(r), serp.creditChip(r), h("span", { class: "sk-dim" }, timeNode(r.created_at)))),
          h("td", { class: "aa-status hide-sm" }, serp.statusBadge(r)),
          h("td", { class: "hide-md", text: sourceLabel(r.source) }),
          h("td", { class: "num hide-sm", text: r.status === "ok" ? num(r.result_count) : "–" }),
          h("td", { class: "hide-sm" }, serp.creditChip(r)),
          h("td", { class: "num hide-md", text: r.cache_hit ? "cache" : ms(r.duration_ms) }),
          h("td", { class: "hide-sm aa-when" }, timeNode(r.created_at)),
          h("td", { class: "aa-acts" }, pinBtn, rerun));
        tr.addEventListener("dblclick", open);
        return tr;
      });
      tableHost.replaceChildren(h("div", { class: "table-wrap aa-table-wrap" }, h("table", { class: "table aa-table" },
        h("caption", { class: "visually-hidden", text: "Stored searches. Activate a search title to open its details." }),
        h("thead", {}, h("tr", {}, h("th", { scope: "col", class: "aa-check" }, allBox), sortHeader("search", "Search"), sortHeader("status", "Status", { cls: "hide-sm" }), sortHeader("source", "Source", { cls: "hide-md" }), sortHeader("results", "Results", { cls: "hide-sm", num: true }), sortHeader("credits", "Credits", { cls: "hide-sm" }), sortHeader("duration", "Time taken", { cls: "hide-md", num: true }), sortHeader("when", "When", { cls: "hide-sm" }), h("th", { scope: "col", class: "aa-acts" }, h("span", { class: "visually-hidden", text: "Actions" })))),
        h("tbody", {}, ...body))));
    }

    function paintPager() {
      const first = total ? state.offset + 1 : 0;
      const last = Math.min(total, state.offset + items.length);
      summary.textContent = total ? `${num(first)}–${num(last)} of ${num(total)} searches${activeFilters().length ? " match these filters" : ""}` : "";
      pager.replaceChildren(
        h("span", { text: total ? `${num(first)}–${num(last)} of ${num(total)}` : "" }),
        h("div", { class: "btn-row" }, pageSize,
          btn("Previous", { icon: "left", kind: "secondary", sm: true, disabled: state.offset === 0, onClick: () => { state.offset = Math.max(0, state.offset - state.limit); load(); } }),
          btn("Next", { icon: "right", kind: "secondary", sm: true, disabled: state.offset + state.limit >= total, onClick: () => { state.offset += state.limit; load(); } }),
          btn("Export all matches", { icon: "download", kind: "ghost", sm: true, disabled: !total, onClick: (event) => exportFiltered(event.currentTarget) })));
      pager.hidden = !total;
    }

    function serverFilters() {
      return { q: state.q.trim(), engine: state.engine, status: state.status, source: state.source, cache: state.cache, pinned: state.pinned, from: state.from, to: state.to, in_results: state.inside && state.q.trim() ? "1" : "" };
    }

    async function load() {
      const token = (loadToken += 1);
      tableHost.setAttribute("aria-busy", "true");
      try {
        const [page, running] = await Promise.all([
          api(`${BASE}/searches?${qs({ ...serverFilters(), limit: state.limit, offset: state.offset })}`, { silent: true }),
          state.status && state.status !== "running" ? Promise.resolve({ items: [] }) : api(`${BASE}/searches?status=running&limit=20`, { silent: true }),
        ]);
        if (token !== loadToken) return;
        if (page.total && !page.items.length && state.offset > 0) { state.offset = Math.max(0, (Math.ceil(page.total / state.limit) - 1) * state.limit); load(); return; }
        items = page.items; total = page.total;
        for (const id of [...selected]) if (!items.some((r) => r.id === id)) selected.delete(id);
        paintQueue(running.items); pollQueue(running.items);
        if (!items.length) {
          tableHost.replaceChildren(activeFilters().length
            ? ui.empty("No searches match", "Loosen a filter, or clear them all to see the full history.", btn("Reset filters", { kind: "", onClick: () => reset.click() }))
            : ui.empty("No searches stored yet", "Every search Aira runs is kept here for the retention period, with its parameters, result and cost. Run one in the Playground to start the archive.", btn("Open Playground", { icon: "play", kind: "", href: link("playground") })));
        } else paintTable();
        paintPager(); paintBulk();
      } catch (error) {
        if (token !== loadToken) return;
        tableHost.replaceChildren(failure(error, load));
      } finally { tableHost.removeAttribute("aria-busy"); }
    }

    // -- clean up
    const cleanup = h("details", { class: "aa-cleanup" }, h("summary", { text: "Clean up history" }), h("div", { class: "aa-cleanup-body" },
      h("p", { class: "muted", text: "Aira keeps searches for the retention period you set in Account (30 days by default) and prunes the oldest beyond the record limit. Pinned searches are always kept unless you say otherwise here." }),
      h("div", { class: "btn-row" },
        btn("Delete older than…", { icon: "trash", kind: "danger secondary", sm: true, onClick: () => cleanupDialog() }),
        btn("Delete every unpinned search", { icon: "trash", kind: "danger secondary", sm: true, onClick: async () => {
          const ok = await Aira.confirm({ title: "Delete every unpinned search?", message: `This removes all stored results except pinned ones (${num(total)} searches match the current view, more may exist). It cannot be undone.`, confirmLabel: "Delete all unpinned", danger: true });
          if (!ok) return;
          try { const r = await api(`${BASE}/searches?all=1`, { method: "DELETE" }); toast(`Deleted ${pluralize(r.deleted, "search")}. ${r.kept_pinned ? `${pluralize(r.kept_pinned, "pinned search")} kept.` : ""}`, "success"); selected.clear(); state.offset = 0; load(); } catch (error) { Aira.reportError(error); }
        } }))));
    function cleanupDialog() {
      const days = ui.select([["7", "7 days"], ["14", "14 days"], ["30", "30 days"], ["60", "60 days"], ["90", "90 days"]], "30", { id: "aaCleanDays" });
      const includePinned = h("input", { type: "checkbox", id: "aaCleanPinned" });
      Aira.modal({ title: "Delete older searches", body: h("div", { class: "form-grid" }, ui.field("Older than", days), h("label", { class: "check wide" }, includePinned, "Also delete pinned searches")), actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Delete", kind: "danger", onClick: async () => {
          const before = new Date(Date.now() - Number(days.value) * 86400000).toISOString();
          try { const r = await api(`${BASE}/searches?${qs({ before, include_pinned: includePinned.checked ? "1" : "" })}`, { method: "DELETE" }); toast(`Deleted ${pluralize(r.deleted, "search")}${r.kept_pinned ? `, kept ${pluralize(r.kept_pinned, "pinned search")}` : ""}.`, "success"); selected.clear(); load(); } catch (error) { Aira.reportError(error); return false; }
          return undefined;
        } },
      ] });
    }

    content.replaceChildren(filters, queue, summary, bulk, tableHost, sortHint, pager, cleanup);
    await load();
    if (q.search && /^[0-9a-f]{32}$/.test(q.search)) openDetail(q.search, items.map((r) => r.id), { onChange: load });
  };

  // ===========================================================================
  // Locations
  // ===========================================================================

  const LOCATION_SUGGESTIONS = ["Austin", "New York", "London", "Bengaluru", "Toronto", "Berlin", "Sydney", "Seattle"];

  tabs.locations = async (content, ctx) => {
    const state = { q: ctx.query.q || "" };
    let token = 0;
    const input = h("input", { type: "search", id: "aaLocQuery", placeholder: "City, region, university or country", value: state.q, autocomplete: "off", "aria-describedby": "aaLocHint" });
    const results = h("div", { class: "aa-loc-results", "aria-live": "polite" });
    const recent = h("div", { class: "sk-chips aa-loc-recent" });
    const recents = () => { const list = Aira.recall("airaapi.locations.recent", []); return Array.isArray(list) ? list.filter((x) => typeof x === "string").slice(0, 8) : []; };
    const remember = (text) => Aira.remember("airaapi.locations.recent", [text, ...recents().filter((x) => x.toLowerCase() !== text.toLowerCase())].slice(0, 8));
    const paintRecent = () => {
      const past = recents();
      recent.replaceChildren(h("span", { class: "sk-dim", text: past.length ? "Recent:" : "Try:" }), ...(past.length ? past : LOCATION_SUGGESTIONS).map((text) => h("button", { type: "button", class: "sk-chip link", onclick: () => { input.value = text; search(); } }, text)));
    };

    const rowFor = (loc) => {
      const gps = Array.isArray(loc.gps) && loc.gps.length === 2 ? { lon: Number(loc.gps[0]), lat: Number(loc.gps[1]) } : null;
      const mapUrl = gps && Number.isFinite(gps.lat) && Number.isFinite(gps.lon) ? `https://www.openstreetmap.org/?mlat=${gps.lat}&mlon=${gps.lon}#map=11/${gps.lat}/${gps.lon}` : null;
      return h("tr", {},
        h("td", { class: "aa-main" }, h("strong", { text: loc.name }), h("div", { class: "aa-sub aa-loc-canon" }, h("code", { text: loc.canonical_name }), serp.copyButton(loc.canonical_name, { label: `Copy canonical name for ${loc.name}`, message: "Canonical name copied" })),
          h("div", { class: "aa-mobile-meta" }, serp.chip(loc.target_type), serp.chip(loc.country_code), loc.reach ? serp.chip(`${compactNum(loc.reach)} reach`) : null)),
        h("td", { class: "hide-sm", text: loc.target_type || "–" }),
        h("td", { class: "hide-sm", text: loc.country_code || "–" }),
        h("td", { class: "num hide-sm", text: loc.reach ? num(loc.reach) : "–" }),
        h("td", { class: "hide-md" }, gps ? h("span", { class: "aa-gps" }, `${gps.lat.toFixed(3)}, ${gps.lon.toFixed(3)}`) : h("span", { class: "sk-dim", text: "–" })),
        h("td", { class: "aa-loc-acts" },
          btn("Use in Playground", { icon: "play", sm: true, kind: "", href: playgroundLink("google", { location: loc.canonical_name }), aria: `Use ${loc.name} in the Playground` }),
          serp.copyButton(loc.id, { label: `Copy id of ${loc.name}`, message: "Location id copied" }),
          mapUrl ? h("a", { class: "btn ghost sm", href: mapUrl, target: "_blank", rel: "noopener noreferrer", title: "Open on OpenStreetMap", "aria-label": `Show ${loc.name} on OpenStreetMap (opens in a new tab)` }, ico("external")) : null));
    };

    async function search() {
      const text = input.value.trim();
      state.q = text;
      setQuery({ q: text });
      if (text.length < 2) { results.replaceChildren(ui.empty("Find a location", "Type at least two characters. Results are sorted by reach, so bigger places come first.")); return; }
      const mine = (token += 1);
      results.replaceChildren(loading(3));
      try {
        const data = await api(`${BASE}/locations?${qs({ q: text, limit: 10 })}`, { silent: true });
        if (mine !== token) return;
        remember(text); paintRecent();
        if (!data.locations.length) { results.replaceChildren(ui.empty("No location found", `Nothing matches "${text}". Try the English name of the place or fewer words.`)); return; }
        results.replaceChildren(
          h("p", { class: "aa-count", role: "status", text: `${pluralize(data.locations.length, "match", "matches")}${data.locations.length === 10 ? ". Showing the 10 largest; type more of the name to narrow it" : ""}.` }),
          h("div", { class: "table-wrap aa-table-wrap" }, h("table", { class: "table aa-table aa-loc-table" },
            h("caption", { class: "visually-hidden", text: `Locations matching ${text}` }),
            h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Location" }), h("th", { scope: "col", class: "hide-sm", text: "Type" }), h("th", { scope: "col", class: "hide-sm", text: "Country" }), h("th", { scope: "col", class: "num hide-sm", text: "Reach" }), h("th", { scope: "col", class: "hide-md", text: "GPS (lat, lon)" }), h("th", { scope: "col" }, h("span", { class: "visually-hidden", text: "Actions" })))),
            h("tbody", {}, ...data.locations.map(rowFor)))));
      } catch (error) { if (mine === token) results.replaceChildren(failure(error, search)); }
    }
    input.addEventListener("input", debounce(search, 300));
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); search(); } });
    paintRecent();
    content.replaceChildren(
      panel("Locations", { sub: "Google location targets for the location parameter", body: panelBody(
        h("div", { class: "field" }, h("label", { for: "aaLocQuery", text: "Search locations" }), h("div", { class: "sk-filter-row" }, ico("search"), Object.assign(input, { className: "sk-filter" })),
          h("span", { class: "hint", id: "aaLocHint", text: "Free: lookups never use a search and need no SerpApi key. Answers are cached for 24 hours." })),
        recent) }),
      results);
    if (state.q.length >= 2) search(); else results.replaceChildren(ui.empty("Find a location", "Type at least two characters. Results are sorted by reach, so bigger places come first."));
  };

  // ===========================================================================
  // Monitors and saved searches
  // ===========================================================================

  const INTERVALS = [[15, "Every 15 minutes"], [30, "Every 30 minutes"], [60, "Hourly"], [180, "Every 3 hours"], [360, "Every 6 hours"], [720, "Every 12 hours"], [1440, "Daily"], [10080, "Weekly"]];
  const monthlyFor = (minutes) => Math.ceil((30 * 24 * 60) / Math.max(1, minutes));
  const intervalSelect = (value, attrs = {}) => {
    const options = INTERVALS.some(([m]) => m === Number(value)) ? INTERVALS : [...INTERVALS, [Number(value), intervalLabel(value)]].sort((a, b) => a[0] - b[0]);
    return ui.select(options.map(([m, label]) => [String(m), label]), String(value), attrs);
  };
  /** Cost line for a schedule: how many searches a month it would use, set against what is left on the plan. */
  function costNote(minutes, account, budget = 0) {
    const schedule = monthlyFor(minutes);
    const estimate = budget > 0 ? Math.min(schedule, budget) : schedule;
    const capped = budget > 0 && budget < schedule ? ` The budget caps the schedule's ${num(schedule)}; once it is used up the monitor waits for the next month.` : "";
    const left = account ? account.total_searches_left ?? account.plan_searches_left : null;
    const heavy = left !== null && left !== undefined && estimate > left * 0.25;
    const text = left !== null && left !== undefined ? `About ${num(estimate)} searches a month (${Math.round((estimate / Math.max(1, left)) * 100)}% of the ${num(left)} you have left). Monitors always run live, never from the cache.${capped}` : `About ${num(estimate)} searches a month. Monitors always run live, never from the cache.${capped} Connect a key to compare this with your plan.`;
    return { estimate, heavy, node: h("div", { class: `sk-note${heavy ? " warn" : ""}`, role: "status" }, ico(heavy ? "shield" : "clock"), h("span", { text })) };
  }
  function budgetInput(value, attrs = {}) {
    return h("input", { type: "number", min: "0", max: "100000", step: "1", inputmode: "numeric", value: String(value || 0), class: "aa-budget-input", ...attrs });
  }

  async function paramFormDialog({ title, engineId, name = "", tags = [], params = {}, lockEngine = false, confirmLabel, onSubmit, withRun = false }) {
    const list = await loadEngines();
    const nameInput = h("input", { type: "text", id: "aaSavedName", maxlength: "120", value: name, placeholder: "Remote architect roles, Seattle", autocomplete: "off" });
    const tagsInput = h("input", { type: "text", id: "aaSavedTags", value: tags.join(", "), placeholder: "jobs, weekly", autocomplete: "off" });
    const engineField = engineSelect(list, engineId || "google", { attrs: { id: "aaSavedEngine", disabled: lockEngine || null } });
    const formHost = h("div", { class: "aa-form-host" }, loading(2));
    let form = null;
    const mount = async (id, initial) => {
      formHost.replaceChildren(loading(2));
      try {
        const entry = await loadEngine(id);
        form = serp.paramForm(entry, initial || {}, { compact: true });
        formHost.replaceChildren(form.node);
      } catch (error) { form = null; formHost.replaceChildren(failure(error, () => mount(id, initial))); }
    };
    engineField.addEventListener("change", () => mount(engineField.value, {}));
    mount(engineField.value, params);
    const body = h("div", { class: "aa-dialog" },
      h("div", { class: "form-grid" }, ui.field("Name", nameInput), ui.field("Tags", tagsInput, "Optional, separated by commas.")),
      ui.field("Engine", engineField, lockEngine ? "The engine of a saved search cannot change; make a new one instead." : null),
      formHost);
    const submit = async (close, andRun) => {
      if (!nameInput.value.trim()) { toast("Give the saved search a name.", "error"); nameInput.focus(); return false; }
      if (!form) { toast("The parameter form is not ready.", "error"); return false; }
      const problems = form.validate();
      if (problems.length) { toast(problems[0], "error"); return false; }
      const payload = { name: nameInput.value.trim(), engine: engineField.value, params: form.getValues(), tags: tagsInput.value.split(",").map((t) => t.trim()).filter(Boolean) };
      try { await onSubmit(payload, andRun); } catch (error) { Aira.reportError(error); return false; }
      return undefined;
    };
    Aira.modal({ title, wide: true, body, actions: [
      { label: "Cancel", kind: "ghost" },
      ...(withRun ? [{ label: `${confirmLabel} and run`, kind: "secondary", onClick: (close) => submit(close, true) }] : []),
      { label: confirmLabel, kind: "", onClick: (close) => submit(close, false) },
    ] });
    setTimeout(() => nameInput.focus(), 30);
  }

  tabs.monitors = async (content) => {
    const [savedData, monitorData] = await Promise.all([api(`${BASE}/saved`), api(`${BASE}/monitors`)]);
    let saved = savedData.saved, monitors = monitorData.monitors;
    const limits = monitorData.limits || { min_interval_minutes: 15, max_monitors: 20 };
    const account = await api(`${BASE}/overview`, { silent: true }).then((data) => data.account).catch(() => null);
    const filter = { q: "", tag: "" };

    const reload = async () => {
      const [s, m] = await Promise.all([api(`${BASE}/saved`, { silent: true }), api(`${BASE}/monitors`, { silent: true })]);
      saved = s.saved; monitors = m.monitors;
      paint();
    };
    const monitorOf = (savedId) => monitors.find((m) => m.saved_id === savedId);

    const runSaved = (item, button) => run(button, async () => {
      const record = await api(`${BASE}/saved/${item.id}/run`, { method: "POST", body: {} });
      toast(record.cache_hit ? "Answered from the cache: no search used." : `Ran "${item.name}": ${num(record.result_count)} results.`, "success", { timeout: 3000 });
      await reload();
      openDetail(record.id, [record.id]);
    });

    const savedForm = (existing) => paramFormDialog(existing
      ? { title: "Edit saved search", engineId: existing.engine, name: existing.name, tags: existing.tags, params: existing.params, lockEngine: true, confirmLabel: "Save changes", onSubmit: async (payload) => { await api(`${BASE}/saved/${existing.id}`, { method: "PATCH", body: { name: payload.name, tags: payload.tags, params: payload.params } }); toast("Saved search updated.", "success"); await reload(); } }
      : { title: "New saved search", withRun: true, confirmLabel: "Save", onSubmit: async (payload, andRun) => { const created = await api(`${BASE}/saved`, { method: "POST", body: payload }); toast("Saved search created.", "success"); await reload(); if (andRun) { const record = await api(`${BASE}/saved/${created.id}/run`, { method: "POST", body: {} }); await reload(); openDetail(record.id, [record.id]); } } });

    const monitorDialog = (preselect) => {
      const free = saved.filter((s) => !monitorOf(s.id));
      if (!free.length) {
        Aira.modal({ title: saved.length ? "Every saved search is monitored" : "Save a search first", body: h("p", { class: "muted", text: saved.length ? "A saved search can have one monitor. Delete a monitor, or create another saved search." : "A monitor re-runs a saved search on a schedule. Save the search you want to watch first." }), actions: [{ label: "Close", kind: "ghost" }, { label: "New saved search", kind: "", onClick: () => { setTimeout(() => savedForm(null), 0); } }] });
        return;
      }
      const which = h("select", { id: "aaMonSaved" }, ...free.map((s) => h("option", { value: s.id, text: `${s.name} · ${s.engine_name}`, selected: s.id === preselect })));
      const every = intervalSelect(1440, { id: "aaMonEvery" });
      const notify = serp.toggleSwitch("Notify me when the results change", true, () => {}, { hint: "A notification links to the comparison." });
      const budget = budgetInput(0, { id: "aaMonBudget" });
      const pauseOnError = serp.toggleSwitch("Pause if a run fails", false, () => {}, { hint: "Stops a broken search from failing on every run; you get a notification." });
      const noteHost = h("div");
      const refresh = () => noteHost.replaceChildren(costNote(Number(every.value), account, Number(budget.value) || 0).node);
      every.addEventListener("change", refresh);
      budget.addEventListener("input", refresh);
      refresh();
      Aira.modal({ title: "New monitor", body: h("div", { class: "aa-dialog" }, h("div", { class: "form-grid" }, ui.field("Saved search", which), ui.field("Run", every, `No faster than every ${limits.min_interval_minutes} minutes.`), ui.field("Monthly budget", budget, "Most live searches a month. 0 means no cap.")), notify.node, pauseOnError.node, noteHost), actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Start monitoring", kind: "", onClick: async () => {
          const cap = Number(budget.value) || 0;
          const estimate = costNote(Number(every.value), account, cap);
          if (estimate.heavy && !(await Aira.confirm({ title: "This schedule is expensive", message: `${num(estimate.estimate)} searches a month is a large share of what you have left. Start it anyway, or set a monthly budget?`, confirmLabel: "Start anyway" }))) return false;
          try { await api(`${BASE}/monitors`, { method: "POST", body: { saved_id: which.value, interval_minutes: Number(every.value), notify_on_change: notify.get(), monthly_budget: cap, pause_on_error: pauseOnError.get() } }); toast("Monitor started. Its first run is a baseline.", "success"); await reload(); } catch (error) { Aira.reportError(error); return false; }
          return undefined;
        } },
      ] });
    };

    // -- monitors
    const monitorCard = (m) => {
      const failing = /^Run failed/.test(m.last_change_summary || "");
      const waiting = m.budget_exhausted;
      const changed = m.last_change_summary && !failing && !waiting && !/^(No differences|Baseline|Monthly budget)/.test(m.last_change_summary);
      const every = intervalSelect(m.interval_minutes, { "aria-label": `How often to run ${m.saved_name}` });
      const estimate = h("div", { class: "aa-mon-cost" });
      const paintEstimate = (minutes) => { const cost = costNote(minutes, account, m.monthly_budget); estimate.replaceChildren(serp.chip(`≈ ${num(cost.estimate)} searches a month`, cost.heavy ? "warn" : ""), h("small", { class: "sk-dim", text: ` ${intervalLabel(minutes)}` })); };
      paintEstimate(m.interval_minutes);
      const budget = budgetInput(m.monthly_budget, { "aria-label": `Monthly budget for ${m.saved_name} (0 for no cap)` });
      budget.addEventListener("change", async () => {
        const next = Math.max(0, Math.floor(Number(budget.value) || 0));
        if (next === m.monthly_budget) return;
        try { await api(`${BASE}/monitors/${m.id}`, { method: "PATCH", body: { monthly_budget: next } }); toast(next ? `Budget set to ${pluralize(next, "search", "searches")} a month.` : "Budget removed: the schedule decides.", "success", { timeout: 2400 }); await reload(); } catch (error) { budget.value = String(m.monthly_budget); Aira.reportError(error); }
      });
      const budgetMeter = m.monthly_budget
        ? h("div", { class: `aa-mon-budget${waiting ? " out" : ""}` }, meter({ value: m.month_runs, max: m.monthly_budget, label: "Budget used this month", tone: waiting ? "bad" : m.month_runs >= m.monthly_budget * 0.8 ? "warn" : "", valueText: `${num(m.month_runs)} of ${num(m.monthly_budget)}` }),
          h("small", { class: "sk-dim" }, waiting ? ["Used up. Runs resume ", timeNode(m.budget_resets_at, { future: true }), "."] : `${pluralize(m.budget_remaining, "search", "searches")} left this month.`))
        : h("div", { class: "aa-mon-budget" }, h("small", { class: "sk-dim", text: `${pluralize(m.month_runs, "live run")} this month · no budget cap` }));
      every.addEventListener("change", async () => {
        const cost = costNote(Number(every.value), account, m.monthly_budget);
        if (cost.heavy && !(await Aira.confirm({ title: "This schedule is expensive", message: `${num(cost.estimate)} searches a month is a large share of what you have left. Use it anyway?`, confirmLabel: "Use it" }))) { every.value = String(m.interval_minutes); return; }
        try { await api(`${BASE}/monitors/${m.id}`, { method: "PATCH", body: { interval_minutes: Number(every.value) } }); toast("Schedule updated.", "success", { timeout: 2200 }); await reload(); } catch (error) { every.value = String(m.interval_minutes); Aira.reportError(error); }
      });
      const enabled = serp.toggleSwitch(m.enabled ? "Running on schedule" : "Paused", m.enabled, async (on) => {
        try { await api(`${BASE}/monitors/${m.id}`, { method: "PATCH", body: { enabled: on } }); toast(on ? "Monitor resumed." : "Monitor paused. It will not use any searches.", "success", { timeout: 2200 }); await reload(); } catch (error) { enabled.set(!on); Aira.reportError(error); }
      });
      const notify = serp.toggleSwitch("Notify on change", m.notify_on_change, async (on) => {
        try { await api(`${BASE}/monitors/${m.id}`, { method: "PATCH", body: { notify_on_change: on } }); toast(on ? "You will be notified when results change." : "Notifications off for this monitor.", "success", { timeout: 2200 }); } catch (error) { notify.set(!on); Aira.reportError(error); }
      });
      const pauseOnError = serp.toggleSwitch("Pause if a run fails", m.pause_on_error, async (on) => {
        try { await api(`${BASE}/monitors/${m.id}`, { method: "PATCH", body: { pause_on_error: on } }); toast(on ? "A failed run will pause this monitor." : "Failed runs will be retried on schedule.", "success", { timeout: 2200 }); } catch (error) { pauseOnError.set(!on); Aira.reportError(error); }
      });
      const runNow = btn("Run now", { icon: "play", sm: true, kind: "", disabled: waiting, title: waiting ? "This month's budget is used up. Raise it to run now." : "", onClick: (event) => run(event.currentTarget, async () => {
        if (!(await serp.confirmCost(1, "Running this monitor now"))) return;
        const outcome = await api(`${BASE}/monitors/${m.id}/run`, { method: "POST", body: {} });
        await reload();
        if (outcome.diff && outcome.diff.changed_any) { toast(`Changed: ${outcome.summary}`, "success", { timeout: 4500 }); showDiff(outcome.diff.a.id, outcome.diff.b.id, reload); }
        else toast(outcome.summary, "success", { timeout: 3500 });
      }) });
      return h("article", { class: `aa-mon ${failing ? "failing" : ""} ${m.enabled ? "" : "paused"}`.trim(), "aria-label": `Monitor ${m.saved_name}` },
        h("header", {}, h("div", {}, h("h4", { text: m.saved_name }), h("div", { class: "sk-chips" }, serp.engineBadge({ id: m.engine, name: m.engine_name }), ...paramChips({ params: m.params }, 3))),
          h("span", { class: `badge aa-mon-state ${failing ? "err" : waiting ? "wait" : m.enabled ? "on" : "off"}`, text: failing ? "Failing" : !m.enabled ? "Paused" : waiting ? "Budget used" : "Active" })),
        h("div", { class: "aa-mon-grid" },
          ui.field("Schedule", every), estimate,
          ui.field("Monthly budget", budget, "0 means no cap"), budgetMeter,
          h("dl", { class: "kv aa-facts" }, h("dt", { text: "Next run" }), h("dd", {}, m.enabled ? timeNode(m.next_run_at, { future: true }) : h("span", { class: "sk-dim", text: "paused" })), h("dt", { text: "Last run" }), h("dd", {}, timeNode(m.last_run_at)))),
        h("div", { class: `sk-note aa-mon-summary ${failing ? "error" : changed || waiting ? "warn" : ""}`, role: "status" }, ico(failing || waiting ? "shield" : changed ? "pulse" : "check"), h("span", { text: m.last_change_summary || "No run yet. The first run captures a baseline to compare against." })),
        h("div", { class: "aa-mon-toggles" }, enabled.node, notify.node, pauseOnError.node),
        h("footer", { class: "btn-row" }, runNow,
          m.last_search_id ? btn("Last result", { sm: true, onClick: () => openDetail(m.last_search_id, [m.last_search_id]) }) : null,
          btn("Run history", { sm: true, kind: "ghost", href: link("history", { source: "monitor", q: `Monitor: ${m.saved_name}` }) }),
          btn("Delete", { icon: "trash", sm: true, kind: "danger secondary", onClick: async () => { if (!(await Aira.confirm({ title: "Delete this monitor?", message: `"${m.saved_name}" stops running. The saved search and its history stay.`, confirmLabel: "Delete monitor", danger: true }))) return; try { await api(`${BASE}/monitors/${m.id}`, { method: "DELETE" }); toast("Monitor deleted.", "success"); await reload(); } catch (error) { Aira.reportError(error); } } })));
    };

    const savedCard = (s) => {
      const monitor = monitorOf(s.id);
      const pin = h("button", { type: "button", class: `aa-icon-btn${s.pinned ? " on" : ""}`, "aria-pressed": String(s.pinned), "aria-label": s.pinned ? `Unpin ${s.name}` : `Pin ${s.name}`, title: s.pinned ? "Unpin" : "Pin to the top" }, ico("pin"));
      pin.addEventListener("click", async () => { try { await api(`${BASE}/saved/${s.id}`, { method: "PATCH", body: { pinned: !s.pinned } }); await reload(); } catch (error) { Aira.reportError(error); } });
      const runBtn = btn("Run", { icon: "play", sm: true, kind: "", onClick: (event) => runSaved(s, event.currentTarget) });
      return h("article", { class: "aa-saved", "aria-label": `Saved search ${s.name}` },
        h("header", {}, h("div", {}, h("h4", { text: s.name }), serp.engineBadge({ id: s.engine, name: s.engine_name })), pin),
        h("div", { class: "sk-chips" }, ...paramChips({ params: s.params }, 5)),
        s.tags.length ? h("div", { class: "sk-chips aa-tags", "aria-label": "Tags" }, ...s.tags.map((t) => h("button", { type: "button", class: "sk-chip tone", title: `Show only ${t}`, onclick: () => { filter.tag = filter.tag === t ? "" : t; paint(); } }, `#${t}`))) : null,
        h("p", { class: "sk-dim aa-saved-last" }, s.last_run_at ? ["Last run ", timeNode(s.last_run_at), s.last_search_id ? [" · ", h("button", { type: "button", class: "aa-link", onclick: () => openDetail(s.last_search_id, [s.last_search_id]) }, "view result")] : null] : "Never run", monitor ? [" · ", serp.chip(monitor.enabled ? `monitored ${intervalLabel(monitor.interval_minutes).toLowerCase()}` : "monitor paused", "good")] : null),
        h("footer", { class: "btn-row" }, runBtn,
          btn("Playground", { sm: true, href: playgroundLink(s.engine, s.params) }),
          monitor ? null : btn("Monitor", { icon: "bell", sm: true, onClick: () => monitorDialog(s.id) }),
          btn("Edit", { icon: "edit", sm: true, kind: "ghost", onClick: () => savedForm(s) }),
          btn("Delete", { icon: "trash", sm: true, kind: "danger secondary", aria: `Delete ${s.name}`, onClick: async () => {
            if (!(await Aira.confirm({ title: "Delete this saved search?", message: monitor ? `"${s.name}" and its monitor are removed. Stored results stay in History.` : `"${s.name}" is removed. Stored results stay in History.`, confirmLabel: "Delete", danger: true }))) return;
            try { await api(`${BASE}/saved/${s.id}`, { method: "DELETE" }); toast("Saved search deleted.", "success"); await reload(); } catch (error) { Aira.reportError(error); }
          } })));
    };

    const monitorsHost = h("div");
    const savedHost = h("div");
    const summaryHost = h("div", { class: "stats aa-stats" });
    function paint() {
      const active = monitors.filter((m) => m.enabled);
      const failing = monitors.filter((m) => /^Run failed/.test(m.last_change_summary || ""));
      const burn = active.reduce((sum, m) => sum + m.estimated_monthly_searches, 0);
      const left = account ? account.total_searches_left ?? account.plan_searches_left : null;
      summaryHost.replaceChildren(
        statTile({ label: "Monitors", value: `${active.length} / ${monitors.length}`, sub: "active of total" }),
        statTile({ label: "Monthly burn", value: num(burn), sub: left !== null && left !== undefined ? `${Math.round((burn / Math.max(1, left)) * 100)}% of ${num(left)} left` : "searches a month", tone: left && burn > left * 0.5 ? "bad" : "" }),
        statTile({ label: "Failing", value: num(failing.length), sub: failing.length ? "check the key and quota" : "all runs succeeded", tone: failing.length ? "bad" : "" }),
        statTile({ label: "Saved searches", value: num(saved.length), sub: `${num(saved.filter((s) => monitorOf(s.id)).length)} monitored` }));
      monitorsHost.replaceChildren(monitors.length
        ? h("div", { class: "aa-card-grid" }, ...monitors.map(monitorCard))
        : ui.empty("No monitors yet", "A monitor re-runs a saved search on a schedule and tells you when the results change: rank tracking, price watching, job alerts.", btn("New monitor", { icon: "bell", kind: "", onClick: () => monitorDialog() })));
      const needle = filter.q.trim().toLowerCase();
      const shown = saved.filter((s) => (!filter.tag || s.tags.includes(filter.tag)) && (!needle || `${s.name} ${s.engine_name} ${s.tags.join(" ")} ${JSON.stringify(s.params)}`.toLowerCase().includes(needle)))
        .sort((a, b) => Number(b.pinned) - Number(a.pinned) || a.name.localeCompare(b.name));
      const tags = [...new Set(saved.flatMap((s) => s.tags))].sort();
      fill(savedHost,
        saved.length ? h("div", { class: "aa-saved-tools" },
          h("div", { class: "sk-filter-row" }, ico("search"), h("input", { type: "search", class: "sk-filter", placeholder: "Filter saved searches", "aria-label": "Filter saved searches", value: filter.q, oninput: (event) => { filter.q = event.target.value; const pos = event.target.selectionStart; paint(); const box = savedHost.querySelector("input[type=search]"); if (box) { box.focus(); box.setSelectionRange(pos, pos); } } })),
          tags.length ? h("div", { class: "sk-chips" }, ...tags.map((t) => h("button", { type: "button", class: `sk-chip link${filter.tag === t ? " tone" : ""}`, "aria-pressed": String(filter.tag === t), onclick: () => { filter.tag = filter.tag === t ? "" : t; paint(); } }, `#${t}`))) : null) : null,
        shown.length ? h("div", { class: "aa-card-grid" }, ...shown.map(savedCard))
          : saved.length ? ui.empty("No saved search matches", "Clear the filter to see all of them.", btn("Clear filter", { kind: "", onClick: () => { filter.q = ""; filter.tag = ""; paint(); } }))
            : ui.empty("Nothing saved yet", "Save the searches you run often with their exact parameters. Run them in one click, clone them as templates or put them on a schedule.", btn("New saved search", { icon: "plus", kind: "", onClick: () => savedForm(null) })));
    }

    content.replaceChildren(
      summaryHost,
      h("section", { "aria-labelledby": "aaMonTitle" }, h("div", { class: "aa-sec-head" }, h("h2", { id: "aaMonTitle", text: "Monitors" }), btn("New monitor", { icon: "bell", kind: "", sm: true, onClick: () => monitorDialog() })), monitorsHost),
      h("section", { "aria-labelledby": "aaSavedTitle" }, h("div", { class: "aa-sec-head" }, h("h2", { id: "aaSavedTitle", text: "Saved searches" }), btn("New saved search", { icon: "plus", kind: "secondary", sm: true, onClick: () => savedForm(null) })), savedHost));
    paint();
  };

  // ===========================================================================
  // Account: SerpApi key, plan and usage meters, local limits
  // ===========================================================================

  const LIMIT_FIELDS = [
    { name: "airaapi_cache_ttl_minutes", label: "Cache window (minutes)", min: 0, max: 10080, hint: "Identical searches inside this window are answered from Aira's own copy and cost nothing. 0 turns the cache off." },
    { name: "airaapi_hourly_limit", label: "Live searches per hour", min: 0, max: 100000, hint: "Your own cap, checked before SerpApi is called. 0 means no cap. A cap protects the plan from runaway scripts and monitors." },
    { name: "airaapi_retention_days", label: "Keep history for (days)", min: 1, max: 3650, hint: "Older searches are removed automatically. Pinned searches are kept. SerpApi's own archive keeps 30 days." },
    { name: "airaapi_max_records", label: "Most searches to keep", min: 50, max: 100000, hint: "When the history grows past this, the oldest unpinned searches go first." },
    { name: "airaapi_result_cap_mb", label: "Largest result to store (MB)", min: 1, max: 50, hint: "A bigger response is still returned to you once, but not kept." },
  ];

  const planKey = (name) => String(name || "").toLowerCase().replace(/\s*plan\s*$/, "").replace(/[^a-z0-9]/g, "");

  function keyPanel(data, onChange) {
    // The top-bar SerpApi pill and the Jobs source list follow the key, so refresh the status the moment it changes.
    const afterChange = () => { Aira.refreshStatus(); onChange(); };
    const configured = data.configured;
    const message = h("div", { class: "aa-keymsg", role: "status", "aria-live": "polite" });
    const input = h("input", { type: "password", id: "aaKeyInput", name: "serpapi-key", autocomplete: "new-password", spellcheck: "false", autocapitalize: "off", maxlength: "128", "data-lpignore": "true", "data-1p-ignore": "true", placeholder: configured ? "Paste the new key to replace the current one" : "Paste your SerpApi private API key" });
    const submit = btn(configured ? "Validate and replace" : "Validate and connect", { icon: "shield", kind: "" });
    submit.type = "submit";
    const form = h("form", { class: "aa-keyform", novalidate: true, autocomplete: "off" },
      h("div", { class: "field" }, h("label", { for: "aaKeyInput", text: "SerpApi private API key" }), input,
        h("span", { class: "hint", text: "Aira checks the key with SerpApi before saving it. It is stored encrypted on this computer and is never shown again, logged, or sent to your browser." })),
      message, h("div", { class: "btn-row" }, submit));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const key = input.value.trim();
      input.value = "";
      message.className = "aa-keymsg";
      if (!/^[A-Za-z0-9_-]{16,128}$/.test(key)) {
        message.classList.add("bad");
        message.textContent = key ? "That does not look like a SerpApi key: it should be 16 to 128 letters, digits, dashes or underscores, with no spaces." : "Paste the key first.";
        input.focus();
        return;
      }
      await run(submit, async () => {
        message.classList.add("busy");
        message.textContent = "Checking the key with SerpApi…";
        try {
          const result = await api(`${BASE}/key`, { method: "POST", body: { api_key: key } });
          toast(`SerpApi key connected${result.account && result.account.plan_name ? `: ${result.account.plan_name}` : ""}.`, "success");
          afterChange();
        } catch (error) {
          message.className = "aa-keymsg bad";
          message.textContent = `${error.message}${configured ? " Your current key was left unchanged." : ""}`;
          input.focus();
        }
      });
    });

    const rows = [];
    if (configured) {
      const src = data.key.source;
      rows.push(h("div", { class: "aa-keystate" },
        h("span", { class: "aa-keystate-dot", "aria-hidden": "true" }),
        h("div", {}, h("strong", { text: "Connected" }), h("p", { class: "muted", text: `Key ending ${data.key.last4 || "••••"}. ${src === "env" ? "It comes from SERPAPI_API_KEY in the server environment." : "Stored encrypted in Aira for this account."}` }))));
      if (data.account_error) rows.push(h("div", { class: "sk-note warn", role: "alert" }, ico("shield"), h("span", { text: `SerpApi did not accept or answer the account check: ${data.account_error}` })));
    } else {
      rows.push(h("div", { class: "aa-keystate off" }, h("span", { class: "aa-keystate-dot", "aria-hidden": "true" }), h("div", {}, h("strong", { text: "Not connected" }), h("p", { class: "muted", text: data.key_error || "Paste a key below to unlock searches. Browsing engines, docs and locations works without one." }))));
    }
    const actions = [];
    if (configured) {
      actions.push(btn("Check again", { icon: "refresh", sm: true, onClick: (event) => run(event.currentTarget, async () => { await api(`${BASE}/account?refresh=1`); toast("Account refreshed.", "success", { timeout: 2000 }); afterChange(); }) }));
      actions.push(btn("Remove key", { icon: "trash", sm: true, kind: "danger secondary", onClick: async (event) => {
        if (data.key.source === "env") { toast("This key comes from SERPAPI_API_KEY. Remove it from .env and restart Aira.", "error", { timeout: 7000 }); return; }
        const ok = await Aira.confirm({ title: "Remove the SerpApi key?", message: "Searches stop working until a key is connected again. Stored history, saved searches and monitors stay. Monitors are skipped while there is no key.", confirmLabel: "Remove key", danger: true });
        if (!ok) return;
        await run(event.currentTarget, async () => { await api(`${BASE}/key`, { method: "DELETE" }); toast("SerpApi key removed.", "success"); afterChange(); });
      } }));
    }
    return panel("SerpApi key", { sub: "the account every search is billed to", aside: h("div", { class: "btn-row" }, ...actions), body: panelBody(...rows,
      configured ? h("details", { class: "aa-replace" }, h("summary", { text: "Replace the key" }), form) : form,
      h("p", { class: "sk-hint", text: "A key saved here takes precedence over SERPAPI_API_KEY for this account. Lost your key? Copy it from your SerpApi dashboard; Aira cannot show it back to you." }),
      h("div", { class: "btn-row" }, btn("Open SerpApi dashboard", { icon: "external", kind: "ghost", sm: true, href: SERPAPI_LINKS.dashboard, external: true }))) });
  }

  function planPanel(data, refresh) {
    const a = data.account;
    if (!a) return null;
    const total = a.searches_per_month || 0;
    const used = a.this_month_usage || 0;
    const left = a.plan_searches_left ?? 0;
    const hourly = a.hourly_rate_limit || 0;
    const row = (label, node) => h("div", { class: "aa-meter-row" }, h("div", { class: "aa-meter-top" }, h("span", { text: label }), node));
    return panel("Plan and usage", { sub: a.fetched_at ? `SerpApi account, read ${fmt.ago(a.fetched_at)}` : "SerpApi account", aside: btn("Refresh", { icon: "refresh", sm: true, onClick: (event) => run(event.currentTarget, async () => { await api(`${BASE}/account?refresh=1`); refresh(); }) }), body: panelBody(
      h("div", { class: "aa-plan-head" },
        h("div", {}, h("h3", { text: a.plan_name || "Plan" }), h("p", { class: "muted", text: [a.plan_monthly_price !== null && a.plan_monthly_price !== undefined ? `${dollars(a.plan_monthly_price)} / month` : "", a.plan_renewal_date ? `renews ${fmt.date(a.plan_renewal_date)}` : "no renewal date", a.account_email].filter(Boolean).join(" · ") })),
        h("span", { class: `badge ${/active/i.test(a.account_status || "") ? "aa-good" : "aa-bad"}`, text: a.account_status || "Unknown" })),
      h("div", { class: "aa-meters" },
        row("Searches used this month", h("strong", { text: total ? `${num(used)} of ${num(total)} (${Math.round((used / total) * 100)}%)` : num(used) })),
        total ? meter({ value: used, max: total, label: "Searches used this month", tone: leftTone(left, total) }) : null,
        row("Plan searches left", h("strong", { text: num(left) })),
        row("Extra credits", h("strong", { text: num(a.extra_credits) })),
        row("Total searches left", h("strong", { text: num(a.total_searches_left) })),
        hourly ? row("Searches this hour", h("strong", { text: `${num(a.this_hour_searches)} of ${num(hourly)}` })) : null,
        hourly ? meter({ value: a.this_hour_searches || 0, max: hourly, label: "Searches this hour", tone: (a.this_hour_searches || 0) / hourly >= 0.8 ? "warn" : "" }) : null,
        row("Searches last hour", h("strong", { text: num(a.last_hour_searches) }))),
      h("p", { class: "sk-dim", text: `Aira has counted ${num(data.usage.credits_used_30d)} credits of live searches in the last 30 days. SerpApi's own meter above is the authority for billing.` }),
      h("div", { class: "btn-row" },
        btn("Change plan or renew", { icon: "external", sm: true, href: SERPAPI_LINKS.plan, external: true }),
        btn("Buy extra credits", { icon: "external", sm: true, href: SERPAPI_LINKS.pricing, external: true }),
        btn("Billing and profile", { icon: "external", sm: true, kind: "ghost", href: SERPAPI_LINKS.dashboard, external: true })),
      h("p", { class: "sk-hint", text: "Plan changes, refunds, invoices and team seats are handled by SerpApi, so those buttons open serpapi.com in a new tab. Aira only reads the plan and usage." })) });
  }

  function plansTable(platform, account) {
    const plans = (platform && platform.plans) || [];
    if (!plans.length) return null;
    const current = planKey(account && account.plan_name);
    const rows = plans.map((p) => {
      const mine = current && planKey(p.name) === current;
      const cells = [mine ? h("span", {}, h("strong", { text: p.name }), " ", h("span", { class: "badge aa-good", text: "Your plan" })) : p.name, typeof p.price === "number" ? dollars(p.price) : `$${p.price}`, typeof p.searches_per_month === "number" ? num(p.searches_per_month) : String(p.searches_per_month), typeof p.throughput_per_hour === "number" ? num(p.throughput_per_hour) : String(p.throughput_per_hour), p.notes || ""];
      return cells;
    });
    const rules = (platform.billing_rules || []).map((rule) => h("li", { text: rule }));
    return panel("SerpApi plans", { sub: "from SerpApi's public pricing, for comparison", body: h("div", {},
      simpleTable("SerpApi plans", ["Plan", { label: "Per month", num: true }, { label: "Searches", num: true }, { label: "Searches per hour", num: true }, "Includes"], rows, { cls: "aa-plans" }),
      h("details", { class: "aa-rules" }, h("summary", { text: "How billing works" }), h("ul", { class: "inline-list" }, ...rules)),
      h("p", { class: "sk-hint", text: "Prices and limits change; check serpapi.com/pricing before deciding." })) });
  }

  function limitsPanel(settings, afterSave) {
    const inputs = {};
    const message = h("div", { class: "aa-keymsg", role: "status", "aria-live": "polite" });
    const fields = LIMIT_FIELDS.map((f) => {
      const input = h("input", { type: "number", id: `aa-${f.name}`, min: String(f.min), max: String(f.max), step: "1", inputmode: "numeric", value: String(settings[f.name] ?? "") });
      inputs[f.name] = input;
      return ui.field(f.label, input, `${f.hint} (${num(f.min)} to ${num(f.max)})`);
    });
    const save = btn("Save limits", { icon: "save", kind: "" });
    save.addEventListener("click", () => run(save, async () => {
      const body = {};
      for (const f of LIMIT_FIELDS) {
        const value = Number(inputs[f.name].value);
        if (!Number.isInteger(value) || value < f.min || value > f.max) { message.className = "aa-keymsg bad"; message.textContent = `${f.label} must be a whole number between ${num(f.min)} and ${num(f.max)}.`; inputs[f.name].setAttribute("aria-invalid", "true"); inputs[f.name].focus(); return; }
        inputs[f.name].removeAttribute("aria-invalid");
        body[f.name] = value;
      }
      try { const data = await api("/api/settings", { method: "POST", body }); Aira.state.settings = data.settings; message.className = "aa-keymsg good"; message.textContent = "Limits saved."; toast("Local limits saved.", "success", { timeout: 2200 }); afterSave(data.settings); }
      catch (error) { message.className = "aa-keymsg bad"; message.textContent = error.message; }
    }));
    return panel("Local limits and retention", { sub: "how Aira protects your plan and your disk", body: panelBody(h("div", { class: "form-grid aa-limits" }, ...fields), message, h("div", { class: "btn-row" }, save)) });
  }

  tabs.account = async (content) => {
    const paint = async () => {
      const [data, platform, settingsData] = await Promise.all([api(`${BASE}/overview`), api(`${BASE}/platform`).catch(() => null), api("/api/settings")]);
      Aira.state.settings = settingsData.settings;
      const after = () => tabs.account(content);
      content.replaceChildren(
        keyPanel(data, after),
        ...[planPanel(data, after)].filter(Boolean),
        h("div", { class: "aa-cols even" }, limitsPanel(settingsData.settings, () => {}), panel("How the key is protected", { body: panelBody(h("ul", { class: "inline-list aa-secure" },
          h("li", { text: "The key is encrypted with a key file that sits beside your database (airaapi.key). Back that file up with the database or the key cannot be read." }),
          h("li", { text: "The browser only ever learns whether a key is connected and its last four characters." }),
          h("li", { text: "Aira talks to SerpApi over HTTPS and refuses redirects, so the key cannot be forwarded elsewhere." }),
          h("li", { text: "Logs, errors, stored searches and exports have the key removed." }),
          h("li", { text: "Other programs on this computer use API keys instead (API keys tab). They never see the SerpApi key." }))) })),
        ...[plansTable(platform, data.account)].filter(Boolean));
    };
    await paint();
  };

  // ===========================================================================
  // API keys
  // ===========================================================================

  function showNewKey(created, afterClose) {
    const secret = created.secret;
    const field = h("input", { type: "text", readonly: true, class: "aa-secret sk-mono", value: secret, "aria-label": "Your new API key", spellcheck: "false", autocomplete: "off" });
    field.addEventListener("focus", () => field.select());
    const origin = `${location.origin}/v1`;
    const sample = `curl "${origin}/search.json?engine=google&q=coffee&api_key=${secret}"`;
    const stored = h("input", { type: "checkbox", id: "aaKeyStored" });
    const body = h("div", { class: "aa-newkey" },
      h("div", { class: "sk-note warn", role: "alert" }, ico("shield"), h("span", {}, h("strong", { text: "This is the only time the full key is shown. " }), "Aira keeps just a hash of it, so it cannot be recovered. Copy it into your password manager or secrets store now.")),
      h("div", { class: "field" }, h("label", { for: "aaNewKeyField", text: `Key "${created.key.name}"` }), h("div", { class: "aa-secret-row" }, Object.assign(field, { id: "aaNewKeyField" }), serp.copyButton(secret, { label: "Copy key", message: "Key copied", sm: false, text: true }))),
      h("div", { class: "field" }, h("span", { class: "label", text: "Try it" }), serp.codeBlock("curl", sample, { label: "cURL" })),
      h("label", { class: "check" }, stored, "I have stored this key somewhere safe"));
    const modal = Aira.modal({ title: "Your new API key", wide: true, body, onClose: () => { field.value = ""; afterClose(); }, actions: [{ label: "Done", kind: "", onClick: async () => {
      if (!stored.checked) { const sure = await Aira.confirm({ title: "Close without saving the key?", message: "You will not be able to see it again. You can always revoke it and create a new one.", confirmLabel: "Close anyway", danger: true }); if (!sure) return false; }
      return undefined;
    } }] });
    setTimeout(() => { field.focus(); }, 30);
    return modal;
  }

  tabs.keys = async (content) => {
    const [keysData, overview] = await Promise.all([api(`${BASE}/keys`), api(`${BASE}/overview`, { silent: true }).catch(() => null)]);
    const keys = keysData.keys;
    const active = keys.filter((k) => !k.revoked_at);
    const apiCalls = overview ? (overview.usage.by_source.find((s) => s.source === "api") || { searches: 0 }).searches : null;
    const reload = () => tabs.keys(content);

    const name = h("input", { type: "text", id: "aaKeyName", maxlength: "80", placeholder: "Laptop scripts, n8n workflow, CI", autocomplete: "off", required: true });
    const limit = h("input", { type: "number", id: "aaKeyLimit", min: "0", max: "1000000", step: "1", value: "0", inputmode: "numeric" });
    const create = btn("Create key", { icon: "plus", kind: "" });
    create.type = "submit";
    const form = h("form", { class: "aa-keycreate", novalidate: true },
      ui.field("Name", name, "Something you will recognise in the list."),
      ui.field("Hourly limit", limit, "Live and cached calls per hour. 0 means unlimited."),
      create);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!name.value.trim()) { toast("Name the key first.", "error"); name.focus(); return; }
      run(create, async () => {
        const created = await api(`${BASE}/keys`, { method: "POST", body: { name: name.value.trim(), hourly_limit: Number(limit.value) || 0 } });
        showNewKey(created, reload);
      });
    });

    const rows = keys.map((k) => {
      const revoked = !!k.revoked_at;
      const revoke = btn("Revoke", { icon: "trash", sm: true, kind: "danger secondary", aria: `Revoke ${k.name}`, onClick: async (event) => {
        const ok = await Aira.confirm({ title: `Revoke "${k.name}"?`, message: "Anything still using this key gets 401 immediately. This cannot be undone; create a new key if you need one again.", confirmLabel: "Revoke key", danger: true });
        if (!ok) return;
        await run(event.currentTarget, async () => { await api(`${BASE}/keys/${k.id}`, { method: "DELETE" }); toast("Key revoked.", "success"); reload(); });
      } });
      return h("tr", { class: revoked ? "revoked" : "" },
        h("td", { class: "aa-main" }, h("strong", { text: k.name }), h("div", { class: "aa-sub" }, h("code", { text: `${k.prefix}${"•".repeat(12)}` })),
          h("div", { class: "aa-mobile-meta" }, h("span", { class: `badge ${revoked ? "aa-bad" : "aa-good"}`, text: revoked ? "Revoked" : "Active" }), h("span", { class: "sk-dim" }, "Used ", timeNode(k.last_used_at)), revoked ? null : h("span", { class: "sk-dim", text: k.hourly_limit ? `${num(k.used_this_hour)} of ${num(k.hourly_limit)} this hour` : `${num(k.used_this_hour)} this hour, no limit` }))),
        h("td", { class: "hide-sm" }, h("span", { class: `badge ${revoked ? "aa-bad" : "aa-good"}`, text: revoked ? "Revoked" : "Active" })),
        h("td", { class: "hide-md" }, timeNode(k.created_at)),
        h("td", { class: "hide-sm" }, timeNode(k.last_used_at)),
        h("td", { class: "aa-usage-cell hide-sm" }, revoked ? h("span", { class: "sk-dim", text: "–" }) : k.hourly_limit
          ? h("div", {}, h("div", { class: "aa-tp-top" }, h("span", { text: `${num(k.used_this_hour)} of ${num(k.hourly_limit)} this hour` })), meter({ value: k.used_this_hour, max: k.hourly_limit, label: `Calls this hour for ${k.name}`, tone: k.used_this_hour / k.hourly_limit >= 0.8 ? "warn" : "" }))
          : h("span", { text: `${num(k.used_this_hour)} this hour · no limit` })),
        h("td", { class: "aa-acts" }, revoked ? null : revoke));
    });
    const table = keys.length
      ? h("div", { class: "table-wrap aa-table-wrap" }, h("table", { class: "table aa-table aa-keys-table" }, h("caption", { class: "visually-hidden", text: "API keys" }),
        h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Key" }), h("th", { scope: "col", class: "hide-sm", text: "Status" }), h("th", { scope: "col", class: "hide-md", text: "Created" }), h("th", { scope: "col", class: "hide-sm", text: "Last used" }), h("th", { scope: "col", class: "hide-sm", text: "Usage" }), h("th", { scope: "col" }, h("span", { class: "visually-hidden", text: "Actions" })))),
        h("tbody", {}, ...rows)))
      : ui.empty("No API keys yet", "Create one to call Aira from scripts, spreadsheets or automations with the same requests you would send to SerpApi.");

    fill(content,
      h("div", { class: "stats aa-stats" },
        statTile({ label: "Active keys", value: num(active.length), sub: `${num(keys.length - active.length)} revoked` }),
        statTile({ label: "/v1 calls, 30 days", value: apiCalls === null ? "–" : num(apiCalls), sub: "from scripts and apps", href: apiCalls ? link("history", { source: "api" }) : null }),
        statTile({ label: "Base URL", value: "/v1", sub: location.origin })),
      overview && !overview.configured ? h("div", { class: "sk-note warn", role: "status" }, ico("key"), h("span", {}, "No SerpApi key is connected yet, so /v1 searches answer 409 until you ", h("a", { href: link("account"), text: "connect one" }), ". You can still create keys now.")) : null,
      h("div", { class: "aa-cols" },
        panel("Create an API key", { sub: "for the SerpApi-compatible /v1 endpoint", body: panelBody(form) }),
        panel("How to call Aira", { body: panelBody(
          h("dl", { class: "kv aa-facts" }, h("dt", { text: "Base URL" }), h("dd", {}, monoCopy(`${location.origin}/v1`, "Copy base URL")), h("dt", { text: "Auth" }), h("dd", { text: "api_key=aira_… or Authorization: Bearer aira_…" }), h("dt", { text: "Scope" }), h("dd", { text: "This account only. Keys never expose the SerpApi key." })),
          h("p", { class: "sk-dim" }, "Moving from SerpApi? Change the base URL and the key, nothing else. ", h("a", { href: link("docs", { page: "migrate" }), text: "Migration guide" }))) })),
      panel("Your keys", { sub: "secrets are stored as hashes and shown once", body: table }),
      panel("Code samples", { sub: "against this Aira server", body: panelBody(serp.snippetPanel("google", { q: "coffee" })) }));
  };

  // ===========================================================================
  // Docs
  // ===========================================================================

  const DOC_PAGES = [
    ["start", "Getting started"],
    ["auth", "Authentication"],
    ["endpoints", "Endpoints"],
    ["parameters", "Common parameters"],
    ["output", "Responses and metadata"],
    ["errors", "Errors"],
    ["async", "Async searches"],
    ["billing", "Billing and caching"],
    ["sdks", "SDKs and libraries"],
    ["migrate", "Migrate from SerpApi"],
    ["coverage", "SerpApi dashboard coverage"],
  ];
  const REFERENCE_PAGES = [["ref-gl", "Countries (gl)", "gl"], ["ref-hl", "Languages (hl)", "hl"], ["ref-domain", "Google domains", "google_domain"]];

  /** Where every SerpApi dashboard page lives in Aira, or why it does not apply to a local app. */
  const COVERAGE = {
    dashboard: { status: "built", where: "Overview", href: link("overview"), how: "Plan, searches left as a ring gauge, hourly throughput, 30-day chart, top engines, error and cache rates, recent searches, plus a cost estimator and monitor health." },
    playground: { status: "built", where: "Playground", href: link("playground"), how: "Engine picker, generated parameter form, options, code in 8 languages, result tabs, diff against the previous run." },
    "my-searches": { status: "built", where: "History", href: link("history"), how: "Filters, text search, sorting, multi-select, compare two runs, export, pin, re-run and bulk delete. Kept as long as you set, not just 30 days." },
    "search-archive": { status: "built", where: "History, Find by search id", href: link("history"), how: "Open any stored search by its Aira or SerpApi id, as JSON or as the archived HTML page; the same ids work on /searches/{id}.json and .html. A deleted or expired id answers 410 for 31 days, like SerpApi. json_with_pixel_position is read from SerpApi's archive by X-Ray, and Export offers the result as Markdown." },
    "api-key": { status: "built", where: "Account and API keys", href: link("account"), how: "Connect, replace or remove the SerpApi key (never shown back), and create or revoke Aira keys. Regenerating the SerpApi key itself happens on serpapi.com." },
    plan: { status: "partial", where: "Account", href: link("account"), how: "Plan, renewal date, usage meters and a plan comparison. Changing, cancelling or renewing a plan, Ludicrous Speed and refunds are billing actions at SerpApi, so buttons open serpapi.com." },
    "extra-credits": { status: "partial", where: "Account", href: link("account"), how: "The extra-credit balance is shown. Buying credits happens on serpapi.com." },
    "account-settings": { status: "partial", where: "Account", href: link("account"), how: "Email and account status are shown. Profile, password and 2FA are managed at SerpApi." },
    team: { status: "na", where: "Not applicable", how: "SerpApi teams share one plan across people. Aira is a local app: each Aira account has its own database and SerpApi key, and API keys separate your own scripts from each other." },
    usage: { status: "built", where: "Overview and Account", href: link("overview"), how: "Daily stacked chart (live, cached, errors), SerpApi's monthly and hourly meters, per-engine and per-source breakdowns." },
    status: { status: "built", where: "Status", href: link("status"), how: "Computed from your own run logs: success rate and latency per engine, SerpApi reachability, hourly cap and async queue. Incident subscriptions stay on SerpApi's status page." },
    webhooks: { status: "na", where: "In-app notifications", href: link("monitors"), how: "SerpApi has no webhooks and neither does Aira: async searches are polled and finish as a toast, and monitors raise in-app notifications. An outgoing webhook was left out on purpose, because a local app that posts to arbitrary URLs is a data-leak risk." },
    "locations-tool": { status: "built", where: "Locations", href: link("locations"), how: "Search, copy the canonical name or id, open the point on a map, and use it in the Playground." },
    docs: { status: "built", where: "This Docs page", href: link("docs"), how: "Reference for /v1, one page per engine generated from the catalog, country, language and domain lists, copy as Markdown." },
    changelog: { status: "na", where: "SerpApi release notes", external: SERPAPI_LINKS.releases, how: "Release notes are a web page at SerpApi and Aira does not mirror them. The catalog marks deprecated engines." },
    sdks: { status: "built", where: "Docs, SDKs and libraries", href: link("docs", { page: "sdks" }), how: "Official client libraries are listed. Aira's own snippets use plain HTTP in 8 languages." },
    xray: { status: "built", where: "History detail, X-Ray", href: link("history"), how: "For Google Search and Google Ads, X-Ray reads SerpApi's pixel positions from the archive (no search used) and draws every measured element on a map of the page, optionally over the stored HTML. Pick a box or a list entry to see its JSON path, fields, size and json_restrictor path. The JSON tree also copies the path of any value for every engine." },
  };
  const IDEAS = [
    ["Saved queries and templates", "built", "Monitors & saved: name, tag, pin, run in one click, open in the Playground to clone."],
    ["Run diff", "built", "History: select two runs, or Compare from a detail. Added, removed, moved and changed results."],
    ["Cost estimator", "built", "Overview estimator, a confirmation before multi-search actions and monitor schedule costs."],
    ["Scheduler", "built", "Monitors with intervals from 15 minutes, notifications on change, a monthly cost estimate, a per-monitor monthly budget (runs wait for the next month once it is used) and pause on error."],
    ["Export", "built", "JSON, CSV, Excel (XLSX), Markdown table, HTML and cURL per search, CSV of the history list and a usage CSV (days, engines, sources) from Overview."],
    ["Side-by-side engine compare", "built", "Engines: Compare engines runs one query on up to four engines and shows overlap."],
    ["Local history with search", "built", "Search covers queries, labels, engines and errors, and with Inside results also the titles, snippets and links of every stored result, with the match highlighted. Kept for as long as you set."],
    ["Usage dashboard", "built", "Overview and Account, with an early warning at 80% of the hourly limit and 20% of the plan."],
    ["Async job board", "built", "History shows running async searches, polls with backoff and announces when each finishes."],
    ["Result-to-action pipeline", "built", "History detail of a Google Jobs search has Save jobs to Aira (imported, scored, duplicates merged); Careers does the same per job. The json_restrictor builder is the path picker. Drafting outreach is not part of it."],
  ];
  const STATUS_LABEL = { built: "Built", partial: "Partly", no: "Not built", na: "Not applicable" };
  /** Common parameters of the Search API and how Aira treats each one (checked against the running app). */
  const PARAMETERS = [
    ["api_key", "built", "/v1 and the SerpApi-compatible paths take an Aira key (aira_...); the SerpApi key stays encrypted on this computer."],
    ["engine", "built", "All 136 SerpApi catalog engines, each with its own generated parameter form, plus Aira's own aira_jobs engine (free public job boards, no key, no credits)."],
    ["output=json", "built", "Default everywhere."],
    ["output=html", "built", "Playground Raw HTML option, /search.html and /v1; the page is stored and shown in the HTML tab."],
    ["output=md", "built", "Playground Markdown option, /search.json?output=md and /v1 (text/markdown); the stored text is shown formatted in the page tab with a source toggle."],
    ["json_restrictor", "built", "Playground option, /v1, and a visual builder in History detail."],
    ["no_cache", "built", "Playground (Skip the cache), Run fresh, /v1. Refused together with async, as SerpApi does."],
    ["async", "built", "Playground option and /v1; History polls the archive with backoff and announces completion."],
    ["zero_trace", "partial", "Forwarded to SerpApi (enterprise plans). Aira still keeps its own local history record of the run; delete it from History if you do not want it stored."],
    ["device, location, uule, lat/lon, radius", "built", "Per-engine forms from the catalog. location together with uule is refused, like SerpApi."],
    ["gl, hl, google_domain, lr, cr, safe, nfpr, filter, start, tbm, tbs", "built", "Offered on the engines that document them, with the country, language and domain lists in Docs."],
    ["Unknown parameters", "built", "Passed through like SerpApi (its SDKs add source=); allow_unknown=false makes them an error."],
    ["output=json_with_pixel_position", "partial", "X-Ray reads it from SerpApi's archive for stored Google Search and Google Ads searches (Pixel Position is in beta at SerpApi). The Playground and /v1 do not request it on a live search."],
  ];
  /** The five SerpApi APIs and the async flow. */
  const APIS = [
    ["Search API (/search, /search.json, /search.html)", "built", "Answered on SerpApi's own paths and under /v1; cached, counted per key, 1 credit per live search (extract in crawl mode: one per page)."],
    ["Account API (/account.json)", "built", "Free, cached for 60 seconds; the Account page and the API show plan, usage and throughput."],
    ["Locations API (/locations.json)", "built", "No key needed, limit 1 to 10, cached for 24 hours; Locations page with copy and map link."],
    ["Search Archive API (/searches/{id}.json and .html)", "built", "By Aira id or SerpApi id, JSON or HTML. X-Ray reads the .json_with_pixel_position copy and keeps it with the search."],
    ["Async searches and archive polling", "built", "async=true returns Queued at once; the archive answers Queued, then Success. History shows running searches."],
    ["Webhooks", "na", "SerpApi has none and neither does Aira; completion is polling plus in-app notifications."],
    ["Bulk search / scheduled searches", "partial", "SerpApi's bulk API is only on its roadmap. Monitors and Careers alerts are the scheduled equivalent, from every hour, with no budget caps."],
  ];
  /** Every documented error and what Aira does with it. */
  const ERRORS = [
    ["200 with no results", "built", "Shown as a note, never an error, and not billed."],
    ["400 Missing or bad parameter", "built", "Validated before SerpApi is called (required, enums, combinations), so it costs nothing."],
    ["401 Invalid API key", "partial", "A bad Aira key answers 401 invalid_aira_key. A rejected SerpApi key answers 422 invalid_api_key, so the browser does not mistake it for a signed-out session."],
    ["403 Forbidden", "na", "Not reachable locally; a deleted SerpApi account shows as a rejected key."],
    ["404 Not Found", "built", "Unknown engine, search id or route."],
    ["410 Gone (expired archive)", "partial", "Expired records are deleted at the retention limit, so the answer is 404 Search not found."],
    ["429 Hourly limit or out of searches", "partial", "Local cap and per-key limit answer 429 local_rate_limit; SerpApi's own 429 keeps its message as rate_limited, and no searches left is answered 402 out_of_searches."],
    ["500 and 503", "partial", "Retried once, then answered 502 upstream_error with the record id."],
    ["200 with status Error", "partial", "Not billed. Aira answers 502 search_failed (not 200) and records the attempt in History."],
  ];

  function paramRows(entry, filterText) {
    const needle = (filterText || "").trim().toLowerCase();
    return serp.logic.orderedGroups(entry)
      .map((group) => [group, entry.params.filter((p) => (p.group || "Search") === group && (!needle || `${p.name} ${p.label} ${p.help}`.toLowerCase().includes(needle)))])
      .filter(([, params]) => params.length);
  }

  function optionsNode(param) {
    const options = param.options || [];
    if (!options.length) return null;
    const list = h("ul", { class: "aa-options" }, ...options.map((o) => h("li", {}, h("code", { text: o.value }), o.label && o.label !== o.value ? h("span", { text: o.label }) : null)));
    return h("details", { class: "aa-opt-details" }, h("summary", { text: `${num(options.length)} value${options.length === 1 ? "" : "s"}${param.options_truncated ? " (partial list; other values may work)" : ""}` }), list);
  }

  function paramRow(p) {
    const flags = [
      p.required ? h("span", { class: "sk-chip warn", text: "required" }) : null,
      p.requires_one_of ? h("span", { class: "sk-chip tone", title: "One of the parameters sharing this label is required", text: `one of: ${p.requires_one_of}` }) : null,
    ];
    const hasDefault = p.default !== null && p.default !== undefined && p.default !== "";
    return h("tr", { id: `param-${p.name}` },
      h("td", {}, h("code", { text: p.name }), ...flags),
      h("td", {}, h("span", { class: "aa-type", text: p.type })),
      h("td", {},
        h("strong", { text: p.label }),
        p.help ? h("p", { class: "sk-dim", text: p.help }) : null,
        hasDefault ? h("p", { class: "sk-dim" }, "Default: ", h("code", { text: String(p.default) })) : null,
        optionsNode(p)));
  }

  function paramTable(entry, filterText) {
    const groups = paramRows(entry, filterText);
    if (!groups.length) return h("p", { class: "sk-dim", text: filterText ? "No parameter matches." : "This engine has no documented parameters." });
    const sections = groups.map(([group, params]) => {
      const table = h("table", { class: "table aa-params" },
        h("caption", { class: "visually-hidden", text: `${group} parameters of ${entry.name}` }),
        h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Parameter" }), h("th", { scope: "col", text: "Type" }), h("th", { scope: "col", text: "Details" }))),
        h("tbody", {}, ...params.map(paramRow)));
      return h("section", { class: "aa-param-group", "aria-label": `${group} parameters` },
        h("h4", {}, group, h("span", { class: "sk-count", text: String(params.length) })),
        h("div", { class: "table-wrap" }, table));
    });
    return h("div", {}, ...sections);
  }

  function engineMarkdown(entry) {
    const lines = [`# ${entry.name} (\`${entry.id}\`)`, "", entry.description, ""];
    if (entry.aira_use) lines.push(`**How Aira uses it:** ${entry.aira_use}`, "");
    lines.push(`Endpoint: \`${entry.endpoint}?engine=${entry.id}\``, "", "## Parameters", "", "| Parameter | Type | Required | Description |", "| --- | --- | --- | --- |");
    for (const p of entry.params) lines.push(`| \`${p.name}\` | ${p.type} | ${p.required ? "yes" : p.requires_one_of ? `one of ${p.requires_one_of}` : "no"} | ${(p.help || p.label || "").replaceAll("|", "\\|").replaceAll("\n", " ")} |`);
    if (entry.result_keys.length) lines.push("", "## Result keys", "", entry.result_keys.map((k) => `\`${k}\``).join(", "));
    if (entry.pagination && entry.pagination.kind !== "none") lines.push("", "## Pagination", "", `${paginationText(entry.pagination)}. ${entry.pagination.notes || ""}`.trim());
    return `${lines.join("\n")}\n`;
  }

  async function enginePage(id) {
    const entry = await loadEngine(id);
    const summary = engineById(id) || entry;
    const filter = h("input", { type: "search", placeholder: `Filter ${entry.params.length} parameters`, "aria-label": "Filter parameters" });
    const tableHost = h("div", {}, paramTable(entry, ""));
    filter.addEventListener("input", debounce(() => tableHost.replaceChildren(paramTable(entry, filter.value)), 100));
    const itemFields = Object.entries(entry.result_item_fields || {});
    const snippets = serp.snippetPanel(entry, (entry.examples && entry.examples.params) || {});
    const anyOf = (entry.requires_any_of || []).filter((g) => g.length > 1);
    return h("article", { class: "aa-doc", "aria-labelledby": "aaDocTitle" },
      h("header", { class: "aa-doc-head" },
        h("div", {}, h("h2", { id: "aaDocTitle", text: entry.name }), h("div", { class: "sk-chips" }, h("code", { text: entry.id }), serp.chip(categoryLabel(entry.category)), serp.chip(entry.family), entry.verified ? serp.chip(`documented: ${entry.verified}`) : null,
          entry.deprecated ? serp.chip("Deprecated", "warn") : null, entry.unlisted ? serp.chip("Unlisted") : null)),
        h("div", { class: "btn-row" },
          btn("Open in Playground", { icon: "play", kind: "", href: playgroundLink(id) }),
          serp.copyButton(() => engineMarkdown(entry), { label: "Copy as Markdown", message: "Markdown copied", sm: false, text: true }),
          btn("", { icon: "download", aria: "Download this page as Markdown", title: "Download as Markdown", onClick: () => serp.downloadText(`${id}.md`, engineMarkdown(entry), "text/markdown") }),
          entry.docs_url ? btn("serpapi.com", { icon: "external", kind: "ghost", href: entry.docs_url, external: true, title: "SerpApi's own page for this engine" }) : null)),
      entry.deprecated ? h("div", { class: "sk-note warn", role: "note" }, ico("shield"), h("span", { text: entry.deprecated_reason || "SerpApi has deprecated this engine. It may stop working." })) : null,
      h("p", { class: "aa-lede", text: entry.description }),
      entry.aira_use ? h("div", { class: "sk-note" }, ico("check"), h("span", {}, h("strong", { text: "In Aira: " }), entry.aira_use)) : null,
      h("section", {}, h("h3", { text: "Request" }),
        h("p", {}, "Send a GET to ", h("code", { text: `${location.origin}/v1/search.json` }), " with ", h("code", { text: `engine=${entry.id}` }), " and an Aira key. Straight to SerpApi the endpoint is ", h("code", { text: `${entry.endpoint}` }), "."),
        h("ul", { class: "inline-list" },
          entry.required.length ? h("li", {}, "Required: ", ...entry.required.flatMap((n, i) => [i ? ", " : "", h("code", { text: n })])) : h("li", { text: "No parameter is strictly required." }),
          ...anyOf.map((g) => h("li", {}, "At least one of: ", ...g.flatMap((n, i) => [i ? ", " : "", h("code", { text: n })]))),
          entry.pagination && entry.pagination.kind !== "none" ? h("li", { text: `${paginationText(entry.pagination)}${entry.pagination.notes ? `. ${entry.pagination.notes}` : ""}` }) : (entry.pagination && entry.pagination.notes ? h("li", { text: entry.pagination.notes }) : null))),
      h("section", {}, h("div", { class: "aa-doc-sechead" }, h("h3", { text: "Parameters" }), h("div", { class: "sk-filter-row" }, ico("search"), Object.assign(filter, { className: "sk-filter" }))), tableHost),
      h("section", {}, h("h3", { text: "Response" }),
        entry.result_keys.length ? h("div", { class: "sk-chips" }, ...entry.result_keys.map((k) => serp.chip(k))) : h("p", { class: "sk-dim", text: summary.id === "google_sports" ? "SerpApi documents no result keys for this engine." : "No result keys are documented." }),
        itemFields.length ? h("div", { class: "aa-fields" }, ...itemFields.map(([key, fields]) => h("details", {}, h("summary", {}, h("code", { text: key }), ` · ${fields.length} fields per item`), h("div", { class: "sk-chips" }, ...fields.map((f) => serp.chip(f)))))) : null),
      h("section", {}, h("h3", { text: "Code" }), snippets));
  }

  function referencePage(listEntry, paramName, title) {
    const param = listEntry && listEntry.params.find((p) => p.name === paramName);
    const options = param ? param.options || [] : [];
    const filter = h("input", { type: "search", placeholder: `Filter ${num(options.length)} values`, "aria-label": `Filter ${title}` });
    const host = h("div");
    const paint = () => {
      const needle = filter.value.trim().toLowerCase();
      const shown = options.filter((o) => !needle || `${o.value} ${o.label}`.toLowerCase().includes(needle));
      host.replaceChildren(shown.length ? simpleTable(`${title} accepted by the ${paramName} parameter`, ["Value", "Name"], shown.map((o) => [h("code", { text: o.value }), o.label])) : h("p", { class: "sk-dim", text: "Nothing matches." }));
    };
    filter.addEventListener("input", debounce(paint, 100));
    paint();
    return h("article", { class: "aa-doc" }, h("h2", { text: title }), h("p", { class: "aa-lede" }, `Values accepted by the `, h("code", { text: paramName }), ` parameter on Google engines (${num(options.length)} listed in the catalog).`),
      options.length ? h("div", {}, h("div", { class: "sk-filter-row" }, ico("search"), Object.assign(filter, { className: "sk-filter" })), host) : ui.empty("Not in the catalog", "The Google engine entry has no list for this parameter."));
  }

  function guidePage(id, docs, platform) {
    const p = platform || {};
    const section = (title, ...body) => h("section", {}, h("h3", { text: title }), ...body);
    const wrap = (title, lede, ...parts) => h("article", { class: "aa-doc" }, h("h2", { text: title }), lede ? h("p", { class: "aa-lede", text: lede }) : null, ...parts);
    switch (id) {
      case "start":
        return wrap("Getting started", "Aira exposes a SerpApi-compatible API on this computer. Your scripts call Aira; Aira calls SerpApi with your key, caches the answer and keeps the history.",
          h("ol", { class: "aa-steps" },
            h("li", { class: "aa-step" }, h("span", { class: "aa-step-n", text: "1" }), h("div", {}, h("strong", { text: "Connect your SerpApi key" }), h("p", {}, "In ", h("a", { href: link("account"), text: "Account" }), ". Aira stores it encrypted and never shows it again."))),
            h("li", { class: "aa-step" }, h("span", { class: "aa-step-n", text: "2" }), h("div", {}, h("strong", { text: "Create an API key" }), h("p", {}, "On the ", h("a", { href: link("keys"), text: "API keys" }), " tab. It looks like aira_ followed by 40 characters and is shown once."))),
            h("li", { class: "aa-step" }, h("span", { class: "aa-step-n", text: "3" }), h("div", {}, h("strong", { text: "Call it like SerpApi" }), h("p", {}, "Use ", h("code", { text: docs.base_url }), " in place of https://serpapi.com. Parameters, responses and errors match.")))),
          section("Example", serp.snippetPanel("google", { q: "coffee" })),
          section("Why go through Aira?", h("ul", { class: "inline-list" }, h("li", { text: "Identical searches inside the cache window cost nothing and return instantly." }), h("li", { text: "Every call is stored with its parameters, cost and result, so you can search, compare and export them." }), h("li", { text: "Per-key hourly limits and a global cap protect your plan from runaway scripts." }), h("li", { text: "Saved searches and monitors turn any query into an alert." }))));
      case "auth":
        return wrap("Authentication", docs.auth.summary,
          section("Key format", h("p", { text: docs.auth.key_format })), section("Scope", h("p", { text: docs.auth.scope })),
          section("Examples", h("p", {}, "Query parameter: ", h("code", { text: "GET /v1/search.json?engine=google&q=coffee&api_key=aira_…" })), h("p", {}, "Header: ", h("code", { text: "Authorization: Bearer aira_…" })), h("p", { class: "sk-dim", text: "A bad, unknown or revoked key answers 401 with code invalid_aira_key. The server listens on 127.0.0.1 only and sends no CORS headers." })));
      case "endpoints":
        return wrap("Endpoints", `Base URL ${docs.base_url}. All endpoints are GET and need an Aira key.`,
          simpleTable("Aira /v1 endpoints", ["Endpoint", "What it does", "Returns"], docs.endpoints.map((e) => [h("span", {}, h("code", { text: e.path }), e.alias ? h("div", { class: "sk-dim" }, "alias ", h("code", { text: e.alias })) : null), e.description, e.returns])));
      case "parameters":
        return wrap("Common parameters", "Accepted by /v1/search.json on every engine. Engine-specific parameters are on each engine page.",
          simpleTable("Common parameters", ["Parameter", "Type", "Description"], [...docs.parameters, ...(docs.aira_parameters || [])].map((x) => [h("span", {}, h("code", { text: x.name }), x.required ? h("span", { class: "sk-chip warn", text: "required" }) : null), x.type || "", h("div", {}, x.description, x.values ? h("div", { class: "sk-chips" }, ...x.values.map((v) => serp.chip(v))) : null)])));
      case "output": {
        const md = (p.metadata_fields && p.metadata_fields.search_metadata) || [];
        return wrap("Responses and metadata", "JSON by default. output=html returns the raw page and output=md a Markdown version for language models.",
          section("search_metadata", p.metadata_fields && p.metadata_fields.note ? h("p", { class: "sk-dim", text: p.metadata_fields.note }) : null, md.length ? simpleTable("search_metadata fields", ["Field", "Type", "Meaning"], md.map((f) => [h("code", { text: f.field }), f.type || "", f.description])) : null,
            h("p", {}, "Aira adds ", h("code", { text: "search_metadata.aira = {id, cache_hit}" }), " to every /v1 response so you can find the stored copy.")),
          section("Trimming responses", h("p", {}, "Pass ", h("code", { text: "json_restrictor=organic_results[].title,organic_results[].link" }), " to keep only some fields. The History detail has a builder that writes the value for you.")),
          p.metadata_fields && p.metadata_fields.top_level_error_key ? section("Errors inside a 200", h("p", { text: p.metadata_fields.top_level_error_key })) : null);
      }
      case "errors": {
        const rows = (list) => list.map((e) => [String(e.http_status), e.code ? h("code", { text: e.code }) : "", e.message, e.meaning, e.advice]);
        return wrap("Errors", "Aira answers in SerpApi's shape: {\"error\": \"…\"} with a matching HTTP status, plus a code where Aira adds one.",
          section("Aira errors", simpleTable("Aira errors", ["Status", "Code", "Message", "Meaning", "What to do"], rows(docs.errors))),
          docs.upstream_errors.length ? section("SerpApi errors passed through", simpleTable("SerpApi errors", ["Status", "Code", "Message", "Meaning", "What to do"], rows(docs.upstream_errors))) : null);
      }
      case "async": {
        const a = p.async_and_webhooks || {};
        return wrap("Async searches", "Send async=true to get a queued search back immediately and collect it later. Aira polls SerpApi's archive for you.",
          a.flow ? section("Flow", h("ol", { class: "inline-list" }, ...a.flow.map((s) => h("li", { text: s })))) : null,
          a.rules ? section("Rules", h("ul", { class: "inline-list" }, ...a.rules.map((s) => h("li", { text: s })))) : null,
          a.polling_advice ? section("Polling", h("p", { text: a.polling_advice })) : null,
          a.webhooks ? section("Webhooks", h("p", { text: a.webhooks }), h("p", { class: "sk-dim", text: "Aira does not send webhooks either. History lists running async searches and tells you when each one finishes." })) : null);
      }
      case "billing":
        return wrap("Billing and caching", "What counts against your SerpApi plan, and what does not.",
          section("SerpApi billing rules", h("ul", { class: "inline-list" }, ...(p.billing_rules || []).map((r) => h("li", { text: r })))),
          section("What Aira adds", h("ul", { class: "inline-list" }, h("li", { text: "A local cache: an identical search inside the cache window (60 minutes by default) is answered from Aira's copy, shown as cached and costs nothing. no_cache=true skips it." }), h("li", { text: "Engine rules: extract in crawl mode costs one credit per page allowed; every other engine costs one." }), h("li", { text: "A local hourly cap and a per-key hourly limit, so a loop cannot burn the plan." }))),
          h("p", {}, "See ", h("a", { href: link("account"), text: "Account" }), " for the plan table and your limits."));
      case "sdks":
        return wrap("SDKs and libraries", "SerpApi publishes client libraries for many languages. Aira's snippets use plain HTTP, which works everywhere.",
          h("div", { class: "aa-sdk-grid" }, ...(p.sdks || []).map((s) => h("article", { class: "aa-sdk" }, h("h4", { text: s.language }), h("p", { class: "sk-dim", text: s.package }), s.repo ? externalLink(s.repo, "Repository") : null))),
          h("p", { class: "sk-hint", text: "Each library targets serpapi.com. Aira answers on SerpApi's own paths, so the official libraries work once their base URL points at Aira and they use an API key. Examples for the Python libraries:" }),
          ...Object.entries(docs.sdk || {}).map(([name, code]) => h("div", { class: "field" }, h("span", { class: "label", text: name }), serp.codeBlock("python", code, { label: name }))),
          h("p", {}, externalLink(SERPAPI_LINKS.integrations, "All integrations on serpapi.com")));
      case "migrate":
        return wrap("Migrate from SerpApi", "Moving an existing script to Aira takes two edits.",
          h("ol", { class: "aa-steps" }, ...docs.migrate.map((s, i) => h("li", { class: "aa-step" }, h("span", { class: "aa-step-n", text: String(i + 1) }), h("div", {}, h("p", { text: s }))))),
          section("Before and after", simpleTable("Before and after", ["", "SerpApi", "Aira"], [["Base URL", h("code", { text: "https://serpapi.com/search.json" }), h("code", { text: `${docs.root_url || docs.base_url}/search.json` })], ["Key", "SerpApi private key", "API key (aira_…)"], ["Everything else", "same", "same"]])));
      case "coverage": {
        const pages = p.dashboard_pages || [];
        const rows = pages.map((page) => {
          const c = COVERAGE[page.id] || { status: "na", where: "Not mapped", how: "" };
          const target = c.href ? h("a", { href: c.href, text: c.where }) : c.external ? externalLink(c.external, c.where) : c.where;
          return [h("div", {}, h("strong", { text: page.title }), h("p", { class: "sk-dim", text: (page.purpose || "").split(". ")[0] })), h("div", {}, target, h("p", { class: "sk-dim", text: c.how })), h("span", { class: `badge aa-cov ${c.status}`, text: STATUS_LABEL[c.status] })];
        });
        return wrap("SerpApi dashboard coverage", "Every page and feature of the SerpApi dashboard, and where it lives in Aira. Billing and account administration stay with SerpApi.",
          pages.length ? simpleTable("Dashboard coverage", ["SerpApi page", "In Aira", "Status"], rows, { cls: "aa-coverage" }) : ui.empty("Reference not loaded", "The platform reference could not be read."),
          section("Common parameters", simpleTable("Common parameters", ["Parameter", "Status", "In Aira"], PARAMETERS.map(([name, status, where]) => [h("code", { text: name }), h("span", { class: `badge aa-cov ${status}`, text: STATUS_LABEL[status] }), where]), { cls: "aa-coverage" })),
          section("APIs", simpleTable("APIs", ["API", "Status", "In Aira"], APIS.map(([name, status, where]) => [name, h("span", { class: `badge aa-cov ${status}`, text: STATUS_LABEL[status] }), where]), { cls: "aa-coverage" })),
          section("Documented errors", simpleTable("Errors", ["Error", "Status", "In Aira"], ERRORS.map(([name, status, where]) => [name, h("span", { class: `badge aa-cov ${status}`, text: STATUS_LABEL[status] }), where]), { cls: "aa-coverage" })),
          section("Improvements beyond the dashboard", simpleTable("Improvements", ["Idea", "Status", "Where"], IDEAS.map(([name, status, where]) => [name, h("span", { class: `badge aa-cov ${status}`, text: STATUS_LABEL[status] }), where]), { cls: "aa-coverage" })));
      }
      default:
        return ui.empty("Page not found", "Choose a page from the list.");
    }
  }

  tabs.docs = async (content, ctx) => {
    const [docs, platform, list] = await Promise.all([api(`${BASE}/docs`), api(`${BASE}/platform`).catch(() => null), loadEngines()]);
    const googleEntry = list.engines.some((e) => e.id === "google") ? await loadEngine("google").catch(() => null) : null;
    let current = ctx.query.engine ? { engine: ctx.query.engine } : { page: DOC_PAGES.some(([id]) => id === ctx.query.page) || REFERENCE_PAGES.some(([id]) => id === ctx.query.page) ? ctx.query.page : "start" };
    const article = h("div", { class: "aa-doc-body", id: "aaDocBody", tabindex: "-1" });
    const nav = h("nav", { class: "aa-docnav", "aria-label": "Documentation" });
    const search = h("input", { type: "search", id: "aaDocSearch", placeholder: "Search pages and engines", autocomplete: "off", "aria-label": "Search the documentation" });
    const navLink = (label, query, extra = null) => {
      const on = (query.engine && current.engine === query.engine) || (query.page && current.page === query.page);
      const a = h("a", { href: link("docs", query), "aria-current": on ? "page" : "false" }, h("span", { text: label }), extra);
      a.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
        event.preventDefault();
        show(query, { scroll: true });
      });
      return a;
    };
    function paintNav() {
      const needle = search.value.trim().toLowerCase();
      const guide = DOC_PAGES.filter(([, label]) => !needle || label.toLowerCase().includes(needle));
      const refs = REFERENCE_PAGES.filter(([, label]) => !needle || label.toLowerCase().includes(needle));
      const engines = list.engines.filter((e) => !needle || `${e.id} ${e.name} ${e.family}`.toLowerCase().includes(needle));
      const byCategory = new Map();
      for (const e of engines) { if (!byCategory.has(e.category)) byCategory.set(e.category, []); byCategory.get(e.category).push(e); }
      const parts = [];
      if (guide.length) parts.push(h("div", { class: "aa-navgroup" }, h("h3", { text: "Guide" }), ...guide.map(([id, label]) => navLink(label, { page: id }))));
      if (refs.length && googleEntry) parts.push(h("div", { class: "aa-navgroup" }, h("h3", { text: "References" }), ...refs.map(([id, label]) => navLink(label, { page: id }))));
      parts.push(h("div", { class: "aa-navgroup" }, h("h3", {}, "Engines", h("span", { class: "sk-count", text: String(engines.length) })),
        ...(engines.length ? list.categories.filter((c) => byCategory.has(c.id)).map((c) => {
          const activeHere = current.engine && byCategory.get(c.id).some((e) => e.id === current.engine);
          const details = h("details", { open: !!needle || activeHere || null }, h("summary", {}, c.label, h("span", { class: "sk-count", text: String(byCategory.get(c.id).length) })), ...byCategory.get(c.id).map((e) => navLink(e.name, { engine: e.id }, e.deprecated ? h("small", { class: "sk-dim", text: "deprecated" }) : null)));
          return details;
        }) : [h("p", { class: "sk-dim", text: "No engine matches." })])));
      nav.replaceChildren(...parts);
    }
    async function show(query, { scroll = false } = {}) {
      current = query;
      setQuery({ page: query.page || "", engine: query.engine || "" });
      paintNav();
      article.replaceChildren(loading(4));
      try {
        let node;
        if (query.engine) { if (!engineById(query.engine)) throw Object.assign(new Error(`There is no engine called ${query.engine}.`), { status: 404 }); node = await enginePage(query.engine); }
        else if (REFERENCE_PAGES.some(([id]) => id === query.page)) { const [, title, param] = REFERENCE_PAGES.find(([id]) => id === query.page); node = referencePage(googleEntry, param, title); }
        else node = guidePage(query.page, docs, platform);
        article.replaceChildren(node);
        if (scroll) article.scrollIntoView({ block: "start", behavior: "auto" });
      } catch (error) { article.replaceChildren(failure(error, () => show(query))); }
    }
    search.addEventListener("input", debounce(paintNav, 100));
    const navWrap = h("details", { class: "aa-docnav-wrap", open: window.innerWidth > 900 || null },
      h("summary", { text: "Pages and engines" }),
      h("div", { class: "sk-filter-row" }, ico("search"), Object.assign(search, { className: "sk-filter" })), nav);
    content.replaceChildren(h("div", { class: "aa-docs" }, navWrap, article));
    await show(current);
  };

  // ===========================================================================
  // Status
  // ===========================================================================

  const percentile = (values, p) => { if (!values.length) return 0; const sorted = [...values].sort((a, b) => a - b); return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))]; };

  tabs.status = async (content) => {
    const auto = serp.toggleSwitch("Refresh every 30 seconds", Aira.recall("airaapi.status.auto", true), (on) => Aira.remember("airaapi.status.auto", on), { hint: "Pauses while this tab is hidden." });
    const host = h("div", { class: "aa-status-host" });
    const checked = h("span", { class: "sk-dim", role: "status" });
    const load = async (refresh = false) => {
      const [status, recent, key] = await Promise.all([
        api(`${BASE}/status${refresh ? "?refresh=1" : ""}`, { silent: !refresh && host.childElementCount > 0 }),
        api(`${BASE}/searches?limit=200`, { silent: true }),
        api(`${BASE}/overview`, { silent: true }).then((data) => (!data.configured ? { state: "none" } : data.account ? { state: "valid", account: data.account } : { state: /key|401|invalid/i.test(data.account_error || "") ? "rejected" : "unknown", error: { message: data.account_error || "The account check failed." } })).catch((error) => ({ state: "unknown", error })),
        loadEngines().catch(() => null),
      ]);
      paint(status, recent.items, key);
      checked.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
    };
    function paint(status, records, key) {
      const sp = status.serpapi, local = status.local, throttle = status.throttle, queue = status.queue;
      const problems = [];
      if (!local.db_ok) problems.push("The local database did not answer.");
      if (!sp.reachable) problems.push(`SerpApi is not reachable${sp.error ? `: ${sp.error}` : ""}.`);
      if (key.state === "rejected") problems.push("SerpApi rejected the stored key.");
      if (throttle.hourly_limit && throttle.used_this_hour >= throttle.hourly_limit) problems.push("Aira's hourly cap is used up: live searches are refused until the hour rolls over.");
      const banner = problems.length
        ? h("div", { class: "aa-banner bad", role: "alert" }, ico("shield"), h("div", {}, h("strong", { text: problems.length === 1 ? "One thing needs attention" : `${problems.length} things need attention` }), h("ul", {}, ...problems.map((p) => h("li", { text: p })))))
        : h("div", { class: "aa-banner good", role: "status" }, ico("check"), h("div", {}, h("strong", { text: "Everything is working" }), h("p", { text: "The local server, database and SerpApi all answered." })));
      const card = (title, tone, ...children) => h("section", { class: `aa-status-card ${tone}`, "aria-label": title }, h("h3", {}, h("span", { class: `aa-dot ${tone}`, "aria-hidden": "true" }), title), ...children);
      const cards = h("div", { class: "aa-status-grid" },
        card("Local server", local.db_ok ? "good" : "bad", factList([["Version", local.version], ["Uptime", uptimeText(local.uptime_s)], ["Database", local.db_ok ? "Answering" : "Not answering"]])),
        card("SerpApi", sp.reachable ? "good" : "bad", factList([["Reachable", sp.reachable ? "Yes" : "No"], ["Latency", ms(sp.latency_ms)], ["Checked", fmt.ago(sp.checked_at)], ["Detail", sp.error || ""]]), h("p", { class: "sk-hint", text: "Probed with SerpApi's free locations endpoint, so checking costs no search." })),
        card("SerpApi key", key.state === "valid" ? "good" : key.state === "none" ? "idle" : "bad",
          key.state === "valid" ? factList([["Account", key.account.account_status || "–"], ["Plan", key.account.plan_name || "–"], ["Searches left", num(key.account.total_searches_left)]])
            : h("p", { class: "muted" }, key.state === "none" ? ["No key connected. ", h("a", { href: link("account"), text: "Connect one" }), "."] : key.state === "rejected" ? ["SerpApi rejected the key. ", h("a", { href: link("account"), text: "Replace it" }), "."] : (key.error && key.error.message) || "The check failed.")),
        card("Hourly cap and queue", throttle.hourly_limit && throttle.used_this_hour >= throttle.hourly_limit ? "bad" : "good",
          throttle.hourly_limit ? h("div", {}, h("div", { class: "aa-tp-top" }, h("span", { text: `${num(throttle.used_this_hour)} of ${num(throttle.hourly_limit)} live searches this hour` })), meter({ value: throttle.used_this_hour, max: throttle.hourly_limit, label: "Live searches this hour against the local cap", tone: throttle.used_this_hour / throttle.hourly_limit >= 0.8 ? "warn" : "" })) : h("p", { class: "muted" }, `No local cap. ${num(throttle.used_this_hour)} live searches this hour.`),
          factList([["Running now", num(queue.running)], ["Async pending", num(queue.async_pending)]])));

      // per-engine health from the last 200 stored searches (cache hits excluded: they never touched SerpApi)
      const live = records.filter((r) => !r.cache_hit && r.status !== "running");
      const byEngine = new Map();
      for (const r of live) {
        if (!byEngine.has(r.engine)) byEngine.set(r.engine, []);
        byEngine.get(r.engine).push(r);
      }
      const rows = [...byEngine].map(([id, items]) => {
        const ok = items.filter((r) => r.status === "ok");
        const lastError = items.find((r) => r.status === "error");
        return { id, name: items[0].engine_name, runs: items.length, rate: ok.length / items.length, p50: percentile(ok.map((r) => r.duration_ms), 50), p95: percentile(ok.map((r) => r.duration_ms), 95), lastError };
      }).sort((a, b) => b.runs - a.runs);
      const health = rows.length
        ? simpleTable("Engine health from your recent searches", ["Engine", { label: "Live runs", num: true }, { label: "Success", num: true }, { label: "Median", num: true }, { label: "Slowest 5%", num: true }, "Last error"],
          rows.map((r) => [h("a", { href: link("history", { engine: r.id }), text: r.name }), num(r.runs), h("span", { class: r.rate < 0.9 ? "aa-bad-text" : "", text: percent(r.rate, r.rate < 1 ? 1 : 0) }), ms(r.p50), ms(r.p95), r.lastError ? h("span", { class: "aa-err-cell", title: r.lastError.error, text: `${fmt.ago(r.lastError.created_at)}: ${r.lastError.error}`.slice(0, 90) }) : h("span", { class: "sk-dim", text: "none" })]))
        : ui.empty("No live searches yet", "Success rate and latency per engine are computed from the searches Aira ran itself. Run a few and they appear here.");
      const series = live.filter((r) => r.status === "ok" && r.duration_ms).slice(0, 60).reverse();
      const chart = series.length >= 3
        ? serp.lineChart({ series: [{ name: "Response time", values: series.map((r) => r.duration_ms) }], labels: series.map((r) => new Date(r.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })), height: 190, format: (v) => ms(v), area: true, ariaLabel: "Response time of the last live searches", tableLabel: "Search" })
        : h("p", { class: "sk-dim", text: "A latency chart appears after a few live searches." });
      host.replaceChildren(banner, cards,
        h("div", { class: "aa-cols" }, panel("Engine health", { sub: `last ${num(live.length)} live searches, cache hits excluded`, body: health }), panel("Response time", { sub: "most recent live searches", body: panelBody(chart) })),
        panel("SerpApi's own status", { body: panelBody(h("p", { class: "muted", text: "SerpApi publishes no status API, so Aira computes health from its own run logs and the reachability probe above. For platform-wide incidents and hourly metrics for every engine, use SerpApi's pages." }),
          h("div", { class: "btn-row" }, btn("API metrics", { icon: "external", sm: true, href: SERPAPI_LINKS.status, external: true }), btn("Incident page and alerts", { icon: "external", sm: true, href: SERPAPI_LINKS.incidents, external: true }))) }));
    }
    const refreshButton = btn("Check now", { icon: "refresh", kind: "", onClick: (event) => run(event.currentTarget, async () => { await load(true); }) });
    content.replaceChildren(h("div", { class: "aa-toolbar" }, h("div", { class: "aa-toolbar-main" }, refreshButton, checked), h("div", { class: "aa-toolbar-side" }, auto.node)), host);
    await load();
    every(30000, host, () => { if (auto.get()) load().catch(() => {}); });
  };

  // ===========================================================================
  // Boot
  // ===========================================================================

  Object.assign(Aira.airaapi, { sourceLabel, recordTitle });
})();

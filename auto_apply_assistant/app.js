/* Aira Career Copilot — core: API client, router, components, navigation, auth */
"use strict";

const Aira = (window.Aira = {
  state: { user: null, profile: null, settings: null, sources: null, dashboard: null, views: {}, inflight: 0 },
  views: {},
});

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value; // only used with trusted, locally built markup (SVG icons)
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, String(value));
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
const $ = (id) => document.getElementById(id);
Aira.h = h;

const ICONS = {
  dashboard: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/><rect x="13" y="11" width="8" height="10" rx="1.5"/><rect x="3" y="14" width="8" height="7" rx="1.5"/></svg>',
  jobs: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  applications: '<svg viewBox="0 0 24 24"><path d="M9 4h6l1 2h3a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3z"/><path d="m9 13 2 2 4-4"/></svg>',
  interviews: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
  recruiters: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.5c2.8.3 5 2.6 5 5.5"/></svg>',
  documents: '<svg viewBox="0 0 24 24"><path d="M6 3h8l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
  analytics: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/></svg>',
  settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>',
  leads: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="18" cy="7" r="2.5"/><path d="m16.5 12.5 1.5 1.5 3-3.5"/></svg>',
  careers: '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18M11 13h2"/></svg>',
  airaapi: '<svg viewBox="0 0 24 24"><path d="M8 4C6 4 5 5 5 7v2c0 1.5-1 2.5-2 3 1 .5 2 1.5 2 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1.5 1 2.5 2 3-1 .5-2 1.5-2 3v2c0 2-1 3-3 3"/><circle cx="12" cy="12" r="1.2"/></svg>',
};
Aira.icon = (name) => h("span", { html: ICONS[name] || "", "aria-hidden": "true", style: { display: "contents" } });

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------
function setProgress(active) {
  Aira.state.inflight = Math.max(0, Aira.state.inflight + (active ? 1 : -1));
  const bar = $("progress");
  bar.classList.toggle("active", Aira.state.inflight > 0);
  bar.setAttribute("aria-hidden", Aira.state.inflight > 0 ? "false" : "true");
}
class ApiError extends Error {
  constructor(message, status, requestId, code = "", data = null) {
    super(message);
    this.status = status;
    this.requestId = requestId;
    this.code = code; // machine-readable error code when the server sent one (for example not_configured)
    this.data = data; // the parsed error body, for callers that need extra fields such as record_id
  }
}
async function api(path, { method = "GET", body, raw = false, silent = false } = {}) {
  if (!silent) setProgress(true);
  try {
    const response = await fetch(path, {
      method,
      // A coach, administrator or auditor opens one candidate's workspace by naming them here (the hosted build).
      headers: { "Content-Type": "application/json", "X-Requested-With": "aira", Accept: raw ? "*/*" : "application/json", ...(Aira.state.actingFor ? { "X-Aira-Candidate": Aira.state.actingFor.id } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    const requestId = response.headers.get("X-Request-ID") || "";
    if (raw) {
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(data.error || `Request failed (${response.status})`, response.status, requestId, data.code || "", data);
      }
      return response;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok && Aira.cloud && Aira.cloud.onApiError) Aira.cloud.onApiError(response.status, data, path);
    if (response.status === 401 && !path.startsWith("/api/auth/") && !path.startsWith("/api/platform/auth/") && !path.startsWith("/api/invites/") && !path.startsWith("/api/cloud/")) {
      Aira.showAuth();
      throw new ApiError("Your session ended. Sign in again.", 401, requestId);
    }
    if (!response.ok) throw new ApiError(data.error || `Request failed (${response.status})`, response.status, requestId, data.code || "", data);
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Aira is unreachable. Check that the server is running.", 0, "");
  } finally {
    if (!silent) setProgress(false);
  }
}
Aira.api = api;
Aira.ApiError = ApiError;

async function download(path, filename) {
  const response = await api(path, { raw: true });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = h("a", { href: url, download: filename || "download" });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
Aira.download = download;

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Could not read the file."));
    reader.readAsDataURL(file);
  });
}
Aira.fileToBase64 = fileToBase64;

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
const fmt = {
  stage: (stage) => (stage || "discovered").replaceAll("_", " "),
  title: (text) => (text || "").replace(/\b\w/g, (c) => c.toUpperCase()),
  date: (value) => {
    if (!value) return "—";
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString([], { month: "short", day: "numeric", year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  },
  datetime: (value, timeZone) => {
    if (!value) return "—";
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    try {
      return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: timeZone || undefined, timeZoneName: timeZone ? "short" : undefined });
    } catch {
      return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }
  },
  ago: (value) => {
    if (!value) return "—";
    const date = typeof value === "number" ? new Date(value * 1000) : new Date(String(value).includes("T") || String(value).includes("+") ? value : value + "Z");
    if (Number.isNaN(date.getTime())) return String(value);
    // Future times (an upcoming interview, a follow-up) read "in 3 d"; clamping them to zero showed "just now".
    const seconds = (Date.now() - date.getTime()) / 1000;
    const future = seconds < -60;
    const diff = Math.abs(seconds);
    const phrase = (n, unit) => (future ? `in ${n} ${unit}` : `${n} ${unit} ago`);
    if (diff < 60) return "just now";
    if (diff < 3600) return phrase(Math.floor(diff / 60), "min");
    if (diff < 86400) return phrase(Math.floor(diff / 3600), "h");
    if (diff < 86400 * 14) return phrase(Math.floor(diff / 86400), "d");
    return fmt.date(date.toISOString());
  },
  money: (min, max, currency) => {
    if (min == null && max == null) return "";
    const code = currency || "USD";
    const money = (n) => Number(n).toLocaleString([], { style: "currency", currency: code, maximumFractionDigits: 0 });
    if (min != null && max != null && min !== max) return `${money(min)} – ${money(max)}`;
    return money(min != null ? min : max);
  },
  bytes: (n) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`),
  pct: (value) => (value === null || value === undefined ? "n/a" : `${Math.round(value)}%`),
  plural: (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`,
};
Aira.fmt = fmt;

// ---------------------------------------------------------------------------
// Components: toast, confirm, modal, drawer, badges, states
// ---------------------------------------------------------------------------
function toast(message, type = "", { timeout = 5000 } = {}) {
  const node = h("div", { class: `toast ${type}`, role: type === "error" ? "alert" : "status" }, h("span", { text: message }), h("button", { type: "button", "aria-label": "Dismiss", text: "✕", onclick: () => node.remove() }));
  $("toasts").append(node);
  if (timeout) setTimeout(() => node.remove(), timeout);
  return node;
}
Aira.toast = toast;
Aira.reportError = (error) => {
  const suffix = error && error.requestId ? ` (request ${error.requestId})` : "";
  toast((error && error.message ? error.message : String(error)) + suffix, "error", { timeout: 8000 });
};

function trapFocus(container, previous) {
  const focusable = () => [...container.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
  const first = focusable()[0];
  if (first) first.focus();
  const handler = (event) => {
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) return;
    const index = items.indexOf(document.activeElement);
    if (event.shiftKey && (index <= 0)) { event.preventDefault(); items[items.length - 1].focus(); }
    else if (!event.shiftKey && index === items.length - 1) { event.preventDefault(); items[0].focus(); }
  };
  container.addEventListener("keydown", handler);
  return () => { container.removeEventListener("keydown", handler); if (previous && previous.focus) previous.focus(); };
}

function modal({ title, body, actions = [], wide = false, onClose }) {
  const previous = document.activeElement;
  const backdrop = h("div", { class: "modal-backdrop" });
  const box = h("div", { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-labelledby": "modalTitle" });
  const close = () => { release(); backdrop.remove(); if (onClose) onClose(); };
  const actionRow = h("div", { class: "actions" });
  for (const action of actions) {
    actionRow.append(h("button", { type: "button", class: `btn ${action.kind || "secondary"}`, text: action.label, onclick: async (event) => { if (action.onClick) { const result = await withBusy(event.currentTarget, () => action.onClick(close)); if (result === false) return; } if (action.close !== false) close(); } }));
  }
  box.append(h("h2", { id: "modalTitle", text: title }), body, actionRow);
  backdrop.append(box);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
  backdrop.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } });
  $("overlay").append(backdrop);
  const release = trapFocus(box, previous);
  return { close, box };
}
Aira.modal = modal;

function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    modal({
      title,
      body: h("p", { class: "muted", text: message }),
      actions: [
        { label: "Cancel", kind: "ghost", onClick: () => resolve(false) },
        { label: confirmLabel, kind: danger ? "danger" : "", onClick: () => resolve(true) },
      ],
      onClose: () => resolve(false),
    });
  });
}
Aira.confirm = confirmDialog;

function drawer({ title, subtitle, body, onClose, headActions = [] }) {
  closeDrawer();
  const previous = document.activeElement;
  const backdrop = h("div", { class: "drawer-backdrop" });
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": title });
  // close(false) skips onClose: the router uses it when the section under the drawer is being replaced anyway.
  const close = (notify = true) => { release(); backdrop.remove(); panel.remove(); Aira.activeDrawer = null; if (onClose && notify !== false) onClose(); };
  const head = h("div", { class: "drawer-head" }, h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { text: title }), subtitle ? h("div", { class: "sub" }, subtitle) : null), h("div", { class: "btn-row" }, ...headActions, h("button", { type: "button", class: "btn ghost icon", "aria-label": "Close", text: "✕", onclick: close })));
  const content = h("div", { class: "drawer-body" }, body);
  panel.append(head, content);
  backdrop.addEventListener("click", close);
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } });
  $("overlay").append(backdrop, panel);
  const release = trapFocus(panel, previous);
  Aira.activeDrawer = { close, panel, body: content, head };
  return Aira.activeDrawer;
}
function closeDrawer() { if (Aira.activeDrawer) Aira.activeDrawer.close(); }
Aira.drawer = drawer;
Aira.closeDrawer = closeDrawer;

async function withBusy(button, fn) {
  if (!button || button.getAttribute("aria-busy") === "true") return;
  const label = button.textContent;
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
  try { return await fn(); }
  catch (error) { Aira.reportError(error); return false; }
  finally {
    button.removeAttribute("aria-busy"); button.disabled = false; button.textContent = label;
    // Disabling the focused button dropped focus to <body>, which left a modal unreachable by Escape after a failed action.
    if (button.isConnected && document.activeElement === document.body) button.focus();
  }
}
Aira.withBusy = withBusy;

const ui = {
  stage: (stage) => h("span", { class: `badge stage ${stage || "discovered"}`, text: fmt.stage(stage) }),
  source: (source) => h("span", { class: "badge source", text: (source || "").replaceAll("_", " ") }),
  classification: (label) => (label ? h("span", { class: `badge class-${label.split(" ")[0]}`, text: label }) : null),
  score: (value) => {
    if (value === null || value === undefined) return h("span", { class: "score none", title: "Not scored yet" }, h("span", { text: "—" }));
    return h("span", { class: "score", style: { "--pct": Math.round(value) }, title: `Match score ${Math.round(value)}%` }, h("span", { text: Math.round(value) }));
  },
  empty: (title, text, action) => h("div", { class: "empty" }, h("strong", { text: title }), text ? h("div", { text }) : null, action || null),
  error: (error, retry) => h("div", { class: "error-box", role: "alert" }, h("strong", { text: "Something went wrong" }), h("div", { text: error && error.message ? error.message : String(error) }), error && error.requestId ? h("div", { class: "muted", text: `Request ID ${error.requestId}` }) : null, retry ? h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary sm", text: "Try again", onclick: retry })) : null),
  skeleton: (rows = 4) => h("div", { class: "skeleton-list", "aria-busy": "true", "aria-label": "Loading" }, ...Array.from({ length: rows }, () => h("div", { class: "skeleton" }))),
  field: (label, input, hint) => { const id = input.id || (input.id = `f${Math.random().toString(36).slice(2, 8)}`); return h("div", { class: "field" }, h("label", { for: id, text: label }), input, hint ? h("span", { class: "hint", text: hint }) : null); },
  select: (options, value, attrs = {}) => { const select = h("select", attrs); for (const option of options) { const [val, label] = Array.isArray(option) ? option : [option, option]; select.append(h("option", { value: val, text: label, selected: val === value })); } return select; },
  kv: (pairs) => h("dl", { class: "kv" }, ...pairs.filter(([, v]) => v !== undefined && v !== null && v !== "").flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : String(v))])),
  tabs: (items, active, onChange) => {
    const bar = h("div", { class: "tabs", role: "tablist" });
    for (const item of items) {
      bar.append(h("button", { type: "button", role: "tab", "aria-selected": item.id === active ? "true" : "false", text: item.label, onclick: () => onChange(item.id), onkeydown: (event) => { const buttons = [...bar.querySelectorAll("button")]; const index = buttons.indexOf(event.currentTarget); if (event.key === "ArrowRight") buttons[(index + 1) % buttons.length].focus(); if (event.key === "ArrowLeft") buttons[(index - 1 + buttons.length) % buttons.length].focus(); } }));
    }
    return bar;
  },
};
Aira.ui = ui;

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
// One account, many apps (docs/apps.md). APPS mirrors aira/apps.py: the switcher lists them, `g` then the app's
// key opens its home, and the sidebar shows only the current app's sections. GET /api/apps says which of them
// this account may open (the hosted build sells each one as a subscription).
const APPS = [
  { id: "careers", name: "Careers", tagline: "Find, apply and track", icon: "careers", home: "dashboard", key: "c" },
  { id: "leads", name: "Leads", tagline: "Find people who need what you sell", icon: "leads", home: "leads", key: "l" },
  { id: "api", name: "API", tagline: "Search engines and keys", icon: "airaapi", home: "airaapi", key: "p" },
];
// One list drives the sidebar, the phone bottom bar, the `g` then <key> chords and the shortcuts help.
// `app` says which app a section belongs to; items in the Account group show under every app.
// `quick` items sit in the bottom bar; everything else is reached through More, which opens the sidebar.
const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard", app: "careers", group: "Careers", key: "d", quick: true },
  { id: "jobs", label: "Jobs", icon: "jobs", app: "careers", group: "Careers", key: "j", quick: true },
  { id: "applications", label: "Applications", icon: "applications", app: "careers", group: "Careers", key: "a", quick: true },
  { id: "interviews", label: "Interviews", icon: "interviews", app: "careers", group: "Careers", key: "i", quick: true },
  { id: "recruiters", label: "Recruiters", icon: "recruiters", app: "careers", group: "Careers", key: "r" },
  { id: "documents", label: "Documents", icon: "documents", app: "careers", group: "Careers", key: "o" },
  { id: "analytics", label: "Analytics", icon: "analytics", app: "careers", group: "Careers", key: "n" },
  { id: "careers", label: "Research", icon: "careers", app: "careers", group: "Careers", key: "f" },
  { id: "leads", label: "Leads", icon: "leads", app: "leads", group: "Leads", key: "l", quick: true },
  { id: "airaapi", label: "API", icon: "airaapi", app: "api", group: "API", key: "p", quick: true },
  { id: "settings", label: "Settings", icon: "settings", app: "account", group: "Account", key: "s" },
];
Aira.NAV = NAV; // the hosted build replaces the entries by role
Aira.APPS = APPS;

// ---------------------------------------------------------------------------
// Apps: the switcher, the current app, the pseudo-apps a build adds (Account, Coaching, Admin console)
// ---------------------------------------------------------------------------
const apps = (Aira.apps = {
  catalog: APPS,
  // Apps a build adds for sections that are not products: the hosted Account (organization) pages, Coaching, the Admin console.
  pseudo: {},
  state: { enabled: {}, reasons: {}, accountUrl: "", localUrl: "", installation: "local", loaded: false, extra: [] },
  decorate: null, // a build may adjust the list the switcher shows: (entries) => entries
  async load() {
    try {
      const data = await api("/api/apps", { silent: true });
      for (const item of data.apps || []) {
        apps.state.enabled[item.id] = item.enabled !== false;
        apps.state.reasons[item.id] = item.reason || "";
      }
      apps.state.accountUrl = String(data.account_url || "");
      apps.state.localUrl = String(data.local_url || "");
      apps.state.installation = data.installation || (Aira.cloud ? "hosted" : "local");
      apps.state.loaded = true;
    } catch { /* an older server or a role without the route: every app counts as available */ }
    return apps.state;
  },
  // The app a section belongs to; the Account group shows under every app, so it keeps the app you were in.
  appOf(section) {
    const item = NAV.find((entry) => entry.id === section);
    if (!item) return null;
    if (item.group === "Account") return null;
    return item.app || null;
  },
  current() {
    const section = Router.current && Router.current.path[0];
    const owner = section ? apps.appOf(section) : null;
    if (owner) Aira.state.currentApp = owner;
    const id = Aira.state.currentApp || (NAV.find((entry) => entry.group !== "Account") || {}).app || APPS[0].id;
    return apps.describe(id);
  },
  describe(id) {
    const product = APPS.find((entry) => entry.id === id);
    if (product) return { ...product, enabled: apps.state.enabled[id] !== false, reason: apps.state.reasons[id] || "" };
    const extra = apps.pseudo[id];
    if (extra) return { id, enabled: true, reason: "", ...extra };
    return { id, name: id, tagline: "", icon: "dashboard", home: id, enabled: true, reason: "" };
  },
  homeOf(id) {
    const described = apps.describe(id);
    if (NAV.some((entry) => entry.id === described.home && entry.app === id)) return described.home;
    const first = NAV.find((entry) => entry.app === id && entry.group !== "Account");
    return first ? first.id : described.home;
  },
  // What the switcher shows: every product app, plus the pseudo-apps that have sections in this build.
  available() {
    const present = new Set(NAV.filter((entry) => entry.group !== "Account").map((entry) => entry.app));
    const entries = [];
    for (const id of Object.keys(apps.pseudo)) if (present.has(id)) entries.push({ ...apps.describe(id), kind: "account" });
    for (const product of APPS) {
      const described = apps.describe(product.id);
      entries.push({ ...described, kind: "product", present: present.has(product.id) });
    }
    const list = apps.decorate ? apps.decorate(entries) : entries;
    return [...list, ...apps.state.extra];
  },
  open(id) {
    const home = apps.homeOf(id);
    if (home) Router.go([home]);
  },
});
// Phones open the sidebar as a sheet (menu button or More); this keeps both triggers and the sheet in step.
function setSidebarOpen(open) {
  $("sidebar").classList.toggle("open", open);
  $("menuBtn").setAttribute("aria-expanded", String(open));
  const more = document.querySelector('#bottomNav a[data-section="more"]');
  if (more) more.setAttribute("aria-expanded", String(open));
}
Aira.setSidebarOpen = setSidebarOpen;
const Router = {
  parse() {
    const hash = location.hash.replace(/^#\/?/, "");
    const [pathPart, queryPart = ""] = hash.split("?");
    const path = pathPart.split("/").filter(Boolean).map(decodeURIComponent);
    const query = Object.fromEntries(new URLSearchParams(queryPart));
    return { path, query };
  },
  go(path, query = {}, { replace = false } = {}) {
    const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    const qs = new URLSearchParams(clean).toString();
    const target = `#/${path.map(encodeURIComponent).join("/")}${qs ? `?${qs}` : ""}`;
    if (replace) history.replaceState(null, "", target); else location.hash = target;
    if (replace) Router.render();
  },
  current: null,
  async render() {
    const route = Router.parse();
    if (!route.path.length) { Router.go(["dashboard"], {}, { replace: true }); return; }
    const section = route.path[0];
    const view = Aira.views[section];
    const main = $("main");
    Router.current = route;
    // A drawer belongs to the section it was opened from; Back, a link inside it, or a chord must not leave it over the next page.
    if (Aira.activeDrawer) Aira.activeDrawer.close(false);
    // Moving into another app redraws the sidebar for that app; inside an app only the current mark moves.
    const before = Aira.state.currentApp;
    const app = apps.current();
    if (app.id !== before || !document.querySelector(`#primaryNav a[data-section="${CSS.escape(section)}"]`)) renderNav();
    for (const link of document.querySelectorAll("#primaryNav a, #bottomNav a")) link.setAttribute("aria-current", link.dataset.section === section ? "page" : "false");
    // A section that is not in the bottom bar lives behind More, so More shows as the current place.
    const more = document.querySelector('#bottomNav a[data-section="more"]');
    if (more && NAV.some((item) => item.id === section && !item.quick)) more.setAttribute("aria-current", "page");
    setSidebarOpen(false);
    if (!view) { main.replaceChildren(ui.empty("Page not found", "Choose a section from the navigation.")); return; }
    const navItem = NAV.find((item) => item.id === section);
    const label = navItem ? navItem.label : section;
    const inProduct = APPS.some((entry) => entry.id === app.id) && navItem && navItem.group !== "Account";
    $("crumbs").replaceChildren(...(inProduct ? [h("span", { class: "crumb-app", text: app.name }), h("span", { class: "crumb-sep", "aria-hidden": "true", text: "/" })] : []), h("span", { text: label }));
    document.title = `${label} · ${inProduct ? `${app.name} · ` : ""}Aira`;
    // Each render draws into its own stage. A newer render swaps in a fresh stage, so a slower
    // earlier view (the dashboard still loading when a link is followed) writes into a detached
    // element instead of over the page the person moved to.
    const stage = h("div", { class: "view-stage" }, ui.skeleton(5));
    main.replaceChildren(stage);
    try { await view(stage, route); }
    catch (error) { console.error(error); if (stage.isConnected) stage.replaceChildren(ui.error(error, () => Router.render())); }
  },
};
Aira.router = Router;
// A view can claim a hash change it handles in place (the Messages tab opening or
// closing a thread on Back and Forward) by returning false from Aira.onHashChange.
window.addEventListener("hashchange", () => { if (Aira.onHashChange && Aira.onHashChange() === false) return; Router.render(); });

// ---------------------------------------------------------------------------
// Persisted filters
// ---------------------------------------------------------------------------
Aira.remember = (key, value) => { try { localStorage.setItem(`aira.${key}`, JSON.stringify(value)); } catch { /* storage unavailable */ } };
Aira.recall = (key, fallback) => { try { const raw = localStorage.getItem(`aira.${key}`); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; } };

// ---------------------------------------------------------------------------
// Shell: navigation, status strip, theme, auth
// ---------------------------------------------------------------------------
// The sections of the current app (and the Account group, which shows under every app).
function visibleNav() {
  const app = apps.current();
  return NAV.filter((item) => item.app === app.id || item.group === "Account");
}
Aira.visibleNav = visibleNav;

function renderNav(counts = {}) {
  // Counts merge, so a widget can set one badge (Careers alerts) without wiping the others.
  Aira.state.navCounts = { ...(Aira.state.navCounts || {}), ...counts };
  const shown = Aira.state.navCounts;
  const current = Router.current && Router.current.path[0];
  const app = apps.current();
  const items = visibleNav();
  $("brandApp").textContent = app.name || "Aira";
  const link = (item) => h("a", { href: `#/${item.id}`, dataset: { section: item.id }, "aria-current": current === item.id ? "page" : "false", "aria-keyshortcuts": `g ${item.key}`, onclick: () => { if (window.innerWidth <= 900) setSidebarOpen(false); } }, Aira.icon(item.icon), h("span", { text: item.label }), shown[item.id] ? h("span", { class: "count", text: shown[item.id] }) : null);
  // The app's own groups first, the Account group (shown under every app) last.
  const groups = [...new Set(items.map((item) => item.group))].sort((a, b) => (a === "Account") - (b === "Account"));
  const head = h("div", { class: "nav-apphead", id: "navAppHead" }, Aira.icon(app.icon || "dashboard"), h("div", {}, h("strong", { text: app.name || "Aira" }), app.tagline ? h("span", { class: "muted", text: app.tagline }) : null));
  $("primaryNav").replaceChildren(head, ...groups.map((group) => {
    const labelId = `navGroup${group.replace(/\W+/g, "")}`;
    return h("div", { class: "nav-group", role: "group", "aria-labelledby": labelId }, h("span", { class: "nav-label", id: labelId, text: group }), ...items.filter((item) => item.group === group).map(link));
  }));
  const bottom = $("bottomNav");
  const more = h("a", { href: "#", dataset: { section: "more" }, role: "button", "aria-controls": "sidebar", "aria-expanded": String($("sidebar").classList.contains("open")), "aria-current": current && items.some((item) => item.id === current && !item.quick) ? "page" : "false", onclick: (event) => { event.preventDefault(); setSidebarOpen(!$("sidebar").classList.contains("open")); } }, Aira.icon("more"), h("span", { text: "More" }));
  const hidden = items.filter((item) => !item.quick).reduce((sum, item) => sum + (Number(shown[item.id]) || 0), 0);
  if (hidden) more.append(h("span", { class: "dot", role: "img", "aria-label": `${hidden} waiting in More` }));
  bottom.replaceChildren(...items.filter((item) => item.quick).slice(0, 4).map((item) => h("a", { href: `#/${item.id}`, dataset: { section: item.id }, "aria-current": current === item.id ? "page" : "false" }, Aira.icon(item.icon), h("span", { text: item.label }))), more);
}
Aira.renderNav = renderNav;

// ---------------------------------------------------------------------------
// Menus in the top bar: the app switcher and the account menu
// ---------------------------------------------------------------------------
const openMenus = new Set();
function closeMenus(except = null) {
  for (const menu of [...openMenus]) if (menu !== except) menu.close();
}
// A popover under a button: Enter/Space/click opens, Escape and an outside click close, arrows move through the items.
function popover(buttons, panel, { render, onClose } = {}) {
  const triggers = buttons.filter(Boolean);
  const menu = {
    isOpen: () => !panel.hidden,
    open(from) {
      closeMenus(menu);
      if (render) render(panel);
      panel.hidden = false;
      for (const button of triggers) button.setAttribute("aria-expanded", "true");
      // On phones the open menu must sit above the navigation sheet, which is above the top bar otherwise.
      const bar = panel.closest(".topbar");
      if (bar) bar.classList.add("menu-open");
      openMenus.add(menu);
      menu.from = from || triggers[0];
      const first = panel.querySelector('[role="menuitem"]:not([aria-disabled="true"])');
      if (first) first.focus();
    },
    close(focusBack = true) {
      if (panel.hidden) return;
      panel.hidden = true;
      for (const button of triggers) button.setAttribute("aria-expanded", "false");
      const bar = panel.closest(".topbar");
      if (bar && ![...openMenus].some((other) => other !== menu && other.isOpen())) bar.classList.remove("menu-open");
      openMenus.delete(menu);
      if (onClose) onClose();
      if (focusBack && menu.from && menu.from.isConnected && window.getComputedStyle(menu.from).display !== "none") menu.from.focus();
    },
    toggle(from) { if (panel.hidden) menu.open(from); else menu.close(); },
  };
  for (const button of triggers) button.addEventListener("click", (event) => { event.stopPropagation(); menu.toggle(button); });
  panel.addEventListener("keydown", (event) => {
    const items = [...panel.querySelectorAll('[role="menuitem"]:not([aria-disabled="true"])')];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); menu.close(); return; }
    if (event.key === "Tab") { menu.close(false); return; }
    if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key) || !items.length) return;
    event.preventDefault();
    const step = ["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1;
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + step + items.length) % items.length;
    items[next].focus();
  });
  panel.addEventListener("click", (event) => { if (event.target.closest("a[href], button[data-close]")) menu.close(false); });
  return menu;
}
document.addEventListener("click", (event) => { if (!event.target.closest(".popover")) closeMenus(); });

function appTile(entry) {
  const disabled = entry.enabled === false;
  const attrs = { class: `app-tile ${disabled ? "disabled" : ""} ${entry.kind === "account" ? "account" : ""}`, role: "menuitem", tabindex: "-1", dataset: { app: entry.id } };
  if (disabled) { attrs["aria-disabled"] = "true"; attrs.title = entry.reason || "Not available"; }
  const node = entry.href
    ? h("a", { ...attrs, href: entry.href, ...(entry.external ? { target: "_blank", rel: "noopener noreferrer" } : {}) })
    : h("button", { ...attrs, type: "button", onclick: (event) => { event.preventDefault(); if (disabled) return; closeMenus(); apps.open(entry.id); } });
  node.append(h("span", { class: "app-tile-icon" }, Aira.icon(entry.icon || "dashboard")), h("span", { class: "app-tile-name", text: entry.name }), h("span", { class: "app-tile-tag", text: disabled && entry.reason ? entry.reason : entry.tagline || "" }));
  return node;
}
function renderSwitcher(panel) {
  const entries = apps.available();
  const current = apps.current();
  const tiles = entries.map((entry) => { const tile = appTile(entry); if (entry.id === current.id) tile.setAttribute("aria-current", "true"); return tile; });
  const links = [];
  const local = apps.state.installation === "local";
  if (local && apps.state.accountUrl) links.push(h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: `${apps.state.accountUrl}/#/org`, target: "_blank", rel: "noopener noreferrer" }, Aira.icon("settings"), h("span", { text: "Manage your account" })));
  if (!local && apps.state.localUrl) links.push(h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: apps.state.localUrl, target: "_blank", rel: "noopener noreferrer" }, Aira.icon("dashboard"), h("span", { text: "Local workspace" })));
  const can = Aira.cloud && Aira.cloud.can;
  if (!local && can && (can("billing.manage") || can("org.settings"))) links.push(h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: "#/org/apps" }, Aira.icon("more"), h("span", { text: "More apps" })));
  // replaceChildren turns a null child into the text "null": only real nodes go in.
  panel.replaceChildren(
    ...[h("div", { class: "popover-title", text: "Aira apps" }), h("div", { class: "app-grid" }, ...tiles), links.length ? h("div", { class: "menu-list" }, ...links) : null].filter(Boolean),
  );
}
Aira.renderSwitcher = renderSwitcher;

// The account menu: who is signed in, where the account is managed, theme, shortcuts, sign out.
Aira.accountLinks = []; // a build adds entries: { label, href, icon?, external?, when?() }
function renderAccount() {
  const user = Aira.state.user || {};
  const email = String(user.email || "");
  const initial = (email[0] || "A").toUpperCase();
  $("accountAvatar").textContent = initial;
  $("accountAvatarLarge").textContent = initial;
  $("accountBtnEmail").textContent = email;
  if (!$("accountEmail").textContent) $("accountEmail").textContent = email;
  const cloud = Aira.cloud || null;
  const me = cloud && cloud.me;
  const links = [];
  if (me && me.kind === "member") {
    if (cloud.isStaff && cloud.isStaff()) links.push({ label: "Manage your account", href: "#/org", icon: "settings" });
    else links.push({ label: "Account settings", href: "#/settings", icon: "settings" });
    if (Array.isArray(me.orgs) && me.orgs.length > 1) links.push({ label: "Switch organization", href: "#/switch-org", icon: "recruiters" });
  } else if (!me && apps.state.installation === "local" && apps.state.accountUrl) {
    links.push({ label: "Manage your account", href: `${apps.state.accountUrl}/#/org`, icon: "settings", external: true });
  }
  for (const extra of Aira.accountLinks) if (!extra.when || extra.when(user)) links.push(extra);
  $("accountLinks").replaceChildren(...links.map((entry) => h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: entry.href, ...(entry.external ? { target: "_blank", rel: "noopener noreferrer" } : {}) }, Aira.icon(entry.icon || "settings"), h("span", { text: entry.label }))));
  $("accountLinks").hidden = !links.length;
}
Aira.renderAccount = renderAccount;

async function refreshStatus() {
  const strip = $("statusStrip");
  try {
    const [linkedin, status] = await Promise.all([api("/api/linkedin/status", { silent: true }), api("/api/status", { silent: true })]);
    Aira.state.linkedin = linkedin;
    Aira.state.status = status;
    const pill = (ok, label, title, warn = false, href = "#/settings/integrations") => h("a", { class: "status-pill", href, title }, h("i", { class: ok ? "ok" : warn ? "warn" : "" }), h("span", { text: label }));
    strip.replaceChildren(
      pill(linkedin.state === "connected", linkedin.state === "connected" ? "LinkedIn connected" : linkedin.state === "login" || linkedin.state === "starting" ? "LinkedIn connecting" : "LinkedIn off", linkedin.message, linkedin.state === "login" || linkedin.state === "starting"),
      pill(status.ollama, status.ollama ? "Ollama ready" : "Ollama offline", status.ollama ? "Local model available for reviews and drafts" : "Start Ollama to review fit and generate drafts"),
      pill(status.google_jobs, status.google_jobs ? "SerpApi ready" : "SerpApi off", status.google_jobs ? "A SerpApi key is connected: Google Jobs, Careers and the API console can search" : "Connect a SerpApi key in API > Account to use Google Jobs, Careers and the API console", false, "#/airaapi?tab=account"),
    );
    if (linkedin.state === "login" || linkedin.state === "starting") setTimeout(refreshStatus, 3000);
  } catch { /* status is best-effort */ }
}
Aira.refreshStatus = refreshStatus;

function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute("data-theme", theme); else document.documentElement.removeAttribute("data-theme");
  Aira.remember("theme", theme || "");
}

function setAuthMode(mode) {
  Aira.state.authMode = mode;
  const register = mode === "register";
  $("authTitle").textContent = register ? "Create your workspace" : "Sign in";
  $("authSubmit").textContent = register ? "Create account" : "Sign in";
  $("authSwitchText").textContent = register ? "Already registered?" : "New to Aira?";
  $("authSwitch").textContent = register ? "Sign in" : "Create an account";
  $("authPassword").autocomplete = register ? "new-password" : "current-password";
  $("authHint").textContent = register ? "Use at least 12 characters. This password protects your local workspace." : "";
  $("authNotice").textContent = "";
}
Aira.showAuth = () => { $("boot").hidden = true; $("app").hidden = true; $("authScreen").hidden = false; $("authEmail").focus(); };

async function loadApp() {
  try {
    const user = await api("/api/auth/me", { silent: true });
    Aira.state.user = user;
    $("accountEmail").textContent = user.email;
    $("authScreen").hidden = true;
    $("app").hidden = false;
    $("boot").hidden = true;
  } catch { Aira.showAuth(); return; }
  // The hosted build decides which sections this person has and whether there is a workspace to load.
  let workspace = true;
  if (Aira.cloud && Aira.cloud.boot) workspace = (await Aira.cloud.boot(Aira.state.user)) !== false;
  Aira.state.currentApp = null;
  renderNav();
  renderAccount();
  // The hosted build hides the sections of apps the organization has not subscribed to once the list arrives.
  apps.load().then(() => { if (Aira.cloud && Aira.cloud.buildNav) Aira.cloud.buildNav(); else renderNav(); renderAccount(); });
  if (workspace) await loadWorkspace();
  const landing = Aira.cloud && Aira.cloud.landing ? Aira.cloud.landing(workspace) : null;
  if (!location.hash || location.hash === "#/" || location.hash === "#") Router.go(landing || (Aira.state.profile && Aira.state.profile.resume_hash ? ["dashboard"] : ["settings", "resume"]), {}, { replace: true });
  else Router.render();
  if (!Aira.state.statusTimer) Aira.state.statusTimer = setInterval(() => { if (Aira.state.workspaceLoaded) refreshStatus(); }, 60000);
}

// Loads what the candidate-facing sections need. The hosted build calls it again when someone opens a candidate's workspace.
async function loadWorkspace() {
  refreshStatus();
  try {
    Aira.state.profile = await api("/api/profile", { silent: true });
    const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const settings = await api("/api/settings", { silent: true });
    Aira.state.settings = settings.settings;
    if (browserZone && settings.settings.timezone === "UTC" && !Aira.recall("tzPrompted", false) && !Aira.state.actingFor) {
      await api("/api/settings", { method: "POST", body: { timezone: browserZone }, silent: true }).then((data) => { Aira.state.settings = data.settings; }).catch(() => {});
      Aira.remember("tzPrompted", true);
    }
    Aira.state.workspaceLoaded = true;
  } catch (error) { Aira.reportError(error); }
}
Aira.loadWorkspace = loadWorkspace;

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
let chord = "";
document.addEventListener("keydown", (event) => {
  const tag = (event.target.tagName || "").toLowerCase();
  const typing = ["input", "textarea", "select"].includes(tag) || event.target.isContentEditable;
  if (event.key === "Escape") {
    if (Aira.activeDrawer) Aira.closeDrawer();
    else if ($("sidebar").classList.contains("open")) { setSidebarOpen(false); const trigger = window.innerWidth <= 900 ? document.querySelector('#bottomNav a[data-section="more"]') : null; if (trigger) trigger.focus(); }
    return;
  }
  if (typing) return;
  if (event.key === "/") { const search = document.querySelector("#main input[type='search'], #main input[data-search]"); if (search) { event.preventDefault(); search.focus(); } return; }
  if (event.key === "?") { showShortcuts(); return; }
  if (chord === "g") {
    // A section's chord first (inside any app), then an app's key, which opens that app's home.
    const target = NAV.find((item) => item.key === event.key);
    const app = !target && APPS.find((item) => item.key === event.key && apps.describe(item.id).enabled !== false);
    if (target) { Router.go([target.id]); } else if (app) { apps.open(app.id); }
    chord = "";
    return;
  }
  if (event.key === "g") { chord = "g"; setTimeout(() => { chord = ""; }, 900); }
});
function showShortcuts() {
  const sections = visibleNav();
  const appRows = APPS.map((item) => [`g then ${item.key}`, `${item.name} app`]);
  const rows = [...appRows, ...sections.map((item) => [`g then ${item.key}`, item.label]), ["/", "Focus search"], ["↑ ↓", "Move through a list"], ["Esc", "Close panel"], ["?", "This help"]];
  modal({ title: "Keyboard shortcuts", body: h("table", { class: "table" }, h("tbody", {}, ...rows.map(([key, label]) => h("tr", {}, h("td", {}, h("span", { class: "kbd", text: key })), h("td", { text: label }))))), actions: [{ label: "Close", kind: "" }] });
}
document.addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
  const list = event.target.closest("[data-roving]");
  if (!list) return;
  const items = [...list.querySelectorAll("[data-roving-item]")];
  const index = items.indexOf(event.target);
  if (index === -1) return;
  event.preventDefault();
  const next = items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
  next.focus();
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  applyTheme(Aira.recall("theme", ""));
  $("themeToggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const effectiveDark = current ? current === "dark" : prefersDark;
    applyTheme(effectiveDark ? "light" : "dark");
  });
  $("shortcutsBtn").addEventListener("click", () => { closeMenus(); showShortcuts(); });
  $("menuBtn").addEventListener("click", () => setSidebarOpen(!$("sidebar").classList.contains("open")));
  document.addEventListener("click", (event) => { if (window.innerWidth <= 900 && $("sidebar").classList.contains("open") && !event.target.closest("#sidebar, #menuBtn, #bottomNav, .popover")) setSidebarOpen(false); });
  $("logoutBtn").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST", body: {} }); } finally { location.hash = ""; location.reload(); } });
  // The app switcher opens from the top bar (desktop) or the More sheet (phones); the account menu from the avatar.
  Aira.switcherMenu = popover([$("appSwitcherBtn"), $("appSwitcherSideBtn")], $("appSwitcher"), { render: renderSwitcher, onClose: () => { if (window.innerWidth <= 900) setSidebarOpen(false); } });
  Aira.accountMenu = popover([$("accountBtn")], $("accountMenu"), { render: renderAccount });
  $("authSwitch").addEventListener("click", () => setAuthMode(Aira.state.authMode === "login" ? "register" : "login"));
  $("authForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = $("authSubmit");
    $("authNotice").textContent = Aira.state.authMode === "register" ? "Creating your workspace…" : "Signing in…";
    await withBusy(submit, async () => {
      try {
        await api(`/api/auth/${Aira.state.authMode}`, { method: "POST", body: { email: $("authEmail").value, password: $("authPassword").value } });
        $("authPassword").value = "";
        $("authNotice").textContent = "";
        await loadApp();
      } catch (error) { $("authNotice").textContent = error.message; }
    });
  });
  setAuthMode("login");
  loadApp();
});

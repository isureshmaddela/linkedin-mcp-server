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
  studio: '<svg viewBox="0 0 24 24"><path d="M4 20h16M6 16l9-9 3 3-9 9H6z"/><path d="m13 9 3 3"/></svg>',
  bookings: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="m9 15 2 2 4-4"/></svg>',
  shop: '<svg viewBox="0 0 24 24"><path d="M5 8h14l-1.2 11.1a1 1 0 0 1-1 .9H7.2a1 1 0 0 1-1-.9z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/></svg>',
  academy: '<svg viewBox="0 0 24 24"><path d="m2 9 10-5 10 5-10 5z"/><path d="M6 11v5c0 1.7 2.7 3 6 3s6-1.3 6-3v-5M22 9v6"/></svg>',
  events: '<svg viewBox="0 0 24 24"><path d="M4 8a2 2 0 0 0 2-2h12a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H6a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4z"/><path d="M14 6v12" stroke-dasharray="2 2"/></svg>',
  marketing: '<svg viewBox="0 0 24 24"><path d="M3 11v2a1 1 0 0 0 1 1h3l6 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M17 9a4 4 0 0 1 0 6M19.5 6.5a8 8 0 0 1 0 11"/></svg>',
  hub: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="m6.5 6.5 3.4 3.4M17.5 6.5l-3.4 3.4M6.5 17.5l3.4-3.4M17.5 17.5l-3.4-3.4"/></svg>',
  family: '<svg viewBox="0 0 24 24"><circle cx="8" cy="7" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M2.5 20c0-3.3 2.5-6 5.5-6s5.5 2.7 5.5 6M13 20c0-2.5 1.8-4.5 4-4.5s4 2 4 4.5"/></svg>',
  care: '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/><path d="M9 11h6M12 8v6"/></svg>',
  learn: '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2z"/><path d="M4 21V5M8 7h6M8 11h4"/></svg>',
  play: '<svg viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="11" rx="5"/><path d="M7 10.5v4M5 12.5h4"/><circle cx="16" cy="11" r="1"/><circle cx="18" cy="14" r="1"/></svg>',
  planner: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="17" rx="2"/><path d="M9 2v4M15 2v4M8 11l2 2 4-4M8 17h8"/></svg>',
  service: '<svg viewBox="0 0 24 24"><path d="M4 13a8 8 0 0 1 16 0"/><rect x="2.5" y="13" width="4" height="6" rx="1.5"/><rect x="17.5" y="13" width="4" height="6" rx="1.5"/><path d="M20 19c0 1.7-2 2.5-5 2.5"/></svg>',
  social: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.6A8 8 0 1 1 21 12z"/><circle cx="9" cy="12" r="1"/><circle cx="12.5" cy="12" r="1"/><circle cx="16" cy="12" r="1"/></svg>',
  commerce: '<svg viewBox="0 0 24 24"><path d="M5 8h14l-1.2 11.1a1 1 0 0 1-1 .9H7.2a1 1 0 0 1-1-.9z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/><path d="m9.5 14 2 2 3.5-4"/></svg>',
  experiences: '<svg viewBox="0 0 24 24"><path d="m2 8 8-4 8 4-8 4z"/><path d="M5 10v4c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5v-4"/><path d="M15 15h5a2 2 0 0 0 2-2v-1a2 2 0 0 1 0-4V7"/></svg>',
  news: '<svg viewBox="0 0 24 24"><path d="M4 5h13v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M17 9h3v10a2 2 0 0 1-2 2"/><path d="M7 9h7M7 13h7M7 17h4"/></svg>',
  videos: '<svg viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/></svg>',
  ads: '<svg viewBox="0 0 24 24"><path d="M3 10v4a1 1 0 0 0 1 1h2l5 4V5L6 9H4a1 1 0 0 0-1 1z"/><path d="M15 9.5a3.5 3.5 0 0 1 0 5"/><circle cx="19" cy="7" r="2.2"/><path d="M19 6.2v1.6M18.2 7h1.6"/></svg>',
  journey: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/><path d="m15.5 8.5 4-4M17 4.5h2.5V7"/></svg>',
  ai: '<svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 7V4M9 3.5h6"/><circle cx="9" cy="12.5" r="1.2"/><circle cx="15" cy="12.5" r="1.2"/><path d="M9.5 16h5M2 12v3M22 12v3"/></svg>',
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/></svg>',
  stocks: '<svg viewBox="0 0 24 24"><path d="M3 20h18"/><path d="m4 15 5-5 4 4 7-8"/><path d="M15 6h5v5"/></svg>',
  today: '<svg viewBox="0 0 24 24"><path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/></svg>',
  speaker: '<svg viewBox="0 0 24 24"><path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/></svg>',
};
Aira.ICONS = ICONS; // shell.js draws the tab icon from them
Aira.icon = (name) => h("span", { html: ICONS[name] || "", "aria-hidden": "true", style: { display: "contents" } });

// Every password input gets a show/hide button, including ones screens add later. Submitting the form hides it again.
const EYE_ICONS = '<svg class="pw-on" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>'
  + '<svg class="pw-off" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
function setPasswordShown(input, button, shown) {
  input.type = shown ? "text" : "password";
  button.setAttribute("aria-pressed", String(shown));
  button.setAttribute("aria-label", shown ? "Hide password" : "Show password");
  button.title = shown ? "Hide password" : "Show password";
}
function addPasswordToggle(input) {
  if (input.dataset.pwToggle || !input.parentNode) return;
  input.dataset.pwToggle = "1";
  if (!input.id) input.id = `pw-${Math.random().toString(36).slice(2, 10)}`;
  const button = h("button", { type: "button", class: "pw-toggle", "aria-controls": input.id, html: EYE_ICONS });
  setPasswordShown(input, button, false);
  button.addEventListener("click", () => { setPasswordShown(input, button, input.type === "password"); input.focus(); });
  const wrap = h("span", { class: "pw-wrap" });
  input.parentNode.insertBefore(wrap, input);
  wrap.append(input, button);
  if (input.form) input.form.addEventListener("submit", () => setPasswordShown(input, button, false), true);
}
function enhancePasswordInputs(root) {
  if (root.nodeType !== 1) return;
  if (root.matches('input[type="password"]')) addPasswordToggle(root);
  root.querySelectorAll('input[type="password"]').forEach(addPasswordToggle);
}
Aira.enhancePasswordInputs = enhancePasswordInputs;
document.addEventListener("DOMContentLoaded", () => {
  enhancePasswordInputs(document.body);
  new MutationObserver((records) => { for (const record of records) record.addedNodes.forEach(enhancePasswordInputs); }).observe(document.body, { childList: true, subtree: true });
});

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
      // The life apps name the person using a shared device (life.js keeps it; the hosted build ignores it).
      headers: { "Content-Type": "application/json", "X-Requested-With": "aira", Accept: raw ? "*/*" : "application/json", ...(Aira.state.actingFor ? { "X-Aira-Candidate": Aira.state.actingFor.id } : {}), ...(Aira.state.lifeProfile ? { "X-Aira-Profile": Aira.state.lifeProfile } : {}) },
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
    if (!response.ok && Aira.onApiError) Aira.onApiError(response.status, data, path);
    if (response.status === 401 && !path.startsWith("/api/auth/") && !path.startsWith("/api/platform/auth/") && !path.startsWith("/api/invites/") && !path.startsWith("/api/cloud/")) {
      Aira.showAuth();
      throw new ApiError("Your session ended. Sign in again.", 401, requestId);
    }
    if (!response.ok) throw new ApiError(data.error || `Request failed (${response.status})`, response.status, requestId, data.code || "", data);
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Monosage is unreachable. Check that the server is running.", 0, "");
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
  // A job source id as people read it: aira_jobs is the free job boards, shown as "Jobs".
  source: (id) => ({ aira_jobs: "Jobs" })[id] || String(id || "").replaceAll("_", " "),
  date: (value) => {
    if (!value) return "—";
    // A calendar day ("2026-11-20") is that day wherever you are: new Date() would read it as UTC midnight,
    // which is the evening before west of Greenwich.
    const day = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.split("-").map(Number) : null;
    const date = day ? new Date(day[0], day[1] - 1, day[2]) : typeof value === "number" ? new Date(value * 1000) : new Date(value);
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

// A server answer that an app is not on here (the organization has not turned it on, or it is switched off for
// everyone): views show the app's closed page instead of an error.
const APP_CLOSED_CODES = new Set(["app_required", "app_disabled"]);
const ui = {
  stage: (stage) => h("span", { class: `badge stage ${stage || "discovered"}`, text: fmt.stage(stage) }),
  source: (source) => h("span", { class: "badge source", text: (source || "").replaceAll("_", " ") }),
  classification: (label) => (label ? h("span", { class: `badge class-${label.split(" ")[0]}`, text: label }) : null),
  score: (value) => {
    if (value === null || value === undefined) return h("span", { class: "score none", title: "Not scored yet" }, h("span", { text: "—" }));
    return h("span", { class: "score", style: { "--pct": Math.round(value) }, title: `Match score ${Math.round(value)}%` }, h("span", { text: Math.round(value) }));
  },
  empty: (title, text, action) => h("div", { class: "empty" }, h("strong", { text: title }), text ? h("div", { text }) : null, action || null),
  error: (error, retry) => (error && APP_CLOSED_CODES.has(error.code) && Aira.apps ? Aira.apps.lockedFromError(error) : h("div", { class: "error-box", role: "alert" }, h("strong", { text: "Something went wrong" }), h("div", { text: error && error.message ? error.message : String(error) }), error && error.requestId ? h("div", { class: "muted", text: `Request ID ${error.requestId}` }) : null, retry ? h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary sm", text: "Try again", onclick: retry })) : null)),
  skeleton: (rows = 4) => h("div", { class: "skeleton-list", "aria-busy": "true", "aria-label": "Loading" }, ...Array.from({ length: rows }, () => h("div", { class: "skeleton" }))),
  field: (label, input, hint) => { const id = input.id || (input.id = `f${Math.random().toString(36).slice(2, 8)}`); return h("div", { class: "field" }, h("label", { for: id, text: label }), input, hint ? h("span", { class: "hint", text: hint }) : null); },
  select: (options, value, attrs = {}) => { const select = h("select", attrs); for (const option of options) { const [val, label] = Array.isArray(option) ? option : [option, option]; select.append(h("option", { value: val, text: label, selected: val === value })); } return select; },
  kv: (pairs) => h("dl", { class: "kv" }, ...pairs.filter(([, v]) => v !== undefined && v !== null && v !== "").flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : String(v))])),
  tabs: (items, active, onChange) => {
    const bar = h("div", { class: "tabs", role: "tablist" });
    for (const item of items) {
      bar.append(h("button", { type: "button", role: "tab", "aria-selected": item.id === active ? "true" : "false", text: item.label, onclick: () => onChange(item.id), onkeydown: (event) => { const buttons = [...bar.querySelectorAll("button")]; const index = buttons.indexOf(event.currentTarget); if (event.key === "ArrowRight") buttons[(index + 1) % buttons.length].focus(); if (event.key === "ArrowLeft") buttons[(index - 1 + buttons.length) % buttons.length].focus(); } }));
    }
    // On a phone the bar scrolls sideways: keep the open tab in sight (scrollLeft, so the page itself stays put).
    requestAnimationFrame(() => {
      const open = bar.querySelector('[aria-selected="true"]');
      if (open && bar.scrollWidth > bar.clientWidth) bar.scrollLeft = Math.max(0, open.offsetLeft - (bar.clientWidth - open.offsetWidth) / 2);
    });
    return bar;
  },
};
Aira.ui = ui;

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
// One account, many apps (docs/apps.md). APPS mirrors aira/apps.py: the switcher lists them, `g` then the app's
// key opens its home, and the sidebar shows only the current app's sections. `path` is the app's own address
// (/stocks, like mail.google.com; shell.js). GET /api/apps says which of them
// this account may open (the hosted build sells each one as a subscription).
const APPS = [
  { id: "careers", name: "Careers", tagline: "Find, apply and track", icon: "careers", home: "dashboard", path: "careers", key: "c" },
  { id: "api", name: "Developers", tagline: "APIs, keys and connected apps", icon: "airaapi", home: "airaapi", path: "developers", key: "p", scope: "org" },
  // Business apps (aira/suite), like the clouds of a CRM suite: data shared by the organization; one app can hold
  // several sections (Commerce is Shop and Bookings). `g` then a digit opens one.
  { id: "studio", name: "Sales", tagline: "Clients, deals, proposals and invoices", icon: "studio", home: "studio", path: "sales", key: "1", category: "industry", scope: "org" },
  { id: "marketing", name: "Marketing", tagline: "Leads, content, campaigns and forms", icon: "marketing", home: "marketing", path: "marketing", key: "2", category: "industry", scope: "org" },
  { id: "service", name: "Service", tagline: "Help your customers fast", icon: "service", home: "service", path: "service", key: "3", category: "industry", scope: "org" },
  { id: "commerce", name: "Commerce", tagline: "Sell products and appointments", icon: "commerce", home: "shop", path: "commerce", key: "4", category: "industry", scope: "org" },
  { id: "experiences", name: "Courses & Events", tagline: "Teach, sell tickets and check in", icon: "experiences", home: "academy", path: "courses", key: "5", category: "industry", scope: "org" },
  { id: "ads", name: "Ads", tagline: "Advertise on videos, pay creators", icon: "ads", home: "ads", path: "ads", category: "industry", scope: "org" },
  // Everyday apps (aira/suite/life.py): every person, each with their own profile. Social holds Family too.
  { id: "social", name: "Social", tagline: "Friends, family, chats and photos", icon: "social", home: "social", path: "social", key: "6", category: "life", scope: "org" },
  { id: "care", name: "Health", tagline: "Medicines, check-ups and wellbeing", icon: "care", home: "care", path: "health", key: "7", category: "life", scope: "org" },
  { id: "learn", name: "Learn", tagline: "Practise a little every day", icon: "learn", home: "learn", path: "learn", key: "8", category: "life", scope: "org" },
  { id: "play", name: "Play", tagline: "Games, quizzes and things to watch", icon: "play", home: "play", path: "play", key: "9", category: "life", scope: "org" },
  { id: "planner", name: "Planner", tagline: "Chores, shopping and bills", icon: "planner", home: "planner", path: "planner", category: "life", scope: "org" },
  { id: "news", name: "News", tagline: "Today's headlines in your language", icon: "news", home: "news", path: "news", category: "life", scope: "org" },
  { id: "videos", name: "Videos", tagline: "Watch, upload and earn", icon: "videos", home: "videos", path: "videos", category: "life", scope: "org" },
  { id: "journey", name: "Life", tagline: "Goals, milestones and documents", icon: "journey", home: "journey", path: "life", category: "life", scope: "org" },
  { id: "ai", name: "AI", tagline: "Chat with Monosage and manage its agents", icon: "ai", home: "ai", path: "ai", category: "life", scope: "org" },
  { id: "stocks", name: "Stocks", tagline: "Markets, predictions and your portfolio", icon: "stocks", home: "stocks", path: "stocks", category: "life", scope: "org" },
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
  { id: "airaapi", label: "API", icon: "airaapi", app: "api", group: "Developers", key: "p", quick: true },
  // Third-party access: OAuth apps, access tokens and webhooks (views-developers.js).
  { id: "developers", label: "Connected apps", icon: "settings", app: "api", group: "Developers" },
  { id: "studio", label: "Sales", icon: "studio", app: "studio", group: "Sales", quick: true },
  { id: "marketing", label: "Marketing", icon: "marketing", app: "marketing", group: "Marketing", quick: true },
  { id: "leads", label: "Leads", icon: "leads", app: "marketing", group: "Marketing", key: "l", quick: true },
  { id: "service", label: "Service", icon: "service", app: "service", group: "Service", quick: true },
  { id: "shop", label: "Shop", icon: "shop", app: "commerce", group: "Commerce", quick: true },
  { id: "bookings", label: "Bookings", icon: "bookings", app: "commerce", group: "Commerce", quick: true },
  { id: "academy", label: "Courses", icon: "academy", app: "experiences", group: "Courses & Events", quick: true },
  { id: "events", label: "Events", icon: "events", app: "experiences", group: "Courses & Events", quick: true },
  { id: "ads", label: "Ads", icon: "ads", app: "ads", group: "Ads", quick: true },
  { id: "social", label: "Social", icon: "social", app: "social", group: "Social", quick: true },
  { id: "family", label: "Family", icon: "family", app: "social", group: "Social", quick: true },
  { id: "care", label: "Health", icon: "care", app: "care", group: "Health", quick: true },
  { id: "learn", label: "Learn", icon: "learn", app: "learn", group: "Learn", quick: true },
  { id: "play", label: "Play", icon: "play", app: "play", group: "Play", quick: true },
  { id: "planner", label: "Planner", icon: "planner", app: "planner", group: "Planner", quick: true },
  { id: "news", label: "News", icon: "news", app: "news", group: "News", quick: true },
  { id: "videos", label: "Videos", icon: "videos", app: "videos", group: "Videos", quick: true },
  { id: "journey", label: "Life", icon: "journey", app: "journey", group: "Life", quick: true },
  { id: "ai", label: "AI", icon: "ai", app: "ai", group: "AI", quick: true },
  { id: "stocks", label: "Stocks", icon: "stocks", app: "stocks", group: "Stocks", quick: true },
  // Today is each person's day across the life apps: medicines due, chores, events, messages, birthdays.
  { id: "today", label: "Today", icon: "today", app: "account", group: "Account" },
  { id: "display", label: "Display and reading", icon: "speaker", app: "account", group: "Account" },
  // The Business hub adds up every industry app (revenue, customers' activity, setup, the shared email outbox).
  { id: "hub", label: "Business hub", icon: "hub", app: "account", group: "Account", key: "0" },
  { id: "settings", label: "Settings", icon: "settings", app: "account", group: "Account", key: "s" },
];
Aira.NAV = NAV; // the hosted build replaces the entries by role
// Every product section and its app, kept before a build narrows NAV to what one person has, so a link to a section
// of an app that is not on here (#/academy, #/social) still knows which app it belongs to.
const SECTIONS = NAV.filter((item) => APPS.some((entry) => entry.id === item.app)).map((item) => ({ ...item }));
Aira.APPS = APPS;

// ---------------------------------------------------------------------------
// Apps: the switcher, the current app, the pseudo-apps a build adds (Account, Coaching, Admin console)
// ---------------------------------------------------------------------------
const apps = (Aira.apps = {
  catalog: APPS,
  // Apps a build adds for sections that are not products: the hosted Account (organization) pages, Coaching, the Admin console.
  pseudo: {},
  state: { enabled: {}, reasons: {}, switchedOff: {}, accountUrl: "", localUrl: "", installation: "local", loaded: false, extra: [] },
  decorate: null, // a build may adjust the list the switcher shows: (entries) => entries
  async load() {
    try {
      const data = await api("/api/apps", { silent: true });
      for (const item of data.apps || []) {
        apps.state.enabled[item.id] = item.enabled !== false;
        apps.state.reasons[item.id] = item.reason || "";
        apps.state.switchedOff[item.id] = !!item.switched_off;
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
    else if (section === "today" && apps.pseudo.home) Aira.state.currentApp = "home";
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
  sectionApp: (section) => (SECTIONS.find((item) => item.id === section) || {}).app || null,
  // Why a section cannot open here, or null: its app is not on for the organization (``off``), switched off for
  // everyone (``disabled``), or, by a build's rule, not part of this person's role (``role``).
  roleBlock: null,
  blocked(section) {
    const id = apps.sectionApp(section);
    if (!id) return null;
    if (apps.state.loaded && apps.state.enabled[id] === false) return { app: id, kind: apps.state.switchedOff[id] ? "disabled" : "off", reason: apps.state.reasons[id] || "" };
    return apps.roleBlock ? apps.roleBlock(section, id) : null;
  },
  // What this person can do about a closed app: a build returns { text, action: { label, href } } or null.
  remedy: null,
  locked({ app: id, kind, reason }) {
    const app = apps.describe(id);
    const title = kind === "role" ? `${app.name} is not part of your role` : kind === "disabled" ? `${app.name} is switched off for now` : `${app.name} is not turned on`;
    const fallback = kind === "role" ? "Your role does not include this app." : kind === "disabled" ? "Nobody can open it until it is switched back on. Your data is kept." : "Your organization has not turned this app on yet.";
    const remedy = apps.remedy ? apps.remedy(id, kind) : null;
    const home = (Aira.startPage && Aira.startPage(false)) || "";
    return h("section", { class: "card app-locked", "data-app": id, "data-kind": kind, role: "status" },
      h("div", { class: "app-locked-icon" }, Aira.icon(app.icon || "dashboard")),
      h("h1", { text: title }),
      app.tagline ? h("p", { class: "muted", text: app.tagline }) : null,
      h("p", { text: reason || fallback }),
      remedy && remedy.text ? h("p", { class: "muted", text: remedy.text }) : null,
      h("div", { class: "btn-row" },
        remedy && remedy.action ? h("a", { class: "btn", href: remedy.action.href, text: remedy.action.label }) : null,
        h("a", { class: `btn ${remedy && remedy.action ? "secondary" : ""}`, href: `#/${home}`, text: "Go to Home" })));
  },
  lockedFromError(error) {
    const data = error.data || {};
    const section = Router.current && Router.current.path[0];
    const id = data.app || apps.sectionApp(section) || "";
    return apps.locked({ app: id, kind: error.code === "app_disabled" ? "disabled" : "off", reason: error.message });
  },
});
// Phones open the sidebar as a sheet (More in the bottom bar); this keeps the trigger and the sheet in step.
function setSidebarOpen(open) {
  $("sidebar").classList.toggle("open", open);
  const more = document.querySelector('#bottomNav a[data-section="more"]');
  if (more) more.setAttribute("aria-expanded", String(open));
}
Aira.setSidebarOpen = setSidebarOpen;
const Router = {
  parse() {
    const hash = location.hash.replace(/^#\/?/, "");
    const [pathPart, queryPart = ""] = hash.split("?");
    const path = pathPart.split("/").filter(Boolean).map(decodeURIComponent);
    // The address reads #/api; the page's id inside the code is still "airaapi" (old #/airaapi links keep working).
    if (path[0] === "api") path[0] = "airaapi";
    const query = Object.fromEntries(new URLSearchParams(queryPart));
    return { path, query };
  },
  go(path, query = {}, { replace = false } = {}) {
    const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => v !== "" && v !== null && v !== undefined));
    const qs = new URLSearchParams(clean).toString();
    const target = `#/${path.map(encodeURIComponent).join("/")}${qs ? `?${qs}` : ""}`;
    if (replace) history.replaceState(null, "", target);
    else if (location.hash === target) { Router.render(); return; } // a link to where you are draws the page again
    else location.hash = target;
    if (replace) Router.render();
  },
  current: null,
  renders: 0, // counts page draws: polling a page started (Aira.suite.live) stops when the page is drawn again
  async render() {
    Router.renders += 1;
    const route = Router.parse();
    if (!route.path.length) { Router.go([(Aira.startPage && Aira.startPage(false)) || "dashboard"], {}, { replace: true }); return; }
    const section = route.path[0];
    if (section === "airaapi" && /^#\/?airaapi\b/.test(location.hash)) history.replaceState(history.state, "", location.hash.replace(/^#\/?airaapi\b/, "#/api"));
    const view = Aira.views[section];
    const main = $("main");
    Router.current = route;
    // A drawer belongs to the section it was opened from; Back, a link inside it, or a chord must not leave it over the next page.
    if (Aira.activeDrawer) Aira.activeDrawer.close(false);
    // Moving into another app redraws the sidebar for that app; inside an app only the current mark moves.
    const before = Aira.state.currentApp;
    const app = apps.current();
    if (app.id !== before || !document.querySelector(`#primaryNav a[data-section="${CSS.escape(section)}"]`)) renderNav();
    if (Aira.shell) Aira.shell.update(app);
    for (const link of document.querySelectorAll("#primaryNav a, #bottomNav a")) link.setAttribute("aria-current", link.dataset.section === section ? "page" : "false");
    // A section that is not in the bottom bar lives behind More, so More shows as the current place.
    const more = document.querySelector('#bottomNav a[data-section="more"]');
    if (more && NAV.some((item) => item.id === section && !item.quick)) more.setAttribute("aria-current", "page");
    setSidebarOpen(false);
    if (!view) { main.replaceChildren(ui.empty("Page not found", "Choose a section from the navigation.")); return; }
    // A build may hold a page back for this person (the Business hub for a candidate, a candidate's workspace page for
    // staff who have not opened one): it returns what to show instead, or null.
    const gate = Aira.sectionGate ? Aira.sectionGate(section) : null;
    if (gate) {
      $("crumbs").replaceChildren(h("span", { text: (NAV.find((item) => item.id === section) || SECTIONS.find((item) => item.id === section) || {}).label || "Not available" }));
      main.replaceChildren(h("div", { class: "view-stage" }, gate));
      return;
    }
    const blocked = apps.blocked(section);
    if (blocked) {
      const closed = apps.describe(blocked.app);
      $("crumbs").replaceChildren(h("span", { class: "crumb-app", text: closed.name }));
      document.title = `${closed.name} · Monosage`;
      main.replaceChildren(h("div", { class: "view-stage" }, apps.locked(blocked)));
      return;
    }
    const navItem = NAV.find((item) => item.id === section);
    const label = navItem ? navItem.label : section;
    const inProduct = APPS.some((entry) => entry.id === app.id) && navItem && navItem.group !== "Account";
    $("crumbs").replaceChildren(...(inProduct ? [h("span", { class: "crumb-app", text: app.name }), h("span", { class: "crumb-sep", "aria-hidden": "true", text: "/" })] : []), h("span", { text: label }));
    document.title = `${label} · ${inProduct ? `${app.name} · ` : ""}Monosage`;
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
  const menuItems = items.filter((item) => item.group !== "Account");
  $("brandApp").textContent = app.name || "Monosage";
  const link = (item) => h("a", { href: `#/${item.id}`, dataset: { section: item.id }, "aria-current": current === item.id ? "page" : "false", "aria-keyshortcuts": `g ${item.key}`, onclick: () => { if (window.innerWidth <= 900) setSidebarOpen(false); } }, Aira.icon(item.icon), h("span", { text: item.label }), shown[item.id] ? h("span", { class: "count", text: shown[item.id] }) : null);
  // The app's own groups first, the Account group (shown under every app) last.
  const groups = [...new Set(menuItems.map((item) => item.group))];
  const head = h("div", { class: "nav-apphead", id: "navAppHead" }, Aira.icon(app.icon || "dashboard"), h("div", {}, h("strong", { text: app.name || "Monosage" }), app.tagline ? h("span", { class: "muted", text: app.tagline }) : null));
  const extra = Aira.shell ? Aira.shell.navExtras(app) : { before: [], after: [] };
  $("primaryNav").replaceChildren(head, ...extra.before, ...groups.map((group) => {
    const labelId = `navGroup${group.replace(/\W+/g, "")}`;
    return h("div", { class: "nav-group", role: "group", "aria-labelledby": labelId }, h("span", { class: "nav-label", id: labelId, text: group }), ...menuItems.filter((item) => item.group === group).map(link));
  }), ...extra.after);
  if (Aira.shell) Aira.shell.afterNav();
  const bottom = $("bottomNav");
  const more = h("a", { href: "#", dataset: { section: "more" }, role: "button", "aria-controls": "sidebar", "aria-expanded": String($("sidebar").classList.contains("open")), "aria-current": current && items.some((item) => item.id === current && !item.quick) ? "page" : "false", onclick: (event) => { event.preventDefault(); setSidebarOpen(!$("sidebar").classList.contains("open")); } }, Aira.icon("more"), h("span", { text: "More" }));
  const hidden = items.filter((item) => !item.quick).reduce((sum, item) => sum + (Number(shown[item.id]) || 0), 0);
  if (hidden) more.append(h("span", { class: "dot", role: "img", "aria-label": `${hidden} waiting in More` }));
  // A build may choose the phone bar's items (life.js: Home and the everyday apps on Today and in those apps).
  const quickItems = (Aira.quickItems && Aira.quickItems(items, current)) || items.filter((item) => item.quick).slice(0, 4);
  bottom.replaceChildren(...quickItems.map((item) => h("a", { href: `#/${item.id}`, dataset: { section: item.id }, "aria-current": current === item.id ? "page" : "false" }, Aira.icon(item.icon), h("span", { text: item.label }))), more);
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
  const own = !entry.href && entry.path && !disabled ? `/${entry.path}#/${apps.homeOf(entry.id)}` : "";
  const node = entry.href
    ? h("a", { ...attrs, href: entry.href, ...(entry.external ? { target: "_blank", rel: "noopener noreferrer" } : {}) })
    : own
      ? h("a", { ...attrs, href: own, onclick: (event) => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return; event.preventDefault(); closeMenus(); apps.open(entry.id); } })
      : h("button", { ...attrs, type: "button", onclick: (event) => { event.preventDefault(); if (disabled) return; closeMenus(); apps.open(entry.id); } });
  node.append(h("span", { class: "app-tile-icon" }, Aira.icon(entry.icon || "dashboard")), h("span", { class: "app-tile-name", text: entry.name }), h("span", { class: "app-tile-tag", text: disabled && entry.reason ? entry.reason : entry.tagline || "" }));
  return node;
}
function renderSwitcher(panel) {
  // Only the apps this account can open: an app it has no access to is not shown (an admin finds it under More apps).
  const entries = apps.available().filter((entry) => entry.enabled !== false || entry.kind !== "product");
  const current = apps.current();
  const tile = (entry) => { const node = appTile(entry); if (entry.id === current.id) node.setAttribute("aria-current", "true"); return node; };
  const core = entries.filter((entry) => entry.category !== "industry" && entry.category !== "life");
  const everyday = entries.filter((entry) => entry.category === "life");
  const industry = entries.filter((entry) => entry.category === "industry");
  const links = [];
  const local = apps.state.installation === "local";
  if (local && apps.state.accountUrl) links.push(h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: `${apps.state.accountUrl}/#/org`, target: "_blank", rel: "noopener noreferrer" }, Aira.icon("recruiters"), h("span", { text: "Your organization online" })));
  if (!local && apps.state.localUrl) links.push(h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: apps.state.localUrl, target: "_blank", rel: "noopener noreferrer" }, Aira.icon("dashboard"), h("span", { text: "Local workspace" })));
  const can = Aira.cloud && Aira.cloud.can;
  if (!local && can && (can("billing.manage") || can("org.settings"))) links.push(h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: "#/org/apps" }, Aira.icon("more"), h("span", { text: "More apps" })));
  // replaceChildren turns a null child into the text "null": only real nodes go in.
  panel.replaceChildren(
    ...[
      h("div", { class: "popover-title", text: "Apps" }),
      Aira.shell ? Aira.shell.homeTile() : null,
      core.length ? h("div", { class: "app-grid" }, ...core.map(tile)) : null,
      everyday.length ? h("div", { class: "popover-subtitle", text: "Everyday" }) : null,
      everyday.length ? h("div", { class: "app-grid" }, ...everyday.map(tile)) : null,
      industry.length ? h("div", { class: "popover-subtitle", text: "Business apps" }) : null,
      industry.length ? h("div", { class: "app-grid" }, ...industry.map(tile)) : null,
      !entries.length ? h("p", { class: "muted", text: "No apps are turned on for your account yet." }) : null,
      links.length ? h("div", { class: "menu-list" }, ...links) : null,
    ].filter(Boolean),
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
  // Everything about the account is here, like a Google account menu: the left menu keeps only the open app.
  const pages = visibleNav().filter((item) => item.group === "Account");
  const settings = pages.find((item) => item.id === "settings");
  if (me && me.kind === "member" && cloud.isStaff && cloud.isStaff()) links.push({ label: "Organization settings", href: "#/org", icon: "recruiters" });
  for (const item of pages) if (item !== settings) links.push({ label: item.label, href: `#/${item.id}`, icon: item.icon, section: item.id });
  if (me && me.kind === "member" && Array.isArray(me.orgs) && me.orgs.length > 1) links.push({ label: "Switch organization", href: "#/switch-org", icon: "recruiters" });
  // A build's own entries (Display and reading from life.js) once each; the online account stays in the launcher.
  for (const extra of Aira.accountLinks) if ((!extra.when || extra.when(user)) && !links.some((entry) => entry.href === extra.href)) links.push(extra);
  const current = Router.current && Router.current.path[0];
  const item = (entry) => h("a", { class: "menu-item", role: "menuitem", tabindex: "-1", href: entry.href, ...(entry.section && entry.section === current ? { "aria-current": "page" } : {}), ...(entry.external ? { target: "_blank", rel: "noopener noreferrer" } : {}) }, Aira.icon(entry.icon || "settings"), h("span", { text: entry.label }));
  const manage = h("a", { class: "btn secondary account-manage", role: "menuitem", tabindex: "-1", href: settings ? "#/settings" : "#/security", dataset: { section: settings ? "settings" : "security" } }, h("span", { text: "Manage your account" }));
  $("accountLinks").replaceChildren(manage, ...links.map(item));
  $("accountLinks").hidden = false;
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
      pill(status.google_jobs, status.google_jobs ? "SerpApi ready" : "SerpApi off", status.google_jobs ? "A SerpApi key is connected: Google Jobs, Careers and the API console can search" : "Connect a SerpApi key in API > Account to use Google Jobs, Careers and the API console", false, "#/api?tab=account"),
    );
    if (linkedin.state === "login" || linkedin.state === "starting") setTimeout(refreshStatus, 3000);
  } catch { /* status is best-effort */ }
}
Aira.refreshStatus = refreshStatus;

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute("data-theme", theme); else document.documentElement.removeAttribute("data-theme");
  Aira.remember("theme", theme || "");
  paintThemeButton();
}
// The button shows where a click takes you: a moon in the light theme, a sun in the dark one.
function paintThemeButton() {
  const button = $("themeToggle");
  if (!button) return;
  const current = document.documentElement.getAttribute("data-theme");
  const dark = current ? current === "dark" : darkQuery.matches;
  const label = dark ? "Switch to the light theme" : "Switch to the dark theme";
  button.setAttribute("aria-label", label);
  button.title = dark ? "Light theme" : "Dark theme";
  button.setAttribute("aria-pressed", String(dark));
  button.dataset.mode = dark ? "dark" : "light";
}
darkQuery.addEventListener("change", paintThemeButton);

// Sign-in modes: login, register, forgot (ask for a reset code) and reset (code + new password).
const AUTH_MODES = {
  login: { title: "Sign in", submit: "Sign in", switchText: "New to Monosage?", switchLabel: "Create an account", password: "Monosage password", autocomplete: "current-password", hint: "" },
  register: { title: "Create your workspace", submit: "Create account", switchText: "Already registered?", switchLabel: "Sign in", password: "Monosage password", autocomplete: "new-password", hint: "Use at least 12 characters. This password protects your local workspace." },
  forgot: { title: "Reset your password", submit: "Get a reset code", switchText: "Remembered it?", switchLabel: "Sign in", intro: "Enter your account email. Monosage emails you a one-time code when a mail server is set up, and always shows it in the window where Monosage is running." },
  reset: { title: "Choose a new password", submit: "Set new password", switchText: "Remembered it?", switchLabel: "Sign in", password: "New Monosage password", autocomplete: "new-password", hint: "At least 12 characters. Every other signed-in session is signed out.", intro: "Enter the code from your email or the window where Monosage is running, then a new password." },
};
const AUTH_INTRO = "Your workspace stays on this computer. LinkedIn sign-in happens separately in your own browser session.";
function setAuthMode(mode) {
  Aira.state.authMode = mode;
  const config = AUTH_MODES[mode];
  $("authTitle").textContent = config.title;
  $("authIntro").textContent = config.intro || AUTH_INTRO;
  $("authSubmit").textContent = config.submit;
  $("authSwitchText").textContent = config.switchText;
  $("authSwitch").textContent = config.switchLabel;
  $("authPasswordField").hidden = mode === "forgot";
  if (config.password) $("authPasswordLabel").textContent = config.password;
  if (config.autocomplete) $("authPassword").autocomplete = config.autocomplete;
  $("authHint").textContent = config.hint || "";
  $("authCodeField").hidden = mode !== "reset";
  $("authForgotRow").hidden = mode !== "login";
  $("authNotice").textContent = "";
}
Aira.showAuth = () => { $("boot").hidden = true; $("app").hidden = true; $("authScreen").hidden = false; $("authEmail").focus(); };

async function loadApp() {
  // The person's language first, so the first screen is already in it (i18n.js).
  if (Aira.i18n) { try { await Aira.i18n.ready; } catch { /* English */ } }
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
  apps.load().then(() => {
    // Until the list arrives every app counts as on; redraw the sidebar only when it turns some off, so a click on a
    // section link at that moment is not lost on a link that is being replaced.
    if (Object.values(apps.state.enabled).some((on) => on === false)) {
      if (Aira.cloud && Aira.cloud.buildNav) Aira.cloud.buildNav(); else renderNav();
    }
    renderAccount();
    const section = Router.current && Router.current.path[0];
    if (section && apps.blocked(section)) Router.render(); // a link to an app that is not on here
  });
  if (workspace) await loadWorkspace();
  // life.js: who is using a shared device, their display preferences, live reminders.
  if (Aira.afterSignIn) { try { await Aira.afterSignIn(Aira.state.user); } catch (error) { console.warn("afterSignIn", error); } }
  const preferred = Aira.startPage ? Aira.startPage(true) : null; // the person's own start page (Today in easy mode)
  const landing = (Aira.cloud && Aira.cloud.landing ? Aira.cloud.landing(workspace) : null) || (preferred ? [preferred] : null);
  const addressed = Aira.shell ? Aira.shell.fromAddress() : null; // /stocks opens Stocks, /home opens Today
  if (!location.hash || location.hash === "#/" || location.hash === "#") Router.go(addressed || landing || (Aira.state.profile && Aira.state.profile.resume_hash ? ["dashboard"] : ["settings", "resume"]), {}, { replace: true });
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
  if (event.key === "/") { const search = $("appSearchInput") && $("appSearchInput").offsetParent ? $("appSearchInput") : document.querySelector("#main input[type='search'], #main input[data-search]"); if (search) { event.preventDefault(); search.focus(); } return; }
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
  document.addEventListener("click", (event) => { if (window.innerWidth <= 900 && $("sidebar").classList.contains("open") && !event.target.closest("#sidebar, #bottomNav, .popover")) setSidebarOpen(false); });
  $("logoutBtn").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST", body: {} }); } finally { location.hash = ""; location.reload(); } });
  // The app switcher opens from the top bar (desktop) or the More sheet (phones); the account menu from the avatar.
  Aira.switcherMenu = popover([$("appSwitcherBtn"), $("appSwitcherSideBtn")], $("appSwitcher"), { render: renderSwitcher, onClose: () => { if (window.innerWidth <= 900) setSidebarOpen(false); } });
  Aira.accountMenu = popover([$("accountBtn")], $("accountMenu"), { render: renderAccount });
  $("authSwitch").addEventListener("click", () => setAuthMode(Aira.state.authMode === "register" ? "login" : Aira.state.authMode === "login" ? "register" : "login"));
  $("authForgot").addEventListener("click", () => { setAuthMode("forgot"); $("authEmail").focus(); });
  $("authForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = Aira.state.authMode;
    const submit = $("authSubmit");
    const notice = $("authNotice");
    notice.textContent = { register: "Creating your workspace…", forgot: "Asking for a reset code…", reset: "Setting your new password…" }[mode] || "Signing in…";
    let codeSent = "";
    await withBusy(submit, async () => {
      try {
        if (mode === "forgot") {
          codeSent = (await api("/api/auth/password/forgot", { method: "POST", body: { email: $("authEmail").value }, silent: true })).message;
          return;
        }
        if (mode === "reset") {
          await api("/api/auth/password/reset", { method: "POST", body: { email: $("authEmail").value, code: $("authCode").value, password: $("authPassword").value }, silent: true });
          $("authCode").value = "";
          toast("Password updated. You are signed in.", "success");
        } else {
          await api(`/api/auth/${mode}`, { method: "POST", body: { email: $("authEmail").value, password: $("authPassword").value } });
        }
        $("authPassword").value = "";
        notice.textContent = "";
        setAuthMode("login");
        await loadApp();
      } catch (error) { notice.textContent = error.message; }
    });
    // withBusy puts the old button label back when it ends, so the next mode is set after it.
    if (codeSent) { setAuthMode("reset"); notice.textContent = codeSent; $("authCode").focus(); }
  });
  setAuthMode("login");
  loadApp();
});

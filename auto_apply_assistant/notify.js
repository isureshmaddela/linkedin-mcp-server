/* Notifications: the notification centre (#/notifications), what to be told and how, turning on notifications in
 * this browser (Web Push through the service worker, /sw.js) and installing Monosage as an app.
 *
 * The server stores every notification (aira/notify) and sends pushes itself, so reminders arrive with every tab
 * closed. Here: the list with its unread count, the choices per category (push, email), quiet hours, and the
 * buttons that ask the browser for permission and hand the server this browser's push subscription.
 */
"use strict";

(() => {
  const Aira = window.Aira;
  const { h } = Aira;
  const N = (Aira.notify = { unread: 0, pushOn: false, settings: null, installPrompt: null });
  const BASE = "/api/notify";
  let registration = null;
  let started = false;
  const redraws = new Set();

  Aira.ICONS.bell = '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>';
  const NAV_ITEM = { id: "notifications", label: "Notifications", icon: "bell", app: "account", group: "Account" };
  if (Aira.NAV && !Aira.NAV.some((item) => item.id === NAV_ITEM.id)) Aira.NAV.push(NAV_ITEM);
  // The hosted build draws its own menu: the account menu still lists the page (once: the menu skips a repeated link).
  if (Aira.accountLinks) Aira.accountLinks.push({ label: NAV_ITEM.label, href: `#/${NAV_ITEM.id}`, icon: NAV_ITEM.icon });

  const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const installed = () => (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
  const appleMobile = () => /iphone|ipad|ipod/i.test(navigator.userAgent || "");

  function redraw() { for (const fn of [...redraws]) { try { fn(); } catch (error) { console.error(error); } } }

  // ---------------------------------------------------------------------------
  // the service worker and the install prompt
  // ---------------------------------------------------------------------------
  async function register() {
    if (registration || !("serviceWorker" in navigator)) return registration;
    try { registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" }); } catch (error) { console.warn("service worker", error); registration = null; }
    return registration;
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      const data = event.data || {};
      if (data.type === "aira-open" && typeof data.url === "string") {
        const target = new URL(data.url, location.origin);
        if (target.origin !== location.origin) return;
        if (target.pathname !== location.pathname) location.assign(target.href);
        else if (target.hash && target.hash !== location.hash) location.hash = target.hash;
      } else if (data.type === "aira-push" && data.data) {
        // Monosage is in front: a toast instead of a system notification.
        Aira.toast([data.data.title, data.data.body].filter(Boolean).join(": "), "info");
        refreshCount();
      }
    });
  }
  window.addEventListener("beforeinstallprompt", (event) => { event.preventDefault(); N.installPrompt = event; redraw(); });
  window.addEventListener("appinstalled", () => { N.installPrompt = null; redraw(); });

  async function install() {
    if (!N.installPrompt) return;
    N.installPrompt.prompt();
    try { await N.installPrompt.userChoice; } catch { /* closed */ }
    N.installPrompt = null;
    redraw();
  }

  // ---------------------------------------------------------------------------
  // push in this browser
  // ---------------------------------------------------------------------------
  function keyBytes(base64url) {
    const padded = `${base64url}${"=".repeat((4 - (base64url.length % 4)) % 4)}`.replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(padded);
    return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
  }
  function deviceName() {
    const agent = navigator.userAgent || "";
    const browser = /Edg\//.test(agent) ? "Edge" : /Firefox\//.test(agent) ? "Firefox" : /Chrome\//.test(agent) ? "Chrome" : /Safari\//.test(agent) ? "Safari" : "Browser";
    const system = /Android/.test(agent) ? "Android" : /iPhone|iPad/.test(agent) ? "iOS" : /Windows/.test(agent) ? "Windows" : /Mac OS/.test(agent) ? "Mac" : /Linux/.test(agent) ? "Linux" : "";
    return system ? `${browser} on ${system}` : browser;
  }
  async function currentSubscription() {
    if (!pushSupported()) return null;
    const reg = await register();
    if (!reg) return null;
    try { return await reg.pushManager.getSubscription(); } catch { return null; }
  }
  async function checkPush() {
    const sub = await currentSubscription();
    N.pushOn = Boolean(sub) && Notification.permission === "granted";
    return N.pushOn;
  }

  async function turnOn() {
    if (!pushSupported()) throw new Error(appleMobile() && !installed() ? "On an iPhone or iPad, add Monosage to the Home Screen first (Share, then Add to Home Screen), then turn on notifications there." : "This browser cannot show notifications from Monosage.");
    const reg = await register();
    if (!reg) throw new Error("This browser did not let Monosage set up notifications.");
    await navigator.serviceWorker.ready;
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notifications are blocked for Monosage in this browser. Allow them in the browser's site settings, then try again.");
    const settings = N.settings || (await load());
    if (!settings.push.key) throw new Error("Monosage could not make its notification key. Check the server log.");
    const options = { userVisibleOnly: true, applicationServerKey: keyBytes(settings.push.key) };
    let sub;
    try { sub = await reg.pushManager.subscribe(options); }
    catch (error) {
      // a subscription made for an older key: replace it
      const old = await reg.pushManager.getSubscription();
      if (!old) throw error;
      await old.unsubscribe();
      sub = await reg.pushManager.subscribe(options);
    }
    const json = sub.toJSON();
    N.settings = await Aira.api(`${BASE}/push/subscribe`, { method: "POST", body: { endpoint: json.endpoint, keys: json.keys, device: deviceName() } });
    N.pushOn = true;
  }
  async function turnOff() {
    const sub = await currentSubscription();
    if (sub) {
      N.settings = await Aira.api(`${BASE}/push/unsubscribe`, { method: "POST", body: { endpoint: sub.endpoint } });
      try { await sub.unsubscribe(); } catch { /* already gone */ }
    }
    N.pushOn = false;
  }

  // ---------------------------------------------------------------------------
  // the unread count (a badge on the account button)
  // ---------------------------------------------------------------------------
  function drawBadge() {
    const button = document.getElementById("accountBtn");
    if (!button) return;
    let badge = document.getElementById("notifyBadge");
    if (!N.unread) { if (badge) badge.remove(); button.setAttribute("aria-label", "Account menu"); return; }
    if (!badge) {
      badge = h("span", { id: "notifyBadge", "aria-hidden": "true", style: { position: "absolute", top: "2px", left: "26px", minWidth: "18px", height: "18px", padding: "0 5px", borderRadius: "999px", background: "var(--danger, #c62828)", color: "#fff", font: "600 11px/18px system-ui, sans-serif", textAlign: "center", pointerEvents: "none" } });
      button.style.position = button.style.position || "relative";
      button.append(badge);
    }
    badge.textContent = N.unread > 99 ? "99+" : String(N.unread);
    button.setAttribute("aria-label", `Account menu, ${N.unread} unread notification${N.unread === 1 ? "" : "s"}`);
  }
  let counting = null;
  async function refreshCount() {
    if (counting) return counting;
    counting = Aira.api(`${BASE}/items?unread=1&limit=1`, { silent: true })
      .then((data) => { N.unread = data.unread || 0; drawBadge(); redraw(); })
      .catch(() => { /* signed out, or a profile to pick first */ })
      .finally(() => { counting = null; });
    return counting;
  }
  N.refreshCount = refreshCount;

  async function load() {
    N.settings = await Aira.api(`${BASE}/settings`, { silent: true });
    return N.settings;
  }

  function startOnce() {
    // Not in the operator console: an operator has no notification centre (only members do).
    if (started || !Aira.state.user || String(location.hash).startsWith("#/platform")) return;
    started = true;
    register().then(checkPush).then(redraw).catch(() => {});
    refreshCount();
    if (Aira.realtime) Aira.realtime.every(refreshCount, { topics: ["notify"], fallbackMs: 60000, liveMs: 300000, delayMs: 200 });
    else setInterval(refreshCount, 60000);
  }
  (Aira.afterRoute = Aira.afterRoute || []).push(() => startOnce());

  // ---------------------------------------------------------------------------
  // the controls (also on Settings): turn on notifications here, install the app
  // ---------------------------------------------------------------------------
  N.controls = ({ compact = false } = {}) => {
    const box = h("div", { class: "btn-row notify-controls" });
    const draw = () => {
      if (!box.isConnected && box.dataset.drawn) { redraws.delete(draw); return; }
      box.dataset.drawn = "1";
      const parts = [];
      if (pushSupported() || appleMobile()) {
        parts.push(N.pushOn
          ? h("button", { type: "button", class: "btn ghost sm", text: "Turn off notifications", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { await turnOff(); Aira.toast("Notifications are off in this browser.", "success"); redraw(); }) })
          : h("button", { type: "button", class: `btn ${compact ? "secondary sm" : "sm"}`, text: "Turn on notifications", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { await turnOn(); Aira.toast("Notifications are on in this browser.", "success"); redraw(); }) }));
        if (N.pushOn && !compact) parts.push(h("button", { type: "button", class: "btn ghost sm", text: "Send a test", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { await Aira.api(`${BASE}/push/test`, { method: "POST", body: {} }); Aira.toast("Sent. It shows when Monosage is not in front.", "success"); }) }));
      }
      if (N.installPrompt) parts.push(h("button", { type: "button", class: "btn secondary sm", text: "Install app", onclick: () => install() }));
      else if (!installed() && appleMobile() && !compact) parts.push(h("span", { class: "muted small", text: "To install: Share, then Add to Home Screen." }));
      if (compact) parts.push(h("a", { class: "btn ghost sm", href: "#/notifications", text: "Notification settings" }));
      box.replaceChildren(...parts);
    };
    redraws.add(draw);
    draw();
    return box;
  };

  // ---------------------------------------------------------------------------
  // the page
  // ---------------------------------------------------------------------------
  function when(value) {
    const fmt = Aira.fmt || {};
    return fmt.ago ? fmt.ago(value) : fmt.datetime ? fmt.datetime(value) : String(value || "");
  }

  function itemRow(item, after) {
    const open = async (event) => {
      if (event) event.preventDefault();
      if (!item.read) { try { await Aira.api(`${BASE}/read`, { method: "POST", body: { ids: [item.id] }, silent: true }); } catch { /* still open it */ } }
      refreshCount();
      if (item.url) location.assign(item.url);
      else after();
    };
    const text = item.detail || item.body || "";
    return h("li", { class: `notify-item${item.read ? "" : " unread"}`, style: { listStyle: "none", padding: "10px 12px", borderRadius: "10px", margin: "0 0 6px", background: item.read ? "transparent" : "var(--surface-2, rgba(12,73,50,0.07))" } },
      h("a", { href: item.url || "#/notifications", onclick: open, style: { display: "block", color: "inherit", textDecoration: "none" } },
        h("strong", { text: item.title + (item.count > 1 ? ` (${item.count})` : "") }),
        text ? h("span", { class: "muted", style: { display: "block" }, text }) : null,
        h("span", { class: "muted small", style: { display: "block" }, text: `${when(item.created_at)}${item.read ? "" : " · Unread"}` })));
  }

  function prefsCard(settings, onSaved) {
    const prefs = JSON.parse(JSON.stringify(settings.prefs));
    const rows = settings.categories.map((category) => {
      const choice = prefs.categories[category.id];
      const box = (channel, label) => h("label", { class: "check", style: { marginRight: "14px" } },
        h("input", { type: "checkbox", checked: Boolean(choice[channel]), "aria-label": `${label}: ${category.label}`, onchange: (event) => { choice[channel] = event.currentTarget.checked; } }),
        h("span", { text: ` ${label}` }));
      return h("li", { style: { listStyle: "none", padding: "8px 0", borderBottom: "1px solid var(--line, rgba(0,0,0,0.08))" } },
        h("strong", { text: category.label }), h("span", { class: "muted small", style: { display: "block" }, text: category.hint }),
        h("div", { style: { marginTop: "4px" } }, box("push", "Push"), box("email", "Email")));
    });
    const quietOn = h("input", { type: "checkbox", id: "notifyQuietOn", checked: Boolean(prefs.quiet.on) });
    const start = h("input", { type: "time", id: "notifyQuietStart", value: prefs.quiet.start });
    const end = h("input", { type: "time", id: "notifyQuietEnd", value: prefs.quiet.end });
    const email = h("input", { type: "email", id: "notifyEmail", maxlength: 254, value: prefs.email || "", placeholder: settings.email || "you@example.com" });
    const save = h("button", { type: "button", class: "btn", text: "Save", onclick: (event) => Aira.withBusy(event.currentTarget, async () => {
      const zone = (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || "";
      const saved = await Aira.api(`${BASE}/settings`, { method: "PUT", body: { categories: prefs.categories, quiet: { on: quietOn.checked, start: start.value, end: end.value }, email: email.value.trim(), timezone: prefs.timezone || zone } });
      N.settings = saved;
      Aira.toast("Saved.", "success");
      onSaved(saved);
    }) });
    return h("section", { class: "card" },
      h("h2", { text: "What to tell you" }),
      h("p", { class: "muted", text: "Everything stays here in your notification centre. Push reaches the browsers you turned notifications on in; email works without any browser." }),
      h("ul", { style: { padding: 0, margin: "8px 0" } }, ...rows),
      h("fieldset", { style: { border: 0, padding: 0, margin: "12px 0" } },
        h("legend", { text: "Quiet hours" }),
        h("label", { class: "check", for: "notifyQuietOn" }, quietOn, h("span", { text: " Hold pushes that are not urgent" })),
        h("div", { class: "btn-row", style: { marginTop: "6px" } }, h("label", { for: "notifyQuietStart", text: "From" }), start, h("label", { for: "notifyQuietEnd", text: "until" }), end),
        h("span", { class: "muted small", style: { display: "block" }, text: `Medicine doses that are due and interviews about to start still come through. Times are in ${settings.timezone}.` })),
      Aira.ui.field("Email notifications to", email, "Leave empty to use your profile's address."),
      h("div", { class: "btn-row" }, save));
  }

  Aira.views.notifications = async (main) => {
    const list = h("ul", { class: "notify-list", style: { padding: 0, margin: 0 } });
    const head = h("div", { class: "page-head" },
      h("div", {}, h("h1", { text: "Notifications" }), h("p", { text: "Reminders and messages from every app, and how Monosage tells you." })),
      h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", text: "Mark all read", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { await Aira.api(`${BASE}/read`, { method: "POST", body: {} }); await drawList(); refreshCount(); }) })));
    const browserCard = h("section", { class: "card" }, h("h2", { text: "This browser" }), h("p", { class: "muted", text: "Turn on notifications to be told with Monosage closed: a dose that is due, an interview about to start, a new message." }), N.controls());
    const prefsHolder = h("div", {});
    main.replaceChildren(head, h("div", { class: "grid sidebar-right" }, h("section", { class: "card" }, h("h2", { text: "Recent" }), list), h("div", {}, browserCard, prefsHolder)));
    async function drawList() {
      const data = await Aira.api(`${BASE}/items?limit=60`, { silent: true });
      N.unread = data.unread || 0;
      drawBadge();
      list.replaceChildren(...(data.items.length ? data.items.map((item) => itemRow(item, drawList)) : [Aira.ui.empty("Nothing yet", "Reminders and messages show here.")]));
    }
    await Promise.all([drawList(), checkPush()]);
    const settings = await load();
    const drawPrefs = (current) => prefsHolder.replaceChildren(prefsCard(current, drawPrefs));
    drawPrefs(settings);
    redraw();
    if (Aira.realtime) Aira.realtime.live(["notify"], drawList, 30000, list);
  };
})();

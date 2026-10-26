/* Aira — the life apps' shared core in the browser (Aira.life): made to be easy at any age.
 *
 * - Who is using Aira: on a shared device each person has a profile (a child, a grandparent); the picker shows big
 *   faces and a PIN pad, and every request says who it is for (X-Aira-Profile). The hosted build skips this: the
 *   person is the signed-in member.
 * - Display and reading: easy mode (bigger everything, fewer menus, Today first), text size, high contrast, less
 *   motion, reading the page aloud. Applied before the first paint from the last values, then from the server.
 * - Home: a big Home button goes to Today, each person's day across the life apps (aira/suite/life_routes.py).
 * - Live: a light pulse every 20 seconds updates the badges and shows reminders (for example a medicine due) as a
 *   large dialog that can speak; pages poll their own data with Aira.suite.live(fn, ms) while they are open.
 * DOM is built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const $ = (id) => document.getElementById(id);
  const DEFAULT_PREFS = { easy_mode: false, text_size: "normal", contrast: false, read_aloud: false, reduce_motion: false, start_page: "", language: "" };
  const L = (Aira.life = { me: null, profiles: [], apps: [], canManage: false, prefs: { ...DEFAULT_PREFS, ...Aira.recall("lifePrefs", {}) }, since: "", timer: 0, ready: false });
  const LIFE_APPS = Aira.APPS.filter((app) => app.category === "life");
  const hosted = () => !!Aira.cloud;
  const accountKey = () => `lifeProfile.${(Aira.state.user && Aira.state.user.email) || "local"}`;
  const lifeAvailable = () => !hosted() || !!(Aira.cloud.can && Aira.cloud.can("life.read"));

  // ---------------------------------------------------------------------------
  // Display and reading
  // ---------------------------------------------------------------------------
  function applyPrefs(prefs) {
    const body = document.body;
    body.classList.toggle("easy", !!prefs.easy_mode);
    body.classList.toggle("contrast", !!prefs.contrast);
    body.classList.toggle("reduce-motion", !!prefs.reduce_motion);
    body.classList.toggle("text-large", prefs.text_size === "large");
    body.classList.toggle("text-xlarge", prefs.text_size === "xlarge");
    const read = $("readAloudBtn");
    if (read) read.hidden = !(prefs.read_aloud || prefs.easy_mode);
    const home = $("homeBtn");
    if (home) home.hidden = !lifeAvailable() || !Aira.state.user;
  }
  applyPrefs(L.prefs);

  L.savePrefs = async (changes) => {
    L.prefs = { ...L.prefs, ...changes };
    applyPrefs(L.prefs);
    Aira.remember("lifePrefs", L.prefs);
    try { L.prefs = (await Aira.api("/api/life/prefs", { method: "POST", body: changes, silent: true })).prefs; Aira.remember("lifePrefs", L.prefs); applyPrefs(L.prefs); } catch (error) { Aira.reportError(error); }
  };

  // Reading aloud: the selection, or the page's heading and text.
  const speech = typeof window.speechSynthesis !== "undefined" ? window.speechSynthesis : null;
  L.speak = (text) => {
    if (!speech || !text) return;
    speech.cancel();
    const utterance = new SpeechSynthesisUtterance(String(text).slice(0, 4000));
    if (Aira.i18n) utterance.lang = Aira.i18n.locale; // a Telugu or Hindi voice when the device has one
    utterance.rate = L.prefs.text_size === "xlarge" || (L.me && L.me.age_group === "senior") ? 0.9 : 1;
    utterance.onend = () => { const button = $("readAloudBtn"); if (button) button.setAttribute("aria-pressed", "false"); };
    speech.speak(utterance);
  };
  function pageText() {
    const selected = String(window.getSelection ? window.getSelection() : "").trim();
    if (selected) return selected;
    const main = $("main");
    if (!main) return "";
    const parts = [];
    for (const node of main.querySelectorAll("h1, h2, h3, p, li, td, .suite-kpi, .life-line-text, label")) {
      if (node.closest("[hidden], .sr-only")) continue;
      const text = node.innerText.replace(/\s+/g, " ").trim();
      if (text && !parts.includes(text)) parts.push(text);
    }
    return parts.join(". ");
  }
  const readButton = $("readAloudBtn");
  if (readButton) readButton.addEventListener("click", () => {
    if (!speech) { Aira.toast("This browser cannot read aloud."); return; }
    if (speech.speaking) { speech.cancel(); readButton.setAttribute("aria-pressed", "false"); return; }
    readButton.setAttribute("aria-pressed", "true");
    L.speak(pageText());
  });

  // ---------------------------------------------------------------------------
  // Who is using Aira (shared devices)
  // ---------------------------------------------------------------------------
  function setHeader(value) {
    Aira.state.lifeProfile = value || "";
    Aira.remember(accountKey(), value || "");
  }

  let picking = null;
  L.pickProfile = (reason = "") => {
    if (hosted()) return Promise.resolve(false);
    if (picking) return picking;
    picking = new Promise((resolve) => {
      const overlay = h("div", { class: "life-picker", role: "dialog", "aria-modal": "true", "aria-labelledby": "lifePickerTitle" });
      const close = (ok) => { overlay.remove(); picking = null; resolve(ok); };
      const grid = h("div", { class: "life-faces" });
      const title = h("h1", { id: "lifePickerTitle", text: "Who's using Aira?" });
      const note = h("p", { class: "muted", text: reason === "profile_locked" ? "This person has a PIN. Choose them and type it." : "Tap your name." });
      overlay.append(h("div", { class: "life-picker-box" }, title, note, grid));
      document.body.append(overlay);
      const choose = async (person, pin) => {
        try {
          const data = await Aira.api("/api/life/unlock", { method: "POST", body: { profile_id: person.id, pin: pin || "" } });
          setHeader(data.header);
          L.me = data.profile;
          close(true);
        } catch (error) { return error.message; }
        return "";
      };
      const pinPad = (person) => {
        let typed = "";
        const dots = h("div", { class: "life-pin-dots", "aria-live": "polite", text: "" });
        const error = h("p", { class: "form-error", role: "alert", hidden: true });
        const render = () => { dots.textContent = typed ? "●".repeat(typed.length) : "Enter PIN"; };
        const press = async (key) => {
          if (key === "back") typed = typed.slice(0, -1);
          else if (key === "ok") {
            const message = await choose(person, typed);
            if (message) { error.textContent = message; error.hidden = false; typed = ""; }
          } else if (typed.length < 8) typed += key;
          render();
        };
        const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "ok"];
        grid.replaceChildren(h("div", { class: "life-pin" },
          h("div", { class: "life-face-big", style: { background: person.color }, text: person.emoji }),
          h("h2", { text: person.name }), dots, error,
          h("div", { class: "life-pin-keys" }, ...keys.map((key) => h("button", { type: "button", class: `life-key ${key === "ok" ? "ok" : ""}`, "aria-label": key === "back" ? "Delete" : key === "ok" ? "OK" : key, text: key === "back" ? "⌫" : key === "ok" ? "OK" : key, onclick: () => press(key) }))),
          h("button", { type: "button", class: "btn ghost", text: "Back", onclick: () => show() })));
        render();
        const keyHandler = (event) => { if (!overlay.isConnected) { document.removeEventListener("keydown", keyHandler); return; } if (/^\d$/.test(event.key)) press(event.key); else if (event.key === "Backspace") press("back"); else if (event.key === "Enter") press("ok"); };
        document.addEventListener("keydown", keyHandler);
      };
      const show = async () => {
        let people = [];
        try { people = (await Aira.api("/api/life/profiles", { silent: true })).profiles; } catch (error) { grid.replaceChildren(Aira.ui.error(error, show)); return; }
        grid.replaceChildren(...people.map((person) => h("button", { type: "button", class: "life-face", onclick: async () => { if (person.has_pin) pinPad(person); else { const message = await choose(person, ""); if (message) Aira.toast(message, "error"); } } },
          h("span", { class: "life-face-big", style: { background: person.color }, "aria-hidden": "true", text: person.emoji }),
          h("span", { class: "life-face-name", text: person.name }),
          person.has_pin ? h("span", { class: "muted small", text: "PIN" }) : null)));
        const first = grid.querySelector("button");
        if (first) first.focus();
      };
      show();
    });
    return picking;
  };

  L.switchPerson = async () => {
    setHeader("");
    if (await L.pickProfile("switch")) { await loadMe(); Aira.router.render(); }
  };

  async function loadMe() {
    try {
      const me = await Aira.api("/api/life/me", { silent: true });
      // The server worked out who this is (the only person, or the owner): remember it, so adding a second
      // person later does not leave this browser without an answer.
      if (!hosted() && !Aira.state.lifeProfile && !me.profile.has_pin) setHeader(me.profile.id);
      L.me = me.profile;
      L.profiles = me.profiles;
      L.canManage = me.can_manage;
      L.apps = me.apps;
      L.prefs = { ...DEFAULT_PREFS, ...me.profile.prefs };
      Aira.remember("lifePrefs", L.prefs);
      applyPrefs(L.prefs);
      // Each person's language follows them to any device (the browser's choice until they pick one).
      if (Aira.i18n && L.prefs.language && L.prefs.language !== Aira.i18n.lang) await Aira.i18n.set(L.prefs.language, { save: false });
      // A language chosen on this device before signing in becomes the person's (News reads it for headlines).
      else if (Aira.i18n && !L.prefs.language && Aira.i18n.lang !== "en") L.savePrefs({ language: Aira.i18n.lang });
      if (Aira.renderAccount) Aira.renderAccount();
      return true;
    } catch (error) {
      if (error.code === "profile_required" || error.code === "profile_locked") {
        if (await L.pickProfile(error.code)) return loadMe();
      }
      return false;
    }
  }
  L.reload = loadMe;

  Aira.onApiError = (status, data, path) => {
    if (status !== 403 || !data || !["profile_required", "profile_locked"].includes(data.code)) return;
    if (String(path).startsWith("/api/life/unlock") || String(path).startsWith("/api/life/profiles")) return;
    setHeader("");
    L.pickProfile(data.code).then((ok) => { if (ok) { loadMe(); Aira.router.render(); } });
  };

  Aira.afterSignIn = async () => {
    if (!hosted()) Aira.state.lifeProfile = Aira.recall(accountKey(), "") || "";
    applyPrefs(L.prefs);
    if (!lifeAvailable()) return;
    await loadMe();
    L.ready = true;
    startPulse();
  };
  // Where an empty address goes: the person's own start page, or Today in easy mode.
  Aira.startPage = (initial) => {
    const page = L.prefs.start_page || (L.prefs.easy_mode && lifeAvailable() ? "today" : "");
    if (page && Aira.views[page]) return page;
    return initial ? null : "dashboard";
  };

  // On Today, Display and inside a life app, the phone bar is Home plus the everyday apps.
  Aira.quickItems = (items, current) => {
    // every section of an everyday app (Social has Social and Family)
    const lifeApps = LIFE_APPS.map((app) => app.id);
    const lifeIds = Aira.NAV.filter((item) => lifeApps.includes(item.app)).map((item) => item.id);
    const here = current || "";
    if (!lifeAvailable() || !(here === "today" || here === "display" || lifeIds.includes(here))) return null;
    const all = Aira.NAV;
    const home = all.find((item) => item.id === "today");
    const apps = all.filter((item) => lifeIds.includes(item.id) && (!L.apps.length || L.apps.includes(item.id)));
    return [home, ...apps].filter(Boolean).slice(0, 4);
  };

  if (Aira.i18n) Aira.i18n.onChange((lang, { save }) => { if (save && L.me && lifeAvailable()) L.savePrefs({ language: lang }); });

  Aira.accountLinks.push(
    { label: "Switch person", href: "#/today?switch=1", icon: "family", when: () => !hosted() && L.profiles.length > 1 },
    { label: "Display and reading", href: "#/display", icon: "settings", when: () => lifeAvailable() },
  );

  // ---------------------------------------------------------------------------
  // Live: badges, reminders, and page polling
  // ---------------------------------------------------------------------------
  const seen = new Map(Object.entries(Aira.recall("lifeAlertsSeen", {}))); // alert id -> until (ms)
  const queue = [];
  let showing = false;
  function remember() {
    const now = Date.now();
    for (const [id, until] of seen) if (until < now) seen.delete(id);
    Aira.remember("lifeAlertsSeen", Object.fromEntries(seen));
  }
  function showAlert(alert) {
    const until = seen.get(alert.id);
    if (until && until > Date.now()) return;
    if (queue.some((item) => item.id === alert.id)) return;
    queue.push(alert);
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      try { new Notification(alert.title, { body: alert.body || "", tag: alert.id }); } catch { /* not allowed here */ }
    }
    nextAlert();
  }
  function nextAlert() {
    if (showing || !queue.length) return;
    const alert = queue.shift();
    showing = true;
    const done = (snoozeMinutes) => { seen.set(alert.id, Date.now() + (snoozeMinutes || 24 * 60) * 60000); remember(); showing = false; nextAlert(); };
    const actions = (alert.actions || []).map((action) => ({ label: action.label, kind: "primary", onClick: async () => {
      try { await Aira.api(action.path, { method: action.method || "POST", body: action.body || {} }); Aira.toast("Done.", "success"); done(); refreshToday(); } catch (error) { Aira.reportError(error); return false; }
      return true;
    } }));
    Aira.modal({
      title: alert.title,
      body: h("div", { class: `life-alert ${alert.tone || ""}` }, alert.body ? h("p", { text: alert.body }) : null),
      actions: [...actions, { label: "Remind me in 10 minutes", kind: "secondary", onClick: () => { done(10); } }, { label: "Close", kind: "ghost", onClick: () => { done(); } }],
      onClose: () => { if (showing) { showing = false; nextAlert(); } },
    });
    if (alert.speak && (L.prefs.read_aloud || L.prefs.easy_mode)) L.speak(`${alert.title}. ${alert.body || ""}`);
  }

  async function tick() {
    if (!L.ready || !Aira.state.user) return;
    if (document.hidden && !("Notification" in window && Notification.permission === "granted")) return;
    try {
      const data = await Aira.api(`/api/life/pulse${L.since ? `?since=${encodeURIComponent(L.since)}` : ""}`, { silent: true });
      L.since = data.now;
      const counts = {};
      for (const [app, value] of Object.entries(data.apps || {})) counts[app] = value.badge || 0;
      Aira.renderNav(counts);
      for (const alert of data.alerts || []) showAlert(alert);
    } catch { /* the next beat tries again */ }
  }
  function startPulse() {
    if (L.timer) return;
    tick();
    L.timer = setInterval(tick, 20000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  }
  L.tick = tick;

  // A page polls its own data while it stays open and visible: const stop = Aira.suite.live(refresh, 4000).
  S.live = (fn, ms = 5000) => {
    const here = location.hash.split("?")[0];
    let timer = 0;
    const stop = () => { if (timer) clearInterval(timer); timer = 0; };
    timer = setInterval(async () => {
      if (location.hash.split("?")[0] !== here) { stop(); return; }
      if (document.hidden) return;
      try { await fn(); } catch { /* the next round tries again */ }
    }, Math.max(1500, ms));
    return stop;
  };

  // ---------------------------------------------------------------------------
  // Today
  // ---------------------------------------------------------------------------
  let todayRefresh = null;
  function refreshToday() { if (todayRefresh) todayRefresh(); }

  function line(entry, rerender) {
    const actionButton = entry.action ? h("button", { type: "button", class: `life-check ${entry.done ? "done" : ""}`, "aria-label": `${entry.action.label}: ${entry.text}`, title: entry.action.label, onclick: async (event) => {
      await Aira.withBusy(event.currentTarget, async () => {
        try { await Aira.api(entry.action.path, { method: entry.action.method || "POST", body: entry.action.body || {} }); Aira.toast(`${entry.action.label}: ${entry.text}`, "success"); rerender(); L.tick(); } catch (error) { Aira.reportError(error); }
      });
    } }, h("span", { "aria-hidden": "true", text: entry.done ? "✓" : "" }), h("span", { class: "life-check-label", text: entry.action.label })) : entry.done ? h("span", { class: "life-check done", "aria-hidden": "true" }, h("span", { text: "✓" })) : null;
    return h("li", { class: `life-line ${entry.done ? "done" : ""}` }, actionButton, h("div", {}, h("span", { class: "life-line-text", text: entry.text }), entry.sub ? h("span", { class: "muted life-line-sub", text: entry.sub }) : null));
  }

  function cardNode(card, rerender) {
    const app = Aira.APPS.find((entry) => entry.id === card.app);
    return h("section", { class: `life-card ${card.tone || ""}` },
      h("header", {}, h("span", { class: "life-card-icon", "aria-hidden": "true" }, Aira.icon(card.icon || card.app)), h("h2", { text: card.title })),
      card.lines.length ? h("ul", { class: "life-lines" }, ...card.lines.map((entry) => line(entry, rerender))) : h("p", { class: "muted", text: card.empty || "Nothing for now." }),
      card.link ? h("a", { class: "btn secondary life-open", href: card.link, text: `Open ${app ? app.name : "app"}` }) : null);
  }

  Aira.views.today = async (main, route) => {
    if (route.query.switch) { Aira.router.go(["today"], {}, { replace: true }); await L.switchPerson(); return; }
    const render = async () => {
      let data;
      try { data = await Aira.api("/api/life/today"); } catch (error) { main.replaceChildren(Aira.ui.error(error, render)); return; }
      const me = data.profile;
      const tiles = LIFE_APPS.filter((app) => data.apps.includes(app.id)).map((app) => h("a", { class: "life-tile", href: `#/${app.id}` }, h("span", { class: "life-tile-icon", "aria-hidden": "true" }, Aira.icon(app.icon)), h("strong", { text: app.name }), h("span", { class: "muted", text: app.tagline })));
      const birthdays = (data.birthdays || []).map((b) => h("div", { class: "life-birthday", role: "status" }, h("span", { "aria-hidden": "true", text: "🎂" }), h("span", { text: b.in_days === 0 ? `Today is ${b.name}'s birthday${b.age ? ` (${b.age})` : ""}!` : `${b.name}'s birthday is in ${b.in_days} day${b.in_days === 1 ? "" : "s"}${b.age ? ` (turning ${b.age})` : ""}.` })));
      const remindersOff = "Notification" in window && Notification.permission === "default";
      main.replaceChildren(...[
        h("div", { class: "life-hero" },
          h("div", { class: "life-hero-face", style: { background: me.color }, "aria-hidden": "true", text: me.emoji }),
          h("div", {}, h("h1", {}, h("span", { text: data.salutation || data.greeting }), data.salutation ? h("span", { translate: "no", text: `, ${me.name}` }) : null),
            h("p", { class: "life-date", text: new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) + " · " + new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) })),
          h("div", { class: "btn-row life-hero-actions" },
            !hosted() && L.profiles.length > 1 ? h("button", { type: "button", class: "btn secondary", text: "Switch person", onclick: () => L.switchPerson() }) : null,
            h("button", { type: "button", class: "btn secondary", text: L.prefs.easy_mode ? "Normal size" : "Make it bigger", onclick: async () => { await L.savePrefs({ easy_mode: !L.prefs.easy_mode, text_size: L.prefs.easy_mode ? "normal" : "large" }); render(); } }),
            h("a", { class: "btn ghost", href: "#/display", text: "Display and reading" }))),
        ...birthdays,
        remindersOff ? h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: "Get reminders on this device" }), h("span", { text: "Aira can show your medicine and chore reminders even when this tab is in the background." }), h("button", { type: "button", class: "btn secondary sm", text: "Turn on", onclick: async () => { try { await Notification.requestPermission(); } catch { /* ignored */ } render(); } })) : null,
        data.cards.length ? h("div", { class: "life-cards" }, ...data.cards.map((card) => cardNode(card, render))) : null,
        !data.apps.length ? Aira.ui.empty("No everyday apps yet", hosted() ? "Ask your organization's owner to turn on Family, Health, Learn, Play or Planner." : "The everyday apps appear here when they are installed.") : null,
        tiles.length ? h("section", { class: "life-tiles-wrap" }, h("h2", { class: "suite-h2", text: "Your apps" }), h("div", { class: "life-tiles" }, ...tiles)) : null,
        !L.prefs.start_page && !L.prefs.easy_mode ? h("p", { class: "muted small" }, h("button", { type: "button", class: "btn ghost sm", text: "Open Aira on this page", onclick: async () => { await L.savePrefs({ start_page: "today" }); Aira.toast("Aira now opens on Today.", "success"); render(); } })) : null,
      ].filter(Boolean));
    };
    todayRefresh = render;
    await render();
    S.live(render, 60000);
  };

  // ---------------------------------------------------------------------------
  // Display and reading, and the people in the household
  // ---------------------------------------------------------------------------
  function toggle(label, hint, checked, onChange) {
    const input = h("input", { type: "checkbox", role: "switch", checked: !!checked });
    input.addEventListener("change", () => onChange(input.checked));
    return h("label", { class: "life-toggle" }, input, h("span", {}, h("strong", { text: label }), hint ? h("span", { class: "muted", text: hint }) : null));
  }

  Aira.views.display = async (main) => {
    if (!L.me) await loadMe();
    const render = () => {
      const pages = [["", "Usual (the first app)"], ["today", "Today"], ...LIFE_APPS.map((app) => [app.id, app.name]), ["dashboard", "Careers"]];
      const start = Aira.ui.select(pages, L.prefs.start_page || "", { "aria-label": "Start page" });
      start.addEventListener("change", () => L.savePrefs({ start_page: start.value }));
      const sizes = h("div", { class: "life-sizes", role: "radiogroup", "aria-label": "Text size" }, ...[["normal", "A", "Normal"], ["large", "A", "Large"], ["xlarge", "A", "Extra large"]].map(([value, letter, label], index) => h("button", { type: "button", role: "radio", "aria-checked": String(L.prefs.text_size === value), class: `life-size size-${index}`, onclick: async () => { await L.savePrefs({ text_size: value }); render(); } }, h("span", { "aria-hidden": "true", text: letter }), h("span", { text: label }))));
      const body = S.page(main, { title: "Display and reading", subtitle: L.me ? `Settings for ${L.me.name}. Each person on this device has their own.` : "" });
      const languages = Aira.i18n ? h("div", { class: "life-sizes", role: "radiogroup", "aria-label": "Language", translate: "no" }, ...Aira.i18n.languages.map((language) => h("button", { type: "button", role: "radio", lang: language.code, "aria-checked": String(Aira.i18n.lang === language.code), class: "life-size", onclick: async () => { await Aira.i18n.set(language.code); render(); } }, h("span", { text: language.native }), language.code === "en" ? null : h("span", { class: "muted small", text: language.name })))) : null;
      body.append(
        languages ? S.section("Language", h("p", { class: "muted", text: "Aira shows its menus, buttons and messages in this language." }), languages) : null,
        S.section("Text size", sizes),
        S.section("Easier to use",
          toggle("Easy mode", "Bigger buttons, fewer menus, and Aira opens on Today.", L.prefs.easy_mode, (on) => L.savePrefs({ easy_mode: on })),
          toggle("High contrast", "Stronger colours and borders.", L.prefs.contrast, (on) => L.savePrefs({ contrast: on })),
          toggle("Read aloud button", "Shows a button that reads the page, or what you select, out loud.", L.prefs.read_aloud, (on) => L.savePrefs({ read_aloud: on })),
          toggle("Less movement", "Turns off animations.", L.prefs.reduce_motion, (on) => L.savePrefs({ reduce_motion: on }))),
        S.section("Start page", h("p", { class: "muted", text: "The page Aira opens on." }), Aira.ui.field("Open Aira on", start)),
        hosted() ? null : peopleSection(render),
      );
    };
    render();
  };

  function peopleSection(rerender) {
    const people = L.profiles || [];
    const fields = (withPin) => [
      { name: "name", label: "Name", required: true },
      { name: "emoji", label: "Picture", type: "select", options: [["", "Choose one for me"], "🙂", "😀", "🧑", "👩", "👨", "👵", "👴", "👧", "👦", "🧒", "🐱", "🐶", "🌻", "⭐", "🚀", "🎵"] },
      { name: "age_group", label: "Age group", type: "select", options: [["adult", "Adult"], ["senior", "Senior (starts with bigger text)"], ["teen", "Teen"], ["child", "Child (starts in easy mode)"]] },
      { name: "birthday", label: "Birthday", type: "date", hint: "Shown on Today when it is near." },
      ...(withPin ? [{ name: "pin", label: "PIN (optional)", type: "password", hint: "4 to 8 digits. Without one, anyone on this device can open this person." }] : []),
    ];
    const refresh = async () => { await loadMe(); rerender(); };
    const rows = people.map((person) => h("li", { class: "life-person" },
      h("span", { class: "life-face-small", style: { background: person.color }, "aria-hidden": "true", text: person.emoji }),
      h("div", {}, h("strong", { text: person.name }), h("span", { class: "muted small", text: ` ${person.age_group}${person.owner ? " · owner" : ""}${person.has_pin ? " · PIN" : ""}` })),
      L.canManage || (L.me && person.id === L.me.id) ? h("div", { class: "btn-row" },
        S.button("Edit", () => S.edit({ title: `Edit ${person.name}`, fields: fields(false), values: person, onSave: async (values) => { await Aira.api(`/api/life/profiles/${person.id}`, { method: "PATCH", body: values }); await refresh(); } }), { kind: "ghost sm" }),
        S.button(person.has_pin ? "Change PIN" : "Set PIN", () => S.edit({ title: `PIN for ${person.name}`, fields: [{ name: "pin", label: "New PIN (4 to 8 digits; empty removes it)", type: "password" }], onSave: async (values) => { const data = await Aira.api(`/api/life/profiles/${person.id}/pin`, { method: "POST", body: { pin: values.pin } }); if (data.header) setHeader(data.header); await refresh(); } }), { kind: "ghost sm" }),
        L.canManage && !person.owner ? S.button("Remove", async () => { if (await S.confirmDelete(person.name)) { await Aira.api(`/api/life/profiles/${person.id}`, { method: "DELETE" }); await refresh(); } }, { kind: "ghost sm" }) : null) : null));
    return S.section("People on this device", h("p", { class: "muted", text: "Everyone in the household can have their own reminders, games and settings." }),
      h("ul", { class: "life-people" }, ...rows),
      L.canManage ? h("div", { class: "btn-row" }, S.button("Add a person", () => S.edit({ title: "Add a person", fields: fields(true), values: { emoji: "", age_group: "adult" }, onSave: async (values) => { await Aira.api("/api/life/profiles", { method: "POST", body: values }); await refresh(); } }))) : null);
  }
})();

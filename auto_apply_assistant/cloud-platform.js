/* Aira hosted build — the platform (vendor) console.
 *
 * A separate application surface for Aira's own operators at #/platform/...: its own session with mandatory MFA
 * (cookie aira_operator, endpoints under /api/platform), never a tenant's session. Operators sign in on the same
 * page as everyone (cloud-auth.js, POST /api/auth/login); that page continues here, where the second factor is
 * asked (or set up the first time) before the console opens. Operators have no candidate workspace;
 * they reach a tenant's own screens only through a support grant the tenant owner approved (at most 24 hours),
 * and a banner stays on screen for as long as that access lasts.
 *
 * cloud-boot.js calls Aira.cloud.platformBoot() when the hash starts with #/platform.
 */
"use strict";

(function () {
  const h = window.h; // app.js declares $ as a top-level const, which classic scripts share by name
  const cloud = Aira.cloud;
  if (!cloud) return;
  const { fmt, ui } = Aira;

  const P = { active: false, me: null, items: [], tenants: null, supportTimer: 0 };
  const api = (path, opts) => Aira.api(path, opts);
  const SUPPORT_KEY = "airaSupportGrant";

  // ---------------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------------
  const isAdmin = () => !!P.me && P.me.role === "platform_admin";
  const titleCase = (text) => String(text || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  const ts = (seconds) => (seconds ? fmt.datetime(seconds) : "—");
  const dollars = (cents) => (Number(cents || 0) / 100).toLocaleString([], { style: "currency", currency: "USD" });
  const chip = (text, level = "") => h("span", { class: `cp-chip ${level}`, text: String(text).replace(/_/g, " ") });
  const FLAG_LEVEL = { suspended: "danger", past_due: "danger", read_only: "danger", cancelled: "warn", paused: "warn", over_limits: "warn", rate_limited: "warn", deletion_scheduled: "danger", error_budget: "danger" };
  const flagChips = (flags) => (flags && flags.length ? h("div", { class: "cp-chips" }, ...flags.map((f) => chip(f, FLAG_LEVEL[f] || ""))) : h("span", { class: "muted", text: "healthy" }));
  const statusBadge = (status) => h("span", { class: `badge cb-status ${{ active: "ok", trialing: "info", past_due: "danger", read_only: "danger", cancelled: "warn" }[status] || ""}`, text: String(status).replace("_", " ") });
  const empty = (title, text) => ui.empty(title, text);
  // replaceChildren renders a null child as the text "null"; leave out what is not there.
  const put = (node, ...children) => node.replaceChildren(...children.filter((child) => child !== null && child !== undefined && child !== false));
  const section = (title, ...children) => h("section", { class: "card cb-card" }, title ? h("h2", { text: title }) : null, ...children);
  const pageHead = (title, text, ...actions) => h("div", { class: "page-head" }, h("div", {}, h("h1", { text: title }), text ? h("p", { class: "muted", text }) : null), actions.length ? h("div", { class: "btn-row" }, ...actions) : null);
  const table = (headers, rows, emptyText = "Nothing here yet.") => (rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "table cp-table" }, h("thead", {}, h("tr", {}, ...headers.map((t) => h("th", { scope: "col", class: t.startsWith("#") ? "num" : "", text: t.replace(/^#/, "") })))), h("tbody", {}, ...rows))) : h("p", { class: "muted", text: emptyText }));
  const td = (content, cls = "") => h("td", { class: cls }, content instanceof Node ? content : String(content ?? "—"));
  const readonlyNote = () => (isAdmin() ? null : h("div", { class: "cb-note info", role: "status", text: "You are signed in as support: these screens are read-only. A platform admin makes changes." }));

  async function tenantsList() {
    if (!P.tenants) P.tenants = (await api("/api/platform/tenants?limit=500", { silent: true })).tenants;
    return P.tenants;
  }
  const tenantName = (id) => ((P.tenants || []).find((t) => t.org_id === id) || {}).name || (id ? id.slice(0, 8) : "");
  // The plans (built-in and the superadmin's, archived ones too) in the superadmin's order; loaded once per screen.
  async function plansList(fresh = false) {
    if (fresh || !P.planData) P.planData = await api("/api/platform/plans", { silent: true }).catch(() => ({ plans: [] }));
    return P.planData.plans || [];
  }
  const planName = (id) => ((P.planData && P.planData.plans) || []).find((p) => p.plan_id === id)?.name || titleCase(id);
  /** [id, label] pairs for a select: live plans (the trial only when asked), each named. */
  const planChoices = ({ trial = true, archived = false } = {}) => ((P.planData && P.planData.plans) || []).filter((p) => (trial || p.plan_id !== "trial") && (archived || !p.archived)).map((p) => [p.plan_id, p.archived ? `${p.name} (archived)` : p.name]);

  /** A modal form: fields in, values out. Validation errors are thrown and shown without closing it. */
  function formModal({ title, intro, fields, submit = "Save", danger = false, onSubmit, wide = false }) {
    const inputs = {};
    const body = h("form", { class: "cp-form", onsubmit: (event) => event.preventDefault() }, intro ? h("p", { class: "muted", text: intro }) : null);
    for (const f of fields) {
      let input;
      if (f.type === "textarea") input = h("textarea", { rows: String(f.rows || 4), class: f.mono ? "cp-mono" : "" });
      else if (f.type === "select") input = ui.select(f.options, f.value ?? f.options[0][0]);
      else if (f.type === "checkbox") input = h("input", { type: "checkbox" });
      else input = h("input", { type: f.type || "text", autocomplete: "off" });
      if (f.type === "checkbox") input.checked = !!f.value;
      else if (f.type !== "select" && f.value !== undefined) input.value = f.value;
      for (const key of ["min", "max", "step", "placeholder", "maxlength", "list"]) if (f[key] !== undefined) input.setAttribute(key, String(f[key]));
      inputs[f.name] = input;
      body.append(f.type === "checkbox" ? h("label", { class: "check" }, input, h("span", { text: f.label })) : ui.field(f.label, input, f.hint));
    }
    const read = () => Object.fromEntries(fields.map((f) => [f.name, f.type === "checkbox" ? inputs[f.name].checked : f.type === "number" ? (inputs[f.name].value === "" ? null : Number(inputs[f.name].value)) : String(inputs[f.name].value).trim()]));
    Aira.modal({ title, body, wide, actions: [{ label: "Cancel", kind: "ghost" }, { label: submit, kind: danger ? "danger" : "", onClick: async () => { await onSubmit(read()); } }] });
    const first = body.querySelector("input, textarea, select");
    if (first) setTimeout(() => first.focus(), 30);
  }

  const download = (name, text, type = "application/x-ndjson") => {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = h("a", { href: url, download: name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };

  // ---------------------------------------------------------------------------
  // Operator sign-in and MFA
  // ---------------------------------------------------------------------------
  function authShell(title, intro, ...children) {
    P.screen = "auth";
    // The same panel as the sign-in page (cloud-auth.js): one page for everyone.
    if (cloud.auth && cloud.auth.show) { cloud.auth.show(title, intro, ...children); return; }
    $("boot").hidden = true;
    $("app").hidden = true;
    const screen = $("authScreen");
    screen.hidden = false;
    screen.replaceChildren(h("div", { class: "auth-panel cp-auth" }, h("div", { class: "brand" }, h("span", { class: "brand-mark", text: "M" }), h("span", {}, "MONOSAGE", h("small", { text: "Platform console" }))), h("h1", { style: { marginTop: "18px" }, text: title }), intro ? h("p", { class: "muted", text: intro }) : null, ...children));
    const first = screen.querySelector("input");
    if (first) setTimeout(() => first.focus(), 30);
  }

  // No console session: the one sign-in page, which comes back here once an operator has signed in.
  function showLogin(message = "") {
    P.screen = "auth";
    $("boot").hidden = true;
    $("app").hidden = true;
    if (cloud.auth && cloud.auth.render) cloud.auth.render("login", { message });
  }

  function showMfaVerify(message = "") {
    const code = h("input", { id: "cpCode", inputmode: "numeric", autocomplete: "one-time-code", required: true, maxlength: "20" });
    const notice = h("p", { class: "muted", role: "status", "aria-live": "polite", text: message });
    const submit = h("button", { class: "btn", type: "submit", text: "Verify", style: { width: "100%" } });
    const form = h("form", { onsubmit: async (event) => {
      event.preventDefault();
      await Aira.withBusy(submit, async () => {
        try { await api("/api/platform/auth/mfa/verify", { method: "POST", body: { code: code.value } }); await finishSignIn(); }
        catch (error) { notice.textContent = error.message; code.select(); }
      });
    } }, ui.field("Authenticator code or a recovery code", code), notice, submit);
    authShell("Second factor", "Operators always sign in with a second factor.", form, h("p", { class: "switch" }, h("button", { type: "button", class: "btn ghost sm", text: "Use another account", onclick: () => signOut() })));
  }

  async function showMfaEnrol() {
    let begun;
    try { begun = await api("/api/platform/auth/mfa/begin", { method: "POST", body: {} }); }
    catch (error) { showLogin(error.message); return; }
    const code = h("input", { id: "cpEnrolCode", inputmode: "numeric", autocomplete: "one-time-code", required: true, maxlength: "10" });
    const notice = h("p", { class: "muted", role: "status", "aria-live": "polite" });
    const submit = h("button", { class: "btn", type: "submit", text: "Turn on MFA", style: { width: "100%" } });
    const form = h("form", { onsubmit: async (event) => {
      event.preventDefault();
      await Aira.withBusy(submit, async () => {
        try { const done = await api("/api/platform/auth/mfa/enable", { method: "POST", body: { code: code.value } }); showRecoveryCodes(done.recovery_codes); }
        catch (error) { notice.textContent = error.message; }
      });
    } },
    h("ol", { class: "cp-steps" }, h("li", { text: "Add this account to an authenticator app: scan with a QR-capable app via the link below, or type the key." }), h("li", { text: "Enter the six-digit code it shows." })),
    h("div", { class: "cp-secret" }, h("code", { id: "cpSecret", text: begun.secret }), h("button", { type: "button", class: "btn secondary sm", text: "Copy key", onclick: () => navigator.clipboard && navigator.clipboard.writeText(begun.secret).then(() => Aira.toast("Key copied.", "success")) })),
    h("p", {}, h("a", { href: begun.otpauth_uri, text: "Open in an authenticator app" })),
    ui.field("Six-digit code", code), notice, submit);
    authShell("Set up MFA", "Platform operators must use a second factor before anything else works.", form);
  }

  function showRecoveryCodes(codes) {
    const list = h("ul", { class: "cp-recovery", "aria-label": "Recovery codes" }, ...codes.map((c) => h("li", {}, h("code", { text: c }))));
    authShell("Save your recovery codes", "Each works once if you lose your authenticator. They are shown only now.", list,
      h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", text: "Copy all", onclick: () => navigator.clipboard && navigator.clipboard.writeText(codes.join("\n")).then(() => Aira.toast("Codes copied.", "success")) }), h("button", { type: "button", class: "btn", id: "cpSaved", text: "I have saved them", onclick: finishSignIn })));
  }

  async function finishSignIn() {
    P.me = await api("/api/platform/me", { silent: true });
    showConsole();
  }

  // Signs out of everything (the console and the same person's organization session) and shows the sign-in page.
  async function signOut() {
    try { await api("/api/platform/auth/logout", { method: "POST", body: {} }); } catch { /* the session may already be gone */ }
    sessionStorage.removeItem(SUPPORT_KEY);
    Aira.remember("operatorConsole", false);
    history.replaceState(null, "", "/");
    location.reload();
  }

  // Someone who is an operator and a member moves between the console and their apps without signing in again.
  if (Aira.accountLinks) {
    Aira.accountLinks.push({ label: "Your apps", href: "#/today", icon: "dashboard", when: () => P.active && !!P.me && !!P.me.member });
    Aira.accountLinks.push({ label: "Admin console", href: "#/platform", icon: "settings", when: () => !P.active && !Aira.state.platformMode && !!cloud.me && !!cloud.me.console });
  }

  // ---------------------------------------------------------------------------
  // The console shell
  // ---------------------------------------------------------------------------
  const NAV_DEFS = [
    { sub: "tenants", id: "ptenants", label: "Tenants", icon: "dashboard", key: "t", quick: true },
    { sub: "plans", id: "pplans", label: "Plans", icon: "settings", key: "l" },
    { sub: "apps", id: "papps", label: "Apps", icon: "hub", key: "p" },
    { sub: "flags", id: "pflags", label: "Feature flags", icon: "airaapi", key: "f" },
    { sub: "support", id: "psupport", label: "Support access", icon: "recruiters", key: "u", quick: true },
    { sub: "announcements", id: "pannounce", label: "Announcements", icon: "more", key: "n" },
    { sub: "health", id: "phealth", label: "Health", icon: "analytics", key: "h", quick: true },
    { sub: "audit", id: "paudit", label: "Audit", icon: "documents", key: "a", quick: true },
    { sub: "moderation", id: "pmoderation", label: "Social moderation", icon: "recruiters", key: "m" },
    { sub: "videos", id: "pvideos", label: "Videos moderation", icon: "videos", key: "v" },
    { sub: "creators", id: "pcreators", label: "Creators & ads", icon: "ads", key: "e" },
    { sub: "abuse", id: "pabuse", label: "Abuse and compliance", icon: "careers", key: "b", admin: true },
    { sub: "operators", id: "poperators", label: "Operators", icon: "recruiters", key: "o", admin: true },
  ];

  function decorateNav() {
    if (!P.active) return;
    const current = Aira.router.parse().path[1] || "tenants";
    for (const link of document.querySelectorAll("#primaryNav a[data-section], #bottomNav a[data-section]")) {
      const item = P.items.find((i) => i.id === link.dataset.section);
      if (!item) continue;
      link.setAttribute("href", `#/platform/${item.sub}`);
      link.setAttribute("aria-current", item.sub === current ? "page" : "false");
    }
  }

  function showConsole() {
    P.active = true;
    P.screen = "console";
    Aira.state.platformMode = true;
    Aira.remember("operatorConsole", true); // the main address opens the console on this browser (cloud-spaces.js)
    P.items = NAV_DEFS.filter((item) => !item.admin || isAdmin());
    Aira.state.user = { email: P.me.email, kind: "operator", role: P.me.role };
    $("authScreen").hidden = true;
    $("boot").hidden = true;
    $("app").hidden = false;
    $("accountEmail").textContent = P.me.email;
    $("accountMeta").textContent = P.me.role === "platform_admin" ? "platform admin" : "support";
    $("statusStrip").replaceChildren();
    const logout = $("logoutBtn");
    const fresh = logout.cloneNode(true);
    logout.replaceWith(fresh);
    fresh.addEventListener("click", signOut);
    // The console is its own app in the switcher: every account, no workspace.
    Aira.apps.pseudo.platform = { name: "Admin console", tagline: "Every account on this platform", icon: "settings", home: "ptenants" };
    Aira.NAV.splice(0, Aira.NAV.length, ...P.items.map((item) => ({ id: item.id, label: item.label, icon: item.icon, group: "Platform", app: "platform", key: item.key, quick: !!item.quick })));
    Aira.state.currentApp = "platform";
    Aira.renderNav();
    if (Aira.renderAccount) Aira.renderAccount();
    renderSupportBar();
    if (!location.hash.startsWith("#/platform")) location.hash = "#/platform/tenants";
    else Aira.router.render();
  }

  // The member sign-in must not appear while the console is open; a 401 means the operator session ended.
  // app.js also starts the member boot on page load; its 401 probe of /api/auth/me must not replace this sign-in.
  const inPlatform = () => P.active || String(location.hash).startsWith("#/platform");
  const originalShowAuth = Aira.showAuth;
  Aira.showAuth = () => {
    if (P.memberProbe) { P.memberProbe = false; return; }
    if (inPlatform()) { P.active = false; showLogin("Your session ended. Sign in again."); } else originalShowAuth();
  };
  const originalBoot = cloud.boot;
  // Someone who is both an operator and a member, and has used the console on this browser, starts in the console
  // when they open the main address (an app's own address, /careers, still opens that app).
  const mainAddress = () => location.pathname === "/" && ["", "#", "#/"].includes(location.hash);
  cloud.boot = async (user) => {
    if (!inPlatform()) {
      if (user && user.kind === "member" && user.console && mainAddress() && Aira.recall("operatorConsole", false)) {
        history.replaceState(null, "", "/#/platform");
        location.reload();
        return new Promise(() => {}); // the member app does not start: the page is reloading into the console
      }
      return originalBoot(user);
    }
    // app.js shows the app as soon as a member session answers /api/auth/me (someone who is also a member); while
    // the console's sign-in or second factor is up, that screen stays.
    if (P.screen === "auth") { $("app").hidden = true; $("authScreen").hidden = false; }
    return false;
  };
  const originalOnApiError = cloud.onApiError;
  cloud.onApiError = (status, data, path) => {
    if (inPlatform() && path === "/api/auth/me" && status === 401) { P.memberProbe = true; return; }
    if (String(path).startsWith("/api/platform/") && !String(path).startsWith("/api/platform/auth/")) {
      if (status === 401 && data && data.code === "mfa_required") { P.active = false; showMfaVerify(); }
      else if (status === 402 && data && data.code === "plan_required") { /* not meaningful here */ }
      return;
    }
    if (originalOnApiError) originalOnApiError(status, data, path);
  };
  const originalBuildNav = cloud.buildNav;
  cloud.buildNav = () => { if (Aira.state.platformMode) { Aira.renderNav(); return; } originalBuildNav(); };
  const originalRenderNav = Aira.renderNav;
  Aira.renderNav = (counts) => { originalRenderNav(counts); decorateNav(); };
  window.addEventListener("hashchange", decorateNav);

  cloud.platformBoot = async () => {
    P.active = true;
    Aira.state.platformMode = true;
    Aira.state.actingFor = null;
    $("boot").hidden = false;
    try {
      P.me = await api("/api/platform/me", { silent: true });
    } catch (error) {
      if (!(error && error.status === 401)) showLogin(error && error.message);
      else if (error.code === "mfa_required") showMfaVerify();
      else showLogin();
      return;
    }
    if (!P.me.mfa.enabled) showMfaEnrol();
    else if (!P.me.mfa.verified) showMfaVerify();
    else showConsole();
  };

  // ---------------------------------------------------------------------------
  // Router
  // ---------------------------------------------------------------------------
  const SCREENS = {};
  Aira.views.platform = async (stage, route) => {
    const sub = route.path[1] || "tenants";
    const item = P.items.find((i) => i.sub === sub);
    $("crumbs").replaceChildren(h("span", { text: item ? `Platform · ${item.label}` : "Platform" }));
    document.title = `${item ? item.label : "Platform"} · Monosage platform`;
    // Until the second factor is given the console draws nothing (a member session's app boot may ask for a page).
    if (!P.active || !P.me || P.screen !== "console") { put(stage, empty("Sign in to the platform console", "Open #/platform and sign in as an operator.")); return; }
    if (!item) { put(stage, empty("Not available", "That console page does not exist or needs a platform admin.")); return; }
    await SCREENS[sub](stage, route);
  };
  for (const def of NAV_DEFS) Aira.views[def.id] = async (stage) => { Aira.router.go(["platform", def.sub], {}, { replace: true }); put(stage); };

  // ---------------------------------------------------------------------------
  // Tenants
  // ---------------------------------------------------------------------------
  SCREENS.tenants = async (stage, route) => {
    if (route.path[2]) return tenantDetail(stage, route.path[2]);
    await plansList(true);
    const filters = { q: route.query.q || "", status: route.query.status || "", plan: route.query.plan || "" };
    const listHost = h("div", {});
    const q = h("input", { type: "search", "data-search": "", placeholder: "Search by name or address", value: filters.q, "aria-label": "Search tenants" });
    const status = ui.select([["", "Any state"], ["trialing", "Trial"], ["active", "Active"], ["past_due", "Past due"], ["read_only", "Read-only"], ["cancelled", "Cancelled"]], filters.status, { "aria-label": "State" });
    const plan = ui.select([["", "Any plan"], ...planChoices({ archived: true })], filters.plan, { "aria-label": "Plan" });
    const flagSelect = ui.select([["", "Any health"], ["problems", "Needs a look"]], "", { "aria-label": "Health" });
    let timer = 0;
    async function load() {
      listHost.replaceChildren(ui.skeleton(5));
      const params = new URLSearchParams({ limit: "500" });
      if (q.value) params.set("q", q.value);
      if (status.value) params.set("status", status.value);
      if (plan.value) params.set("plan", plan.value);
      const { tenants } = await api(`/api/platform/tenants?${params}`);
      P.tenants = P.tenants || tenants;
      const shown = flagSelect.value === "problems" ? tenants.filter((t) => t.flags.length) : tenants;
      listHost.replaceChildren(table(["Organization", "Plan", "State", "#Seats", "#Applications this month", "Health", "Errors 24 h", "Created"], shown.map((t) => h("tr", {},
        td(h("a", { href: `#/platform/tenants/${t.org_id}`, text: t.name }), "cp-name"), td(planName(t.plan_id)), td(statusBadge(t.status)),
        td(`${t.seats.candidates}${t.seats.purchased ? ` / ${t.seats.purchased}` : ""} · ${t.seats.staff} staff`, "num"), td(String(t.applications_this_month), "num"),
        td(flagChips(t.flags)), td(t.error_rate_24h ? `${Math.round(t.error_rate_24h * 100)}%` : "—"), td(fmt.date(t.created_at)))), "No organizations match."));
    }
    const debounced = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
    q.addEventListener("input", debounced);
    for (const control of [status, plan, flagSelect]) control.addEventListener("change", load);
    // A platform admin makes an account for someone: on the plan for new sign-ups, with the email confirmed.
    const newAccount = isAdmin() ? h("button", { type: "button", class: "btn", text: "New account", "data-new-account": "1", onclick: () => formModal({
      title: "New account",
      intro: "Creates the person's account on the plan for new sign-ups, with every app it includes. Their email counts as confirmed; give them the password, and they can change it under Security.",
      fields: [
        { name: "name", label: "Their name", maxlength: 120 },
        { name: "email", label: "Email", type: "email", maxlength: 254 },
        { name: "password", label: "Password (at least 12 characters)", type: "password", maxlength: 1024 },
        { name: "organization", label: "Business or organization name (optional)", maxlength: 120, placeholder: "Leave empty for “<name>'s workspace”" },
      ],
      submit: "Create account",
      onSubmit: async (v) => {
        const made = await api("/api/platform/accounts", { method: "POST", body: v });
        Aira.toast(`Account created for ${made.member.email} on ${planName(made.subscription.plan_id)}. Recorded in the platform audit as ${P.me.email}.`, "success", { timeout: 8000 });
        P.tenants = null;
        location.hash = `#/platform/tenants/${made.org.org_id}`;
      },
    }) }) : null;
    put(stage, pageHead("Tenants", "Every organization, its plan, usage and health. Open one for actions.", h("div", { class: "btn-row" }, newAccount, h("button", { type: "button", class: "btn secondary", text: "Refresh", onclick: () => { P.tenants = null; load(); } }))), readonlyNote(), h("div", { class: "cp-filters" }, q, status, plan, flagSelect), listHost);
    await load();
  };

  async function tenantDetail(stage, orgId) {
    const [t, inv, appsData] = await Promise.all([api(`/api/platform/tenants/${orgId}`), api(`/api/platform/tenants/${orgId}/invoices`, { silent: true }).catch(() => ({ invoices: [] })), api(`/api/platform/tenants/${orgId}/apps`, { silent: true }).catch(() => ({ apps: [] })), plansList(true)]);
    const tenantApps = appsData.apps || [];
    const b = t.billing;
    const reload = () => Aira.router.render();
    const done = (text) => { Aira.toast(`${text} Recorded in the platform audit as ${P.me.email}.`, "success", { timeout: 7000 }); reload(); };
    const post = (path, body = {}, method = "POST") => api(`/api/platform/tenants/${orgId}/${path}`, { method, body: method === "DELETE" ? undefined : body });
    const actions = [];
    const act = (label, handler, { danger = false, show = true, admin = true } = {}) => { if (show && (!admin || isAdmin())) actions.push(h("button", { type: "button", class: `btn sm ${danger ? "danger secondary" : "secondary"}`, text: label, onclick: handler })); };

    act("Suspend", () => formModal({ title: `Suspend ${t.name}?`, intro: "Everyone in the organization is signed out and automation stops. Reinstate to restore it.", fields: [{ name: "reason", label: "Reason", type: "textarea", rows: 3 }], submit: "Suspend", danger: true, onSubmit: async (v) => { if (!v.reason) throw new Error("Give a reason."); await post("suspend", v); done("Suspended."); } }), { danger: true, show: t.org_status !== "suspended" });
    act("Reinstate", async () => { if (await Aira.confirm({ title: "Reinstate this organization?", message: "Members can sign in again and automation resumes.", confirmLabel: "Reinstate" })) { await post("reinstate"); done("Reinstated."); } }, { show: t.org_status === "suspended" });
    act("Schedule deletion", () => formModal({ title: `Schedule deletion of ${t.name}`, intro: `All rows, documents, keys and browser profiles are deleted on the chosen day; only the billing record and an audit stub remain. Type the organization's name to confirm.`, fields: [{ name: "days", label: "Delete in (days)", type: "number", value: 30, min: 1, max: 365 }, { name: "confirm", label: `Type "${t.name}"` }], submit: "Schedule deletion", danger: true, onSubmit: async (v) => { if (v.confirm !== t.name) throw new Error("The name does not match."); const r = await post("schedule-deletion", { days: v.days }); done(`Deletion scheduled for ${fmt.date(r.deletion_at)}.`); } }), { danger: true, show: !b.deletion_scheduled_at });
    act("Cancel deletion", async () => { await post("cancel-deletion"); done("Deletion cancelled."); }, { show: !!b.deletion_scheduled_at });
    act("Rate-limit", () => formModal({ title: `Rate-limit ${t.name}`, intro: "Cap the organization's requests per minute for a while (0 blocks automation as well).", fields: [{ name: "per_minute", label: "Requests per minute", type: "number", value: 60, min: 0, max: 10000 }, { name: "hours", label: "For (hours)", type: "number", value: 24, min: 1, max: 720 }], submit: "Apply limit", onSubmit: async (v) => { await post("rate-limit", v); done("Rate limit applied."); } }), { show: !t.flags.includes("rate_limited") });
    act("Clear rate limit", async () => { await post("rate-limit", {}, "DELETE"); done("Rate limit cleared."); }, { show: t.flags.includes("rate_limited") });
    act("Extend trial", () => formModal({ title: "Extend the trial", fields: [{ name: "days", label: "Days (1 to 90)", type: "number", value: 14, min: 1, max: 90 }], submit: "Extend", onSubmit: async (v) => { const r = await post("extend-trial", v); done(`Trial now ends ${fmt.date(r.trial_end)}.`); } }), { show: b.status === "trialing" || (b.status === "cancelled" && (b.plan_id === "trial" || !!b.trial_end)) });
    act("Issue credit", () => formModal({ title: "Issue a credit", fields: [{ name: "amount", label: "Amount (USD)", type: "number", value: 50, min: 1, step: "0.01" }, { name: "reason", label: "Reason", type: "textarea", rows: 2 }], submit: "Issue credit", onSubmit: async (v) => { if (!v.amount || v.amount <= 0) throw new Error("Enter an amount."); await post("credit", { amount_cents: Math.round(v.amount * 100), reason: v.reason }); done(`Credit of ${dollars(Math.round(v.amount * 100))} issued.`); } }));
    act("Set subscription", () => subscriptionForm(t, tenantApps, post, done));
    act("Activate Enterprise", () => enterpriseForm(t, post, done));
    act("Reconcile billing", async () => { const r = await post("reconcile"); Aira.modal({ title: "Reconciliation", body: h("div", {}, ui.kv([["Local state", r.local_status], ["Remote state", r.remote_status || "n/a"], ["Provider", r.provider || "none"], ["In sync", r.in_sync === undefined ? "unknown" : r.in_sync ? "Yes" : "No — investigate"], ["Error", r.error || ""]])), actions: [{ label: "Close", kind: "ghost" }] }); reload(); });
    act("Request support access", () => formModal({ title: `Ask ${t.name} for support access`, intro: "The organization's owner must approve. Access lasts at most 24 hours, shows a banner to the organization and is audited.", fields: [{ name: "reason", label: "Reason (the owner sees it)", type: "textarea", rows: 3 }, { name: "role", label: "Access", type: "select", options: [["auditor", "Read-only (auditor)"], ["admin", "With administrator rights"]] }], submit: "Send request", onSubmit: async (v) => { if (!v.reason) throw new Error("Give a reason."); await post("support-request", v); done("Support access requested."); } }), { admin: false });
    act("Export audit log", async () => { const r = await api(`/api/platform/tenants/${orgId}/audit`); download(`audit-${t.slug}.jsonl`, r.events.map((e) => JSON.stringify(e)).join("\n")); Aira.toast(`Exported ${r.events.length} events.`, "success"); });

    const usage = Object.entries(t.usage || {}).filter(([k]) => !k.includes(":")).map(([k, v]) => [titleCase(k), String(v)]);
    const grants = (t.support_access || []).map((g) => h("tr", {}, td(ts(g.requested_at)), td(g.role), td(chip(g.status, g.status === "approved" ? "warn" : "")), td(g.reason), td(g.expires_at ? ts(g.expires_at) : "—")));
    const invoices = (inv.invoices || []).map((i) => h("tr", {}, td(fmt.date(i.created_at)), td(titleCase(i.kind)), td(dollars(i.amount_cents), "num"), td(chip(i.status, i.status === "paid" ? "ok" : "warn")), td(isAdmin() && i.status !== "paid" ? h("button", { type: "button", class: "btn sm secondary", text: "Mark paid", onclick: async () => { if (await Aira.confirm({ title: "Mark this invoice paid?", message: `${dollars(i.amount_cents)} ${i.kind} invoice. A past-due organization becomes active again.`, confirmLabel: "Mark paid" })) { await post("invoice-paid", { invoice_id: i.invoice_id }); done("Invoice marked paid."); } } }) : null)));
    const audit = (t.operator_audit || []).map((e) => h("tr", {}, td(ts(e.at)), td(e.operator_label || "system"), td(e.action), td(h("code", { class: "cp-detail", text: JSON.stringify(e.detail) }))));

    put(stage, 
      h("div", { class: "page-head" }, h("div", {}, h("a", { href: "#/platform/tenants", class: "muted", text: "← All tenants" }), h("h1", { text: t.name }), h("p", { class: "muted", text: `${t.slug} · ${t.region} · created ${fmt.date(t.created_at)}` })), h("div", { class: "cp-head-state" }, statusBadge(t.status), t.org_status === "suspended" ? chip("suspended", "danger") : null, flagChips(t.flags))),
      readonlyNote(),
      h("div", { class: "grid cols-4 cp-tiles" },
        tile("Plan", planName(t.plan_id), b.status === "trialing" && b.trial_end ? `trial ends ${fmt.date(b.trial_end)}` : b.cancel_at_period_end ? "cancels at period end" : b.period_end ? `renews ${fmt.date(b.period_end)}` : ""),
        tile("Seats", `${b.seat_usage.candidates} / ${b.candidate_seats}`, `${b.seat_usage.staff} coach and admin`),
        tile("Applications this month", String(t.applications_this_month), `${Math.round((t.error_rate_24h || 0) * 100)}% errors in 24 h`),
        tile("Queue", `${t.queue.queued || 0} waiting`, `${t.queue.running || 0} running`)),
      section("Actions", actions.length ? h("div", { class: "btn-row cp-actions" }, ...actions) : h("p", { class: "muted", text: "Nothing to do here for your role." })),
      h("div", { class: "grid cols-2" },
        section("Billing", ui.kv([["State", b.status.replace("_", " ")], ["Provider", b.provider || "none"], ["Pending change", b.pending ? `${planName(b.pending.plan_id)} (${b.pending.seats} seats) on ${fmt.date(b.pending.effective_at)}` : ""], ["Past due since", b.past_due_since ? ts(b.past_due_since) : ""], ["Read-only since", b.read_only_at ? ts(b.read_only_at) : ""], ["Deletion scheduled", b.deletion_scheduled_at ? ts(b.deletion_scheduled_at) : ""], ["Export until", b.export_until ? ts(b.export_until) : ""], ["Over limits", (b.over_limits || []).join(", ")]])),
        section("Usage this month", usage.length ? ui.kv(usage) : h("p", { class: "muted", text: "No usage yet." }))),
      section("Apps", h("p", { class: "muted", text: "What this organization can open. Included apps come with its plan; an add-on is paid by the organization or granted here at no charge. Turn an app off here to close it for this organization only (its data is kept)." }),
        h("p", { class: "muted" }, h("span", { text: "To switch an app off for every organization, use " }), h("a", { href: "#/platform/apps", text: "Apps" }), h("span", { text: ". To change what a plan includes, use " }), h("a", { href: "#/platform/plans", text: "Plans" }), h("span", { text: "." })),
        appsTable(tenantApps, orgId, done)),
      section("Owners and admins", t.other_members ? h("p", { class: "muted", text: `And ${t.other_members} more members; their details stay with the organization.` }) : null, table(["Name", "Email", "Role", "Email confirmed", ""], (t.members || []).map((m) => h("tr", {}, td(m.name || "—"), td(m.email), td(titleCase(m.role)), td(m.email_verified ? chip("confirmed", "ok") : chip("not confirmed", "warn")),
        td(!m.email_verified && isAdmin() ? h("button", { type: "button", class: "btn sm secondary", text: "Confirm email", "data-confirm-email": m.member_id, onclick: async () => { await api(`/api/platform/tenants/${orgId}/members/${m.member_id}/verify-email`, { method: "POST", body: {} }); done(`${m.email} is confirmed and can sign in.`); } }) : null))), "No owners or admins.")),
      section("Policy", ui.kv(Object.entries(t.policy || {}).map(([k, v]) => [titleCase(k), Array.isArray(v) ? v.join(", ") || "none" : String(v)]))),
      section("Invoices", table(["Date", "Kind", "#Amount", "Status", ""], invoices, "No invoices.")),
      section("Support access", table(["Requested", "Role", "Status", "Reason", "Expires"], grants, "No support access requested.")),
      section("Operator actions on this tenant", table(["When", "Operator", "Action", "Detail"], audit, "No operator has acted on this tenant.")),
    );
  }

  const APP_STATUS_LEVEL = { included: "ok", active: "ok", granted: "ok", cancelling: "warn", off: "", unavailable: "danger", blocked: "danger", disabled: "danger" };
  const APP_STATUS_TEXT = { blocked: "off for this organization", disabled: "off for everyone" };
  function appsTable(apps, orgId, done) {
    const change = (a, status, message) => async (event) => {
      await Aira.withBusy(event.currentTarget, async () => {
        await api(`/api/platform/tenants/${orgId}/apps/${a.id}`, { method: "PUT", body: { status, note: "set from the admin console" } });
        done(message);
      });
    };
    const rows = apps.map((a) => {
      const buttons = [];
      if (isAdmin() && (a.status === "off" || a.status === "unavailable")) buttons.push(h("button", { type: "button", class: "btn sm secondary", text: "Grant", "data-app-grant": a.id, onclick: change(a, "granted", `${a.name} granted.`) }));
      if (isAdmin() && a.status === "granted") buttons.push(h("button", { type: "button", class: "btn sm danger secondary", text: "Take back", onclick: change(a, "revoked", `${a.name} grant taken back.`) }));
      if (isAdmin() && a.id !== "careers" && ["included", "granted", "off", "unavailable"].includes(a.status)) buttons.push(h("button", { type: "button", class: "btn sm ghost", text: "Turn off here", "data-app-off": a.id, onclick: change(a, "disabled", `${a.name} is off for this organization.`) }));
      if (isAdmin() && a.status === "blocked") buttons.push(h("button", { type: "button", class: "btn sm", text: "Turn on again", "data-app-on": a.id, onclick: change(a, "enabled", `${a.name} is back to what the plan allows.`) }));
      return h("tr", {}, td(h("strong", { text: a.name })), td(a.category === "industry" ? a.industry || "Business" : a.category === "life" ? "Everyone, every day" : "Core"), td(chip(APP_STATUS_TEXT[a.status] || a.status, APP_STATUS_LEVEL[a.status] || "")), td(a.seat_cents_month === null ? "not on this plan" : a.seat_cents_month ? `${dollars(a.seat_cents_month)} / seat` : "included", "num"), td(a.monthly_cents ? dollars(a.monthly_cents) : "—", "num"), td(buttons.length ? h("div", { class: "btn-row" }, ...buttons) : ""));
    });
    return table(["App", "For", "Status", "#Price", "#Monthly", ""], rows, "No apps.");
  }

  // The superadmin sets a subscription the customer bought outside the product (an invoice, a transfer, a contract).
  function subscriptionForm(t, apps, post, done) {
    const b = t.billing;
    const addons = apps.filter((a) => (a.state !== "included" || a.status === "granted") && a.status !== "blocked");
    const choices = planChoices({ trial: false });
    formModal({
      title: `Set ${t.name}'s subscription`,
      intro: "Use when the customer paid outside the product. The plan becomes active for the months you choose, with the seats you set. Ticked apps are granted at no extra charge; apps the organization pays for itself stay as they are.",
      wide: true,
      fields: [
        { name: "plan_id", label: "Plan", type: "select", options: choices, value: choices.some(([id]) => id === b.plan_id) ? b.plan_id : (choices.find(([id]) => id === "team") || choices[0] || [""])[0] },
        { name: "seats", label: "Candidate seats", type: "number", value: Math.max(b.candidate_seats || 0, 5), min: 1, max: 100000 },
        { name: "months", label: "Paid for (months)", type: "number", value: 12, min: 1, max: 36 },
        ...addons.map((a) => ({ name: `app_${a.id}`, label: `${a.name}${a.status === "active" ? " (paid by the organization)" : ""}`, type: "checkbox", value: ["granted", "active", "cancelling"].includes(a.status) })),
        { name: "note", label: "Note (invoice number, contract)", type: "textarea", rows: 2 },
      ],
      submit: "Set subscription",
      onSubmit: async (v) => {
        if (!v.seats || v.seats < 1) throw new Error("Enter the number of seats.");
        const chosen = {};
        for (const a of addons) chosen[a.id] = !!v[`app_${a.id}`];
        const r = await post("subscription", { plan_id: v.plan_id, seats: v.seats, months: v.months, apps: chosen, note: v.note });
        done(`Subscription set: ${planName(r.subscription.plan_id)}, ${r.subscription.candidate_seats} seats.`);
      },
    });
  }

  const tile = (label, value, sub) => h("div", { class: "stat" }, h("span", { class: "label", text: label }), h("span", { class: "value", text: value }), sub ? h("span", { class: "sub", text: sub }) : null);

  const LIMIT_KEYS = ["candidate_seats", "staff_seats", "apps_per_candidate_day", "apps_per_org_month", "search_plans_per_candidate", "cover_letters_per_month", "api_searches_per_month", "api_v1_keys", "api_v1_per_minute", "monitors_per_candidate", "storage_bytes", "retention_days", "audit_days", "coach_max_scope", "support"];
  function enterpriseForm(t, post, done) {
    formModal({
      title: `Activate Enterprise for ${t.name}`,
      intro: "Enterprise is sold by contract: set the seats and any custom limits, and record the invoice. The organization is active at once and invoiced outside the card provider.",
      wide: true,
      fields: [
        { name: "seats", label: "Candidate seats", type: "number", value: 200, min: 1 },
        { name: "period_days", label: "Contract period (days)", type: "number", value: 365, min: 1, max: 1825 },
        { name: "invoice", label: "Invoice amount (USD, optional)", type: "number", value: 0, min: 0, step: "0.01" },
        { name: "reference", label: "Invoice or purchase-order reference", maxlength: 80 },
        { name: "overrides", label: "Custom limits (JSON, optional)", type: "textarea", rows: 6, mono: true, value: "{}", hint: `Keys: ${LIMIT_KEYS.join(", ")}; features: {"features": {"add": ["siem_export"], "remove": []}}` },
      ],
      submit: "Activate Enterprise",
      onSubmit: async (v) => {
        let overrides = {};
        try { overrides = v.overrides ? JSON.parse(v.overrides) : {}; } catch (error) { throw new Error(`Custom limits are not valid JSON: ${error.message}`); }
        if (typeof overrides !== "object" || Array.isArray(overrides) || overrides === null) throw new Error("Custom limits must be a JSON object.");
        const unknown = Object.keys(overrides).filter((k) => !LIMIT_KEYS.includes(k) && k !== "features");
        if (unknown.length) throw new Error(`Unknown limit: ${unknown.join(", ")}.`);
        if (!v.seats || v.seats < 1) throw new Error("Enter the number of seats.");
        const r = await post("activate-enterprise", { seats: v.seats, period_days: v.period_days, invoice_cents: Math.round((v.invoice || 0) * 100), reference: v.reference, overrides });
        done(`Enterprise activated (${r.candidate_seats} seats).`);
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Plans
  // ---------------------------------------------------------------------------
  const MB = 1024 * 1024;
  // Every feature a plan can switch on, with the words the console shows (the server sends the list it accepts).
  const FEATURE_LABELS = {
    "sources.aira_jobs": "Job source: free job boards", "sources.company_boards": "Job source: company career boards", "sources.google_jobs": "Job source: Google Jobs (SerpApi)", "sources.linkedin": "Job source: LinkedIn",
    "automation.submit": "Automatic submission", "ai.cover_letters": "AI cover letters", "api.console_read": "API console: history", "api.searches": "API console: live searches", "api.v1": "The API (/v1) with keys",
    "auth.mfa": "Two-step sign-in (MFA)", "auth.sso": "Single sign-on (SSO)", "auth.scim": "SCIM provisioning", "coach.act": "Coaches can act", "coach.approve": "Coaches can approve and submit",
    branding: "Own name and logo in email", overage: "Metered overage (billed past the limits)", pooled_serpapi: "Searches on the platform's SerpApi key", leads_outreach: "Send lead email (outreach)",
    leads: "Leads (comes with Marketing)", "api.console": "API console (comes with the Developers app)", byo_model: "Own AI model endpoint", siem_export: "Audit stream to a SIEM", legal_hold: "Legal hold", priority_sources: "Queue priority", custom_limits: "Custom limits per organization",
  };
  const featureLabel = (feature) => FEATURE_LABELS[feature] || feature;
  const LIMIT_FIELDS = [["candidate_seats", "Candidate seats"], ["staff_seats", "Coach and admin seats"], ["apps_per_candidate_day", "Applications per candidate per day"], ["apps_per_org_month", "Applications per organization per month"], ["search_plans_per_candidate", "Search plans per candidate"], ["cover_letters_per_month", "AI cover letters per month"], ["api_searches_per_month", "API searches per month"], ["pooled_searches_per_month", "Pooled SerpApi searches per month"], ["api_v1_keys", "API keys (/v1)"], ["api_v1_per_minute", "/v1 requests per minute"], ["monitors_per_candidate", "Monitors per candidate"], ["leads_stored", "Saved leads"], ["lead_enrichments_per_month", "Lead enrichments per month"], ["lead_emails_per_month", "Outreach emails per month"], ["lead_email_daily_cap", "Outreach emails per day"], ["storage_mb", "Document storage (MB)"], ["retention_days", "Data retention (days)"], ["audit_days", "Audit log (days)"]];
  const PRICE_FIELDS = [["seat_dollars", "Price per seat per month (USD)"], ["min_seats", "Minimum seats"], ["trial_days", "Trial days (when new sign-ups start on it)"], ["application_cents", "Overage: per application (cents)"], ["search_cents", "Overage: per search (cents)"], ["cover_letter_cents", "Overage: per cover letter (cents)"], ["pooled_search_cents", "Overage: per pooled search (cents)"], ["api_search_cents", "Overage: per API search (cents)"]];
  const OVERAGE_KEYS = ["application_cents", "search_cents", "cover_letter_cents", "pooled_search_cents", "api_search_cents"];
  const APP_STATE_LABEL = { included: "Included", addon: "Add-on (per seat)", unavailable: "Not offered" };
  const signupText = (s) => `${s.plan_name}${s.trial_days ? `, as a ${s.trial_days}-day trial` : ", with no trial end"}`;
  const priceSummary = (plan) => (plan.pricing.contact_sales ? "Contact sales" : plan.pricing.seat_cents_month ? `${dollars(plan.pricing.seat_cents_month)} per seat, from ${plan.pricing.min_seats || 0}` : "Free");
  const appSummary = (plan) => {
    const apps = Object.values(plan.apps || {}).filter((a) => a.priced);
    const count = (state) => apps.filter((a) => a.state === state).length;
    return plan.plan_id === "trial" ? "Every app" : `${count("included")} included, ${count("addon")} add-on, ${count("unavailable")} not offered`;
  };

  SCREENS.plans = async (stage) => {
    const [data, versions] = await Promise.all([api("/api/platform/plans"), api("/api/platform/plans/versions", { silent: true }).catch(() => ({ versions: {} }))]);
    P.planData = data;
    const plans = data.plans;
    const order = plans.map((p) => p.plan_id);
    const move = async (planId, step) => {
      const next = order.slice();
      const at = next.indexOf(planId);
      const to = at + step;
      if (to < 0 || to >= next.length) return;
      [next[at], next[to]] = [next[to], next[at]];
      await api("/api/platform/plans/sort-order", { method: "PUT", body: { order: next } });
      Aira.toast("Order saved.", "success");
      Aira.router.render();
    };
    const cards = plans.map((plan, index) => {
      const counts = (versions.versions || {})[plan.plan_id] || {};
      const older = Object.entries(counts).filter(([v]) => Number(v) < plan.version).reduce((n, [, c]) => n + c, 0);
      const e = plan.entitlements;
      const chips = h("div", { class: "cp-chips" }, chip(plan.builtin ? "built-in" : "created"), chip(plan.public ? "on the billing page" : "private", plan.public ? "ok" : ""), plan.archived ? chip("archived", "warn") : null, plan.signup_default ? chip("new sign-ups start here", "ok") : null);
      const buttons = !isAdmin() ? [] : [
        h("button", { type: "button", class: "btn", text: "Edit", "data-plan-edit": plan.plan_id, onclick: () => planEditor(plan, counts) }),
        h("button", { type: "button", class: "btn secondary", text: "Schedule migration", onclick: () => migrationForm(plan, counts) }),
        plan.plan_id === "trial" ? null : plan.archived
          ? h("button", { type: "button", class: "btn secondary", text: "Restore", onclick: async () => { await api(`/api/platform/plans/${plan.plan_id}/restore`, { method: "POST", body: {} }); Aira.toast(`${plan.name} can be chosen again.`, "success"); Aira.router.render(); } })
          : h("button", { type: "button", class: "btn danger secondary", text: "Archive", "data-plan-archive": plan.plan_id, onclick: async () => { if (await Aira.confirm({ title: `Archive ${plan.name}?`, message: `No new organization can start on it or buy it. The ${plan.subscriptions} organization${plan.subscriptions === 1 ? "" : "s"} on it keep it until you move them (Set subscription on a tenant). You can restore it later.`, confirmLabel: "Archive", danger: true })) { await api(`/api/platform/plans/${plan.plan_id}/archive`, { method: "POST", body: {} }); Aira.toast(`${plan.name} is archived.`, "success"); Aira.router.render(); } } }),
        h("button", { type: "button", class: "btn ghost sm", text: "Move up", disabled: index === 0, onclick: () => move(plan.plan_id, -1) }),
        h("button", { type: "button", class: "btn ghost sm", text: "Move down", disabled: index === plans.length - 1, onclick: () => move(plan.plan_id, 1) }),
      ];
      return h("section", { class: `card cb-card cp-plan${plan.archived ? " cp-off" : ""}`, "data-plan": plan.plan_id },
        h("div", { class: "cp-plan-head" }, h("h2", { text: `${plan.name} · version ${plan.version}` }), h("span", { class: "muted", text: `${plan.plan_id} · ${plan.subscriptions} subscription${plan.subscriptions === 1 ? "" : "s"}${older ? `, ${older} on an older version` : ""}` })),
        chips,
        plan.description ? h("p", { class: "muted", text: plan.description }) : null,
        ui.kv([["Seats", `${e.candidate_seats ?? "no limit"} candidate, ${e.staff_seats ?? "no limit"} staff`], ["Applications", `${e.apps_per_candidate_day ?? "∞"} per candidate per day, ${e.apps_per_org_month ?? "∞"} per month`], ["Price", priceSummary(plan)], ["Trial days", plan.plan_id === "trial" ? "set under New sign-ups" : String(plan.pricing.trial_days || 0)], ["Features", `${(e.features || []).length} of ${(data.features || []).length}: ${(e.features || []).map(featureLabel).join(", ")}`], ["Apps", appSummary(plan)]]),
        h("div", { class: "btn-row" }, ...buttons.filter(Boolean)),
        plan.migrations.length ? h("div", {}, h("h3", { text: "Migrations" }), table(["Moves to", "On", "Applied"], plan.migrations.map((m) => h("tr", {}, td(`version ${m.to_version}`), td(ts(m.effective_at)), td(m.applied_at ? ts(m.applied_at) : "waiting"))))) : null);
    });
    const signup = data.signup || {};
    const signupCard = h("section", { class: "card cb-card", id: "cpSignup" }, h("h2", { text: "New sign-ups" }),
      h("p", { "data-signup": signup.plan_id, text: `A new organization starts on ${signupText(signup)}. Its members get every app and feature that plan includes. Existing organizations keep their plans.` }),
      isAdmin() ? h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", id: "cpSignupEdit", text: "Change the default plan", onclick: () => signupForm(data) })) : null);
    put(stage, pageHead("Plans", "What each plan includes and costs. Entitlements and prices publish a new version: new subscriptions use it, existing ones keep theirs until a migration moves them. The order here is the order on the billing page and decides whether a change is an upgrade or a downgrade.", isAdmin() ? h("button", { type: "button", class: "btn", id: "cpPlanNew", text: "New plan", onclick: () => planEditor(null, {}, data) }) : null), readonlyNote(), signupCard, ...cards);
  };

  function signupForm(data) {
    const live = data.plans.filter((p) => !p.archived);
    const signup = data.signup || {};
    formModal({ title: "What new sign-ups get", intro: "Choose the plan a new organization starts on. With 0 trial days it keeps that plan until someone changes it (only a free plan, such as All access); with trial days it is a trial of the plan that ends unless a plan is bought. Existing organizations are not changed.", fields: [
      { name: "plan_id", label: "Plan", type: "select", options: live.map((p) => [p.plan_id, `${p.name}${p.pricing.seat_cents_month || p.pricing.contact_sales ? "" : " (free)"}`]), value: signup.plan_id },
      { name: "trial_days", label: "Trial days (0 = no trial end)", type: "number", value: signup.trial_days ?? 0, min: 0, max: 365 },
    ], submit: "Save", onSubmit: async (v) => {
      if (v.trial_days == null || v.trial_days < 0 || v.trial_days > 365) throw new Error("Trial days are 0 to 365.");
      const r = await api("/api/platform/plans/signup-default", { method: "PUT", body: { plan_id: v.plan_id, trial_days: v.trial_days } });
      Aira.toast(`New sign-ups start on ${signupText(r.signup)}.`, "success");
      Aira.router.render();
    } });
  }

  function planFormState(plan) {
    const e = plan.entitlements, p = plan.pricing, o = p.overage || {};
    const apps = {};
    for (const [id, a] of Object.entries(plan.apps || {})) if (a.priced) apps[id] = { state: a.state, cents: a.seat_cents_month };
    return { name: plan.name || "", description: plan.description || "", public: plan.public !== false, limits: Object.fromEntries(LIMIT_FIELDS.map(([k]) => [k, k === "storage_mb" ? (e.storage_bytes == null ? null : Math.round(e.storage_bytes / MB)) : (e[k] ?? null)])), coach_max_scope: e.coach_max_scope || "view", support: e.support || "email", features: new Set(e.features || []), price: { seat_dollars: (p.seat_cents_month || 0) / 100, min_seats: p.min_seats || 0, trial_days: p.trial_days || 0, ...Object.fromEntries(OVERAGE_KEYS.map((k) => [k, o[k] ?? null])) }, apps };
  }

  /** Create a plan (``plan`` null) or edit one: limits, features, prices, apps, name and whether it is sold. */
  function planEditor(plan, counts, data = P.planData) {
    const creating = !plan;
    const all = (data && data.plans) || [];
    const base = plan || all.find((p) => p.plan_id === "starter") || all[0];
    const original = planFormState(base);
    const edit = planFormState(base);
    if (creating) { edit.name = ""; edit.description = ""; edit.price.trial_days = 0; }
    const meta = { plan_id: "", based_on: base.plan_id };
    const body = h("div", { class: "cp-editor" });
    const diffHost = h("div", { class: "cp-diff", "aria-live": "polite" });
    const publish = h("button", { type: "button", class: "btn", id: "cpPlanSave", text: creating ? "Create plan" : "Publish", disabled: !creating });

    const numberField = (store, key, label) => {
      const input = h("input", { type: "number", min: "0", step: key === "seat_dollars" ? "0.01" : "1", value: store[key] ?? "", placeholder: "no limit", id: `cpf-${key}` });
      input.addEventListener("input", () => { store[key] = input.value === "" ? null : Number(input.value); preview(); });
      return ui.field(label, input);
    };
    const textField = (label, value, onInput, attrs = {}) => {
      const input = h("input", { type: "text", value, ...attrs });
      input.addEventListener("input", () => { onInput(input.value); preview(); });
      return ui.field(label, input);
    };

    function render() {
      const about = h("div", { class: "cp-grid-form" });
      if (creating) {
        about.append(textField("Plan id (letters, digits, _)", meta.plan_id, (v) => { meta.plan_id = v.trim(); }, { id: "cpf-plan_id", placeholder: "growth", maxlength: "30" }));
        const basedOn = ui.select(all.map((p) => [p.plan_id, p.name]), meta.based_on, { id: "cpf-based_on" });
        basedOn.addEventListener("change", () => { meta.based_on = basedOn.value; const from = planFormState(all.find((p) => p.plan_id === basedOn.value)); Object.assign(edit, { limits: from.limits, coach_max_scope: from.coach_max_scope, support: from.support, features: from.features, apps: from.apps, price: { ...from.price, trial_days: edit.price.trial_days } }); render(); });
        about.append(ui.field("Start from (limits, features and apps are copied)", basedOn));
      }
      about.append(textField("Name", edit.name, (v) => { edit.name = v; }, { id: "cpf-name", maxlength: "60" }), textField("Description", edit.description, (v) => { edit.description = v; }, { id: "cpf-description", maxlength: "300" }));
      const publicBox = h("input", { type: "checkbox", id: "cpf-public", checked: edit.public });
      publicBox.addEventListener("change", () => { edit.public = publicBox.checked; preview(); });
      const publicField = (plan && plan.plan_id === "trial") ? null : h("label", { class: "check" }, publicBox, h("span", { text: "Sold on the billing page (owners can buy it when it has a seat price)" }));

      const limits = h("div", { class: "cp-grid-form" });
      for (const [key, label] of LIMIT_FIELDS) limits.append(numberField(edit.limits, key, label));
      const scope = ui.select([["view", "view"], ["act", "view, act"], ["approve", "view, act, approve"]], edit.coach_max_scope, { id: "cpf-scope" });
      scope.addEventListener("change", () => { edit.coach_max_scope = scope.value; preview(); });
      const support = ui.select([["community", "Community"], ["email", "Email"], ["email_1bd", "Email, 1 business day"], ["named_contact_sla", "Named contact, SLA"]], edit.support, { id: "cpf-support" });
      support.addEventListener("change", () => { edit.support = support.value; preview(); });
      limits.append(ui.field("Coach scopes up to", scope), ui.field("Support", support));

      const known = [...new Set([...((data && data.features) || Object.keys(FEATURE_LABELS)), ...edit.features])];
      const features = h("fieldset", { class: "cp-features" }, h("legend", { text: "Features" }));
      const allBox = h("button", { type: "button", class: "btn ghost sm", text: "Tick every feature", onclick: () => { for (const f of known) edit.features.add(f); render(); } });
      const noneBox = h("button", { type: "button", class: "btn ghost sm", text: "Clear", onclick: () => { edit.features.clear(); render(); } });
      features.append(h("div", { class: "btn-row" }, allBox, noneBox));
      for (const feature of known) {
        const box = h("input", { type: "checkbox", checked: edit.features.has(feature), "data-feature": feature });
        box.addEventListener("change", () => { if (box.checked) edit.features.add(feature); else edit.features.delete(feature); preview(); });
        features.append(h("label", { class: "check" }, box, h("span", { text: featureLabel(feature) }), h("code", { class: "muted", text: ` ${feature}` })));
      }

      const prices = h("div", { class: "cp-grid-form" }, ...PRICE_FIELDS.filter(([key]) => !(key === "trial_days" && plan && plan.plan_id === "trial")).map(([key, label]) => numberField(edit.price, key, label)));

      const appRows = [];
      const appIds = Object.keys(edit.apps);
      if (plan && plan.plan_id === "trial") appRows.push(h("p", { class: "muted", text: "The trial always has every app that is on." }));
      else {
        const setAll = (state) => { for (const id of appIds) edit.apps[id] = { state, cents: state === "addon" ? edit.apps[id].cents || 200 : state === "included" ? 0 : null }; render(); };
        appRows.push(h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", id: "cpf-apps-all", text: "Include every app", onclick: () => setAll("included") }), h("button", { type: "button", class: "btn ghost sm", text: "Offer none", onclick: () => setAll("unavailable") })));
        const rows = appIds.map((id) => {
          const a = edit.apps[id];
          const name = ((plan || base).apps[id] || {}).name || titleCase(id);
          const select = ui.select(Object.entries(APP_STATE_LABEL), a.state, { "aria-label": `${id} on this plan`, "data-app": id });
          const priceInput = h("input", { type: "number", min: "0.01", max: "1000", step: "0.01", value: a.state === "addon" && a.cents ? (a.cents / 100).toFixed(2) : "", placeholder: "USD per seat", "aria-label": `${id} price per seat`, disabled: a.state !== "addon" });
          select.addEventListener("change", () => { a.state = select.value; a.cents = a.state === "included" ? 0 : a.state === "unavailable" ? null : a.cents || 200; priceInput.disabled = a.state !== "addon"; if (a.state === "addon") priceInput.value = (a.cents / 100).toFixed(2); preview(); });
          priceInput.addEventListener("input", () => { a.cents = priceInput.value === "" ? null : Math.round(Number(priceInput.value) * 100); preview(); });
          return h("tr", {}, td(h("strong", { text: name || id })), td(select), td(priceInput));
        });
        appRows.push(table(["App", "On this plan", "Add-on price per seat (USD)"], rows, "No app can be priced."));
      }
      body.replaceChildren(h("p", { class: "muted", text: creating ? "A new plan starts as version 1. Leave a number empty for no limit." : "Leave a number empty for no limit. Limits, features and prices publish a new version; the name, description, billing page and apps change at once." }), about, publicField || "", h("h3", { text: "Limits" }), limits, features, h("h3", { text: "Pricing" }), prices, h("h3", { text: "Apps" }), ...appRows, h("h3", { text: "What will change" }), diffHost, h("div", { class: "btn-row" }, publish));
      preview();
    }

    function appChanges() {
      const out = {};
      for (const [id, a] of Object.entries(edit.apps)) {
        const before = original.apps[id] || {};
        const value = a.state === "included" ? 0 : a.state === "unavailable" ? null : a.cents;
        const was = before.state === "included" ? 0 : before.state === "unavailable" ? null : before.cents;
        if (creating || value !== was) out[id] = value;
      }
      return out;
    }
    function changes() {
      const out = [];
      if (creating) return out;
      if (original.name !== edit.name) out.push(["Name", original.name, edit.name]);
      if (original.description !== edit.description) out.push(["Description", original.description || "none", edit.description || "none"]);
      if (original.public !== edit.public) out.push(["On the billing page", original.public ? "yes" : "no", edit.public ? "yes" : "no"]);
      for (const [key, label] of LIMIT_FIELDS) if (original.limits[key] !== edit.limits[key]) out.push([label, original.limits[key] ?? "no limit", edit.limits[key] ?? "no limit"]);
      if (original.coach_max_scope !== edit.coach_max_scope) out.push(["Coach scopes up to", original.coach_max_scope, edit.coach_max_scope]);
      if (original.support !== edit.support) out.push(["Support", original.support, edit.support]);
      for (const f of edit.features) if (!original.features.has(f)) out.push([`Feature: ${featureLabel(f)}`, "off", "on"]);
      for (const f of original.features) if (!edit.features.has(f)) out.push([`Feature: ${featureLabel(f)}`, "on", "off"]);
      for (const [key, label] of PRICE_FIELDS) if (original.price[key] !== edit.price[key]) out.push([label, original.price[key] ?? "none", edit.price[key] ?? "none"]);
      for (const [id, value] of Object.entries(appChanges())) out.push([`App: ${id}`, APP_STATE_LABEL[original.apps[id].state], value === null ? "Not offered" : value === 0 ? "Included" : `${dollars(value)} / seat`]);
      return out;
    }
    function preview() {
      if (creating) { publish.disabled = false; diffHost.replaceChildren(h("p", { class: "muted", text: `A new plan with ${edit.features.size} features and ${Object.values(edit.apps).filter((a) => a.state === "included").length} included apps.` })); return; }
      const list = changes();
      publish.disabled = !list.length;
      diffHost.replaceChildren(list.length ? table(["Change", "From", "To"], list.map(([label, from, to]) => h("tr", {}, td(label), td(h("span", { class: "cp-from", text: String(from) })), td(h("span", { class: "cp-to", text: String(to) }))))) : h("p", { class: "muted", text: "No changes yet." }));
    }
    function entitlementsBody(all) {
      const out = {};
      for (const [key] of LIMIT_FIELDS) if (all || original.limits[key] !== edit.limits[key]) { if (key === "storage_mb") out.storage_bytes = edit.limits[key] == null ? null : edit.limits[key] * MB; else out[key] = edit.limits[key]; }
      if (all || original.coach_max_scope !== edit.coach_max_scope) out.coach_max_scope = edit.coach_max_scope;
      if (all || original.support !== edit.support) out.support = edit.support;
      if (all || [...edit.features].some((f) => !original.features.has(f)) || [...original.features].some((f) => !edit.features.has(f))) out.features = [...edit.features];
      return out;
    }
    function pricingBody(all) {
      const out = {};
      if (all || original.price.seat_dollars !== edit.price.seat_dollars) out.seat_cents_month = Math.round((edit.price.seat_dollars || 0) * 100);
      if (all || original.price.min_seats !== edit.price.min_seats) out.min_seats = edit.price.min_seats || 0;
      if ((all || original.price.trial_days !== edit.price.trial_days) && !(plan && plan.plan_id === "trial")) out.trial_days = edit.price.trial_days || 0;
      const overage = all ? {} : { ...((plan && plan.pricing.overage) || {}) };
      let changed = all;
      for (const key of OVERAGE_KEYS) if (all || original.price[key] !== edit.price[key]) { changed = true; if (edit.price[key] == null) delete overage[key]; else overage[key] = edit.price[key]; }
      if (changed) out.overage = overage;
      return out;
    }
    publish.addEventListener("click", (event) => Aira.withBusy(event.currentTarget, async () => {
      for (const a of Object.values(edit.apps)) if (a.state === "addon" && !(a.cents > 0 && a.cents <= 100000)) throw new Error("Give every add-on a price between $0.01 and $1,000 per seat.");
      if (creating) {
        if (!/^[a-z][a-z0-9_]{2,29}$/.test(meta.plan_id)) throw new Error("The plan id is 3 to 30 lowercase letters, digits or underscores, starting with a letter.");
        if (edit.name.trim().length < 2) throw new Error("Enter the plan's name.");
        const r = await api("/api/platform/plans", { method: "POST", body: { plan_id: meta.plan_id, name: edit.name.trim(), description: edit.description.trim(), public: edit.public, based_on: meta.based_on, entitlements: entitlementsBody(true), pricing: pricingBody(true), apps: appChanges() } });
        Aira.toast(`${r.plan.name} created.`, "success");
      } else {
        const list = changes();
        const versioned = Object.keys(entitlementsBody(false)).length || Object.keys(pricingBody(false)).length;
        if (!(await Aira.confirm({ title: versioned ? `Publish ${plan.name} version ${plan.version + 1}?` : `Save ${plan.name}?`, message: `${list.length} change${list.length === 1 ? "" : "s"}. ${versioned ? `New subscriptions use the new version at once; existing ones keep version ${plan.version} until you schedule a migration.` : "These apply at once."}`, confirmLabel: versioned ? "Publish" : "Save" }))) return;
        const payload = {};
        const ent = entitlementsBody(false), pricing = pricingBody(false), apps = appChanges();
        if (Object.keys(ent).length) payload.entitlements = ent;
        if (Object.keys(pricing).length) payload.pricing = pricing;
        if (Object.keys(apps).length) payload.apps = apps;
        if (original.name !== edit.name) payload.name = edit.name.trim();
        if (original.description !== edit.description) payload.description = edit.description.trim();
        if (original.public !== edit.public) payload.public = edit.public;
        const result = await api(`/api/platform/plans/${plan.plan_id}`, { method: "PUT", body: payload });
        Aira.toast(versioned ? `${plan.name} version ${result.version} published.` : `${plan.name} saved.`, "success");
      }
      Aira.closeDrawer();
      Aira.router.render();
    }));
    render();
    Aira.drawer({ title: creating ? "New plan" : `Edit ${plan.name}`, subtitle: h("span", { text: creating ? "Every limit, feature and app it gives" : `Currently version ${plan.version}` }), body });
  }

  function migrationForm(plan, counts) {
    const options = Array.from({ length: plan.version }, (_, i) => [String(i + 1), `Version ${i + 1}${counts[String(i + 1)] ? ` (${counts[String(i + 1)]} subscriptions)` : ""}`]);
    const tomorrow = new Date(Date.now() + 86400000);
    const local = new Date(tomorrow.getTime() - tomorrow.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    formModal({ title: `Migrate ${plan.name} subscriptions`, intro: "Existing subscriptions move to the chosen version on the date; entitlements change for them then.", fields: [{ name: "version", label: "Move subscriptions to", type: "select", options, value: String(plan.version) }, { name: "when", label: "On", type: "datetime-local", value: local }], submit: "Schedule migration", onSubmit: async (v) => {
      const when = new Date(v.when).getTime();
      if (!Number.isFinite(when) || when < Date.now() - 60000) throw new Error("Choose a time in the future.");
      await api(`/api/platform/plans/${plan.plan_id}/migrate`, { method: "POST", body: { version: Number(v.version), effective_at: Math.floor(when / 1000) } });
      Aira.toast("Migration scheduled.", "success");
      Aira.router.render();
    } });
  }

  // ---------------------------------------------------------------------------
  // Apps: switched on or off for everyone, and seat prices per plan
  // ---------------------------------------------------------------------------
  const APP_GROUPS = { core: "Core", industry: "Business", life: "Everyday" };
  const priceText = (cents) => (cents == null ? "Not offered" : cents === 0 ? "Included" : `${dollars(cents)} / seat`);
  // The plans an app is priced on: every plan but the trial (it has every app), archived ones last.
  const appPlans = () => planChoices({ trial: false, archived: true });

  SCREENS.apps = async (stage) => {
    const [{ apps }] = await Promise.all([api("/api/platform/apps"), plansList(true)]);
    const APP_PLANS = appPlans();
    const saved = (verb, app) => {
      const b = app.billing || {};
      Aira.toast(`${app.name} ${verb}.${b.organizations ? ` ${b.synced} of ${b.organizations} paying subscriptions updated${b.failed ? `; ${b.failed} failed: reconcile them from the tenant page` : ""}.` : ""}`, b.failed ? "error" : "success");
      Aira.router.render();
    };
    const save = (app, body) => api(`/api/platform/apps/${app.id}`, { method: "PUT", body });
    const rows = apps.map((app) => {
      const cells = APP_PLANS.map(([plan], index) => {
        if (!app.priced) return td(index === 0 ? "Part of every plan" : "", "muted");
        const changed = app.changed_plans.includes(plan);
        return td(h("div", {}, h("span", { text: priceText(app.prices[plan]) }), changed ? h("div", { class: "muted", text: `was ${priceText(app.default_prices[plan])}` }) : null), changed ? "cp-changed" : "");
      });
      const actions = !isAdmin() || !app.priced ? null : h("div", { class: "btn-row" },
        h("button", { type: "button", class: "btn sm secondary", text: "Prices", onclick: () => appPriceForm(app, save, saved, APP_PLANS) }),
        app.enabled
          ? h("button", { type: "button", class: "btn sm danger secondary", text: "Switch off for everyone", "data-app-kill": app.id, onclick: () => formModal({ title: `Switch off ${app.name} for every organization?`, intro: `Within a minute nobody can open it, including trials, Enterprise and grants${app.paying ? `, and the organizations paying for it (${app.paying}) stop being billed for it from their next period` : ""}. Their data and choices are kept; switching it back on restores them.`, fields: [{ name: "note", label: "Reason (kept in the platform audit log)", type: "textarea", rows: 2 }], submit: "Switch off", danger: true, onSubmit: async (v) => { if (!v.note) throw new Error("Give a reason."); saved("is off for everyone", await save(app, { enabled: false, note: v.note })); } }) })
          : h("button", { type: "button", class: "btn sm", text: "Switch on for everyone", onclick: async () => saved("is on again", await save(app, { enabled: true, note: "switched back on" })) }),
        !app.enabled || app.changed_plans.length ? h("button", { type: "button", class: "btn sm ghost", text: "Reset", onclick: async () => { if (await Aira.confirm({ title: `Reset ${app.name} to the defaults?`, message: "It is on for everyone with the standard prices; paying subscriptions move to them from their next period. What plans you created say about it is kept.", confirmLabel: "Reset" })) saved("is back to the defaults", await api(`/api/platform/apps/${app.id}`, { method: "DELETE" })); } }) : null);
      return h("tr", { class: app.enabled ? "" : "cp-off" },
        td(h("div", {}, h("strong", { text: app.name }), h("div", { class: "muted", text: `${APP_GROUPS[app.category] || titleCase(app.category)}${app.note && !app.enabled ? ` · ${app.note}` : ""}` }))),
        td(app.enabled ? chip("on", "ok") : chip("OFF for everyone", "danger")),
        ...cells,
        td(app.priced ? `${app.paying} paying · ${app.granted} granted` : "everyone", "num"),
        td(app.updated_at ? fmt.ago(app.updated_at) : "defaults"),
        td(actions));
    });
    put(stage, pageHead("Apps", "Switch an app off for every organization, or change what it costs per candidate seat on each plan. Quotes and the Apps page use a new price at once; paying subscriptions move to it from their next billing period. The trial always has every app that is on. To turn an app off for one organization only, open the tenant."), readonlyNote(),
      section(null, table(["App", "For everyone", ...APP_PLANS.map(([, label]) => label), "#Organizations", "Changed", ""], rows)));
  };

  function appPriceForm(app, save, saved, APP_PLANS) {
    const fields = [];
    for (const [plan, label] of APP_PLANS) {
      const cents = app.prices[plan];
      const fallback = app.default_prices[plan] || app.prices.team || 500;
      fields.push({ name: `${plan}_mode`, label: `${label}`, type: "select", value: cents == null ? "none" : cents === 0 ? "included" : "addon", options: [["addon", "Sold per seat"], ["included", "Included in the plan"], ["none", "Not offered"]] });
      fields.push({ name: `${plan}_price`, label: `${label} price per seat per month (USD)`, type: "number", min: 0.01, max: 1000, step: "0.01", value: ((cents || fallback) / 100).toFixed(2), hint: `Default: ${priceText(app.default_prices[plan])}` });
    }
    fields.push({ name: "note", label: "Note (kept in the platform audit log)", type: "textarea", rows: 2 });
    formModal({ title: `${app.name} prices`, intro: `Sold per seat is billed with the plan for every candidate seat. Included gives the app to every organization on that plan at no charge; Not offered hides it there (an operator can still grant it). ${app.paying ? `Paying organizations (${app.paying}) move to new prices from their next billing period.` : "No organization pays for it yet."}`, fields, submit: "Save prices", wide: true, onSubmit: async (v) => {
      const prices = {};
      for (const [plan, label] of APP_PLANS) {
        const mode = v[`${plan}_mode`];
        if (mode === "none") prices[plan] = null;
        else if (mode === "included") prices[plan] = 0;
        else {
          const amount = v[`${plan}_price`];
          if (amount == null || !(amount > 0) || amount > 1000) throw new Error(`Enter a ${label} price between $0.01 and $1,000.`);
          prices[plan] = Math.round(amount * 100);
        }
      }
      if (!APP_PLANS.some(([plan]) => prices[plan] !== app.prices[plan])) throw new Error("Nothing changed.");
      saved("prices saved", await save(app, { prices, note: v.note }));
    } });
  }

  // ---------------------------------------------------------------------------
  // Feature flags
  // ---------------------------------------------------------------------------
  const QUICK_FLAGS = [
    ["sources.linkedin", "LinkedIn search and inbox", "The riskiest source: switch it off for everyone without a deploy."],
    ["linkedin.easy_apply", "LinkedIn Easy Apply", "Automatic applications through LinkedIn."],
    ["automation.submit", "Automatic submission", "Review mode only when off."],
    ["api.v1", "The API (/v1)", "SerpApi-compatible API with organization keys."],
    ["sources.google_jobs", "Google Jobs (SerpApi)", "Searches through an organization's key."],
  ];

  SCREENS.flags = async (stage) => {
    const [{ flags }] = await Promise.all([api("/api/platform/flags"), tenantsList(), plansList(true)]);
    const rows = flags.map((f) => h("tr", {}, td(h("code", { text: f.flag })), td(f.scope_type === "global" ? chip("everyone", f.enabled ? "ok" : "danger") : chip(`${f.scope_type}: ${f.scope_type === "org" ? tenantName(f.scope_id) : f.scope_id}`)), td(f.enabled ? "on" : f.scope_type === "global" ? "OFF: kill switch" : "off"), td(f.enabled ? `${f.percent}%` : "—", "num"), td(`${f.updated_by ? "operator" : ""} ${fmt.ago(f.updated_at)}`), td(isAdmin() ? h("button", { type: "button", class: "btn sm secondary", text: "Remove", onclick: async () => { await api(`/api/platform/flags?flag=${encodeURIComponent(f.flag)}&scope_type=${f.scope_type}&scope_id=${encodeURIComponent(f.scope_id)}`, { method: "DELETE" }); Aira.toast("Flag removed.", "success"); Aira.router.render(); } }) : null)));
    const quick = QUICK_FLAGS.map(([flag, label, text]) => {
      const kill = flags.find((f) => f.flag === flag && f.scope_type === "global" && !f.enabled);
      return h("div", { class: `cp-quick ${kill ? "off" : ""}` }, h("div", {}, h("strong", { text: label }), h("div", { class: "muted", text: `${flag} — ${text}` })), kill ? h("span", { class: "cp-chip danger", text: "OFF for everyone" }) : h("span", { class: "cp-chip ok", text: "plan decides" }),
        isAdmin() ? (kill ? h("button", { type: "button", class: "btn secondary sm", text: "Roll back (turn on)", onclick: async () => { await api(`/api/platform/flags?flag=${encodeURIComponent(flag)}&scope_type=global`, { method: "DELETE" }); Aira.toast(`${flag} is back to what each plan allows.`, "success"); Aira.router.render(); } }) : h("button", { type: "button", class: "btn danger secondary sm", text: "Kill switch", onclick: async () => { if (await Aira.confirm({ title: `Switch off ${label} for every organization?`, message: "Takes effect within a minute, applies to every plan and tenant, and can be rolled back here at any time.", confirmLabel: "Switch off", danger: true })) { await api("/api/platform/flags", { method: "PUT", body: { flag, scope_type: "global", scope_id: "", enabled: false } }); Aira.toast(`${flag} is off for everyone.`, "success"); Aira.router.render(); } } })) : null);
    });
    const add = () => {
      const tenants = P.tenants || [];
      formModal({ title: "Set a feature flag", intro: "Precedence: a tenant's flag, then its plan's, then everyone's. A global flag switched OFF is a kill switch and overrides everything. A tenant or plan flag switched ON grants a feature the plan lacks (a beta).", wide: true, fields: [
        { name: "flag", label: "Flag", list: "cpFlagNames", placeholder: "sources.linkedin" }, { name: "scope_type", label: "Applies to", type: "select", options: [["global", "Everyone"], ["plan", "A plan"], ["org", "One organization"]] },
        { name: "scope_id", label: "Plan id or organization", type: "select", options: [["", "(everyone)"], ...planChoices({ archived: true }).map(([id, label]) => [id, `plan: ${label}`]), ...tenants.map((t) => [t.org_id, `org: ${t.name}`])] },
        { name: "enabled", label: "On (untick for off)", type: "checkbox", value: true }, { name: "percent", label: "Roll out to this percentage of organizations", type: "number", value: 100, min: 0, max: 100 }], submit: "Save flag", onSubmit: async (v) => {
        if (!v.flag) throw new Error("Enter a flag name.");
        if (v.scope_type !== "global" && !v.scope_id) throw new Error("Choose the plan or organization.");
        const planIds = planChoices({ archived: true }).map(([id]) => id);
        if (v.scope_type === "plan" && !planIds.includes(v.scope_id)) throw new Error("Choose a plan, not an organization.");
        if (v.scope_type === "org" && planIds.includes(v.scope_id)) throw new Error("Choose an organization, not a plan.");
        await api("/api/platform/flags", { method: "PUT", body: { flag: v.flag, scope_type: v.scope_type, scope_id: v.scope_type === "global" ? "" : v.scope_id, enabled: v.enabled, percent: v.percent ?? 100 } });
        Aira.toast(v.scope_type === "global" && !v.enabled ? `${v.flag} is off for everyone.` : "Flag saved.", "success");
        Aira.router.render();
      } });
      const list = h("datalist", { id: "cpFlagNames" }, ...[...new Set([...QUICK_FLAGS.map((q) => q[0]), ...flags.map((f) => f.flag), "source.greenhouse", "source.lever", "adapter.workday"])].map((n) => h("option", { value: n })));
      document.getElementById("overlay").append(list);
    };
    put(stage, pageHead("Feature flags", "Turn features on or off per plan, per tenant or by percentage, and roll back without a deploy.", isAdmin() ? h("button", { type: "button", class: "btn", text: "Set a flag", onclick: add }) : null), readonlyNote(), section("Quick controls", h("div", { class: "cp-quicklist" }, ...quick)), section("All flags", table(["Flag", "Scope", "State", "#Rollout", "Updated", ""], rows, "No flags are set: every plan decides for itself.")));
  };

  // ---------------------------------------------------------------------------
  // Support access
  // ---------------------------------------------------------------------------
  SCREENS.support = async (stage) => {
    const { grants } = await api("/api/platform/support");
    const now = Date.now() / 1000;
    const rows = grants.map((g) => {
      const live = g.status === "approved" && g.expires_at > now && !g.ended_at;
      return h("tr", {}, td(h("strong", { text: g.org_name })), td(g.role === "auditor" ? "read-only" : "admin"), td(chip(live ? "approved" : g.status, live ? "warn" : g.status === "denied" ? "danger" : "")), td(g.reason), td(ts(g.requested_at)), td(g.expires_at ? ts(g.expires_at) : "—"),
        td(h("div", { class: "btn-row" }, live ? h("button", { type: "button", class: "btn sm", text: "Enter tenant", onclick: (e) => Aira.withBusy(e.currentTarget, () => enterTenant(g)) }) : null, live || g.status === "requested" ? h("button", { type: "button", class: "btn sm secondary", text: g.status === "requested" ? "Withdraw" : "End access", onclick: async () => { await api(`/api/platform/support/${g.access_id}/end`, { method: "POST", body: {} }); Aira.toast("Access ended.", "success"); Aira.router.render(); } }) : null)));
    });
    put(stage, pageHead("Support access", "Time-boxed, owner-approved access to a tenant. You see the tenant's screens with the granted role for at most 24 hours; the tenant sees a banner and everything is audited.", h("a", { class: "btn secondary", href: "#/platform/tenants", text: "Request access from a tenant" })), h("div", { class: "cb-note info", text: "Request access from a tenant's page. The organization's owner approves it in their Organization → Support screen." }), section("My grants", table(["Organization", "Access", "Status", "Reason", "Requested", "Expires", ""], rows, "You have not requested support access yet.")));
  };

  async function enterTenant(grant) {
    const result = await api(`/api/platform/support/${grant.access_id}/enter`, { method: "POST", body: {} });
    sessionStorage.setItem(SUPPORT_KEY, JSON.stringify({ access_id: grant.access_id, org_id: result.org_id, org_name: grant.org_name, role: result.role, expires_at: result.expires_at }));
    // Reload into the tenant's own screens: the session now behaves as the granted role in that organization.
    location.hash = "#/org";
    location.reload();
  }

  function renderSupportBar() {
    document.getElementById("cpSupportBar")?.remove();
    clearInterval(P.supportTimer);
    let grant = null;
    try { grant = JSON.parse(sessionStorage.getItem(SUPPORT_KEY) || "null"); } catch { grant = null; }
    if (!grant) return;
    const bar = h("div", { id: "cpSupportBar", class: "cp-support-bar", role: "status" }, h("strong", { text: `Support access to ${grant.org_name}` }), h("span", { id: "cpSupportUntil", text: `until ${fmt.datetime(grant.expires_at)} (${grant.role === "auditor" ? "read-only" : "admin"})` }), h("button", { type: "button", class: "btn secondary sm", id: "cpSupportLeave", text: "Leave", onclick: leaveTenant }));
    document.body.prepend(bar);
    document.body.classList.add("cp-has-support");
    P.supportTimer = setInterval(() => { if (Date.now() / 1000 > grant.expires_at) { toastExpired(); leaveTenant(); } }, 15000);
  }

  function toastExpired() { try { Aira.toast("Support access expired.", "error"); } catch { /* ignore */ } }

  async function leaveTenant() {
    try { await fetch("/api/platform/support/leave", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "aira" }, body: "{}" }); } catch { /* the grant expires on its own */ }
    sessionStorage.removeItem(SUPPORT_KEY);
    document.body.classList.remove("cp-has-support");
    location.hash = "#/platform/support";
    location.reload();
  }

  // On every page load (member screens included) keep the banner honest: it shows only while the grant is live.
  document.addEventListener("DOMContentLoaded", async () => {
    let grant = null;
    try { grant = JSON.parse(sessionStorage.getItem(SUPPORT_KEY) || "null"); } catch { grant = null; }
    if (!grant) return;
    try {
      const response = await fetch("/api/platform/me", { credentials: "same-origin", headers: { "X-Requested-With": "aira" } });
      const me = response.ok ? await response.json() : null;
      if (!me || !me.support || me.support.access_id !== grant.access_id) { sessionStorage.removeItem(SUPPORT_KEY); return; }
    } catch { return; }
    if (!P.active) renderSupportBar();
  });

  // ---------------------------------------------------------------------------
  // Social moderation (one network for the whole platform, so its reports are Aira's)
  // ---------------------------------------------------------------------------
  SCREENS.moderation = async (stage) => {
    const status = P.modStatus || "open";
    const { reports, blocklist } = await api(`/api/platform/social/reports?status=${status}`);
    const who = (person) => (person ? `${person.display_name || ""}${person.handle ? ` @${person.handle}` : ""}` : "—");
    const act = async (report, action) => {
      await api(`/api/platform/social/reports/${report.id}`, { method: "POST", body: { action } });
      Aira.toast(action === "dismiss" ? "Report dismissed." : action === "hide" ? "Hidden for everyone." : "Account suspended.", "success");
      Aira.router.render();
    };
    const buttons = (r) => status !== "open" ? chip(r.action || r.status) : h("div", { class: "btn-row" },
      r.target_type === "account" ? null : h("button", { type: "button", class: "btn sm secondary", text: "Hide", onclick: () => act(r, "hide") }),
      r.author ? h("button", { type: "button", class: "btn sm danger secondary", text: "Suspend author", onclick: () => { if (confirm(`Suspend ${who(r.author)} on Social?`)) act(r, "suspend"); } }) : null,
      h("button", { type: "button", class: "btn sm secondary", text: "Dismiss", onclick: () => act(r, "dismiss") }));
    const rows = reports.map((r) => h("tr", {}, td(ts(Date.parse(r.created_at) / 1000 || r.created_at)), td(chip(r.target_type)), td(h("strong", { text: r.reason || "reported" })), td(h("div", { class: "cp-preview", text: r.preview || "(nothing to preview)" })), td(who(r.author)), td(who(r.reporter)), td(buttons(r))));
    const filter = h("div", { class: "btn-row" }, ...["open", "resolved", "dismissed"].map((name) => h("button", { type: "button", class: `btn sm ${name === status ? "" : "secondary"}`, text: titleCase(name), onclick: () => { P.modStatus = name; Aira.router.render(); } })));
    const editWords = () => formModal({ title: "Blocked words", intro: "These are masked with asterisks in posts, comments and messages. One word or phrase per line (up to 40 characters).", fields: [{ name: "words", label: "Words", type: "textarea", rows: 10, value: (blocklist || []).join(String.fromCharCode(10)) }], onSubmit: async (v) => {
      await api("/api/platform/social/blocklist", { method: "PUT", body: { words: String(v.words || "").split(/[\r\n]+/).map((w) => w.trim()).filter(Boolean) } });
      Aira.toast("Blocked words saved.", "success");
      Aira.router.render();
    } });
    put(stage, pageHead("Social moderation", "Reports from everyone on Monosage Social. Hide what breaks the rules, suspend people who keep doing it, dismiss the rest. Every action is in the platform audit log.", h("button", { type: "button", class: "btn secondary", text: `Blocked words (${(blocklist || []).length})`, onclick: editWords })),
      section("", filter, table(["Reported", "What", "Reason", "Content", "Author", "Reported by", ""], rows, status === "open" ? "Nothing to review. Reports appear here when someone flags a post, comment, message, story or profile." : `No ${status} reports.`)));
  };

  // ---------------------------------------------------------------------------
  // Videos moderation (one catalog for the whole platform: reports and copyright notices are Aira's)
  // ---------------------------------------------------------------------------
  const NOTICE_LABEL = { received: "Received", taken_down: "Taken down", rejected: "Rejected", counter_filed: "Counter-notice", restored: "Back up", upheld: "Kept down" };
  SCREENS.videos = async (stage) => {
    const status = P.videoStatus || "open";
    const [{ reports }, { notices }] = await Promise.all([api(`/api/platform/videos/reports?status=${status}`), api("/api/platform/videos/copyright")]);
    const who = (channel) => (channel ? channel.name : "—");
    const act = async (report, action) => {
      await api(`/api/platform/videos/reports/${report.id}`, { method: "POST", body: { action } });
      Aira.toast({ hide: "Hidden for everyone.", restore: "Restored.", suspend: "Channel suspended.", dismiss: "Report dismissed." }[action], "success");
      Aira.router.render();
    };
    const buttons = (r) => status !== "open" ? chip(r.action || r.status) : h("div", { class: "btn-row" },
      r.target_type === "channel" ? null : h("button", { type: "button", class: "btn sm secondary", text: r.hidden ? "Restore" : "Hide", onclick: () => act(r, r.hidden ? "restore" : "hide") }),
      r.author ? h("button", { type: "button", class: "btn sm danger secondary", text: "Suspend channel", onclick: () => { if (confirm(`Suspend ${who(r.author)} on Videos?`)) act(r, "suspend"); } }) : null,
      h("button", { type: "button", class: "btn sm secondary", text: "Dismiss", onclick: () => act(r, "dismiss") }));
    const rows = reports.map((r) => h("tr", {}, td(ts(Date.parse(r.created_at) / 1000 || r.created_at)), td(chip(r.target_type)), td(h("strong", { text: r.reason || "reported" })), td(h("div", { class: "cp-preview", text: r.preview || "(nothing to preview)" })), td(who(r.author)), td(who(r.reporter)), td(buttons(r))));
    const filter = h("div", { class: "btn-row" }, ...["open", "resolved", "dismissed"].map((name) => h("button", { type: "button", class: `btn sm ${name === status ? "" : "secondary"}`, text: titleCase(name), onclick: () => { P.videoStatus = name; Aira.router.render(); } })));
    const decide = (notice, action) => formModal({ title: { takedown: "Take the video down", reject: "Reject the notice", restore: "Put the video back", uphold: "Keep the video down" }[action], intro: `${notice.video.title}: claimed by ${notice.claimant_name}${notice.claimant_organization ? ` for ${notice.claimant_organization}` : ""} (${notice.claimant_email}).`, fields: [{ name: "note", label: "Note (the uploader sees it)", type: "textarea", rows: 2 }], submit: "Confirm", danger: action === "takedown" || action === "uphold", onSubmit: async (v) => {
      await api(`/api/platform/videos/copyright/${notice.id}`, { method: "POST", body: { action, note: v.note || "" } });
      Aira.toast("Done.", "success");
      Aira.router.render();
    } });
    const noticeRows = notices.map((n) => h("tr", {}, td(ts(Date.parse(n.created_at) / 1000 || n.created_at)), td(h("strong", { text: n.video.title })), td(`${n.claimant_name} · ${n.claimant_email}`), td(h("div", { class: "cp-preview", text: n.work })), td(n.counter_text ? h("div", { class: "cp-preview", text: `${n.counter_name}: ${n.counter_text}` }) : "—"), td(`${who(n.channel)}${n.strikes ? ` (${n.strikes} strikes)` : ""}`), td(chip(NOTICE_LABEL[n.status] || n.status, n.status === "taken_down" || n.status === "upheld" ? "danger" : n.status === "received" || n.status === "counter_filed" ? "warn" : "")),
      td(h("div", { class: "btn-row" },
        n.status === "received" ? h("button", { type: "button", class: "btn sm danger secondary", text: "Take down", onclick: () => decide(n, "takedown") }) : null,
        n.status === "received" ? h("button", { type: "button", class: "btn sm secondary", text: "Reject", onclick: () => decide(n, "reject") }) : null,
        n.status === "counter_filed" || n.status === "taken_down" ? h("button", { type: "button", class: "btn sm secondary", text: "Put back", onclick: () => decide(n, "restore") }) : null,
        n.status === "counter_filed" ? h("button", { type: "button", class: "btn sm danger secondary", text: "Keep down", onclick: () => decide(n, "uphold") }) : null))));
    put(stage, pageHead("Videos moderation", "Reports from everyone on Monosage Videos, and copyright notices from the public form. Hide what breaks the rules, suspend channels that keep doing it, take down copied work and handle counter-notices. Every action is in the platform audit log."),
      section("Reports", filter, table(["Reported", "What", "Reason", "Content", "Channel", "Reported by", ""], rows, status === "open" ? "Nothing to review. Reports appear here when someone flags a video, comment or channel." : `No ${status} reports.`)),
      section("Copyright notices", h("p", { class: "muted", text: `Anyone can send one from ${location.origin}/videos/copyright. A takedown is a strike for 90 days; three stop a channel uploading.` }), table(["Received", "Video", "Claimant", "Their work", "Counter-notice", "Channel", "Status", ""], noticeRows, "No copyright notices.")));
  };

  // ---------------------------------------------------------------------------
  // Creators & ads (the books, ad review, payouts; money changes are platform admins')
  // ---------------------------------------------------------------------------
  const micros = (value) => { const n = Number(value || 0) / 1e6; return n > 0 && n < 0.01 ? `$${n.toFixed(4)}` : dollars(Math.round(n * 100)); };
  SCREENS.creators = async (stage) => {
    const [overview, review] = await Promise.all([api("/api/platform/ads/overview"), api("/api/platform/ads/review")]);
    const t = overview.totals, money = overview.can_manage_money;
    const kpi = (label, value, hint) => h("div", { class: "suite-kpi" }, h("span", { class: "suite-kpi-label", text: label }), h("strong", { class: "suite-kpi-value", text: value }), hint ? h("span", { class: "suite-kpi-hint", text: hint }) : null);
    const kpis = h("div", { class: "suite-kpis" },
      kpi("Gross ad revenue", micros(t.gross_micros), "what advertisers were charged"),
      kpi("Creator earnings", micros(t.creators_micros), `${t.settings.revenue_share_percent}% share`),
      kpi("Platform revenue", micros(t.platform_micros), "what Monosage keeps"),
      kpi("Estimated, not yet confirmed", micros(t.estimated_micros)),
      kpi("Payouts waiting", micros(t.requested_micros), `${t.payouts_waiting} requests`),
      kpi("Paid to creators", micros(t.paid_micros)),
      kpi("Refunded for invalid traffic", micros(t.refunded_micros)),
      kpi("Advertiser balances", micros(t.balances_micros), `${micros(t.topped_up_micros)} topped up in all`));
    const settings = money ? h("button", { type: "button", class: "btn secondary", text: "Share and minimum payout", onclick: () => formModal({ title: "Creators' share and payouts", intro: "The share applies to charges from now on; earnings already credited keep theirs.", fields: [{ name: "share", label: "Creators' share of each ad (percent, 0 to 90)", type: "number", min: 0, max: 90, value: t.settings.revenue_share_percent }, { name: "minimum", label: "Smallest payout (USD)", type: "number", min: 1, max: 1000, step: "0.01", value: (t.settings.min_payout_micros / 1e6).toFixed(2) }], onSubmit: async (v) => {
      await api("/api/platform/ads/settings", { method: "PUT", body: { revenue_share_percent: Number(v.share), min_payout_cents: Math.round(Number(v.minimum) * 100) } });
      Aira.toast("Saved.", "success");
      Aira.router.render();
    } }) }) : null;
    const reviewRows = review.creatives.map((c) => h("tr", {}, td(h("div", {}, h("strong", { text: c.headline }), h("div", { class: "muted", text: `${c.kind_label} · ${c.cta}${c.body ? ` · ${c.body}` : ""}` }))), td(c.advertiser), td(c.mime.startsWith("video/") ? h("video", { src: c.media_url, controls: true, muted: true, preload: "metadata", style: { maxWidth: "220px", maxHeight: "140px" }, "aria-label": c.headline }) : h("img", { src: c.media_url, alt: c.headline, style: { maxWidth: "160px", maxHeight: "120px" } })), td(h("div", {}, h("div", { text: c.campaign.landing_url }), h("div", { class: "muted", text: `${c.campaign.family_safe ? "says family-safe · " : ""}${c.campaign.allow_mature ? "on adult videos too · " : ""}${(c.campaign.countries || []).join(", ") || "all countries"}` }))),
      td(h("div", { class: "btn-row" },
        h("button", { type: "button", class: "btn sm", text: "Approve", onclick: async () => { await api(`/api/platform/ads/creatives/${c.id}/review`, { method: "POST", body: { decision: "approve" } }); Aira.toast("Approved: it can run now.", "success"); Aira.router.render(); } }),
        h("button", { type: "button", class: "btn sm danger secondary", text: "Reject", onclick: () => formModal({ title: "Reject this ad", fields: [{ name: "note", label: "What the advertiser must change", type: "textarea", rows: 3 }], submit: "Reject", danger: true, onSubmit: async (v) => { if (!v.note) throw new Error("Tell the advertiser why."); await api(`/api/platform/ads/creatives/${c.id}/review`, { method: "POST", body: { decision: "reject", note: v.note } }); Aira.router.render(); } }) })))));
    const payoutRows = overview.payouts.map((p) => h("tr", {}, td(ts(Date.parse(p.requested_at) / 1000 || p.requested_at)), td(p.channel ? p.channel.name : "Removed"), td(micros(p.amount_micros), "num"), td(h("div", { class: "cp-preview", text: p.details })), td(micros((p.balances || {}).payable_micros), "num"),
      td(money ? h("div", { class: "btn-row" },
        h("button", { type: "button", class: "btn sm", text: "Mark paid", onclick: () => formModal({ title: `Paid ${micros(p.amount_micros)}?`, intro: "Pay it outside Monosage (bank transfer, UPI), then give the reference here. The creator is told.", fields: [{ name: "reference", label: "Payment reference" }], submit: "Mark paid", onSubmit: async (v) => { if (!v.reference) throw new Error("Give the payment reference."); await api(`/api/platform/ads/payouts/${p.id}`, { method: "POST", body: { action: "paid", reference: v.reference } }); Aira.router.render(); } }) }),
        h("button", { type: "button", class: "btn sm secondary", text: "Decline", onclick: () => formModal({ title: "Decline this payout", fields: [{ name: "note", label: "Why (the creator sees it)", type: "textarea", rows: 2 }], submit: "Decline", danger: true, onSubmit: async (v) => { if (!v.note) throw new Error("Say why."); await api(`/api/platform/ads/payouts/${p.id}`, { method: "POST", body: { action: "reject", note: v.note } }); Aira.router.render(); } }) })) : null)));
    const monthRows = t.months.map((m) => h("tr", {}, td(m.month), td(micros(m.gross_micros), "num"), td(micros(m.creators_micros), "num"), td(micros(m.platform_micros), "num"), td(String(m.impressions), "num"), td(String(m.invalid), "num"),
      td(m.closed ? chip("closed", "ok") : money ? h("button", { type: "button", class: "btn sm secondary", text: "Close month", onclick: async () => { if (await Aira.confirm({ title: `Close ${m.month}?`, message: "Its traffic is reviewed once more (too many ads from one viewer to one channel in a day are refunded), then creators' earnings are confirmed and can be paid.", confirmLabel: "Close month" })) { const r = await api("/api/platform/ads/close-month", { method: "POST", body: { month: m.month } }); Aira.toast(`${r.month} closed: ${r.invalid} impressions refunded.`, "success"); Aira.router.render(); } } }) : chip("open"))));
    const clawback = money ? h("button", { type: "button", class: "btn secondary danger", text: "Claw back earnings", onclick: () => formModal({ title: "Take a channel's earnings back", intro: "Its counted ads for that month stop counting; advertisers are refunded and the creator is told.", fields: [{ name: "channel_id", label: "Channel id" }, { name: "month", label: "Month (YYYY-MM)" }, { name: "reason", label: "Reason", type: "textarea", rows: 2 }], submit: "Claw back", danger: true, onSubmit: async (v) => { const r = await api("/api/platform/ads/clawback", { method: "POST", body: v }); Aira.toast(`${r.impressions} impressions taken back.`, "success"); Aira.router.render(); } }) }) : null;
    put(stage, pageHead("Creators & ads", "Advertisers prepay their balance (billed through their own subscription), ads are reviewed here before they run, and each counted impression or click pays the video's creator a share. Every change is in the platform audit log.", ...[settings, clawback].filter(Boolean)), readonlyNote(),
      section(null, kpis),
      section(`Ads to review (${review.creatives.length})`, table(["Ad", "Advertiser", "Media", "Landing page and targeting", ""], reviewRows, "Nothing to review.")),
      section(`Payout requests (${overview.payouts.length})`, table(["Asked", "Creator", "#Amount", "Pay to", "#Payable", ""], payoutRows, "No payout requests.")),
      section("Months", table(["Month", "#Gross", "#Creators", "#Platform", "#Impressions", "#Not counted", ""], monthRows, "No ads have run yet.")));
  };

  // ---------------------------------------------------------------------------
  // Announcements
  // ---------------------------------------------------------------------------
  SCREENS.announcements = async (stage) => {
    await Promise.all([tenantsList(), plansList(true)]);
    const { announcements } = await api("/api/platform/announcements");
    const now = Date.now() / 1000;
    const state = (a) => (a.cancelled_at ? "cancelled" : a.ends_at && a.ends_at < now ? "ended" : "live");
    const rows = announcements.map((a) => h("tr", {}, td(h("strong", { text: a.title })), td(a.body), td(chip(a.severity, a.severity === "info" ? "" : a.severity === "danger" ? "danger" : "warn")), td(a.scope_type === "all" ? "everyone" : a.scope_type === "plan" ? `plan ${a.scope_id}` : tenantName(a.scope_id)), td(chip(state(a), state(a) === "live" ? "ok" : "")), td(a.ends_at ? ts(a.ends_at) : "until cancelled"), td(isAdmin() || P.me.role === "support" ? (state(a) === "live" ? h("button", { type: "button", class: "btn sm secondary", text: "Cancel", onclick: async () => { await api(`/api/platform/announcements/${a.announcement_id}`, { method: "DELETE" }); Aira.toast("Announcement cancelled.", "success"); Aira.router.render(); } }) : null) : null)));
    const compose = () => formModal({ title: "New announcement", intro: "Shown to every organization's members in their banner area (or one plan, or one organization).", fields: [
      { name: "title", label: "Title", maxlength: 160 }, { name: "body", label: "Message", type: "textarea", rows: 3 }, { name: "severity", label: "Severity", type: "select", options: [["info", "Information"], ["warning", "Warning"], ["danger", "Outage"]] },
      { name: "scope_type", label: "Audience", type: "select", options: [["all", "Everyone"], ["plan", "One plan"], ["org", "One organization"]] },
      { name: "scope_id", label: "Plan or organization", type: "select", options: [["", "(everyone)"], ...planChoices().map(([id, label]) => [id, `plan: ${label}`]), ...(P.tenants || []).map((t) => [t.org_id, `org: ${t.name}`])] },
      { name: "hours", label: "Show for (hours; empty = until cancelled)", type: "number", min: 1, max: 720 }], submit: "Publish", onSubmit: async (v) => {
      if (!v.title) throw new Error("Enter a title.");
      if (v.scope_type !== "all" && !v.scope_id) throw new Error("Choose the plan or organization.");
      await api("/api/platform/announcements", { method: "POST", body: { title: v.title, body: v.body, severity: v.severity, scope_type: v.scope_type, scope_id: v.scope_type === "all" ? "" : v.scope_id, hours: v.hours || null } });
      Aira.toast("Announcement published.", "success");
      Aira.router.render();
    } });
    put(stage, pageHead("Announcements", "In-app notices for maintenance or a source outage.", h("button", { type: "button", class: "btn", text: "New announcement", onclick: compose })), section("", table(["Title", "Message", "Severity", "Audience", "State", "Ends", ""], rows, "No announcements yet.")));
  };

  // ---------------------------------------------------------------------------
  // Health
  // ---------------------------------------------------------------------------
  const bar = (fraction, level = "") => h("span", { class: `cp-bar ${level}`, role: "img", "aria-label": `${Math.round(fraction * 100)} percent` }, h("i", { style: { width: `${Math.max(0, Math.min(1, fraction)) * 100}%` } }));
  const seconds = (n) => (n < 90 ? `${n} s` : n < 5400 ? `${Math.round(n / 60)} min` : `${(n / 3600).toFixed(1)} h`);

  SCREENS.health = async (stage) => {
    await tenantsList();
    const host = h("div", { class: "cp-stack" });
    put(stage, pageHead("Health", "Queue, workers, application success by employer system, source errors, model latency and per-tenant error budgets. Refreshes every 15 seconds."), h("div", { class: "muted", id: "cpHealthStamp", "aria-live": "off" }), host);
    async function paint() {
      const [health, metrics] = await Promise.all([api("/api/platform/health", { silent: true }), api("/api/platform/metrics", { silent: true }).catch(() => ({ per_tenant: {} }))]);
      const w = health.workers, q = health.queue, m = health.model_latency_ms;
      const busiest = Object.entries(metrics.per_tenant || {}).sort((a, b) => b[1].requests - a[1].requests).slice(0, 8);
      host.replaceChildren(
        h("div", { class: "grid cols-4 cp-tiles" }, tile("Queue depth", String(q.depth), `${q.running} running`), tile("Oldest wait", seconds(q.oldest_wait_seconds), q.oldest_wait_seconds > 300 ? "workers are behind" : "keeping up"), tile("Workers", String(w.count), w.capacity ? `${w.busy} of ${w.capacity} busy` : "none registered"), tile("Model latency", m.p50 == null ? "—" : `${m.p50} ms`, m.p95 == null ? "no calls yet" : `p95 ${m.p95} ms · ${m.calls} calls`)),
        section("Worker utilisation", w.utilisation == null ? h("p", { class: "muted", text: "No workers have reported in the last minute." }) : h("div", { class: "cp-row" }, bar(w.utilisation, w.utilisation > 0.9 ? "bad" : w.utilisation > 0.7 ? "warn" : ""), h("strong", { text: `${Math.round(w.utilisation * 100)}%` })), q.by_kind.length ? table(["Job kind", "State", "#Count"], q.by_kind.map((k) => h("tr", {}, td(k.kind), td(k.status === "leased" ? "running" : "waiting"), td(String(k.n), "num")))) : null),
        h("div", { class: "grid cols-2" },
          section("Application success by employer system (24 h)", health.apply_success_by_ats.length ? table(["System", "#Runs", "Success"], health.apply_success_by_ats.map((r) => h("tr", {}, td(r.ats), td(String(r.runs), "num"), td(h("div", { class: "cp-row" }, bar(r.success_rate, r.success_rate < 0.5 ? "bad" : r.success_rate < 0.8 ? "warn" : ""), h("span", { text: `${Math.round(r.success_rate * 100)}%` })))))) : h("p", { class: "muted", text: "No applications in the last day." })),
          section("Source error rates (24 h)", health.source_error_rates.length ? table(["Source", "#Calls", "Errors"], health.source_error_rates.map((r) => h("tr", {}, td(r.source), td(String(r.calls), "num"), td(h("div", { class: "cp-row" }, bar(r.error_rate, r.error_rate > 0.25 ? "bad" : r.error_rate > 0.1 ? "warn" : ""), h("span", { text: `${Math.round(r.error_rate * 100)}%` })))))) : h("p", { class: "muted", text: "No searches in the last day." }))),
        section("Per-tenant error budgets", health.tenant_error_budgets.length ? table(["Organization", "#Runs", "#Errors", "Error rate"], health.tenant_error_budgets.map((r) => h("tr", {}, td(h("a", { href: `#/platform/tenants/${r.org_id}`, text: tenantName(r.org_id) })), td(String(r.total), "num"), td(String(r.errors), "num"), td(h("div", { class: "cp-row" }, bar(r.rate, r.over_budget ? "bad" : "warn"), h("span", { text: `${Math.round(r.rate * 100)}%${r.over_budget ? " · over budget" : ""}` })))))) : h("p", { class: "muted", text: "No tenant has run work in the last day." })),
        h("div", { class: "grid cols-2" },
          section("Busiest tenants (requests since the web tier started)", busiest.length ? table(["Organization", "#Requests", "#Errors", "#Rate-limited"], busiest.map(([id, v]) => h("tr", {}, td(tenantName(id)), td(String(v.requests), "num"), td(String(v.errors), "num"), td(String(v.limited), "num")))) : h("p", { class: "muted", text: "No requests yet." })),
          section("Sources blocked for everyone", health.blocked_sources.length ? table(["Source", "Reason", "Since"], health.blocked_sources.map((s) => h("tr", {}, td(h("code", { text: s.source })), td(s.reason || "—"), td(ts(s.blocked_at))))) : h("p", { class: "muted", text: "No source is blocked." }))));
      const stamp = document.getElementById("cpHealthStamp");
      if (stamp) stamp.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    }
    await paint();
    const timer = setInterval(() => { if (!stage.isConnected) { clearInterval(timer); return; } paint().catch(() => {}); }, 15000);
  };

  // ---------------------------------------------------------------------------
  // Audit
  // ---------------------------------------------------------------------------
  SCREENS.audit = async (stage) => {
    await tenantsList();
    const state = { events: [], done: false };
    const org = ui.select([["", "All organizations and platform"], ...(P.tenants || []).map((t) => [t.org_id, t.name])], "", { "aria-label": "Organization" });
    const text = h("input", { type: "search", "data-search": "", placeholder: "Filter by action or operator", "aria-label": "Filter" });
    const host = h("div", {});
    const more = h("button", { type: "button", class: "btn secondary", text: "Load older" });
    const verifyHost = h("div", { "aria-live": "polite" });
    async function load(reset) {
      if (reset) { state.events = []; state.done = false; }
      const params = new URLSearchParams({ limit: "100" });
      if (org.value) params.set("org", org.value);
      if (!reset && state.events.length) params.set("before", String(Math.min(...state.events.map((e) => e.seq))));
      const { events } = await api(`/api/platform/audit?${params}`);
      state.events = reset ? events : [...state.events, ...events];
      state.done = events.length < 100;
      paint();
    }
    function paint() {
      const needle = text.value.toLowerCase();
      const shown = state.events.filter((e) => !needle || `${e.action} ${e.operator_label}`.toLowerCase().includes(needle));
      host.replaceChildren(table(["When", "Operator", "Organization", "Action", "Target", "Detail"], shown.map((e) => h("tr", {}, td(ts(e.at)), td(e.operator_label || e.source), td(e.org_id ? tenantName(e.org_id) : "platform"), td(h("code", { text: e.action })), td(e.target_type ? `${e.target_type} ${String(e.target_id).slice(0, 12)}` : "—"), td(h("code", { class: "cp-detail", text: JSON.stringify(e.detail) })))), "No matching events."), state.done ? h("p", { class: "muted", text: "That is every event." }) : more);
    }
    org.addEventListener("change", () => load(true));
    text.addEventListener("input", paint);
    more.addEventListener("click", (event) => Aira.withBusy(event.currentTarget, () => load(false)));
    const verify = h("button", { type: "button", class: "btn secondary", text: "Verify the chain", onclick: (event) => Aira.withBusy(event.currentTarget, async () => { const r = await api("/api/platform/audit/verify"); verifyHost.replaceChildren(h("div", { class: `cb-note ${r.ok ? "ok" : "danger"}`, role: r.ok ? "status" : "alert", text: r.ok ? `The chain is intact: ${r.checked} events verified.` : `The chain is broken at event ${r.broken_at} (of ${r.checked} checked).` })); }) });
    put(stage, pageHead("Platform audit", "Every operator action, kept for seven years, apart from the tenants' own logs. Each row is chained to the one before it.", verify), verifyHost, h("div", { class: "cp-filters" }, org, text), host);
    await load(true);
  };

  // ---------------------------------------------------------------------------
  // Abuse and compliance
  // ---------------------------------------------------------------------------
  SCREENS.abuse = async (stage) => {
    const [health] = await Promise.all([api("/api/platform/health"), tenantsList()]);
    const blocked = health.blocked_sources;
    const blockForm = () => formModal({ title: "Block a source for everyone", intro: "Use when an employer system changes its form or a board asks us to stop. Searches and applications on it stop for every organization within a minute. Unblock to restore.", fields: [{ name: "source", label: "Source", list: "cpSources", placeholder: "greenhouse" }, { name: "reason", label: "Reason", type: "textarea", rows: 2 }], submit: "Block source", danger: true, onSubmit: async (v) => {
      if (!v.source) throw new Error("Enter the source name.");
      await api("/api/platform/sources/block", { method: "POST", body: { source: v.source, reason: v.reason } });
      Aira.toast(`${v.source} is blocked for every organization.`, "success");
      Aira.router.render();
    } });
    const rateForm = () => formModal({ title: "Rate-limit an organization", fields: [{ name: "org", label: "Organization", type: "select", options: (P.tenants || []).map((t) => [t.org_id, t.name]) }, { name: "per_minute", label: "Requests per minute", type: "number", value: 60, min: 0, max: 10000 }, { name: "hours", label: "For (hours)", type: "number", value: 24, min: 1, max: 720 }], submit: "Apply limit", onSubmit: async (v) => {
      if (!v.org) throw new Error("Choose an organization.");
      await api(`/api/platform/tenants/${v.org}/rate-limit`, { method: "POST", body: { per_minute: v.per_minute, hours: v.hours } });
      P.tenants = null;
      Aira.toast("Rate limit applied.", "success");
      Aira.router.render();
    } });
    const limited = (P.tenants || []).filter((t) => t.flags.includes("rate_limited"));
    const dsrResult = h("div", { "aria-live": "polite" });
    const dsrForm = () => formModal({ title: "Data-subject request", intro: "Find one person across every organization. Access lists where they appear; export builds a download per candidate workspace; delete erases their workspace and personal details (an organization's last owner is skipped).", fields: [{ name: "kind", label: "Request", type: "select", options: [["access", "Access (where do they appear)"], ["export", "Export their data"], ["delete", "Delete (erasure)"]] }, { name: "email", label: "Email address", type: "email" }, { name: "confirm", label: "For deletion, type the email again to confirm", type: "email" }], submit: "Run request", onSubmit: async (v) => {
      if (!v.email) throw new Error("Enter an email address.");
      if (v.kind === "delete" && v.confirm.toLowerCase() !== v.email.toLowerCase()) throw new Error("Type the same email address to confirm deletion.");
      const r = await api("/api/platform/dsr", { method: "POST", body: { kind: v.kind, email: v.email } });
      dsrResult.replaceChildren(section(`Result for ${r.email}`, h("p", { class: "muted", text: `Request ${r.request_id.slice(0, 8)} recorded in the platform audit.` }), table(["Organization", "Role", "Outcome"], r.memberships.map((m) => h("tr", {}, td(m.org), td(m.role), td(v.kind === "delete" ? ((r.deleted || []).find((d) => d.org_id === m.org_id) || {}).skipped || "erased" : v.kind === "export" ? "export prepared" : "found")))), r.memberships.length ? null : h("p", { class: "muted", text: "This person is not a member of any organization." })));
      Aira.toast("Request completed.", "success");
    } });
    put(stage, pageHead("Abuse and compliance", "Controls that touch every tenant. Each action is audited."),
      h("datalist", { id: "cpSources" }, ...["linkedin", "google_jobs", "aira_jobs", "greenhouse", "lever", "ashby", "smartrecruiters"].map((s) => h("option", { value: s }))),
      section("Blocked sources", h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn danger secondary", text: "Block a source", onclick: blockForm })), table(["Source", "Reason", "Since", ""], blocked.map((s) => h("tr", {}, td(h("code", { text: s.source })), td(s.reason || "—"), td(ts(s.blocked_at)), td(h("button", { type: "button", class: "btn sm secondary", text: "Unblock", onclick: async () => { await api(`/api/platform/sources/${encodeURIComponent(s.source)}`, { method: "DELETE" }); Aira.toast(`${s.source} is available again.`, "success"); Aira.router.render(); } })))), "No source is blocked.")),
      section("Tenant rate limits", h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", text: "Rate-limit an organization", onclick: rateForm })), table(["Organization", "Until", ""], limited.map((t) => h("tr", {}, td(h("a", { href: `#/platform/tenants/${t.org_id}`, text: t.name })), td("see tenant"), td(h("button", { type: "button", class: "btn sm secondary", text: "Clear", onclick: async () => { await api(`/api/platform/tenants/${t.org_id}/rate-limit`, { method: "DELETE" }); P.tenants = null; Aira.toast("Rate limit cleared.", "success"); Aira.router.render(); } })))), "No organization is rate-limited.")),
      section("Data-subject requests", h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn", text: "New request", onclick: dsrForm })), dsrResult));
  };

  // ---------------------------------------------------------------------------
  // Operators
  // ---------------------------------------------------------------------------
  SCREENS.operators = async (stage) => {
    const { operators } = await api("/api/platform/operators");
    const create = () => formModal({ title: "Add an operator", intro: "They set up MFA at first sign-in. Support operators are read-only and can request tenant access; platform admins can change plans, flags and billing.", fields: [{ name: "email", label: "Email", type: "email" }, { name: "name", label: "Name" }, { name: "role", label: "Role", type: "select", options: [["support", "Support (read-only)"], ["platform_admin", "Platform admin"]] }, { name: "password", label: "Temporary password (12+ characters)", type: "password" }], submit: "Add operator", onSubmit: async (v) => {
      if (!v.email) throw new Error("Enter an email address.");
      if (v.password.length < 12) throw new Error("The password needs at least 12 characters.");
      await api("/api/platform/operators", { method: "POST", body: v });
      Aira.toast("Operator added.", "success");
      Aira.router.render();
    } });
    const rows = operators.map((o) => h("tr", {}, td(h("strong", { text: o.name || o.email }), "cp-name"), td(o.email), td(o.role === "platform_admin" ? "platform admin" : "support"), td(chip(o.status, o.status === "active" ? "ok" : "danger")), td(o.mfa_enabled_at ? "on" : "not yet"), td(o.last_sign_in_at ? fmt.ago(o.last_sign_in_at) : "never"),
      td(o.operator_id === P.me.operator_id ? h("span", { class: "muted", text: "you" }) : h("button", { type: "button", class: "btn sm secondary", text: o.status === "active" ? "Disable" : "Enable", onclick: async () => { if (o.status === "active" && !(await Aira.confirm({ title: `Disable ${o.email}?`, message: "Their sessions end at once.", confirmLabel: "Disable", danger: true }))) return; await api(`/api/platform/operators/${o.operator_id}`, { method: "PATCH", body: { status: o.status === "active" ? "disabled" : "active" } }); Aira.toast("Updated.", "success"); Aira.router.render(); } }))));
    put(stage, pageHead("Operators", "Who can sign in to the platform console.", h("button", { type: "button", class: "btn", text: "Add operator", onclick: create })), section("", table(["Name", "Email", "Role", "State", "MFA", "Last sign-in", ""], rows)));
  };

  window.AiraPlatformLogic = { FEATURES, LIMIT_FIELDS };
})();

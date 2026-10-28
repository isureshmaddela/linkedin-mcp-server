/* Aira hosted build — core: who is signed in, role-aware navigation, banners, acting for a candidate, plan prompts.
 *
 * Contract for the other cloud-*.js files (they load after this one, before cloud-boot.js):
 *   Aira.cloud.me                  the /api/auth/me payload (role, org, plan, permissions, features, rules)
 *   Aira.cloud.can(permission)     organization-level or own-workspace permission
 *   Aira.cloud.has(feature)        the plan includes a feature (for example "sources.linkedin")
 *   Aira.cloud.role()              owner | admin | coach | candidate | billing | auditor | operator
 *   Aira.cloud.register(def)       add a section: { id, label, icon, group, key, roles: [...], view(stage, route), when?(me) }
 *                                  `id` becomes the first hash segment (#/<id>/...); `roles` limits who sees it.
 *   Aira.cloud.showGate(code)      shown for mfa_required / mfa_enrollment_required / email_unverified (cloud-auth.js sets it)
 *   Aira.cloud.showAuth()          the sign-in / sign-up / invite screens (cloud-auth.js sets it)
 *   Aira.cloud.enterWorkspace(c)   open a candidate's workspace ({id, name, scope}); leaveWorkspace() returns
 *   Aira.cloud.enterOwnWorkspace() an owner or admin opens (or starts) the workspace they use the apps in themselves
 *   Aira.cloud.planPrompt(error)   explain a 402 plan_required (an upgrade prompt for staff, the organization's rule for a candidate)
 *   Aira.cloud.api(path, opts)     Aira.api, with errors surfaced the same way
 */
"use strict";

(function () {
  const h = window.h; // a function declaration in app.js, so it is a window property
  const $ = (id) => document.getElementById(id); // `const $` in app.js is not a window property
  const cloud = (Aira.cloud = {
    me: null,
    config: null,
    items: [],
    banners: [],
    api: (path, opts) => Aira.api(path, opts),
  });

  cloud.role = () => (cloud.me ? (cloud.me.kind === "operator" && !cloud.me.support ? "operator" : cloud.me.role || (cloud.me.support && cloud.me.support.role) || "") : "");
  cloud.can = (permission) => !!cloud.me && ((cloud.me.permissions || []).includes(permission) || (cloud.me.candidate_permissions || []).includes(permission) || (cloud.inOwnWorkspace() && (cloud.own().candidate_permissions || []).includes(permission)));
  cloud.has = (feature) => !!cloud.me && (cloud.me.features || []).includes(feature);
  cloud.plan = () => (cloud.me && cloud.me.plan) || {};
  cloud.isStaff = () => ["owner", "admin", "coach", "billing", "auditor"].includes(cloud.role());
  // An owner or admin may keep a workspace of their own and use the apps themselves; only they can open it.
  cloud.own = () => (cloud.me && cloud.me.personal_workspace) || { available: false, exists: false };
  cloud.inOwnWorkspace = () => !!(Aira.state.actingFor && Aira.state.actingFor.self);

  // ---------------------------------------------------------------------------
  // Sections and navigation
  // ---------------------------------------------------------------------------
  const WORKSPACE_IDS = ["dashboard", "jobs", "autopilot", "needs", "applications", "interviews", "recruiters", "emails", "offers", "profile", "documents", "analytics", "careers", "settings"]; // Leads is the business's (Marketing), not a candidate's
  const LOCAL_NAV = Aira.NAV.map((item) => ({ ...item }));
  // The app a registered section belongs to when its definition does not say: the candidate's own sections are
  // Careers, organization administration is the Account app, coaching is its own app-like group.
  const APP_OF_GROUP = { Workspace: "careers", Account: "account", Organization: "account", Coaching: "coaching", Platform: "platform" };
  Aira.apps.pseudo.account = { name: "Account", tagline: "Organization and sign-in", icon: "settings", home: "org" };
  Aira.apps.pseudo.coaching = { name: "Coaching", tagline: "Your candidates and their held questions", icon: "recruiters", home: "coach" };

  cloud.register = (definition) => {
    cloud.items = cloud.items.filter((item) => item.id !== definition.id);
    cloud.items.push(definition);
    if (definition.view) Aira.views[definition.id] = definition.view;
  };

  // Apps whose data is the organization's (the API console, the industry apps) are not part of a candidate's workspace.
  const ORG_APPS = new Set(Aira.APPS.filter((entry) => entry.scope === "org").map((entry) => entry.id));
  const appOff = (id) => Aira.apps.state.loaded && Aira.apps.state.enabled[id] === false;

  // Each member's own everyday sections: Today, Display and reading, and the life apps the organization has.
  const PERSONAL_IDS = new Set(["today", "display"]);
  const LIFE_IDS = new Set(Aira.APPS.filter((entry) => entry.category === "life").map((entry) => entry.id));
  function lifeNav() {
    if (!cloud.can("life.read") || (Aira.state.actingFor && !cloud.inOwnWorkspace())) return [];
    return LOCAL_NAV.filter((item) => PERSONAL_IDS.has(item.id) || (LIFE_IDS.has(item.app) && !appOff(item.app)));
  }

  function workspaceNav() {
    const hide = new Set(["hub", ...PERSONAL_IDS]);
    if (!cloud.has("sources.google_jobs")) hide.add("careers");
    return LOCAL_NAV.filter((item) => !hide.has(item.id) && !ORG_APPS.has(item.app) && !appOff(item.app));
  }

  // The organization's apps, opened without a workspace: the API console for staff who may read the integrations,
  // the industry apps and the Business hub for members who may use them (owners, admins, coaches as team members).
  function orgNav() {
    if (cloud.role() === "candidate") return [];
    const industry = LOCAL_NAV.filter((item) => ORG_APPS.has(item.app) && item.app !== "api" && !LIFE_IDS.has(item.app) && !appOff(item.app));
    const items = [];
    if (cloud.can("integrations.read") && !appOff("api")) items.push(...LOCAL_NAV.filter((item) => item.app === "api"));
    if (cloud.can("suite.read") && industry.length) items.push(...industry, ...LOCAL_NAV.filter((item) => item.id === "hub"));
    return items;
  }

  function itemsFor(role) {
    return cloud.items.filter((item) => (!item.roles || item.roles.includes(role)) && (!item.when || item.when(cloud.me)));
  }

  cloud.buildNav = () => {
    const role = cloud.role();
    const acting = !!Aira.state.actingFor;
    const self = cloud.inOwnWorkspace();
    let nav = [];
    if (role === "candidate" || acting) nav = workspaceNav().map((item) => (item.id === "dashboard" && role !== "candidate" && !self ? { ...item, label: "Candidate home" } : item));
    const own = itemsFor(role).map((item) => ({ id: item.id, label: item.label, icon: item.icon || "dashboard", group: item.group || "Organization", app: item.app || APP_OF_GROUP[item.group || "Organization"] || "account", key: item.key, quick: !!item.quick }));
    // Staff sections come first for people who administer; a candidate's own sections stay first for them.
    nav = role === "candidate" ? [...nav, ...lifeNav(), ...own] : [...own, ...nav.map((item) => ({ ...item, group: acting && !self && item.group !== "Account" ? "Candidate workspace" : item.group })), ...orgNav(), ...lifeNav()];
    Aira.NAV.splice(0, Aira.NAV.length, ...nav);
    Aira.state.currentApp = null;
    Aira.apps.pseudo.account.tagline = cloud.me && cloud.me.org ? cloud.me.org.name : "Organization and sign-in";
    Aira.renderNav();
    if (Aira.renderAccount) Aira.renderAccount();
  };

  // A link to a section this person's role does not have (a candidate opening a business app, staff without the
  // permission) shows the app's closed page rather than a broken one. Careers opens in staff's own workspace.
  Aira.apps.roleBlock = (section, id) => {
    if (!cloud.me || Aira.state.platformMode || id === "careers") return null;
    return Aira.NAV.some((item) => item.id === section) ? null : { app: id, kind: "role", reason: "" };
  };
  // Pages held back for this person, with what to do instead. The Business hub adds up the business apps, for the
  // people who may read them. A candidate's workspace pages (Careers, Settings) need a workspace: staff open their
  // own, or a candidate's, first.
  Aira.sectionGate = (section) => {
    if (!cloud.me || Aira.state.platformMode) return null;
    if (section === "hub" && !cloud.can("suite.read")) return Aira.ui.empty("Not available", "This page is not part of your role.");
    if (!WORKSPACE_IDS.includes(section) || cloud.hasWorkspace()) return null;
    const own = cloud.own();
    const actions = [];
    if (own.available) actions.push(h("button", { type: "button", class: "btn", text: own.exists ? "Open my workspace" : "Start my workspace", onclick: () => cloud.enterOwnWorkspace({ go: [section] }) }));
    if (cloud.can("candidates.list")) actions.push(h("a", { class: `btn ${actions.length ? "secondary" : ""}`, href: cloud.role() === "coach" ? "#/coach" : "#/org", text: "Choose a candidate" }));
    if (!actions.length) return Aira.ui.empty("Not available", "This page is part of a candidate's workspace, which your role does not open.");
    return h("section", { class: "card app-locked", "data-kind": "workspace", role: "status" },
      h("h1", { text: "Open a workspace first" }),
      h("p", { text: "This page belongs to a job seeker's workspace: their profile, searches and applications." }),
      h("p", { class: "muted", text: own.available ? "Open your own workspace to use it yourself, or a candidate's to work for them." : "Choose a candidate to work for them." }),
      h("div", { class: "btn-row" }, ...actions));
  };
  Aira.apps.remedy = (id, kind) => {
    if (kind === "disabled") return { text: "Monosage has paused it for every organization; it comes back with your data when it is switched on again." };
    if (kind === "role") {
      return cloud.role() === "candidate" && !LIFE_IDS.has(id)
        ? { text: "It holds your organization's business data, which its owners, administrators and coaches use." }
        : { text: "Ask an owner or administrator of your organization if you need it." };
    }
    if (cloud.can("billing.manage")) return { text: "Turn it on for your organization under Apps. It is billed per candidate seat with your plan; the trial includes every app.", action: { label: "Open Apps", href: "#/org/apps" } };
    return { text: "Ask an owner or the billing contact to turn it on under Apps." };
  };

  // The switcher: a candidate opens the apps the organization subscribes to; staff open them for a candidate.
  Aira.apps.decorate = (entries) => {
    const role = cloud.role();
    const acting = !!Aira.state.actingFor;
    return entries.map((entry) => {
      if (entry.kind !== "product") return entry;
      if (role === "operator") return { ...entry, enabled: false, reason: "Operators have no workspace" };
      if (entry.enabled === false) return entry;
      if (role !== "candidate" && !acting && !entry.present) {
        // An owner or admin opens Careers or Leads in their own workspace (started on first use).
        if (cloud.own().available) return { ...entry, enabled: true, reason: "", tagline: cloud.own().exists ? entry.tagline : "Start your own workspace" };
        return { ...entry, enabled: false, reason: "Open a candidate's workspace first" };
      }
      if (!entry.present) return { ...entry, enabled: false, reason: entry.reason || "Not available for your role" };
      return entry;
    });
  };

  // ---------------------------------------------------------------------------
  // Boot (called by app.js after /api/auth/me)
  // ---------------------------------------------------------------------------
  cloud.boot = async (user) => {
    cloud.me = user;
    if (user.org && user.org.slug) Aira.remember("lastOrg", user.org.slug); // the next sign-in page shows this organization's branding
    cloud.restoreActing();
    $("accountEmail").textContent = user.email;
    $("accountMeta").textContent = `${user.role || ""}${user.org ? " · " + user.org.name : ""}`;
    cloud.buildNav();
    cloud.refreshBanners();
    if (!cloud.bannerTimer) cloud.bannerTimer = setInterval(cloud.refreshBanners, 60000);
    return cloud.hasWorkspace();
  };

  // Does this person have a candidate workspace to load right now?
  cloud.hasWorkspace = () => cloud.role() === "candidate" || !!Aira.state.actingFor;

  cloud.landing = (workspace) => {
    const role = cloud.role();
    if (role === "candidate") return null;
    if (Aira.state.actingFor) return ["dashboard"];
    return [{ owner: "org", admin: "org", auditor: "org", coach: "coach", billing: "billing" }[role] || "org"];
  };

  // ---------------------------------------------------------------------------
  // Acting for a candidate (coach, administrator, auditor)
  // ---------------------------------------------------------------------------
  cloud.restoreActing = () => {
    if (cloud.role() === "candidate") { Aira.state.actingFor = null; return; }
    const saved = Aira.recall("actingFor", null);
    if (!saved || saved.org !== (cloud.me.org && cloud.me.org.org_id)) return;
    if (saved.candidate && saved.candidate.self && (saved.candidate.id !== cloud.me.member_id || !cloud.own().exists)) return; // ended, or another person on this browser
    Aira.state.actingFor = saved.candidate;
  };

  cloud.enterWorkspace = async (candidate, { go = ["dashboard"] } = {}) => {
    Aira.state.actingFor = { id: candidate.id || candidate.candidate_id, name: candidate.name || candidate.email || "candidate", scope: candidate.scope || "", self: !!candidate.self };
    Aira.remember("actingFor", { org: cloud.me.org.org_id, candidate: Aira.state.actingFor });
    Aira.state.profile = null;
    cloud.buildNav();
    await Aira.loadWorkspace();
    cloud.renderActingBanner();
    if (go) Aira.router.go(go);
  };

  // An owner's or admin's own workspace: started (one candidate seat) the first time they open an app.
  cloud.enterOwnWorkspace = async ({ go = ["dashboard"] } = {}) => {
    let own = cloud.own();
    if (!own.available) return false;
    if (!own.exists) {
      const ok = await Aira.confirm({
        title: "Start your own workspace",
        message: "Use Careers and Leads yourself, in a workspace only you can open (coaches and other admins cannot). It takes one candidate seat. Starting it agrees to Monosage running searches and applications for you, and to your organization's policy for sensitive answers.",
        confirmLabel: "Start my workspace",
      });
      if (!ok) return false;
      try { own = await Aira.api("/api/workspace/own", { method: "POST" }); } catch { return false; }
      cloud.me.personal_workspace = own;
    }
    if (!cloud.inOwnWorkspace()) await cloud.enterWorkspace({ id: cloud.me.member_id, name: "your own workspace", self: true }, { go });
    else if (go) Aira.router.go(go);
    return true;
  };

  // Opening Careers or Leads from the switcher (or `g` + key) as an owner or admin enters their own workspace first.
  const openApp = Aira.apps.open.bind(Aira.apps);
  Aira.apps.open = async (id) => {
    const product = Aira.APPS.some((entry) => entry.id === id);
    if (product && id !== "api" && cloud.isStaff() && !Aira.state.actingFor && cloud.own().available) {
      if (!(await cloud.enterOwnWorkspace({ go: null }))) return;
    }
    openApp(id);
  };

  cloud.register({
    id: "own-workspace", label: "My workspace", icon: "dashboard", group: "Account", roles: ["owner", "admin"],
    when: (me) => !!(me && me.personal_workspace && me.personal_workspace.available),
    view(stage) {
      const own = cloud.own();
      const status = h("p", { class: "muted", text: own.exists ? `Started. Stage: ${own.stage || "onboarding"}${own.paused ? " (paused)" : ""}.` : "Not started yet." });
      const open = h("button", { type: "button", class: "btn", text: own.exists ? "Open my workspace" : "Start my workspace", onclick: () => cloud.enterOwnWorkspace() });
      const end = own.exists ? h("button", { type: "button", class: "btn ghost", text: "End my workspace", onclick: async () => {
        if (!(await Aira.confirm({ title: "End your workspace", message: "Your searches, applications, leads and documents in it are deleted and the seat is free again. This cannot be undone.", confirmLabel: "End and delete", danger: true }))) return;
        try { cloud.me.personal_workspace = await Aira.api("/api/workspace/own", { method: "DELETE" }); } catch { return; }
        if (cloud.inOwnWorkspace()) cloud.leaveWorkspace();
        Aira.toast("Your workspace was ended.", "success");
        cloud.buildNav();
        Aira.router.go(["own-workspace"]);
      } }) : null;
      stage.replaceChildren(
        h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "My workspace" }), h("p", { text: "Use the apps yourself: your own job search in Careers, your own pipeline in Leads. Only you can open it; it takes one candidate seat." }))),
        h("div", { class: "card" }, status, h("div", { class: "adm-btnrow" }, open, end)),
      );
    },
  });

  cloud.leaveWorkspace = () => {
    Aira.state.actingFor = null;
    Aira.state.workspaceLoaded = false;
    Aira.remember("actingFor", null);
    cloud.buildNav();
    cloud.renderActingBanner();
    Aira.router.go(cloud.landing(false));
  };

  cloud.renderActingBanner = () => {
    const slot = cloud.slot("cloudActing");
    const acting = Aira.state.actingFor;
    if (!acting) { slot.replaceChildren(); slot.hidden = true; return; }
    if (acting.self) {
      slot.hidden = false;
      slot.replaceChildren(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: "Your own workspace" }), h("span", { class: "muted", text: "Only you can open it." }), h("button", { type: "button", class: "btn secondary sm", text: "Back to organization", onclick: cloud.leaveWorkspace })));
      return;
    }
    const scope = acting.scope ? ` (${acting.scope} access)` : "";
    slot.hidden = false;
    slot.replaceChildren(h("div", { class: "cloud-banner acting", role: "status" }, h("strong", { text: `Acting for ${acting.name}${scope}` }), h("span", { class: "muted", text: "Everything you do here is recorded in their name and yours." }), h("button", { type: "button", class: "btn secondary sm", text: "Back to my view", onclick: cloud.leaveWorkspace })));
  };

  // A stable slot above the page content for banners.
  cloud.slot = (id) => {
    let node = document.getElementById(id);
    if (!node) {
      node = h("div", { id, class: "cloud-slot", hidden: true });
      const main = document.getElementById("main");
      main.parentNode.insertBefore(node, main);
    }
    return node;
  };

  // ---------------------------------------------------------------------------
  // Banners: paused, trial, billing state, over limits, support access, platform announcements
  // ---------------------------------------------------------------------------
  cloud.refreshBanners = async () => {
    if (!cloud.me || cloud.me.kind !== "member") return;
    try {
      const data = await Aira.api("/api/org/banners", { silent: true });
      cloud.banners = data.banners || [];
    } catch { return; }
    cloud.renderBanners();
    cloud.renderActingBanner();
  };

  cloud.renderBanners = () => {
    const slot = cloud.slot("cloudBanners");
    const items = (cloud.banners || []).filter((banner) => !(Aira.recall("dismissed", {})[banner.kind + banner.title]));
    slot.hidden = !items.length;
    slot.replaceChildren(...items.map((banner) => h("div", { class: `cloud-banner ${banner.level || "info"}`, role: banner.level === "danger" ? "alert" : "status" },
      h("strong", { text: banner.title }), banner.body ? h("span", { text: banner.body }) : null,
      banner.link ? h("a", { class: "btn secondary sm", href: banner.link, text: "Open" }) : null,
      banner.kind === "announcement" ? h("button", { type: "button", class: "btn ghost sm", "aria-label": "Dismiss", text: "✕", onclick: () => { const d = Aira.recall("dismissed", {}); d[banner.kind + banner.title] = 1; Aira.remember("dismissed", d); cloud.renderBanners(); } }) : null)));
  };

  // ---------------------------------------------------------------------------
  // Errors the hosted API adds: plan limits and the sign-in gates
  // ---------------------------------------------------------------------------
  cloud.inlinePaths = ["/api/org/policy", "/api/org/invites", "/api/org/assignments", "/api/org/integrations", "/api/org/closure"];
  cloud.onApiError = (status, data, path) => {
    const code = data && data.code;
    // Screens that show a plan limit inline (policy, invitations, assignments) list their path here to avoid a second, modal message.
    if (status === 402 && code === "plan_required") { if (!cloud.inlinePaths.some((prefix) => String(path).startsWith(prefix))) cloud.planPrompt(data); return; }
    if (status === 402 && code === "subscription_inactive") { cloud.refreshBanners(); return; }
    if ((status === 401 || status === 403) && ["mfa_required", "mfa_enrollment_required", "email_unverified"].includes(code) && !String(path).startsWith("/api/auth/")) {
      if (cloud.showGate) cloud.showGate(code);
    }
  };

  let promptOpen = false;
  cloud.planPrompt = (error) => {
    if (promptOpen) return;
    promptOpen = true;
    const staff = error.audience !== "candidate" && cloud.can("billing.manage");
    const needed = error.required_plan ? error.required_plan[0].toUpperCase() + error.required_plan.slice(1) : "";
    const actions = [{ label: "Close", kind: "ghost" }];
    if (staff) actions.unshift({ label: needed ? `See ${needed} plan` : "See plans", kind: "", onClick: () => { Aira.router.go(["billing"]); } });
    const parts = [h("p", { text: error.error || error.message })];
    if (error.used !== undefined && error.limit_value !== undefined) parts.push(h("p", { class: "muted", text: `Used ${error.used} of ${error.limit_value}.` }));
    if (!staff && error.audience !== "candidate") parts.push(h("p", { class: "muted", text: "Ask an owner or the billing contact to change the plan." }));
    Aira.modal({ title: error.audience === "candidate" ? "Your organization's limit" : "Not included in your plan", body: h("div", {}, ...parts), actions, onClose: () => { promptOpen = false; } });
  };

  // The sign-in screen is the cloud's; app.js calls this when a session ends.
  Aira.showAuth = () => { if (cloud.showAuth) cloud.showAuth(); };
})();

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
  cloud.can = (permission) => !!cloud.me && ((cloud.me.permissions || []).includes(permission) || (cloud.me.candidate_permissions || []).includes(permission));
  cloud.has = (feature) => !!cloud.me && (cloud.me.features || []).includes(feature);
  cloud.plan = () => (cloud.me && cloud.me.plan) || {};
  cloud.isStaff = () => ["owner", "admin", "coach", "billing", "auditor"].includes(cloud.role());

  // ---------------------------------------------------------------------------
  // Sections and navigation
  // ---------------------------------------------------------------------------
  const WORKSPACE_IDS = ["dashboard", "jobs", "applications", "interviews", "recruiters", "documents", "analytics", "careers", "leads", "settings"];
  const LOCAL_NAV = Aira.NAV.map((item) => ({ ...item }));

  cloud.register = (definition) => {
    cloud.items = cloud.items.filter((item) => item.id !== definition.id);
    cloud.items.push(definition);
    if (definition.view) Aira.views[definition.id] = definition.view;
  };

  function workspaceNav() {
    const hide = new Set(["airaapi"]); // the Aira API console belongs to the organization, not to a candidate
    if (!cloud.has("sources.google_jobs")) hide.add("careers");
    return LOCAL_NAV.filter((item) => !hide.has(item.id));
  }

  function itemsFor(role) {
    return cloud.items.filter((item) => (!item.roles || item.roles.includes(role)) && (!item.when || item.when(cloud.me)));
  }

  cloud.buildNav = () => {
    const role = cloud.role();
    const acting = !!Aira.state.actingFor;
    let nav = [];
    if (role === "candidate" || acting) nav = workspaceNav().map((item) => (item.id === "dashboard" && role !== "candidate" ? { ...item, label: "Candidate home" } : item));
    const own = itemsFor(role).map((item) => ({ id: item.id, label: item.label, icon: item.icon || "dashboard", group: item.group || "Organization", key: item.key, quick: !!item.quick }));
    // Staff sections come first for people who administer; a candidate's own sections stay first for them.
    nav = role === "candidate" ? [...nav, ...own] : [...own, ...nav.map((item) => ({ ...item, group: acting ? "Candidate workspace" : item.group }))];
    Aira.NAV.splice(0, Aira.NAV.length, ...nav);
    Aira.renderNav();
  };

  // ---------------------------------------------------------------------------
  // Boot (called by app.js after /api/auth/me)
  // ---------------------------------------------------------------------------
  cloud.boot = async (user) => {
    cloud.me = user;
    if (user.org && user.org.slug) Aira.remember("lastOrg", user.org.slug); // the next sign-in page shows this organization's branding
    cloud.restoreActing();
    $("accountEmail").textContent = `${user.email} · ${user.role}${user.org ? " · " + user.org.name : ""}`;
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
    if (saved && saved.org === (cloud.me.org && cloud.me.org.org_id)) Aira.state.actingFor = saved.candidate;
  };

  cloud.enterWorkspace = async (candidate) => {
    Aira.state.actingFor = { id: candidate.id || candidate.candidate_id, name: candidate.name || candidate.email || "candidate", scope: candidate.scope || "" };
    Aira.remember("actingFor", { org: cloud.me.org.org_id, candidate: Aira.state.actingFor });
    Aira.state.profile = null;
    cloud.buildNav();
    await Aira.loadWorkspace();
    cloud.renderActingBanner();
    Aira.router.go(["dashboard"]);
  };

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

/* Monosage Cloud — spaces and role navigation.
 *
 * Spaces (monosage_cloud/spaces.py): with the organization's policy "personal" every member runs the business apps
 * (Sales, Marketing, Service, Commerce, Courses & Events, Sites) and the Business hub in a space of their own: their
 * business, their customers, what they earn. Owners, admins, coaches and the members the organization names may also
 * open the organization's own apps; the browser asks for them with X-Aira-Space: company. /api/auth/me says which:
 * me.spaces = { mode: "personal" | "shared", apps: [...], own, company }.
 *
 *   Aira.cloud.ownSpaceApps()   the app ids this member runs in their own space ([] in a shared organization)
 *   Aira.cloud.space()          "member" (their own) or "company" (the organization's), remembered per account
 *   Aira.cloud.setSpace(space)  switch, and draw the page again
 *   Aira.extraHeaders(path)     app.js adds these to every API call: X-Aira-Space only while the company is chosen
 *                               and a business app (or the hub) is open, so Home's earnings stay the member's own
 *
 * Role navigation: the account menu's role and organization (renderWho), its way into the organization (orgLink),
 * Home's cards (what the member earned this month; their part of the organization) and, for an operator whose
 * console session is open, the platform console instead of the member sign-in.
 */
"use strict";

(function () {
  const h = window.h;
  const cloud = Aira.cloud;
  if (!cloud) return;
  const MINE = "member";
  const COMPANY = "company";

  // ---------------------------------------------------------------------------
  // Which space
  // ---------------------------------------------------------------------------
  const info = () => (cloud.me && cloud.me.spaces) || { mode: "shared", apps: [], own: false, company: false };
  const personal = () => !!cloud.me && cloud.me.kind === "member" && info().mode === "personal" && !!info().own;
  const orgName = () => (cloud.me && cloud.me.org && cloud.me.org.name) || "Your organization";
  const storeKey = () => `space.${(cloud.me && cloud.me.org && cloud.me.org.org_id) || ""}.${(cloud.me && cloud.me.member_id) || ""}`;

  cloud.ownSpaceApps = () => (personal() ? (info().apps || []).slice() : []);
  cloud.space = () => (personal() && info().company && Aira.recall(storeKey(), MINE) === COMPANY ? COMPANY : MINE);

  // A business app's section (Shop, Leads, Sites...) or the Business hub: the pages a space applies to.
  const currentSection = () => (Aira.router && Aira.router.current && Aira.router.current.path[0]) || "";
  cloud.inBusinessSection = (section = currentSection()) => !!section && (section === "hub" || cloud.ownSpaceApps().includes(Aira.apps.sectionApp(section)));

  Aira.extraHeaders = (path) => {
    if (Aira.state.platformMode || !String(path).startsWith("/api/")) return {};
    return cloud.space() === COMPANY && cloud.inBusinessSection() ? { "X-Aira-Space": COMPANY } : {};
  };

  cloud.setSpace = (space) => {
    if (!personal() || !info().company) return;
    Aira.remember(storeKey(), space === COMPANY ? COMPANY : MINE);
    if (Aira.suite) Aira.suite._business = null; // the business profile is each space's own
    Aira.toast(space === COMPANY ? `Now in ${orgName()}'s apps.` : "Now in your own apps.", "info", { timeout: 3000 });
    Aira.router.render();
  };

  // ---------------------------------------------------------------------------
  // The switcher in the top bar, and the strip under it
  // ---------------------------------------------------------------------------
  function switchNode() {
    let node = document.getElementById("spaceSwitch");
    if (!node) {
      node = h("div", { id: "spaceSwitch", class: "space-switch", hidden: true });
      const end = document.querySelector(".topbar-end");
      if (end) end.prepend(node);
    }
    return node;
  }

  function option(value, label, current, hint) {
    return h("button", { type: "button", class: "space-opt", dataset: { space: value }, "aria-pressed": String(current === value), title: hint,
      onclick: () => { if (cloud.space() !== value) cloud.setSpace(value); } }, h("span", { text: label }));
  }

  function paint() {
    const node = switchNode();
    const strip = cloud.slot("cloudSpace");
    const on = !Aira.state.platformMode && personal() && cloud.inBusinessSection();
    const space = cloud.space();
    document.body.dataset.space = on ? space : "";
    if (!on) {
      node.hidden = true;
      node.replaceChildren();
      strip.hidden = true;
      strip.replaceChildren();
      return;
    }
    node.hidden = false;
    if (!info().company) {
      // Everything here is theirs: a quiet badge says so; there is nothing to switch to.
      node.replaceChildren(h("span", { class: "space-badge", title: "Only you see these apps. What you earn here is yours.", text: "Your business" }));
      strip.hidden = true;
      strip.replaceChildren();
      return;
    }
    node.setAttribute("role", "group");
    node.setAttribute("aria-label", "Whose business");
    node.replaceChildren(
      option(MINE, "Mine", space, "Your own business: only you see it, and what you earn is yours"),
      option(COMPANY, orgName(), space, "Your organization's own apps: its customers, sales and revenue"),
    );
    // Under the bar: a clear warning while the organization's apps are open; on narrow screens (no room in the bar)
    // it also carries the switch.
    strip.hidden = false;
    strip.replaceChildren(space === COMPANY
      ? h("div", { class: "cloud-banner space-strip", dataset: { space: COMPANY }, role: "status" },
        h("strong", { text: orgName() }),
        h("span", { text: "You are working in your organization's apps: its customers, sales and revenue." }),
        h("button", { type: "button", class: "btn secondary sm", text: "Switch to mine", onclick: () => cloud.setSpace(MINE) }))
      : h("div", { class: "cloud-banner space-strip", dataset: { space: MINE }, role: "status" },
        h("strong", { text: "Your business" }),
        h("span", { text: "Only you see it. What you earn here is yours." }),
        h("button", { type: "button", class: "btn ghost sm", text: `Open ${orgName()}'s apps`, onclick: () => cloud.setSpace(COMPANY) })));
  }
  cloud.paintSpace = paint;

  // Every page draw (a link, Back, a switch) repaints the switcher for the page now open.
  const baseRender = Aira.router.render;
  Aira.router.render = function (...args) {
    const done = baseRender.apply(this, args);
    try { paint(); } catch (error) { console.warn("spaces", error); }
    return done;
  };

  // ---------------------------------------------------------------------------
  // Who is signed in: role and organization in the account menu, and the way into the organization
  // ---------------------------------------------------------------------------
  const ROLE_LABELS = { owner: "Owner", admin: "Administrator", coach: "Coach", candidate: "Member", billing: "Billing contact", auditor: "Auditor", operator: "Monosage operator" };
  cloud.roleLabel = (role = cloud.role()) => ROLE_LABELS[role] || (role ? role[0].toUpperCase() + role.slice(1) : "");

  cloud.renderWho = (user) => {
    const meta = document.getElementById("accountMeta");
    if (!meta) return;
    const support = user.kind === "operator" && user.support;
    const role = support ? `Monosage support (${cloud.roleLabel(user.support.role || "")})` : cloud.roleLabel(user.role || cloud.role());
    meta.replaceChildren(...[
      role ? h("span", { class: "account-role", text: role }) : null,
      user.org ? h("span", { class: "account-org", text: user.org.name }) : null,
    ].filter(Boolean));
    const button = document.getElementById("accountBtn");
    if (button) button.title = [user.email, role, user.org && user.org.name].filter(Boolean).join(" · ");
  };

  cloud.orgLink = () => {
    switch (cloud.role()) {
      case "owner": case "admin": case "auditor": return { label: "Organization", href: "#/org", icon: "recruiters", section: "org" };
      case "coach": return { label: "My candidates", href: "#/coach", icon: "recruiters", section: "coach" };
      case "billing": return { label: "Plan & billing", href: "#/billing", icon: "settings", section: "billing" };
      default: return null;
    }
  };

  // ---------------------------------------------------------------------------
  // Home (Today): what the member earned this month in their own apps, and their part of the organization
  // ---------------------------------------------------------------------------
  let earned = null; // { at, summary } — Today draws itself again every minute; ask at most that often
  let asking = null;
  async function loadEarnings() {
    if (earned && Date.now() - earned.at < 55000) return earned.summary;
    if (!asking) {
      // No X-Aira-Space here: Home is never a business page, so this is always the member's own space.
      asking = Aira.api("/api/suite/revenue?months=2", { silent: true })
        .then((data) => { earned = { at: Date.now(), summary: data.summary }; return data.summary; })
        .finally(() => { asking = null; });
    }
    return asking;
  }

  const money = (cents, currency) => (Aira.suite && Aira.suite.money ? Aira.suite.money(cents, currency) : (Number(cents || 0) / 100).toLocaleString([], { style: "currency", currency: currency || "USD" }));

  function earningsCard() {
    const apps = cloud.ownSpaceApps().filter((id) => !Aira.apps.state.loaded || Aira.apps.state.enabled[id] !== false);
    if (!apps.length) return null;
    const value = h("p", { class: "space-earn-value", text: "…" });
    const note = h("p", { class: "muted" });
    const card = h("section", { class: "life-card space-earn", dataset: { card: "earnings" } },
      h("header", {}, h("span", { class: "life-card-icon", "aria-hidden": "true" }, Aira.icon("studio")), h("h2", { text: "Your earnings this month" })),
      value, note,
      h("a", { class: "btn secondary life-open", href: "#/hub", text: "Open Business hub" }));
    const fill = (summary) => {
      value.textContent = money(summary.this_month_cents, summary.currency);
      note.textContent = summary.this_month_cents
        ? `Last month: ${money(summary.last_month_cents, summary.currency)}. From your own apps; only you see it.`
        : "Nothing yet this month. Sell in Commerce, send an invoice in Sales or run a course: what you earn in your own apps adds up here.";
    };
    if (earned) fill(earned.summary);
    loadEarnings().then(fill, () => card.remove());
    return card;
  }

  function organizationCard() {
    const role = cloud.role();
    const links = {
      owner: [["Organization overview", "#/org"], ["Members", "#/org/members"], ["Apps", "#/org/apps"]],
      admin: [["Organization overview", "#/org"], ["Members", "#/org/members"], ["Apps", "#/org/apps"]],
      auditor: [["Organization overview", "#/org"], ["Audit log", "#/org/audit"]],
      coach: [["My candidates", "#/coach"], ["Held questions", "#/coach-queue"]],
      billing: [["Plan & billing", "#/billing"]],
    }[role];
    if (!links || !cloud.me || !cloud.me.org) return null;
    return h("section", { class: "life-card space-org", dataset: { card: "organization" } },
      h("header", {}, h("span", { class: "life-card-icon", "aria-hidden": "true" }, Aira.icon("recruiters")), h("h2", { text: cloud.me.org.name })),
      h("p", { class: "muted", text: `Your role here: ${cloud.roleLabel()}.` }),
      h("div", { class: "btn-row" }, ...links.map(([label, href], index) => h("a", { class: `btn ${index ? "ghost" : "secondary"} sm`, href, text: label }))));
  }

  Aira.todayCards = () => {
    if (!cloud.me || cloud.me.kind !== "member" || (Aira.state.actingFor && !cloud.inOwnWorkspace())) return [];
    const cards = [earningsCard(), organizationCard()].filter(Boolean);
    return cards.length ? [h("div", { class: "life-cards space-today" }, ...cards)] : [];
  };

  // ---------------------------------------------------------------------------
  // An operator whose console session is open lands in the console, not on the member sign-in
  // ---------------------------------------------------------------------------
  const baseShowAuth = cloud.showAuth;
  let probed = false;
  cloud.showAuth = () => {
    const hash = String(location.hash || "");
    const bare = hash === "" || hash === "#" || hash === "#/";
    // Only on a browser where the console was used: everyone else goes straight to the sign-in.
    if (probed || !bare || !Aira.recall("operatorConsole", false)) { if (baseShowAuth) baseShowAuth(); return; }
    probed = true;
    fetch("/api/platform/me", { credentials: "same-origin", headers: { Accept: "application/json", "X-Requested-With": "aira" } })
      .then((response) => {
        if (response.ok) { location.hash = "#/platform"; return; }
        Aira.remember("operatorConsole", false);
        if (baseShowAuth) baseShowAuth();
      }, () => { if (baseShowAuth) baseShowAuth(); });
  };
})();

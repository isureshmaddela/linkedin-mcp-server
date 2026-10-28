/* Aira hosted build — organization administration: shared helpers, Overview, Candidates, candidate detail.
 *
 * Routing: every administration page lives under the single view id "org" (#/org, #/org/candidates, #/org/candidates/<id>,
 * #/org/members, #/org/coaches, #/org/policy, #/org/integrations, #/org/audit, #/org/data). The sidebar needs one first
 * hash segment per item, so the other items are registered as "org-<page>" ids whose view only redirects to #/org/<page>
 * (the navigation highlight is set by the page itself). Page renderers register in Aira.cloud.admin.pages[<page>]; the
 * pages for Members, Coaches, Policy, Integrations, Audit and Data are in cloud-people.js.
 * Backend links such as #/org/dashboard, #/org/support and #/org/billing are aliased below.
 */
"use strict";

(function () {
  const { h, api, fmt, ui } = Aira;
  const cloud = Aira.cloud;
  const A = (cloud.admin = { pages: {}, version: 1 });

  // --------------------------------------------------------------------------------------------
  // Helpers shared with cloud-people.js
  // --------------------------------------------------------------------------------------------
  A.n = (value) => (value === null || value === undefined ? "–" : Number(value).toLocaleString());
  A.ago = (epoch) => (epoch ? fmt.ago(epoch) : "never");
  A.when = (epoch) => (epoch ? fmt.datetime(epoch) : "—");
  A.message = (error) => (error && error.message ? error.message : String(error));
  A.titleCase = (text) => String(text || "").replace(/[_.]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  A.roleLabel = { owner: "Owner", admin: "Admin", coach: "Coach", candidate: "Candidate", billing: "Billing contact", auditor: "Auditor" };
  A.roleBadge = (role) => h("span", { class: `adm-badge ${role}`, text: A.roleLabel[role] || role });
  A.badge = (text, kind = "") => h("span", { class: `adm-badge ${kind}`, text });
  A.can = (permission) => cloud.can(permission);
  A.q = (params) => { const usp = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined)); const s = usp.toString(); return s ? `?${s}` : ""; };
  A.size = (bytes) => { if (bytes === null || bytes === undefined) return "–"; if (bytes < 1024 * 1024) return fmt.bytes(bytes); if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(bytes < 1024 ** 2 * 10 ? 1 : 0)} MB`; return `${(bytes / 1024 ** 3).toFixed(1)} GB`; };
  A.duration = (seconds) => {
    if (seconds === null || seconds === undefined) return "no answers yet";
    if (seconds < 90) return `${Math.round(seconds)} s`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
    if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} h`;
    return `${(seconds / 86400).toFixed(1)} days`;
  };

  A.crumbs = (...parts) => { const node = document.getElementById("crumbs"); if (node) node.replaceChildren(...parts.map((p, i) => h("span", { text: (i ? "· " : "") + p }))); };
  A.setCurrent = (page) => {
    const section = page ? `org-${page}` : "org";
    for (const link of document.querySelectorAll("#primaryNav a, #bottomNav a")) {
      if (link.dataset.section === "org" || (link.dataset.section || "").startsWith("org-")) link.setAttribute("aria-current", link.dataset.section === section ? "page" : "false");
    }
  };

  A.head = (title, subtitle, ...actions) => h("div", { class: "page-head" }, h("div", {}, h("h1", { text: title }), subtitle ? h("p", { text: subtitle }) : null), actions.length ? h("div", { class: "adm-btnrow" }, ...actions.filter(Boolean)) : null);
  A.panel = (title, body, { sub, actions, id } = {}) => h("section", { class: "panel", id }, h("div", { class: "panel-head" }, h("div", {}, h("h2", { style: { fontSize: "15px" }, text: title }), sub ? h("small", { class: "muted", text: sub }) : null), actions ? h("div", { class: "adm-btnrow" }, ...[].concat(actions)) : null), body);
  A.empty = (text) => h("div", { class: "adm-empty", text });

  A.meter = (used, limit) => {
    const pct = limit ? Math.min(100, Math.round((100 * used) / limit)) : 0;
    const kind = limit && used >= limit ? "bad" : limit && pct >= 85 ? "warn" : "";
    return h("div", { class: `adm-meter ${kind}`, role: "meter", "aria-valuemin": "0", "aria-valuemax": String(limit || 0), "aria-valuenow": String(used) }, h("i", { style: { width: `${limit ? Math.max(2, pct) : 0}%` } }));
  };
  A.usageTile = (label, used, limit, href, sub, fmtValue = A.n) => {
    const over = limit && used >= limit;
    const tag = href ? "a" : "div";
    return h(tag, { class: `adm-tile ${over ? "bad" : limit && used / limit >= 0.85 ? "warn" : ""}`, ...(href ? { href } : {}) },
      h("span", { class: "label", text: label }),
      h("span", { class: "value", text: limit ? `${fmtValue(used)} / ${fmtValue(limit)}` : fmtValue(used) }),
      limit ? A.meter(used, limit) : null,
      h("span", { class: "sub", text: sub || (limit ? `${Math.max(0, Math.round(100 - (100 * used) / limit))}% left` : "No limit on your plan") }));
  };

  // A modal that needs a written reason.
  A.askReason = ({ title, message, confirmLabel = "Confirm", placeholder = "Reason (shown on the dashboard)", danger = false }) => new Promise((resolve) => {
    const input = h("textarea", { rows: 3, maxlength: 300, placeholder, "aria-label": "Reason" });
    const error = h("div", { class: "adm-inline-error", role: "alert" });
    let done = false;
    const finish = (value) => { if (!done) { done = true; resolve(value); } };
    Aira.modal({ title, body: h("div", { class: "adm-form" }, h("p", { class: "muted", text: message }), ui.field("Reason", input), error),
      actions: [{ label: "Cancel", kind: "ghost", onClick: () => finish(null) }, { label: confirmLabel, kind: danger ? "danger" : "", onClick: () => { if (!input.value.trim()) { error.textContent = "Give a reason."; input.focus(); return false; } finish(input.value.trim()); } }],
      onClose: () => finish(null) });
  });

  // Delete-style confirmation: type the exact text.
  A.confirmTyped = ({ title, message, expected, confirmLabel = "Delete", extra }) => new Promise((resolve) => {
    const input = h("input", { type: "text", autocomplete: "off", "aria-label": `Type ${expected} to confirm` });
    const error = h("div", { class: "adm-inline-error", role: "alert" });
    let done = false;
    const finish = (value) => { if (!done) { done = true; resolve(value); } };
    Aira.modal({ title, body: h("div", { class: "adm-form" }, h("p", { class: "muted", text: message }), extra || null, ui.field(`Type ${expected} to confirm`, input), error),
      actions: [{ label: "Cancel", kind: "ghost", onClick: () => finish(false) }, { label: confirmLabel, kind: "danger", onClick: () => { if (input.value.trim().toLowerCase() !== String(expected).toLowerCase()) { error.textContent = "That does not match."; input.focus(); return false; } finish(true); } }],
      onClose: () => finish(false) });
  });

  A.download = async (path, name) => { try { await Aira.download(path, name); } catch (error) { Aira.reportError(error); } };

  // People directory (members + candidates) for turning ids into names; cached briefly.
  let directoryCache = null;
  A.directory = async (fresh = false) => {
    if (!fresh && directoryCache && Date.now() - directoryCache.at < 15000) return directoryCache.value;
    const data = await api("/api/org/members?limit=500", { silent: true });
    const byId = {};
    for (const m of data.members) byId[m.member_id] = m;
    directoryCache = { at: Date.now(), value: { members: data.members, byId, seats: data.seats } };
    return directoryCache.value;
  };
  A.forgetDirectory = () => { directoryCache = null; };
  A.nameOf = (directory, id) => { const m = directory && directory.byId[id]; return m ? (m.name || m.email) : ""; };

  let plansCache = null;
  A.plans = async () => { if (!plansCache) plansCache = (await api("/api/billing/plans", { silent: true })).plans; return plansCache; };
  // The cheapest plan that includes a feature, for "upgrade to X" hints.
  A.planWith = async (feature) => { const plans = await A.plans(); const found = plans.find((p) => (p.features || []).includes(feature)); return found ? found.name : ""; };

  // --------------------------------------------------------------------------------------------
  // Registration
  // --------------------------------------------------------------------------------------------
  const STAFF = ["owner", "admin"];
  const READERS = ["owner", "admin", "auditor"];
  const SECTIONS = [
    { id: "org", page: "", label: "Overview", icon: "dashboard", key: "v", roles: READERS, quick: true },
    { id: "org-candidates", page: "candidates", label: "Candidates", icon: "recruiters", key: "u", roles: READERS, quick: true },
    { id: "org-members", page: "members", label: "Members", icon: "recruiters", key: "m", roles: STAFF },
    { id: "org-coaches", page: "coaches", label: "Coaches", icon: "interviews", key: "h", roles: STAFF },
    { id: "org-policy", page: "policy", label: "Policy", icon: "settings", key: "l", roles: STAFF },
    { id: "org-integrations", page: "integrations", label: "Integrations", icon: "airaapi", key: "k", roles: READERS },
    { id: "org-audit", page: "audit", label: "Audit log", icon: "documents", key: "t", roles: READERS },
    { id: "org-data", page: "data", label: "Data & closure", icon: "analytics", key: "x", roles: READERS },
    // The apps the organization subscribes to (cloud-apps.js draws the page; a plain list stands in until it loads).
    { id: "org-apps", page: "apps", label: "Apps", icon: "more", key: "z", roles: [...READERS, "billing"] },
  ];
  const ALIASES = { dashboard: "", support: "data" };

  async function orgView(stage, route) {
    let page = route.path[1] || "";
    if (page in ALIASES) { Aira.router.go(["org", ...(ALIASES[page] ? [ALIASES[page]] : [])], route.query, { replace: true }); return; }
    if (page === "billing") { if (Aira.views.billing) { Aira.router.go(["billing"], route.query, { replace: true }); return; } stage.replaceChildren(ui.empty("Billing is not available", "Open Billing from the navigation.")); return; }
    const render = A.pages[page];
    A.setCurrent(page);
    if (!render) { stage.replaceChildren(ui.empty("Page not found", "Choose a page from the navigation.")); return; }
    if (!READERS.includes(cloud.role()) && !(page === "apps" && cloud.role() === "billing")) { stage.replaceChildren(ui.empty("Not available", "Organization administration is for owners, administrators and auditors.")); return; }
    if (["members", "coaches", "policy"].includes(page) && !STAFF.includes(cloud.role())) { stage.replaceChildren(ui.empty("Not available for your role", "Members, coach assignments and policy are managed by owners and administrators. Auditors have read-only access to the rest of the organization.")); return; }
    await render(stage, route);
  }

  for (const section of SECTIONS) {
    cloud.register({
      id: section.id, label: section.label, icon: section.icon, group: "Organization", key: section.key, roles: section.roles, quick: section.quick,
      view: section.id === "org" ? orgView : async (stage) => { Aira.router.go(["org", section.page], {}, { replace: true }); },
    });
  }

  // --------------------------------------------------------------------------------------------
  // Overview
  // --------------------------------------------------------------------------------------------
  A.pages[""] = async (stage) => {
    A.crumbs("Overview");
    stage.replaceChildren(ui.skeleton(6));
    let data;
    try { data = await api("/api/org/dashboard"); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    let directory = null;
    try { directory = await A.directory(true); } catch { /* names are a nicety */ }
    const usage = data.plan_usage;
    const readOnly = cloud.role() === "auditor";
    const orgName = (cloud.me.org && cloud.me.org.name) || "Organization";
    const page = h("div", { class: "adm-page" });
    page.append(A.head(orgName, `${A.titleCase(usage.plan_id)} plan · ${A.titleCase(usage.status)}${readOnly ? " · read-only view" : ""}`,
      !readOnly ? h("a", { class: "btn", href: "#/org/members?invite=candidate", text: "Invite people" }) : null,
      h("a", { class: "btn secondary", href: "#/org/candidates", text: "All candidates" })));

    if (!data.pipeline.total && !data.coach_workload.length) page.append(starterChecklist(readOnly));
    page.append(planUsagePanel(usage));
    page.append(pipelinePanel(data.pipeline));
    const grid = h("div", { class: "adm-grid" });
    grid.append(automationPanel(data.automation_today, directory), attentionPanel(data.needs_attention));
    grid.append(coachPanel(data.coach_workload), outcomesPanel(data.outcomes));
    page.append(grid);
    if (data.audit) page.append(auditPanel(data.audit, directory));
    stage.replaceChildren(page);
  };

  function starterChecklist(readOnly) {
    const steps = [
      ["Invite your coaches", "Coaches run searches and answer held questions for the candidates assigned to them.", "#/org/members?invite=coach"],
      ["Invite your candidates", "Each candidate gets their own workspace, and consents to how applications are run in their name.", "#/org/members?invite=candidate"],
      ["Set the application policy", "Defaults are safe: review before submit, no LinkedIn, 10 applications per candidate a day.", "#/org/policy"],
      ["Connect Google Jobs (optional)", "Add your organization's SerpApi key to search Google Jobs.", "#/org/integrations"],
    ];
    return A.panel("What to do next", h("ol", { class: "adm-checklist", style: { padding: "16px" } }, ...steps.map(([title, text, href], i) => h("li", {}, h("span", { class: "dot", text: String(i + 1) }), h("div", {}, h("b", { text: title }), h("div", { class: "muted", text }), readOnly ? null : h("a", { href, text: "Open" }))))), { sub: "A new organization starts here" });
  }

  function planUsagePanel(usage) {
    const tiles = h("div", { class: "adm-tiles" });
    const billingHref = A.can("billing.manage") ? "#/billing" : null;
    tiles.append(h(billingHref ? "a" : "div", { class: "adm-tile", ...(billingHref ? { href: billingHref } : {}) },
      h("span", { class: "label", text: "Plan" }), h("span", { class: "value", text: A.titleCase(usage.plan_id) }),
      h("span", { class: "sub", text: usage.cancel_at_period_end ? `Ends in ${usage.days_to_renewal} days` : `${A.titleCase(usage.status)} · renews in ${usage.days_to_renewal} days` })));
    tiles.append(A.usageTile("Candidate seats", usage.seats.candidates.used, usage.seats.candidates.limit, "#/org/members"));
    tiles.append(A.usageTile("Coach and admin seats", usage.seats.staff.used, usage.seats.staff.limit, "#/org/members"));
    tiles.append(A.usageTile("Applications this month", usage.applications.used, usage.applications.limit, "#/org/candidates", usage.applications.overage ? `${A.n(usage.applications.overage)} billed as overage` : undefined));
    tiles.append(A.usageTile("Searches", usage.searches.used, null, "#/org/audit?action=candidate.jobs_search", "Candidate and automated searches"));
    tiles.append(A.usageTile("API searches", usage.api_searches.used, usage.api_searches.limit, A.can("integrations.manage") ? "#/api" : "#/org/integrations"));
    tiles.append(A.usageTile("AI cover letters", usage.cover_letters.used, usage.cover_letters.limit, "#/org/audit?action=candidate.job_generate"));
    tiles.append(A.usageTile("Document storage", usage.storage.used_bytes, usage.storage.limit_bytes, "#/org/data", undefined, A.size));
    const history = usage.history || [];
    const peak = Math.max(1, ...history.map((x) => x.applications || 0));
    const bars = history.length > 1 ? h("div", {}, h("h3", { class: "adm-section-title", text: "Applications per month" }),
      h("div", { class: "adm-bars", role: "img", "aria-label": `Applications per month: ${history.slice().reverse().map((x) => `${x.period} ${x.applications || 0}`).join(", ")}` },
        ...history.slice().reverse().map((x) => h("div", { title: `${x.period}: ${x.applications || 0}` }, h("i", { style: { height: `${Math.max(3, (100 * (x.applications || 0)) / peak)}%` } }), h("span", { text: (x.period || "").slice(5) }))))) : null;
    const cost = usage.cost_per_application || {};
    const costLine = cost.browser_seconds ? h("p", { class: "adm-sub", text: `Per application this month: ${cost.browser_seconds}s of browser time, ${cost.model_calls ?? 0} model calls, ${cost.searches ?? 0} searches.` }) : null;
    return A.panel("Plan and usage", h("div", { class: "panel-body", style: { display: "grid", gap: "12px" } }, tiles, bars, costLine), { sub: `Period ${usage.period}` });
  }

  function pipelinePanel(pipeline) {
    const stages = h("div", { class: "adm-pipeline" }, ...pipeline.stages.map((s) => h("a", { class: "adm-stage", href: s.link }, h("span", { class: "name", text: s.stage }), h("span", { class: "count", text: A.n(s.count) }),
      h("span", { class: `adm-delta ${s.change_30d > 0 ? "up" : s.change_30d < 0 ? "down" : ""}`, text: `${s.change_30d > 0 ? "+" : ""}${s.change_30d} in 30 days` }))));
    const extra = pipeline.departed && pipeline.departed.count ? h("p", { class: "adm-sub", text: `${pipeline.departed.count} candidate(s) have left; ${pipeline.departed.placed} of them were placed.` }) : null;
    return A.panel("Pipeline", h("div", { class: "panel-body", style: { display: "grid", gap: "10px" } }, stages, extra), { sub: `${pipeline.total} active candidates` });
  }

  function automationPanel(today, directory) {
    const tiles = h("div", { class: "adm-tiles", style: { padding: "16px 16px 8px" } },
      h("a", { class: "adm-tile", href: "#/org/candidates" }, h("span", { class: "label", text: "Submitted" }), h("span", { class: "value", text: A.n(today.submitted) })),
      h("a", { class: `adm-tile ${today.held ? "warn" : ""}`, href: "#/org/candidates?attention=1" }, h("span", { class: "label", text: "Held for answers" }), h("span", { class: "value", text: A.n(today.held) })),
      h("a", { class: "adm-tile", href: "#/org/candidates?attention=1" }, h("span", { class: "label", text: "Ready for approval" }), h("span", { class: "value", text: A.n(today.ready_for_approval) })),
      h("a", { class: `adm-tile ${today.failed ? "bad" : ""}`, href: "#/org/candidates?attention=1" }, h("span", { class: "label", text: "Failed" }), h("span", { class: "value", text: A.n(today.failed) })));
    const sources = today.by_source.length ? h("div", { class: "adm-scroll" }, h("table", { class: "adm-table" }, h("thead", {}, h("tr", {}, ...["Source", "Submitted", "Held", "Ready", "Failed"].map((t, i) => h("th", { class: i ? "num" : "", text: t })))),
      h("tbody", {}, ...today.by_source.map((s) => h("tr", {}, h("td", { text: A.titleCase(s.source) }), ...["submitted", "needs_review", "ready", "failed"].map((k) => h("td", { class: "num", text: A.n(s[k]) }))))))) : A.empty("No automatic runs yet today.");
    const live = today.live.length ? h("div", { class: "adm-list" }, ...today.live.map((job) => h("div", { class: "adm-item" }, h("div", {}, h("div", { class: "title", text: A.nameOf(directory, job.candidate_id) || "Organization job" }), h("div", { class: "sub", text: job.detail || A.titleCase(job.kind) })), h("div", { class: "aside" }, A.badge(job.status === "running" ? "Running" : "Waiting", job.status === "running" ? "good" : "")))))
      : h("div", { class: "adm-empty", text: "Nothing is running right now." });
    return A.panel("Automation today", h("div", {}, tiles, sources, h("h3", { class: "adm-section-title", style: { padding: "10px 16px 0" }, text: "Running now" }), live), { sub: "Applications, held questions and live passes" });
  }

  function attentionPanel(items) {
    const body = items.length ? h("div", { class: "adm-list" }, ...items.map((item) => h("a", { class: "adm-item", href: item.link || "#/org/candidates" }, h("div", {}, h("div", { class: "title", text: item.candidate || "Candidate" }), h("div", { class: "sub", text: item.label })), h("div", { class: "aside" }, h("span", { class: "btn secondary sm", text: "Fix" })))))
      : A.empty("Nothing needs attention. Held questions older than a day, failed runs, missing resumes and candidates without a coach show up here.");
    return A.panel("Needs attention", body, { sub: `${items.length} item${items.length === 1 ? "" : "s"}` });
  }

  function coachPanel(rows) {
    const body = rows.length ? h("div", { class: "adm-scroll" }, h("table", { class: "adm-table" }, h("thead", {}, h("tr", {}, h("th", { text: "Coach" }), h("th", { class: "num", text: "Candidates" }), h("th", { class: "num", text: "Open items" }), h("th", { text: "Median time to answer" }))),
      h("tbody", {}, ...rows.map((c) => h("tr", {}, h("td", {}, h("a", { href: c.link, text: c.name })), h("td", { class: "num", text: A.n(c.candidates) }), h("td", { class: "num", text: `${A.n(c.open_items)}${c.held ? ` (${c.held} held)` : ""}` }), h("td", { text: A.duration(c.median_answer_seconds) })))))) : A.empty("No coaches yet. Invite a coach to assign candidates.");
    return A.panel("Coach workload", body, { sub: "Last 30 days" });
  }

  function outcomesPanel(outcomes) {
    const table = (rows, label) => rows.length ? h("div", { class: "adm-scroll" }, h("table", { class: "adm-table" }, h("thead", {}, h("tr", {}, h("th", { text: label }), h("th", { class: "num", text: "Applied" }), h("th", { class: "num", text: "Interviews / 100" }), h("th", { class: "num", text: "Offers / 100" }))),
      h("tbody", {}, ...rows.map((r) => h("tr", {}, h("td", {}, h("a", { href: outcomes.link, text: A.titleCase(r.name) }), r.name === "unknown" ? null : null), h("td", { class: "num", text: A.n(r.applications) }), h("td", { class: "num", text: fmt.pct(r.interviews_per_100).replace("%", "") }), h("td", { class: "num", text: fmt.pct(r.offers_per_100).replace("%", "") })))))) : null;
    const bySource = table(outcomes.by_source, "By source");
    const byResume = table(outcomes.by_resume_version, "By resume version");
    const body = bySource || byResume ? h("div", {}, bySource, byResume) : A.empty("Outcomes appear once candidates have submitted applications.");
    return A.panel("Outcomes", body, { sub: "Interviews and offers per 100 applications" });
  }

  const ACTION_WORDS = {
    "org.create": "created the organization", "org.policy": "changed the application policy", "org.branding": "changed branding", "org.suspended": "suspended the organization", "org.reinstated": "reinstated the organization",
    "org.closure_requested": "requested closure", "org.closure_cancelled": "cancelled closure", "member.invite": "invited someone", "member.invite_resend": "resent an invitation", "member.invite_revoke": "withdrew an invitation",
    "member.join": "joined", "member.role_change": "changed a role", "member.remove": "removed a member", "member.verify_email": "verified their email", "member.erased": "erased a member's data",
    "coach.assign": "assigned a coach", "coach.scope_change": "changed a coach's scope", "coach.unassign": "unassigned a coach", "automation.pause_org": "paused automation for the organization", "automation.resume_org": "resumed automation",
    "automation.pause_candidate": "paused automation for a candidate", "automation.resume_candidate": "resumed automation for a candidate", "automation.paused_over_limits": "paused automation: over plan limits", "automation.resumed_within_limits": "resumed automation: within limits again",
    "consent.grant": "gave consent", "consent.revoke": "withdrew consent", "application.submit": "submitted an application", "held.answer": "answered a held question", "auth.login": "signed in", "auth.mfa_enable": "turned on MFA", "auth.mfa_disable": "turned off MFA",
    "auth.mfa_reset": "reset MFA", "auth.password_change": "changed their password", "auth.password_reset": "reset their password", "auth.locked": "was locked out after failed sign-ins", "auth.session_revoke": "signed out a device", "auth.sign_out_devices": "signed out other devices",
    "billing.checkout_start": "started checkout", "billing.upgrade": "upgraded the plan", "billing.downgrade_scheduled": "scheduled a downgrade", "billing.downgrade_cancelled": "cancelled a downgrade", "billing.cancel": "cancelled the subscription", "billing.reactivate": "reactivated the subscription",
    "data.export_org": "exported the organization", "data.export_candidate": "exported a candidate", "data.export_audit": "exported the audit log", "data.delete_candidate": "deleted a candidate workspace", "support.request": "Monosage support asked for access", "support.approve": "approved support access",
    "support.deny": "denied support access", "support.enter": "support entered the organization", "support.end": "support access ended", "support.expired": "support access expired", "linkedin.opt_in": "turned LinkedIn on", "linkedin.easy_apply_opt_in": "turned LinkedIn Easy Apply on",
    "integrations.model_endpoint": "set a model endpoint", "integrations.model_endpoint_removed": "removed the model endpoint",
  };
  A.describe = (event) => {
    const d = event.detail || {};
    if (event.action === "member.invite" && d.email) return `invited ${d.email} as ${d.role || "a member"}`;
    if (event.action === "member.role_change" && d.to) return `changed a role from ${d.from} to ${d.to}`;
    if (event.action === "coach.assign" && d.scope) return `assigned a coach with ${d.scope} scope`;
    if (event.action === "coach.scope_change" && d.scope) return `changed a coach's scope from ${d.previous} to ${d.scope}`;
    if (event.action === "consent.grant" && Array.isArray(d.kinds)) return `gave consent (${d.kinds.map((k) => k.replace(/_/g, " ")).join(", ")})`;
    if (ACTION_WORDS[event.action]) return ACTION_WORDS[event.action];
    if (event.action.startsWith("subscription.")) return `subscription ${event.action.slice(13).replace("_to_", " → ").replace(/_/g, " ")}`;
    if (event.action.startsWith("candidate.")) return event.action.slice(10).replace(/_/g, " ");
    if (event.action.startsWith("integrations.")) return `API: ${event.action.slice(13).replace(/_/g, " ")}`;
    return event.action.replace(/[._]/g, " ");
  };

  // One audit row, shared by the Overview and the Audit log. The actor and the candidate are both shown.
  A.eventRow = (event, directory) => {
    const candidate = event.candidate_id ? (A.nameOf(directory, event.candidate_id) || "a candidate") : "";
    const actor = event.actor_label || event.actor_type;
    const role = (event.actor_role || "").replace("support:", "");
    // Acting in someone's name means working in their workspace (their jobs, applications, answers), not administering them.
    const acting = candidate && ["candidate.", "application.", "held.", "automation.pause_candidate"].some((p) => event.action.startsWith(p)) && event.actor_type === "member" && event.actor_id !== event.candidate_id;
    const skip = new Set(["method", "invite_id", "version", "kinds", "email", "role", "scope", "previous", "from", "to"]);
    const show = (v) => (Array.isArray(v) ? v.join(", ") : v && typeof v === "object" ? (("from" in v || "to" in v) ? `${Array.isArray(v.from) ? v.from.join("/") : v.from} → ${Array.isArray(v.to) ? v.to.join("/") : v.to}` : Object.keys(v).join(", ")) : v);
    const detail = event.detail && Object.keys(event.detail).length ? Object.entries(event.detail).filter(([k, v]) => !skip.has(k) && v !== null && v !== "").slice(0, 4).map(([k, v]) => `${k.replace(/_/g, " ")}: ${show(v)}`).join(" · ") : "";
    return h("div", { class: "adm-item" },
      h("div", {}, h("div", { class: "title", text: `${actor.includes(":") ? actor.split(":").slice(1).join(":") : actor} ${A.describe(event)}${candidate ? ` — ${candidate}` : ""}` }),
        h("div", { class: "meta" }, role ? A.roleBadge(role) : A.badge(event.actor_type), acting ? A.badge("acting in their name", "warn") : null, detail ? h("span", { class: "sub", text: detail }) : null)),
      h("div", { class: "aside" }, h("span", { class: "sub", title: A.when(event.at), text: A.ago(event.at) })));
  };

  function auditPanel(audit, directory) {
    const body = audit.recent.length ? h("div", { class: "adm-list" }, ...audit.recent.map((event) => A.eventRow(event, directory))) : A.empty("No actions recorded yet.");
    return A.panel("Audit", body, { sub: "The last 50 actions", actions: h("a", { class: "btn secondary sm", href: audit.link, text: "Full log" }) });
  }

  // --------------------------------------------------------------------------------------------
  // Candidates
  // --------------------------------------------------------------------------------------------
  const STAGES = ["onboarding", "searching", "applying", "interviewing", "offers", "placed"];

  // --------------------------------------------------------------------------------------------
  // Apps: what the organization can open. cloud-apps.js replaces this with the subscription page;
  // until it loads, the list from /api/apps stands in.
  // --------------------------------------------------------------------------------------------
  if (!A.pages.apps) {
    A.pages.apps = async (stage) => {
      A.crumbs("Apps");
      stage.replaceChildren(ui.skeleton(3));
      let data;
      try { data = await api("/api/apps", { silent: true }); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
      const rows = (data.apps || []).map((app) => h("div", { class: "adm-tile" },
        h("span", { class: "label", text: app.name }),
        h("span", { text: app.description || app.tagline || "" }),
        h("span", { class: app.enabled === false ? "muted" : "", text: app.enabled === false ? (app.reason || "Not subscribed") : "On" })));
      stage.replaceChildren(h("div", { class: "adm-page" }, A.head("Apps", "The apps your organization can open. Each one is a subscription billed with the plan."), rows.length ? h("div", { class: "adm-grid" }, ...rows) : A.empty("No apps are listed for this organization.")));
    };
  }

  A.pages.candidates = async (stage, route) => {
    if (route.path[2]) return candidateDetail(stage, route.path[2]);
    A.crumbs("Candidates");
    const query = route.query;
    const search = h("input", { type: "search", "data-search": "1", placeholder: "Name or email", value: query.q || "", "aria-label": "Search candidates" });
    const stageSelect = ui.select([["", "Any stage"], ...STAGES.map((s) => [s, A.titleCase(s)])], query.stage || "");
    const coachSelect = ui.select([["", "Any coach"]], query.coach || "");
    const attention = h("input", { type: "checkbox", id: "advAttention" }); attention.checked = query.attention === "1";
    const results = h("div", {});
    const apply = (replace = true) => Aira.router.go(["org", "candidates"], { q: search.value.trim(), stage: stageSelect.value, coach: coachSelect.value, attention: attention.checked ? "1" : "" }, { replace });
    let timer = null;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => apply(true), 350); });
    for (const control of [stageSelect, coachSelect, attention]) control.addEventListener("change", () => apply(true));
    stage.replaceChildren(h("div", { class: "adm-page" },
      A.head("Candidates", "Every candidate in the organization with where they are and what needs attention. Open one to see the detail.", A.can("members.manage") ? h("a", { class: "btn", href: "#/org/members?invite=candidate", text: "Invite candidates" }) : null),
      h("div", { class: "filters" }, h("div", { class: "field grow" }, h("label", { for: "advSearch", text: "Search" }), (search.id = "advSearch", search)), ui.field("Stage", stageSelect), ui.field("Coach", coachSelect), h("label", { class: "adm-check", for: "advAttention", style: { alignSelf: "end" } }, attention, h("span", { class: "what" }, h("b", { text: "Needs attention only" })))), results));
    try {
      const dir = await A.directory();
      for (const coach of dir.members.filter((m) => m.role === "coach")) coachSelect.append(h("option", { value: coach.member_id, text: coach.name || coach.email, selected: coach.member_id === query.coach }));
    } catch { /* the filter still works without names */ }
    results.replaceChildren(ui.skeleton(5));
    try {
      const data = await api(`/api/org/candidates${A.q({ q: query.q, stage: query.stage, coach: query.coach, attention: query.attention })}`);
      results.replaceChildren(candidateList(data.candidates));
    } catch (error) { results.replaceChildren(ui.error(error, () => Aira.router.render())); }
  };

  function candidateList(rows) {
    if (!rows.length) return h("div", { class: "panel" }, A.empty("No candidates match. Invite candidates from Members, or clear the filters."));
    return h("div", { class: "panel" }, h("div", { class: "adm-list" }, ...rows.map((c) => h("a", { class: "adm-item", href: `#/org/candidates/${c.candidate_id}` },
      h("div", {}, h("div", { class: "title", text: c.name || c.email }), h("div", { class: "sub", text: c.name ? c.email : "" }),
        h("div", { class: "meta" }, h("span", { class: `badge stage ${c.stage}`, text: c.stage }), c.paused ? A.badge("Paused", "warn") : null, c.workspace_status !== "active" ? A.badge(c.workspace_status, "warn") : null,
          c.linkedin_connected ? A.badge("LinkedIn", "good") : null,
          ...c.coaches.map((coach) => A.badge(`${coach.name} · ${coach.scope}`, "coach")),
          !c.coaches.length ? A.badge("No coach", "bad") : null,
          ...c.needs_attention.map((issue) => A.badge(issue.label, "warn")))),
      h("div", { class: "aside" }, h("span", { class: "sub", text: `${c.held} held · ${c.ready} ready · ${c.submissions_30d} applied (30 d) · ${c.interviews} interviews` }), h("span", { class: "sub", text: c.last_activity_at ? `Active ${A.ago(c.last_activity_at)}` : "No activity yet" }))))));
  }

  async function candidateDetail(stage, id) {
    A.crumbs("Candidates", "Detail");
    stage.replaceChildren(ui.skeleton(6));
    let data; let assignments = null;
    try { data = await api(`/api/org/candidates/${id}`); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    try { assignments = await api("/api/org/assignments", { silent: true }); } catch { /* only needed to offer scopes */ }
    const c = data.candidate; const name = c.name || c.email;
    const reload = () => Aira.router.render();
    const actions = [];
    actions.push(h("button", { type: "button", class: "btn", text: "Open workspace (read-only)", onclick: () => cloud.enterWorkspace({ id, name, scope: "read-only" }) }));
    if (A.can("members.manage") || A.can("coaches.assign")) {
      actions.push(data.paused.paused
        ? h("button", { type: "button", class: "btn secondary", text: "Resume automation", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api(`/api/org/candidates/${id}/resume`, { method: "POST", body: {} }); Aira.toast("Automation resumed for this candidate.", "success"); reload(); }) })
        : h("button", { type: "button", class: "btn secondary", text: "Pause automation", onclick: async () => {
          const reason = await A.askReason({ title: `Pause automation for ${name}`, message: "Their search plans stop running and no application is submitted until you resume. The reason shows on the dashboard and to the candidate.", confirmLabel: "Pause" });
          if (!reason) return;
          try { await api(`/api/org/candidates/${id}/pause`, { method: "POST", body: { reason } }); Aira.toast("Automation paused.", "success"); cloud.refreshBanners(); reload(); } catch (error) { Aira.reportError(error); }
        } }));
    }
    if (A.can("coaches.assign")) actions.push(h("button", { type: "button", class: "btn secondary", text: "Assign coach", onclick: () => assignCoachModal(id, name, assignments, reload) }));
    if (A.can("export.org")) actions.push(h("button", { type: "button", class: "btn secondary", text: "Export", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
      const made = await api("/api/org/exports", { method: "POST", body: { candidate_id: id } });
      Aira.toast(`Export ready (${fmt.bytes(made.size)}).`, "success");
      await A.download(`/api/org/exports/${made.export_id}/download`, `aira-export-${name.replace(/\W+/g, "-")}.zip`);
    }) }));
    if (A.can("candidates.manage")) actions.push(h("button", { type: "button", class: "btn danger secondary", text: "Delete workspace", onclick: async () => {
      const ok = await A.confirmTyped({ title: `Delete ${name}'s workspace`, message: "Their profile, jobs, applications, documents and browser profile are deleted for good and their membership ends. Export first if you may need them.", expected: c.email, confirmLabel: "Delete workspace" });
      if (!ok) return;
      try { await api(`/api/org/candidates/${id}/workspace`, { method: "DELETE" }); Aira.toast("Workspace deleted.", "success"); A.forgetDirectory(); Aira.router.go(["org", "candidates"]); } catch (error) { Aira.reportError(error); }
    } }));

    const s = data.snapshot;
    const page = h("div", { class: "adm-page" });
    page.append(h("div", { class: "adm-crumb" }, h("a", { href: "#/org/candidates", text: "← Candidates" })));
    page.append(h("div", { class: "adm-head" }, h("div", {}, h("h1", { text: name }), h("div", { class: "adm-btnrow", style: { marginTop: "6px" } }, h("span", { class: `badge stage ${c.stage}`, text: c.stage }), c.workspace_status !== "active" ? A.badge(c.workspace_status, "warn") : null, data.paused.paused ? A.badge("Automation paused", "warn") : A.badge("Automation on", "good"), h("span", { class: "sub", text: c.email }))), h("div", { class: "adm-btnrow" }, ...actions)));
    if (data.paused.paused) page.append(h("div", { class: "adm-note warn", role: "status", text: `Paused ${A.ago(data.paused.since)}: ${data.paused.reason || "no reason given"}.` }));
    if (!data.can_see_sensitive) page.append(h("div", { class: "adm-note", text: "Sensitive profile fields (work authorization, visa, demographics, salary) are entered and seen by the candidate only. You can see whether each is set, not its value." }));
    page.append(h("div", { class: "adm-tiles" },
      ...[["Jobs", s.jobs], ["Search plans on", `${s.plans_enabled}/${s.plans}`], ["Applied (30 d)", s.submissions_30d], ["Interviews", s.interviews], ["Offers", s.offers], ["Held", s.held], ["Ready for approval", s.ready], ["Failed (24 h)", s.failed_24h], ["Documents", fmt.bytes(s.documents_bytes || 0)]].map(([label, value]) => h("div", { class: "adm-tile" }, h("span", { class: "label", text: label }), h("span", { class: "value", text: String(value) })))));
    const grid = h("div", { class: "adm-grid" });
    grid.append(A.panel("Onboarding", h("div", { class: "panel-body" }, h("p", { class: "adm-sub", text: `${data.onboarding.done} of ${data.onboarding.total} required steps done` }), A.meter(data.onboarding.done, data.onboarding.total),
      h("ul", { class: "adm-checklist", style: { marginTop: "10px" } }, ...data.onboarding.items.map((item) => h("li", { class: item.done ? "done" : "" }, h("span", { class: "dot", text: item.done ? "✓" : "" }), h("span", { text: item.label + (item.optional ? " (optional)" : "") })))))));
    grid.append(A.panel("Consent", h("div", { class: "panel-body" }, data.consent.complete ? A.badge("Required consents given", "good") : A.badge("Consent needed before automation runs", "warn"),
      h("dl", { class: "adm-dl", style: { marginTop: "10px" } }, ...data.consent.required.concat(Object.keys(data.consent.granted).filter((k) => !data.consent.required.includes(k))).flatMap((kind) => { const g = data.consent.granted[kind]; return [h("dt", { text: A.titleCase(kind) }), h("dd", { text: g ? `v${g.version} · ${A.when(g.granted_at)}${g.current ? "" : " (older version)"}` : "Not given" })]; })))));
    grid.append(A.panel("LinkedIn", h("div", { class: "panel-body" }, h("dl", { class: "adm-dl" }, h("dt", { text: "Session" }), h("dd", { text: data.linkedin.connected ? "Connected" : "Not connected" }), h("dt", { text: "Turned on by the candidate" }), h("dd", { text: data.linkedin.opted_in ? "Yes, after the User Agreement warning" : "No" }), h("dt", { text: "Easy Apply" }), h("dd", { text: data.linkedin.easy_apply ? "On" : "Off" })),
      h("p", { class: "adm-sub", style: { marginTop: "8px" }, text: "LinkedIn is the candidate's own session. Only they sign in or turn it on." }))));
    grid.append(A.panel("Coaches", coachesBody(data, id, name, assignments, reload), { sub: "Scope controls what a coach may do" }));
    grid.append(A.panel("Needs attention", data.needs_attention.length ? h("div", { class: "adm-list" }, ...data.needs_attention.map((i) => h("a", { class: "adm-item", href: i.link }, h("div", { class: "title", text: i.label }), h("div", { class: "aside" }, h("span", { class: "btn secondary sm", text: "Fix" }))))) : A.empty("Nothing needs attention.")));
    grid.append(A.panel("Held questions and approvals", data.queue.length ? h("div", { class: "adm-list" }, ...data.queue.map((q) => h("div", { class: "adm-item" }, h("div", {}, h("div", { class: "title", text: `${q.title || "Application"}${q.company ? " · " + q.company : ""}` }), h("div", { class: "sub", text: q.status === "ready" ? "Filled and waiting for approval" : (q.questions || []).join(" · ") || "Questions to answer" })), h("div", { class: "aside" }, A.badge(q.status === "ready" ? "Ready" : "Held", "warn"), h("span", { class: "sub", text: `since ${A.ago(Date.parse(q.held_since) / 1000)}` }))))) : A.empty("No held questions or approvals waiting."), { sub: "Answered by the candidate or an assigned coach" }));
    page.append(grid);
    stage.replaceChildren(page);
  }

  function coachesBody(data, id, name, assignments, reload) {
    if (!data.coaches.length) return A.empty("No coach assigned.");
    return h("div", { class: "adm-list" }, ...data.coaches.map((coach) => h("div", { class: "adm-item" },
      h("div", {}, h("div", { class: "title", text: coach.name || coach.email }), h("div", { class: "sub", text: coach.effective_scope !== coach.scope ? `Assigned ${coach.scope}, the plan allows ${coach.effective_scope}` : coach.email })),
      h("div", { class: "aside" }, A.badge(coach.effective_scope, "coach"),
        A.can("coaches.assign") ? h("button", { type: "button", class: "btn ghost sm", text: "Change", onclick: () => assignCoachModal(id, name, assignments, reload, coach) }) : null,
        A.can("coaches.assign") ? h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: async () => { if (!(await Aira.confirm({ title: "Remove coach", message: `${coach.name || coach.email} will no longer see or act for ${name}.`, confirmLabel: "Remove", danger: true }))) return; try { await api(`/api/org/assignments/${coach.coach_id}/${id}`, { method: "DELETE" }); Aira.toast("Coach removed.", "success"); reload(); } catch (error) { Aira.reportError(error); } } }) : null))));
  }

  // Assign a coach to a candidate, or change the scope of an existing assignment.
  async function assignCoachModal(candidateId, name, assignments, after, existing) {
    let coaches = [];
    try { coaches = (await A.directory()).members.filter((m) => m.role === "coach"); } catch (error) { Aira.reportError(error); return; }
    if (!coaches.length) { Aira.toast("There is no coach yet. Invite one from Members.", "error"); return; }
    const scopes = (assignments && assignments.scopes) || ["view", "act", "approve"];
    const coach = ui.select(coaches.map((m) => [m.member_id, m.name || m.email]), existing ? existing.coach_id : coaches[0].member_id, existing ? { disabled: true } : {});
    const scope = ui.select(["view", "act", "approve"].map((s) => [s, `${A.titleCase(s)}${scopes.includes(s) ? "" : " (not in your plan)"}`]), existing ? existing.scope : "act");
    const hint = h("p", { class: "adm-sub", text: "View: read only. Act: search and prepare, answer held questions. Approve: also submit applications for the candidate." });
    const error = h("div", { class: "adm-inline-error", role: "alert" });
    Aira.modal({ title: existing ? `Change scope for ${name}` : `Assign a coach to ${name}`, body: h("div", { class: "adm-form" }, ui.field("Coach", coach), ui.field("Scope", scope), hint, error),
      actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save", onClick: async (close) => { try { await api("/api/org/assignments", { method: "PUT", body: { coach_id: coach.value, candidate_id: candidateId, scope: scope.value } }); Aira.toast("Coach assignment saved.", "success"); close(); after(); return false; } catch (e) { if (e.status === 402) { close(); return false; } error.textContent = A.message(e); return false; } } }] });
  }
  A.assignCoachModal = assignCoachModal;
})();

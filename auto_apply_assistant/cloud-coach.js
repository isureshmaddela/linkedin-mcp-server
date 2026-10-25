/* Aira hosted build — the candidate's and the coach's experience.
 *
 * Candidate: consent and onboarding, the checklist and the organization's rules on the Dashboard, LinkedIn and Easy Apply
 * (always behind the User Agreement warning), pausing their own automation, and the record of what was done in their name.
 * Coach: their candidates with open items, one queue of held questions across all of them, and opening a workspace to act
 * for a candidate at the scope the organization granted (view < act < approve).
 */
"use strict";

(function () {
  // -------------------------------------------------------------------------
  // Pure logic
  // -------------------------------------------------------------------------
  const LEVEL = { view: 1, act: 2, approve: 3 };
  const atLeast = (scope, needed) => (LEVEL[scope] || 0) >= LEVEL[needed];

  const CONSENTS = {
    automation: {
      title: "Applications in my name",
      text: "My organization may search for jobs and fill in and, within its rules, submit applications on my behalf using my profile, my resume and the answers I have confirmed. Every submission keeps a screenshot and the confirmation page, and I can pause automation at any time.",
    },
    coach_access: {
      title: "Coach access",
      text: "A coach assigned to me can see my jobs, applications, documents and the evidence of what was submitted, up to the access level the organization gave them. They can act for me only at that level, and everything they do is recorded here with their name and mine.",
    },
    sensitive_policy: {
      title: "Sensitive information stays with me",
      text: "Work authorization, visa status, demographics and salary are entered and confirmed by me only. Coaches and administrators can see that these are set, not the values, unless my organization's policy allows it and I agree separately. These values are never sent to the AI model and never filled into a form without an answer I confirmed.",
    },
  };

  function heldHours(since, now = Date.now()) {
    if (!since) return null;
    const stamp = typeof since === "number" ? since * 1000 : Date.parse(String(since).includes("T") || String(since).includes("+") ? since : since + "Z");
    return Number.isNaN(stamp) ? null : Math.max(0, (now - stamp) / 3600000);
  }

  function heldLabel(hours) {
    if (hours === null) return "";
    if (hours < 1) return "held for under an hour";
    if (hours < 48) return `held for ${Math.floor(hours)} h`;
    return `held for ${Math.floor(hours / 24)} days`;
  }

  // What a coach can do with a queue item at their scope.
  function queueAction(item) {
    const scope = item.scope || "view";
    if (item.status === "needs_review") return atLeast(scope, "act") ? { kind: "answer", label: "Answer" } : { kind: "view", label: "View only" };
    return atLeast(scope, "approve") ? { kind: "approve", label: "Review and submit" } : { kind: "view", label: "View only" };
  }

  const logic = { LEVEL, atLeast, CONSENTS, heldHours, heldLabel, queueAction };
  if (typeof window === "undefined") { module.exports = logic; return; }

  // -------------------------------------------------------------------------
  // Browser
  // -------------------------------------------------------------------------
  const { api, ui, fmt } = Aira;
  const cloud = Aira.cloud;
  const router = Aira.router;
  const coach = (cloud.coach = { logic });

  const panel = (title, right, ...body) => h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: title }), right || null), ...body);
  const body = (...children) => h("div", { class: "panel-body" }, ...children);
  const chip = (text, tone = "") => h("span", { class: `ca-chip ${tone}`, text });
  const scopeBadge = (scope) => h("span", { class: `badge scope-${scope}`, title: { view: "Can look, cannot change anything", act: "Can search and prepare, cannot submit", approve: "Can also submit applications" }[scope] || "", text: `${scope} access` });
  const stageBadge = (stage) => h("span", { class: "badge ca-stage", text: String(stage || "onboarding") });

  function scopeOf(candidateId) {
    const found = ((cloud.me && cloud.me.assigned_candidates) || []).find((c) => c.candidate_id === candidateId);
    return found ? found.scope : "";
  }

  // -------------------------------------------------------------------------
  // Candidate: consent and onboarding
  // -------------------------------------------------------------------------
  async function onboardingView(stage) {
    const [consent, dash] = await Promise.all([api("/api/me/consent"), api("/api/org/dashboard")]);
    const org = cloud.me.org.name;
    const rules = dash.rules || {};
    const coaches = dash.coaches || [];
    const checks = {};
    const boxes = consent.required.map((kind) => {
      const info = CONSENTS[kind] || { title: kind, text: "" };
      const granted = consent.granted[kind] && consent.granted[kind].current;
      const input = h("input", { type: "checkbox", id: `consent-${kind}`, checked: !!granted, disabled: !!granted });
      checks[kind] = input;
      return h("label", { class: "ca-consent", for: input.id }, input, h("span", {}, h("strong", { text: info.title }), h("span", { text: info.text })));
    });
    const optional = h("input", { type: "checkbox", id: "consent-sensitive_access", checked: !!(consent.granted.sensitive_access && consent.granted.sensitive_access.current) });
    const coachRows = coaches.length ? coaches.map((c) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: c.coach_name || c.coach_email }), h("span", { class: "sub", text: c.coach_email })), scopeBadge(c.effective_scope))) : [h("div", { class: "empty", text: "No coach is assigned to you yet." })];
    const policy = cloud.me.policy || {};
    const submit = h("button", { type: "submit", class: "btn", text: consent.complete ? "Update my choices" : "Agree and continue" });
    const form = h("form", { novalidate: true },
      ...boxes,
      h("label", { class: "ca-consent optional", for: optional.id }, optional, h("span", {}, h("strong", { text: "Optional: let my coach see sensitive values" }), h("span", { text: `Only if ${org} allows it in its policy${policy.coach_sees_sensitive === false ? "" : ""}. You can withdraw this at any time.` }))),
      h("p", { class: "muted", text: `Consent version ${consent.version}. Recorded with the date, and you can review or withdraw it later.` }),
      h("div", { class: "btn-row" }, submit));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const missing = consent.required.filter((kind) => !checks[kind].checked);
      if (missing.length) { Aira.toast("Tick each of the three statements to continue. They explain how your organization works for you.", "error"); checks[missing[0]].focus(); return; }
      await Aira.withBusy(submit, async () => {
        const kinds = [...consent.required, ...(optional.checked ? ["sensitive_access"] : [])];
        await api("/api/me/consent", { method: "POST", body: { kinds } });
        if (!optional.checked && consent.granted.sensitive_access) await api("/api/me/consent/sensitive_access", { method: "DELETE" });
        cloud.me.consent = await api("/api/me/consent");
        cloud.buildNav();
        Aira.toast("Thank you. Your choices are saved.", "success");
        router.go(["dashboard"]);
      });
    });
    stage.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: `Welcome to ${org}` }), h("p", { text: "Before anything runs for you, here is plainly what happens and what stays yours." }))),
      h("div", { class: "grid sidebar-right" },
        panel("How this works", null, body(h("p", { text: `${org} pays for Aira so that you have help with your job search. It works from your profile and resume, finds roles, prepares applications and, if its rules allow, submits them for you.` }), form)),
        h("div", { class: "grid" },
          panel("Your organization's rules", null, body(rulesList(rules, cloud.me.policy))),
          panel("Your coaches", null, h("div", { class: "list" }, ...coachRows)))));
  }

  function rulesList(rules, policy) {
    const mode = rules.submission_mode === "auto" ? "Applications can be submitted automatically once you turn that on in a search plan." : "Applications are filled in and wait for a person to submit them.";
    const sources = (rules.allowed_sources || []).map((s) => s.replaceAll("_", " "));
    return h("ul", { class: "ca-rules" },
      h("li", { text: mode }),
      h("li", { text: rules.daily_cap ? `Up to ${rules.daily_cap} applications a day for you.` : "There is no daily limit set for you." }),
      h("li", { text: sources.length ? `Jobs are searched on: ${sources.join(", ")}.` : "No job sources are enabled yet." }),
      h("li", { text: rules.linkedin_allowed ? "LinkedIn is allowed, and stays off until you turn it on yourself." : "LinkedIn is turned off by your organization." }),
      h("li", { text: rules.cover_letters ? "AI-drafted cover letters are on." : "AI-drafted cover letters are off." }),
      policy && policy.paused ? h("li", { text: "Automation is paused for the whole organization right now." }) : null);
  }

  // -------------------------------------------------------------------------
  // Candidate: dashboard cards
  // -------------------------------------------------------------------------
  const originalDashboard = Aira.views.dashboard;

  async function candidateCards(slot) {
    let dash;
    try { dash = await api("/api/org/dashboard", { silent: true }); }
    catch { return; }
    const cards = [];
    if (dash.onboarding && !dash.onboarding.complete) cards.push(checklistCard(dash.onboarding));
    cards.push(pauseCard(dash));
    cards.push(rulesCard(dash));
    cards.push(linkedinCard(dash));
    if ((dash.queue || []).length) cards.unshift(queueCard(dash.queue));
    slot.replaceChildren(h("div", { class: "ca-cards" }, ...cards));
  }

  function checklistCard(onboarding) {
    return panel(`Getting set up · ${onboarding.done} of ${onboarding.total}`, null, h("div", { class: "panel-body" },
      h("div", { class: "ca-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(onboarding.total), "aria-valuenow": String(onboarding.done) }, h("i", { style: { width: `${Math.round((100 * onboarding.done) / Math.max(1, onboarding.total))}%` } })),
      h("div", { class: "checklist" }, ...onboarding.items.map((item) => {
        const label = `${item.label}${item.optional ? "" : ""}`;
        return item.link ? h("a", { class: item.done ? "done" : "", href: item.link }, h("i", { text: item.done ? "✓" : "" }), h("span", { text: label })) : h("div", { class: `ca-static ${item.done ? "done" : ""}` }, h("i", { text: item.done ? "✓" : "" }), h("span", { text: label }));
      }))));
  }

  function queueCard(queue) {
    const open = queue.filter((item) => item.status === "needs_review").length;
    return panel("Waiting for you", h("a", { class: "btn secondary sm", href: "#/jobs/autopilot", text: "Open Auto-apply" }), h("div", { class: "list" }, ...queue.slice(0, 4).map((item) => h("a", { class: "row", href: "#/jobs/autopilot" },
      h("div", {}, h("span", { class: "title", text: `${item.title || "Application"}${item.company ? " · " + item.company : ""}` }), h("span", { class: "sub", text: item.status === "needs_review" ? `${(item.questions || []).length} question(s) need your answer` : "Filled in and waiting for your OK" })),
      h("small", { class: "muted", text: heldLabel(heldHours(item.held_since)) })))),
      open ? null : null);
  }

  function rulesCard(dash) {
    const today = dash.today || {};
    const rules = dash.rules || {};
    const coaches = dash.coaches || [];
    return panel("Your organization's rules", null, body(
      rulesList(rules, { paused: (dash.banners || []).some((b) => b.kind === "paused") }),
      h("p", { class: "muted", text: `Today: ${today.applications || 0} application(s) sent${rules.daily_cap ? ` of ${rules.daily_cap}` : ""}.` }),
      coaches.length ? h("div", { class: "ca-coaches" }, h("strong", { text: "Your coaches" }), ...coaches.map((c) => h("div", {}, h("span", { text: c.coach_name || c.coach_email }), scopeBadge(c.effective_scope)))) : null,
      h("p", { class: "muted" }, "See everything done in your name under ", h("a", { href: "#/activity", text: "Activity" }), ".")));
  }

  function pauseCard(dash) {
    const state = dash.paused || {};
    const me = cloud.me;
    const toggle = h("button", { type: "button", class: `btn ${state.paused ? "" : "secondary"}`, text: state.paused ? "Resume my automation" : "Pause my automation" });
    toggle.addEventListener("click", async () => {
      if (state.paused) {
        await Aira.withBusy(toggle, async () => {
          await api(`/api/org/candidates/${me.member_id}/resume`, { method: "POST", body: {} });
          Aira.toast("Automation resumed.", "success");
          router.render();
        });
        return;
      }
      const reason = h("input", { type: "text", maxlength: 200, placeholder: "For example: interviewing this week" });
      Aira.modal({
        title: "Pause my automation",
        body: h("div", {}, h("p", { class: "muted", text: "Nothing is searched or sent for you until you resume. Your coach and administrators can see the reason." }), ui.field("Reason", reason)),
        actions: [{ label: "Cancel", kind: "ghost" }, { label: "Pause", onClick: async () => {
          if (!reason.value.trim()) { Aira.toast("Give a short reason.", "error"); return false; }
          await api(`/api/org/candidates/${me.member_id}/pause`, { method: "POST", body: { reason: reason.value } });
          Aira.toast("Automation paused.", "success");
          router.render();
        } }],
      });
    });
    return panel("Automation", state.paused ? chip("Paused", "warn") : chip("Running", "ok"), body(
      h("p", { text: state.paused ? `Paused: ${state.reason || "no reason given"}.` : "Searches and applications run for you under your organization's rules." }), h("div", { class: "btn-row" }, toggle)));
  }

  function linkedinCard(dash) {
    const me = cloud.me;
    const rules = dash.rules || {};
    const connected = !!me.linkedin_connected;
    const optedIn = !!(me.workspace && me.workspace.linkedin_opted_in);
    if (!rules.linkedin_allowed) {
      return panel("LinkedIn (optional)", chip("Off", ""), body(h("p", { class: "muted", text: "Your organization has LinkedIn turned off. Aira finds jobs on the other sources without it." })));
    }
    const connect = h("button", { type: "button", class: "btn secondary", text: connected ? "LinkedIn is connected" : "Connect LinkedIn", disabled: connected });
    connect.addEventListener("click", () => Aira.withBusy(connect, async () => {
      if (!(await cloud.auth.confirmLinkedIn())) return;
      try {
        const result = await api("/api/linkedin/connect", { method: "POST", body: { acknowledge: true } });
        cloud.me.workspace.linkedin_opted_in = true;
        if (result && result.remote_login) {
          // A remote browser on a worker host: a short-lived link opens it in this person's own browser.
          const minutes = Math.max(1, Math.round((result.remote_login.expires_in || 600) / 60));
          Aira.modal({ title: "Sign in to LinkedIn", body: h("div", {}, h("p", { text: "LinkedIn opens in a private browser that Aira runs for you, shown in a new window. Sign in there yourself, including any security check. Aira does not store or log what you type; it keeps only the signed-in session." }), h("p", { class: "muted", text: `The link works once and expires in ${minutes} minutes. The window closes by itself when you are signed in.` })), actions: [{ label: "Later", kind: "ghost" }, { label: "Open the sign-in window", onClick: () => { window.open(result.remote_login.url, "_blank", "noopener"); } }] });
        } else {
          Aira.toast("A LinkedIn sign-in window opens next. Complete it there.", "success");
        }
        router.render();
      } catch (error) { Aira.reportError(error); }
    }));
    const easy = h("button", { type: "button", class: "btn secondary", text: "Turn on Easy Apply" });
    easy.addEventListener("click", () => Aira.withBusy(easy, async () => {
      if (!(await cloud.auth.confirmLinkedIn("I understand, turn it on"))) return;
      try {
        await api("/api/me/easy-apply", { method: "POST", body: { enable: true, acknowledge: true } });
        Aira.toast("Easy Apply is on. Turn it on per search plan too.", "success");
      } catch (error) { Aira.reportError(error); }
    }));
    return panel("LinkedIn (optional)", chip(connected ? "Connected" : optedIn ? "Acknowledged" : "Off", connected ? "ok" : ""), body(
      h("p", { class: "muted", text: "LinkedIn forbids automated access in its User Agreement, so this is your own choice and your own session. Aira works without it." }),
      h("div", { class: "btn-row" }, connect, easy)));
  }

  // The API usage widget is for the organization's administrators; everyone else keeps the Careers one.
  const originalWidgets = Aira.dashboardWidgets;
  if (originalWidgets) Aira.dashboardWidgets = () => { const nodes = originalWidgets(); return cloud.can("integrations.read") ? nodes : nodes.slice(1); };

  Aira.views.dashboard = async (main, route) => {
    if (cloud.role() !== "candidate" && !Aira.state.actingFor) {
      // No candidate workspace is open: the workspace Dashboard has nothing to show. Go to this role's own home.
      const home = cloud.landing(false);
      if (home && home[0] !== "dashboard") { router.go(home, {}, { replace: true }); return; }
    }
    if (cloud.role() !== "candidate" || Aira.state.actingFor) return originalDashboard(main, route);
    const slot = h("div", { class: "ca-top" });
    const rest = h("div");
    main.replaceChildren(slot, rest);
    candidateCards(slot);
    await originalDashboard(rest, route);
  };

  // -------------------------------------------------------------------------
  // Candidate: activity in my name
  // -------------------------------------------------------------------------
  const ACTIONS = {
    "application.submit": "Submitted an application",
    "held.answer": "Answered a held question",
    "coach.assign": "Was assigned as your coach",
    "coach.scope_change": "Changed a coach's access level",
    "coach.unassign": "Was removed as your coach",
    "automation.pause_candidate": "Paused your automation",
    "automation.resume_candidate": "Resumed your automation",
    "consent.grant": "You recorded your consent",
    "consent.revoke": "You withdrew a consent",
    "linkedin.opt_in": "LinkedIn was connected after the warning",
    "member.remove": "Membership ended",
    "data.export_candidate": "Exported your data",
  };
  const describe = (event) => ACTIONS[event.action] || fmt.title(event.action.replaceAll(".", " ").replaceAll("_", " ").replace(/^candidate /, ""));

  async function activityView(stage) {
    const data = await api("/api/me/activity?limit=200");
    const rows = data.events.map((event) => {
      const mine = /^candidate:/.test(event.actor) || event.role === "candidate";
      return h("tr", {}, h("td", {}, h("time", { datetime: new Date(event.at * 1000).toISOString(), title: fmt.datetime(event.at), text: fmt.ago(event.at) })), h("td", {}, h("span", { class: `ca-actor ${mine ? "me" : "other"}`, text: mine ? "You" : `${event.actor.split(":").slice(1).join(":") || event.actor}` }), event.role && !mine ? h("small", { class: "muted", text: ` (${event.role})` }) : null), h("td", { text: describe(event) }), h("td", { class: "muted", text: event.target && event.target.length < 40 ? event.target : "" }));
    });
    stage.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Activity in my name" }), h("p", { text: "Every action taken in your workspace, by you or by someone acting for you. Coaches and administrators are always named." }))),
      data.events.length ? h("div", { class: "panel" }, h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, ...["When", "Who", "What", "Detail"].map((t) => h("th", { text: t })))), h("tbody", {}, ...rows)))) : ui.empty("Nothing yet", "Actions taken in your workspace will appear here."));
  }

  // -------------------------------------------------------------------------
  // Coach: my candidates
  // -------------------------------------------------------------------------
  async function coachHome(stage) {
    let filter = Aira.recall("coachFilter", { q: "", attention: false });
    const host = h("div");
    const search = h("input", { type: "search", "data-search": "", placeholder: "Search candidates", value: filter.q, "aria-label": "Search candidates" });
    const attention = h("input", { type: "checkbox", id: "coachAttention", checked: filter.attention });
    const bar = h("div", { class: "toolbar ca-toolbar" }, search, h("label", { class: "ca-check", for: "coachAttention" }, attention, h("span", { text: "Needs attention only" })));
    stage.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "My candidates" }), h("p", { text: "Open a candidate's workspace to act for them at the access level you were given." }))), bar, host);
    let timer = 0;
    const load = async () => {
      filter = { q: search.value.trim(), attention: attention.checked };
      Aira.remember("coachFilter", filter);
      host.replaceChildren(ui.skeleton(3));
      try {
        const data = await api(`/api/org/candidates?${new URLSearchParams({ q: filter.q, attention: filter.attention ? "1" : "" })}`);
        host.replaceChildren(...(data.candidates.length ? [h("div", { class: "ca-grid" }, ...data.candidates.map(candidateCard))] : [ui.empty(filter.q || filter.attention ? "No candidates match" : "No candidates yet", filter.q || filter.attention ? "Clear the filters to see everyone." : "An administrator assigns candidates to you.")]));
      } catch (error) { host.replaceChildren(ui.error(error, load)); }
    };
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    attention.addEventListener("change", load);
    await load();
  }

  function candidateCard(candidate) {
    const scope = scopeOf(candidate.candidate_id) || (candidate.coaches.find((c) => c.coach_id === cloud.me.member_id) || {}).scope || "view";
    const open = (candidate.held || 0) + (candidate.ready || 0);
    return h("article", { class: "card ca-candidate" },
      h("div", { class: "ca-candidate-head" }, h("div", {}, h("strong", { text: candidate.name || candidate.email }), h("div", { class: "muted", text: candidate.email })), stageBadge(candidate.stage)),
      h("div", { class: "ca-chips" }, scopeBadge(scope), candidate.paused ? chip("Paused", "warn") : null, open ? chip(`${open} open item${open === 1 ? "" : "s"}`, "warn") : chip("Nothing waiting", "ok"), ...(candidate.needs_attention || []).map((issue) => chip(issue.label, "bad"))),
      h("dl", { class: "kv ca-kv" }, h("dt", { text: "Applied (30 days)" }), h("dd", { text: String(candidate.submissions_30d || 0) }), h("dt", { text: "Interviews" }), h("dd", { text: String(candidate.interviews || 0) }), h("dt", { text: "Last activity" }), h("dd", { text: candidate.last_activity_at ? fmt.ago(candidate.last_activity_at) : "none yet" })),
      h("div", { class: "btn-row" },
        h("button", { type: "button", class: "btn sm", text: "Open workspace", onclick: () => cloud.enterWorkspace({ id: candidate.candidate_id, name: candidate.name || candidate.email, scope }) }),
        h("button", { type: "button", class: "btn secondary sm", text: "Details", onclick: () => candidateDrawer(candidate, scope) })));
  }

  async function candidateDrawer(candidate, scope) {
    const content = h("div", {}, ui.skeleton(4));
    const drawer = Aira.drawer({ title: candidate.name || candidate.email, subtitle: h("span", {}, scopeBadge(scope), " ", stageBadge(candidate.stage)), body: content });
    try {
      const data = await api(`/api/org/candidates/${candidate.candidate_id}`);
      const pauseButton = atLeast(scope, "act") ? h("button", { type: "button", class: "btn secondary sm", text: data.paused.paused ? "Resume automation" : "Pause automation", onclick: () => togglePause(candidate, data, drawer) }) : h("span", { class: "muted", text: "View access cannot pause automation." });
      content.replaceChildren(
        h("section", {}, h("h3", { text: "Setup" }), h("div", { class: "checklist" }, ...data.onboarding.items.map((item) => h("div", { class: `ca-static ${item.done ? "done" : ""}` }, h("i", { text: item.done ? "✓" : "" }), h("span", { text: item.label }))))),
        h("section", { class: "ca-section" }, h("h3", { text: "Consent" }), data.consent.complete ? h("p", { text: `Recorded (version ${data.consent.version}).` }) : h("p", { class: "ca-warn", text: "The candidate has not given every consent yet, so automation will not run." })),
        h("section", { class: "ca-section" }, h("h3", { text: "Automation" }), h("p", { text: data.paused.paused ? `Paused: ${data.paused.reason}` : "Running." }), pauseButton),
        h("section", { class: "ca-section" }, h("h3", { text: "Coaches" }), ...(data.coaches.length ? data.coaches.map((c) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: c.name || c.email })), scopeBadge(c.effective_scope))) : [h("p", { class: "muted", text: "None." })])),
        h("section", { class: "ca-section" }, h("h3", { text: "Sensitive values" }), h("p", { class: "muted", text: data.can_see_sensitive ? "You can see them because the policy allows it and the candidate agreed." : "Not shown to you. You can see that they are set, not the values." })),
        data.needs_attention.length ? h("section", { class: "ca-section" }, h("h3", { text: "Needs attention" }), h("div", { class: "ca-chips" }, ...data.needs_attention.map((i) => chip(i.label, "bad")))) : null);
    } catch (error) { content.replaceChildren(ui.error(error, () => { drawer.close(); candidateDrawer(candidate, scope); })); }
  }

  function togglePause(candidate, data, drawer) {
    if (data.paused.paused) {
      return api(`/api/org/candidates/${candidate.candidate_id}/resume`, { method: "POST", body: {} }).then(() => { Aira.toast("Automation resumed.", "success"); drawer.close(); router.render(); }, Aira.reportError);
    }
    const reason = h("input", { type: "text", maxlength: 200, placeholder: "Reason (the candidate sees it)" });
    Aira.modal({ title: `Pause ${candidate.name || candidate.email}`, body: ui.field("Reason", reason), actions: [{ label: "Cancel", kind: "ghost" }, { label: "Pause", onClick: async () => {
      if (!reason.value.trim()) { Aira.toast("Give a reason.", "error"); return false; }
      await api(`/api/org/candidates/${candidate.candidate_id}/pause`, { method: "POST", body: { reason: reason.value } });
      Aira.toast("Automation paused.", "success"); drawer.close(); router.render();
    } }] });
  }

  // -------------------------------------------------------------------------
  // Coach: held questions across all candidates
  // -------------------------------------------------------------------------
  async function queueView(stage) {
    const data = await api("/api/org/queue");
    const items = data.queue;
    const rows = items.map((item) => {
      const hours = heldHours(item.held_since);
      const action = queueAction(item);
      const labels = item.questions || [];
      const button = action.kind === "view"
        ? h("span", { class: "badge", title: "Your access level does not allow acting on this", text: "View only" })
        : h("button", { type: "button", class: "btn sm", text: action.label, onclick: async () => { await cloud.enterWorkspace({ id: item.candidate_id, name: item.candidate, scope: item.scope }); router.go(["jobs", "autopilot"]); } });
      return h("div", { class: `row ca-queue-row ${hours !== null && hours > 24 ? "stale" : ""}` },
        h("div", {}, h("span", { class: "title", text: `${item.title || "Application"}${item.company ? " · " + item.company : ""}` }), h("span", { class: "sub", text: `${item.candidate} · ${item.status === "needs_review" ? "needs answers" : "ready for approval"}` }),
          labels.length ? h("ul", { class: "ca-questions" }, ...labels.slice(0, 4).map((q) => h("li", { text: q })), labels.length > 4 ? h("li", { class: "muted", text: `and ${labels.length - 4} more` }) : null) : null),
        h("div", { class: "aside" }, item.scope ? scopeBadge(item.scope) : null, h("small", { class: hours !== null && hours > 24 ? "ca-warn" : "muted", text: heldLabel(hours) }), button));
    });
    stage.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Held questions" }), h("p", { text: "Questions and applications waiting across all of your candidates, oldest first. Sensitive questions (work authorization and similar) can only be answered by the candidate." }))),
      items.length ? h("div", { class: "panel" }, h("div", { class: "list" }, ...rows)) : ui.empty("Nothing is waiting", "Held questions and applications that need approval appear here."));
    cloud.queueCount = items.length;
    Aira.renderNav({ "coach-queue": items.length });
  }

  // -------------------------------------------------------------------------
  // Registration, landing, navigation
  // -------------------------------------------------------------------------
  cloud.register({ id: "onboarding", label: "Get started", group: "Workspace", key: "k", icon: "applications", roles: ["candidate"], when: (me) => !(me.consent && me.consent.complete), view: onboardingView });
  cloud.register({ id: "activity", label: "Activity in my name", group: "Account", key: "w", icon: "analytics", roles: ["candidate"], view: activityView });
  cloud.register({ id: "coach", label: "My candidates", group: "Coaching", key: "y", icon: "recruiters", roles: ["coach"], quick: true, view: coachHome });
  cloud.register({ id: "coach-queue", label: "Held questions", group: "Coaching", key: "q", icon: "applications", roles: ["coach"], quick: true, view: queueView });

  const coreLanding = cloud.landing;
  cloud.landing = (workspace) => {
    const base = coreLanding(workspace);
    if (base && base[0] === "gate") return base; // a sign-in gate is up: request nothing behind it
    if (cloud.role() === "candidate" && cloud.me && cloud.me.consent && !cloud.me.consent.complete) return ["onboarding"];
    return base;
  };

  // The coach's badge on the queue entry: how much waits.
  const coreBoot = cloud.boot;
  cloud.boot = async (user) => {
    const result = await coreBoot(user);
    if (user.kind === "member" && user.role === "coach") {
      api("/api/org/queue", { silent: true }).then((data) => Aira.renderNav({ "coach-queue": (data.queue || []).length }), () => {});
    }
    return result;
  };
})();

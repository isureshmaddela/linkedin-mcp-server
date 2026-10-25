/* Aira hosted build — organization administration: Members, Coaches, Policy, Integrations, Audit log, Data & closure.
 * Loads after cloud-org.js, which owns routing (#/org/<page>) and the shared helpers on Aira.cloud.admin. */
"use strict";

(function () {
  const { h, api, fmt, ui } = Aira;
  const cloud = Aira.cloud;
  const A = cloud.admin;
  const inlineError = () => h("div", { class: "adm-inline-error", role: "alert" });
  const showError = (node, error) => { node.textContent = A.message(error); };
  const check = (id, label, hint, checked, { disabled = false, lockedHint = "" } = {}) => {
    const input = h("input", { type: "checkbox", id, ...(disabled ? { disabled: true } : {}) }); input.checked = !!checked && !disabled;
    return { input, node: h("label", { class: `adm-check${disabled ? " locked" : ""}`, for: id }, input, h("span", { class: "what" }, h("b", { text: label }), h("span", { text: disabled && lockedHint ? lockedHint : hint }))) };
  };

  // --------------------------------------------------------------------------------------------
  // Members
  // --------------------------------------------------------------------------------------------
  A.pages.members = async (stage, route) => {
    A.crumbs("Members");
    stage.replaceChildren(ui.skeleton(6));
    let directory; let invites; let org;
    try { [directory, invites, org] = await Promise.all([A.directory(true), api("/api/org/invites"), api("/api/org")]); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    const ent = org.entitlements; const limits = ent.limits;
    const reload = () => { A.forgetDirectory(); Aira.router.render(); };
    const canOwners = A.can("members.manage_owners");
    const page = h("div", { class: "adm-page" });
    const seatTile = (label, used, limit) => A.usageTile(label, used, limit, null, limit && used >= limit ? "All seats are in use" : undefined);
    page.append(A.head("Members", "People in the organization, their roles, and pending invitations. Candidates each get their own workspace.", h("a", { class: "btn secondary", href: "#/org/coaches", text: "Coach assignments" })));
    page.append(h("div", { class: "adm-tiles" }, seatTile("Candidate seats", directory.seats.candidates, limits.candidate_seats), seatTile("Coach and admin seats", directory.seats.staff, limits.staff_seats)));

    // Invite form
    const emails = h("textarea", { rows: 3, placeholder: "name@example.com, another@example.com", "aria-label": "Email addresses" });
    const roles = ["candidate", "coach", "admin", "billing", "auditor", ...(canOwners ? ["owner"] : [])];
    const role = ui.select(roles.map((r) => [r, A.roleLabel[r]]), roles.includes(route.query.invite) ? route.query.invite : "candidate");
    const coaches = directory.members.filter((m) => m.role === "coach");
    const coach = ui.select([["", "No coach yet"], ...coaches.map((m) => [m.member_id, m.name || m.email])], "");
    const scope = ui.select(["view", "act", "approve"].map((s) => [s, `${A.titleCase(s)}${ent.coach_scopes.includes(s) ? "" : " (not in your plan)"}`]), "act");
    const coachRow = h("div", { class: "adm-fields" }, ui.field("Coach for new candidates", coach), ui.field("Coach scope", scope));
    const syncCoachRow = () => { coachRow.hidden = role.value !== "candidate"; };
    role.addEventListener("change", syncCoachRow); syncCoachRow();
    const result = h("div", { role: "status" });
    const error = inlineError();
    const send = h("button", { type: "button", class: "btn", text: "Send invitations", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
      error.textContent = ""; result.replaceChildren();
      const list = emails.value.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
      if (!list.length) { error.textContent = "Enter at least one email address."; emails.focus(); return; }
      try {
        const body = { emails: list, role: role.value, ...(role.value === "candidate" && coach.value ? { coach_id: coach.value, coach_scope: scope.value } : {}) };
        const out = await api("/api/org/invites", { method: "POST", body });
        if (out.invites.length) Aira.toast(`${Aira.fmt.plural(out.invites.length, "invitation")} sent.`, "success");
        if (out.failed.length) result.replaceChildren(h("div", { class: "adm-note bad" }, h("b", { text: "Not sent:" }), h("ul", { style: { margin: "6px 0 0", paddingLeft: "18px" } }, ...out.failed.map((f) => h("li", { text: `${f.email}: ${f.error}` })))));
        emails.value = out.failed.map((f) => f.email).join(", ");
        if (!out.failed.length) reload(); else A.forgetDirectory();
      } catch (e) { showError(error, e); }
    }) });
    const invitePanel = A.panel("Invite people", h("div", { class: "panel-body adm-form" }, ui.field("Email addresses", emails, "One or several, separated by commas or new lines. Each invitation is a single-use link that expires in 7 days."), h("div", { class: "adm-fields" }, ui.field("Role", role)), coachRow, error, result, h("div", { class: "adm-btnrow" }, send)), { id: "invitePanel" });
    page.append(invitePanel);

    // Pending invites
    const pending = invites.invites;
    page.append(A.panel("Pending invitations", pending.length ? h("div", { class: "adm-list" }, ...pending.map((invite) => h("div", { class: "adm-item" },
      h("div", {}, h("div", { class: "title", text: invite.email }), h("div", { class: "meta" }, A.roleBadge(invite.role), h("span", { class: "sub", text: `Sent ${A.ago(invite.created_at)} · expires ${A.when(invite.expires_at)}${invite.resent_count ? ` · resent ${invite.resent_count}×` : ""}` }))),
      h("div", { class: "aside" },
        h("button", { type: "button", class: "btn secondary sm", text: "Resend", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api(`/api/org/invites/${invite.invite_id}/resend`, { method: "POST", body: {} }); Aira.toast(`Invitation resent to ${invite.email}.`, "success"); reload(); }) }),
        h("button", { type: "button", class: "btn ghost sm", text: "Revoke", onclick: async () => { if (!(await Aira.confirm({ title: "Revoke invitation", message: `The link sent to ${invite.email} stops working.`, confirmLabel: "Revoke", danger: true }))) return; try { await api(`/api/org/invites/${invite.invite_id}`, { method: "DELETE" }); Aira.toast("Invitation revoked.", "success"); reload(); } catch (e) { Aira.reportError(e); } } }))))) : A.empty("No pending invitations."), { sub: `${pending.length} waiting` }));

    // Member list
    const filter = h("input", { type: "search", "data-search": "1", placeholder: "Filter by name, email or role", "aria-label": "Filter members" });
    const list = h("div", { class: "adm-list" });
    const draw = () => {
      const needle = filter.value.trim().toLowerCase();
      const rows = directory.members.filter((m) => !needle || `${m.name} ${m.email} ${m.role}`.toLowerCase().includes(needle));
      list.replaceChildren(...(rows.length ? rows.map((m) => memberRow(m, org, reload, canOwners)) : [A.empty("No one matches.")]));
    };
    filter.addEventListener("input", draw); draw();
    page.append(A.panel("Everyone", h("div", {}, h("div", { style: { padding: "12px 16px 0" } }, filter), list), { sub: `${directory.members.length} members` }));
    stage.replaceChildren(page);
    if (route.query.invite) { emails.focus(); invitePanel.scrollIntoView({ block: "nearest" }); }
  };

  function memberRow(member, org, reload, canOwners) {
    const isOwner = member.role === "owner"; const mine = member.member_id === cloud.me.member_id;
    const actions = [];
    const editable = member.role !== "candidate" && (!isOwner || canOwners);
    if (editable) actions.push(h("button", { type: "button", class: "btn secondary sm", text: "Change role", onclick: () => changeRole(member, canOwners, reload) }));
    if (member.mfa_enabled && (!isOwner || canOwners)) actions.push(h("button", { type: "button", class: "btn ghost sm", text: "Reset MFA", onclick: async () => {
      if (!(await Aira.confirm({ title: "Reset MFA", message: `${member.name || member.email} is signed out everywhere and sets up MFA again at the next sign-in.`, confirmLabel: "Reset MFA", danger: true }))) return;
      try { await api(`/api/org/members/${member.member_id}/mfa-reset`, { method: "POST", body: {} }); Aira.toast("MFA reset.", "success"); reload(); } catch (e) { Aira.reportError(e); }
    } }));
    if (!isOwner || canOwners) actions.push(h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: () => removeMember(member, org, reload) }));
    return h("div", { class: "adm-item" },
      h("div", {}, h("div", { class: "title", text: `${member.name || member.email}${mine ? " (you)" : ""}` }), h("div", { class: "sub", text: member.name ? member.email : "" }),
        h("div", { class: "meta" }, A.roleBadge(member.role), member.mfa_enabled ? A.badge("MFA on", "good") : A.badge("No MFA"), member.email_verified ? null : A.badge("Email not verified", "warn"), h("span", { class: "sub", text: `Last sign-in ${A.ago(member.last_sign_in_at)}` }))),
      h("div", { class: "aside" }, ...actions));
  }

  function changeRole(member, canOwners, reload) {
    const options = ["admin", "coach", "billing", "auditor", ...(canOwners ? ["owner"] : [])];
    const select = ui.select(options.map((r) => [r, A.roleLabel[r]]), member.role);
    const error = inlineError();
    Aira.modal({ title: `Change role for ${member.name || member.email}`, body: h("div", { class: "adm-form" }, ui.field("Role", select), h("p", { class: "adm-sub", text: "They are signed out of every device and sign in again with the new role. A coach changed to another role loses their assignments. A candidate cannot change role: remove them and invite them again." }), error),
      actions: [{ label: "Cancel", kind: "ghost" }, { label: "Change role", onClick: async (close) => { try { await api(`/api/org/members/${member.member_id}`, { method: "PATCH", body: { role: select.value } }); Aira.toast("Role changed.", "success"); close(); reload(); return false; } catch (e) { if (e.status !== 402) showError(error, e); else close(); return false; } } }] });
  }

  async function removeMember(member, org, reload) {
    const policy = org.policy.candidate_data_policy;
    const note = member.role === "candidate" ? (policy === "candidate_keeps"
      ? "Your policy is that the candidate keeps their data: they receive an export link by email, then their workspace is deleted and only aggregate statistics stay."
      : "Your policy is that the organization keeps everything: their workspace is archived read-only for administrators and auditors.") : "They are signed out of every device.";
    if (!(await Aira.confirm({ title: `Remove ${member.name || member.email}`, message: note, confirmLabel: "Remove", danger: true }))) return;
    try {
      const out = await api(`/api/org/members/${member.member_id}`, { method: "DELETE" });
      const message = out.export_id ? "Removed. The candidate was emailed a link to download their data." : out.data_policy === "organization_keeps" ? "Removed. Their workspace is archived." : "Member removed.";
      Aira.toast(message, "success");
      reload();
    } catch (e) { Aira.reportError(e); }
  }

  // --------------------------------------------------------------------------------------------
  // Coaches: assignment matrix
  // --------------------------------------------------------------------------------------------
  A.pages.coaches = async (stage, route) => {
    A.crumbs("Coaches");
    stage.replaceChildren(ui.skeleton(6));
    let assignments; let candidates; let directory;
    try { [assignments, candidates, directory] = await Promise.all([api("/api/org/assignments"), api("/api/org/candidates?limit=500"), A.directory(true)]); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    const reload = () => { A.forgetDirectory(); Aira.router.render(); };
    const coachList = directory.members.filter((m) => m.role === "coach");
    const byPair = new Map(assignments.assignments.map((a) => [`${a.coach_id}:${a.candidate_id}`, a]));
    const highlight = route.query.coach || "";
    const page = h("div", { class: "adm-page" });
    page.append(A.head("Coaches", "Who may act for which candidate, and how far. A candidate can always see what a coach did in their name.", h("a", { class: "btn secondary", href: "#/org/members?invite=coach", text: "Invite a coach" })));
    page.append(h("div", { class: "adm-note", text: `View: read only. Act: search, prepare, answer held questions, pause. Approve: also submit applications. Your plan allows: ${assignments.scopes.join(", ")}.` }));
    if (!coachList.length || !candidates.candidates.length) page.append(A.panel("Assignments", A.empty(!coachList.length ? "No coaches yet. Invite a coach, then assign candidates here." : "No candidates yet. Invite candidates, then assign coaches here.")));
    else {
      const head = h("tr", {}, h("th", { text: "Candidate" }), ...coachList.map((coach) => h("th", { class: coach.member_id === highlight ? "hl" : "", scope: "col", title: coach.email, text: coach.name || coach.email })));
      const rows = candidates.candidates.map((c) => h("tr", {}, h("td", { title: c.email }, h("a", { href: `#/org/candidates/${c.candidate_id}`, text: c.name || c.email })), ...coachList.map((coach) => {
        const a = byPair.get(`${coach.member_id}:${c.candidate_id}`);
        const cell = h("td", { class: coach.member_id === highlight ? "hl" : "" });
        const open = () => A.assignCoachModal(c.candidate_id, c.name || c.email, assignments, reload, a ? { coach_id: coach.member_id, scope: a.scope } : { coach_id: coach.member_id });
        cell.append(a
          ? h("button", { type: "button", class: `adm-scope ${a.effective_scope}`, title: a.effective_scope !== a.scope ? `Assigned ${a.scope}; the plan allows ${a.effective_scope}` : "Change or remove", "aria-label": `${coach.name || coach.email} for ${c.name || c.email}: ${a.effective_scope}. Change`, text: a.effective_scope + (a.effective_scope !== a.scope ? " ⚠" : ""), onclick: () => scopeActions(a, coach, c, assignments, reload) })
          : h("button", { type: "button", class: "adm-scope add", "aria-label": `Assign ${coach.name || coach.email} to ${c.name || c.email}`, text: "+ assign", onclick: open }));
        return cell;
      })));
      page.append(A.panel("Assignments", h("div", { class: "adm-scroll" }, h("table", { class: "adm-matrix" }, h("thead", {}, head), h("tbody", {}, ...rows))), { sub: `${coachList.length} coaches × ${candidates.candidates.length} candidates` }));
      const from = ui.select(coachList.map((m) => [m.member_id, m.name || m.email]), coachList[0].member_id);
      const to = ui.select(coachList.map((m) => [m.member_id, m.name || m.email]), (coachList[1] || coachList[0]).member_id);
      const scope = ui.select([["", "Keep each scope"], ...["view", "act", "approve"].map((s) => [s, A.titleCase(s)])], "");
      const error = inlineError();
      page.append(A.panel("Reassign in bulk", h("div", { class: "panel-body adm-form" }, h("p", { class: "adm-sub", text: "Move every candidate of one coach to another, for example when a coach leaves." }), h("div", { class: "adm-fields" }, ui.field("From", from), ui.field("To", to), ui.field("Scope", scope)), error,
        h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: "Reassign", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
          error.textContent = "";
          if (from.value === to.value) { error.textContent = "Choose two different coaches."; return; }
          try { const out = await api("/api/org/assignments/reassign", { method: "POST", body: { from_coach: from.value, to_coach: to.value, scope: scope.value } }); Aira.toast(`${out.moved} candidate(s) moved.`, "success"); reload(); } catch (err) { showError(error, err); }
        }) })))));
    }
    stage.replaceChildren(page);
  };

  function scopeActions(assignment, coach, candidate, assignments, reload) {
    const name = candidate.name || candidate.email;
    const modal = Aira.modal({ title: `${coach.name || coach.email} for ${name}`, body: h("p", { class: "muted", text: `Current scope: ${assignment.scope}${assignment.effective_scope !== assignment.scope ? ` (capped to ${assignment.effective_scope} by the plan)` : ""}.` }),
      actions: [{ label: "Close", kind: "ghost" },
        { label: "Remove coach", kind: "danger", onClick: async () => { try { await api(`/api/org/assignments/${coach.member_id}/${candidate.candidate_id}`, { method: "DELETE" }); Aira.toast("Coach removed.", "success"); reload(); } catch (e) { Aira.reportError(e); return false; } } },
        { label: "Change scope", close: false, onClick: (close) => { close(); A.assignCoachModal(candidate.candidate_id, name, assignments, reload, { coach_id: coach.member_id, scope: assignment.scope }); return false; } }] });
    return modal;
  }

  // --------------------------------------------------------------------------------------------
  // Policy
  // --------------------------------------------------------------------------------------------
  A.pages.policy = async (stage) => {
    A.crumbs("Policy");
    stage.replaceChildren(ui.skeleton(6));
    let org; let plans;
    try { [org, plans] = await Promise.all([api("/api/org"), A.plans()]); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    const policy = org.policy; const ent = org.entitlements; const features = new Set(ent.features); const limits = ent.limits;
    const planWith = (feature) => (plans.find((p) => (p.features || []).includes(feature)) || {}).name || "";
    const upgrade = (feature) => { const name = planWith(feature); return name ? `Included from the ${name} plan.` : "Not part of your plan."; };
    const dailyMax = limits.apps_per_candidate_day || 1000;
    const retentionMax = limits.retention_days || 3650;
    const reload = () => Aira.router.render();

    const mode = { review: h("input", { type: "radio", name: "mode", value: "review", id: "modeReview" }), auto: h("input", { type: "radio", name: "mode", value: "auto", id: "modeAuto" }) };
    mode.review.checked = policy.submission_mode === "review"; mode.auto.checked = policy.submission_mode === "auto";
    if (!features.has("automation.submit")) { mode.auto.disabled = true; mode.review.checked = true; mode.auto.checked = false; }
    const radio = (input, title, text, locked) => h("label", { class: `adm-radio${input.checked ? " on" : ""}`, for: input.id }, input, h("span", { class: "what" }, h("b", { text: title }), h("span", { text: text }), locked ? h("span", { text: locked }) : null));
    const modeBox = h("div", { class: "adm-choices", role: "radiogroup", "aria-label": "Submission mode" }, radio(mode.review, "Review before submit", "Aira fills each application and a person (the candidate, or a coach with approve scope) submits it.", ""),
      radio(mode.auto, "Automatic submission", "Aira submits matches on its own within the limits below. A candidate must also turn it on in their own search plan.", features.has("automation.submit") ? "" : upgrade("automation.submit")));
    modeBox.addEventListener("change", () => { for (const label of modeBox.querySelectorAll(".adm-radio")) label.classList.toggle("on", label.querySelector("input").checked); });

    const daily = h("input", { type: "number", min: "1", max: String(dailyMax), value: String(policy.daily_cap_per_candidate), id: "polDaily" });
    const retention = h("input", { type: "number", min: "30", max: String(retentionMax), value: String(policy.retention_days), id: "polRetention" });
    const sourceDefs = [["aira_jobs", "Aira Jobs", "Free job boards"], ["company_boards", "Company boards", "Greenhouse, Lever, Ashby, SmartRecruiters"], ["google_jobs", "Google Jobs", "Needs your organization's SerpApi key"], ["linkedin", "LinkedIn", "The candidate's own session, with the User Agreement warning"]];
    const sources = sourceDefs.map(([key, label, hint]) => check(`polSrc-${key}`, label, hint, policy.allowed_sources.includes(key), { disabled: !features.has(`sources.${key}`), lockedHint: upgrade(`sources.${key}`) }));
    const linkedin = check("polLinkedin", "LinkedIn allowed", "Candidates may connect their own LinkedIn session. LinkedIn forbids automated access; each candidate must turn it on with the risk notice.", policy.linkedin_allowed, { disabled: !features.has("sources.linkedin"), lockedHint: upgrade("sources.linkedin") });
    const easy = check("polEasy", "LinkedIn Easy Apply allowed", "Candidates may enable Easy Apply in their own plans. Off by default.", policy.easy_apply_allowed, { disabled: !features.has("sources.linkedin"), lockedHint: upgrade("sources.linkedin") });
    const covers = check("polCovers", "AI cover letters", "Cover letters are drafted by the model gateway; sensitive profile fields are never sent.", policy.cover_letters, { disabled: !features.has("ai.cover_letters"), lockedHint: upgrade("ai.cover_letters") });
    const sensitive = check("polSensitive", "Coaches may see sensitive profile values", "Only for an assigned coach, and only for a candidate who consented at onboarding. Never editable by anyone but the candidate.", policy.coach_sees_sensitive);
    const mfa = check("polMfa", "Require MFA for all staff", "Owners and admins already need MFA on Team and Enterprise. This adds coaches, billing contacts and auditors.", policy.require_mfa_staff);
    const pooled = check("polPooled", "Use Aira's pooled SerpApi searches", "A metered add-on for when you have no SerpApi key of your own.", policy.use_pooled_serpapi, { disabled: !features.has("pooled_serpapi"), lockedHint: upgrade("pooled_serpapi") });
    const messages = ui.select([["off", "Candidate only (default)"], ["view", "Assigned coaches can read"], ["act", "Assigned coaches can read and reply (act scope)"]], policy.coach_messages);
    const regions = h("textarea", { rows: 2, id: "polRegions", placeholder: "For example: California, Germany, New York City", "aria-label": "Regions needing human review" }); regions.value = (policy.review_required_regions || []).join(", ");
    const dataPolicy = { keeps: h("input", { type: "radio", name: "dp", value: "candidate_keeps", id: "dpKeeps" }), org: h("input", { type: "radio", name: "dp", value: "organization_keeps", id: "dpOrg" }) };
    dataPolicy.keeps.checked = policy.candidate_data_policy === "candidate_keeps"; dataPolicy.org.checked = policy.candidate_data_policy === "organization_keeps";
    const dpBox = h("div", { class: "adm-choices", role: "radiogroup", "aria-label": "Candidate data when someone leaves" }, radio(dataPolicy.keeps, "The candidate keeps their data", "They get an export (JSON plus documents); the workspace is deleted and only aggregate statistics stay. Suits an employee benefit.", ""), radio(dataPolicy.org, "The organization keeps everything", "The workspace is archived read-only for administrators and auditors. Suits outplacement.", ""));
    dpBox.addEventListener("change", () => { for (const label of dpBox.querySelectorAll(".adm-radio")) label.classList.toggle("on", label.querySelector("input").checked); });

    const read = () => ({
      submission_mode: mode.auto.checked ? "auto" : "review",
      daily_cap_per_candidate: Number(daily.value),
      allowed_sources: sources.map((s, i) => (s.input.checked && !s.input.disabled ? sourceDefs[i][0] : null)).filter(Boolean),
      linkedin_allowed: linkedin.input.checked && !linkedin.input.disabled,
      easy_apply_allowed: easy.input.checked && !easy.input.disabled,
      cover_letters: covers.input.checked && !covers.input.disabled,
      retention_days: Number(retention.value),
      candidate_data_policy: dataPolicy.org.checked ? "organization_keeps" : "candidate_keeps",
      coach_sees_sensitive: sensitive.input.checked,
      coach_messages: messages.value,
      require_mfa_staff: mfa.input.checked,
      review_required_regions: regions.value.split(/[,\n]+/).map((s) => s.trim()).filter(Boolean),
      use_pooled_serpapi: pooled.input.checked && !pooled.input.disabled,
    });
    const changed = () => { const now = read(); const was = { ...policy }; return Object.fromEntries(Object.entries(now).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(was[k]))); };
    const error = inlineError();
    const dirty = h("span", { class: "grow adm-sub", "aria-live": "polite", text: "No changes" });
    const save = h("button", { type: "button", class: "btn", text: "Save policy", disabled: true });
    const refresh = () => { const n = Object.keys(changed()).length; save.disabled = !n; dirty.textContent = n ? `${n} unsaved change${n === 1 ? "" : "s"}` : "No changes"; };
    const form = h("div", { class: "adm-form" });
    form.addEventListener("input", refresh); form.addEventListener("change", refresh);
    save.addEventListener("click", () => Aira.withBusy(save, async () => {
      error.textContent = ""; const patch = changed(); if (!Object.keys(patch).length) return;
      try { await api("/api/org/policy", { method: "PATCH", body: patch }); Aira.toast("Policy saved.", "success"); cloud.me.policy = { ...cloud.me.policy }; reload(); } catch (e) { showError(error, e); }
    }));
    const cap = (n, what) => (n ? `Your plan allows up to ${A.n(n)} ${what}.` : "No plan limit.");
    form.append(
      h("h3", { class: "adm-section-title", text: "Applications" }), modeBox,
      h("div", { class: "adm-fields" }, ui.field("Applications per candidate per day", daily, cap(limits.apps_per_candidate_day, "per candidate per day")), ui.field("Data retention (days)", retention, cap(limits.retention_days, "days of retention"))),
      h("h3", { class: "adm-section-title", text: "Job sources" }), h("div", {}, ...sources.map((s) => s.node)), linkedin.node, easy.node, pooled.node,
      h("h3", { class: "adm-section-title", text: "AI" }), covers.node,
      h("h3", { class: "adm-section-title", text: "Privacy and access" }), sensitive.node, ui.field("Coach access to a candidate's LinkedIn messages", messages), mfa.node,
      h("h3", { class: "adm-section-title", text: "When a member leaves" }), dpBox,
      h("h3", { class: "adm-section-title", text: "Human review" }), ui.field("Regions where every application needs human review", regions, "Matched against the candidate's location. Some markets regulate automated employment decisions."));
    const page = h("div", { class: "adm-page" });
    page.append(A.head("Policy", "How applications are run for your candidates. Defaults are safe; relax them within what your plan allows. Changes apply when you save and are recorded in the audit log."));
    page.append(A.panel("Application policy", h("div", { class: "panel-body" }, form)));
    page.append(h("div", { class: "adm-savebar" }, dirty, error, h("button", { type: "button", class: "btn ghost", text: "Reset", onclick: reload }), save));
    page.append(pausePanel(org, reload));
    page.append(brandingPanel(org, features, plans, reload));
    stage.replaceChildren(page);
  };

  function pausePanel(org, reload) {
    const paused = org.paused;
    const reason = h("input", { type: "text", maxlength: 300, placeholder: "Reason (shown on the dashboard)", "aria-label": "Reason for pausing", id: "pauseReason" });
    const error = inlineError();
    const body = paused.paused
      ? h("div", { class: "panel-body adm-form" }, h("div", { class: "adm-note warn", role: "status", text: `Automation is paused for the whole organization: ${paused.reason || "no reason given"}.` }), h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: "Resume automation", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api("/api/org/automation/resume", { method: "POST", body: {} }); Aira.toast("Automation resumed.", "success"); cloud.refreshBanners(); reload(); }) })))
      : h("div", { class: "panel-body adm-form" }, h("p", { class: "adm-sub", text: "Stops every search plan, scheduled run and automatic submission for all candidates until you resume. Nothing is lost." }), ui.field("Reason", reason), error,
        h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn danger secondary", text: "Pause all automation", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
          error.textContent = ""; if (!reason.value.trim()) { error.textContent = "Give a reason; it shows on the dashboard."; reason.focus(); return; }
          try { await api("/api/org/automation/pause", { method: "POST", body: { reason: reason.value.trim() } }); Aira.toast("Automation paused for the organization.", "success"); cloud.refreshBanners(); reload(); } catch (err) { showError(error, err); }
        }) })));
    return A.panel("Pause automation", body, { sub: paused.paused ? "Paused" : "Running" });
  }

  function brandingPanel(org, features, plans, reload) {
    const allowed = features.has("branding");
    const name = h("input", { type: "text", maxlength: 120, value: org.org.name, id: "brandName", ...(allowed ? {} : { disabled: true }) });
    const logo = h("input", { type: "url", placeholder: "https://example.com/logo.png", value: (org.branding && org.branding.logo_url) || "", id: "brandLogo", ...(allowed ? {} : { disabled: true }) });
    const error = inlineError();
    const planName = (plans.find((p) => (p.features || []).includes("branding")) || {}).name;
    return A.panel("Branding", h("div", { class: "panel-body adm-form" }, allowed ? h("p", { class: "adm-sub", text: "Your organization's name and logo appear on the sign-in page and in notification emails." }) : h("div", { class: "adm-note", text: `Branding is included from the ${planName || "Team"} plan.` }),
      h("div", { class: "adm-fields" }, ui.field("Display name", name), ui.field("Logo address (https)", logo)), error,
      allowed ? h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn secondary", text: "Save branding", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { error.textContent = ""; try { await api("/api/org/branding", { method: "PUT", body: { name: name.value.trim(), logo_url: logo.value.trim() } }); Aira.toast("Branding saved.", "success"); reload(); } catch (err) { showError(error, err); } }) })) : null));
  }

  // --------------------------------------------------------------------------------------------
  // Integrations
  // --------------------------------------------------------------------------------------------
  A.pages.integrations = async (stage) => {
    A.crumbs("Integrations");
    stage.replaceChildren(ui.skeleton(6));
    let data; let plans;
    try { [data, plans] = await Promise.all([api("/api/org/integrations"), A.plans()]); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    const manage = A.can("integrations.manage"); const reload = () => Aira.router.render();
    const planWith = (feature) => (plans.find((p) => (p.features || []).includes(feature)) || {}).name || "a higher";
    const page = h("div", { class: "adm-page" });
    page.append(A.head("Integrations", manage ? "Keys and connections that belong to the organization. Secrets are encrypted per organization and are never shown again." : "Connections that belong to the organization (read-only for auditors)."));
    const grid = h("div", { class: "adm-grid" });
    grid.append(serpapiCard(data, manage, planWith, reload), apiCard(data, manage, planWith), modelCard(data, manage, planWith, reload));
    grid.append(h("div", { class: "adm-wide", id: "ssoSlot" }));
    page.append(grid);
    stage.replaceChildren(page);
    const slot = stage.querySelector("#ssoSlot");
    slot.replaceChildren(await ssoCard(data, manage, planWith), await scimCard(data, manage, planWith));
  };

  function serpapiCard(data, manage, planWith, reload) {
    const s = data.serpapi;
    const key = h("input", { type: "password", autocomplete: "off", placeholder: "Paste a SerpApi key", id: "serpKey", "aria-label": "SerpApi key", ...(s.available && manage ? {} : { disabled: true }) });
    const error = inlineError();
    const status = s.configured ? h("div", { class: "adm-note" }, `A key ending in ${s.last4} is stored encrypted. It is used for Google Jobs, Careers and the API console.`) : s.source === "pooled" ? h("div", { class: "adm-note", text: "No key of your own: searches use Aira's pooled allowance (metered)." }) : h("div", { class: "adm-note warn", text: "No key yet. Google Jobs and Careers need your organization's SerpApi key." });
    const body = h("div", { class: "panel-body adm-form" }, status,
      !s.available ? h("div", { class: "adm-note", text: `Google Jobs is included from the ${planWith("sources.google_jobs")} plan.` }) : null,
      manage && s.available ? h("div", { class: "adm-form" }, ui.field(s.configured ? "Replace the key" : "SerpApi key", key, "The key is checked with SerpApi before it is saved. Never put it in an environment file."), error,
        h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: s.configured ? "Replace key" : "Save key", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { error.textContent = ""; if (!key.value.trim()) { error.textContent = "Paste a key first."; key.focus(); return; } try { await api("/api/airaapi/key", { method: "POST", body: { api_key: key.value.trim() } }); Aira.toast("SerpApi key saved.", "success"); reload(); } catch (err) { showError(error, err); } }) }),
          s.configured ? h("button", { type: "button", class: "btn ghost", text: "Remove key", onclick: async () => { if (!(await Aira.confirm({ title: "Remove the SerpApi key", message: "Google Jobs and Careers stop working until a key is added again.", confirmLabel: "Remove", danger: true }))) return; try { await api("/api/airaapi/key", { method: "DELETE" }); Aira.toast("Key removed.", "success"); reload(); } catch (err) { Aira.reportError(err); } } }) : null)) : null);
    return A.panel("SerpApi key (Google Jobs)", body, { sub: s.configured ? "Configured" : "Not configured" });
  }

  function apiCard(data, manage, planWith) {
    const a = data.api;
    return A.panel("API", h("div", { class: "panel-body adm-form" },
      h("dl", { class: "adm-dl" }, h("dt", { text: "Console (history, read-only)" }), h("dd", { text: a.console ? "Included" : "Not included" }), h("dt", { text: "Live searches from the console" }), h("dd", { text: a.searches ? "Included" : `From the ${planWith("api.searches")} plan` }), h("dt", { text: "SerpApi-compatible /v1 and keys" }), h("dd", { text: a.v1 ? `${a.keys} of ${a.v1_limit ?? "unlimited"} keys in use` : `From the ${planWith("api.v1")} plan` })),
      manage && a.console ? h("div", { class: "adm-btnrow" }, h("a", { class: "btn secondary", href: "#/airaapi", text: "Open the API console" })) : null,
      h("p", { class: "adm-sub", text: "API keys, per-key limits and the monthly search allowance are managed in the console. Keys belong to the organization, not to a person." })), { sub: a.v1 ? "Keys enabled" : "No /v1" });
  }

  function modelCard(data, manage, planWith, reload) {
    const m = data.model;
    const url = h("input", { type: "url", placeholder: "https://models.example.com/v1", id: "modelUrl", value: m.url || "", ...(m.byo_available && manage ? {} : { disabled: true }) });
    const key = h("input", { type: "password", autocomplete: "off", placeholder: m.configured ? "Leave empty to keep the stored key" : "API key", id: "modelKey", ...(m.byo_available && manage ? {} : { disabled: true }) });
    const model = h("input", { type: "text", placeholder: "model name", id: "modelName", value: m.model || "", ...(m.byo_available && manage ? {} : { disabled: true }) });
    const error = inlineError();
    return A.panel("Model endpoint", h("div", { class: "panel-body adm-form" },
      m.configured ? h("div", { class: "adm-note" }, `Your own endpoint (${m.url}, model ${m.model}) answers prompts. Sensitive profile fields are still never sent.`) : h("div", { class: "adm-note", text: `Aira's hosted model (${m.provider}) answers prompts by default.` }),
      m.byo_available ? null : h("div", { class: "adm-note", text: `Bring-your-own model endpoint is included from the ${planWith("byo_model")} plan.` }),
      manage && m.byo_available ? h("div", { class: "adm-form" }, h("div", { class: "adm-fields" }, ui.field("OpenAI-compatible endpoint (https)", url), ui.field("Model", model), ui.field("API key", key)), error,
        h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: "Save endpoint", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { error.textContent = ""; try { await api("/api/org/integrations/model", { method: "PUT", body: { url: url.value.trim(), api_key: key.value, model: model.value.trim() } }); Aira.toast("Model endpoint saved.", "success"); reload(); } catch (err) { showError(error, err); } }) }),
          m.configured ? h("button", { type: "button", class: "btn ghost", text: "Use the hosted model", onclick: async () => { try { await api("/api/org/integrations/model", { method: "DELETE" }); Aira.toast("Back to the hosted model.", "success"); reload(); } catch (err) { Aira.reportError(err); } } }) : null)) : null), { sub: m.configured ? "Own endpoint" : "Hosted" });
  }

  // SSO and SCIM talk to endpoints built alongside this screen; anything that is not there yet degrades to a note.
  async function optional(path, options) {
    try { return { ok: true, data: await api(path, { silent: true, ...options }) }; } catch (error) { return { ok: false, error }; }
  }

  async function ssoCard(data, manage, planWith) {
    const info = data.sso; const reload = () => Aira.router.render();
    const card = (body, sub) => A.panel("Single sign-on", body, { sub, id: "ssoCard" });
    if (!info.available) return card(h("div", { class: "panel-body" }, h("div", { class: "adm-note", text: `Single sign-on (OIDC and SAML) is included from the ${planWith("auth.sso")} plan.` })), "Not in your plan");
    if (!manage) return card(h("div", { class: "panel-body" }, h("div", { class: "adm-note", text: "Single sign-on settings are visible to owners and admins." })), "Restricted");
    const loaded = await optional("/api/org/sso");
    if (!loaded.ok) return card(h("div", { class: "panel-body" }, h("div", { class: "adm-note", text: loaded.error.status === 404 || loaded.error.status === 405 ? "Single sign-on setup is not available yet on this deployment." : A.message(loaded.error) })), "Unavailable");
    const cfg = loaded.data; const sp = cfg.sp || {};
    const body = h("div", { class: "panel-body adm-form" });
    body.append(cfg.configured ? h("div", { class: "adm-note" }, `${(cfg.protocol || "").toUpperCase()} is configured${cfg.enforced ? " and enforced: members sign in through your identity provider (owners keep password plus MFA as a break-glass)" : " (optional for members)"}.`) : h("div", { class: "adm-note warn", text: "Not configured. Members sign in with a password." }));
    body.append(h("dl", { class: "adm-dl" }, ...[["Sign-in URL", sp.login_url], ["OIDC redirect URI", sp.oidc_redirect_uri], ["SAML entity id", sp.saml_entity_id], ["SAML ACS URL", sp.saml_acs_url], ["SAML metadata", sp.saml_metadata_url]].filter(([, v]) => v).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, h("span", { class: "adm-code", text: v }))])));
    if (!manage) return card(body, cfg.configured ? "Configured" : "Not configured");
    const protocol = ui.select([["oidc", "OpenID Connect (OIDC)"], ["saml", "SAML 2.0"]], cfg.protocol || "oidc");
    const f = (id, label, value = "", attrs = {}) => ({ el: h("input", { type: "text", id, value, ...attrs }), label });
    const settings = cfg.settings || {};
    const oidc = { issuer: f("ssoIssuer", "Issuer URL", settings.issuer || ""), client_id: f("ssoClient", "Client id", settings.client_id || ""), client_secret: f("ssoSecret", "Client secret", "", { type: "password", autocomplete: "off", placeholder: settings.client_secret_set ? "Leave empty to keep the stored secret" : "" }), groups_claim: f("ssoGroupsClaim", "Groups claim", settings.groups_claim || "groups") };
    const saml = { idp_entity_id: f("ssoEntity", "IdP entity id", settings.idp_entity_id || ""), sso_url: f("ssoUrl", "IdP single sign-on URL", settings.sso_url || "") };
    const cert = h("textarea", { rows: 5, id: "ssoCert", placeholder: "-----BEGIN CERTIFICATE-----", "aria-label": "IdP signing certificate (PEM)" }); cert.value = settings.idp_certificate || "";
    const domains = h("input", { type: "text", id: "ssoDomains", value: (cfg.domains || []).join(", "), placeholder: "example.com, example.org" });
    const defaultRole = ui.select(["candidate", "coach", "admin", "billing", "auditor"].map((r) => [r, A.roleLabel[r]]), cfg.default_role || "candidate");
    const groupMap = h("textarea", { rows: 3, id: "ssoGroups", placeholder: "Coaches=coach\nStaff=admin" }); groupMap.value = Object.entries(cfg.group_role_map || {}).map(([g, r]) => `${g}=${r}`).join("\n");
    const enforce = check("ssoEnforce", "Enforce single sign-on", "Members must sign in through the identity provider. Owners keep password plus MFA as a break-glass.", cfg.enforced);
    const oidcBox = h("div", { class: "adm-fields" }, ...Object.values(oidc).map((x) => ui.field(x.label, x.el)));
    const samlBox = h("div", { class: "adm-form" }, h("div", { class: "adm-fields" }, ...Object.values(saml).map((x) => ui.field(x.label, x.el))), ui.field("IdP signing certificate (PEM)", cert));
    const sync = () => { oidcBox.hidden = protocol.value !== "oidc"; samlBox.hidden = protocol.value !== "saml"; };
    protocol.addEventListener("change", sync); sync();
    const error = inlineError();
    const parseMap = () => Object.fromEntries(groupMap.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.lastIndexOf("="); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }));
    body.append(h("h3", { class: "adm-section-title", text: cfg.configured ? "Change configuration" : "Configure" }), ui.field("Protocol", protocol), oidcBox, samlBox,
      h("div", { class: "adm-fields" }, ui.field("Email domains allowed to sign in", domains, "Comma separated. People from these domains are created on first sign-in."), ui.field("Role for new people", defaultRole)),
      ui.field("Directory groups → roles (one per line, group=role)", groupMap, "Roles come from groups; owners are never granted by a group."), enforce.node, error,
      h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: "Save configuration", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
        error.textContent = "";
        const common = { protocol: protocol.value, domains: domains.value.split(/[,\s]+/).filter(Boolean), default_role: defaultRole.value, group_role_map: parseMap(), enforced: enforce.input.checked };
        const payload = protocol.value === "oidc" ? { ...common, issuer: oidc.issuer.el.value.trim(), client_id: oidc.client_id.el.value.trim(), client_secret: oidc.client_secret.el.value, groups_claim: oidc.groups_claim.el.value.trim() } : { ...common, idp_entity_id: saml.idp_entity_id.el.value.trim(), sso_url: saml.sso_url.el.value.trim(), idp_certificate_pem: cert.value };
        try { await api("/api/org/sso", { method: "POST", body: payload }); Aira.toast("Single sign-on saved.", "success"); reload(); } catch (err) { showError(error, err); }
      }) }), cfg.configured ? h("button", { type: "button", class: "btn danger secondary", text: "Remove SSO", onclick: async () => { if (!(await Aira.confirm({ title: "Remove single sign-on", message: "Members go back to signing in with a password (they may need to reset it).", confirmLabel: "Remove", danger: true }))) return; try { await api("/api/org/sso", { method: "DELETE" }); Aira.toast("Single sign-on removed.", "success"); reload(); } catch (err) { Aira.reportError(err); } } }) : null));
    return card(body, cfg.configured ? `${(cfg.protocol || "").toUpperCase()}${cfg.enforced ? " · enforced" : ""}` : "Not configured");
  }

  async function scimCard(data, manage, planWith) {
    const info = data.scim; const reload = () => Aira.router.render();
    const card = (body, sub) => A.panel("SCIM provisioning", body, { sub });
    if (!info.available) return card(h("div", { class: "panel-body" }, h("div", { class: "adm-note", text: `SCIM provisioning is included from the ${planWith("auth.scim")} plan.` })), "Not in your plan");
    const loaded = await optional("/api/org/sso/scim-tokens");
    if (!loaded.ok) return card(h("div", { class: "panel-body" }, h("div", { class: "adm-note", text: loaded.error.status === 404 || loaded.error.status === 405 ? "SCIM setup is not available yet on this deployment." : A.message(loaded.error) })), "Unavailable");
    const tokens = loaded.data.tokens || loaded.data || [];
    const rows = tokens.length ? h("div", { class: "adm-list" }, ...tokens.map((t) => h("div", { class: "adm-item" }, h("div", {}, h("div", { class: "title", text: t.label || "Token" }), h("div", { class: "sub", text: `Created ${A.ago(t.created_at)} · last used ${A.ago(t.last_used_at)}${t.revoked_at ? " · revoked" : ""}` })),
      h("div", { class: "aside" }, t.revoked_at ? A.badge("Revoked", "bad") : (manage ? h("button", { type: "button", class: "btn ghost sm", text: "Revoke", onclick: async () => { if (!(await Aira.confirm({ title: "Revoke token", message: "Your directory stops provisioning until a new token is used.", confirmLabel: "Revoke", danger: true }))) return; try { await api(`/api/org/sso/scim-tokens/${t.id}`, { method: "DELETE" }); Aira.toast("Token revoked.", "success"); reload(); } catch (err) { Aira.reportError(err); } } }) : null))))) : A.empty("No tokens yet.");
    const label = h("input", { type: "text", maxlength: 80, placeholder: "Label, for example Okta", id: "scimLabel", "aria-label": "Token label" });
    const body = h("div", { class: "panel-body adm-form" }, h("p", { class: "adm-sub", text: "Your directory creates and deactivates members and maps groups to roles through SCIM 2.0. A token is shown once." }), rows);
    if (manage) body.append(h("div", { class: "adm-fields" }, ui.field("New token", label)), h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: "Create SCIM token", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
      const out = await api("/api/org/sso/scim-tokens", { method: "POST", body: { label: label.value.trim() } });
      Aira.modal({ title: "Copy your SCIM token now", body: h("div", { class: "adm-form" }, h("p", { class: "muted", text: "It is not shown again." }), h("div", { class: "adm-codeblock", text: out.token }), out.base_url ? h("p", { class: "adm-sub", text: `Base URL: ${out.base_url}` }) : null), actions: [{ label: "I have copied it", onClick: () => reload() }] });
    }) })));
    return card(body, `${tokens.filter((t) => !t.revoked_at).length} active`);
  }

  // --------------------------------------------------------------------------------------------
  // Audit log
  // --------------------------------------------------------------------------------------------
  const ACTION_GROUPS = [["", "Any action"], ["member.", "Members"], ["coach.", "Coach assignments"], ["org.", "Organization and policy"], ["automation.", "Pause and resume"], ["application.", "Applications"], ["held.", "Held questions"], ["consent.", "Consent"], ["auth.", "Sign-in and MFA"], ["billing.", "Billing"], ["subscription.", "Subscription state"], ["data.", "Exports and deletion"], ["support.", "Aira support access"], ["candidate.", "Candidate workspace changes"], ["integrations.", "Integrations"], ["sso.", "Single sign-on"]];

  A.pages.audit = async (stage, route) => {
    A.crumbs("Audit log");
    stage.replaceChildren(ui.skeleton(6));
    let directory;
    try { directory = await A.directory(true); } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    const q = route.query;
    const action = ui.select(ACTION_GROUPS, ACTION_GROUPS.some(([v]) => v === q.action) ? q.action : "");
    const actionText = h("input", { type: "text", id: "auditActionText", placeholder: "Or an action name", value: ACTION_GROUPS.some(([v]) => v === q.action) ? "" : q.action || "" });
    const candidates = directory.members.filter((m) => m.role === "candidate");
    const candidate = ui.select([["", "Any candidate"], ...candidates.map((m) => [m.member_id, m.name || m.email])], q.candidate || "");
    const actor = ui.select([["", "Anyone"], ...directory.members.map((m) => [m.member_id, `${m.name || m.email} (${A.roleLabel[m.role]})`])], q.actor || "");
    const since = h("input", { type: "date", id: "auditSince", value: q.since || "" });
    const until = h("input", { type: "date", id: "auditUntil", value: q.until || "" });
    const results = h("div", {}); const status = h("span", { class: "adm-sub", role: "status" });
    const apply = () => Aira.router.go(["org", "audit"], { action: actionText.value.trim() || action.value, candidate: candidate.value, actor: actor.value, since: since.value, until: until.value }, { replace: true });
    for (const control of [action, candidate, actor, since, until]) control.addEventListener("change", apply);
    actionText.addEventListener("change", apply);
    const day = (value, end) => (value ? Math.floor(Date.parse(value + (end ? "T23:59:59Z" : "T00:00:00Z")) / 1000) : "");
    const params = (before) => A.q({ limit: 50, before, action: q.action, candidate: q.candidate, actor: q.actor, since: day(q.since, false), until: day(q.until, true) });
    const verify = h("button", { type: "button", class: "btn secondary", text: "Verify chain", onclick: (e) => Aira.withBusy(e.currentTarget, async () => {
      const out = await api("/api/org/audit/verify");
      status.className = `adm-sub adm-chain ${out.ok ? "ok" : "bad"}`;
      status.textContent = out.ok ? `The audit log is intact: ${A.n(out.checked)} entries verified.` : `The audit log was altered near entry ${out.broken_at}.`;
    }) });
    const exportBtn = h("button", { type: "button", class: "btn secondary", text: "Export (JSON lines)", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await A.download("/api/org/audit/export", "audit.jsonl"); }) });
    stage.replaceChildren(h("div", { class: "adm-page" }, A.head("Audit log", "Every action with who did it, for which candidate, and when. Entries cannot be edited or deleted; each carries the hash of the one before it.", verify, exportBtn),
      status,
      h("div", { class: "filters" }, ui.field("Action", action), ui.field("Action name", actionText), ui.field("Candidate", candidate), ui.field("Who", actor), ui.field("From", since), ui.field("To", until)), results));
    const draw = async () => {
      results.replaceChildren(ui.skeleton(4));
      try {
        const first = await api(`/api/org/audit${params()}`);
        const list = h("div", { class: "adm-list" }); const more = h("div", { class: "panel-body" });
        const retention = first.retention_days ? `Your plan keeps ${A.n(first.retention_days)} days of history.` : "";
        const append = (events) => { for (const event of events) list.append(A.eventRow(event, directory)); };
        append(first.events);
        let last = first.events.length ? first.events[first.events.length - 1].seq : 0; let exhausted = first.events.length < 50;
        const button = h("button", { type: "button", class: "btn secondary", text: "Load older entries", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { const next = await api(`/api/org/audit${params(last)}`); append(next.events); if (next.events.length) last = next.events[next.events.length - 1].seq; if (next.events.length < 50) { exhausted = true; button.remove(); } }) });
        if (!exhausted) more.append(button);
        results.replaceChildren(A.panel("Events", h("div", {}, first.events.length ? list : A.empty("No entries match these filters."), exhausted ? null : more), { sub: retention }));
      } catch (error) { results.replaceChildren(ui.error(error, draw)); }
    };
    await draw();
  };

  // --------------------------------------------------------------------------------------------
  // Data and closure
  // --------------------------------------------------------------------------------------------
  A.pages.data = async (stage) => {
    A.crumbs("Data & closure");
    stage.replaceChildren(ui.skeleton(6));
    const staff = ["owner", "admin"].includes(cloud.role()); const owner = cloud.role() === "owner";
    let org; let exports; let directory; let support = null; let notices = null;
    try {
      [org, exports, directory] = await Promise.all([api("/api/org"), api("/api/org/exports"), A.directory(true)]);
      if (owner) support = await optional("/api/org/support");
      if (staff || A.can("billing.manage")) notices = await optional("/api/org/notices");
    } catch (error) { stage.replaceChildren(ui.error(error, () => Aira.router.render())); return; }
    const reload = () => Aira.router.render();
    const page = h("div", { class: "adm-page" });
    page.append(A.head("Data & closure", "Export everything the organization holds, export one candidate, and close the organization. Exports are JSON plus the original documents."));
    if (org.closure_requested_at) page.append(h("div", { class: "adm-note bad", role: "status" }, `Closure was requested ${A.ago(org.closure_requested_at)}. The organization is deleted after the retention window; only the billing record and an audit stub are kept.`, owner ? " " : "", owner ? h("button", { type: "button", class: "btn secondary sm", text: "Cancel closure", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api("/api/org/closure", { method: "DELETE" }); Aira.toast("Closure cancelled.", "success"); cloud.refreshBanners(); reload(); }) }) : null));

    const candidateSelect = ui.select([["", "Choose a candidate"], ...directory.members.filter((m) => m.role === "candidate").map((m) => [m.member_id, m.name || m.email])], "");
    page.append(A.panel("Export", h("div", { class: "panel-body adm-form" },
      h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn", text: "Export the whole organization", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { const made = await api("/api/org/exports", { method: "POST", body: {} }); Aira.toast(`Export ready (${fmt.bytes(made.size)}).`, "success"); await A.download(`/api/org/exports/${made.export_id}/download`, "aira-organization-export.zip"); reload(); }) })),
      h("div", { class: "adm-fields" }, ui.field("Or one candidate", candidateSelect)),
      h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn secondary", text: "Export candidate", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { if (!candidateSelect.value) { Aira.toast("Choose a candidate first.", "error"); return; } const made = await api("/api/org/exports", { method: "POST", body: { candidate_id: candidateSelect.value } }); Aira.toast("Export ready.", "success"); await A.download(`/api/org/exports/${made.export_id}/download`, "aira-candidate-export.zip"); reload(); }) })))));
    page.append(A.panel("Recent exports", exports.exports.length ? h("div", { class: "adm-list" }, ...exports.exports.map((x) => h("div", { class: "adm-item" }, h("div", {}, h("div", { class: "title", text: x.candidate_id ? `Candidate: ${A.nameOf(directory, x.candidate_id) || "removed candidate"}` : "Whole organization" }), h("div", { class: "sub", text: `${fmt.bytes(x.size)} · created ${A.ago(x.created_at)} · available until ${A.when(x.expires_at)}${x.downloaded_at ? " · downloaded" : ""}` })),
      h("div", { class: "aside" }, h("button", { type: "button", class: "btn secondary sm", text: "Download", onclick: () => A.download(`/api/org/exports/${x.export_id}/download`, `aira-export-${x.export_id.slice(0, 8)}.zip`) }))))) : A.empty("No exports yet."), { sub: "Available for 30 days" }));

    if (owner && support) page.append(supportPanel(support, reload));
    if (notices && notices.ok) page.append(noticesPanel(notices.data, reload));
    if (owner) page.append(A.panel("Close the organization", h("div", { class: "panel-body adm-form" },
      h("p", { class: "adm-sub", text: "Closing cancels the subscription, stops all automation and creates a full export. Everything is deleted after a 30-day window; only the billing record and an audit stub are kept. You can cancel until then." }),
      org.closure_requested_at ? A.badge("Closure requested", "bad") : h("div", { class: "adm-btnrow" }, h("button", { type: "button", class: "btn danger secondary", text: "Request closure", onclick: () => closureModal(reload) })))));
    stage.replaceChildren(page);
  };

  function closureModal(reload) {
    const password = h("input", { type: "password", autocomplete: "current-password", id: "closePassword", "aria-label": "Your password" });
    const error = inlineError();
    Aira.modal({ title: "Close the organization", body: h("div", { class: "adm-form" }, h("p", { class: "muted", text: "This stops all automation now and builds a full export. After 30 days every candidate workspace, document, key and browser profile is deleted. Only the billing record and an audit stub remain." }), ui.field("Confirm with your password", password), error),
      actions: [{ label: "Cancel", kind: "ghost" }, { label: "Request closure", kind: "danger", onClick: async (close) => { error.textContent = ""; if (!password.value) { error.textContent = "Enter your password."; return false; } try { const out = await api("/api/org/closure", { method: "POST", body: { password: password.value } }); Aira.toast(`Closure requested. A full export (${fmt.bytes(out.export.size)}) is ready under Exports.`, "success", { timeout: 9000 }); cloud.refreshBanners(); close(); reload(); return false; } catch (e) { showError(error, e); return false; } } }] });
  }

  function supportPanel(result, reload) {
    if (!result.ok) return A.panel("Aira support access", h("div", { class: "panel-body" }, h("div", { class: "adm-note", text: A.message(result.error) })));
    const requests = result.data.requests || [];
    const hours = ui.select([["1", "1 hour"], ["4", "4 hours"], ["8", "8 hours"], ["24", "24 hours"]], "24");
    const rows = requests.length ? h("div", { class: "adm-list" }, ...requests.map((r) => {
      const open = r.status === "requested"; const active = r.status === "approved" && r.expires_at > Date.now() / 1000;
      return h("div", { class: "adm-item" }, h("div", {}, h("div", { class: "title", text: `${r.operator_name || r.operator_email} asks for ${r.role === "admin" ? "administrator" : "read-only"} access` }), h("div", { class: "sub", text: `${r.reason} · requested ${A.ago(r.requested_at)}${r.expires_at ? ` · until ${A.when(r.expires_at)}` : ""}` })),
        h("div", { class: "aside" }, open ? [
          h("button", { type: "button", class: "btn sm", text: "Approve", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api(`/api/org/support/${r.access_id}/approve`, { method: "POST", body: { hours: Number(hours.value) } }); Aira.toast("Access approved.", "success"); cloud.refreshBanners(); reload(); }) }),
          h("button", { type: "button", class: "btn ghost sm", text: "Deny", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api(`/api/org/support/${r.access_id}/deny`, { method: "POST", body: {} }); Aira.toast("Request denied.", "success"); reload(); }) })]
          : active ? [A.badge("Active", "warn"), h("button", { type: "button", class: "btn danger secondary sm", text: "Revoke now", onclick: (e) => Aira.withBusy(e.currentTarget, async () => { await api(`/api/org/support/${r.access_id}/revoke`, { method: "POST", body: {} }); Aira.toast("Access revoked.", "success"); cloud.refreshBanners(); reload(); }) })] : A.badge(A.titleCase(r.status), r.status === "denied" ? "bad" : "")));
    })) : A.empty("Aira support has not asked for access.");
    return A.panel("Aira support access", h("div", {}, h("div", { class: "panel-body adm-form" }, h("p", { class: "adm-sub", text: "Support sees your organization only if you approve, for at most 24 hours. Your members see a banner while it is active and every step is audited." }), h("div", { class: "adm-fields" }, ui.field("Approve for", hours))), rows), { sub: "Owner only" });
  }

  function noticesPanel(data, reload) {
    const notices = data.notices || [];
    return A.panel("Notices", notices.length ? h("div", { class: "adm-list" }, ...notices.map((n) => h("a", { class: "adm-item", href: n.link || "#/org" }, h("div", {}, h("div", { class: "title", text: n.title }), h("div", { class: "sub", text: n.body })), h("div", { class: "aside" }, A.badge(n.level, n.level === "danger" ? "bad" : n.level === "warning" ? "warn" : ""), n.read_at ? null : A.badge("New", "good"), h("span", { class: "sub", text: A.ago(n.created_at) }))))) : A.empty("No notices."), { sub: data.unread ? `${data.unread} unread` : "", actions: data.unread ? h("button", { type: "button", class: "btn secondary sm", text: "Mark all read", onclick: async () => { await api("/api/org/notices/read", { method: "POST", body: {} }); reload(); } }) : null });
  }
})();

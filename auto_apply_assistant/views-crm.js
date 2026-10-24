/* Aira — Interviews, Recruiters (with Messages), Documents */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy, router } = Aira;
  const INTERVIEW_TYPES = ["recruiter_screen", "technical_interview", "manager_interview", "panel_interview", "final_interview", "assessment", "other"];
  const zone = () => (Aira.state.settings && Aira.state.settings.timezone) || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  // -------------------------------------------------------------------------
  // Interviews
  // -------------------------------------------------------------------------
  Aira.views.interviews = async (main, route) => {
    const scope = route.query.scope || "upcoming";
    const list = h("div", { class: "panel" });
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Interviews" }), h("p", { text: "Track every conversation with its time zone, participants, prep notes, and follow-ups. Add events to any calendar with one click." })), h("div", { class: "page-actions" }, h("button", { type: "button", class: "btn secondary sm", text: "Download all (.ics)", onclick: () => Aira.download("/api/interviews/calendar.ics", "aira-interviews.ics") }), h("a", { class: "btn secondary sm", href: "#/applications?tab=email", text: "Detect from email" }), h("button", { type: "button", class: "btn sm", text: "+ Add interview", onclick: () => interviewForm(null, () => router.render()) }))),
      h("nav", { class: "subnav", "aria-label": "Interview scope" }, ...[["upcoming", "Upcoming"], ["past", "Past"], ["all", "All"]].map(([id, label]) => h("a", { href: `#/interviews?scope=${id}`, "aria-current": id === scope ? "page" : "false", text: label }))),
      list,
    );
    const load = async () => {
      list.replaceChildren(ui.skeleton(4));
      try {
        const data = await api(`/api/interviews?scope=${scope}`);
        const rows = h("div", { class: "list", "data-roving": "1" });
        if (!data.interviews.length) rows.append(ui.empty(scope === "upcoming" ? "No upcoming interviews" : "Nothing here", "Add one manually or import an invitation email.", h("button", { type: "button", class: "btn", text: "Add interview", onclick: () => interviewForm(null, load) })));
        for (const item of data.interviews) rows.append(h("button", { type: "button", class: "row", "data-roving-item": "1", onclick: () => openInterview(item.interview_id, load) }, h("div", {}, h("span", { class: "title", text: `${fmt.title(fmt.stage(item.interview_type))} · ${item.company || item.job_title || "Interview"}` }), h("span", { class: "sub", text: `${fmt.datetime(item.start_at, item.timezone)} (${item.timezone})${item.role ? ` · ${item.role}` : ""}${item.recruiter_name ? ` · with ${item.recruiter_name}` : ""}` }), h("div", { class: "meta" }, h("span", { class: `badge ${item.status === "cancelled" ? "stage rejected" : item.status === "completed" ? "stage applied" : "stage saved"}`, text: item.status }), item.meeting_link ? h("span", { class: "badge", text: "Meeting link" }) : null, item.source === "email" ? h("span", { class: "badge", text: "From email" }) : null)), h("small", { class: "muted", text: fmt.ago(item.start_at) })));
        list.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: `${fmt.title(scope)} interviews` }), h("small", { class: "muted", text: String(data.interviews.length) })), rows);
      } catch (error) { list.replaceChildren(ui.error(error, load)); }
    };
    await load();
    if (route.path[1]) openInterview(route.path[1], load);
  };

  async function openInterview(id, refresh) {
    const body = h("div", {}, ui.skeleton(4));
    const panel = Aira.drawer({ title: "Interview", body, onClose: refresh });
    let data;
    try { data = await api(`/api/interviews/${id}`); } catch (error) { body.replaceChildren(ui.error(error)); return; }
    const i = data.interview;
    panel.head.querySelector("h2").textContent = `${fmt.title(fmt.stage(i.interview_type))} · ${i.company || i.job_title || "Interview"}`;
    const links = h("div", { class: "calendar-links" },
      h("button", { type: "button", class: "btn secondary sm", text: "Download .ics", onclick: () => Aira.download(`/api/interviews/${i.interview_id}/calendar.ics`, `interview-${i.interview_id.slice(0, 8)}.ics`) }),
      h("a", { class: "btn secondary sm", href: data.calendar.google, target: "_blank", rel: "noopener noreferrer", text: "Google Calendar ↗" }),
      h("a", { class: "btn secondary sm", href: data.calendar.outlook, target: "_blank", rel: "noopener noreferrer", text: "Outlook.com ↗" }),
      h("a", { class: "btn secondary sm", href: data.calendar.office365, target: "_blank", rel: "noopener noreferrer", text: "Office 365 ↗" }),
    );
    body.replaceChildren(
      h("div", { class: "btn-row", style: { marginBottom: "12px" } }, h("button", { type: "button", class: "btn sm", text: "Edit", onclick: () => interviewForm(i, () => openInterview(id, refresh)) }), i.status !== "completed" && i.status !== "cancelled" ? h("button", { type: "button", class: "btn secondary sm", text: "Mark completed", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/interviews/${id}`, { method: "PATCH", body: { status: "completed" } }); toast("Marked completed.", "success"); openInterview(id, refresh); }) }) : null, i.status !== "cancelled" ? h("button", { type: "button", class: "btn ghost sm danger", text: "Cancel interview", onclick: async (event) => { if (await Aira.confirm({ title: "Cancel this interview?", message: "The calendar entry becomes a cancellation (same UID, next sequence) and reminders are dismissed.", confirmLabel: "Cancel interview", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/interviews/${id}`, { method: "PATCH", body: { status: "cancelled" } }); toast("Interview cancelled.", "success"); openInterview(id, refresh); }); } }) : null, h("button", { type: "button", class: "btn ghost sm danger", text: "Delete", onclick: async (event) => { if (await Aira.confirm({ title: "Delete this interview?", message: "This removes it permanently.", confirmLabel: "Delete", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/interviews/${id}`, { method: "DELETE" }); toast("Deleted.", "success"); Aira.closeDrawer(); }); } })),
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h3", { text: "When & where" }), ui.kv([["Start", `${fmt.datetime(i.start_at, i.timezone)} (${i.timezone})`], ["End", fmt.datetime(i.end_at, i.timezone)], ["Your local time", fmt.datetime(i.start_at)], ["Status", i.status], ["Meeting link", i.meeting_link ? h("a", { href: i.meeting_link, target: "_blank", rel: "noopener noreferrer", text: i.meeting_link }) : null], ["Location", i.location || null], ["Job", i.job_id ? h("a", { href: "#", text: i.job_title || i.job_id, onclick: (event) => { event.preventDefault(); Aira.openJob(i.job_id); } }) : null], ["Recruiter", i.recruiter_id ? h("a", { href: `#/recruiters/${i.recruiter_id}`, text: i.recruiter_name || "Recruiter" }) : null], ["Hiring manager", i.hiring_manager || null], ["Calendar UID", `${data.calendar.uid} · seq ${data.calendar.sequence}`]]), h("div", { style: { marginTop: "12px" } }, links)),
        h("div", { class: "card" }, h("h3", { text: "People" }), i.participants.length ? h("ul", { class: "inline-list" }, ...i.participants.map((p) => h("li", { text: `${p.name}${p.role ? ` · ${p.role}` : ""}${p.email ? ` · ${p.email}` : ""}` }))) : h("p", { class: "muted", text: "No participants listed." }), h("h3", { text: "Action items", style: { marginTop: "14px" } }), i.action_items.length ? h("ul", { class: "inline-list" }, ...i.action_items.map((a) => h("li", { text: a }))) : h("p", { class: "muted", text: "None yet." }), i.follow_up_at ? h("p", { class: "note", style: { marginTop: "10px" }, text: `Follow-up due ${fmt.datetime(i.follow_up_at)}` }) : null),
      ),
      h("div", { class: "grid cols-2", style: { marginTop: "14px" } }, h("div", { class: "card" }, h("h3", { text: "Preparation" }), h("div", { class: "description", style: { maxHeight: "260px" }, text: i.preparation || "Add prep notes: STAR stories, questions to ask, research." })), h("div", { class: "card" }, h("h3", { text: "Notes & feedback" }), h("div", { class: "description", style: { maxHeight: "260px" }, text: [i.notes, i.feedback].filter(Boolean).join("\n\n") || "Capture notes and feedback after the conversation." }))),
    );
  }

  function interviewForm(existing, onSaved) {
    const v = existing || {};
    const jobId = h("input", { type: "text", value: v.job_id || "", placeholder: "Optional job ID (e.g. 4123456789 or greenhouse:123)" });
    const company = h("input", { type: "text", value: v.company || "", maxlength: 160 });
    const role = h("input", { type: "text", value: v.role || "", maxlength: 160 });
    const type = ui.select(INTERVIEW_TYPES.map((t) => [t, fmt.title(fmt.stage(t))]), v.interview_type || "recruiter_screen");
    const timezone = h("input", { type: "text", value: v.timezone || zone(), list: "tzlist", maxlength: 60 });
    const start = h("input", { type: "datetime-local", required: true, value: v.start_at ? localInZone(v.start_at, v.timezone) : "" });
    const end = h("input", { type: "datetime-local", value: v.end_at ? localInZone(v.end_at, v.timezone) : "" });
    const link = h("input", { type: "url", value: v.meeting_link || "", placeholder: "https://…" });
    const location = h("input", { type: "text", value: v.location || "", maxlength: 300 });
    const manager = h("input", { type: "text", value: v.hiring_manager || "", maxlength: 160 });
    const participants = h("textarea", { rows: 3, placeholder: "One per line: Name, Role, email@example.com" }, (v.participants || []).map((p) => [p.name, p.role, p.email].filter(Boolean).join(", ")).join("\n"));
    const prep = h("textarea", { rows: 4 }, v.preparation || "");
    const notes = h("textarea", { rows: 3 }, v.notes || "");
    const feedback = h("textarea", { rows: 3 }, v.feedback || "");
    const actions = h("textarea", { rows: 3, placeholder: "One action item per line" }, (v.action_items || []).join("\n"));
    const followUp = h("input", { type: "datetime-local", value: v.follow_up_at ? localInZone(v.follow_up_at, v.timezone) : "" });
    const status = ui.select([["scheduled", "Scheduled"], ["rescheduled", "Rescheduled"], ["completed", "Completed"], ["cancelled", "Cancelled"]], v.status || "scheduled");
    const updateStage = h("input", { type: "checkbox", checked: true });
    const body = h("div", {},
      h("div", { class: "form-grid" }, ui.field("Company", company), ui.field("Role", role), ui.field("Interview type", type), ui.field("Status", status), ui.field("Start (in the interview's time zone)", start), ui.field("End", end, "Defaults to 45 minutes after the start."), ui.field("Time zone (IANA)", timezone, "e.g. America/New_York"), ui.field("Meeting link", link), ui.field("Location", location), ui.field("Hiring manager", manager), ui.field("Linked job", jobId), ui.field("Follow-up reminder", followUp)),
      h("div", { class: "form-grid", style: { marginTop: "12px" } }, h("div", { class: "field wide" }, h("label", { text: "Participants", for: participants.id = "ivParts" }), participants), h("div", { class: "field" }, h("label", { text: "Preparation", for: prep.id = "ivPrep" }), prep), h("div", { class: "field" }, h("label", { text: "Action items", for: actions.id = "ivActions" }), actions), h("div", { class: "field" }, h("label", { text: "Notes", for: notes.id = "ivNotes" }), notes), h("div", { class: "field" }, h("label", { text: "Feedback", for: feedback.id = "ivFeedback" }), feedback)),
      existing ? null : h("label", { class: "check", style: { marginTop: "10px" } }, updateStage, "Move the linked application to the matching interview stage"),
    );
    Aira.modal({ title: existing ? "Edit interview" : "Add interview", body, wide: true, actions: [{ label: "Cancel", kind: "ghost" }, { label: existing ? "Save changes" : "Create interview", kind: "", onClick: async () => {
      const payload = { job_id: jobId.value.trim() || null, company: company.value, role: role.value, interview_type: type.value, status: status.value, timezone: timezone.value.trim(), start_at: start.value, end_at: end.value || null, meeting_link: link.value, location: location.value, hiring_manager: manager.value, preparation: prep.value, notes: notes.value, feedback: feedback.value, action_items: actions.value.split("\n").map((s) => s.trim()).filter(Boolean), follow_up_at: followUp.value || null, participants: participants.value.split("\n").map((line) => line.split(",").map((s) => s.trim())).filter((parts) => parts[0]).map(([name, roleName, email]) => ({ name, role: roleName || "", email: email || "" })), update_stage: updateStage.checked };
      try {
        if (existing) await api(`/api/interviews/${existing.interview_id}`, { method: "PATCH", body: payload });
        else await api("/api/interviews", { method: "POST", body: payload });
        toast(existing ? "Interview updated." : "Interview created with reminders.", "success");
        onSaved();
      } catch (error) { Aira.reportError(error); return false; }
    } }] });
    if (!document.getElementById("tzlist")) document.body.append(h("datalist", { id: "tzlist" }, ...(typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London", "Asia/Kolkata"]).map((tz) => h("option", { value: tz }))));
  }
  function localInZone(iso, tz) {
    try {
      const date = new Date(iso);
      const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz || "UTC", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date).map((p) => [p.type, p.value]));
      return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
    } catch { return ""; }
  }

  // -------------------------------------------------------------------------
  // Recruiters
  // -------------------------------------------------------------------------
  Aira.views.recruiters = async (main, route) => {
    const tab = route.query.tab || "contacts";
    const content = h("div", {});
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Recruiters" }), h("p", { text: "A lightweight CRM: contacts, every interaction, follow-up reminders, and the jobs and interviews each person is connected to." })), h("div", { class: "page-actions" }, h("button", { type: "button", class: "btn sm", text: "+ Add recruiter", onclick: () => recruiterForm(null, () => router.render()) }))),
      ui.tabs([{ id: "contacts", label: "Contacts" }, { id: "messages", label: "Messages" }], tab, (id) => router.go(["recruiters"], { tab: id }, { replace: true })),
      content,
    );
    if (tab === "messages") { await Aira.renderMessages(content, route); return; }
    const search = h("input", { type: "search", "data-search": "1", placeholder: "Search by name, company, or email", value: route.query.q || "" });
    let timer;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 300); });
    const list = h("div", { class: "panel" });
    const followUps = h("div", { class: "panel" });
    content.replaceChildren(h("div", { class: "filters" }, h("div", { class: "field grow" }, h("label", { for: "recruiterSearch", text: "Search" }), Object.assign(search, { id: "recruiterSearch" }))), h("div", { class: "grid sidebar-right" }, list, followUps));
    async function load() {
      list.replaceChildren(ui.skeleton(4));
      try {
        const data = await api(`/api/recruiters?q=${encodeURIComponent(search.value)}`);
        const rows = h("div", { class: "list", "data-roving": "1" });
        if (!data.recruiters.length) rows.append(ui.empty("No recruiters yet", "Add contacts manually or import a recruiter email.", h("button", { type: "button", class: "btn", text: "Add recruiter", onclick: () => recruiterForm(null, load) })));
        for (const r of data.recruiters) rows.append(h("button", { type: "button", class: "row", "data-roving-item": "1", onclick: () => openRecruiter(r.recruiter_id, load) }, h("div", {}, h("span", { class: "title", text: r.name }), h("span", { class: "sub", text: [r.title, r.company, r.email].filter(Boolean).join(" · ") }), h("div", { class: "meta" }, h("span", { class: "badge", text: `${fmt.plural(r.interaction_count || 0, "interaction")}` }), r.next_follow_up_at ? h("span", { class: "badge stage needs_review", text: `Follow up ${fmt.date(r.next_follow_up_at)}` }) : null, r.source === "email" ? h("span", { class: "badge", text: "From email" }) : null)), h("small", { class: "muted", text: r.last_interaction_at ? fmt.ago(r.last_interaction_at) : "" })));
        list.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: "Contacts" }), h("small", { class: "muted", text: String(data.recruiters.length) })), rows);
        followUps.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: "Upcoming follow-ups" })), data.follow_ups.length ? h("div", { class: "list" }, ...data.follow_ups.map((f) => h("button", { type: "button", class: "row", onclick: () => openRecruiter(f.recruiter_id, load) }, h("div", {}, h("span", { class: "title", text: f.name }), h("span", { class: "sub", text: `${fmt.datetime(f.follow_up_at)} · ${f.summary.slice(0, 80)}` }))))) : h("div", { class: "empty", text: "No follow-ups scheduled." }));
      } catch (error) { list.replaceChildren(ui.error(error, load)); }
    }
    await load();
    if (route.path[1]) openRecruiter(route.path[1], load);
  };

  async function openRecruiter(id, refresh) {
    const body = h("div", {}, ui.skeleton(4));
    const panel = Aira.drawer({ title: "Recruiter", body, onClose: refresh });
    let r;
    try { r = (await api(`/api/recruiters/${id}`)).recruiter; } catch (error) { body.replaceChildren(ui.error(error)); return; }
    panel.head.querySelector("h2").textContent = r.name;
    const kind = ui.select([["email", "Email"], ["message", "Message"], ["call", "Call"], ["meeting", "Meeting"], ["note", "Note"], ["follow_up", "Follow-up"]], "note");
    const direction = ui.select([["outbound", "I reached out"], ["inbound", "They reached out"]], "outbound");
    const summary = h("textarea", { rows: 2, placeholder: "What happened?" });
    const when = h("input", { type: "datetime-local", value: Aira.toLocalInput(new Date().toISOString()) });
    const followUp = h("input", { type: "datetime-local" });
    const jobLink = h("input", { type: "text", placeholder: "Job ID (optional)" });
    const addBtn = h("button", { type: "button", class: "btn sm", text: "Log interaction" });
    addBtn.addEventListener("click", () => withBusy(addBtn, async () => { await api(`/api/recruiters/${id}/interactions`, { method: "POST", body: { kind: kind.value, direction: direction.value, summary: summary.value, occurred_at: when.value ? new Date(when.value).toISOString() : null, follow_up_at: followUp.value ? new Date(followUp.value).toISOString() : null, job_id: jobLink.value.trim() || null } }); toast("Interaction logged.", "success"); openRecruiter(id, refresh); }));
    // Element.replaceChildren prints a null as the text "null": drop the empty slots.
    body.replaceChildren(...[
      h("div", { class: "btn-row", style: { marginBottom: "12px" } }, h("button", { type: "button", class: "btn sm", text: "Edit", onclick: () => recruiterForm(r, () => openRecruiter(id, refresh)) }), r.email ? h("a", { class: "btn secondary sm", href: `mailto:${r.email}`, text: "Email" }) : null, h("button", { type: "button", class: "btn ghost sm danger", text: "Delete", onclick: async (event) => { if (await Aira.confirm({ title: "Delete this recruiter?", message: "The contact is removed from lists; interactions stay in history.", confirmLabel: "Delete", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/recruiters/${id}`, { method: "DELETE" }); toast("Recruiter deleted.", "success"); Aira.closeDrawer(); }); } })),
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h3", { text: "Contact" }), ui.kv([["Title", r.title || null], ["Company", r.company || null], ["Email", r.email || null], ["Phone", r.phone || null], ["LinkedIn profile", r.linkedin_url || null], ["Source", r.source], ["Consent note", r.consent_note || null], ["Notes", r.notes || null]])),
        h("div", { class: "card" }, h("h3", { text: "Related" }), r.related_jobs.length ? h("ul", { class: "inline-list" }, ...r.related_jobs.map((j) => h("li", {}, h("a", { href: "#", text: `${j.title} (${fmt.stage(j.stage)})`, onclick: (event) => { event.preventDefault(); Aira.openJob(j.job_id); } })))) : h("p", { class: "muted", text: "No linked jobs yet. Link one when logging an interaction." }), r.interviews.length ? h("ul", { class: "inline-list" }, ...r.interviews.map((i) => h("li", {}, h("a", { href: `#/interviews/${i.interview_id}`, text: `${fmt.title(fmt.stage(i.interview_type))} · ${fmt.datetime(i.start_at)} · ${i.status}` })))) : null),
      ),
      (r.threads || []).length ? h("div", { class: "card", style: { marginTop: "14px" } }, h("h3", { text: "LinkedIn conversations" }), ...r.threads.map((t) => h("div", { class: "row", style: { padding: "6px 0" } }, h("div", {}, h("span", { class: "title", text: t.participant || "Conversation" }), h("span", { class: "sub", text: t.mode === "auto" ? "Auto-reply is on" : "Manual send" })), h("a", { class: "btn secondary sm", href: `#/recruiters?tab=messages&thread=${encodeURIComponent(t.thread_id)}&participant=${encodeURIComponent(t.participant || "")}`, text: "Open conversation", onclick: () => Aira.closeDrawer() })))) : null,
      h("div", { class: "card", style: { marginTop: "14px" } }, h("h3", { text: "Log an interaction" }), h("div", { class: "form-grid" }, ui.field("Type", kind), ui.field("Direction", direction), ui.field("When", when), ui.field("Follow-up reminder", followUp), ui.field("Related job", jobLink), h("div", { class: "field wide" }, h("label", { for: "intSummary", text: "Summary" }), Object.assign(summary, { id: "intSummary" }))), h("div", { style: { marginTop: "10px" } }, addBtn)),
      h("div", { class: "card", style: { marginTop: "14px" } }, h("h3", { text: "Communication history" }), r.interactions.length ? h("ul", { class: "timeline" }, ...r.interactions.map((x) => h("li", {}, h("strong", { text: `${fmt.title(x.kind)} · ${x.direction === "inbound" ? "from them" : "from you"}` }), h("div", { text: x.summary }), h("small", { text: `${fmt.datetime(x.occurred_at)}${x.job_title ? ` · ${x.job_title}` : ""}${x.follow_up_at ? ` · follow up ${fmt.datetime(x.follow_up_at)}` : ""}` }), h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: async (event) => { if (await Aira.confirm({ title: "Remove this interaction?", message: "", confirmLabel: "Remove", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/interactions/${x.interaction_id}`, { method: "DELETE" }); openRecruiter(id, refresh); }); } })))) : h("p", { class: "muted", text: "No interactions logged yet." })),
    ].filter(Boolean));
  }

  function recruiterForm(existing, onSaved) {
    const v = existing || {};
    const name = h("input", { type: "text", value: v.name || "", required: true, maxlength: 160 });
    const title = h("input", { type: "text", value: v.title || "", maxlength: 160 });
    const company = h("input", { type: "text", value: v.company || "", maxlength: 160 });
    const email = h("input", { type: "email", value: v.email || "", maxlength: 254 });
    const phone = h("input", { type: "tel", value: v.phone || "", maxlength: 40 });
    const linkedin = h("input", { type: "url", value: v.linkedin_url || "", placeholder: "https://www.linkedin.com/in/…" });
    const consent = h("input", { type: "text", value: v.consent_note || "", placeholder: "e.g. Met at conference; agreed to stay in touch" });
    const notes = h("textarea", { rows: 3 }, v.notes || "");
    const body = h("div", { class: "form-grid" }, ui.field("Name", name), ui.field("Title", title), ui.field("Company", company), ui.field("Email", email), ui.field("Phone", phone), ui.field("LinkedIn URL", linkedin), h("div", { class: "field wide" }, h("label", { for: "rcConsent", text: "Consent / source note" }), Object.assign(consent, { id: "rcConsent" })), h("div", { class: "field wide" }, h("label", { for: "rcNotes", text: "Notes" }), Object.assign(notes, { id: "rcNotes" })));
    Aira.modal({ title: existing ? "Edit recruiter" : "Add recruiter", body, actions: [{ label: "Cancel", kind: "ghost" }, { label: existing ? "Save" : "Add", kind: "", onClick: async () => {
      const payload = { name: name.value, title: title.value, company: company.value, email: email.value, phone: phone.value, linkedin_url: linkedin.value, consent_note: consent.value, notes: notes.value };
      try {
        if (existing) await api(`/api/recruiters/${existing.recruiter_id}`, { method: "PATCH", body: payload });
        else await api("/api/recruiters", { method: "POST", body: payload });
        toast(existing ? "Recruiter updated." : "Recruiter added (merged if a duplicate existed).", "success");
        onSaved();
      } catch (error) { Aira.reportError(error); return false; }
    } }] });
  }

  // Messages: see views-messages.js (Aira.renderMessages)

  // -------------------------------------------------------------------------
  // Documents
  // -------------------------------------------------------------------------
  Aira.views.documents = async (main, route) => {
    const kind = route.query.kind || "";
    const list = h("div", { class: "panel" });
    const kindSelect = ui.select([["", "All kinds"]], kind);
    kindSelect.addEventListener("change", () => router.go(["documents"], { kind: kindSelect.value }, { replace: true }));
    const uploadKind = ui.select([["other", "Other"]], "master_resume");
    const file = h("input", { type: "file", accept: ".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.eml,.json,.ics,.csv" });
    const title = h("input", { type: "text", maxlength: 200, placeholder: "Optional title" });
    const jobId = h("input", { type: "text", placeholder: "Link to a job ID (optional)" });
    const uploadBtn = h("button", { type: "button", class: "btn", text: "Upload" });
    const drop = h("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Drop a file here or press Enter to choose" }, h("strong", { text: "Drop a file here" }), h("span", { text: "PDF, DOCX, TXT, MD, PNG, JPG, EML, JSON, ICS, CSV · up to 10 MB · encrypted at rest" }));
    drop.addEventListener("click", () => file.click());
    drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); file.click(); } });
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (event) => { event.preventDefault(); drop.classList.remove("over"); if (event.dataTransfer.files[0]) { file.files = event.dataTransfer.files; drop.querySelector("strong").textContent = event.dataTransfer.files[0].name; } });
    file.addEventListener("change", () => { if (file.files[0]) drop.querySelector("strong").textContent = file.files[0].name; });
    uploadBtn.addEventListener("click", () => withBusy(uploadBtn, async () => {
      const chosen = file.files[0];
      if (!chosen) { toast("Choose a file first.", "error"); return; }
      if (chosen.size > 10 * 1024 * 1024) { toast("Files must be under 10 MB.", "error"); return; }
      await api("/api/documents", { method: "POST", body: { filename: chosen.name, content_base64: await Aira.fileToBase64(chosen), kind: uploadKind.value, title: title.value, job_id: jobId.value.trim() || null } });
      toast("Document stored.", "success");
      file.value = ""; title.value = ""; drop.querySelector("strong").textContent = "Drop a file here";
      load();
    }));
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Documents" }), h("p", { text: "Master resumes, tailored versions, cover letters, confirmations, screenshots, offers, and certificates. Versioned, encrypted at rest, and auditable." }))),
      h("div", { class: "grid sidebar-right" },
        h("div", {}, h("div", { class: "filters" }, ui.field("Kind", kindSelect)), list),
        h("div", { class: "card" }, h("h3", { text: "Upload" }), drop, h("div", { style: { display: "none" } }, file), h("div", { class: "form-grid", style: { marginTop: "12px" } }, ui.field("Kind", uploadKind), ui.field("Title", title), ui.field("Job", jobId)), h("div", { style: { marginTop: "10px" } }, uploadBtn)),
      ),
    );
    async function load() {
      list.replaceChildren(ui.skeleton(4));
      try {
        const data = await api(`/api/documents${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`);
        if (kindSelect.options.length === 1) { for (const k of data.kinds) { kindSelect.append(h("option", { value: k, text: fmt.title(fmt.stage(k)), selected: k === kind })); } uploadKind.replaceChildren(...data.kinds.map((k) => h("option", { value: k, text: fmt.title(fmt.stage(k)), selected: k === "master_resume" }))); }
        const rows = h("div", { class: "list" });
        if (!data.documents.length) rows.append(ui.empty("No documents yet", "Upload a master resume or save tailored drafts from a job."));
        for (const d of data.documents) rows.append(h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: d.title || d.filename }), h("span", { class: "sub", text: `${fmt.title(fmt.stage(d.kind))} · v${d.version} · ${fmt.bytes(d.size)} · ${fmt.ago(d.created_at)}${d.job_id ? ` · job ${d.job_id}` : ""} · scan: ${fmt.stage(d.scan_status)}` })), h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary sm", text: "Download", onclick: () => Aira.download(`/api/documents/${d.document_id}/download`, d.filename) }), h("button", { type: "button", class: "btn ghost sm", text: "History", onclick: () => showVersions(d) }), h("button", { type: "button", class: "btn ghost sm danger", text: "Delete", onclick: async (event) => { if (await Aira.confirm({ title: "Delete this document?", message: "The encrypted file is removed. The audit record is kept.", confirmLabel: "Delete", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/documents/${d.document_id}`, { method: "DELETE" }); toast("Deleted.", "success"); load(); }); } }))));
        list.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: "Library" }), h("small", { class: "muted", text: `${data.usage.count} files · ${fmt.bytes(data.usage.bytes)} · kept ${data.retention_days} days` })), rows);
      } catch (error) { list.replaceChildren(ui.error(error, load)); }
    }
    await load();
  };
  async function showVersions(doc) {
    const data = await api(`/api/documents/${doc.document_id}/versions`);
    Aira.modal({ title: doc.title || doc.filename, wide: true, body: h("div", {}, h("h3", { text: "Versions" }), h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Version" }), h("th", { text: "Created" }), h("th", { text: "Size" }), h("th", { text: "" }))), h("tbody", {}, ...data.versions.map((v) => h("tr", {}, h("td", { text: `v${v.version}${v.deleted_at ? " (deleted)" : ""}` }), h("td", { text: fmt.datetime(v.created_at) }), h("td", { text: fmt.bytes(v.size) }), h("td", {}, v.deleted_at ? null : h("button", { type: "button", class: "btn ghost sm", text: "Download", onclick: () => Aira.download(`/api/documents/${v.document_id}/download`, v.filename) })))))), h("h3", { text: "Audit trail", style: { marginTop: "14px" } }), h("ul", { class: "timeline" }, ...data.audit.map((a) => h("li", {}, h("strong", { text: fmt.title(a.action) }), h("small", { text: `${a.actor} · ${fmt.datetime(a.at)} · ${a.detail || ""}` }))))), actions: [{ label: "Close", kind: "" }] });
  }
})();

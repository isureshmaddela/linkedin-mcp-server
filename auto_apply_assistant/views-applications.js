/* Aira — Dashboard, Applications (pipeline, queue, email updates) and the Apply tab */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy, router } = Aira;

  // -------------------------------------------------------------------------
  // Apply tab inside the job drawer
  // -------------------------------------------------------------------------
  Aira.renderApplyTab = ({ job, reload, pane }) => {
    const prep = job.preparation;
    const submission = job.submission;
    const docsReady = Boolean(job.resume_draft && job.cover_letter);
    const profileReady = Aira.state.profile && Aira.state.profile.completeness && Aira.state.profile.completeness.ready;
    const steps = h("div", { class: "step-list" });
    const stepNode = (state, title, text, action) => h("div", { class: `step ${state}` }, h("div", {}, h("strong", { text: title }), h("span", { text })), action || h("span"));

    // Step 1 profile
    steps.append(stepNode(profileReady ? "done" : "active", "Complete your profile", profileReady ? "Contact details and preferences are ready." : `Missing: ${(Aira.state.profile && Aira.state.profile.completeness ? Aira.state.profile.completeness.missing_required : []).join(", ") || "profile"}.`, profileReady ? null : h("a", { class: "btn secondary sm", href: "#/settings/profile", text: "Open profile" })));
    // Step 2 documents
    // Once the application is sent (here or another way) the preparation steps are no longer to-dos.
    const sentText = "Not needed: this application has been sent.";
    steps.append(stepNode(docsReady ? "done" : submission ? "" : profileReady ? "active" : "", "Approve tailored documents", docsReady ? "Resume and cover letter drafts are attached." : submission ? sentText : "Generate and review drafts in the Documents tab.", docsReady || submission ? null : h("button", { type: "button", class: "btn secondary sm", text: "Documents tab", onclick: () => Aira.openJob(job.job_id, { tab: "documents" }) })));
    // Step 3 prepare packet
    const prepDone = prep && ["approved", "submitted"].includes(prep.status);
    const prepareBtn = h("button", { type: "button", class: "btn sm" + (prep ? " secondary" : ""), text: prep ? "Re-prepare packet" : "Prepare packet", disabled: !job.description || !profileReady, onclick: (event) => withBusy(event.currentTarget, async () => { event.currentTarget.textContent = "Preparing…"; await api(`/api/jobs/${encodeURIComponent(job.job_id)}/prepare`, { method: "POST", body: {} }); toast("Application packet prepared.", "success"); reload("apply"); }) });
    steps.append(stepNode(prepDone ? "done" : submission ? "" : prep ? "active" : docsReady ? "active" : "", "Prepare and review answers", prep ? `${prep.summary.prefilled} prefilled · ${prep.summary.needs_confirmation} to confirm · ${prep.summary.unknown} unknown (${fmt.stage(prep.questions_source)})` : submission ? sentText : "Map the application questions to your profile and flag anything sensitive.", submission ? null : prepareBtn));
    // Step 4 submit
    steps.append(stepNode(submission ? "done" : prepDone ? "active" : "", "Apply on the provider site, then record confirmation", submission ? `Recorded ${fmt.datetime(submission.submitted_at)}${submission.confirmation_number ? ` · #${submission.confirmation_number}` : ""}` : "Monosage opens the application page; you submit it and paste the confirmation here.", null));
    pane.append(Aira.renderAutoApplyCard(job, reload), h("h3", { text: "Or apply yourself", style: { marginTop: "16px" } }), h("p", { class: "note", text: "Monosage prepares, prefills and flags; you review and submit on the provider site, then Monosage records the confirmation and tracks the outcome." }), steps);

    if (prep && !submission) pane.append(renderPacket(job, prep, reload));
    if (prepDone && !submission) pane.append(renderSubmitCard(job, prep, reload));
    if (!submission && ["discovered", "saved", "needs_review", "ready_to_apply", "applying"].includes(job.stage)) pane.append(renderOutsideCard(job, reload));
    if (submission) pane.append(h("div", { class: "card", style: { marginTop: "14px" } }, h("h3", { text: "Submitted" }), ui.kv([["Recorded", fmt.datetime(submission.submitted_at)], ["Confirmation #", submission.confirmation_number || "—"], ["Notes", submission.confirmation_text || "—"], ["Evidence", submission.evidence_document_id ? "Stored in Documents" : "None"]]), h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("a", { class: "btn secondary sm", href: "#/interviews", text: "Track interviews" }), h("a", { class: "btn ghost sm", href: "#/applications?tab=email", text: "Import a status email" }))));
  };

  function renderPacket(job, prep, reload) {
    const answers = {};
    const list = h("div", {});
    for (const field of prep.fields) {
      const key = field.provider_field_id || field.label;
      let input;
      if (field.field_type === "file") input = h("span", { class: "muted", text: field.canonical_field === "resume" ? (job.resume_draft ? "Tailored resume attached" : "Not ready") : field.canonical_field === "cover_letter" ? (job.cover_letter ? "Cover letter attached" : "Not ready") : "File upload on the provider site" });
      else if (field.options && field.options.length) input = ui.select([["", "Choose…"], ...field.options.map((o) => [o, o])], field.value || "", { "aria-label": field.label });
      else if (field.field_type === "textarea") input = h("textarea", { rows: 2, "aria-label": field.label }, field.value || "");
      else input = h("input", { type: field.field_type === "email" ? "email" : field.field_type === "url" ? "url" : "text", value: field.value || "", "aria-label": field.label, placeholder: field.status === "needs_confirmation" ? "Confirm or enter your answer" : "" });
      if (input.tagName !== "SPAN") input.addEventListener("input", () => { answers[key] = input.value; });
      list.append(h("div", { class: "packet-field" }, h("div", { class: "q" }, h("span", { text: field.label + (field.required ? " *" : "") }), h("small", { text: `${field.canonical_field ? fmt.stage(field.canonical_field) : "Unmapped"} · ${Math.round(field.confidence * 100)}% match${field.sensitivity === "sensitive" ? " · sensitive" : ""}` })), input, h("span", { class: `status status-${field.status}`, title: field.message, text: fmt.stage(field.status) })));
    }
    const notes = h("textarea", { rows: 2, placeholder: "Review notes (optional)" }, prep.review_notes || "");
    const remember = h("input", { type: "checkbox", checked: true });
    const card = h("div", { class: "card", style: { marginTop: "14px" } },
      h("h3", { text: `Application packet · ${fmt.stage(prep.status)}` }),
      prep.summary.sensitive_pending && prep.summary.sensitive_pending.length ? h("div", { class: "sensitive-note" }, h("span", { text: "⚠" }), h("span", { text: `Sensitive questions need your explicit answer: ${prep.summary.sensitive_pending.join(", ")}. Monosage never guesses work authorization, visa, salary, demographic, or legal answers.` })) : null,
      prep.summary.unknown_questions && prep.summary.unknown_questions.length ? h("p", { class: "muted", style: { fontSize: "12.5px" }, text: `Unmapped questions you will answer on the site: ${prep.summary.unknown_questions.join("; ")}` }) : null,
      list,
      h("div", { style: { marginTop: "12px" } }, ui.field("Review notes", notes), h("label", { class: "check", style: { marginTop: "8px" } }, remember, "Remember my confirmed answers for future applications")),
      h("div", { class: "btn-row", style: { marginTop: "12px" } },
        h("button", { type: "button", class: "btn secondary", text: "Save review", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/preparations/${prep.prep_id}`, { method: "PATCH", body: { answers, notes: notes.value, remember_answers: remember.checked } }); toast("Review saved.", "success"); reload("apply"); }) }),
        prep.status !== "approved" ? h("button", { type: "button", class: "btn", text: "Approve packet", onclick: (event) => withBusy(event.currentTarget, async () => { if (Object.keys(answers).length) await api(`/api/preparations/${prep.prep_id}`, { method: "PATCH", body: { answers, notes: notes.value, remember_answers: remember.checked } }); await api(`/api/preparations/${prep.prep_id}/approve`, { method: "POST", body: {} }); toast("Packet approved. Ready to apply.", "success"); reload("apply"); }) }) : h("span", { class: "badge stage ready_to_apply", text: "Approved" }),
      ),
    );
    return card;
  }

  // Applied by email, in person, on another site or before using Aira: record it so it is tracked like any other.
  function renderOutsideCard(job, reload) {
    const channel = ui.select([["website", "On the employer's website"], ["job_board", "Through a job board"], ["email", "By email"], ["in_person", "In person"], ["phone", "By phone"], ["referral", "Through a referral"], ["agency", "Through a recruiter or agency"], ["other", "Another way"]], job.has_posting === false ? "in_person" : "website");
    const today = Aira.toLocalInput(new Date().toISOString()).slice(0, 10); // the person's own day, not UTC
    const when = h("input", { type: "date", value: today, max: today });
    const note = h("input", { type: "text", maxlength: 300, placeholder: "e.g. Gave my CV to the manager, Sam" });
    const reference = h("input", { type: "text", maxlength: 120, placeholder: "Optional" });
    const save = h("button", { type: "button", class: "btn secondary", text: "Record my application" });
    save.addEventListener("click", () => withBusy(save, async () => {
      if (!when.value) throw new Error("Choose the day you applied.");
      if (when.value > today) throw new Error("The day you applied cannot be in the future.");
      // Today means now; an earlier day is recorded at noon that day (the day is what matters).
      const sentAt = when.value === today ? new Date() : new Date(`${when.value}T12:00:00`);
      const data = await api(`/api/jobs/${encodeURIComponent(job.job_id)}/submit`, { method: "POST", body: { outside: true, channel: channel.value, submitted_at: sentAt.toISOString(), confirmation_text: note.value, confirmation_number: reference.value, idempotency_key: `job:${job.job_id}` } });
      toast(data.submission.duplicate ? "This application was already recorded." : "Recorded. This job is now tracked as Applied.", "success");
      reload("apply");
    }));
    return h("details", { class: "card careers-outside-card", style: { marginTop: "14px" } },
      h("summary", { text: "Already applied another way? Record it" }),
      h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Sent your CV by email, handed it in at the shop, or applied before you used Monosage? Record it so it is tracked, reminded and counted in your job search log." }),
      h("div", { class: "form-grid" }, ui.field("How you applied", channel), ui.field("Day you applied", when), ui.field("Reference number", reference), ui.field("Note", note)),
      h("div", { class: "btn-row", style: { marginTop: "12px" } }, save),
    );
  }

  function renderSubmitCard(job, prep, reload) {
    const confirmationNumber = h("input", { type: "text", maxlength: 120, placeholder: "e.g. REQ-12345 or the email subject" });
    const confirmationText = h("textarea", { rows: 3, placeholder: "Paste the confirmation message or describe what you saw (optional)" });
    const evidence = h("input", { type: "file", accept: ".png,.jpg,.jpeg,.pdf,.eml,.txt" });
    const nowLocal = Aira.toLocalInput(new Date().toISOString());
    const when = h("input", { type: "datetime-local", value: nowLocal, max: nowLocal });
    // A job added by hand without a link has no page to open: the person applies the way the employer asked.
    const hasPage = job.has_posting !== false;
    const openBtn = h("button", { type: "button", class: "btn", text: hasPage ? "Open application & start applying ↗" : "I am applying now" });
    openBtn.addEventListener("click", () => withBusy(openBtn, async () => {
      const data = await api(`/api/jobs/${encodeURIComponent(job.job_id)}/apply`, { method: "POST", body: {} });
      if (hasPage) window.open(data.application_url, "_blank", "noopener");
      toast(hasPage ? "Application opened in a new tab. Record the confirmation when you finish." : "Marked as applying. Record it below when you have sent it.", "success");
      reload("apply");
    }));
    const copyRow = h("div", { class: "chips", style: { marginTop: "8px" } }, ...prep.fields.filter((f) => f.value && f.field_type !== "file").slice(0, 12).map((f) => h("button", { type: "button", class: "chip", title: `Copy ${f.label}`, text: `${f.label}: ${String(f.value).slice(0, 28)}`, onclick: async () => { await navigator.clipboard.writeText(String(f.value)); toast(`${f.label} copied.`, "success", { timeout: 1500 }); } })));
    const submitBtn = h("button", { type: "button", class: "btn", text: "Record submission" });
    submitBtn.addEventListener("click", () => withBusy(submitBtn, async () => {
      if (when.value && new Date(when.value).getTime() > Date.now() + 5 * 60 * 1000) throw new Error("The time you applied cannot be in the future. Check the date and the year.");
      const file = evidence.files[0];
      const body = { confirmation_number: confirmationNumber.value, confirmation_text: confirmationText.value, submitted_at: when.value ? new Date(when.value).toISOString() : "", idempotency_key: `job:${job.job_id}` };
      if (file) { if (file.size > 10 * 1024 * 1024) throw new Error("Evidence files must be under 10 MB."); body.evidence_base64 = await Aira.fileToBase64(file); body.evidence_filename = file.name; }
      const data = await api(`/api/jobs/${encodeURIComponent(job.job_id)}/submit`, { method: "POST", body });
      toast(data.submission.duplicate ? "This application was already recorded." : "Submission recorded. Tracking as Applied.", "success");
      reload("apply");
    }));
    return h("div", { class: "card", style: { marginTop: "14px" } },
      h("h3", { text: "Apply and capture confirmation" }),
      h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Open the application, use the copy chips to fill the form quickly, submit it yourself, then record the confirmation so the application is tracked and never duplicated." }),
      h("div", { class: "btn-row" }, openBtn, job.stage === "applying" ? ui.stage("applying") : null),
      copyRow,
      h("div", { class: "form-grid", style: { marginTop: "14px" } }, ui.field("Confirmation number or reference", confirmationNumber), ui.field("Submitted at", when), h("div", { class: "field wide" }, h("label", { for: "evidenceFile", text: "Confirmation evidence (screenshot, PDF, or email)" }), Object.assign(evidence, { id: "evidenceFile" }), h("span", { class: "hint", text: "Stored encrypted in Documents." })), h("div", { class: "field wide" }, h("label", { for: "confText", text: "Confirmation notes" }), Object.assign(confirmationText, { id: "confText" }))),
      h("div", { class: "btn-row", style: { marginTop: "12px" } }, submitBtn),
    );
  }

  // -------------------------------------------------------------------------
  // Applications page
  // -------------------------------------------------------------------------
  const GROUPS = [
    { id: "preparing", label: "Preparing", stages: ["needs_review", "ready_to_apply", "applying"] },
    { id: "applied", label: "Applied", stages: ["applied", "assessment"] },
    { id: "interviewing", label: "Interviewing", stages: ["recruiter_screen", "technical_interview", "manager_interview", "panel_interview", "final_interview"] },
    { id: "offers", label: "Offers", stages: ["offer", "accepted"] },
    { id: "closed", label: "Closed", stages: ["rejected", "withdrawn", "failed"] },
  ];

  const APP_TABS = [
    { id: "pipeline", label: "Pipeline", icon: "applications" },
    { id: "queue", label: "Queue & review", icon: "careers-check" },
    { id: "offers", label: "Offers", icon: "careers-coin" },
    { id: "email", label: "Email updates", icon: "careers-mail" },
  ];

  Aira.views.applications = async (main, route) => {
    const asked = route.query.tab || Aira.recall("applications.tab", "pipeline");
    const tab = APP_TABS.some((item) => item.id === asked) ? asked : "pipeline";
    Aira.remember("applications.tab", tab);
    Aira.careersFolders("applications", APP_TABS, tab);
    const content = h("div", {});
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Applications" }), h("p", { text: "Every status change is recorded with who, when, and why. Move cards only through allowed transitions." })), h("div", { class: "page-actions" }, h("button", { type: "button", class: "btn secondary sm", text: "Download job search log", title: "Every application as a spreadsheet (CSV): dates, employers, how you applied. Many unemployment offices ask for this.", onclick: (event) => withBusy(event.currentTarget, () => Aira.download("/api/jobs?format=csv&scope=applications", `aira-job-search-log-${new Date().toISOString().slice(0, 10)}.csv`)) }), h("a", { class: "btn secondary sm", href: "#/applications?tab=email", text: "Update from email" }))),
      Aira.careersStrip(ui.tabs(APP_TABS, tab, (id) => router.go(["applications"], { tab: id }, { replace: true }))),
      content,
    );
    if (route.path[1]) Aira.openJob(route.path[1], { tab: "apply" });
    const load = async () => {
      content.replaceChildren(ui.skeleton(5));
      try {
        if (tab === "pipeline") await renderPipeline(content);
        else if (tab === "queue") await renderQueue(content);
        else if (tab === "offers") await renderOffers(content);
        else await renderEmail(content, route);
      } catch (error) { content.replaceChildren(ui.error(error, load)); }
    };
    Aira.state.views.applicationsReload = load;
    await load();
  };

  async function renderPipeline(content) {
    // Only jobs in the pipeline are fetched, so a long list of discovered jobs cannot push applications off the
    // board. They come a page at a time (most recently updated first); each column counts every job in it.
    const PAGE = 200;
    let page = 1;
    let data = await api(`/api/jobs?view=pipeline&page_size=${PAGE}&page=1&sort=updated`);
    const jobs = [...data.jobs];
    const draw = () => {
      const counts = new Map((data.facets.stages || []).map((item) => [item.stage, item.count]));
      const board = h("div", { class: "kanban" });
      for (const group of GROUPS) {
        const items = jobs.filter((job) => group.stages.includes(job.stage));
        const total = Math.max(items.length, group.stages.reduce((sum, stage) => sum + (counts.get(stage) || 0), 0));
        const column = h("div", { class: "kanban-col" }, h("h3", {}, h("span", { text: group.label }), h("span", { class: "muted", text: String(total) })));
        const cards = h("div", { class: "cards" });
        if (!items.length) cards.append(h("div", { class: "muted", style: { padding: "12px", fontSize: "12.5px" }, text: total ? "Older ones are not shown yet. Choose Show more below." : "Nothing here yet." }));
        for (const job of items) cards.append(h("button", { type: "button", class: "kcard", onclick: () => Aira.openJob(job.job_id, { tab: "apply" }) }, h("strong", { text: job.title || job.job_id }), h("span", { text: job.company || "" }), h("div", { class: "meta" }, ui.stage(job.stage), ui.classification(job.match_classification), job.match_score != null ? h("span", { class: "badge", text: `${Math.round(job.match_score)}%` }) : null)));
        if (items.length && items.length < total) cards.append(h("div", { class: "muted careers-more-note", text: `${total - items.length} older not shown` }));
        column.append(cards);
        board.append(column);
      }
      const discovered = (data.facets.stages || []).filter((item) => ["discovered", "saved"].includes(item.stage)).reduce((sum, item) => sum + item.count, 0);
      const left = Math.max(0, data.total - jobs.length);
      const more = left ? h("button", { type: "button", class: "btn secondary", text: `Show more applications (${left} more)`, onclick: (event) => withBusy(event.currentTarget, async () => {
        page += 1;
        data = await api(`/api/jobs?view=pipeline&page_size=${PAGE}&page=${page}&sort=updated`);
        const known = new Set(jobs.map((job) => job.job_id));
        jobs.push(...data.jobs.filter((job) => !known.has(job.job_id)));
        draw();
      }) }) : null;
      const summary = h("p", { class: "muted", style: { marginTop: "10px", fontSize: "12.5px" }, text: `Showing ${jobs.length} of ${data.total} applications, most recently updated first. ${discovered} more jobs are discovered or saved and not yet in the pipeline.` });
      content.replaceChildren(...(jobs.length ? [board, summary, ...(more ? [h("div", { class: "btn-row", style: { marginTop: "10px" } }, more)] : [])] : [ui.empty("No applications in progress", `${discovered} discovered or saved jobs are waiting in Jobs. Prepare one to start the pipeline.`, h("a", { class: "btn", href: "#/jobs/recommended", text: "Pick a recommended job" }))]));
    };
    draw();
  }

  async function renderQueue(content) {
    const data = await api("/api/applications/queue");
    const budget = h("div", { class: "stats", style: { marginBottom: "16px" } }, h("div", { class: "stat accent" }, h("span", { class: "label", text: "Today's budget" }), h("span", { class: "value", text: `${data.submitted_today} / ${data.daily_limit}` }), h("span", { class: "sub", text: `${data.remaining_today} applications remaining today` })), h("div", { class: "stat" }, h("span", { class: "label", text: "Ready to apply" }), h("span", { class: "value", text: String(data.ready.length) })), h("div", { class: "stat" }, h("span", { class: "label", text: "Needs review" }), h("span", { class: "value", text: String(data.needs_review.length) })), h("div", { class: "stat" }, h("span", { class: "label", text: "In progress" }), h("span", { class: "value", text: String(data.in_progress.length) })));
    const section = (title, items, emptyText, buttonLabel) => h("div", { class: "panel", style: { marginBottom: "14px" } }, h("div", { class: "panel-head" }, h("h3", { text: title }), h("small", { class: "muted", text: String(items.length) })), items.length ? h("div", { class: "list" }, ...items.map((job) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: job.title || job.job_id }), h("span", { class: "sub", text: [job.company, job.prep_status ? `packet ${fmt.stage(job.prep_status)}` : "no packet", job.documents_ready ? "drafts ready" : "drafts missing"].filter(Boolean).join(" · ") }), h("div", { class: "meta" }, ui.stage(job.stage), ui.classification(job.match_classification))), h("div", { class: "aside" }, h("button", { type: "button", class: "btn secondary sm", text: buttonLabel, onclick: () => Aira.openJob(job.job_id, { tab: "apply" }) }))))) : h("div", { class: "empty", text: emptyText }));
    content.replaceChildren(
      budget,
      h("p", { class: "note info" }, `Automatic applications: ${data.auto_submit.paused ? "paused" : "available"}. ${data.auto_submit.reason} `, h("a", { href: "#/jobs/autopilot", text: "Open Auto-apply" })),
      section("Ready to apply", data.ready, "Approve a packet to see it here.", "Apply now"),
      section("Needs your review", data.needs_review, "Nothing is waiting for review.", "Review"),
      section("In progress", data.in_progress, "No application is open right now.", "Record confirmation"),
    );
  }

  // -------------------------------------------------------------------------
  // Offers: compare what employers offered and ask for more
  // -------------------------------------------------------------------------
  const OPEN_OFFER = ["considering", "negotiating"];
  const OFFER_STATUS_LABELS = { considering: "Thinking about it", negotiating: "Negotiating", accepted: "Accepted", declined: "Declined", expired: "Expired" };
  // A table cell that is a labelled card line on a phone (the label is real text, so it is translated too).
  const cell = (label, value, kind = "") => h("td", { class: kind }, h("span", { class: "careers-cell-label", text: label }), h("span", { class: "careers-cell-value", text: value }));

  async function renderOffers(content) {
    const data = await api("/api/jobs?view=offers&page_size=100&sort=updated");
    const comparison = data.comparison || { offers: [], expectation: {} };
    const money = Aira.careersMoney;
    const per = Aira.careersPer;
    const compared = (o) => {
      if (!OPEN_OFFER.includes(o.status) && o.status !== "accepted") return "No longer open";
      if (o.vs_best === null || o.vs_best === undefined) return "Other currency";
      if (o.best || o.vs_best === 0) return "—";
      return o.vs_best < 0 ? `${money(-o.vs_best, o.currency)} less` : `${money(o.vs_best, o.currency)} more`;
    };
    const withOffer = new Set(comparison.offers.map((o) => o.job_id));
    const waiting = data.jobs.filter((job) => !withOffer.has(job.job_id));
    const wanted = comparison.expectation || {};
    const intro = h("p", { class: "note info", text: wanted.min ? `Offers are compared with the pay you want: at least ${money(wanted.min, wanted.currency || comparison.currency || "USD")} a year (from your profile).` : "Add the pay you want to your profile (Settings > Profile) to see how each offer compares with it." });
    const parts = [intro];
    if (!comparison.offers.length) {
      parts.push(ui.empty("No offers recorded yet", waiting.length ? "Open a job below and record its offer to compare it here." : "When an employer makes you an offer, open the job and choose Record the offer.", h("a", { class: "btn", href: "#/applications?tab=pipeline", text: "Open the pipeline" })));
    } else {
      const best = comparison.offers.find((o) => o.best);
      if (best) parts.push(h("div", { class: "stats careers-offer-stats", style: { marginBottom: "14px" } },
        h("div", { class: "stat accent" }, h("span", { class: "label", text: "Best first-year pay" }), h("span", { class: "value", text: money(best.first_year_total, best.currency) }), h("span", { class: "sub", text: [best.title, best.company].filter(Boolean).join(" · ") })),
        h("div", { class: "stat" }, h("span", { class: "label", text: "Offers" }), h("span", { class: "value", text: String(comparison.offers.length) }), h("span", { class: "sub", text: `${comparison.offers.filter((o) => OPEN_OFFER.includes(o.status)).length} still open` })),
      ));
      const table = h("table", { class: "table careers-offer-table" },
        h("thead", {}, h("tr", {}, h("th", { text: "Job" }), h("th", { class: "num", text: "Base pay" }), h("th", { class: "num", text: "Pay a year" }), h("th", { class: "num", text: "First-year pay" }), h("th", { class: "num", text: "Compared with best" }), h("th", { text: "Your target" }), h("th", { text: "Status" }), h("th", { text: "" }))),
        h("tbody", {}, ...comparison.offers.map((o) => h("tr", { class: o.best ? "careers-offer-best" : "" },
          h("td", {}, h("strong", { text: o.title || "Job" }), h("div", { class: "muted", text: o.company || "" }), o.best ? h("span", { class: "badge stage applied", text: "Best offer" }) : null),
          cell("Base pay", `${money(o.base_salary, o.currency)} ${per[o.pay_period] || ""}`, "num"),
          cell("Pay a year", money(o.yearly_total, o.currency), "num"),
          cell("First-year pay", money(o.first_year_total, o.currency), "num"),
          cell("Compared with best", compared(o), "num"),
          cell("Your target", o.meets_expectation === null || o.meets_expectation === undefined ? "Not set" : o.meets_expectation ? "Meets it" : `${money(-o.vs_expectation, o.currency)} short`),
          cell("Status", OFFER_STATUS_LABELS[o.status] || fmt.title(o.status)),
          h("td", { class: "careers-offer-actions" }, h("div", { class: "btn-row" }, OPEN_OFFER.includes(o.status) ? h("button", { type: "button", class: "btn sm", text: "Ask for more", onclick: () => negotiate(o, comparison) }) : null, h("button", { type: "button", class: "btn ghost sm", text: "Open job", onclick: () => Aira.openJob(o.job_id) }))),
        ))),
      );
      parts.push(h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Compare offers" }), h("small", { class: "muted", text: "Pay a year = base + bonus + equity + benefits. First-year pay adds the sign-on payment." })), h("div", { class: "table-wrap" }, table)));
    }
    if (waiting.length) parts.push(h("div", { class: "panel", style: { marginTop: "14px" } }, h("div", { class: "panel-head" }, h("h3", { text: "Offers to write down" }), h("small", { class: "muted", text: String(waiting.length) })), h("div", { class: "list" }, ...waiting.map((job) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: job.title || job.job_id }), h("span", { class: "sub", text: job.company || "" }), h("div", { class: "meta" }, ui.stage(job.stage))), h("div", { class: "aside" }, h("button", { type: "button", class: "btn secondary sm", text: "Record the offer", onclick: async (event) => { await withBusy(event.currentTarget, async () => { const detail = (await api(`/api/jobs/${encodeURIComponent(job.job_id)}`)).job; Aira.offerForm(detail, () => renderOffers(content)); }); } })))))));
    content.replaceChildren(...parts);
  }

  function negotiate(offer, comparison) {
    const money = Aira.careersMoney;
    const per = Aira.careersPer;
    const ask = h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", id: "negotiateAsk", placeholder: "Leave empty for Monosage's suggestion" });
    const higher = comparison.offers.some((o) => o.job_id !== offer.job_id && o.currency === offer.currency && o.annual_base > offer.annual_base && !["declined", "expired"].includes(o.status));
    const mention = h("input", { type: "checkbox" });
    const subject = h("input", { type: "text", id: "negotiateSubject", maxlength: 300 });
    const message = h("textarea", { rows: 12, id: "negotiateBody" });
    const reasons = h("ul", { class: "inline-list" });
    const result = h("div", { hidden: true, style: { marginTop: "12px" } },
      h("h3", { text: "Why this amount" }), reasons,
      h("div", { class: "field" }, h("label", { for: "negotiateSubject", text: "Subject" }), subject),
      h("div", { class: "field" }, h("label", { for: "negotiateBody", text: "Email" }), message),
      h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Monosage does not send this. Read it, change anything you like, then send it from your own email." }),
      h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary sm", text: "Copy email", onclick: async () => { await navigator.clipboard.writeText(`${subject.value}\n\n${message.value}`); toast("Email copied.", "success"); } })),
    );
    const write = h("button", { type: "button", class: "btn", text: "Write the email" });
    write.addEventListener("click", () => withBusy(write, async () => {
      write.textContent = "Writing…";
      const data = await api(`/api/jobs/${encodeURIComponent(offer.job_id)}/generate`, { method: "POST", body: { kind: "negotiation", ask: ask.value, mention_other_offer: mention.checked } });
      const draft = data.draft;
      subject.value = draft.subject;
      message.value = draft.body;
      if (!ask.value) ask.value = String(draft.ask);
      reasons.replaceChildren(...draft.reasons.map((r) => h("li", { text: r })), h("li", { text: `Asking for ${money(draft.ask, draft.currency)} ${per[draft.pay_period] || ""}.` }));
      result.hidden = false;
      message.focus();
    }));
    const body = h("div", {},
      h("p", { class: "muted", style: { marginTop: "0" }, text: `${offer.title || "This job"}${offer.company ? ` at ${offer.company}` : ""} offered ${money(offer.base_salary, offer.currency)} ${per[offer.pay_period] || ""}. Most employers expect a polite counter-offer.` }),
      h("div", { class: "form-grid" }, ui.field(`Base pay to ask for (${offer.currency}, ${per[offer.pay_period] || "a year"})`, ask)),
      higher ? h("label", { class: "check", style: { marginTop: "8px" } }, mention, "Mention that I have a higher offer") : null,
      h("div", { class: "btn-row", style: { marginTop: "12px" } }, write),
      result,
    );
    Aira.modal({ title: "Ask for a better offer", body, wide: true, actions: [{ label: "Close", kind: "" }] });
    ask.focus();
  }

  // -------------------------------------------------------------------------
  // Email updates
  // -------------------------------------------------------------------------
  async function renderEmail(content, route) {
    const subject = h("input", { type: "text", maxlength: 500, placeholder: "Subject" });
    const sender = h("input", { type: "email", maxlength: 254, placeholder: "recruiter@company.com" });
    const body = h("textarea", { rows: 6, placeholder: "Paste the email body here. Only the parts needed for classification are kept." });
    const eml = h("input", { type: "file", accept: ".eml" });
    const importBtn = h("button", { type: "button", class: "btn", text: "Classify & import" });
    const form = h("div", { class: "card", style: { marginBottom: "16px" } },
      h("h3", { text: "Import an email" }),
      h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Monosage classifies interview invitations, assessments, confirmations, rejections, offers, and recruiter outreach, extracts explicit dates and links, and suggests an action you confirm. Mailbox sync is not connected in this build, so paste the message or upload an .eml file." }),
      h("div", { class: "form-grid" }, ui.field("Subject", subject), ui.field("From", sender), h("div", { class: "field wide" }, h("label", { for: "emailBody", text: "Body" }), Object.assign(body, { id: "emailBody" })), h("div", { class: "field" }, h("label", { for: "emlFile", text: "Or upload .eml" }), Object.assign(eml, { id: "emlFile" }))),
      h("div", { class: "btn-row", style: { marginTop: "12px" } }, importBtn),
    );
    const list = h("div", { class: "panel" });
    importBtn.addEventListener("click", () => withBusy(importBtn, async () => {
      const payload = {};
      const file = eml.files[0];
      if (file) { payload.eml_base64 = await Aira.fileToBase64(file); payload.filename = file.name; }
      else { payload.subject = subject.value; payload.body = body.value; payload.sender = sender.value; }
      if (route.query.job) payload.job_id = route.query.job;
      const data = await api("/api/email/import", { method: "POST", body: payload });
      toast(data.duplicate ? "This email was already imported." : `Classified as ${fmt.stage(data.message.category)} (${Math.round(data.message.confidence * 100)}% confidence).${data.auto_applied ? " Action applied automatically." : ""}`, "success");
      subject.value = ""; body.value = ""; sender.value = ""; eml.value = "";
      loadList();
    }));
    async function loadList() {
      list.replaceChildren(ui.skeleton(3));
      const data = await api("/api/email?limit=100");
      const rows = h("div", { class: "list" });
      if (!data.messages.length) rows.append(ui.empty("No emails yet", "Import one above to detect interviews and status changes."));
      for (const message of data.messages) rows.append(emailRow(message, data.categories, loadList));
      list.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: "Classified emails" }), h("small", { class: "muted", text: `Auto-apply ${data.auto_apply ? "on" : "off"} · threshold ${Math.round(data.threshold * 100)}%` })), rows);
    }
    content.replaceChildren(form, list);
    await loadList();
  }

  function emailRow(message, categories, refresh) {
    const action = message.suggested_action || {};
    const details = message.details || {};
    const extracted = [details.company && `Company: ${details.company}`, details.role && `Role: ${details.role}`, details.start_at ? `When: ${fmt.datetime(details.start_at, details.timezone && details.timezone.includes("/") ? details.timezone : undefined)}` : details.date && `Date: ${details.date}${details.time ? " " + details.time : ""}${details.timezone ? " " + details.timezone : ""}`, details.meeting_link && `Link: ${details.meeting_platform || "meeting"}`, details.assessment_deadline && `Deadline: ${details.assessment_deadline}`, details.recruiter_name && `From: ${details.recruiter_name}`, details.application_id && `Ref: ${details.application_id}`].filter(Boolean);
    const jobSelect = Aira.jobPicker(message.job_id || "", { placeholder: "Which job is this about?" });
    jobSelect.classList.add("careers-email-job");
    const categorySelect = ui.select(categories.map((c) => [c, fmt.title(fmt.stage(c))]), message.category, { "aria-label": "Correct category" });
    categorySelect.addEventListener("change", async () => { try { await api(`/api/email/${message.message_id}`, { method: "PATCH", body: { category: categorySelect.value } }); toast("Category corrected.", "success"); refresh(); } catch (error) { Aira.reportError(error); } });
    const actionText = action.kind && action.kind !== "none" ? `Suggested: ${fmt.stage(action.kind)}${action.stage ? ` → ${fmt.stage(action.stage)}` : ""}${action.requires_confirmation ? " (needs your confirmation)" : ""}` : "No action suggested.";
    const applyBtn = message.status === "pending" && action.kind && action.kind !== "none" ? h("button", { type: "button", class: "btn sm", text: "Apply action", onclick: (event) => withBusy(event.currentTarget, async () => { const data = await api(`/api/email/${message.message_id}/apply`, { method: "POST", body: { confirm: true, job_id: jobSelect.jobId() } }); toast(`Applied: ${fmt.stage(data.applied.kind)}.`, "success"); refresh(); }) }) : null;
    const dismissBtn = message.status === "pending" ? h("button", { type: "button", class: "btn ghost sm", text: "Dismiss", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/email/${message.message_id}`, { method: "PATCH", body: { status: "dismissed" } }); refresh(); }) }) : null;
    const deleteBtn = h("button", { type: "button", class: "btn ghost sm danger", text: "Delete", onclick: async (event) => { if (await Aira.confirm({ title: "Delete this email record?", message: "The stored excerpt and classification are removed.", confirmLabel: "Delete", danger: true })) await withBusy(event.currentTarget, async () => { await api(`/api/email/${message.message_id}`, { method: "DELETE" }); refresh(); }); } });
    return h("div", { class: "row" },
      h("div", { style: { minWidth: "0" } },
        h("span", { class: "title", text: message.subject || "(no subject)" }),
        h("span", { class: "sub", text: `${message.sender_name || message.sender_email || "unknown sender"} · ${fmt.ago(message.created_at)}${message.job_title ? ` · linked to ${message.job_title}` : ""}` }),
        h("div", { class: "meta" }, h("span", { class: `badge ${message.status === "applied" ? "stage applied" : message.status === "dismissed" ? "" : "stage needs_review"}`, text: fmt.stage(message.status) }), h("span", { class: "badge", text: `${fmt.title(fmt.stage(message.category))} · ${Math.round(message.confidence * 100)}%` }), ...(message.signals || []).slice(0, 3).map((s) => h("span", { class: "chip", text: s }))),
        extracted.length ? h("div", { class: "muted", style: { fontSize: "12.5px", marginTop: "6px" }, text: extracted.join(" · ") }) : null,
        h("div", { class: "muted", style: { fontSize: "12.5px", marginTop: "4px" }, text: actionText }),
        h("div", { class: "btn-row", style: { marginTop: "8px" } }, message.status === "pending" ? jobSelect : null, categorySelect, applyBtn, dismissBtn, deleteBtn),
      ),
      h("div", { class: "aside" }, message.job_id ? h("button", { type: "button", class: "btn ghost sm", text: "Open job", onclick: () => Aira.openJob(message.job_id, { tab: "history" }) }) : null),
    );
  }

  // -------------------------------------------------------------------------
  // Dashboard
  // -------------------------------------------------------------------------
  Aira.views.dashboard = async (main) => {
    const [data, activity] = await Promise.all([api("/api/dashboard"), api("/api/activity?limit=12")]);
    Aira.state.dashboard = data;
    Aira.renderNav({ applications: data.counts.needs_review + data.counts.ready || 0, interviews: data.upcoming_interviews.length || 0 });
    const c = data.counts;
    const hour = new Date().getHours();
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const checklist = h("div", { class: "checklist" },
      checkItem(data.profile.has_resume, "Master resume saved", "#/settings/resume"),
      checkItem(data.profile.ready, `Profile ${data.profile.percent}% complete`, "#/settings/profile"),
      checkItem(data.linkedin && data.linkedin.state === "connected", "LinkedIn connected", "#/settings/integrations"),
      checkItem(data.ollama, "Ollama running for reviews", "#/settings/integrations"),
    );
    const hero = h("section", { class: "hero" }, h("div", {}, h("h1", { text: `${greeting}.` }), h("p", { text: c.ready ? `${fmt.plural(c.ready, "application")} ready to submit and ${fmt.plural(c.needs_review, "item")} waiting for review.` : c.recommended ? `${fmt.plural(c.recommended, "recommended job")} waiting. Prepare one today.` : "Search jobs across LinkedIn, Google Jobs, and company portals to start your pipeline." }), h("div", { class: "btn-row", style: { marginTop: "14px" } }, h("a", { class: "btn secondary", href: "#/jobs/recommended", text: "Recommended jobs" }), h("a", { class: "btn secondary", href: "#/applications?tab=queue", text: "Review queue" }))), checklist);
    const stats = h("div", { class: "stats", style: { marginBottom: "18px" } },
      statLink("Recommended", c.recommended, "#/jobs/recommended", "jobs worth a look"),
      statLink("Needs review", c.needs_review, "#/applications?tab=queue", "packets or drafts"),
      statLink("Ready to apply", c.ready, "#/applications?tab=queue", `${data.queue.remaining_today} left today`),
      statLink("Applied", c.applied, "#/applications", "tracked applications"),
      statLink("Interviewing", c.interviewing, "#/interviews", "active conversations"),
      statLink("Offers", c.offers, "#/applications", "offers received"),
    );
    const today = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Today" }), h("a", { class: "btn ghost sm", href: "#/interviews", text: "All interviews" })),
      h("div", { class: "list" },
        ...data.upcoming_interviews.map((i) => h("a", { class: "row", href: `#/interviews/${i.interview_id}` }, h("div", {}, h("span", { class: "title", text: `${fmt.title(fmt.stage(i.interview_type))} · ${i.company || i.role || "Interview"}` }), h("span", { class: "sub", text: fmt.datetime(i.start_at, i.timezone) })), i.meeting_link ? h("span", { class: "badge", text: "Link" }) : null)),
        ...data.tasks.map((t) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: t.title }), h("span", { class: "sub", text: `${fmt.stage(t.kind)}${t.due_at ? ` · due ${fmt.datetime(t.due_at)}` : ""}${t.job_title ? ` · ${t.job_title}` : ""}` })), h("button", { type: "button", class: "btn ghost sm", text: "Done", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/tasks/${t.task_id}`, { method: "PATCH", body: { status: "done" } }); router.render(); }) }))),
        !data.upcoming_interviews.length && !data.tasks.length ? h("div", { class: "empty", text: "No interviews or tasks due. Nice." }) : null,
      ));
    const recent = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Recent jobs" }), h("a", { class: "btn ghost sm", href: "#/jobs/all", text: "All jobs" })), h("div", { class: "list" }, ...(data.recent_jobs.length ? data.recent_jobs.map((j) => h("button", { type: "button", class: "row", onclick: () => Aira.openJob(j.job_id) }, h("div", {}, h("span", { class: "title", text: j.title }), h("span", { class: "sub", text: j.company || "" }), h("div", { class: "meta" }, ui.stage(j.stage), ui.classification(j.match_classification))), ui.score(j.match_score))) : [ui.empty("No jobs yet", "Search or import a job to get started.", h("a", { class: "btn", href: "#/jobs/all", text: "Find jobs" }))])));
    const notices = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Updates" }), h("button", { type: "button", class: "btn ghost sm", text: "Mark read", onclick: async () => { await api("/api/notifications/read", { method: "POST", body: {} }); router.render(); } })), h("div", { class: "list" }, ...(data.notifications.length ? data.notifications.map(notificationRow) : [h("div", { class: "empty", text: c.pending_emails ? `${fmt.plural(c.pending_emails, "email")} waiting for your decision.` : "You're all caught up." })])));
    const feed = h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", { text: "Automation & activity" }), h("a", { class: "btn ghost sm", href: "#/settings/automation", text: "Schedules" })), h("div", { class: "list" }, ...(activity.activity.length ? activity.activity.map((a) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: fmt.title(fmt.stage(a.operation)) }), h("span", { class: "sub", text: [a.target, a.detail].filter(Boolean).join(" · ") })), h("div", { class: "aside" }, h("span", { class: `badge ${a.status === "ok" ? "stage applied" : "stage failed"}`, text: a.status === "ok" ? "Done" : "Failed" }), h("small", { class: "muted", text: `${a.source === "schedule" ? "Schedule" : "You"} · ${fmt.ago(a.at)}` })))) : [h("div", { class: "empty", text: "Searches, reads, and scheduled runs appear here." })])));
    // The API and Careers widgets load themselves after this page has drawn (views-widgets.js).
    const widgets = Aira.dashboardWidgets ? Aira.dashboardWidgets() : [];
    main.replaceChildren(hero, stats, h("div", { class: "grid cols-2" }, today, recent, notices, feed), ...(widgets.length ? [h("div", { class: "grid cols-2", style: { marginTop: "16px", alignItems: "start" } }, ...widgets)] : []));
  };
  // A notification that carries an in-app link (monitor changes, job alerts, new messages) opens it and is marked read.
  function notificationRow(n) {
    const inner = [h("div", {}, h("span", { class: "title", text: n.title }), h("span", { class: "sub", text: n.body })), h("small", { class: "muted", text: fmt.ago(n.at) })];
    if (!n.link || !n.link.startsWith("#/")) return h("div", { class: "row" }, ...inner);
    return h("a", { class: "row", href: n.link, title: "Open", onclick: () => { api("/api/notifications/read", { method: "POST", body: { ids: [n.notification_id] }, silent: true }).catch(() => {}); } }, ...inner);
  }
  function checkItem(done, label, href) { return h("a", { class: done ? "done" : "", href }, h("i", { text: done ? "✓" : "" }), h("span", { text: label })); }
  function statLink(label, value, href, sub) { return h("a", { class: "stat", href }, h("span", { class: "label", text: label }), h("span", { class: "value", text: String(value || 0) }), h("span", { class: "sub", text: sub })); }
})();

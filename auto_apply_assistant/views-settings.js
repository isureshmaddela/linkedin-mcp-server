/* Aira — Settings: profile, resume, standard answers, integrations, automation, data */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy, router } = Aira;
  const TABS = [["profile", "Profile"], ["resume", "Resume"], ["answers", "Standard answers"], ["integrations", "Integrations & sources"], ["automation", "Automation"], ["data", "Data & privacy"]];

  Aira.views.settings = async (main, route) => {
    const tab = route.path[1] || "profile";
    const content = h("div", {});
    main.replaceChildren(
      h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Settings" }), h("p", { text: "Your profile, resume, confirmed answers, connections, automation policy, and data controls." }))),
      h("nav", { class: "subnav", "aria-label": "Settings sections" }, ...TABS.map(([id, label]) => h("a", { href: `#/settings/${id}`, "aria-current": id === tab ? "page" : "false", text: label }))),
      content,
    );
    const renderers = { profile: renderProfile, resume: renderResume, answers: renderAnswers, integrations: renderIntegrations, automation: renderAutomation, data: renderData };
    const renderer = renderers[tab] || renderProfile;
    content.replaceChildren(ui.skeleton(5));
    try { await renderer(content); } catch (error) { content.replaceChildren(ui.error(error, () => router.render())); }
  };

  async function loadProfile() { Aira.state.profile = await api("/api/profile"); return Aira.state.profile; }
  async function saveProfile(partial) {
    const p = Aira.state.profile || (await loadProfile());
    const body = { resume: p.resume, profile_url: p.profile_url, resume_name: p.resume_name, application_profile: p.candidate_profile, matching_preferences: p.matching_preferences, ...partial };
    Aira.state.profile = await api("/api/profile", { method: "POST", body });
    return Aira.state.profile;
  }

  // -------------------------------------------------------------------------
  async function renderProfile(content) {
    const p = await loadProfile();
    const inputs = {};
    const groups = [
      ["Identity", ["first_name", "last_name", "preferred_name", "full_name"]],
      ["Contact", ["email", "phone", "address", "city", "state", "postal_code", "country", "location"]],
      ["Links", ["linkedin_url", "github_url", "portfolio_url"]],
      ["Career", ["current_company", "current_title", "years_of_experience", "career_profile", "target_seniority", "availability_date", "notice_period"]],
      ["Work authorization (sensitive)", ["work_authorization", "sponsorship_required", "visa_status", "citizenship", "security_clearance"]],
      ["Compensation & relocation (sensitive)", ["salary_expectation_min", "salary_expectation_max", "salary_currency", "relocation"]],
    ];
    const fieldMap = Object.fromEntries(p.profile_fields.map((f) => [f.name, f]));
    const sections = groups.map(([title, names]) => h("fieldset", {}, h("legend", { text: title }), title.includes("sensitive") ? h("div", { class: "sensitive-note" }, h("span", { text: "⚠" }), h("span", { text: "Optional. These values stay on this computer, are never sent to the local model, and are only used in an application after you confirm the matching standard answer." })) : null, h("div", { class: "form-grid" }, ...names.map((name) => { const f = fieldMap[name]; let input; if (f.type === "select") input = ui.select([["", "Not specified"], ...f.options.map((o) => [o, name === "career_profile" ? p.career_profiles[o] || o : fmt.title(fmt.stage(o))])], p.candidate_profile[name] || ""); else input = h("input", { type: f.type === "email" ? "email" : f.type === "url" ? "url" : f.type === "number" ? "number" : f.type === "date" ? "date" : f.type === "phone" ? "tel" : "text", value: p.candidate_profile[name] || "", maxlength: f.max_length }); inputs[name] = input; return ui.field(f.label, input, f.help); }))));
    const locations = h("input", { type: "text", value: (p.matching_preferences.locations || []).join("; "), placeholder: "Seattle, WA; Remote" });
    const skills = h("textarea", { rows: 3, placeholder: "Python; AWS; Enterprise architecture" }, (p.matching_preferences.verified_skills || []).join("; "));
    const workplace = ["remote", "hybrid", "on_site"].map((w) => { const box = h("input", { type: "checkbox", value: w, checked: (p.matching_preferences.workplace_types || []).includes(w) }); return h("label", { class: "check" }, box, fmt.title(fmt.stage(w))); });
    const prefs = h("fieldset", {}, h("legend", { text: "Matching preferences" }), h("div", { class: "form-grid" }, ui.field("Preferred locations", locations, "Separate with semicolons. Used for explicit location alignment only."), h("div", { class: "field wide" }, h("label", { for: "skillsInput", text: "Skills you confirm you have" }), Object.assign(skills, { id: "skillsInput" }), h("span", { class: "hint", text: "Counted as verified evidence in scoring. Separate with semicolons." })), h("div", { class: "field" }, h("span", { class: "label", text: "Preferred workplace" }), h("div", { class: "btn-row" }, ...workplace))));
    const completeness = h("div", { class: "note", text: p.completeness.ready ? `Profile ${p.completeness.percent}% complete. Ready for application preparation.` : `Profile ${p.completeness.percent}% complete. Required for applications: ${p.completeness.missing_required.join(", ")}.` });
    const save = h("button", { type: "button", class: "btn", text: "Save profile" });
    save.addEventListener("click", () => withBusy(save, async () => {
      const application_profile = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value]));
      const matching_preferences = { locations: locations.value.split(/[;\n]/).map((s) => s.trim()).filter(Boolean), workplace_types: workplace.map((l) => l.querySelector("input")).filter((b) => b.checked).map((b) => b.value), verified_skills: skills.value.split(/[;\n]/).map((s) => s.trim()).filter(Boolean) };
      const saved = await saveProfile({ application_profile, matching_preferences, profile_url: application_profile.linkedin_url || "" });
      toast(saved.completeness.ready ? "Profile saved. Jobs are being rescored." : `Profile saved. Still missing: ${saved.completeness.missing_required.join(", ")}.`, "success");
      router.render();
    }));
    content.replaceChildren(completeness, h("div", { style: { marginTop: "14px" } }, ...sections, prefs), h("div", { class: "btn-row" }, save));
  }

  // -------------------------------------------------------------------------
  async function renderResume(content) {
    const p = await loadProfile();
    const text = h("textarea", { rows: 18, placeholder: "Upload a file or paste your resume. Review the extracted text before saving.", style: { font: "13px/1.6 var(--font)" } }, p.resume || "");
    const file = h("input", { type: "file", accept: ".pdf,.docx,.txt,.md" });
    const count = h("p", { class: "muted", style: { fontSize: "12px" } });
    let pendingName = "";
    const updateCount = () => { const length = text.value.trim().length; count.textContent = `${length.toLocaleString()} characters${text.value.trim() !== (p.resume || "").trim() ? " · unsaved changes" : ""}${length > 0 && length < 80 ? " · too short to use" : ""}`; };
    text.addEventListener("input", updateCount);
    updateCount();
    file.addEventListener("change", async () => {
      const chosen = file.files[0];
      if (!chosen) return;
      if (chosen.size > 700000) { toast("Resume files must be under 700 KB.", "error"); return; }
      try { const data = await api("/api/resume/extract", { method: "POST", body: { filename: chosen.name, content_base64: await Aira.fileToBase64(chosen) } }); text.value = data.text; pendingName = data.name; updateCount(); toast("Text extracted. Review it, then save.", "success"); }
      catch (error) { Aira.reportError(error); }
    });
    const save = h("button", { type: "button", class: "btn", text: "Save master resume" });
    save.addEventListener("click", () => withBusy(save, async () => {
      const saved = await saveProfile({ resume: text.value, resume_name: pendingName || p.resume_name });
      if (text.value.trim()) {
        try { await api("/api/documents", { method: "POST", body: { filename: `${(pendingName || saved.resume_name || "master-resume").replace(/\.[^.]+$/, "")}.md`, content_base64: btoa(unescape(encodeURIComponent(text.value))), kind: "master_resume", title: "Master resume" } }); } catch (error) { console.warn("Master resume snapshot skipped", error); }
      }
      file.value = ""; pendingName = "";
      toast("Master resume saved. Earlier drafts will need regenerating before approval.", "success");
      router.render();
    }));
    const status = h("div", { class: "note", text: p.resume_hash ? `Saved${p.resume_name ? ` as ${p.resume_name}` : ""}${p.resume_updated_at ? `, updated ${fmt.ago(p.resume_updated_at)}` : ""}. Version ${p.resume_hash}. Every review, draft, and schedule uses this text.` : "No resume saved yet. Add one so Aira can score jobs, review fit, and draft documents." });
    content.replaceChildren(status, h("div", { class: "grid sidebar-right", style: { marginTop: "14px" } }, h("div", { class: "card" }, ui.field("Upload a file (PDF, DOCX, TXT, or MD · up to 700 KB)", file), h("div", { class: "field", style: { marginTop: "12px" } }, h("label", { for: "resumeText", text: "Resume text" }), Object.assign(text, { id: "resumeText" })), count, h("div", { class: "btn-row" }, save)), h("div", { class: "card" }, h("h3", { text: "How your resume is used" }), h("ul", { class: "inline-list" }, h("li", { text: "Scoring finds exact skill evidence in this text; it never infers skills you did not write." }), h("li", { text: "Fit reviews and tailored drafts are generated by your local Ollama model only." }), h("li", { text: "Each save stores an encrypted, versioned copy in Documents." }), h("li", { text: "Approved drafts are tied to the resume version they came from; a new master resume marks older drafts as outdated." })))));
  }

  // -------------------------------------------------------------------------
  async function renderAnswers(content) {
    const data = await api("/api/profile/answers");
    const existing = Object.fromEntries(data.answers.map((a) => [a.canonical_field, a]));
    const sensitiveFirst = [...data.fields].sort((a, b) => (a.sensitivity === "sensitive" ? -1 : 1) - (b.sensitivity === "sensitive" ? -1 : 1) || a.label.localeCompare(b.label));
    const rows = sensitiveFirst.filter((f) => f.type !== "file").map((f) => {
      const current = existing[f.name] || {};
      const input = f.options && f.options.length ? ui.select([["", "Not set"], ...f.options.map((o) => [o, o])], current.value || "", { "aria-label": f.label }) : h("input", { type: "text", value: current.value || "", maxlength: 2000, "aria-label": f.label });
      const confirmed = h("input", { type: "checkbox", checked: Boolean(current.confirmed), "aria-label": `${f.label}: confirmed` });
      const save = h("button", { type: "button", class: "btn secondary sm", text: "Save" });
      // Clear forgets the stored answer (DELETE), so Aira stops prefilling it; it only shows once an answer exists.
      const clear = h("button", { type: "button", class: "btn ghost sm danger", text: "Clear", hidden: !existing[f.name] });
      save.addEventListener("click", () => withBusy(save, async () => { await api("/api/profile/answers", { method: "POST", body: { canonical_field: f.name, value: input.value, confirmed: confirmed.checked, sensitivity: f.sensitivity } }); clear.hidden = false; toast(`${f.label} saved.`, "success"); }));
      clear.addEventListener("click", async () => {
        if (!(await Aira.confirm({ title: `Clear ${f.label}?`, message: "Aira forgets this answer and stops prefilling it in application packets.", confirmLabel: "Clear", danger: true }))) return;
        await withBusy(clear, async () => { await api(`/api/profile/answers/${encodeURIComponent(f.name)}`, { method: "DELETE" }); input.value = ""; confirmed.checked = false; clear.hidden = true; toast(`${f.label} cleared.`, "success"); });
      });
      return h("div", { class: "packet-field" }, h("div", { class: "q" }, h("span", { text: f.label }), h("small", { text: `${f.sensitivity}${f.requires_confirmation ? " · confirmation required before use" : ""}` })), input, h("div", { class: "btn-row" }, h("label", { class: "check", title: "Allow Aira to prefill this answer in application packets" }, confirmed, "Confirmed"), save, clear));
    });
    content.replaceChildren(h("div", { class: "sensitive-note" }, h("span", { text: "⚠" }), h("span", { text: "Aira prefills an application answer only when you have confirmed it here. Sensitive answers (work authorization, visa, salary, demographics, legal attestations) are never guessed and never sent to the local model." })), h("div", { class: "card" }, ...rows));
  }

  // -------------------------------------------------------------------------
  async function renderIntegrations(content) {
    const [sources, linkedin, status, settings, serp] = await Promise.all([api("/api/sources"), api("/api/linkedin/status"), api("/api/status"), api("/api/settings"), api("/api/airaapi/overview").catch(() => null)]);
    Aira.state.sources = sources.sources;
    const connectBtn = h("button", { type: "button", class: "btn", text: linkedin.state === "connected" ? "LinkedIn connected" : linkedin.state === "login" ? "Waiting for sign-in…" : "Connect LinkedIn", disabled: ["connected", "login", "starting"].includes(linkedin.state) });
    connectBtn.addEventListener("click", () => withBusy(connectBtn, async () => { const result = await api("/api/linkedin/connect", { method: "POST", body: {} }); toast(result.state === "login" ? "A LinkedIn sign-in window opened. Complete it in that browser." : "Resuming your saved LinkedIn session.", "success"); setTimeout(() => router.render(), 3000); }));
    const restartBtn = h("button", { type: "button", class: "btn secondary sm", text: "Restart LinkedIn session" });
    restartBtn.addEventListener("click", () => withBusy(restartBtn, async () => { await api("/api/linkedin/restart", { method: "POST", body: {} }); toast("Restarting your LinkedIn session. It is ready in a few seconds.", "success"); setTimeout(() => router.render(), 4000); }));
    const freshBtn = h("button", { type: "button", class: "btn ghost sm", text: "Sign in again" });
    freshBtn.addEventListener("click", async () => { if (!(await Aira.confirm({ title: "Sign in to LinkedIn again?", message: "Use this if LinkedIn signed you out. Aira stops the current session and opens the LinkedIn sign-in window.", confirmLabel: "Open sign-in" }))) return; await withBusy(freshBtn, async () => { await api("/api/linkedin/connect", { method: "POST", body: { fresh_login: true } }); toast("Complete sign-in in the LinkedIn window.", "success"); setTimeout(() => router.render(), 3000); }); });
    const model = h("input", { type: "text", value: settings.settings.default_model, maxlength: 100 });
    const saveModel = h("button", { type: "button", class: "btn secondary sm", text: "Save model" });
    saveModel.addEventListener("click", () => withBusy(saveModel, async () => { const data = await api("/api/settings", { method: "POST", body: { default_model: model.value } }); Aira.state.settings = data.settings; toast("Default model saved.", "success"); }));
    const showBrowser = h("input", { type: "checkbox", checked: settings.settings.linkedin_show_browser === "1" });
    showBrowser.addEventListener("change", async () => {
      const previous = !showBrowser.checked;
      showBrowser.disabled = true;
      try {
        const data = await api("/api/settings", { method: "POST", body: { linkedin_show_browser: showBrowser.checked } });
        Aira.state.settings = data.settings;
        // Only a running session can be restarted; without one the preference applies when LinkedIn is connected.
        if (linkedin.state === "connected") {
          await api("/api/linkedin/restart", { method: "POST", body: {} });
          toast(showBrowser.checked ? "Restarting the LinkedIn session in a visible window. You will see each conversation open and each reply typed." : "Restarting the LinkedIn session in the background (headless).", "success", { timeout: 8000 });
          Aira.refreshStatus();
        } else {
          toast(showBrowser.checked ? "Saved. The LinkedIn browser will be visible once you connect." : "Saved. The LinkedIn browser will run in the background once you connect.", "success");
        }
      } catch (error) {
        Aira.reportError(error);
        showBrowser.checked = previous;
        // Keep the saved preference in step with what the box shows.
        api("/api/settings", { method: "POST", body: { linkedin_show_browser: previous }, silent: true }).then((data) => { Aira.state.settings = data.settings; }).catch(() => {});
      } finally { showBrowser.disabled = false; }
    });
    const browserRow = h("label", { class: "check", style: { marginTop: "10px" }, title: "Restarts the LinkedIn session so you can watch it work" }, showBrowser, "Show the LinkedIn browser while Aira works (off by default; Aira runs it in the background)");
    const cards = h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h3", { text: "LinkedIn" }), h("p", { class: "muted", text: linkedin.message }), h("div", { class: "btn-row" }, connectBtn, linkedin.state === "connected" ? restartBtn : null, freshBtn), browserRow, h("p", { class: "muted", style: { fontSize: "12px", marginTop: "8px" }, text: "Aira opens LinkedIn in an isolated browser profile for this account. Sign in there; Aira never sees your LinkedIn password and never bypasses verification steps. Restart the session after updating Aira so new features such as sending replies are available." })),
      h("div", { class: "card" }, h("h3", { text: "Ollama (local AI)" }), h("p", { class: "muted", text: status.ollama ? "Reachable. Fit reviews, drafts, and reply suggestions run locally." : "Not reachable. Start Ollama and pull a chat model (for example qwen3.5:4b)." }), h("div", { class: "btn-row" }, h("div", { class: "field", style: { minWidth: "220px" } }, h("label", { for: "defaultModel", text: "Default model" }), Object.assign(model, { id: "defaultModel" })), saveModel)),
      googleCard(serp, status),
      h("div", { class: "card" }, h("h3", { text: "Email & calendar" }), h("p", { class: "muted", text: "Mailbox sync (Gmail/Outlook OAuth) is not connected in this build. Import emails by paste or .eml, and add interviews to any calendar with .ics downloads or one-click links. The connector interface is in place for a future provider." })),
    );
    const table = h("div", { class: "panel", style: { marginTop: "16px" } }, h("div", { class: "panel-head" }, h("h3", { text: "Job sources" }), h("small", { class: "muted", text: `${sources.sources.filter((s) => s.status === "available").length} available` })), h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Source" }), h("th", { text: "Status" }), h("th", { text: "Access" }), h("th", { text: "Capabilities" }), h("th", { text: "Last run" }))), h("tbody", {}, ...sources.sources.map((s) => h("tr", {}, h("td", {}, h("strong", { text: s.label }), h("div", { class: "muted", style: { fontSize: "12px" }, text: s.identifier_label ? `${s.identifier_label}: ${s.identifier_help}` : "" })), h("td", {}, h("span", { class: `badge ${s.status === "available" ? (s.connected ? "stage applied" : "stage needs_review") : ""}`, text: s.status === "available" ? (s.connected ? "Connected" : "Needs setup") : "Planned" })), h("td", { text: s.status === "available" ? s.access_method : s.reason }), h("td", { text: Object.entries(s.capabilities).filter(([, v]) => v).map(([k]) => fmt.stage(k)).join(", ") || "—" }), h("td", { text: s.last_run ? `${s.last_run.status} · ${s.last_run.found} found · ${fmt.ago(s.last_run.finished_at)}` : "—" })))))));
    content.replaceChildren(cards, table);
  }

  // The SerpApi key behind Google Jobs. Its state comes from the Aira API overview (the key may live in the app or in
  // SERPAPI_API_KEY); the key itself is never shown. Managing it happens in Aira API > Account.
  function googleCard(serp, status) {
    const manage = (text) => h("a", { class: `btn${text === "Manage key" ? " secondary" : ""} sm`, href: "#/airaapi?tab=account", text });
    const configured = serp ? serp.configured : Boolean(status.google_jobs);
    let state = "No key";
    let badge = "stage needs_review";
    let detail = "No SerpApi key is connected. Paste one in Aira API > Account (it is checked with SerpApi, then stored encrypted on this computer) or set SERPAPI_API_KEY in .env.";
    if (configured && serp) {
      const account = serp.account;
      const origin = serp.key && serp.key.source === "env" ? "set by SERPAPI_API_KEY in the environment" : "stored encrypted in Aira";
      if (account) {
        state = "Connected"; badge = "stage applied";
        detail = `Key ending ${serp.key.last4 || "····"}, ${origin}. ${account.plan_name || "Plan"}: ${Number(account.total_searches_left || 0).toLocaleString()} searches left this month. Jobs-tab searches, Careers and the local /v1 endpoint share it, and a repeated search inside the cache window costs nothing.`;
      } else {
        state = "Check key"; badge = "stage failed";
        detail = `Key ending ${serp.key.last4 || "····"}, ${origin}. SerpApi could not confirm it right now${serp.account_error ? `: ${serp.account_error}` : "."}`;
      }
    } else if (configured) {
      state = "Connected"; badge = "stage applied";
      detail = "A SerpApi key is configured. Searches use the documented structured API.";
    } else if (serp && serp.key_error) {
      state = "Key unusable"; badge = "stage failed";
      detail = serp.key_error;
    }
    return h("div", { class: "card" },
      h("div", { class: "btn-row", style: { justifyContent: "space-between" } }, h("h3", { text: "Google Jobs and Aira API" }), h("span", { class: `badge ${badge}`, text: state })),
      h("p", { class: "muted", text: detail }),
      h("div", { class: "btn-row" }, manage(configured ? "Manage key" : "Connect key"), h("a", { class: "btn ghost sm", href: "#/airaapi", text: "Open Aira API" }), h("a", { class: "btn ghost sm", href: "#/careers", text: "Open Careers" })));
  }

  // -------------------------------------------------------------------------
  async function renderAutomation(content) {
    const [automation, settings, queue] = await Promise.all([api("/api/automation"), api("/api/settings"), api("/api/applications/queue")]);
    const s = settings.settings;
    const limit = h("input", { type: "number", min: 0, max: 100, value: s.daily_application_limit });
    const threshold = h("input", { type: "number", min: 0.5, max: 1, step: 0.05, value: s.email_confidence_threshold });
    const autoEmail = h("input", { type: "checkbox", checked: s.email_auto_apply === "1" });
    const timezone = h("input", { type: "text", value: s.timezone, list: "tzlist" });
    const messageLimit = h("input", { type: "number", min: 0, max: 100, value: s.daily_message_limit });
    const autoReplyPaused = h("input", { type: "checkbox", checked: s.auto_reply_paused === "1" });
    const savePolicy = h("button", { type: "button", class: "btn", text: "Save policy" });
    savePolicy.addEventListener("click", () => withBusy(savePolicy, async () => { const data = await api("/api/settings", { method: "POST", body: { daily_application_limit: Number(limit.value), email_confidence_threshold: Number(threshold.value), email_auto_apply: autoEmail.checked, timezone: timezone.value, daily_message_limit: Number(messageLimit.value), auto_reply_paused: autoReplyPaused.checked } }); Aira.state.settings = data.settings; toast("Policy saved.", "success"); }));
    const policy = h("div", { class: "card" }, h("h3", { text: "Application policy" }), h("div", { class: "form-grid" }, ui.field("Daily application limit", limit, `${queue.submitted_today} submitted today`), ui.field("Email action confidence threshold", threshold, "Below this, email actions always wait for you."), ui.field("Your time zone (IANA)", timezone, "Used when an email omits a time zone."), h("div", { class: "field" }, h("span", { class: "label", text: "Email actions" }), h("label", { class: "check" }, autoEmail, "Apply high-confidence, non-sensitive email actions automatically")), ui.field("Daily automatic LinkedIn replies", messageLimit, "Applies to conversations switched to Auto-reply. 0 stops automatic replies."), h("div", { class: "field" }, h("span", { class: "label", text: "Automatic replies" }), h("label", { class: "check" }, autoReplyPaused, "Pause all automatic LinkedIn replies"))), h("p", { class: "note warn", style: { marginTop: "12px" }, text: `Auto-submit: ${settings.auto_submit.available ? "available" : "disabled"}. ${settings.auto_submit.reason}` }), h("div", { class: "btn-row", style: { marginTop: "12px" } }, savePolicy));

    const kind = ui.select([["jobs", "Find LinkedIn jobs and prepare drafts"], ["messages", "Check LinkedIn inbox and draft replies"]], "jobs");
    const keywords = h("input", { type: "text", maxlength: 200, placeholder: "e.g. Solution Architect" });
    const location = h("input", { type: "text", maxlength: 200, value: "United States" });
    // ui.select matches option values strictly, so the default must be the number 60, not "60" (which left "Every 15 minutes" showing).
    const interval = ui.select([[15, "Every 15 minutes"], [30, "Every 30 minutes"], [60, "Every hour"], [180, "Every 3 hours"], [360, "Every 6 hours"], [720, "Every 12 hours"], [1440, "Daily"]], 60);
    const items = h("input", { type: "number", min: 1, max: 5, value: 3 });
    const minFit = h("input", { type: "number", min: 0, max: 100, value: 60 });
    const addBtn = h("button", { type: "button", class: "btn", text: "Add schedule" });
    const keywordsField = ui.field("Job keywords", keywords); const locationField = ui.field("Location", location); const fitField = ui.field("Minimum fit %", minFit);
    kind.addEventListener("change", () => { const jobs = kind.value === "jobs"; keywordsField.hidden = !jobs; locationField.hidden = !jobs; fitField.hidden = !jobs; items.max = jobs ? 5 : 10; });
    addBtn.addEventListener("click", () => withBusy(addBtn, async () => { await api("/api/schedules", { method: "POST", body: { kind: kind.value, keywords: keywords.value, location: location.value, interval_minutes: Number(interval.value), max_items: Number(items.value), min_fit: Number(minFit.value) } }); toast("Schedule added. First run in about a minute.", "success"); router.render(); }));
    const form = h("div", { class: "card", style: { marginTop: "16px" } }, h("h3", { text: "Scheduled discovery & drafting" }), h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Schedules search LinkedIn, score fit, and prepare drafts for your approval while Aira runs. Applications are never submitted for you. Inbox checks send replies only in conversations you switched to Auto-reply, within the limits above. Frequent runs can trigger LinkedIn rate limits; failed runs back off for 15 minutes." }), h("div", { class: "form-grid" }, ui.field("Task", kind), keywordsField, locationField, ui.field("Run every", interval), ui.field("Items per run", items), fitField), h("div", { class: "btn-row", style: { marginTop: "12px" } }, addBtn));
    const scheduleRow = (sch) => {
      const every = sch.interval_minutes >= 60 ? `${sch.interval_minutes / 60} h` : `${sch.interval_minutes} min`;
      const summary = `Every ${every} · up to ${sch.max_items} per run${sch.kind === "jobs" ? ` · ${sch.min_fit}% minimum fit` : ""} · ${sch.enabled ? `next ${fmt.datetime(sch.next_run_at)}` : "paused"}`;
      const runNow = h("button", { type: "button", class: "btn secondary sm", text: "Run now" });
      runNow.addEventListener("click", () => withBusy(runNow, async () => { await api(`/api/schedules/${sch.id}/run`, { method: "POST", body: {} }); toast("Run started.", "success"); }));
      const toggle = h("button", { type: "button", class: "btn ghost sm", text: sch.enabled ? "Pause" : "Resume" });
      toggle.addEventListener("click", () => withBusy(toggle, async () => { await api(`/api/schedules/${sch.id}`, { method: "PATCH", body: { enabled: !sch.enabled } }); router.render(); }));
      const remove = h("button", { type: "button", class: "btn ghost sm danger", text: "Delete" });
      remove.addEventListener("click", async () => { if (await Aira.confirm({ title: "Delete this schedule?", message: "", confirmLabel: "Delete", danger: true })) await withBusy(remove, async () => { await api(`/api/schedules/${sch.id}`, { method: "DELETE" }); router.render(); }); });
      return h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: sch.kind === "jobs" ? `Jobs: ${sch.keywords}` : "Inbox replies" }), h("span", { class: "sub", text: summary }), sch.last_status ? h("span", { class: "sub", text: `Last: ${sch.last_status}` }) : null), h("div", { class: "btn-row" }, runNow, toggle, remove));
    };
    const schedules = h("div", { class: "panel", style: { marginTop: "16px" } }, h("div", { class: "panel-head" }, h("h3", { text: "Schedules" }), h("small", { class: "muted", text: String(automation.schedules.length) })), automation.schedules.length ? h("div", { class: "list" }, ...automation.schedules.map(scheduleRow)) : h("div", { class: "empty", text: "No schedules yet." }));
    const runs = h("div", { class: "panel", style: { marginTop: "16px" } }, h("div", { class: "panel-head" }, h("h3", { text: "Recent runs" })), automation.runs.length ? h("div", { class: "list" }, ...automation.runs.map((run) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: `${run.kind === "jobs" ? "Jobs" : "Messages"} · ${fmt.datetime(run.finished_at)}` }), h("span", { class: "sub", text: run.summary })), h("span", { class: `badge ${run.status === "ok" ? "stage applied" : "stage failed"}`, text: run.status })))) : h("div", { class: "empty", text: "No runs yet." }));
    content.replaceChildren(policy, form, schedules, runs);
    if (!document.getElementById("tzlist")) document.body.append(h("datalist", { id: "tzlist" }, ...(typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : ["UTC"]).map((tz) => h("option", { value: tz }))));
  }

  // -------------------------------------------------------------------------
  async function renderData(content) {
    const [settings, audit, metrics] = await Promise.all([api("/api/settings"), api("/api/audit?limit=40"), api("/api/metrics")]);
    const retention = h("input", { type: "number", min: 30, max: 3650, value: settings.settings.retention_days });
    const saveRetention = h("button", { type: "button", class: "btn secondary sm", text: "Save retention" });
    saveRetention.addEventListener("click", () => withBusy(saveRetention, async () => { await api("/api/settings", { method: "POST", body: { retention_days: Number(retention.value) } }); toast("Retention saved.", "success"); }));
    const password = h("input", { type: "password", autocomplete: "current-password", placeholder: "Your Aira password" });
    const deleteBtn = h("button", { type: "button", class: "btn danger", text: "Delete my account and data" });
    deleteBtn.addEventListener("click", async () => { if (!password.value) { toast("Enter your password to confirm.", "error"); return; } if (await Aira.confirm({ title: "Delete everything?", message: "Your jobs, applications, documents, interviews, recruiters, and LinkedIn session are permanently removed from this computer.", confirmLabel: "Delete permanently", danger: true })) await withBusy(deleteBtn, async () => { await api("/api/account/delete", { method: "POST", body: { password: password.value } }); toast("Account deleted.", "success"); setTimeout(() => location.reload(), 800); }); });
    content.replaceChildren(
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h3", { text: "Where your data lives" }), h("p", { class: "muted", text: "Everything is stored in local SQLite databases and an encrypted document store on this computer. Aira binds to 127.0.0.1 only. Resume and job text go to your local Ollama only when you ask for a review or draft." }), ui.kv([["Schema version", String(settings.schema_version)], ["Uptime", `${Math.round(metrics.uptime_seconds / 60)} min`], ["Requests served", String(metrics.requests_total)]])),
        h("div", { class: "card" }, h("h3", { text: "Export & retention" }), h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", text: "Export all data (JSON)", onclick: () => Aira.download("/api/account/export", "aira-export.json") })), h("div", { class: "form-grid", style: { marginTop: "12px" } }, ui.field("Document retention (days)", retention, "Expired documents are purged on the next cleanup.")), h("div", { class: "btn-row", style: { marginTop: "8px" } }, saveRetention)),
        h("div", { class: "card" }, h("h3", { text: "Delete account" }), h("p", { class: "muted", text: "Irreversible. Export first if you want a copy." }), ui.field("Confirm password", password), h("div", { class: "btn-row", style: { marginTop: "10px" } }, deleteBtn)),
        h("div", { class: "card" }, h("h3", { text: "Health" }), ui.kv(Object.entries(metrics.integrations).map(([k, v]) => [fmt.title(k), `${v.ok ? "ok" : "down"} · ${fmt.ago(v.checked_at)}`])), h("p", { class: "muted", style: { fontSize: "12px", marginTop: "8px" }, text: "Liveness: /api/health · Readiness: /api/ready · Metrics: /api/metrics" })),
      ),
      h("div", { class: "panel", style: { marginTop: "16px" } }, h("div", { class: "panel-head" }, h("h3", { text: "Audit log" }), h("small", { class: "muted", text: "latest 40 actions" })), audit.audit.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "When" }), h("th", { text: "Action" }), h("th", { text: "Target" }), h("th", { text: "Actor" }), h("th", { text: "Detail" }))), h("tbody", {}, ...audit.audit.map((a) => h("tr", {}, h("td", { text: fmt.datetime(a.at) }), h("td", { text: a.action }), h("td", { text: `${a.target_type} ${a.target_id}`.trim() }), h("td", { text: `${a.actor} (${a.source})` }), h("td", { text: JSON.stringify(a.detail).slice(0, 120) })))))) : h("div", { class: "empty", text: "No audit entries yet." })),
    );
  }
})();

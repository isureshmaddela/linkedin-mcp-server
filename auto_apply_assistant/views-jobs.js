/* Aira — Jobs view and the shared job detail drawer */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy, router } = Aira;

  const JOB_TABS = [
    { id: "all", label: "All Jobs", view: "all" },
    { id: "linkedin", label: "LinkedIn", view: "linkedin" },
    { id: "aira", label: "Aira Jobs", view: "aira_jobs" },
    { id: "google", label: "Google Jobs", view: "google_jobs" },
    { id: "portals", label: "Company Portals", view: "portals" },
    { id: "recommended", label: "Recommended", view: "recommended" },
    { id: "saved", label: "Saved", view: "saved" },
    { id: "autopilot", label: "Auto-apply", view: "" },
  ];
  const STAGES = ["discovered", "saved", "ready_to_apply", "applying", "needs_review", "applied", "assessment", "recruiter_screen", "technical_interview", "manager_interview", "panel_interview", "final_interview", "offer", "accepted", "rejected", "withdrawn", "failed"];
  const CLASSES = ["Recommended", "Strong Match", "Good Match", "Stretch", "Not Recommended"];

  // The source catalog is read again on every visit to Jobs: a SerpApi key connected in Aira API, or a LinkedIn session
  // that just came up, must show here without a reload.
  let sourcesLoad = null;
  function sources() {
    if (!sourcesLoad) sourcesLoad = api("/api/sources", { silent: true }).then((data) => (Aira.state.sources = data.sources), (error) => { sourcesLoad = null; throw error; });
    return sourcesLoad;
  }
  const KEY_PAGE = "#/airaapi?tab=account";

  // -------------------------------------------------------------------------
  // Search panel (per tab)
  // -------------------------------------------------------------------------
  function searchPanel(tab, onResults) {
    const panel = h("form", { class: "search-panel", "aria-label": "Search jobs" });
    const keywords = h("input", { type: "search", "data-search": "1", placeholder: "e.g. Solution Architect", maxlength: 200, required: true, value: Aira.recall("search.keywords", "") });
    const location = h("input", { type: "text", placeholder: "City, region, or Remote", maxlength: 200, value: Aira.recall("search.location", "United States") });
    const sourceSelect = ui.select([], "");
    const identifier = h("input", { type: "text", placeholder: "", maxlength: 128, autocomplete: "off" });
    const identifierField = ui.field(
      "Board identifier",
      identifier,
      "Used only by the selected source; not required for LinkedIn.",
    );
    const workType = ui.select([["", "Any arrangement"], ["remote", "Remote"], ["hybrid", "Hybrid"], ["on_site", "On-site"]], "");
    const jobType = ui.select([["", "Any employment type"], ["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"], ["temporary", "Temporary"], ["internship", "Internship"]], "");
    const pages = ui.select([["1", "1 page"], ["2", "2 pages"], ["3", "3 pages"]], "1");
    const w2 = h("input", { type: "checkbox" });
    const hint = h("p", { class: "muted", style: { margin: "8px 0 0", fontSize: "12px" } });
    const submit = h("button", { type: "submit", class: "btn" }, "Find roles");
    const importUrl = h("input", { type: "url", placeholder: "Paste a LinkedIn, Greenhouse, Lever, Ashby, or SmartRecruiters job URL", maxlength: 500 });
    const importBtn = h("button", { type: "button", class: "btn secondary" }, "Import job");

    const grid = h("div", { class: "form-grid" }, ui.field("Role or keywords", keywords), ui.field("Location", location), ui.field("Source", sourceSelect), identifierField, ui.field("Arrangement", workType), ui.field("Employment type", jobType), ui.field("Depth", pages), h("div", { class: "field" }, h("span", { class: "label", text: "Pay wording" }), h("label", { class: "check" }, w2, "Search W-2 wording")));
    panel.append(grid, h("div", { class: "btn-row", style: { marginTop: "12px" } }, submit, h("span", { class: "muted", text: "or" }), h("div", { style: { flex: "1 1 320px", display: "flex", gap: "8px" } }, importUrl, importBtn)), hint);

    let catalog = [];
    const pickSource = () => {
      const chosen = catalog.find((item) => item.source === sourceSelect.value);
      if (!chosen) return;
      identifierField.hidden = !chosen.identifier_label;
      identifierField.querySelector("label").textContent = chosen.identifier_label || "Identifier";
      identifierField.querySelector(".hint").textContent = chosen.identifier_help || "";
      identifier.required = Boolean(chosen.identifier_label);
      identifier.placeholder = chosen.identifier_label || "";
      pages.parentElement.hidden = !["linkedin", "google_jobs"].includes(chosen.source);
      const structured = ["linkedin", "google_jobs", "aira_jobs", "lever", "ashby", "smartrecruiters"].includes(chosen.source);
      workType.disabled = !structured;
      jobType.disabled = !structured;
      if (chosen.source === "google_jobs" && !chosen.connected) hint.replaceChildren(`${chosen.label}: `, h("a", { href: KEY_PAGE, text: "connect a SerpApi key in Aira API > Account" }), " (or set SERPAPI_API_KEY in .env), or search ", h("a", { href: "#/jobs/aira", text: "Aira Jobs" }), " for free.");
      else if (chosen.source === "aira_jobs") hint.textContent = "Aira Jobs searches free job boards (Himalayas, The Muse, Remotive, Jobicy, Arbeitnow) itself: no key, no credits. Each result links back to its board.";
      else hint.textContent = chosen.connected ? chosen.connection_hint || "" : `${chosen.label}: ${chosen.connection_hint}`;
      submit.disabled = !chosen.connected && chosen.source !== "linkedin";
      Aira.remember("search.source", chosen.source);
      Aira.remember(`search.identifier.${chosen.source}`, identifier.value);
    };
    sources().then((list) => {
      catalog = list.filter((item) => item.status === "available");
      const allowed = tab.id === "linkedin" ? ["linkedin"] : tab.id === "aira" ? ["aira_jobs"] : tab.id === "google" ? ["google_jobs"] : tab.id === "portals" ? catalog.filter((s) => s.group === "ats").map((s) => s.source) : catalog.map((s) => s.source);
      sourceSelect.replaceChildren(...catalog.filter((s) => allowed.includes(s.source)).map((s) => h("option", { value: s.source, text: s.label + (s.connected ? "" : " (not connected)") })));
      // Without a LinkedIn session the free Aira Jobs source is the one that works out of the box.
      const linkedinUp = catalog.some((s) => s.source === "linkedin" && s.connected);
      const remembered = Aira.recall("search.source", linkedinUp ? "linkedin" : "aira_jobs");
      if (allowed.includes(remembered)) sourceSelect.value = remembered;
      identifier.value = Aira.recall(`search.identifier.${sourceSelect.value}`, "");
      pickSource();
    });
    sourceSelect.addEventListener("change", () => { identifier.value = Aira.recall(`search.identifier.${sourceSelect.value}`, ""); pickSource(); });
    identifier.addEventListener("input", () => Aira.remember(`search.identifier.${sourceSelect.value}`, identifier.value));

    panel.addEventListener("submit", async (event) => {
      event.preventDefault();
      Aira.remember("search.keywords", keywords.value);
      Aira.remember("search.location", location.value);
      await withBusy(submit, async () => {
        submit.textContent = "Searching…";
        hint.textContent = `Searching ${sourceSelect.options[sourceSelect.selectedIndex].text}… this can take a minute for LinkedIn.`;
        const data = await api("/api/jobs/search", { method: "POST", body: { source: sourceSelect.value, identifier: identifier.value, keywords: keywords.value, location: location.value, max_pages: Number(pages.value), work_type: workType.value, job_type: jobType.value, w2_search: w2.checked } });
        const errors = Object.keys(data.section_errors || {});
        toast(`${fmt.plural(data.count, "job")} found${errors.length ? " (some sections had errors)" : ""}.`, "success");
        hint.textContent = errors.length ? `Provider reported partial errors: ${errors.join(", ")}.` : "";
        onResults(data);
      });
    });
    importBtn.addEventListener("click", () => withBusy(importBtn, async () => {
      if (!importUrl.value.trim()) { toast("Paste a job URL first.", "error"); return; }
      const data = await api("/api/jobs/import", { method: "POST", body: { url: importUrl.value } });
      importUrl.value = "";
      toast(`Imported from ${data.source.replaceAll("_", " ")}.`, "success");
      onResults({ jobs: [data.job], imported: data.job, needs_details: data.needs_details });
    }));
    return panel;
  }

  // -------------------------------------------------------------------------
  // Jobs list
  // -------------------------------------------------------------------------
  function jobRow(job, { selected = false, onSelect }) {
    const salary = fmt.money(job.salary_min, job.salary_max, job.currency);
    const row = h("button", { type: "button", class: "row", "data-roving-item": "1", "aria-selected": selected ? "true" : "false", onclick: () => onSelect(job) },
      h("div", { style: { minWidth: "0" } },
        h("span", { class: "title", text: job.title || `Job ${job.job_id}` }),
        h("span", { class: "sub", text: [job.company, job.location].filter(Boolean).join(" · ") || "Details not loaded yet" }),
        h("div", { class: "meta" }, ...(job.sources || [job.source]).map(ui.source), ui.stage(job.stage), ui.classification(job.match_classification), job.easy_apply ? h("span", { class: "badge easy", text: "Easy Apply" }) : null, job.workplace_type ? h("span", { class: "badge", text: fmt.stage(job.workplace_type) }) : null, salary ? h("span", { class: "badge", text: salary }) : null, job.saved ? h("span", { class: "badge", text: "★ Saved" }) : null, job.documents_ready ? h("span", { class: "badge", text: "Drafts ready" }) : null),
      ),
      h("div", { class: "aside" }, ui.score(job.match_score), h("small", { class: "muted", text: job.posted_at ? `Posted ${fmt.date(job.posted_at)}` : fmt.ago(job.updated_at) })),
    );
    return row;
  }

  Aira.views.jobs = async (main, route) => {
    sourcesLoad = null;
    const tabId = route.path[1] || "all";
    const tab = JOB_TABS.find((item) => item.id === tabId) || JOB_TABS[0];
    const stored = Aira.recall(`filters.jobs.${tab.id}`, {});
    const filters = { q: "", stage: "", classification: "", min_score: "", sort: tab.id === "recommended" ? "score" : "updated", page: "1", workplace_type: "", ...stored, ...route.query };
    const listBox = h("div", { class: "panel" });
    const subnav = h("nav", { class: "subnav", "aria-label": "Job sources" }, ...JOB_TABS.map((item) => h("a", { href: `#/jobs/${item.id}`, "aria-current": item.id === tab.id ? "page" : "false", text: item.label })));

    const q = h("input", { type: "search", "data-search": "1", placeholder: "Filter by title, company, or location", value: filters.q });
    const stage = ui.select([["", "Any status"], ...STAGES.map((s) => [s, fmt.title(fmt.stage(s))])], filters.stage);
    const classification = ui.select([["", "Any match"], ...CLASSES.map((c) => [c, c])], filters.classification);
    const minScore = ui.select([["", "Any score"], ["85", "85+"], ["70", "70+"], ["55", "55+"], ["40", "40+"]], filters.min_score);
    const workplace = ui.select([["", "Any workplace"], ["remote", "Remote"], ["hybrid", "Hybrid"], ["on_site", "On-site"]], filters.workplace_type);
    const sort = ui.select([["updated", "Recently updated"], ["score", "Best match"], ["posted", "Newest posting"], ["company", "Company A–Z"], ["title", "Title A–Z"], ["stage", "Status"]], filters.sort);
    const apply = () => {
      const next = { q: q.value, stage: stage.value, classification: classification.value, min_score: minScore.value, workplace_type: workplace.value, sort: sort.value, page: "1" };
      Aira.remember(`filters.jobs.${tab.id}`, next);
      router.go(["jobs", tab.id], next, { replace: true });
    };
    for (const control of [stage, classification, minScore, workplace, sort]) control.addEventListener("change", apply);
    let timer;
    q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(apply, 350); });
    const filterBar = h("div", { class: "filters", role: "search" }, h("div", { class: "field grow" }, h("label", { for: "jobFilterQ", text: "Filter" }), Object.assign(q, { id: "jobFilterQ" })), ui.field("Status", stage), ui.field("Match", classification), ui.field("Score", minScore), ui.field("Workplace", workplace), ui.field("Sort", sort), h("button", { type: "button", class: "btn ghost sm", text: "Clear", onclick: () => { Aira.remember(`filters.jobs.${tab.id}`, {}); router.go(["jobs", tab.id], {}, { replace: true }); } }));

    const head = h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Jobs" }), h("p", { text: "Search every connected source, import postings by URL, and keep one deduplicated pipeline. Scores are explainable and never guess at missing facts." })), h("div", { class: "page-actions" }, h("button", { type: "button", class: "btn secondary sm", text: "Rescore all", onclick: (event) => withBusy(event.currentTarget, async () => { const data = await api("/api/jobs/rescore", { method: "POST", body: {} }); toast(`${fmt.plural(data.scored, "job")} rescored.`, "success"); load(); }) })));

    if (tab.id === "autopilot") {
      const host = h("div");
      main.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Jobs" }), h("p", { text: "Auto-apply: tell Aira which positions you want. On a schedule it searches, scores every job against your profile and applies to the best matches, within your limits." }))), subnav, host);
      await Aira.renderAutopilot(host);
      return;
    }
    const showSearch = !["recommended", "saved"].includes(tab.id);
    main.replaceChildren(...[head, subnav, showSearch ? searchPanel(tab, (data) => { if (data.imported) { load().then(() => openJob(data.imported.job_id, { loadDetails: data.needs_details })); } else { load(); if (data.jobs[0]) openJob(data.jobs[0].job_id, { loadDetails: !data.jobs[0].description }); } }) : h("p", { class: "note", text: tab.id === "recommended" ? "Jobs classified Recommended or Strong Match by the explainable scorer, ranked by score. Load a description and your profile to score more jobs." : "Jobs you starred, plus anything in the Saved status." }), tab.id === "google" ? h("p", { class: "muted", style: { margin: "-6px 0 14px", fontSize: "12.5px" } }, "Want Google's own filters, job alerts and company research? ", h("a", { href: "#/careers?tab=jobs", text: "Open Careers" }), ".") : tab.id === "aira" ? h("p", { class: "muted", style: { margin: "-6px 0 14px", fontSize: "12.5px" } }, "Aira's own job search, built into Aira API: no SerpApi key and no credits. Every search is kept in ", h("a", { href: "#/airaapi?tab=history&engine=aira_jobs", text: "Aira API history" }), ".") : null, filterBar, listBox].filter(Boolean));

    async function load() {
      listBox.replaceChildren(ui.skeleton(6));
      try {
        const params = { view: tab.view, q: q.value, stage: stage.value, classification: classification.value, min_score: minScore.value, workplace_type: workplace.value, sort: sort.value, page: filters.page, page_size: "40" };
        const data = await api(`/api/jobs?${new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v)))}`);
        // The Google Jobs tab explains a missing key instead of inviting a search that cannot run.
        const googleMissing = tab.id === "google" && !data.jobs.length && !(await sources().catch(() => [])).some((item) => item.source === "google_jobs" && item.connected);
        renderList(data, googleMissing);
      } catch (error) { listBox.replaceChildren(ui.error(error, load)); }
    }
    function renderList(data, googleMissing = false) {
      const list = h("div", { class: "list", "data-roving": "1", role: "list" });
      const selectedId = route.path[3];
      if (googleMissing) {
        list.append(ui.empty("Google Jobs needs a SerpApi key", "Connect a key once and Google Jobs searches here, in Careers and in your own scripts all use it. It is stored encrypted on this computer. No key? Aira Jobs searches free job boards without one.", h("div", { class: "btn-row", style: { justifyContent: "center" } }, h("a", { class: "btn", href: "#/jobs/aira", text: "Search Aira Jobs (free)" }), h("a", { class: "btn secondary", href: KEY_PAGE, text: "Connect a key in Aira API" }))));
      } else if (!data.jobs.length) {
        const message = tab.id === "recommended" ? "Score jobs to see recommendations here." : tab.id === "saved" ? "Star a job in any list to keep it here." : "Search above, or import a posting by URL to start your pipeline.";
        list.append(ui.empty("No jobs yet", message));
      }
      for (const job of data.jobs) list.append(jobRow(job, { selected: job.job_id === selectedId, onSelect: (item) => openJob(item.job_id, { loadDetails: !item.has_description }) }));
      const pager = h("div", { class: "pager" }, h("span", { text: `${data.total} ${data.total === 1 ? "job" : "jobs"} · page ${data.page} of ${data.pages}` }), h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", text: "Previous", disabled: data.page <= 1, onclick: () => { filters.page = String(data.page - 1); router.go(["jobs", tab.id], { ...route.query, page: filters.page }, { replace: true }); } }), h("button", { type: "button", class: "btn ghost sm", text: "Next", disabled: data.page >= data.pages, onclick: () => { filters.page = String(data.page + 1); router.go(["jobs", tab.id], { ...route.query, page: filters.page }, { replace: true }); } })));
      listBox.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: tab.label }), h("small", { class: "muted", text: data.facets && data.facets.sources.length ? data.facets.sources.map((s) => `${s.source.replaceAll("_", " ")} ${s.count}`).join(" · ") : "" })), list, pager);
    }
    await load();
    if (route.path[2] === "job" && route.path[3]) openJob(route.path[3]);
    Aira.state.views.jobsReload = load;
  };

  // -------------------------------------------------------------------------
  // Job detail drawer
  // -------------------------------------------------------------------------
  async function openJob(jobId, { loadDetails = false, tab = "overview" } = {}) {
    const body = h("div", {}, ui.skeleton(5));
    const panel = Aira.drawer({ title: "Loading…", body, onClose: () => { if (Aira.state.views.jobsReload) Aira.state.views.jobsReload(); if (Aira.state.views.applicationsReload) Aira.state.views.applicationsReload(); } });
    let job;
    try {
      job = (await api(`/api/jobs/${encodeURIComponent(jobId)}`)).job;
      if (loadDetails && !job.description && (job.sources || []).includes("linkedin")) {
        body.replaceChildren(h("p", { class: "note", text: "Reading the full posting from LinkedIn…" }), ui.skeleton(4));
        try { job = (await api(`/api/jobs/${encodeURIComponent(jobId)}/details`, { method: "POST", body: {} })).job; job = (await api(`/api/jobs/${encodeURIComponent(jobId)}`)).job; }
        catch (error) { Aira.reportError(error); }
      }
    } catch (error) { body.replaceChildren(ui.error(error)); return; }
    renderJob(panel, job, tab);
  }
  Aira.openJob = openJob;

  function renderJob(panel, job, activeTab) {
    const reload = async (nextTab) => { const fresh = (await api(`/api/jobs/${encodeURIComponent(job.job_id)}`)).job; renderJob(panel, fresh, nextTab || activeTab); };
    panel.head.querySelector("h2").textContent = job.title || `Job ${job.job_id}`;
    const sub = panel.head.querySelector(".sub") || h("div", { class: "sub" });
    sub.replaceChildren(h("span", { text: [job.company, job.location].filter(Boolean).join(" · ") }), " ", ...(job.sources || []).map(ui.source), " ", ui.stage(job.stage), " ", ui.classification(job.match_classification) || "");
    if (!sub.parentElement) panel.head.firstElementChild.append(sub);
    const applyUrl = job.application_url || job.job_url;
    const headActions = h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn secondary sm", text: job.saved ? "★ Saved" : "☆ Save", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/save`, { method: "POST", body: { saved: !job.saved } }); toast(job.saved ? "Removed from saved jobs." : "Saved.", "success"); reload(); }) }),
      h("a", { class: "btn secondary sm", href: applyUrl, target: "_blank", rel: "noopener noreferrer", text: job.application_url && job.application_url !== job.job_url ? "Open application ↗" : "Open posting ↗" }),
    );
    const tabs = ui.tabs([{ id: "overview", label: "Overview" }, { id: "fit", label: "Fit & evidence" }, { id: "documents", label: "Documents" }, { id: "apply", label: "Apply" }, { id: "history", label: "History" }], activeTab, (id) => renderJob(panel, job, id));
    const pane = h("div", {});
    const headRow = panel.head.querySelector(".btn-row");
    for (const stale of headRow.querySelectorAll("[data-job-action]")) stale.remove();
    for (const action of [...headActions.children]) { action.dataset.jobAction = "1"; headRow.insertBefore(action, headRow.lastElementChild); }
    panel.body.replaceChildren(tabs, pane);
    const ctx = { job, reload, pane };
    if (activeTab === "overview") renderOverview(ctx);
    else if (activeTab === "fit") renderFit(ctx);
    else if (activeTab === "documents") renderDocuments(ctx);
    else if (activeTab === "apply") Aira.renderApplyTab(ctx);
    else renderHistory(ctx);
  }

  function renderOverview({ job, reload, pane }) {
    const stageSelect = ui.select([[job.stage, `${fmt.title(fmt.stage(job.stage))} (current)`], ...(job.allowed_stages || []).map((s) => [s, fmt.title(fmt.stage(s))])], job.stage, { "aria-label": "Application status" });
    stageSelect.addEventListener("change", async () => {
      if (["rejected", "withdrawn", "failed"].includes(stageSelect.value) && !(await Aira.confirm({ title: `Mark as ${fmt.stage(stageSelect.value)}?`, message: "This closes the application. You can still see its history.", confirmLabel: "Yes, update", danger: true }))) { stageSelect.value = job.stage; return; }
      try { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/stage`, { method: "PATCH", body: { stage: stageSelect.value, reason: "Updated from the job panel" } }); toast(`Moved to ${fmt.stage(stageSelect.value)}.`, "success"); reload(); }
      catch (error) { Aira.reportError(error); stageSelect.value = job.stage; }
    });
    const facts = ui.kv([
      ["Source", (job.source_records || []).map((r) => `${r.source.replaceAll("_", " ")} · ${r.external_id}`).join(", ")],
      ["Workplace", job.workplace_type ? fmt.stage(job.workplace_type) : null],
      ["Employment", job.employment_type ? fmt.stage(job.employment_type) : null],
      ["Seniority", job.seniority || null],
      ["Salary", fmt.money(job.salary_min, job.salary_max, job.currency) || null],
      ["Posted", job.posted_at ? fmt.date(job.posted_at) : null],
      ["Expires", job.expires_at ? fmt.date(job.expires_at) : null],
      ["Application", job.application_method ? fmt.stage(job.application_method) : null],
      ["First seen", job.first_seen_at ? fmt.ago(job.first_seen_at) : null],
      ["Last seen", job.last_seen_at ? fmt.ago(job.last_seen_at) : null],
    ]);
    const notes = h("textarea", { class: "textarea", placeholder: "Private notes about this role, contacts, or next steps…", rows: 3 }, job.notes || "");
    const followUp = h("input", { type: "datetime-local", value: job.follow_up_at ? toLocalInput(job.follow_up_at) : "" });
    const saveNotes = h("button", { type: "button", class: "btn secondary sm", text: "Save notes" });
    saveNotes.addEventListener("click", () => withBusy(saveNotes, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/notes`, { method: "PATCH", body: { notes: notes.value, follow_up_at: followUp.value ? new Date(followUp.value).toISOString() : "" } }); toast("Notes saved.", "success"); }));
    const descriptionBox = job.description ? h("div", { class: "description", text: job.description }) : h("div", { class: "empty" }, h("strong", { text: "No description yet" }), (job.sources || []).includes("linkedin") ? h("button", { type: "button", class: "btn", text: "Read posting from LinkedIn", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/details`, { method: "POST", body: {} }); toast("Description loaded and scored.", "success"); reload(); }) }) : h("span", { text: "This source did not include a description. Open the posting to read it." }));
    const required = (job.required_skills || []).slice(0, 25);
    const preferred = (job.preferred_skills || []).slice(0, 15);
    pane.append(
      h("div", { class: "grid sidebar-right" },
        h("div", {},
          h("div", { class: "btn-row", style: { marginBottom: "12px" } }, h("div", { class: "field", style: { minWidth: "220px" } }, h("label", { for: "stageSelect", text: "Status" }), Object.assign(stageSelect, { id: "stageSelect" })), h("button", { type: "button", class: "btn", text: "Prepare application →", onclick: () => renderJob(panel_of(pane), job, "apply") }), h("button", { type: "button", class: "btn ghost sm danger", text: "Archive", onclick: async (event) => { if (await Aira.confirm({ title: "Archive this job?", message: "It disappears from lists and analytics. History is kept.", confirmLabel: "Archive", danger: true })) { await withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/archive`, { method: "POST", body: {} }); toast("Job archived.", "success"); Aira.closeDrawer(); }); } } })),
          h("h3", { text: "Job description" }), descriptionBox,
          required.length ? h("div", { style: { marginTop: "12px" } }, h("h3", { text: "Skills mentioned" }), h("div", { class: "chips" }, ...required.map((s) => h("span", { class: "chip on", text: fmt.stage(s) })), ...preferred.map((s) => h("span", { class: "chip", text: `${fmt.stage(s)} (preferred)` })))) : null,
        ),
        h("div", {},
          h("div", { class: "card" }, h("h3", { text: "At a glance" }), facts),
          h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Notes & follow-up" }), ui.field("Notes", notes), ui.field("Follow-up reminder", followUp), h("div", { style: { marginTop: "8px" } }, saveNotes)),
          job.interviews && job.interviews.length ? h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Interviews" }), ...job.interviews.map((i) => h("div", { class: "row", style: { padding: "6px 0" } }, h("div", {}, h("span", { class: "title", text: fmt.title(fmt.stage(i.interview_type)) }), h("span", { class: "sub", text: `${fmt.datetime(i.start_at, i.timezone)} · ${i.status}` })), h("a", { class: "btn ghost sm", href: `#/interviews/${i.interview_id}`, text: "Open" })))) : null,
          job.recruiters && job.recruiters.length ? h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Recruiters" }), ...job.recruiters.map((r) => h("a", { class: "row", href: `#/recruiters/${r.recruiter_id}`, style: { padding: "6px 0" } }, h("div", {}, h("span", { class: "title", text: r.name }), h("span", { class: "sub", text: [r.company, r.email].filter(Boolean).join(" · ") }))))) : null,
        ),
      ),
    );
  }
  const panel_of = (pane) => Aira.activeDrawer;

  function renderFit({ job, reload, pane }) {
    const explanation = job.match_explanation || {};
    const dims = explanation.dimensions || [];
    const scoreBox = h("div", { class: "card" },
      h("div", { style: { display: "flex", gap: "14px", alignItems: "center" } }, ui.score(job.match_score), h("div", {}, h("strong", { text: job.match_classification || "Not scored yet" }), h("div", { class: "muted", text: explanation.classification_reason || "Load the job description and complete your profile, then score this job." }), explanation.version ? h("small", { class: "muted", text: `${explanation.version} · ${fmt.ago(explanation.computed_at)}` }) : null)),
      (explanation.hard_blockers || []).length ? h("div", { class: "note warn", style: { marginTop: "10px" } }, h("strong", { text: "Blocker: " }), explanation.hard_blockers.join(" ")) : null,
      h("div", { class: "btn-row", style: { marginTop: "12px" } }, h("button", { type: "button", class: "btn secondary sm", text: job.match_score == null ? "Score this job" : "Rescore", disabled: !job.description, onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/score`, { method: "POST", body: {} }); toast("Scored.", "success"); reload("fit"); }) }), h("a", { class: "btn ghost sm", href: "#/settings/profile", text: "Improve profile inputs" })),
    );
    const dimensionList = h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Score breakdown" }), h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Each dimension is scored only from explicit evidence. Dimensions without enough information are left out of the total instead of being guessed." }));
    for (const dim of dims) {
      const bar = h("div", { class: `bar${dim.score == null ? " none" : ""}` }, dim.score == null ? null : h("i", { class: dim.score < 50 ? "low" : "", style: { width: `${Math.max(2, dim.score)}%` } }));
      dimensionList.append(h("div", { class: "dimension" }, h("span", { text: dim.label || dim.name }), bar, h("span", { class: "num", text: dim.score == null ? "n/a" : `${Math.round(dim.score)}` }), h("div", { class: "why" }, h("span", { text: dim.score == null ? dim.missing_information || dim.reason || "" : dim.reason || "" }), (dim.evidence || []).length ? h("div", { class: "evidence" }, ...dim.evidence.slice(0, 12).map((e) => h("span", { class: "chip", title: `Source: ${e.source}`, text: fmt.stage(String(e.fact)) }))) : null, (dim.missing || []).length ? h("div", { class: "muted", style: { marginTop: "4px" }, text: `Not found: ${dim.missing.slice(0, 12).map((m) => fmt.stage(String(m))).join(", ")}` }) : null)));
    }
    if (!dims.length) dimensionList.append(h("div", { class: "empty", text: "No breakdown yet." }));
    const model = h("input", { type: "text", value: (Aira.state.settings && Aira.state.settings.default_model) || "qwen3.5:4b", maxlength: 100 });
    const aiBox = h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Local AI fit review" }), h("p", { class: "muted", style: { fontSize: "12.5px" }, text: "Ollama compares your saved resume with the posting and explains strengths and gaps. Nothing leaves this computer." }),
      h("div", { class: "btn-row" }, h("div", { class: "field", style: { minWidth: "200px" } }, h("label", { for: "fitModel", text: "Ollama model" }), Object.assign(model, { id: "fitModel" })), h("button", { type: "button", class: "btn", text: "Review fit", disabled: !job.description, onclick: (event) => withBusy(event.currentTarget, async () => { event.currentTarget.textContent = "Reviewing…"; await api(`/api/jobs/${encodeURIComponent(job.job_id)}/evaluate`, { method: "POST", body: { model: model.value } }); toast("Fit review complete.", "success"); reload("fit"); }) })),
      job.fit_score != null ? h("div", { class: "note", style: { marginTop: "12px" } }, h("strong", { text: `${job.fit_score}% · ` }), h("span", { text: job.fit_summary }), h("div", { class: "grid cols-2", style: { marginTop: "10px" } }, h("div", {}, h("strong", { text: "Evidence of fit" }), h("ul", { class: "inline-list" }, ...(job.strengths || []).map((s) => h("li", { text: s })))), h("div", {}, h("strong", { text: "Gaps to consider" }), h("ul", { class: "inline-list" }, ...(job.gaps || []).map((s) => h("li", { text: s })))))) : null,
    );
    pane.append(scoreBox, dimensionList, aiBox);
  }

  function renderDocuments({ job, reload, pane }) {
    const stale = job.resume_draft && job.resume_hash && Aira.state.profile && Aira.state.profile.resume_hash && job.resume_hash !== Aira.state.profile.resume_hash;
    const model = h("input", { type: "text", value: (Aira.state.settings && Aira.state.settings.default_model) || "qwen3.5:4b", maxlength: 100 });
    const resume = h("textarea", { class: "textarea doc-editor", placeholder: "Tailored resume appears here after generation. Edit freely." }, job.resume_draft || "");
    const letter = h("textarea", { class: "textarea doc-editor", placeholder: "Cover letter appears here after generation." }, job.cover_letter || "");
    const confirm = h("input", { type: "checkbox" });
    pane.append(
      h("p", { class: "note", text: "Drafts use only facts from your saved resume and profile. Review every line, then approve to move the job to Ready to Apply." }),
      stale ? h("p", { class: "note warn", text: "Your master resume changed after these drafts were written. Regenerate them before approving." }) : null,
      h("div", { class: "btn-row", style: { margin: "12px 0" } }, h("div", { class: "field", style: { minWidth: "200px" } }, h("label", { for: "docModel", text: "Ollama model" }), Object.assign(model, { id: "docModel" })), h("button", { type: "button", class: "btn", text: job.resume_draft ? "Regenerate drafts" : "Generate tailored drafts", disabled: !job.description, onclick: (event) => withBusy(event.currentTarget, async () => { event.currentTarget.textContent = "Drafting…"; await api(`/api/jobs/${encodeURIComponent(job.job_id)}/generate`, { method: "POST", body: { model: model.value } }); toast("Drafts generated. Review before approving.", "success"); reload("documents"); }) })),
      h("div", { class: "grid cols-2" }, docEditor("Tailored resume", resume), docEditor("Cover letter", letter)),
      h("div", { class: "btn-row", style: { marginTop: "12px" } },
        h("button", { type: "button", class: "btn secondary", text: "Save edits", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/documents`, { method: "PATCH", body: { resume: resume.value, cover_letter: letter.value } }); toast("Edits saved.", "success"); reload("documents"); }) }),
        h("button", { type: "button", class: "btn secondary", text: "Save to Documents", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/documents`, { method: "PATCH", body: { resume: resume.value, cover_letter: letter.value } }); await api(`/api/jobs/${encodeURIComponent(job.job_id)}/documents/snapshot`, { method: "POST", body: {} }); toast("Saved as versioned documents.", "success"); }) }),
      ),
      h("div", { class: "card", style: { marginTop: "14px", background: "var(--surface-2)" } }, h("h3", { text: "Approval checkpoint" }), h("label", { class: "check" }, confirm, "I reviewed both drafts and verified every claim is true."), h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { type: "button", class: "btn", text: "Approve drafts → Ready to apply", onclick: (event) => withBusy(event.currentTarget, async () => { if (!confirm.checked) { toast("Confirm you reviewed both drafts first.", "error"); return; } await api(`/api/jobs/${encodeURIComponent(job.job_id)}/documents`, { method: "PATCH", body: { resume: resume.value, cover_letter: letter.value } }); await api(`/api/jobs/${encodeURIComponent(job.job_id)}/stage`, { method: "PATCH", body: { stage: "ready_to_apply", reason: "Drafts approved" } }); toast("Approved. Continue in the Apply tab.", "success"); reload("apply"); }) }))),
    );
  }
  function docEditor(label, area) {
    return h("div", { class: "field" }, h("div", { class: "btn-row", style: { justifyContent: "space-between" } }, h("label", { text: label, for: area.id || (area.id = `doc${Math.random().toString(36).slice(2, 7)}`) }), h("button", { type: "button", class: "btn ghost sm", text: "Copy", onclick: async () => { await navigator.clipboard.writeText(area.value); toast(`${label} copied.`, "success"); } })), area);
  }

  function renderHistory({ job, pane }) {
    const events = job.events || [];
    pane.append(h("h3", { text: "Application history" }), events.length ? h("ul", { class: "timeline" }, ...events.slice().reverse().map((e) => h("li", {}, h("strong", { text: `${e.previous_status ? fmt.title(fmt.stage(e.previous_status)) + " → " : ""}${fmt.title(fmt.stage(e.new_status))}` }), h("div", { text: e.reason }), h("small", { text: `${e.source} · ${e.actor} · ${fmt.datetime(e.occurred_at)} · ${e.correlation_id.slice(0, 8)}` })))) : ui.empty("No history yet", ""));
    if (job.submission) pane.append(h("div", { class: "card" }, h("h3", { text: "Submission record" }), ui.kv([["Submitted", fmt.datetime(job.submission.submitted_at)], ["Method", job.submission.method], ["Confirmation #", job.submission.confirmation_number || "—"], ["Evidence", job.submission.evidence_document_id ? h("a", { href: "#/documents", text: "Stored in Documents" }) : "None attached"], ["Correlation", job.submission.correlation_id]])));
    if (job.documents && job.documents.length) pane.append(h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Linked documents" }), ...job.documents.map((d) => h("div", { class: "row", style: { padding: "6px 0" } }, h("div", {}, h("span", { class: "title", text: d.title || d.filename }), h("span", { class: "sub", text: `${fmt.stage(d.kind)} · v${d.version} · ${fmt.ago(d.created_at)}` })), h("button", { type: "button", class: "btn ghost sm", text: "Download", onclick: () => Aira.download(`/api/documents/${d.document_id}/download`, d.filename) })))));
  }

  function toLocalInput(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  Aira.toLocalInput = toLocalInput;
})();

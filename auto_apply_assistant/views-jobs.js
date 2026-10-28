/* Aira — Jobs view, the shared job detail drawer, and Careers in the Aira frame (shell.js): what "+ New" offers, what
 * the search box across the top finds, and the icons of the app's menu folders. */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy, router } = Aira;

  // Careers is the account owner's job search. Another person in the household (a child, a partner) who opens it
  // gets "careers_owner_only" from the server: say whose it is and offer to switch person instead of an error.
  if (!ui.careersOwnerOnly) {
    const baseError = ui.error;
    ui.careersOwnerOnly = (error) => h("div", { class: "empty careers-owner-only", role: "status" },
      h("strong", { text: "This is someone else's job search" }),
      h("div", { text: error.message }),
      Aira.life && Aira.life.switchPerson ? h("div", { class: "btn-row", style: { justifyContent: "center", marginTop: "10px" } }, h("button", { type: "button", class: "btn", text: "Switch person", onclick: () => Aira.life.switchPerson() })) : null);
    ui.error = (error, retry) => (error && error.code === "careers_owner_only" ? ui.careersOwnerOnly(error) : baseError(error, retry));
  }

  // -------------------------------------------------------------------------
  // Careers in the Aira frame
  // -------------------------------------------------------------------------
  // Icons for the menu folders and "+ New" (Aira.ICONS has one per app section; these are Careers' own, prefixed).
  const svgIcon = (body) => `<svg viewBox="0 0 24 24">${body}</svg>`;
  const OWN_ICONS = {
    "careers-star": svgIcon('<path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>'),
    "careers-bell": svgIcon('<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>'),
    "careers-mail": svgIcon('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 6.5 8.5-6.5"/>'),
    "careers-bolt": svgIcon('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>'),
    "careers-globe": svgIcon('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>'),
    "careers-building": svgIcon('<path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 9h4a1 1 0 0 1 1 1v11M3 21h18M8 8h3M8 12h3M8 16h3"/>'),
    "careers-sparkle": svgIcon('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>'),
    "careers-link": svgIcon('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
    "careers-list": svgIcon('<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>'),
    "careers-check": svgIcon('<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16 10"/>'),
    "careers-coin": svgIcon('<circle cx="12" cy="12" r="9"/><path d="M15 9.2c-.5-1-1.6-1.6-3-1.6-1.7 0-3 .9-3 2.2 0 3 6 1.6 6 4.5 0 1.3-1.3 2.2-3 2.2-1.4 0-2.6-.6-3.1-1.7M12 6v1.6M12 16.4V18"/>'),
    "careers-history": svgIcon('<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v5l3 2"/>'),
    "careers-chat": svgIcon('<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9.5h8M8 12.5h5"/>'),
    "careers-plane": svgIcon('<path d="M21 15.5v-2l-8-5V4a1.5 1.5 0 0 0-3 0v4.5l-8 5v2l8-2.5v4.5L8 19v1.5l3.5-1 3.5 1V19l-2-1.5V13z"/>'),
    "careers-upload": svgIcon('<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>'),
    "careers-pen": svgIcon('<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>'),
  };
  for (const [name, markup] of Object.entries(OWN_ICONS)) if (!Aira.ICONS[name]) Aira.ICONS[name] = markup;

  // A page's tabs as folders in the left menu (each opens #/<section>?tab=<id>, which the view honors).
  Aira.careersFolders = (section, items, current) => { if (Aira.shell && Aira.shell.tabs) Aira.shell.tabs(section, items, current); };
  // The same tabs as a strip under the page heading: on wider screens the menu folders stand in for it (app-careers.css).
  Aira.careersStrip = (node) => { node.classList.add("careers-tabstrip"); return node; };

  // "+ New" opens a page with ?new=<kind>: the page opens its create form once and drops the parameter from the
  // address without drawing again, so Back and a reload do not open the form a second time.
  Aira.careersTakeNew = (route) => {
    const kind = route && route.query && route.query.new;
    if (!kind) return "";
    delete route.query.new;
    const qs = new URLSearchParams(route.query).toString();
    history.replaceState(history.state, "", `${location.pathname}${location.search}#/${route.path.map(encodeURIComponent).join("/")}${qs ? `?${qs}` : ""}`);
    return String(kind);
  };

  // Careers is one person's job search: another person's profile in a household, or a hosted role that may only
  // read, gets no "+ New" (the server would refuse the create anyway).
  const careersCan = (permission = "candidates.write") => {
    if (Aira.cloud && typeof Aira.cloud.can === "function") return Aira.cloud.can(permission);
    return !(Aira.life && Aira.life.me && Aira.life.me.owner === false);
  };
  Aira.appNew = Aira.appNew || {};
  Aira.appNew.careers = () => (careersCan() ? [
    { label: "Add a job", icon: "careers", href: "#/jobs/all?new=job" },
    { label: "Schedule an interview", icon: "interviews", href: "#/interviews?new=interview" },
    { label: "Add a recruiter", icon: "recruiters", href: "#/recruiters?tab=contacts&new=recruiter" },
    { label: "Upload a document", icon: "careers-upload", href: "#/documents?new=document" },
    careersCan("search.run") ? { label: "Create a job alert", icon: "careers-bell", href: "#/careers?tab=alerts&new=alert" } : null,
  ].filter(Boolean) : []);

  // The search box across the top: jobs, recruiters and interviews as you type; Enter filters the jobs list (or the
  // contacts, on Recruiters). Lists are read with the app's own endpoints; each answer is kept for a few seconds.
  const answers = new Map();
  const cached = (url, ms = 8000) => {
    const hit = answers.get(url);
    if (hit && Date.now() - hit.at < ms) return hit.promise;
    const promise = api(url, { silent: true }).catch(() => null);
    answers.set(url, { at: Date.now(), promise });
    if (answers.size > 40) answers.delete(answers.keys().next().value);
    return promise;
  };
  const folded = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  Aira.appSearch = Aira.appSearch || {};
  Aira.appSearch.careers = {
    placeholder: "Search jobs, people, interviews",
    async suggest(query) {
      const q = query.trim();
      if (!q) return [];
      const needle = folded(q);
      const [jobs, people, meetings] = await Promise.all([
        cached(`/api/jobs?${new URLSearchParams({ view: "all", q, page_size: "6", sort: "updated" })}`),
        cached(`/api/recruiters?q=${encodeURIComponent(q)}`),
        cached("/api/interviews?scope=all", 20000), // one list, filtered here: people have few interviews
      ]);
      const jobRows = ((jobs && jobs.jobs) || []).map((job) => ({ label: job.title || `Job ${job.job_id}`, sub: ["Job", job.company, job.location].filter(Boolean).join(" · "), icon: "careers", href: `#/jobs/all/job/${encodeURIComponent(job.job_id)}` }));
      const personRows = ((people && people.recruiters) || []).map((person) => ({ label: person.name, sub: ["Recruiter", person.title, person.company].filter(Boolean).join(" · "), icon: "recruiters", href: `#/recruiters/${encodeURIComponent(person.recruiter_id)}` }));
      const meetingRows = ((meetings && meetings.interviews) || [])
        .filter((item) => folded([item.company, item.role, item.job_title, item.recruiter_name, fmt.stage(item.interview_type)].join(" ")).includes(needle))
        .map((item) => ({ label: `${fmt.title(fmt.stage(item.interview_type))} · ${item.company || item.job_title || "Interview"}`, sub: ["Interview", fmt.datetime(item.start_at, item.timezone), item.status === "scheduled" ? "" : item.status].filter(Boolean).join(" · "), icon: "interviews", href: `#/interviews/${encodeURIComponent(item.interview_id)}` }));
      // Every kind gets a place in the six; jobs fill whatever the others leave.
      const picked = [...jobRows.slice(0, 3), ...personRows.slice(0, 2), ...meetingRows.slice(0, 2)];
      return [...picked, ...jobRows.slice(3)].slice(0, 6);
    },
    run(query) {
      const q = query.trim();
      const here = router.current && router.current.path[0];
      if (here === "recruiters") router.go(["recruiters"], { tab: "contacts", q });
      else router.go(["jobs", "all"], { q });
    },
  };

  const JOB_TABS = [
    { id: "all", label: "All Jobs", view: "all", icon: "careers-list" },
    { id: "linkedin", label: "LinkedIn", view: "linkedin", icon: "careers-link" },
    { id: "aira", label: "Jobs", view: "aira_jobs", icon: "careers" },
    { id: "google", label: "Google Jobs", view: "google_jobs", icon: "careers-globe" },
    { id: "portals", label: "Company Portals", view: "portals", icon: "careers-building" },
    { id: "recommended", label: "Recommended", view: "recommended", icon: "careers-sparkle" },
    { id: "saved", label: "Saved", view: "saved", icon: "careers-star" },
    { id: "autopilot", label: "Auto-apply", view: "", icon: "careers-bolt" },
  ];
  const STAGES = ["discovered", "saved", "ready_to_apply", "applying", "needs_review", "applied", "assessment", "recruiter_screen", "technical_interview", "manager_interview", "panel_interview", "final_interview", "offer", "accepted", "rejected", "withdrawn", "failed"];
  const CLASSES = ["Recommended", "Strong Match", "Good Match", "Stretch", "Not Recommended"];
  const SUBMISSION_METHODS = { manual: "You sent Monosage's application", assisted: "Monosage filled it in, you sent it", auto: "Monosage applied for you", outside: "You applied another way" };

  // The source catalog is read again on every visit to Jobs: a SerpApi key connected in API, or a LinkedIn session
  // that just came up, must show here without a reload.
  let sourcesLoad = null;
  function sources() {
    if (!sourcesLoad) sourcesLoad = api("/api/sources", { silent: true }).then((data) => (Aira.state.sources = data.sources), (error) => { sourcesLoad = null; throw error; });
    return sourcesLoad;
  }
  const KEY_PAGE = "#/api?tab=account";

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
      if (chosen.source === "google_jobs" && !chosen.connected) hint.replaceChildren(`${chosen.label}: `, h("a", { href: KEY_PAGE, text: "connect a SerpApi key in API > Account" }), " (or set SERPAPI_API_KEY in .env), or search ", h("a", { href: "#/jobs/aira", text: "Jobs" }), " for free.");
      else if (chosen.source === "aira_jobs") hint.textContent = "Jobs searches free job boards (Himalayas, The Muse, Remotive, Jobicy, Arbeitnow) itself: no key, no credits. Each result links back to its board.";
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
      toast(`Imported from ${Aira.fmt.source(data.source)}.`, "success");
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
        h("div", { class: "meta" }, ...(job.sources || [job.source]).map(sourceBadge), ui.stage(job.stage), ui.classification(job.match_classification), job.easy_apply ? h("span", { class: "badge easy", text: "Easy Apply" }) : null, job.workplace_type ? h("span", { class: "badge", text: fmt.stage(job.workplace_type) }) : null, salary ? h("span", { class: "badge", text: salary }) : null, job.saved ? h("span", { class: "badge", text: "★ Saved" }) : null, job.documents_ready ? h("span", { class: "badge", text: "Drafts ready" }) : null),
      ),
      h("div", { class: "aside" }, ui.score(job.match_score), h("small", { class: "muted", text: job.posted_at ? `Posted ${fmt.date(job.posted_at)}` : fmt.ago(job.updated_at) })),
    );
    return row;
  }

  // A job typed in by hand shows "Added by you" instead of the internal source name.
  const sourceBadge = (source) => ui.source(source === "manual" ? "added_by_you" : source);
  Aira.sourceBadge = sourceBadge;

  Aira.views.jobs = async (main, route) => {
    sourcesLoad = null;
    const making = Aira.careersTakeNew(route);
    // A tab is #/jobs/<tab>; a menu folder links #/jobs?tab=<tab>, which opens the same place.
    const { tab: tabQuery, ...asked } = route.query;
    const tabId = route.path[1] || tabQuery || "all";
    const tab = JOB_TABS.find((item) => item.id === tabId) || JOB_TABS[0];
    Aira.careersFolders("jobs", JOB_TABS.map(({ id, label, icon }) => ({ id, label, icon })), tab.id);
    // A link that names filters (the search box across the top, a metric in Analytics) shows exactly those; a plain
    // visit brings back the filters last used on this tab.
    const stored = Object.keys(asked).length ? {} : Aira.recall(`filters.jobs.${tab.id}`, {});
    const filters = { q: "", stage: "", classification: "", min_score: "", sort: tab.id === "recommended" ? "score" : "updated", page: "1", workplace_type: "", source: "", ...stored, ...asked };
    const listBox = h("div", { class: "panel" });
    const subnav = h("nav", { class: "subnav careers-tabstrip", "aria-label": "Job sources" }, ...JOB_TABS.map((item) => h("a", { href: `#/jobs/${item.id}`, "aria-current": item.id === tab.id ? "page" : "false", text: item.label })));
    const addJob = () => manualJobForm((job) => { if (Aira.state.views.jobsReload) Aira.state.views.jobsReload(); openJob(job.job_id); });

    const q = h("input", { type: "search", "data-search": "1", placeholder: "Filter by title, company, or location", value: filters.q });
    const stage = ui.select([["", "Any status"], ...STAGES.map((s) => [s, fmt.title(fmt.stage(s))])], filters.stage);
    const classification = ui.select([["", "Any match"], ...CLASSES.map((c) => [c, c])], filters.classification);
    const minScore = ui.select([["", "Any score"], ["85", "85+"], ["70", "70+"], ["55", "55+"], ["40", "40+"]], filters.min_score);
    const workplace = ui.select([["", "Any workplace"], ["remote", "Remote"], ["hybrid", "Hybrid"], ["on_site", "On-site"]], filters.workplace_type);
    const sort = ui.select([["updated", "Recently updated"], ["score", "Best match"], ["posted", "Newest posting"], ["company", "Company A–Z"], ["title", "Title A–Z"], ["stage", "Status"]], filters.sort);
    const current = (page) => ({ q: q.value, stage: stage.value, classification: classification.value, min_score: minScore.value, workplace_type: workplace.value, sort: sort.value, page, ...(filters.source ? { source: filters.source } : {}) });
    const apply = () => {
      const next = current("1");
      Aira.remember(`filters.jobs.${tab.id}`, next);
      router.go(["jobs", tab.id], next, { replace: true });
    };
    for (const control of [stage, classification, minScore, workplace, sort]) control.addEventListener("change", apply);
    let timer;
    q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(apply, 350); });
    // A link from Analytics can narrow the list to one source; a chip shows it and clears it.
    const sourceChip = filters.source ? h("div", { class: "btn-row careers-filter-chip" }, h("span", { class: "badge source", text: `Source: ${Aira.fmt.source((filters.source === "manual" ? "added by you" : filters.source))}` }), h("button", { type: "button", class: "btn ghost sm", text: "Show every source", onclick: () => { filters.source = ""; apply(); } })) : null;
    const filterBar = h("div", { class: "filters careers-compact-filters", role: "search" }, h("div", { class: "field grow" }, h("label", { for: "jobFilterQ", text: "Filter" }), Object.assign(q, { id: "jobFilterQ" })), ui.field("Status", stage), ui.field("Match", classification), ui.field("Score", minScore), ui.field("Workplace", workplace), ui.field("Sort", sort), h("button", { type: "button", class: "btn ghost sm", text: "Clear", onclick: () => { Aira.remember(`filters.jobs.${tab.id}`, {}); router.go(["jobs", tab.id], {}, { replace: true }); } }));

    const head = h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Jobs" }), h("p", { text: "Search every connected source, import postings by URL, and keep one deduplicated pipeline. Scores are explainable and never guess at missing facts." })), h("div", { class: "page-actions" }, h("button", { type: "button", class: "btn sm", text: "+ Add a job", onclick: addJob }), h("button", { type: "button", class: "btn secondary sm", text: "Rescore all", onclick: (event) => withBusy(event.currentTarget, async () => { const data = await api("/api/jobs/rescore", { method: "POST", body: {} }); toast(`${fmt.plural(data.scored, "job")} rescored.`, "success"); load(); }) })));

    if (tab.id === "autopilot") {
      const host = h("div");
      main.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Jobs" }), h("p", { text: "Auto-apply: tell Monosage which positions you want. On a schedule it searches, scores every job against your profile and applies to the best matches, within your limits." }))), subnav, host);
      await Aira.renderAutopilot(host);
      if (making === "job") addJob();
      return;
    }
    const showSearch = !["recommended", "saved"].includes(tab.id);
    let finder = showSearch ? searchPanel(tab, (data) => { if (data.imported) { load().then(() => openJob(data.imported.job_id, { loadDetails: data.needs_details })); } else { load(); if (data.jobs[0]) openJob(data.jobs[0].job_id, { loadDetails: !data.jobs[0].description }); } }) : null;
    // On a phone the long search form folds away, so the jobs themselves are in view; it opens with one tap.
    if (finder && window.matchMedia && window.matchMedia("(max-width: 640px)").matches) finder = h("details", { class: "careers-finder", open: !filters.q && Aira.recall("careers.jobs.finderOpen", false), ontoggle: (event) => Aira.remember("careers.jobs.finderOpen", event.currentTarget.open) }, h("summary", {}, Aira.icon("jobs"), h("span", { text: tab.id === "all" ? "Search job boards or import a posting" : `Search ${tab.label}` }), h("span", { class: "careers-finder-chevron", "aria-hidden": "true", html: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>' })), finder);
    main.replaceChildren(...[head, subnav, finder || h("p", { class: "note", text: tab.id === "recommended" ? "Jobs classified Recommended or Strong Match by the explainable scorer, ranked by score. Load a description and your profile to score more jobs." : "Jobs you starred, plus anything in the Saved status." }), tab.id === "google" ? h("p", { class: "muted", style: { margin: "-6px 0 14px", fontSize: "12.5px" } }, "Want Google's own filters, job alerts and company research? ", h("a", { href: "#/careers?tab=jobs", text: "Open Research" }), ".") : tab.id === "aira" ? h("p", { class: "muted", style: { margin: "-6px 0 14px", fontSize: "12.5px" } }, "Monosage's own job search, built into API: no SerpApi key and no credits. Every search is kept in ", h("a", { href: "#/api?tab=history&engine=aira_jobs", text: "API history" }), ".") : null, sourceChip, filterBar, listBox].filter(Boolean));

    async function load() {
      listBox.replaceChildren(ui.skeleton(6));
      try {
        const params = { view: tab.view, source: filters.source, q: q.value, stage: stage.value, classification: classification.value, min_score: minScore.value, workplace_type: workplace.value, sort: sort.value, page: filters.page, page_size: "40" };
        const data = await api(`/api/jobs?${new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, v]) => v)))}`);
        // The Google Jobs tab explains a missing key instead of inviting a search that cannot run.
        const googleMissing = tab.id === "google" && !data.jobs.length && !(await sources().catch(() => [])).some((item) => item.source === "google_jobs" && item.connected);
        renderList(data, googleMissing);
      } catch (error) { listBox.replaceChildren(ui.error(error, load)); }
    }
    function renderList(data, googleMissing = false) {
      const list = h("div", { class: "list", "data-roving": "1", role: "list" });
      const selectedId = route.path[3];
      const narrowed = Boolean(q.value || stage.value || classification.value || minScore.value || workplace.value || filters.source);
      if (googleMissing) {
        list.append(ui.empty("Google Jobs needs a SerpApi key", "Connect a key once and Google Jobs searches here, in Careers and in your own scripts all use it. It is stored encrypted on this computer. No key? Jobs searches free job boards without one.", h("div", { class: "btn-row", style: { justifyContent: "center" } }, h("a", { class: "btn", href: "#/jobs/aira", text: "Search Jobs (free)" }), h("a", { class: "btn secondary", href: KEY_PAGE, text: "Connect a key in API" }))));
      } else if (!data.jobs.length && narrowed) {
        list.append(ui.empty(q.value ? `No jobs match “${q.value}”` : "No jobs match these filters", "Try other words, or clear the filters to see every job on this tab.", h("button", { type: "button", class: "btn secondary", text: "Clear filters", onclick: () => { Aira.remember(`filters.jobs.${tab.id}`, {}); router.go(["jobs", tab.id], {}, { replace: true }); } })));
      } else if (!data.jobs.length) {
        const message = tab.id === "recommended" ? "Score jobs to see recommendations here: add a job description and complete your profile." : tab.id === "saved" ? "Star a job in any list to keep it here." : "Search above, import a posting by URL, or add a job you heard about yourself.";
        list.append(ui.empty("No jobs yet", message, tab.id === "recommended" || tab.id === "saved" ? h("a", { class: "btn", href: "#/jobs/all", text: "See all jobs" }) : h("button", { type: "button", class: "btn", text: "+ Add a job", onclick: addJob })));
      }
      for (const job of data.jobs) list.append(jobRow(job, { selected: job.job_id === selectedId, onSelect: (item) => openJob(item.job_id, { loadDetails: !item.has_description }) }));
      const turn = (page) => { filters.page = String(page); router.go(["jobs", tab.id], current(filters.page), { replace: true }); };
      const pager = h("div", { class: "pager" }, h("span", { text: `${data.total} ${data.total === 1 ? "job" : "jobs"} · page ${data.page} of ${data.pages}` }), h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", text: "Previous", disabled: data.page <= 1, onclick: () => turn(data.page - 1) }), h("button", { type: "button", class: "btn ghost sm", text: "Next", disabled: data.page >= data.pages, onclick: () => turn(data.page + 1) })));
      const heading = q.value ? `${tab.id === "all" ? "Jobs" : tab.label} matching “${q.value}”` : tab.label;
      listBox.replaceChildren(h("div", { class: "panel-head" }, h("h3", { text: heading }), h("small", { class: "muted", text: data.facets && data.facets.sources.length ? data.facets.sources.map((s) => `${s.source === "manual" ? "added by you" : Aira.fmt.source(s.source)} ${s.count}`).join(" · ") : "" })), list, pager);
    }
    Aira.state.views.jobsReload = load;
    if (making === "job") addJob();
    await load();
    if (route.path[2] === "job" && route.path[3]) openJob(route.path[3]);
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
    sub.replaceChildren(h("span", { text: [job.company, job.location].filter(Boolean).join(" · ") }), " ", ...(job.sources || []).map(sourceBadge), " ", ui.stage(job.stage), " ", ui.classification(job.match_classification) || "");
    if (!sub.parentElement) panel.head.firstElementChild.append(sub);
    const applyUrl = job.application_url || job.job_url;
    const headActions = h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn secondary sm", text: job.saved ? "★ Saved" : "☆ Save", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/save`, { method: "POST", body: { saved: !job.saved } }); toast(job.saved ? "Removed from saved jobs." : "Saved.", "success"); reload(); }) }),
      job.has_posting === false ? null : h("a", { class: "btn secondary sm", href: applyUrl, target: "_blank", rel: "noopener noreferrer", text: job.application_url && job.application_url !== job.job_url ? "Open application ↗" : "Open posting ↗" }),
    );
    const tabs = ui.tabs([{ id: "overview", label: "Overview" }, { id: "fit", label: "Fit & evidence" }, { id: "documents", label: "Documents" }, { id: "apply", label: "Apply" }, { id: "history", label: "History" }], activeTab, (id) => renderJob(panel, job, id));
    const pane = h("div", {});
    const headRow = panel.head.querySelector(".btn-row");
    for (const stale of headRow.querySelectorAll("[data-job-action]")) stale.remove();
    for (const action of [...headActions.children].filter(Boolean)) { action.dataset.jobAction = "1"; headRow.insertBefore(action, headRow.lastElementChild); }
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
      ["Source", (job.source_records || []).map((r) => (r.source === "manual" ? "Added by you" : `${Aira.fmt.source(r.source)} · ${r.external_id}`)).join(", ")],
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
    const byHand = job.source === "manual";
    const editJob = () => manualJobForm(() => reload("overview"), job);
    const noDescription = byHand
      ? [h("span", { text: "Write a few lines about the job (duties, skills, hours) and Monosage scores it like any other." }), h("div", { class: "btn-row", style: { justifyContent: "center", marginTop: "8px" } }, h("button", { type: "button", class: "btn", text: "Add a description", onclick: editJob }))]
      : (job.sources || []).includes("linkedin")
        ? [h("button", { type: "button", class: "btn", text: "Read posting from LinkedIn", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/details`, { method: "POST", body: {} }); toast("Description loaded and scored.", "success"); reload(); }) })]
        : [h("span", { text: job.has_posting === false ? "This job has no description." : "This source did not include a description. Open the posting to read it." })];
    const descriptionBox = job.description ? h("div", { class: "description", text: job.description }) : h("div", { class: "empty" }, h("strong", { text: "No description yet" }), ...noDescription);
    const required = (job.required_skills || []).slice(0, 25);
    const preferred = (job.preferred_skills || []).slice(0, 15);
    pane.append(
      h("div", { class: "grid sidebar-right" },
        h("div", {},
          h("div", { class: "btn-row", style: { marginBottom: "12px" } }, h("div", { class: "field", style: { minWidth: "220px" } }, h("label", { for: "stageSelect", text: "Status" }), Object.assign(stageSelect, { id: "stageSelect" })), h("button", { type: "button", class: "btn", text: "Prepare application →", onclick: () => renderJob(panel_of(pane), job, "apply") }), byHand ? h("button", { type: "button", class: "btn secondary sm", text: "Edit job", onclick: editJob }) : null, h("button", { type: "button", class: "btn ghost sm danger", text: "Archive", onclick: async (event) => { if (await Aira.confirm({ title: "Archive this job?", message: "It disappears from lists and analytics. History is kept.", confirmLabel: "Archive", danger: true })) { await withBusy(event.currentTarget, async () => { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/archive`, { method: "POST", body: {} }); toast("Job archived.", "success"); Aira.closeDrawer(); }); } } })),
          h("h3", { text: "Job description" }), descriptionBox,
          required.length ? h("div", { style: { marginTop: "12px" } }, h("h3", { text: "Skills mentioned" }), h("div", { class: "chips" }, ...required.map((s) => h("span", { class: "chip on", text: fmt.stage(s) })), ...preferred.map((s) => h("span", { class: "chip", text: `${fmt.stage(s)} (preferred)` })))) : null,
        ),
        h("div", {},
          h("div", { class: "card" }, h("h3", { text: "At a glance" }), facts),
          offerCard(job, reload),
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
    if (job.submission) pane.append(h("div", { class: "card" }, h("h3", { text: "Submission record" }), ui.kv([["Submitted", fmt.datetime(job.submission.submitted_at)], ["How", SUBMISSION_METHODS[job.submission.method] || job.submission.method], ["Confirmation #", job.submission.confirmation_number || "—"], ["Evidence", job.submission.evidence_document_id ? h("a", { href: "#/documents", text: "Stored in Documents" }) : "None attached"], ["Correlation", job.submission.correlation_id]])));
    if (job.documents && job.documents.length) pane.append(h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: "Linked documents" }), ...job.documents.map((d) => h("div", { class: "row", style: { padding: "6px 0" } }, h("div", {}, h("span", { class: "title", text: d.title || d.filename }), h("span", { class: "sub", text: `${fmt.stage(d.kind)} · v${d.version} · ${fmt.ago(d.created_at)}` })), h("button", { type: "button", class: "btn ghost sm", text: "Download", onclick: () => Aira.download(`/api/documents/${d.document_id}/download`, d.filename) })))));
  }

  // -------------------------------------------------------------------------
  // Offer: what the employer offered, as numbers that can be compared (Applications > Offers)
  // -------------------------------------------------------------------------
  const OFFER_STAGES = ["applied", "assessment", "recruiter_screen", "technical_interview", "manager_interview", "panel_interview", "final_interview", "offer", "accepted"];
  function offerCard(job, reload) {
    const offer = job.offer;
    if (!offer && !OFFER_STAGES.includes(job.stage)) return null;
    const card = h("div", { class: "card careers-offer-card", style: { marginTop: "12px" } }, h("h3", { text: "Offer" }));
    if (!offer) {
      card.append(h("p", { class: "muted", text: "Got an offer? Write down the pay, bonus and benefits to compare offers and plan your answer." }), h("button", { type: "button", class: "btn sm", text: "Record the offer", onclick: () => offerForm(job, () => reload("overview")) }));
      return card;
    }
    card.append(ui.kv([
      ["Base pay", `${money(offer.base_salary, offer.currency)} ${PER[offer.pay_period] || ""}`],
      ["Pay a year", money(offer.yearly_total, offer.currency)],
      ["First-year pay", money(offer.first_year_total, offer.currency)],
      ["Status", fmt.title(offer.status)],
      ["Answer by", offer.deadline ? fmt.date(offer.deadline) : null],
    ]), h("div", { class: "btn-row", style: { marginTop: "10px" } }, h("button", { type: "button", class: "btn secondary sm", text: "Edit offer", onclick: () => offerForm(job, () => reload("overview")) }), h("a", { class: "btn ghost sm", href: "#/applications?tab=offers", text: "Compare offers", onclick: () => Aira.closeDrawer() })));
    return card;
  }
  const PER = { year: "a year", month: "a month", hour: "an hour" };
  function money(value, currency) {
    if (value === null || value === undefined || value === "") return "";
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", maximumFractionDigits: Number(value) % 1 ? 2 : 0 }).format(Number(value)); }
    catch { return `${currency || ""} ${Number(value).toLocaleString()}`.trim(); }
  }
  Aira.careersMoney = money;
  Aira.careersPer = PER;

  function offerForm(job, onSaved) {
    const v = job.offer || {};
    const profile = (Aira.state.profile && Aira.state.profile.candidate_profile) || {};
    const num = (value, label) => h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", value: value || value === 0 ? String(value) : "", "aria-label": label });
    const base = num(v.base_salary, "Base pay");
    const period = ui.select([["year", "A year"], ["month", "A month"], ["hour", "An hour"]], v.pay_period || "year");
    const hours = num(v.hours_per_week || 40, "Hours a week");
    const currency = h("input", { type: "text", maxlength: 3, value: v.currency || profile.salary_currency || job.currency || "USD", style: { textTransform: "uppercase" } });
    const bonus = num(v.bonus, "Bonus a year");
    const equity = num(v.equity_per_year, "Equity a year");
    const signOn = num(v.sign_on, "Sign-on payment");
    const benefits = num(v.benefits, "Benefits value a year");
    const start = h("input", { type: "date", value: v.start_date || "" });
    const deadline = h("input", { type: "date", value: v.deadline || "" });
    const status = ui.select([["considering", "Thinking about it"], ["negotiating", "Negotiating"], ["accepted", "Accepted"], ["declined", "Declined"], ["expired", "Expired"]], v.status || "considering");
    const notes = h("textarea", { rows: 3, maxlength: 4000, placeholder: "Health insurance, holidays, remote days, anything else that matters" }, v.notes || "");
    const hoursField = ui.field("Hours a week", hours, "Used to work out pay a year for hourly pay.");
    const syncHours = () => { hoursField.hidden = period.value !== "hour"; };
    period.addEventListener("change", syncHours);
    syncHours();
    notes.id = "offerNotes";
    const body = h("div", {},
      h("p", { class: "muted", style: { marginTop: "0" }, text: "Only the base pay is needed. Add what you know; Monosage adds it up so offers can be compared fairly." }),
      h("div", { class: "form-grid" }, ui.field("Base pay", base), ui.field("Paid", period), hoursField, ui.field("Currency", currency, "Three letters, like USD, GBP or INR."), ui.field("Yearly bonus", bonus), ui.field("Stock or equity a year", equity), ui.field("Sign-on payment", signOn, "Paid once."), ui.field("Benefits worth a year", benefits, "Your own estimate, for example health cover."), ui.field("Start date", start), ui.field("Answer by", deadline), ui.field("Status", status), h("div", { class: "field wide" }, h("label", { for: "offerNotes", text: "Notes" }), notes)),
    );
    const actions = [{ label: "Cancel", kind: "ghost" }];
    if (job.offer) actions.push({ label: "Remove offer", kind: "ghost danger", onClick: async () => {
      try { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/notes`, { method: "PATCH", body: { offer: null } }); toast("Offer removed.", "success"); onSaved(); }
      catch (error) { Aira.reportError(error); return false; }
    } });
    actions.push({ label: job.offer ? "Save offer" : "Record offer", kind: "", onClick: async () => {
      const offer = { base_salary: base.value, pay_period: period.value, hours_per_week: hours.value, currency: currency.value.trim().toUpperCase(), bonus: bonus.value, equity_per_year: equity.value, sign_on: signOn.value, benefits: benefits.value, start_date: start.value, deadline: deadline.value, status: status.value, notes: notes.value };
      try { await api(`/api/jobs/${encodeURIComponent(job.job_id)}/notes`, { method: "PATCH", body: { offer } }); toast("Offer saved.", "success"); onSaved(); }
      catch (error) { Aira.reportError(error); return false; }
    } });
    Aira.modal({ title: `Offer · ${job.title || "Job"}${job.company ? ` at ${job.company}` : ""}`, body, wide: true, actions });
    base.focus();
  }
  Aira.offerForm = offerForm;

  // -------------------------------------------------------------------------
  // A job added by hand: a referral, a sign in a window, a phone call (no posting needed)
  // -------------------------------------------------------------------------
  function manualJobForm(onSaved, existing = null) {
    const was = existing || {};
    const title = h("input", { type: "text", maxlength: 200, required: true, placeholder: "e.g. Store manager", value: was.title || "" });
    const company = h("input", { type: "text", maxlength: 160, required: true, placeholder: "e.g. Corner Bakery", value: was.company || "" });
    const location = h("input", { type: "text", maxlength: 200, placeholder: "City, or Remote", value: was.location || "" });
    const url = h("input", { type: "url", maxlength: 1000, placeholder: "https://… (leave empty if there is no posting)", value: existing && existing.has_posting !== false ? existing.job_url || "" : "" });
    const workplace = ui.select([["", "Not sure"], ["on_site", "On-site"], ["hybrid", "Hybrid"], ["remote", "Remote"]], was.workplace_type || "");
    const employment = ui.select([["", "Not sure"], ["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"], ["temporary", "Temporary"], ["internship", "Internship"]], was.employment_type || "");
    const low = h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", "aria-label": "Lowest pay a year", value: was.salary_min == null ? "" : String(was.salary_min) });
    const high = h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", "aria-label": "Highest pay a year", value: was.salary_max == null ? "" : String(was.salary_max) });
    const currency = h("input", { type: "text", maxlength: 3, value: was.currency || ((Aira.state.profile && Aira.state.profile.candidate_profile) || {}).salary_currency || "USD", style: { textTransform: "uppercase" } });
    const description = h("textarea", { rows: 5, maxlength: 50000, placeholder: "Paste or write what you know about the job: duties, skills, hours. Monosage scores the job from this." }, was.description || "");
    description.id = "manualJobDescription";
    const body = h("div", {},
      h("p", { class: "muted", style: { marginTop: "0" }, text: existing ? "Correct anything you typed. The job keeps its history, documents and offer." : "For a job you heard about from a friend, saw in a shop window or found on a site Monosage does not search." }),
      h("div", { class: "form-grid" }, ui.field("Job title", title), ui.field("Company or employer", company), ui.field("Location", location), ui.field("Link to the posting", url), ui.field("Workplace", workplace), ui.field("Employment", employment), ui.field("Lowest pay a year", low), ui.field("Highest pay a year", high), ui.field("Currency", currency), h("div", { class: "field wide" }, h("label", { for: "manualJobDescription", text: "About the job" }), description)),
    );
    Aira.modal({ title: existing ? "Edit this job" : "Add a job", body, wide: true, actions: [{ label: "Cancel", kind: "ghost" }, { label: existing ? "Save changes" : "Add job", kind: "", onClick: async () => {
      if (!title.value.trim() || !company.value.trim()) { toast("Enter the job title and the company.", "error"); (title.value.trim() ? company : title).focus(); return false; }
      const fields = { title: title.value, company: company.value, location: location.value, url: url.value.trim(), workplace_type: workplace.value, employment_type: employment.value, salary_min: low.value, salary_max: high.value, currency: currency.value.trim().toUpperCase(), description: description.value };
      try {
        if (existing) {
          const data = await api(`/api/jobs/${encodeURIComponent(existing.job_id)}/notes`, { method: "PATCH", body: { job: fields } });
          toast("Job updated.", "success");
          onSaved(data.job);
          return undefined;
        }
        const data = await api("/api/jobs/import", { method: "POST", body: { manual: fields } });
        // A link Aira already tracks opens that job instead of making a second one.
        if (data.existing) toast(`Monosage already tracks this link: ${[data.job.title, data.job.company].filter(Boolean).join(" at ")}. It is open now; nothing new was added.`, "info", { timeout: 8000 });
        else toast("Job added.", "success");
        onSaved(data.job);
      } catch (error) { Aira.reportError(error); return false; }
      return undefined;
    } }] });
    title.focus();
  }
  Aira.manualJobForm = manualJobForm;

  // -------------------------------------------------------------------------
  // Job picker: choose a job by its title and company instead of typing an internal id
  // -------------------------------------------------------------------------
  function jobPicker(value = "", { placeholder = "Start typing a job title or company (optional)" } = {}) {
    const listId = `jobPick${Math.random().toString(36).slice(2, 8)}`;
    const input = h("input", { type: "text", id: `${listId}Input`, list: listId, autocomplete: "off", maxlength: 300, placeholder });
    const datalist = h("datalist", { id: listId });
    const hint = h("span", { class: "hint careers-picker-hint", "aria-live": "polite", text: "" });
    const wrap = h("div", { class: "careers-job-picker" }, input, datalist, hint);
    const byLabel = new Map();
    const known = new Set();
    const label = (job) => [job.title || job.job_id, job.company].filter(Boolean).join(" · ");
    const add = (job) => {
      if (known.has(job.job_id)) return;
      known.add(job.job_id);
      let text = label(job);
      if (byLabel.has(text)) text = `${text} (${job.job_id.slice(-6)})`;
      byLabel.set(text, job.job_id);
      datalist.append(h("option", { value: text }));
      if (value && job.job_id === value) input.value = text;
    };
    const load = (query) => api(`/api/jobs?view=all&page_size=${query ? 50 : 200}&sort=updated${query ? `&q=${encodeURIComponent(query)}` : ""}`, { silent: true }).then((data) => { data.jobs.forEach(add); return data; });
    load("").then(() => {
      // A linked job older than the latest 200 is looked up by its id.
      if (value && !known.has(value)) return api(`/api/jobs/${encodeURIComponent(value)}`, { silent: true }).then((data) => add(data.job)).catch(() => {});
      return undefined;
    }).catch(() => {}).finally(() => { if (value && !input.value) input.value = value; });
    // Typing searches every job (title, company or place), not only the ones loaded first.
    let timer = 0;
    let lastQuery = "";
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const text = input.value.trim();
      if (byLabel.has(text)) { hint.textContent = ""; return; }
      const query = text.replace(/\s+\([0-9a-z]{6}\)$/i, "").split(" · ")[0].trim();
      if (query.length < 2 || query === lastQuery) return;
      timer = setTimeout(() => {
        lastQuery = query;
        load(query).then((data) => { hint.textContent = data.total ? "" : "No job matches. Leave it empty, or add the job in Jobs first."; }).catch(() => {});
      }, 250);
    });
    // The chosen job's id: "" when empty; an id typed directly still works; anything else is an error.
    wrap.jobId = () => {
      const text = input.value.trim();
      if (!text) return "";
      if (byLabel.has(text)) return byLabel.get(text);
      if (known.has(text) || text === value) return text;
      input.focus();
      throw new Error("Pick the job from the suggestions as you type, or leave it empty.");
    };
    wrap.input = input;
    return wrap;
  }
  Aira.jobPicker = jobPicker;
  // A labelled form field holding a job picker (the label focuses the text box).
  Aira.jobPickerField = (label, picker, hint) => h("div", { class: "field" }, h("label", { for: picker.input.id, text: label }), picker, hint ? h("span", { class: "hint", text: hint }) : null);

  function toLocalInput(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  Aira.toLocalInput = toLocalInput;
})();

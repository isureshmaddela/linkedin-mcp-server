/* Aira — Leads: find, qualify, enrich and track sales leads.
 *
 * One view (#/leads) with a tab per workspace (?tab=): find (default), leads, settings (the ideal customer profile)
 * and history. Everything runs through /api/leads/* (aira/airaapi/leads_routes.py).
 *
 * - Find: pick a source (local businesses on Google Maps, people or companies through Google, hiring signals,
 *   your own LinkedIn Sales Navigator, a CSV file), see what the search will cost, run it, and save the leads you
 *   want into a list. A search only previews; nothing is saved until you say so.
 * - Leads: your saved leads with filters, bulk actions, export (CSV / XLSX) and a drawer for each lead where you edit
 *   it, read its fit score, enrich it from the company's own public website and draft (never send) a message.
 * - Settings: the ideal customer profile that every lead is scored against, and the services you offer.
 * - Outreach (views-leads-outreach.js): the email outbox (drafts, approval, sending through your own mail server), the
 *   sender settings and the do-not-contact list. Calls are prepared and logged in the lead drawer; Aira never dials.
 * - History: every search you ran, with what it found, added and cost.
 *
 * Deep links: #/leads?tab=find&source=maps&query=dentists&location=Austin prefills a search form (it never runs it).
 *
 * The file has two halves. The first is pure logic (form validation, request building, query strings, merging
 * results). It touches no DOM and is exported as Aira.leads.logic and, in node, through CommonJS so
 * tests/js/leads.test.mjs can exercise it. The second builds DOM with the h() helper and textContent only: nothing the
 * server sends is ever parsed as HTML. External links open in a new tab with rel="noopener noreferrer".
 */
"use strict";
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const Aira = root.Aira || (root.Aira = { views: {} });
  const hasDom = typeof document !== "undefined";

  // ===========================================================================
  // Pure logic
  // ===========================================================================

  const isEmpty = (value) => value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
  const clean = (obj) => Object.fromEntries(Object.entries(obj || {}).filter(([, v]) => !isEmpty(v) && v !== false));
  const trim = (value) => String(value === undefined || value === null ? "" : value).replace(/\s+/g, " ").trim();
  const plural = (n, word) => `${n} ${n === 1 ? word : /(s|x|ch|sh)$/i.test(word) ? `${word}es` : `${word}s`}`;

  const STATUSES = [["new", "New"], ["contacted", "Contacted"], ["replied", "Replied"], ["qualified", "Qualified"], ["disqualified", "Disqualified"], ["customer", "Customer"]];
  const SORTS = [["updated", "Recently updated"], ["score", "Best fit"], ["name", "Name"], ["company", "Company"], ["created", "Recently added"]];
  const SOURCE_LABELS = { google_maps: "Google Maps", google_web: "Google (LinkedIn pages)", google_jobs: "Hiring signals", sales_navigator: "Sales Navigator", import: "CSV import", manual: "Added by hand", google_news: "Company news", google_intent: "People asking for a service" };
  const SALES_URL = /^https:\/\/www\.linkedin\.com\/sales\/(search\/people|lists\/people)/i;

  /** The sources of the Find tab. `api` and `mode` are what the server calls them. */
  const SOURCES = [
    { id: "maps", api: "google_maps", mode: "", label: "Local businesses", detail: "Google Maps: businesses with phone and website", needsKey: true },
    { id: "requests", api: "google_intent", mode: "", label: "People asking", detail: "Public posts where someone asks for a website, marketing, content or ads service", needsKey: true },
    { id: "people", api: "google_web", mode: "people", label: "People", detail: "LinkedIn profiles found through Google", needsKey: true },
    { id: "companies", api: "google_web", mode: "companies", label: "Companies", detail: "LinkedIn company pages found through Google", needsKey: true },
    { id: "hiring", api: "google_jobs", mode: "", label: "Hiring signals", detail: "Companies hiring for a role you sell into", needsKey: true },
    { id: "navigator", api: "sales_navigator", mode: "", label: "Sales Navigator", detail: "Your own LinkedIn Sales Navigator search", needsKey: false },
    { id: "csv", api: "import", mode: "", label: "CSV file", detail: "Import leads you already have", needsKey: false },
  ];
  const SOURCE_IDS = SOURCES.map((s) => s.id);
  const FIELDS = {
    maps: [
      { name: "query", label: "What kind of business?", placeholder: "dentists, coffee roasters, plumbers", required: true },
      { name: "location", label: "Where?", placeholder: "Austin, TX", hint: "A city or area. Google Maps picks one from the search itself if you leave it empty." },
    ],
    people: [
      { name: "titles", label: "Job titles", placeholder: "VP Sales, Head of Sales", hint: "Separate with commas (up to 6)." },
      { name: "keywords", label: "Keywords", placeholder: "SaaS, fintech" },
      { name: "company", label: "Company", placeholder: "Northwind" },
      { name: "location", label: "Location", placeholder: "Austin" },
    ],
    companies: [
      { name: "keywords", label: "Keywords", placeholder: "cloud software" },
      { name: "company", label: "Company name", placeholder: "Northwind" },
      { name: "location", label: "Location", placeholder: "Austin" },
    ],
    hiring: [
      { name: "query", label: "Role they are hiring for", placeholder: "sales development representative", required: true },
      { name: "location", label: "Location", placeholder: "Austin, TX" },
    ],
    requests: [
      { name: "location", label: "Where? (optional)", placeholder: "Austin, TX", hint: "Only posts that mention this place." },
    ],
    navigator: [
      { name: "url", label: "Your Sales Navigator search or lead list address", placeholder: "https://www.linkedin.com/sales/search/people?query=…", required: true, wide: true, hint: "Build the search in Sales Navigator with your filters, then copy the address from the browser." },
    ],
  };
  const FIELD_NAMES = [...new Set(Object.values(FIELDS).flat().map((f) => f.name)), "max_pages", "services", "where", "freshness"];
  const MAX_PAGES = 5;

  const sourceById = (id) => SOURCES.find((s) => s.id === id) || null;
  const sourceOfApi = (api, mode) => (SOURCES.find((s) => s.api === api && (api !== "google_web" || s.mode === (mode || "people"))) || {}).id || "";
  const sourceLabel = (api) => SOURCE_LABELS[api] || String(api || "").replace(/_/g, " ");

  /** Split "a, b; c" or one-per-line text into unique, trimmed values. */
  function splitList(text, cap = 20) {
    const out = [];
    for (const part of String(text === undefined || text === null ? "" : text).split(/[\n,;]+/)) {
      const value = trim(part);
      if (value && !out.some((o) => o.toLowerCase() === value.toLowerCase())) out.push(value);
    }
    return out.slice(0, cap);
  }

  const clampPages = (value) => Math.max(1, Math.min(MAX_PAGES, Math.round(Number(value) || 2)));

  /** What the person still has to fill in ("" when the form can run). */
  function validateForm(sourceId, form = {}) {
    const v = (name) => trim(form[name]);
    if (sourceId === "maps" && !v("query")) return "Say what kind of business to look for, for example “dentists”.";
    if (sourceId === "people" && !(splitList(form.titles).length || v("keywords") || v("company"))) return "Give at least a job title, a keyword or a company.";
    if (sourceId === "companies" && !(v("keywords") || v("company") || v("location"))) return "Give at least a keyword, a company or a location.";
    if (sourceId === "hiring" && !v("query")) return "Say which role companies are hiring for.";
    if (sourceId === "requests" && !(form.services || []).length) return "Choose at least one service you offer (in “Services I offer” above), then search for people asking for it.";
    if (sourceId === "navigator") {
      if (!v("url")) return "Paste the address of a Sales Navigator search or lead list.";
      if (!SALES_URL.test(v("url"))) return "That is not a Sales Navigator people search or lead list address (it starts with https://www.linkedin.com/sales/search/people or /sales/lists/people).";
    }
    return "";
  }

  /** The request body the server expects for a source form (without dry_run, start or list). */
  function buildPayload(sourceId, form = {}) {
    const source = sourceById(sourceId);
    if (!source || source.id === "csv") throw new Error("This source has no search.");
    const v = (name) => trim(form[name]);
    const payload = { source: source.api };
    if (source.id === "maps") Object.assign(payload, { query: v("query"), location: v("location") });
    else if (source.id === "people") Object.assign(payload, { mode: "people", titles: splitList(form.titles, 6), keywords: v("keywords"), company: v("company"), location: v("location") });
    else if (source.id === "companies") Object.assign(payload, { mode: "companies", keywords: v("keywords"), company: v("company"), location: v("location") });
    else if (source.id === "hiring") Object.assign(payload, { query: v("query"), location: v("location") });
    else if (source.id === "requests") Object.assign(payload, { services: (form.services || []).slice(), where: (form.where || []).length ? form.where.slice() : ["reddit", "web"], freshness: ["week", "month", "year"].includes(form.freshness) ? form.freshness : "month", location: v("location") });
    else if (source.id === "navigator") Object.assign(payload, { url: v("url"), max_pages: clampPages(form.max_pages) });
    return clean(payload);
  }

  /** The cost line shown before a live search, from a dry-run answer. */
  function planText(plan, sourceId) {
    if (!plan) return "";
    if (sourceId === "navigator") return "No search credits are used: this reads your own Sales Navigator, read only.";
    if (plan.cache_hit) return "Answered from your cache: no SerpApi search is used.";
    if (!plan.credits) return "No SerpApi search is used.";
    return `Uses ${plural(plan.credits, "SerpApi search")} (a repeat inside the cache window is free).`;
  }
  /** Ask before the first search of a session that would spend credits. */
  const needsConfirm = (plan, state) => Boolean(plan && plan.credits > 0 && !state.confirmed);

  const scoreTone = (score) => (score === null || score === undefined ? "none" : score >= 70 ? "high" : score >= 40 ? "mid" : "low");

  /** Append new results to the ones already shown, keeping the first copy of each lead. */
  function mergeResults(current, incoming) {
    const seen = new Set(current.map((lead) => lead.preview_id));
    const out = current.slice();
    for (const lead of incoming) {
      if (seen.has(lead.preview_id)) continue;
      seen.add(lead.preview_id);
      out.push(lead);
    }
    return out;
  }
  const selectable = (leads) => leads.filter((lead) => !lead.duplicate_of);

  /** Query string for GET /api/leads/items (and the export) from the filter bar. */
  function leadQuery(filters = {}, { paging = true } = {}) {
    const f = filters;
    const map = { list_id: f.list_id, status: f.status, source: f.source, kind: f.kind, q: trim(f.q), min_score: f.min_score, has_email: f.has_email, tag: trim(f.tag), sort: f.sort };
    if (paging) { map.limit = f.limit; map.offset = f.offset ? f.offset : ""; }
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(map)) if (!isEmpty(value)) params.set(key, String(value));
    return params.toString();
  }
  function exportHref(format, filters = {}) {
    const qs = leadQuery({ ...filters, sort: "" }, { paging: false });
    return `/api/leads/export?format=${format === "xlsx" ? "xlsx" : "csv"}${qs ? `&${qs}` : ""}`;
  }

  /** A deep link's query becomes { source, form } (it only fills the form in). */
  function parseDeepLink(query = {}) {
    const known = { google_maps: "maps", google_jobs: "hiring", google_intent: "requests", sales_navigator: "navigator", import: "csv" };
    let source = String(query.source || "");
    if (query.source === "google_web") source = query.mode === "companies" ? "companies" : "people";
    source = known[source] || source;
    if (!SOURCE_IDS.includes(source)) source = "";
    const form = {};
    for (const name of FIELD_NAMES) if (!isEmpty(query[name])) form[name] = String(query[name]);
    for (const name of ["services", "where"]) if (form[name] !== undefined) form[name] = splitList(form[name], 10);
    return { source, form };
  }

  const safeUrl = (url) => { try { const u = new URL(String(url)); return u.protocol === "https:" || u.protocol === "http:" ? u.href : ""; } catch { return ""; } };
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };

  /** The ICP form as the server expects it. */
  function icpPayload(form = {}) {
    const rating = trim(form.min_rating);
    return {
      titles: splitList(form.titles), keywords: splitList(form.keywords), industries: splitList(form.industries), locations: splitList(form.locations),
      exclude_keywords: splitList(form.exclude_keywords), exclude_companies: splitList(form.exclude_companies),
      require: (form.require || []).filter((r) => ["website", "phone", "email", "linkedin"].includes(r)),
      services: (form.services || []).map(String),
      min_rating: rating === "" ? null : Number(rating),
    };
  }
  const icpSummary = (icp = {}) => [["titles", "titles"], ["keywords", "keywords"], ["industries", "industries"], ["locations", "locations"]].map(([key, word]) => (icp[key] || []).length ? plural(icp[key].length, word.slice(0, -1)) : "").filter(Boolean).join(", ");

  /** One line for a run in History. */
  function describeRun(run) {
    if (run.status === "error") return `Failed: ${run.error || "unknown error"}`;
    const parts = [`${plural(run.found, "lead")} found`, `${run.added} added`];
    if (run.duplicates) parts.push(`${run.duplicates} already saved`);
    return parts.join(", ");
  }
  const rangeText = (offset, shown, total) => (total ? `${offset + 1}–${offset + shown} of ${total}` : "0 leads");

  // ---- services you offer, gaps in a business's presence, public requests, outreach state ------------------------------

  /** Used when /api/leads/services cannot be read; the server's list is the source of truth. */
  const FALLBACK_SERVICES = [
    { id: "website", label: "Website design and development", roles: ["web developer", "website designer", "wordpress developer", "front end developer"] },
    { id: "marketing", label: "Digital marketing and SEO", roles: ["digital marketing manager", "SEO specialist", "marketing manager", "growth marketer"] },
    { id: "content", label: "Content creation and copywriting", roles: ["content writer", "copywriter", "content marketing manager", "content creator", "video editor"] },
    { id: "ads", label: "Paid ads (Google and Meta)", roles: ["PPC specialist", "paid media manager", "performance marketing manager"] },
    { id: "social", label: "Social media management", roles: ["social media manager", "community manager"] },
  ];
  const WHERE_OPTIONS = [["reddit", "Reddit"], ["quora", "Quora"], ["linkedin", "LinkedIn posts"], ["web", "Anywhere on the web"]];
  const FRESHNESS = [["week", "Past week"], ["month", "Past month"], ["year", "Past year"]];
  const GAP_TYPES = { no_website: "No website", thin_reviews: "Few reviews", low_rating: "Low rating" };
  const NEED_TYPES = { ...GAP_TYPES, weak_site: "Weak website", request: "Asked for it" };
  const SERVICE_NEEDS = { website: ["no_website", "weak_site"], marketing: ["thin_reviews", "low_rating", "weak_site"], content: ["weak_site"], ads: ["weak_site"], social: ["thin_reviews", "weak_site"] };
  const SEVERITY_TONE = { high: "danger", medium: "warn", low: "" };
  const OUTBOX_STATUSES = [["", "All messages"], ["draft", "Drafts"], ["approved", "Approved"], ["sent", "Sent"], ["failed", "Failed"], ["rejected", "Rejected"], ["skipped", "Skipped"]];

  const serviceLabel = (catalog, id) => ((catalog || FALLBACK_SERVICES).find((s) => s.id === id) || {}).label || String(id || "");
  const signalTypes = (lead) => new Set((lead.signals || []).map((s) => s.type));
  /** The gaps a Google Maps listing shows by itself: no website, few reviews, a low rating. */
  const gapsOf = (lead) => Object.keys(GAP_TYPES).filter((type) => signalTypes(lead).has(type)).map((type) => ({ type, label: GAP_TYPES[type] }));
  const hasGap = (lead) => gapsOf(lead).length > 0;
  const gapFilter = (leads, only) => (only ? leads.filter(hasGap) : leads.slice());

  /** Badges for a public request, read from the “The post mentions a budget, says it is urgent, posted 2 days ago” signal. */
  function requestBadges(lead) {
    const detail = ((lead.signals || []).find((s) => s.type === "request_detail") || {}).text || "";
    const badges = [];
    if (/budget|pay/i.test(detail)) badges.push({ text: "Budget mentioned", tone: "ok" });
    if (/urgent/i.test(detail)) badges.push({ text: "Urgent", tone: "hot" });
    const when = /posted\s+(.+)$/i.exec(detail);
    if (when) badges.push({ text: `Posted ${trim(when[1])}`, tone: "" });
    return badges;
  }
  const requestText = (lead) => ((lead.signals || []).find((s) => s.type === "request") || {}).text || "";
  const requestUrl = (lead) => ((lead.signals || []).find((s) => s.type === "request" && s.url) || {}).url || lead.source_ref || "";

  /** Role chips for the Hiring-signals form: the roles of the services you offer (every service when none is chosen). */
  function roleChips(catalog, offered) {
    const chosen = (catalog || FALLBACK_SERVICES).filter((s) => !(offered || []).length || offered.includes(s.id));
    return [...new Set(chosen.flatMap((s) => s.roles || []))];
  }

  /** Which of the services you offer this lead's own signals call for: [{ service, label, reason }]. */
  function needsOf(lead, offered, catalog) {
    const signals = lead.signals || [];
    const out = [];
    for (const service of offered || []) {
      const wanted = SERVICE_NEEDS[service] || [];
      let reason = ((signals.find((s) => wanted.includes(s.type)) || {}).text || "").replace(/^Site audit: /, "");
      if (!reason && signalTypes(lead).has("request") && (lead.tags || []).includes(service)) reason = "Asked for it in a public post";
      if (!reason) reason = ((signals.find((s) => s.type === "hiring" && /marketing|content|writer|developer|designer|seo|ppc|social|copywriter|paid/i.test(s.text)) || {}).text || "");
      if (reason) out.push({ service, label: serviceLabel(catalog, service), reason });
    }
    return out;
  }

  /** What “Write emails” did: a headline and the reasons some leads were skipped. */
  function summarizeDrafts(out) {
    const created = out.created || 0;
    const skipped = (out.skipped || []).map((s) => ({ name: s.name || "A lead", reason: s.reason }));
    const headline = `${plural(created, "draft")} written${skipped.length ? `, ${skipped.length} skipped` : ""}.`;
    return { headline, created, skipped, hint: created ? "Nothing is sent yet: review and approve each message in the Outbox." : "" };
  }
  const authorLabel = (source) => (source === "model" ? "Ollama" : source === "edited" ? "Edited by you" : source === "manual" ? "Written by you" : "Template");

  /** The Send button's numbers: how many are approved, what went today and what the cap leaves. */
  function outboxState(counts = {}, settings = {}) {
    const cap = Number(settings.daily_cap) || 20;
    const sentToday = Number(counts.sent_today) || 0;
    const remaining = Math.max(0, cap - sentToday);
    const approved = Number(counts.approved) || 0;
    return { approved, drafts: Number(counts.draft) || 0, sentToday, cap, remaining, willSend: Math.min(approved, remaining, 10), canSend: approved > 0 && remaining > 0 };
  }

  /** The sender-settings form as the server expects it (the password is sent only when typed). */
  function settingsPayload(form = {}) {
    const text = (name) => trim(form[name]);
    const payload = {
      from_name: text("from_name"), from_email: text("from_email"), reply_to: text("reply_to"), smtp_host: text("smtp_host"),
      smtp_port: Number(form.smtp_port) || 587, smtp_security: form.smtp_security || "starttls", smtp_username: text("smtp_username"),
      postal_address: text("postal_address"), signature: text("signature"), offer: trim(form.offer), calling_number: text("calling_number"),
      services: (form.services || []).map(String), daily_cap: Number(form.daily_cap) || 20, delay_seconds: Number(form.delay_seconds) || 5,
      allow_regulated_regions: Boolean(form.allow_regulated_regions),
    };
    if (form.smtp_password) payload.smtp_password = String(form.smtp_password);
    if (form.clear_password) payload.clear_password = true;
    return payload;
  }
  const problemsText = (problems) => (problems && problems.length ? `Still needed before anything can be sent: ${problems.join("; ")}.` : "");
  const OUTCOME_FALLBACK = [["no_answer", "No answer"], ["voicemail", "Left a voicemail"], ["spoke", "Spoke with them"], ["interested", "Interested"], ["callback", "Asked me to call back"], ["not_interested", "Not interested"], ["wrong_number", "Wrong number"], ["do_not_call", "Asked not to be called again"]];

  const logic = { SOURCES, FIELDS, STATUSES, SORTS, MAX_PAGES, splitList, clampPages, validateForm, buildPayload, planText, needsConfirm, scoreTone, mergeResults, selectable, leadQuery, exportHref, parseDeepLink, safeUrl, hostOf, icpPayload, icpSummary, describeRun, rangeText, sourceOfApi, sourceLabel, plural, FALLBACK_SERVICES, WHERE_OPTIONS, FRESHNESS, GAP_TYPES, NEED_TYPES, SEVERITY_TONE, OUTBOX_STATUSES, OUTCOME_FALLBACK, serviceLabel, gapsOf, hasGap, gapFilter, requestBadges, requestText, requestUrl, roleChips, needsOf, summarizeDrafts, authorLabel, outboxState, settingsPayload, problemsText };
  Aira.leads = Object.assign(Aira.leads || {}, { logic });
  if (!hasDom) {
    if (typeof module !== "undefined" && module.exports) module.exports = logic;
    return;
  }

  // ===========================================================================
  // DOM
  // ===========================================================================

  const { h, api, fmt, ui, toast, router } = Aira;
  const BASE = "/api/leads";
  const TABS = [["find", "Find"], ["leads", "Leads"], ["outreach", "Outreach"], ["settings", "Settings"], ["history", "History"]];
  const TAB_IDS = TABS.map(([id]) => id);
  const link = (tab, query = {}) => `#/leads?${new URLSearchParams({ tab, ...clean(query) })}`;
  const accountLink = () => "#/airaapi?tab=account";
  const remember = (key, value) => Aira.remember(`leads.${key}`, value);
  const recall = (key, fallback) => Aira.recall(`leads.${key}`, fallback);
  const session = { confirmed: false }; // the credits confirmation is asked once per page load

  const ICON_PATHS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    save: '<path d="M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6"/>',
    download: '<path d="M12 4v11m-4-4 4 4 4-4M5 20h14"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    pin: '<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 5a3.5 3.5 0 0 1 0 7M18 20c0-2.4-1-4.4-3-5.4"/>',
    building: '<path d="M4 21V5l8-2v18M12 9h8v12M7 8h2M7 12h2M7 16h2M15 13h2M15 17h2M2 21h20"/>',
    briefcase: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18"/>',
    linkedin: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 10v7M8 7v.5M12 17v-4a2 2 0 0 1 4 0v4M12 10v7"/>',
    file: '<path d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5M12 17.5v.5"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
    phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    send: '<path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
  };
  const SOURCE_ICONS = { maps: "pin", requests: "chat", people: "users", companies: "building", hiring: "briefcase", navigator: "linkedin", csv: "file" };
  const ico = (name) => h("span", { class: "ld-icon", "aria-hidden": "true", html: `<svg viewBox="0 0 24 24" focusable="false">${ICON_PATHS[name] || ""}</svg>` });

  function btn(label, { icon, kind = "secondary", sm = false, onClick, title, href, external = false, disabled = false, aria, cls = "", download = false, id } = {}) {
    const attrs = { id: id || null, class: `btn ${kind}${sm ? " sm" : ""} ${cls}`.replace(/\s+/g, " ").trim(), title: title || null, "aria-label": aria || null, onclick: onClick || null };
    const children = [icon ? ico(icon) : null, label ? h("span", { text: label }) : null];
    if (href) {
      attrs.href = href;
      if (external) { attrs.target = "_blank"; attrs.rel = "noopener noreferrer"; }
      if (download) attrs.download = "";
      return h("a", attrs, ...children, external ? h("span", { class: "visually-hidden", text: " (opens in a new tab)" }) : null);
    }
    attrs.type = "button";
    attrs.disabled = disabled || null;
    return h("button", attrs, ...children);
  }
  const externalLink = (url, children, attrs = {}) => {
    const safe = safeUrl(url);
    if (!safe) return h("span", attrs, children);
    return h("a", { ...attrs, href: safe, target: "_blank", rel: "noopener noreferrer" }, children, h("span", { class: "visually-hidden", text: " (opens in a new tab)" }));
  };
  async function busy(button, fn) {
    if (button && button.getAttribute("aria-busy") === "true") return undefined;
    if (button) { button.setAttribute("aria-busy", "true"); button.disabled = true; }
    try { return await fn(); }
    catch (error) { Aira.reportError(error); return false; }
    finally { if (button) { button.removeAttribute("aria-busy"); button.disabled = false; } }
  }
  const fill = (node, ...children) => node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  const chip = (text, tone = "") => (isEmpty(text) ? null : h("span", { class: `ld-chip ${tone}`.trim(), text: String(text) }));
  const live = h("div", { class: "visually-hidden", role: "status", "aria-live": "polite", id: "ldLive" });
  const announce = (text) => { live.textContent = ""; setTimeout(() => { live.textContent = text; }, 30); };

  async function copyText(text, what = "Copied") {
    try { await navigator.clipboard.writeText(text); toast(`${what} to the clipboard.`, "success"); return; } catch { /* fall through */ }
    const area = h("textarea", { "aria-hidden": "true", style: { position: "fixed", opacity: "0" } });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
    toast(ok ? `${what} to the clipboard.` : "Could not copy. Select the text and copy it yourself.", ok ? "success" : "error");
  }

  /** A text field row: label, input, hint. */
  function textField(field, value, { id } = {}) {
    const attrs = { id: id || `ld-${field.name}`, name: field.name, type: field.type || "text", value: value || "", placeholder: field.placeholder || null, autocomplete: "off", maxlength: field.maxlength || "300" };
    const input = field.multiline ? h("textarea", { ...attrs, rows: field.rows || 3, type: null, value: null }) : h("input", attrs);
    if (field.multiline) input.value = value || "";
    return h("div", { class: `field${field.wide ? " wide" : ""}` }, h("label", { for: attrs.id, text: field.label + (field.required ? " *" : "") }), input, field.hint ? h("span", { class: "hint", text: field.hint }) : null);
  }

  // ---- states ---------------------------------------------------------------------------------------------------------

  function setupPanel({ compact = false } = {}) {
    return h("section", { class: `ld-setup${compact ? " compact" : ""}`, "aria-labelledby": "ldSetupTitle" },
      h("span", { class: "ld-setup-mark", "aria-hidden": "true" }, ico("key")),
      h("div", { class: "ld-setup-body" },
        h("h2", { id: "ldSetupTitle", text: "Connect SerpApi to search Google for leads" }),
        h("p", { class: "muted", text: "Google Maps, people, companies and hiring signals use the SerpApi key you connect in Aira API. Sales Navigator and CSV import work without it. Until a key is connected you can still see what each search would cost." }),
        h("div", { class: "btn-row" }, btn("Connect SerpApi in Aira API > Account", { icon: "key", kind: "", href: accountLink() }))));
  }

  function problem(error, retry) {
    if (error && error.code === "not_configured") return setupPanel();
    return ui.error(error, retry);
  }

  // ---- lists ----------------------------------------------------------------------------------------------------------

  async function newListDialog() {
    return new Promise((resolve) => {
      const input = h("input", { id: "ldNewListName", type: "text", maxlength: "80", placeholder: "Austin SaaS prospects", autocomplete: "off" });
      const desc = h("input", { id: "ldNewListDesc", type: "text", maxlength: "300", placeholder: "Optional note", autocomplete: "off" });
      let done = false;
      const finish = (value) => { if (!done) { done = true; resolve(value); } };
      Aira.modal({
        title: "New list",
        body: h("div", { class: "ld-form" }, ui.field("List name", input), ui.field("Description", desc)),
        actions: [
          { label: "Cancel", kind: "ghost", onClick: () => finish(null) },
          { label: "Create list", kind: "", onClick: async () => { const out = await api(`${BASE}/lists`, { method: "POST", body: { name: input.value, description: desc.value } }); finish(out.list); } },
        ],
        onClose: () => finish(null),
      });
      input.focus();
    });
  }

  /** A <select> of lists with a "New list…" entry. The chosen list id is kept in remembered state. */
  function listPicker(lists, { id, label = "Save to", withNew = true, onChange } = {}) {
    const NEW = "__new__";
    const remembered = recall("list", "");
    let current = lists.some((l) => l.id === remembered) ? remembered : (lists[0] ? lists[0].id : "");
    const select = h("select", { id: id || "ldListPick", "aria-label": label });
    const paint = () => {
      select.replaceChildren(
        ...(lists.length ? [] : [h("option", { value: "", text: "My leads (new)" })]),
        ...lists.map((list) => h("option", { value: list.id, text: `${list.name} (${list.lead_count})`, selected: list.id === current })),
        ...(withNew ? [h("option", { value: NEW, text: "New list…" })] : []));
      select.value = current;
    };
    paint();
    select.addEventListener("change", async () => {
      if (select.value === NEW) {
        const created = await newListDialog();
        if (created) { lists.push({ ...created, lead_count: 0 }); current = created.id; remember("list", current); if (onChange) onChange(current, created); }
        paint();
        return;
      }
      current = select.value;
      remember("list", current);
      if (onChange) onChange(current);
    });
    return { select, value: () => current, node: h("label", { class: "ld-pick" }, h("span", { text: label }), select) };
  }

  // ---- score and result rows ------------------------------------------------------------------------------------------

  function scoreBadge(lead) {
    const reasons = lead.score_reasons || [];
    const label = lead.score === null || lead.score === undefined ? "Not scored" : `Fit ${lead.score} of 100`;
    const tip = reasons.length ? reasons.join("\n") : (lead.score === null || lead.score === undefined ? "Describe your ideal customer in the Settings tab to score leads." : label);
    return h("span", { class: `ld-score ${scoreTone(lead.score)}`, tabindex: "0", role: "img", "aria-label": `${label}. ${reasons.join(". ")}`.trim() },
      h("span", { class: "ld-score-n", text: lead.score === null || lead.score === undefined ? "—" : String(lead.score) }),
      h("span", { class: "ld-tip", role: "tooltip", text: tip }));
  }
  const kindIcon = (lead) => ico(lead.kind === "person" ? "users" : lead.kind === "request" ? "chat" : "building");

  function contactCell(lead) {
    const bits = [];
    if (lead.phone) bits.push(h("div", { class: "ld-line", text: lead.phone }));
    if (lead.website) bits.push(h("div", { class: "ld-line" }, externalLink(lead.website, hostOf(lead.website) || lead.website)));
    if (lead.email) bits.push(h("div", { class: "ld-line" }, lead.email));
    if (lead.kind === "request" && requestUrl(lead)) bits.push(h("div", { class: "ld-line" }, externalLink(requestUrl(lead), "Open the post")));
    const li = lead.linkedin_url || lead.sales_nav_url;
    if (li) bits.push(h("div", { class: "ld-line" }, externalLink(li, lead.sales_nav_url && !lead.linkedin_url ? "Sales Navigator" : "LinkedIn")));
    return bits.length ? bits : h("span", { class: "muted", text: "—" });
  }
  function signalChips(lead, max = 3) {
    const out = [];
    for (const s of lead.signals || []) {
      if (s.type === "request_detail") continue;
      const need = NEED_TYPES[s.type];
      const label = need || (s.text.length > 46 ? `${s.text.slice(0, 44)}…` : s.text);
      const tone = need ? "warn" : s.type === "hiring" || s.type === "news" ? "hot" : "";
      out.push(h("span", { class: `ld-chip ${tone}`.trim(), title: s.text, text: label }));
      if (out.length >= max) break;
    }
    return out.length ? out : h("span", { class: "muted", text: "—" });
  }

  // ===========================================================================
  // Find
  // ===========================================================================

  /** The services catalog from the server (phrases and roles searched); the built-in list is used if it cannot be read. */
  let catalogCache = null;
  let offeredCache = [];
  async function loadCatalog() {
    if (catalogCache) return catalogCache;
    try {
      const out = await api(`${BASE}/services`, { silent: true });
      if (out && Array.isArray(out.services) && out.services.length) { catalogCache = out.services; return catalogCache; }
    } catch { /* the built-in list is used */ }
    return FALLBACK_SERVICES;
  }
  /** The services you offer right now (what the drawer, the call notes and the emails are about). */
  const liveOffered = () => { const remembered = recall("services", null); return Array.isArray(remembered) ? remembered : offeredCache; };
  /** The services you offer: remembered on this device, else what your ideal customer profile says. */
  function offeredFrom(overview, catalog) {
    const known = new Set(catalog.map((s) => s.id));
    const remembered = recall("services", null);
    const list = Array.isArray(remembered) ? remembered : ((overview.icp && overview.icp.services) || []);
    return list.filter((id) => known.has(id));
  }

  async function renderFind(content, ctx) {
    const { overview } = ctx;
    const catalog = await loadCatalog();
    const offered = offeredFrom(overview, catalog);
    offeredCache = offered.slice();
    const deep = parseDeepLink(ctx.route.query);
    const sourceInfo = Object.fromEntries((overview.sources || []).map((s) => [s.id, s]));
    const apiOf = (id) => (sourceById(id) || {}).api;
    const availability = (id) => {
      const source = sourceById(id);
      if (id === "csv") return { ok: true };
      const info = sourceInfo[source.api] || {};
      return { ok: info.available !== false, needs: info.needs || "", cost: info.cost || "" };
    };

    const state = {
      source: deep.source || recall("source", "maps"),
      forms: Object.fromEntries(SOURCE_IDS.map((id) => [id, { ...(id === "navigator" ? { max_pages: 2 } : {}), ...(id === "requests" ? { services: offered.slice(), where: recall("where", ["reddit", "web"]), freshness: recall("freshness", "month") } : {}) }])),
      gapOnly: false,
      results: [], response: null, selected: new Set(), listId: "", running: false, payload: null,
    };
    if (!SOURCE_IDS.includes(state.source)) state.source = "maps";
    if (deep.source) Object.assign(state.forms[deep.source], deep.form, deep.source === "requests" && !(deep.form.services || []).length ? { services: offered.slice() } : {});
    const lists = overview.lists.slice();
    const picker = listPicker(lists, { id: "ldSaveList" });

    // ---- the services you offer (kept on this device, and saved to your profile and sender settings) ----
    const offeredBox = h("section", { class: "panel ld-panel ld-offered", "aria-labelledby": "ldOfferedTitle" });
    let paintSummaryLine = () => {};
    let offeredTimer = null;
    const offeredList = () => state.forms.requests.services;
    function paintOffered() {
      const boxes = catalog.map((service) => {
        const input = h("input", { type: "checkbox", id: `ldOffer-${service.id}`, value: service.id, checked: offeredList().includes(service.id) || null });
        input.addEventListener("change", () => {
          const chosen = new Set(offeredList());
          if (input.checked) chosen.add(service.id); else chosen.delete(service.id);
          const next = catalog.map((s) => s.id).filter((id) => chosen.has(id));
          offeredList().splice(0, offeredList().length, ...next);
          remember("services", next);
          paintSummaryLine();
          if (state.source === "hiring") paintForm();
          clearTimeout(offeredTimer);
          offeredTimer = setTimeout(() => saveOffered(next), 500);
        });
        return h("label", { class: "check ld-offer" }, input, h("span", { text: service.label }));
      });
      fill(offeredBox,
        h("div", { class: "panel-head" }, h("h2", { id: "ldOfferedTitle", text: "Services I offer" }), h("small", { class: "muted", id: "ldOfferedSaved", role: "status", "aria-live": "polite" })),
        h("div", { class: "panel-body" },
          h("p", { class: "muted", text: "Tell Aira what you sell. It uses this to find people asking for it, to point out which businesses need it, to raise the fit score of leads that do, and to write emails and call notes about it." }),
          h("div", { class: "ld-checks", role: "group", "aria-label": "Services I offer" }, ...boxes)));
    }
    async function saveOffered(next) {
      const status = document.getElementById("ldOfferedSaved");
      try {
        await api(`${BASE}/icp`, { method: "POST", body: { ...(overview.icp || {}), services: next }, silent: true });
        overview.icp = { ...(overview.icp || {}), services: next };
        try { await api(`${BASE}/outreach/settings`, { method: "POST", body: { services: next }, silent: true }); } catch { /* sender settings are optional here */ }
        if (status) status.textContent = "Saved for scoring and email drafts.";
      } catch (error) {
        if (status) status.textContent = "Could not save to your profile (kept on this device).";
      }
    }

    const header = h("div", { class: "ld-find" });
    const sourceRow = h("div", { class: "ld-sources", role: "radiogroup", "aria-label": "Where to find leads" });
    const formBox = h("div", { class: "ld-formbox" });
    const resultsBox = h("div", { class: "ld-results", id: "ldResults", "aria-live": "polite" });
    const savedBox = h("div", { class: "ld-saved" });
    const searches = overview.searches.slice();

    const paintSources = () => fill(sourceRow, ...SOURCES.map((source) => {
      const on = source.id === state.source;
      const info = availability(source.id);
      return h("button", { type: "button", class: `ld-source${on ? " on" : ""}${info.ok ? "" : " off"}`, role: "radio", "aria-checked": on ? "true" : "false", id: `ldSource-${source.id}`, onclick: () => { state.source = source.id; remember("source", source.id); paintSources(); paintForm(); fill(resultsBox); }, onkeydown: (event) => { const buttons = [...sourceRow.querySelectorAll("button")]; const index = buttons.indexOf(event.currentTarget); if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); buttons[(index + 1) % buttons.length].focus(); buttons[(index + 1) % buttons.length].click(); } if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); buttons[(index - 1 + buttons.length) % buttons.length].focus(); buttons[(index - 1 + buttons.length) % buttons.length].click(); } } },
        h("span", { class: "ld-source-ico" }, ico(SOURCE_ICONS[source.id])),
        h("span", { class: "ld-source-text" }, h("strong", { text: source.label }), h("small", { text: source.detail })));
    }));

    // ---- the form for the chosen source ----
    function requestControls(form) {
      const summary = h("p", { class: "ld-offered-line", id: "ldOfferedLine", role: "status" });
      paintSummaryLine = () => {
        const labels = (form.services || []).map((id) => serviceLabel(catalog, id));
        summary.textContent = labels.length ? `Looking for people who need: ${labels.join(", ")}. Change this in “Services I offer” above.` : "Choose the services you offer above first.";
      };
      paintSummaryLine();
      const where = h("fieldset", { class: "ld-fieldset", id: "ldWhere" }, h("legend", { text: "Look in" }),
        h("div", { class: "ld-checks" }, ...WHERE_OPTIONS.map(([id, label]) => {
          const box = h("input", { type: "checkbox", id: `ldWhere-${id}`, value: id, checked: (form.where || []).includes(id) || null });
          box.addEventListener("change", () => { const list = new Set(form.where || []); if (box.checked) list.add(id); else list.delete(id); form.where = [...list]; remember("where", form.where); });
          return h("label", { class: "check" }, box, h("span", { text: label }));
        })));
      const fresh = h("div", { class: "field" }, h("label", { for: "ldFreshness", text: "Posted" }), ui.select(FRESHNESS, form.freshness || "month", { id: "ldFreshness", name: "freshness" }));
      const note = h("p", { class: "ld-notice-line", id: "ldRequestNote" }, ico("info"), " These are public posts. Aira never replies for you: open a post and answer in your own name, following that community's rules.");
      return { summary, fresh, where, note };
    }
    function roleChipRow(form) {
      const roles = roleChips(catalog, offeredList());
      return h("div", { class: "ld-rolechips", id: "ldRoleChips", role: "group", "aria-label": "Roles that signal a need for what you offer" },
        h("span", { class: "muted", text: offeredList().length ? "Roles that signal a need for what you offer:" : "Roles that signal a need (choose your services above to narrow this):" }),
        ...roles.map((role) => h("button", { type: "button", class: "ld-rolechip", text: role, onclick: () => { form.query = role; const input = document.getElementById("ld-query"); if (input) { input.value = role; input.focus(); } } })));
    }

    function paintForm() {
      const source = sourceById(state.source);
      const form = state.forms[source.id];
      if (source.id === "csv") { fill(formBox, csvForm()); return; }
      const info = availability(source.id);
      const fields = FIELDS[source.id].map((field) => textField(field, form[field.name]));
      let pages = null;
      if (source.id === "navigator") {
        pages = h("div", { class: "field" }, h("label", { for: "ldMaxPages", text: "Pages to read" }), ui.select([1, 2, 3, 4, 5].map((n) => [String(n), `${n} (about ${n * 25} people)`]), String(form.max_pages || 2), { id: "ldMaxPages", name: "max_pages" }), h("span", { class: "hint", text: "Fewer pages is gentler on your LinkedIn account." }));
      }
      const requests = source.id === "requests" ? requestControls(form) : null;
      const note = h("p", { class: "ld-cost", id: "ldCost", role: "status", "aria-live": "polite", text: info.cost ? `Cost: ${info.cost}.` : "" });
      const error = h("p", { class: "ld-formerror", id: "ldFormError", role: "alert" });
      const notice = source.id === "navigator" ? navigatorNotice(info) : (!info.ok ? setupPanel({ compact: true }) : null);
      const submit = h("button", { type: "submit", class: "btn", id: "ldSearchBtn", disabled: info.ok ? null : true }, ico("search"), h("span", { text: source.id === "navigator" ? "Read my Sales Navigator search" : source.id === "requests" ? "Find people asking" : "Search" }));
      const saveSearch = btn("Save this search", { icon: "save", kind: "secondary", onClick: () => saveSearchDialog(form) });
      const formNode = h("form", { class: "ld-form", id: "ldSearchForm", novalidate: "true", "aria-label": `${source.label} search`, onsubmit: (event) => { event.preventDefault(); runSearch({ button: submit, formNode }); } },
        requests ? requests.summary : null,
        h("div", { class: "ld-grid" }, ...fields, pages, requests ? requests.fresh : null),
        requests ? requests.where : null, requests ? requests.note : null,
        source.id === "hiring" ? roleChipRow(form) : null,
        error, note,
        h("div", { class: "btn-row" }, submit, info.ok ? saveSearch : null, h("span", { class: "muted ld-hint-line", text: "A search only previews: nothing is saved until you choose." })));
      formNode.addEventListener("input", (event) => { const t = event.target; if (t && t.name) form[t.name] = t.value; error.textContent = ""; if (t && t.name === "freshness") remember("freshness", t.value); });
      fill(formBox, notice, formNode);
    }

    function navigatorNotice(info) {
      return h("div", { class: `ld-notice ${info.ok ? "" : "warn"}`, role: "note", id: "ldNavNotice" },
        h("span", { class: "ld-notice-ico" }, ico(info.ok ? "info" : "warn")),
        h("div", {},
          h("strong", { text: info.ok ? "Read only, in your own LinkedIn session" : "Sales Navigator is not available yet" }),
          h("p", { class: "muted", text: "Aira opens the search you built in your own signed-in LinkedIn browser and reads the results at a human pace. It never connects, messages or saves anyone. LinkedIn restricts automated access, so keep volumes low (a few pages at a time) and use your own account. The LinkedIn account needs a Sales Navigator subscription." }),
          info.ok ? null : h("p", {}, h("strong", { text: info.needs || "Connect your LinkedIn account first." }), " ", h("a", { href: "#/settings/integrations", text: "Open Settings > Integrations" }))));
    }

    // ---- saved searches ----
    function paintSaved() {
      if (!searches.length) { fill(savedBox); return; }
      fill(savedBox, h("section", { class: "panel ld-panel", "aria-labelledby": "ldSavedTitle" },
        h("div", { class: "panel-head" }, h("h2", { id: "ldSavedTitle", text: "Saved searches" }), h("small", { class: "muted", text: "Running one adds its new leads to its list." })),
        h("ul", { class: "ld-saved-list" }, ...searches.map((search) => h("li", {},
          h("div", { class: "ld-saved-main" }, h("strong", { text: search.name }), h("small", { class: "muted", text: `${sourceLabel(search.source)}${search.last_run_at ? ` · last run ${fmt.datetime(search.last_run_at)}: ${search.last_found} found, ${search.last_added} added` : " · never run"}` })),
          h("div", { class: "btn-row" },
            btn("Run", { icon: "refresh", sm: true, aria: `Run ${search.name}`, onClick: (event) => busy(event.currentTarget, () => runSaved(search)) }),
            btn("", { icon: "trash", kind: "ghost", sm: true, aria: `Delete ${search.name}`, title: "Delete this saved search", onClick: async () => { if (!(await Aira.confirm({ title: "Delete saved search", message: `Delete “${search.name}”? Leads it already added stay.`, confirmLabel: "Delete", danger: true }))) return; await api(`${BASE}/searches/${search.id}`, { method: "DELETE" }); searches.splice(searches.indexOf(search), 1); paintSaved(); toast("Saved search deleted.", "success"); } })))))));
    }
    async function saveSearchDialog(form) {
      const source = sourceById(state.source);
      const problemText = validateForm(source.id, form);
      if (problemText) { toast(problemText, "error"); return; }
      const params = buildPayload(source.id, form);
      const name = h("input", { id: "ldSearchName", type: "text", maxlength: "100", value: `${source.label}: ${trim(form.query || form.titles || form.keywords || form.company || "search")}`.slice(0, 100) });
      Aira.modal({ title: "Save this search", body: h("div", { class: "ld-form" }, ui.field("Name", name), h("p", { class: "muted", text: "Running it later adds new leads to the list you pick below the results." })),
        actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save search", kind: "", onClick: async () => { const { source: apiSource, ...rest } = params; const out = await api(`${BASE}/searches`, { method: "POST", body: { name: name.value, source: apiSource, params: rest, list_id: picker.value() } }); searches.unshift(out.search); paintSaved(); toast("Search saved.", "success"); } }] });
      name.focus();
    }
    async function runSaved(search) {
      const out = await api(`${BASE}/searches/${search.id}/run`, { method: "POST", body: { auto_add: true } });
      Object.assign(search, { last_run_at: new Date().toISOString(), last_found: out.count, last_added: out.added });
      paintSaved();
      state.response = out; state.results = out.leads; state.selected = new Set(); state.payload = null;
      paintResults();
      toast(`${search.name}: ${out.count} found, ${out.added} added.`, "success");
    }

    // ---- running a search ----
    async function runSearch({ button, formNode, more = null }) {
      if (state.running) return;
      const source = sourceById(state.source);
      const form = state.forms[source.id];
      const error = formNode ? formNode.querySelector("#ldFormError") : null;
      const invalid = validateForm(source.id, form);
      if (invalid) { if (error) error.textContent = invalid; const first = formNode && formNode.querySelector("input:not([type=hidden])"); if (first) first.focus(); return; }
      const payload = more ? state.payload : buildPayload(source.id, form);
      state.running = true;
      await busy(button, async () => {
        const body = { ...payload, ...(more || {}) };
        const plan = await api(`${BASE}/search`, { method: "POST", body: { ...body, dry_run: true } });
        const costLine = document.getElementById("ldCost");
        if (costLine) costLine.textContent = planText(plan, source.id);
        if (needsConfirm(plan, session)) {
          const go = await Aira.confirm({ title: "Run this search?", message: `${planText(plan, source.id)} Aira asks once per visit before it spends SerpApi searches.`, confirmLabel: "Search" });
          if (!go) return;
          session.confirmed = true;
        }
        const out = await api(`${BASE}/search`, { method: "POST", body });
        state.payload = payload;
        state.response = { ...out, count: more ? state.results.length + out.leads.length : out.count };
        state.results = more ? mergeResults(state.results, out.leads) : out.leads;
        if (!more) state.selected = new Set();
        if (costLine) costLine.textContent = `${planText({ credits: out.credits, cache_hit: out.cache_hit }, source.id)}`;
        paintResults(out);
        announce(`${out.count} ${out.count === 1 ? "lead" : "leads"} found.`);
        const target = document.getElementById("ldResults");
        if (target && !more && target.scrollIntoView) target.scrollIntoView({ block: "start", behavior: "smooth" });
      }).finally(() => { state.running = false; });
    }

    // ---- results ----
    function resultRow(lead, mode) {
      const dup = Boolean(lead.duplicate_of);
      const box = h("input", { type: "checkbox", "aria-label": `Select ${lead.name}`, checked: state.selected.has(lead.preview_id) || null, disabled: dup ? true : null });
      box.addEventListener("change", () => { if (box.checked) state.selected.add(lead.preview_id); else state.selected.delete(lead.preview_id); paintResults(); });
      const badge = dup ? h("span", { class: "badge ld-dup", title: "This lead is already in your saved leads", text: "Already saved" }) : null;
      if (mode === "requests") {
        const badges = requestBadges(lead).map((b) => chip(b.text, b.tone));
        const tags = (lead.tags || []).map((id) => chip(serviceLabel(catalog, id), "ok"));
        const url = requestUrl(lead);
        return h("tr", { class: dup ? "dup" : "" },
          h("td", { class: "ld-check" }, box),
          h("td", { "data-label": "Post" }, h("div", { class: "ld-name" }, kindIcon(lead), h("div", {}, h("strong", { text: lead.name }), lead.notes ? h("div", { class: "muted", text: lead.notes }) : null)), badge),
          h("td", { "data-label": "What it says", class: "ld-says", text: requestText(lead) || "—" }),
          h("td", { "data-label": "Details" }, badges, tags, badges.length || tags.length ? null : h("span", { class: "muted", text: "—" })),
          h("td", { "data-label": "Post link" }, url ? externalLink(url, "Open the post") : h("span", { class: "muted", text: "—" })),
          h("td", { "data-label": "Fit" }, scoreBadge(lead)));
      }
      return h("tr", { class: dup ? "dup" : "" },
        h("td", { class: "ld-check" }, box),
        h("td", { "data-label": "Lead" }, h("div", { class: "ld-name" }, kindIcon(lead), h("div", {}, h("strong", { text: lead.name }), lead.kind === "person" && lead.company ? h("div", { class: "muted", text: lead.company }) : (lead.industry ? h("div", { class: "muted", text: lead.industry }) : null))), badge),
        h("td", { "data-label": "Title", text: lead.title || "—" }),
        h("td", { "data-label": "Location", text: lead.location || "—" }),
        h("td", { "data-label": "Contact" }, contactCell(lead)),
        h("td", { "data-label": "Fit" }, scoreBadge(lead)),
        h("td", { "data-label": "Signals" }, signalChips(lead)));
    }

    function paintResults(latest) {
      const response = state.response;
      if (!response) { fill(resultsBox); return; }
      const mode = state.source;
      const requests = mode === "requests";
      const maps = mode === "maps";
      const everything = state.results;
      if (!everything.length) {
        fill(resultsBox, ui.empty("No leads came back", (response.warnings || []).filter((w) => !/public posts/.test(w)).join(" ") || "Try broader words or another location.", null));
        return;
      }
      const leads = maps ? gapFilter(everything, state.gapOnly) : everything;
      const fresh = selectable(leads);
      const gaps = maps ? everything.filter(hasGap).length : 0;
      const noun = requests ? "post" : "lead";
      const summary = h("div", { class: "ld-summary", role: "status" },
        h("strong", { text: `${plural(leads.length, noun)} found` }),
        h("span", { text: `${fresh.length} new` }), h("span", { class: "muted", text: `${leads.length - fresh.length} already saved` }),
        maps ? h("span", { class: "muted", text: `${gaps} of ${everything.length} show a gap` }) : null,
        response.skipped ? h("span", { class: "muted", text: `${response.skipped} results skipped (not a ${requests ? "post" : "person or company page"})` }) : null,
        response.pages_read ? h("span", { class: "muted", text: `${plural(response.pages_read, "page")} read` }) : null,
        h("span", { class: "muted", text: planText({ credits: response.credits, cache_hit: response.cache_hit }, state.source) }));
      const warnings = (response.warnings || []).filter(Boolean).map((w) => h("p", { class: "ld-warning", text: w }));
      const gapToggle = maps ? (() => {
        const toggle = h("input", { type: "checkbox", id: "ldGapOnly", checked: state.gapOnly || null });
        toggle.addEventListener("change", () => { state.gapOnly = toggle.checked; paintResults(); });
        return h("label", { class: "check ld-gaptoggle" }, toggle, h("span", { text: "Show only businesses with a gap (no website, few reviews or a low rating)" }));
      })() : null;
      const all = h("input", { type: "checkbox", id: "ldSelectAll", "aria-label": "Select all new leads", checked: fresh.length > 0 && fresh.every((l) => state.selected.has(l.preview_id)) || null, disabled: fresh.length ? null : true });
      all.addEventListener("change", () => { if (all.checked) fresh.forEach((l) => state.selected.add(l.preview_id)); else state.selected.clear(); paintResults(); });
      const count = state.selected.size;
      const saveSel = btn(`Save selected (${count})`, { icon: "save", kind: "", disabled: !count, onClick: (event) => busy(event.currentTarget, () => saveLeads(everything.filter((l) => state.selected.has(l.preview_id)))) });
      const saveNew = btn(`Save all new (${fresh.length})`, { icon: "plus", disabled: !fresh.length, onClick: (event) => busy(event.currentTarget, () => saveLeads(fresh)) });
      const heads = requests ? ["Post", "What it says", "Details", "Link", "Fit"] : ["Lead", "Title", "Location", "Contact", "Fit", "Signals"];
      const table = leads.length
        ? h("div", { class: "table-wrap ld-table-wrap" }, h("table", { class: `table ld-table${requests ? " ld-requests" : ""}` },
          h("caption", { class: "visually-hidden", text: requests ? "Public posts asking for a service" : "Leads found by this search" }),
          h("thead", {}, h("tr", {}, h("th", { class: "ld-check" }, all), ...heads.map((text) => h("th", { text })))),
          h("tbody", {}, ...leads.map((lead) => resultRow(lead, mode)))))
        : h("p", { class: "muted ld-nogap", text: "None of these businesses shows a gap. Turn the filter off to see them all." });
      const moreButton = (response.next_start || response.next_page_token) && state.payload
        ? btn("Load more", { icon: "plus", onClick: (event) => busy(event.currentTarget, () => runSearch({ button: null, formNode: null, more: response.next_start ? { start: response.next_start } : { next_page_token: response.next_page_token } })) })
        : null;
      fill(resultsBox, h("section", { class: "panel ld-panel", "aria-labelledby": "ldResultsTitle" },
        h("div", { class: "panel-head" }, h("h2", { id: "ldResultsTitle", text: "Results" }), h("div", { class: "btn-row" }, picker.node, saveSel, saveNew)),
        h("div", { class: "panel-body" }, gapToggle, summary, ...warnings, table, requests ? h("p", { class: "ld-notice-line" }, ico("info"), " Replies are yours: open the post and answer in your own name. Aira never posts or messages for you.") : null, moreButton ? h("div", { class: "btn-row ld-more" }, moreButton) : null)));
    }

    async function saveLeads(chosen) {
      if (!chosen.length) return;
      const out = await api(`${BASE}/items`, { method: "POST", body: { leads: chosen, list_id: picker.value() }, expect: 201 });
      const names = new Set(chosen.map((l) => l.preview_id));
      for (const lead of state.results) if (names.has(lead.preview_id) && !lead.duplicate_of) lead.duplicate_of = { id: "", name: lead.name, list_id: out.list_id };
      state.selected = new Set();
      const list = lists.find((l) => l.id === out.list_id);
      if (list) list.lead_count += out.created; else lists.push({ id: out.list_id, name: "My leads", lead_count: out.created });
      paintResults();
      toast(`Saved ${out.created}${out.duplicates ? `, ${out.duplicates} were already saved` : ""}.`, "success");
      announce(`Saved ${out.created} leads.`);
    }

    // ---- CSV ----
    function csvForm() {
      const text = h("textarea", { id: "ldCsvText", rows: 7, placeholder: "Name,Title,Company,Email,Website,LinkedIn\nJane Doe,VP Sales,Acme,jane@acme.com,acme.com,https://www.linkedin.com/in/jane-doe", "aria-label": "CSV text", spellcheck: "false" });
      const file = h("input", { id: "ldCsvFile", type: "file", accept: ".csv,text/csv,text/plain", "aria-label": "Choose a CSV file" });
      file.addEventListener("change", async () => {
        const chosen = file.files && file.files[0];
        if (!chosen) return;
        if (chosen.size > 3_000_000) { toast("That file is larger than 3 MB. Split it and import the parts.", "error"); file.value = ""; return; }
        text.value = await chosen.text();
        toast(`Loaded ${chosen.name}. Review it, then import.`, "success");
      });
      const result = h("div", { class: "ld-csv-result", role: "status", "aria-live": "polite" });
      const go = h("button", { type: "button", class: "btn", id: "ldCsvImport" }, ico("download"), h("span", { text: "Import leads" }));
      go.addEventListener("click", () => busy(go, async () => {
        if (!text.value.trim()) { toast("Choose a CSV file or paste its text first.", "error"); return; }
        const out = await api(`${BASE}/items/import-csv`, { method: "POST", body: { csv: text.value, list_id: picker.value() }, expect: 201 });
        fill(result, h("div", { class: "ld-notice" }, h("span", { class: "ld-notice-ico" }, ico("info")), h("div", {}, h("strong", { text: `${plural(out.created, "lead")} imported` }), h("p", { class: "muted", text: `${out.duplicates} already saved, ${out.failed || 0} could not be saved, out of ${plural(out.rows, "row")}.` }), (out.skipped || []).length ? h("ul", { class: "ld-skipped" }, ...out.skipped.map((s) => h("li", { text: s }))) : null, btn("Open Leads", { kind: "secondary", sm: true, href: link("leads", { list_id: out.list_id }) }))));
        const list = lists.find((l) => l.id === out.list_id);
        if (list) list.lead_count += out.created;
        toast(`Imported ${out.created} leads.`, "success");
      }));
      return h("div", { class: "ld-form", id: "ldCsvForm" },
        h("p", { class: "muted", text: "Use a header row. Known columns: Name, First name, Last name, Title, Company, Website, Email, Phone, LinkedIn, Location, Industry, Notes, Tags. Unknown columns are ignored. Duplicates of leads you already saved are skipped." }),
        h("div", { class: "field" }, h("label", { for: "ldCsvFile", text: "CSV file" }), file),
        h("div", { class: "field" }, h("label", { for: "ldCsvText", text: "…or paste the CSV text" }), text),
        h("div", { class: "btn-row" }, picker.node, go), result);
    }

    // ---- first-run help ----
    const intro = overview.counts.total === 0 && !overview.runs.length
      ? h("section", { class: "ld-intro", "aria-label": "How to get leads" },
        h("h2", { text: "Four ways to get leads" }),
        h("ol", {},
          h("li", {}, h("strong", { text: "Search Google. " }), "Local businesses from Google Maps (phone and website included), or people and companies from LinkedIn pages Google has indexed."),
          h("li", {}, h("strong", { text: "Find people who need your services. " }), "Public posts where someone asks for a website, marketing, content or ads help, and local businesses with no website, few reviews or a low rating."),
          h("li", {}, h("strong", { text: "Read your Sales Navigator. " }), "Build a search in LinkedIn Sales Navigator, paste its address, and Aira reads the results in your own session, read only."),
          h("li", {}, h("strong", { text: "Import a CSV. " }), "Bring leads from anywhere. Everything lands in lists you can score, enrich and export.")))
      : null;

    paintOffered(); paintSources(); paintForm(); paintSaved();
    fill(header, intro, offeredBox, h("div", { class: "panel ld-panel" }, h("div", { class: "panel-head" }, h("h2", { id: "ldFindTitle", text: "Find leads" })), h("div", { class: "panel-body" }, sourceRow, formBox)), resultsBox, savedBox);
    fill(content, header);
    if (deep.source) { const first = formBox.querySelector("input, textarea"); if (first && first.focus) first.focus(); }
  }

  // ===========================================================================
  // Leads
  // ===========================================================================

  async function renderLeads(content, ctx) {
    const { overview } = ctx;
    const leadsCatalog = await loadCatalog();
    offeredCache = offeredFrom(overview, leadsCatalog);
    const lists = overview.lists.slice();
    const filters = { list_id: ctx.route.query.list_id || "", status: "", source: "", kind: "", q: "", min_score: "", has_email: "", tag: "", sort: "updated", limit: 25, offset: 0 };
    const state = { leads: [], total: 0, selected: new Set(), loading: false };

    const sidebar = h("nav", { class: "ld-lists", "aria-label": "Lists" });
    const bar = h("div", { class: "ld-filters", role: "search" });
    const bulk = h("div", { class: "ld-bulk", role: "region", "aria-label": "Actions for the selected leads", hidden: true });
    const tableBox = h("div", { class: "ld-tablebox", "aria-live": "polite" });
    const pager = h("div", { class: "ld-pager" });
    const exportRow = h("div", { class: "btn-row ld-export" });

    const total = overview.counts.total;
    if (!total) {
      fill(content, ui.empty("No leads yet", "Find leads from Google Maps, Google, your Sales Navigator or a CSV file, and save the ones you want. They show up here.", btn("Find leads", { kind: "", href: link("find") })));
      return;
    }

    const debounced = (fn, ms) => { let timer = null; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; };
    const reload = async ({ keepPage = false } = {}) => {
      if (!keepPage) filters.offset = 0;
      state.loading = true;
      fill(tableBox, ui.skeleton(4));
      try {
        const out = await api(`${BASE}/items?${leadQuery(filters)}`, { silent: true });
        state.leads = out.leads; state.total = out.total;
        if (filters.offset && !out.leads.length && out.total) { filters.offset = Math.max(0, Math.floor((out.total - 1) / filters.limit) * filters.limit); return reload({ keepPage: true }); }
      } catch (error) { fill(tableBox, problem(error, () => reload({ keepPage: true }))); return; }
      finally { state.loading = false; }
      state.selected = new Set([...state.selected].filter((id) => state.leads.some((l) => l.id === id)));
      paintTable(); paintExport();
    };

    async function refreshLists() {
      const out = await api(`${BASE}/lists`, { silent: true });
      lists.splice(0, lists.length, ...out.lists);
      paintLists();
    }

    function paintLists() {
      const sum = lists.reduce((n, l) => n + l.lead_count, 0);
      const item = (id, name, count) => h("button", { type: "button", class: `ld-list${filters.list_id === id ? " on" : ""}`, "aria-current": filters.list_id === id ? "true" : "false", onclick: () => { filters.list_id = id; paintLists(); reload(); } }, h("span", { class: "ld-list-name", text: name }), h("span", { class: "count", text: String(count) }));
      const current = lists.find((l) => l.id === filters.list_id);
      fill(sidebar,
        h("h2", { class: "ld-side-title", text: "Lists" }),
        item("", "All leads", sum), ...lists.map((l) => item(l.id, l.name, l.lead_count)),
        h("div", { class: "btn-row ld-list-actions" },
          btn("New", { icon: "plus", sm: true, aria: "New list", onClick: async () => { const created = await newListDialog(); if (created) { await refreshLists(); } } }),
          current ? btn("Rename", { icon: "edit", sm: true, kind: "ghost", aria: `Rename ${current.name}`, onClick: () => renameList(current) }) : null,
          current ? btn("Delete", { icon: "trash", sm: true, kind: "ghost", aria: `Delete ${current.name}`, onClick: () => deleteList(current) }) : null));
    }
    function renameList(list) {
      const name = h("input", { id: "ldRename", type: "text", maxlength: "80", value: list.name });
      const desc = h("input", { id: "ldRenameDesc", type: "text", maxlength: "300", value: list.description || "" });
      Aira.modal({ title: "Rename list", body: h("div", { class: "ld-form" }, ui.field("Name", name), ui.field("Description", desc)), actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save", kind: "", onClick: async () => { await api(`${BASE}/lists/${list.id}`, { method: "PATCH", body: { name: name.value, description: desc.value } }); await refreshLists(); toast("List renamed.", "success"); } }] });
      name.focus(); name.select();
    }
    async function deleteList(list) {
      if (!(await Aira.confirm({ title: `Delete “${list.name}”`, message: `This deletes the list and its ${plural(list.lead_count, "lead")}. It cannot be undone.`, confirmLabel: "Delete list", danger: true }))) return;
      const out = await api(`${BASE}/lists/${list.id}`, { method: "DELETE" });
      toast(`List deleted (${plural(out.leads_removed, "lead")} removed).`, "success");
      filters.list_id = ""; await refreshLists(); await reload();
    }

    // ---- filters ----
    const searchBox = h("input", { id: "ldFilterText", type: "search", "data-search": "true", placeholder: "Search name, company, title…", "aria-label": "Search leads", autocomplete: "off" });
    searchBox.addEventListener("input", debounced(() => { filters.q = searchBox.value; reload(); }, 250));
    const selectOf = (id, label, options, key) => { const s = ui.select(options, filters[key], { id, "aria-label": label }); s.addEventListener("change", () => { filters[key] = s.value; reload(); }); return s; };
    const tagBox = h("input", { id: "ldFilterTag", type: "text", placeholder: "Tag", "aria-label": "Filter by tag", maxlength: "40" });
    tagBox.addEventListener("input", debounced(() => { filters.tag = tagBox.value; reload(); }, 250));
    fill(bar, searchBox,
      selectOf("ldFilterStatus", "Status", [["", "Any status"], ...STATUSES], "status"),
      selectOf("ldFilterSource", "Source", [["", "Any source"], ...Object.entries(SOURCE_LABELS)], "source"),
      selectOf("ldFilterKind", "People, companies or requests", [["", "All kinds"], ["person", "People"], ["company", "Companies"], ["request", "Public requests"]], "kind"),
      selectOf("ldFilterScore", "Minimum fit score", [["", "Any fit"], ["40", "Fit 40+"], ["60", "Fit 60+"], ["80", "Fit 80+"]], "min_score"),
      selectOf("ldFilterEmail", "Email", [["", "Any email"], ["true", "Has an email"], ["false", "No email"]], "has_email"),
      tagBox,
      selectOf("ldSort", "Sort by", SORTS, "sort"));

    // ---- table ----
    function paintTable() {
      const leads = state.leads;
      if (!leads.length) {
        fill(tableBox, ui.empty("No leads match", "Change the filters, or find more leads.", btn("Find leads", { kind: "", href: link("find") })));
        fill(pager); paintBulk(); return;
      }
      const all = h("input", { type: "checkbox", id: "ldLeadsAll", "aria-label": "Select every lead on this page", checked: leads.every((l) => state.selected.has(l.id)) || null });
      all.addEventListener("change", () => { if (all.checked) leads.forEach((l) => state.selected.add(l.id)); else state.selected.clear(); paintTable(); });
      fill(tableBox, h("div", { class: "table-wrap ld-table-wrap" }, h("table", { class: "table ld-table" },
        h("caption", { class: "visually-hidden", text: "Your leads" }),
        h("thead", {}, h("tr", {}, h("th", { class: "ld-check" }, all), h("th", { text: "Lead" }), h("th", { text: "Title" }), h("th", { text: "Contact" }), h("th", { text: "Status" }), h("th", { text: "Fit" }), h("th", { text: "Source" }))),
        h("tbody", {}, ...leads.map((lead) => {
          const box = h("input", { type: "checkbox", "aria-label": `Select ${lead.name}`, checked: state.selected.has(lead.id) || null });
          box.addEventListener("change", () => { if (box.checked) state.selected.add(lead.id); else state.selected.delete(lead.id); paintBulk(); });
          return h("tr", { dataset: { id: lead.id } },
            h("td", { class: "ld-check" }, box),
            h("td", { "data-label": "Lead" }, h("button", { type: "button", class: "ld-open", onclick: () => openLead(lead, { onUpdate: (updated) => { Object.assign(lead, updated); paintTable(); }, onDelete: async () => { await refreshLists(); await reload({ keepPage: true }); }, onClose: () => {} }) }, kindIcon(lead), h("span", {}, h("strong", { text: lead.name }), lead.company && lead.kind === "person" ? h("small", { class: "muted", text: lead.company }) : null))),
            h("td", { "data-label": "Title", text: lead.title || (lead.industry || "—") }),
            h("td", { "data-label": "Contact" }, contactCell(lead)),
            h("td", { "data-label": "Status" }, h("span", { class: `ld-status ${lead.status}`, text: (STATUSES.find(([id]) => id === lead.status) || [0, lead.status])[1] })),
            h("td", { "data-label": "Fit" }, scoreBadge(lead)),
            h("td", { "data-label": "Source" }, h("span", { class: "badge source", text: sourceLabel(lead.source) })));
        })))));
      paintBulk();
      const start = filters.offset;
      fill(pager,
        h("span", { class: "muted", text: rangeText(start, leads.length, state.total) }),
        h("div", { class: "btn-row" },
          btn("Previous", { icon: null, sm: true, disabled: start === 0, onClick: () => { filters.offset = Math.max(0, start - filters.limit); state.selected.clear(); reload({ keepPage: true }); } }),
          btn("Next", { sm: true, disabled: start + leads.length >= state.total, onClick: () => { filters.offset = start + filters.limit; state.selected.clear(); reload({ keepPage: true }); } })));
    }

    // ---- bulk bar ----
    function paintBulk() {
      const ids = [...state.selected];
      bulk.hidden = !ids.length;
      if (!ids.length) { fill(bulk); return; }
      const move = ui.select([["", "Move to…"], ...lists.map((l) => [l.id, l.name])], "", { "aria-label": "Move the selected leads to a list", id: "ldBulkMove" });
      const status = ui.select([["", "Set status…"], ...STATUSES], "", { "aria-label": "Set status of the selected leads", id: "ldBulkStatus" });
      const tag = h("input", { id: "ldBulkTag", type: "text", maxlength: "40", placeholder: "Tag", "aria-label": "Tag for the selected leads" });
      const apply = async (body, done) => { const out = await api(`${BASE}/items/bulk`, { method: "POST", body: { ids, ...body } }); toast(`${plural(out.changed, "lead")} updated.`, "success"); state.selected.clear(); await refreshLists(); await reload({ keepPage: true }); if (done) done(); };
      move.addEventListener("change", () => { if (move.value) busy(move, () => apply({ action: "move", value: move.value })); });
      status.addEventListener("change", () => { if (status.value) busy(status, () => apply({ action: "status", value: status.value })); });
      fill(bulk, h("strong", { text: `${ids.length} selected` }), btn("Write emails", { icon: "mail", sm: true, kind: "", title: "Write a draft for each selected lead. Nothing is sent until you approve it in the Outbox.", onClick: (event) => { const outreach = Aira.leads.outreach; if (outreach) busy(event.currentTarget, () => outreach.writeEmails(ids)); } }), move, status, tag,
        btn("Add tag", { sm: true, onClick: (event) => { if (!tag.value.trim()) { tag.focus(); return; } busy(event.currentTarget, () => apply({ action: "tag", value: tag.value })); } }),
        btn("Remove tag", { sm: true, kind: "ghost", onClick: (event) => { if (!tag.value.trim()) { tag.focus(); return; } busy(event.currentTarget, () => apply({ action: "untag", value: tag.value })); } }),
        btn("Delete", { icon: "trash", sm: true, kind: "danger", onClick: async (event) => { if (!(await Aira.confirm({ title: `Delete ${plural(ids.length, "lead")}`, message: "They are removed from your lists. This cannot be undone.", confirmLabel: "Delete", danger: true }))) return; busy(event.currentTarget, () => apply({ action: "delete" })); } }));
    }

    function paintExport() {
      fill(exportRow,
        btn("Export CSV", { icon: "download", sm: true, href: exportHref("csv", filters), download: true }),
        btn("Export Excel", { icon: "download", sm: true, href: exportHref("xlsx", filters), download: true }),
        h("small", { class: "muted", text: "Exports the current filters." }));
    }

    paintLists();
    const extras = Aira.leads.extras;
    const followNode = extras && extras.followUps ? extras.followUps({ openById: (id) => openLeadById(id, () => reload({ keepPage: true })) }) : null;
    fill(content, h("div", { class: "ld-leads" }, sidebar, h("div", { class: "ld-main" }, bar, h("div", { class: "ld-toolbar" }, followNode, exportRow), bulk, tableBox, pager)));
    await reload();
  }

  // ---- the lead drawer --------------------------------------------------------------------------------------------------

  const LEAD_FIELDS = [
    { name: "name", label: "Name" }, { name: "title", label: "Job title" }, { name: "company", label: "Company" },
    { name: "location", label: "Location" }, { name: "website", label: "Website", placeholder: "https://" }, { name: "email", label: "Email", type: "email" },
    { name: "phone", label: "Phone" }, { name: "linkedin_url", label: "LinkedIn", placeholder: "https://www.linkedin.com/in/…" }, { name: "industry", label: "Industry" },
  ];

  async function openLeadById(id, onChange) {
    const out = await api(`${BASE}/items/${id}`);
    return openLead(out.lead, { onUpdate: () => {}, onDelete: async () => { if (onChange) await onChange(); }, onClose: () => {} });
  }

  function openLead(initial, ctx) {
    let lead = initial;
    const inputs = {};
    const body = h("div", { class: "ld-drawer" });
    const drawer = Aira.drawer({ title: lead.name, subtitle: h("span", { text: (lead.kind === "person" ? [lead.title, lead.company] : [lead.industry, lead.location]).filter(Boolean).join(" · ") || sourceLabel(lead.source) }), body, onClose: ctx.onClose });
    const enrichBox = h("div", { class: "ld-enrich" });
    const newsBox = h("div", { class: "ld-news" });

    const helpers = { update: (next) => { lead = next; ctx.onUpdate(lead); paint(); }, repaint: () => paint(), catalog: () => catalogCache || FALLBACK_SERVICES, offered: liveOffered };
    const paint = () => {
      const isRequest = lead.kind === "request";
      const fieldDefs = isRequest ? LEAD_FIELDS.filter((f) => f.name === "name") : LEAD_FIELDS;
      const extra = !isRequest && Aira.leads.extras && Aira.leads.extras.drawerSections ? Aira.leads.extras.drawerSections(lead, helpers) : {};
      const status = ui.select(STATUSES, lead.status, { id: "ldStatus", "aria-label": "Status" });
      const tags = h("input", { id: "ldTags", type: "text", value: (lead.tags || []).join(", "), maxlength: "300", placeholder: "warm, event-2026" });
      const notes = h("textarea", { id: "ldNotes", rows: 4, maxlength: "4000", placeholder: "What you learned, who you talked to…" });
      notes.value = lead.notes || "";
      const fields = fieldDefs.map((field) => { const node = textField(field, lead[field.name], { id: `ldf-${field.name}` }); inputs[field.name] = node.querySelector("input"); return node; });
      const saveButton = btn("Save changes", { icon: "save", kind: "", onClick: (event) => busy(event.currentTarget, async () => {
        const changes = Object.fromEntries(fieldDefs.map((f) => [f.name, inputs[f.name].value]));
        const out = await api(`${BASE}/items/${lead.id}`, { method: "PATCH", body: { ...changes, status: status.value, notes: notes.value, tags: splitList(tags.value, 20) } });
        lead = out.lead; ctx.onUpdate(lead); paint(); toast("Lead saved.", "success");
      }) });
      const origin = lead.source_ref && safeUrl(lead.source_ref) ? externalLink(lead.source_ref, isRequest ? "Open the post" : "Open the source page") : null;
      const postBlock = isRequest ? h("section", { class: "ld-block ld-post", "aria-label": "The post" }, h("h3", { text: "The post" }),
        h("p", { class: "ld-says", text: requestText(lead) || "—" }),
        h("div", { class: "ld-badges" }, ...requestBadges(lead).map((b) => chip(b.text, b.tone)), ...(lead.tags || []).map((id) => chip(serviceLabel(catalogCache, id), "ok"))),
        requestUrl(lead) ? h("p", {}, externalLink(requestUrl(lead), "Open the post")) : null,
        h("p", { class: "ld-notice-line" }, ico("info"), " Reply on the post yourself, in your own name. Aira has no way to post, email or call for you.")) : null;
      const links = [lead.linkedin_url ? externalLink(lead.linkedin_url, "LinkedIn profile") : null, lead.sales_nav_url ? externalLink(lead.sales_nav_url, "Sales Navigator") : null, lead.website ? externalLink(lead.website, "Website") : null].filter(Boolean);
      fill(body,
        h("section", { class: "ld-block", "aria-label": "Fit" },
          h("div", { class: "ld-fit" }, scoreBadge(lead), h("div", {}, h("strong", { text: lead.score === null || lead.score === undefined ? "Not scored yet" : `Fit ${lead.score} of 100` }), lead.score === null || lead.score === undefined ? h("p", { class: "muted", text: "Describe your ideal customer in the Settings tab to score your leads." }) : null)),
          (lead.score_reasons || []).length ? h("ul", { class: "ld-reasons" }, ...lead.score_reasons.map((r) => h("li", { text: r }))) : null),
        h("section", { class: "ld-block", "aria-label": "Details" }, h("div", { class: "ld-grid" }, ...fields),
          h("div", { class: "ld-grid" }, h("div", { class: "field" }, h("label", { for: "ldStatus", text: "Status" }), status), h("div", { class: "field" }, h("label", { for: "ldTags", text: "Tags" }), tags, h("span", { class: "hint", text: "Separate with commas." }))),
          h("div", { class: "field" }, h("label", { for: "ldNotes", text: "Notes" }), notes),
          h("div", { class: "btn-row" }, saveButton)),
        postBlock,
        extra.needs || null,
        (lead.signals || []).length ? h("section", { class: "ld-block", "aria-label": "Signals" }, h("h3", { text: "Signals" }), h("ul", { class: "ld-signals" }, ...lead.signals.map((s) => h("li", {}, chip(NEED_TYPES[s.type] || (s.type || "signal").replace(/_/g, " "), NEED_TYPES[s.type] ? "warn" : s.type === "hiring" || s.type === "news" ? "hot" : ""), " ", s.url ? externalLink(s.url, s.text) : s.text)))) : null,
        h("section", { class: "ld-block", "aria-label": "Source" }, h("h3", { text: "Where it came from" }),
          h("p", {}, h("span", { class: "badge source", text: sourceLabel(lead.source) }), " ", origin, lead.rating ? h("span", { class: "muted", text: ` · ${lead.rating}★${lead.reviews ? ` (${lead.reviews.toLocaleString()} reviews)` : ""}` }) : null, " ", `Added ${fmt.datetime(lead.created_at)}`),
          links.length ? h("p", { class: "ld-links" }, ...links.flatMap((l, i) => (i ? [" · ", l] : [l]))) : null),
        isRequest ? null : extra.audit || null,
        isRequest ? null : h("section", { class: "ld-block", "aria-label": "Enrich from website" }, h("h3", { text: "Contact details from the website" }), enrichBox),
        isRequest ? null : h("section", { class: "ld-block", "aria-label": "Recent news" }, h("h3", { text: "Recent news" }), newsBox),
        isRequest ? null : extra.call || null,
        isRequest ? null : extra.email || null,
        h("section", { class: "ld-block ld-actions", "aria-label": "Actions" },
          h("div", { class: "btn-row" },
            isRequest ? null : btn("Draft outreach", { icon: "mail", kind: "secondary", onClick: () => draftDialog(lead) }),
            btn("Delete lead", { icon: "trash", kind: "danger", onClick: async () => { if (!(await Aira.confirm({ title: "Delete this lead", message: `Delete ${lead.name}? This cannot be undone.`, confirmLabel: "Delete", danger: true }))) return; await api(`${BASE}/items/${lead.id}`, { method: "DELETE" }); drawer.close(); toast("Lead deleted.", "success"); if (ctx.onDelete) await ctx.onDelete(); } }))));
      if (!isRequest) { paintEnrich(); paintNews(); }
    };

    function paintEnrich() {
      const data = lead.enrichment || {};
      const emails = data.emails || [];
      const phones = data.phones || [];
      const has = Boolean(lead.enriched_at);
      const run = btn(has ? "Read the website again" : "Enrich from website", { icon: "search", disabled: !lead.website, title: lead.website ? "" : "Add the company's website first", onClick: (event) => busy(event.currentTarget, async () => { const out = await api(`${BASE}/items/${lead.id}/enrich`, { method: "POST", body: {} }); lead = out.lead; ctx.onUpdate(lead); paint(); toast("Website read.", "success"); }) });
      const useEmail = (address) => btn("Use this email", { sm: true, kind: "secondary", disabled: lead.email === address, onClick: (event) => busy(event.currentTarget, async () => { const out = await api(`${BASE}/items/${lead.id}`, { method: "PATCH", body: { email: address } }); lead = out.lead; ctx.onUpdate(lead); paint(); toast("Email saved on the lead.", "success"); }) });
      const usePhone = (number) => btn("Use this phone", { sm: true, kind: "secondary", disabled: lead.phone === number, onClick: (event) => busy(event.currentTarget, async () => { const out = await api(`${BASE}/items/${lead.id}`, { method: "PATCH", body: { phone: number } }); lead = out.lead; ctx.onUpdate(lead); paint(); toast("Phone saved on the lead.", "success"); }) });
      fill(enrichBox,
        h("p", { class: "muted", text: "Aira reads the company's own public website (the home page and a few contact, about and team pages), respects robots.txt, and shows only what the site publishes, with the page it came from. It never guesses an address. Role addresses such as info@ or sales@ are labelled as such." }),
        h("div", { class: "btn-row" }, run, lead.website ? null : h("span", { class: "muted", text: "This lead has no website yet." })),
        has ? h("div", { class: "ld-found" },
          emails.length ? h("ul", { class: "ld-contacts", "aria-label": "Emails found" }, ...emails.map((e) => h("li", {}, h("div", {}, h("strong", { text: e.address }), " ", chip(e.kind === "generic" ? "role address" : "personal", e.kind === "generic" ? "" : "ok"), e.same_domain ? null : chip("another domain", "warn"), h("div", { class: "muted" }, "Found on ", externalLink(e.source_url, e.source_url.replace(/^https?:\/\//, "")))), useEmail(e.address)))) : h("p", { class: "muted", text: "No email address is published on the pages Aira could read." }),
          phones.length ? h("ul", { class: "ld-contacts", "aria-label": "Phone numbers found" }, ...phones.map((p) => h("li", {}, h("div", {}, h("strong", { text: p.number }), h("div", { class: "muted" }, "Found on ", externalLink(p.source_url, p.source_url.replace(/^https?:\/\//, "")))), usePhone(p.number)))) : null,
          Object.keys(data.socials || {}).length ? h("p", { class: "ld-links" }, ...Object.entries(data.socials).flatMap(([name, url], i) => (i ? [" · ", externalLink(url, name)] : [externalLink(url, name)]))) : null,
          data.robots_blocked ? h("p", { class: "ld-warning", text: "Some pages were skipped because the site's robots.txt asks automated visitors not to read them." }) : null,
          (data.errors || []).length ? h("p", { class: "muted", text: `Notes: ${data.errors.join("; ")}` }) : null,
          h("p", { class: "muted", text: `Read ${plural((data.pages || []).length, "page")} ${fmt.datetime(lead.enriched_at)}.` })) : null);
    }
    function paintNews() {
      const company = lead.kind === "person" ? lead.company : (lead.company || lead.name);
      const cost = h("p", { class: "ld-cost", id: "ldNewsCost", role: "status", "aria-live": "polite" });
      const find = btn("Find recent news", { icon: "search", disabled: !company, title: company ? "" : "This lead has no company to look up", onClick: (event) => busy(event.currentTarget, async () => {
        const plan = await api(`${BASE}/items/${lead.id}/signals`, { method: "POST", body: { dry_run: true } });
        cost.textContent = planText(plan, "news");
        if (needsConfirm(plan, session)) {
          const go = await Aira.confirm({ title: "Look up recent news?", message: `${planText(plan, "news")} Aira asks once per visit before it spends SerpApi searches.`, confirmLabel: "Look up news" });
          if (!go) return;
          session.confirmed = true;
        }
        const out = await api(`${BASE}/items/${lead.id}/signals`, { method: "POST", body: {} });
        lead = out.lead; ctx.onUpdate(lead);
        paint();
        const after = document.getElementById("ldNewsCost");
        if (after) after.textContent = out.found ? `${planText({ credits: out.credits, cache_hit: out.cache_hit }, "news")}` : "No recent headlines were found.";
        toast(out.found ? `Added ${plural(out.found, "news signal")}.` : "No recent headlines were found.", out.found ? "success" : "");
      }) });
      const news = (lead.signals || []).filter((s) => s.type === "news");
      fill(newsBox,
        h("p", { class: "muted", text: "Looks up recent Google News headlines about the lead's company and keeps up to three as buying signals. Headlines only: Aira does not read the articles, and a headline is not proof of anything." }),
        h("div", { class: "btn-row" }, find, company ? null : h("span", { class: "muted", text: "This lead has no company yet." })), cost,
        news.length ? h("ul", { class: "ld-signals", "aria-label": "News signals" }, ...news.map((s) => h("li", {}, chip("news", "hot"), " ", s.url ? externalLink(s.url, s.text) : s.text))) : null);
    }
    paint();
    return drawer;
  }

  function draftDialog(lead) {
    const tone = ui.select([["friendly", "Friendly"], ["direct", "Direct"], ["formal", "Formal"]], "friendly", { id: "ldTone" });
    const channel = ui.select([["email", "Email"], ["linkedin", "LinkedIn note (300 characters)"]], "email", { id: "ldChannel" });
    const offer = h("textarea", { id: "ldOffer", rows: 3, maxlength: "400", placeholder: "What you offer, in a sentence: “We cut onboarding time in half for sales teams.”" });
    const out = h("div", { class: "ld-draft", "aria-live": "polite" });
    const generate = h("button", { type: "button", class: "btn", id: "ldDraftGo" }, ico("spark"), h("span", { text: "Write a draft" }));
    generate.addEventListener("click", () => busy(generate, async () => {
      const result = await api(`${BASE}/items/${lead.id}/draft`, { method: "POST", body: { tone: tone.value, channel: channel.value, offer: offer.value } });
      const draft = result.draft;
      const subject = h("input", { id: "ldDraftSubject", type: "text", value: draft.subject || "", readonly: "true", "aria-label": "Subject" });
      const text = h("textarea", { id: "ldDraftBody", rows: 9, "aria-label": "Message" });
      text.value = draft.body;
      fill(out,
        h("p", { class: "muted", text: draft.source === "model" ? "Written by your language model from this lead's facts." : "Filled in from a template with this lead's facts (no model was available)." }),
        draft.subject ? h("div", { class: "field" }, h("label", { for: "ldDraftSubject", text: "Subject" }), subject) : null,
        h("div", { class: "field" }, h("label", { for: "ldDraftBody", text: "Message (edit it before you use it)" }), text),
        h("div", { class: "btn-row" }, draft.subject ? btn("Copy subject", { icon: "copy", sm: true, onClick: () => copyText(subject.value, "Subject copied") }) : null, btn("Copy message", { icon: "copy", sm: true, onClick: () => copyText(text.value, "Message copied") })));
    }));
    const modal = Aira.modal({ title: `Draft a message to ${lead.name}`, wide: true,
      body: h("div", { class: "ld-form" },
        h("p", { class: "ld-notice-line" }, ico("info"), " Aira only writes the words. Drafts are never sent: copy it and send it yourself, from your own email or LinkedIn."),
        h("div", { class: "ld-grid" }, ui.field("Channel", channel), ui.field("Tone", tone)),
        ui.field("What you offer", offer, "Used as written. Aira adds nothing that is not on this lead."), h("div", { class: "btn-row" }, generate), out),
      actions: [{ label: "Close", kind: "ghost" }] });
    offer.focus();
    return modal;
  }

  // ===========================================================================
  // Settings (ideal customer profile) and History
  // ===========================================================================

  async function renderSettings(content, ctx) {
    const icp = ctx.overview.icp || {};
    const catalog = await loadCatalog();
    const list = (name) => (icp[name] || []).join(", ");
    const defs = [
      { name: "titles", label: "Target job titles", placeholder: "VP Sales, Head of Sales", hint: "People with these titles score higher." },
      { name: "keywords", label: "Keywords", placeholder: "SaaS, fintech", hint: "Words in a lead's title, company or industry." },
      { name: "industries", label: "Industries", placeholder: "software, logistics" },
      { name: "locations", label: "Locations", placeholder: "Austin, Texas" },
      { name: "exclude_keywords", label: "Never include (words)", placeholder: "recruiter, intern", hint: "A lead that mentions one of these scores 0." },
      { name: "exclude_companies", label: "Never include (companies)", placeholder: "Competitor Inc" },
    ];
    const inputs = {};
    const fields = defs.map((def) => { const node = textField({ ...def, wide: false }, list(def.name), { id: `ldicp-${def.name}` }); inputs[def.name] = node.querySelector("input"); return node; });
    const channels = [["website", "Website"], ["phone", "Phone"], ["email", "Email"], ["linkedin", "LinkedIn"]];
    const boxes = channels.map(([id, label]) => h("label", { class: "check" }, h("input", { type: "checkbox", value: id, checked: (icp.require || []).includes(id) || null }), h("span", { text: label })));
    const serviceBoxes = catalog.map((service) => h("label", { class: "check" }, h("input", { type: "checkbox", id: `ldicp-service-${service.id}`, value: service.id, checked: (icp.services || []).includes(service.id) || null }), h("span", { text: service.label })));
    const rating = h("input", { id: "ldicp-min_rating", type: "number", min: "0", max: "5", step: "0.1", value: icp.min_rating === null || icp.min_rating === undefined ? "" : String(icp.min_rating) });
    const result = h("p", { class: "ld-formerror ok", id: "ldIcpResult", role: "status", "aria-live": "polite" });
    const save = h("button", { type: "submit", class: "btn", id: "ldIcpSave" }, ico("save"), h("span", { text: "Save and re-score leads" }));
    const form = h("form", { class: "ld-form", id: "ldIcpForm", novalidate: "true", "aria-label": "Ideal customer profile", onsubmit: (event) => {
      event.preventDefault();
      busy(save, async () => {
        const payload = icpPayload({ ...Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value])), require: boxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => i.value), services: serviceBoxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => i.value), min_rating: rating.value });
        const out = await api(`${BASE}/icp`, { method: "POST", body: payload });
        remember("services", payload.services);
        result.textContent = `Saved. ${plural(out.rescored, "lead")} re-scored.`;
        toast(`Saved. ${plural(out.rescored, "lead")} re-scored.`, "success");
      });
    } },
      h("div", { class: "ld-grid" }, ...fields),
      h("fieldset", { class: "ld-fieldset" }, h("legend", { text: "Must be reachable by" }), h("div", { class: "ld-checks" }, ...boxes), h("span", { class: "hint", text: "A lead missing one of these has its score halved." })),
      h("fieldset", { class: "ld-fieldset", id: "ldIcpServices" }, h("legend", { text: "Services you offer" }), h("div", { class: "ld-checks" }, ...serviceBoxes), h("span", { class: "hint", text: "A lead whose own signals call for one of these (no website, a weak site, a public request for it, a hiring signal for the role) scores higher, and the reason is listed." })),
      h("div", { class: "field narrow" }, h("label", { for: "ldicp-min_rating", text: "Minimum Google rating (businesses)" }), rating),
      h("div", { class: "btn-row" }, save), result);
    fill(content, h("section", { class: "panel ld-panel" }, h("div", { class: "panel-head" }, h("h2", { text: "Ideal customer profile" }), h("small", { class: "muted", text: ctx.overview.icp_empty ? "Not set: leads have no score yet." : icpSummary(icp) })),
      h("div", { class: "panel-body" }, h("p", { class: "muted", text: "Aira scores every lead from 0 to 100 against these rules and shows which rules fired. The score is rules you can read, not a model. With nothing set, leads are not scored." }), form)));
  }

  async function renderHistory(content, ctx) {
    const runs = ctx.overview.runs || [];
    if (!runs.length) { fill(content, ui.empty("No searches yet", "Every search you run is listed here with what it found, added and cost.", btn("Find leads", { kind: "", href: link("find") }))); return; }
    fill(content, h("section", { class: "panel ld-panel" }, h("div", { class: "panel-head" }, h("h2", { text: "Recent searches" }), h("small", { class: "muted", text: "Google searches also appear in Aira API > History, tagged leads." })),
      h("div", { class: "table-wrap ld-table-wrap" }, h("table", { class: "table ld-table", id: "ldHistory" },
        h("caption", { class: "visually-hidden", text: "Recent lead searches" }),
        h("thead", {}, h("tr", {}, ...["When", "Source", "Search", "Result", "Credits"].map((t) => h("th", { text: t })))),
        h("tbody", {}, ...runs.map((run) => h("tr", { class: run.status === "error" ? "bad" : "" },
          h("td", { "data-label": "When", text: fmt.datetime(run.created_at) }),
          h("td", { "data-label": "Source" }, h("span", { class: "badge source", text: sourceLabel(run.source) })),
          h("td", { "data-label": "Search", text: Object.entries(run.params || {}).filter(([k]) => !["engine", "hl", "type"].includes(k)).map(([, v]) => (Array.isArray(v) ? v.join(", ") : String(v))).join(" · ").slice(0, 120) || "—" }),
          h("td", { "data-label": "Result", text: describeRun(run) }),
          h("td", { "data-label": "Credits" }, run.record_id ? h("a", { href: `#/airaapi?tab=history`, text: `${run.credits}${run.cache_hit ? " (cached)" : ""}` }) : String(run.credits)))))))));
  }

  // ===========================================================================
  // Shell
  // ===========================================================================

  Aira.leads.TABS = TABS;
  Aira.leads.link = link;
  // What the outreach script (views-leads-outreach.js) builds on.
  Aira.leads.kit = { h, api, fmt, ui, toast, router, BASE, btn, chip, ico, fill, busy, externalLink, textField, copyText, announce, live, plural, trim, clean, splitList, safeUrl, hostOf, link, session, scoreBadge, problem, setupPanel, remember, recall, loadCatalog, openLeadById, liveOffered, kindIcon, logic };

  Aira.views.leads = async (main, route) => {
    const tab = TAB_IDS.includes(route.query.tab) ? route.query.tab : "find";
    Aira.closeDrawer();
    const label = (TABS.find(([id]) => id === tab) || TABS[0])[1];
    document.title = `${label} · Leads · Aira`;
    const crumbs = document.getElementById("crumbs");
    if (crumbs) crumbs.replaceChildren(h("span", { text: "Leads" }), h("span", { class: "muted", "aria-hidden": "true", text: "/" }), h("span", { text: label }));

    const content = h("div", { class: "ld-content", id: "ldContent", role: "region", "aria-label": label, tabindex: "-1" });
    const nav = h("nav", { class: "subnav ld-subnav", "aria-label": "Leads workspaces" }, ...TABS.map(([id, text]) => h("a", { href: link(id), "aria-current": id === tab ? "page" : "false" }, text)));
    main.replaceChildren(
      h("div", { class: "page-head ld-head" }, h("div", {}, h("h1", { text: "Leads" }), h("p", { text: "Find people and companies to sell to from Google Maps, Google, your own LinkedIn Sales Navigator or a CSV file; score them against your ideal customer; add contact details from the company's own website; and keep track of who you contacted. Aira finds and organizes. It never messages anyone for you." }))),
      nav, live, content);
    content.replaceChildren(ui.skeleton(5));
    let overview;
    try { overview = await api(`${BASE}/overview`); }
    catch (error) { content.replaceChildren(problem(error, () => router.render())); return; }
    const ctx = { route, overview, tab };
    try {
      if (tab === "find") await renderFind(content, ctx);
      else if (tab === "leads") await renderLeads(content, ctx);
      else if (tab === "outreach") {
        if (Aira.leads.outreach) await Aira.leads.outreach.render(content, ctx);
        else content.replaceChildren(ui.empty("Outreach is not loaded", "Reload the page. If this stays, the outreach script did not load.", null));
      } else if (tab === "settings") await renderSettings(content, ctx);
      else await renderHistory(content, ctx);
    } catch (error) {
      console.error(error);
      content.replaceChildren(problem(error, () => router.render()));
    }
  };
})();

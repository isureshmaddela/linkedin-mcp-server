/* Aira — Careers: Google Jobs search, career intelligence reports and job alerts.
 *
 * One view (#/careers) with a tab per workspace (?tab=): jobs (default), company, salary, market, interview, travel,
 * learn and alerts. Everything runs through /api/careers/* (aira/airaapi/careers*.py), which spends the connected
 * SerpApi key through the API service, so every search is recorded in API > History and shares its cache.
 *
 * - Jobs: the Google Jobs search (query, location, radius, remote, posted within, employment type), dynamic filter
 *   chips from the response, "Load more" through next_page_token, job cards, a detail drawer, Save to Aira (import),
 *   bulk import, saved searches and job alerts.
 * - Company, Salary, Market, Interview prep, Travel, Learn: one Report each. The plan and its cost are shown before
 *   anything runs (dry run), the person picks the sections, and more than 3 searches asks for a confirmation.
 * - Alerts: a saved Google Jobs search that Aira re-runs on a schedule and lists the jobs it has not seen before.
 *
 * Deep links: #/careers?tab=jobs&q=...&location=...&remote=1&posted=week&type=full_time prefills the search (it never
 * runs it); #/careers?tab=company&company=... prefills a report form; #/careers?tab=alerts&alert={id} opens an alert.
 * Aira.careers.link(tab, query) builds them.
 *
 * Everything the server sends is put on the page with textContent (the h() helper). Job descriptions are plain text
 * turned into paragraphs, lists and headings by describe(); no HTML from a job board is ever parsed. External links
 * (employer application pages, result links) open in a new tab with rel="noopener noreferrer".
 */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, router } = Aira;
  const BASE = "/api/careers";
  const serp = () => Aira.serp;

  const TABS = [
    ["jobs", "Jobs"],
    ["company", "Company"],
    ["salary", "Salary"],
    ["market", "Market"],
    ["interview", "Interview prep"],
    ["travel", "Travel"],
    ["learn", "Learn"],
    ["alerts", "Alerts"],
  ];
  const TAB_IDS = TABS.map(([id]) => id);
  // The workspaces as folders in the left menu (shell.js), each with its own icon.
  const TAB_ICONS = { jobs: "jobs", company: "careers-building", salary: "careers-coin", market: "analytics", interview: "interviews", travel: "careers-plane", learn: "learn", alerts: "careers-bell" };
  const CONFIRM_ABOVE = 3; // a report that would use more searches than this asks first
  const POSTED = [["", "Any time"], ["today", "Past day"], ["3days", "Past 3 days"], ["week", "Past week"], ["month", "Past month"]];
  const POSTED_CHIP = { today: "date_posted:today", "3days": "date_posted:3days", week: "date_posted:week", month: "date_posted:month" };
  const EMPLOYMENT = [["", "Any type"], ["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"], ["internship", "Internship"]];
  const EMPLOYMENT_CHIP = { full_time: "employment_type:FULLTIME", part_time: "employment_type:PARTTIME", contract: "employment_type:CONTRACTOR", internship: "employment_type:INTERN" };
  const RADII = [["", "Any distance"], ["5", "5 km"], ["10", "10 km"], ["25", "25 km"], ["50", "50 km"], ["100", "100 km"], ["200", "200 km"]];
  const INTERVALS = [[60, "Every hour"], [180, "Every 3 hours"], [360, "Every 6 hours"], [720, "Twice a day"], [1440, "Once a day"], [4320, "Every 3 days"], [10080, "Once a week"]];
  const INTERVIEW_TYPES = ["recruiter_screen", "technical_interview", "manager_interview", "panel_interview", "final_interview", "assessment", "other"];
  const MATCHED_BY = { source: "the same Google listing", url: "its application link", content: "the same title, company and location" };

  // ===========================================================================
  // Small helpers
  // ===========================================================================

  let uidCounter = 0;
  const uid = (prefix = "cr") => `${prefix}${(uidCounter += 1).toString(36)}${Math.random().toString(36).slice(2, 5)}`;
  const isEmpty = (value) => value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
  const clean = (obj) => Object.fromEntries(Object.entries(obj || {}).filter(([, v]) => !isEmpty(v) && v !== false));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const searchCount = (n) => `${n} ${n === 1 ? "search" : "searches"}`;
  const humanize = (key) => String(key || "").replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());
  const link = (tab, query = {}) => `#/careers?${new URLSearchParams({ tab, ...clean(query) })}`;
  const accountLink = () => "#/api?tab=account";
  const playgroundLink = (engine, params) => (typeof Aira.playgroundLink === "function" ? Aira.playgroundLink(engine, params) : `#/api?${new URLSearchParams({ tab: "playground", engine })}`);
  const safeUrl = (url) => { try { const u = new URL(String(url)); return u.protocol === "https:" || u.protocol === "http:" ? u.href : ""; } catch { return ""; } };
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const debounce = (fn, ms) => { let timer = null; const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; wrapped.cancel = () => clearTimeout(timer); return wrapped; };
  const remember = (key, value) => Aira.remember(`careers.${key}`, value);
  const recall = (key, fallback) => Aira.recall(`careers.${key}`, fallback);

  const ICON_PATHS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    bookmark: '<path d="M6 4h12v17l-6-4-6 4z"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    left: '<path d="m15 6-6 6 6 6"/>',
    right: '<path d="m9 6 6 6-6 6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    play: '<path d="M8 5.5v13l11-6.5z"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>',
    pin: '<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    userplus: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M19 8v6M16 11h6"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    save: '<path d="M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5M12 17.5v.5"/>',
    filter: '<path d="M4 5h16l-6 8v6l-4-2v-4z"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v5l3 2"/>',
  };
  const ico = (name) => h("span", { class: "sk-icon", "aria-hidden": "true", html: `<svg viewBox="0 0 24 24" focusable="false">${ICON_PATHS[name] || ""}</svg>` });

  /** Button or link with an optional icon. kind: "" (primary), "secondary", "ghost", "danger". */
  function btn(label, { icon, kind = "secondary", sm = false, onClick, title, href, external = false, disabled = false, aria, cls = "" } = {}) {
    const attrs = { class: `btn ${kind}${sm ? " sm" : ""} ${cls}`.replace(/\s+/g, " ").trim(), title: title || null, "aria-label": aria || null, onclick: onClick || null };
    const children = [icon ? ico(icon) : null, label ? h("span", { text: label }) : null];
    if (href) {
      attrs.href = href;
      if (external) { attrs.target = "_blank"; attrs.rel = "noopener noreferrer"; }
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

  /** Runs fn while the button shows a busy state; keeps its icon and label (Aira.withBusy would flatten them). */
  async function busy(button, fn) {
    if (button && button.getAttribute("aria-busy") === "true") return undefined;
    if (button) { button.setAttribute("aria-busy", "true"); button.disabled = true; }
    try { return await fn(); }
    catch (error) { Aira.reportError(error); return false; }
    finally { if (button) { button.removeAttribute("aria-busy"); button.disabled = false; } }
  }
  /** replaceChildren that skips empty slots (the native method would print "null"). */
  const fill = (node, ...children) => node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  const chip = (text, tone = "") => (isEmpty(text) ? null : h("span", { class: `cr-chip ${tone}`.trim(), text: String(text) }));
  const live = h("div", { class: "visually-hidden", role: "status", "aria-live": "polite", id: "crLive" });
  const announce = (text) => { live.textContent = ""; setTimeout(() => { live.textContent = text; }, 30); };

  // ---- states ---------------------------------------------------------------------------------------------------------

  function setupPanel({ compact = false, error = "" } = {}) {
    return h("section", { class: `cr-setup${compact ? " compact" : ""}`, "aria-labelledby": "crSetupTitle" },
      h("span", { class: "cr-setup-mark", "aria-hidden": "true" }, ico("key")),
      h("div", { class: "cr-setup-body" },
        h("h2", { id: "crSetupTitle", text: "Connect your SerpApi key to search" }),
        h("p", { class: "muted", text: error || "Careers reads job listings, company news, pay and trends through the SerpApi key you connect in API. Nothing is invented without it: until a key is connected you can see what each workspace will do and what it costs, but nothing runs." }),
        h("div", { class: "btn-row" }, btn("Connect key", { icon: "key", kind: "", href: accountLink() }), btn("About the API", { kind: "ghost", href: "#/api?tab=overview" }))));
  }

  /** An error as a card with the next step for the cases Careers can explain. */
  function problem(error, retry) {
    const code = error && error.code;
    if (code === "not_configured") return setupPanel({ compact: true, error: error.message });
    const box = ui.error(error, retry);
    const hints = {
      invalid_api_key: ["SerpApi rejected the stored key. ", h("a", { href: accountLink(), text: "Replace it in API, Account" }), "."],
      out_of_searches: ["The SerpApi account has no searches left. Cached results still work. ", h("a", { href: accountLink(), text: "Check the plan" }), "."],
      rate_limited: ["SerpApi is throttling this account. Wait a few minutes before the next live search."],
      local_rate_limit: ["Monosage's own hourly limit stopped this search. You can raise it in ", h("a", { href: accountLink(), text: "API, Account" }), "."],
      busy: ["Monosage is already running several searches. Try again in a moment."],
    };
    const anchor = box.querySelector(".btn-row");
    if (hints[code]) box.insertBefore(h("p", { class: "muted cr-hint", text: "" }, ...hints[code]), anchor);
    else if (error && error.status === 0) box.insertBefore(h("p", { class: "muted cr-hint", text: "Check that Monosage is still running on this computer, then try again." }), anchor);
    return box;
  }

  // ---- cross-links into the rest of Aira (recruiter, interview) ------------------------------------------------------

  const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };
  const cleanBody = (obj) => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== "" && v !== null && v !== undefined));

  /** Small "Add recruiter" dialog, prefilled from a report. POST /api/recruiters merges a duplicate instead of failing. */
  function recruiterDialog(defaults = {}) {
    const name = h("input", { type: "text", required: true, maxlength: 160, value: defaults.name || "", placeholder: "Recruiter or hiring contact" });
    const title = h("input", { type: "text", maxlength: 160, value: defaults.title || "" });
    const company = h("input", { type: "text", maxlength: 160, value: defaults.company || "" });
    const email = h("input", { type: "email", maxlength: 254 });
    const linkedin = h("input", { type: "url", placeholder: "https://www.linkedin.com/in/…" });
    const notes = h("textarea", { rows: 3, maxlength: 2000 }, defaults.notes || "");
    const body = h("div", { class: "form-grid" }, ui.field("Name", name), ui.field("Title", title), ui.field("Company", company), ui.field("Email", email), ui.field("LinkedIn URL", linkedin), h("div", { class: "field wide" }, h("label", { for: (notes.id = uid("crn")), text: "Notes" }), notes));
    const save = async (open) => {
      if (!name.value.trim()) { toast("Enter the recruiter's name.", "error"); name.focus(); return false; }
      try {
        const data = await api("/api/recruiters", { method: "POST", body: cleanBody({ name: name.value, title: title.value, company: company.value, email: email.value, linkedin_url: linkedin.value, notes: notes.value, source: "careers" }) });
        toast("Recruiter saved (merged if you already had them).", "success");
        if (open && data.recruiter) router.go(["recruiters", data.recruiter.recruiter_id]);
        return true;
      } catch (error) { Aira.reportError(error); return false; }
    };
    Aira.modal({ title: "Add recruiter", body, actions: [{ label: "Cancel", kind: "ghost" }, { label: "Add and open", kind: "secondary", onClick: () => save(true) }, { label: "Add recruiter", kind: "primary", onClick: () => save(false) }] });
  }

  /** Small "Add interview" dialog, prefilled from a report. */
  function interviewDialog(defaults = {}) {
    const company = h("input", { type: "text", maxlength: 160, value: defaults.company || "" });
    const role = h("input", { type: "text", maxlength: 160, value: defaults.role || "" });
    const type = ui.select(INTERVIEW_TYPES.map((t) => [t, fmt.title(fmt.stage(t))]), defaults.interview_type || "recruiter_screen");
    const start = h("input", { type: "datetime-local", required: true });
    const zone = h("input", { type: "text", maxlength: 60, value: browserZone(), list: "crTzList" });
    const meeting = h("input", { type: "url", placeholder: "https://…" });
    const notes = h("textarea", { rows: 3, maxlength: 4000 }, defaults.notes || "");
    if (!document.getElementById("crTzList")) {
      const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London", "Asia/Kolkata"];
      document.body.append(h("datalist", { id: "crTzList" }, ...zones.map((tz) => h("option", { value: tz }))));
    }
    const body = h("div", { class: "form-grid" }, ui.field("Company", company), ui.field("Role", role), ui.field("Interview type", type), ui.field("Starts (in the time zone below)", start), ui.field("Time zone", zone, "An IANA name such as America/New_York."), ui.field("Meeting link", meeting), h("div", { class: "field wide" }, h("label", { for: (notes.id = uid("crn")), text: "Notes" }), notes));
    const save = async (open) => {
      if (!start.value) { toast("Choose when the interview starts.", "error"); start.focus(); return false; }
      try {
        const data = await api("/api/interviews", { method: "POST", body: cleanBody({ company: company.value, role: role.value, interview_type: type.value, start_at: start.value, timezone: zone.value.trim(), meeting_link: meeting.value, notes: notes.value }) });
        toast("Interview added with reminders.", "success");
        if (open && data.interview) router.go(["interviews", data.interview.interview_id]);
        return true;
      } catch (error) { Aira.reportError(error); return false; }
    };
    Aira.modal({ title: "Add interview", wide: true, body, actions: [{ label: "Cancel", kind: "ghost" }, { label: "Add and open", kind: "secondary", onClick: () => save(true) }, { label: "Add interview", kind: "primary", onClick: () => save(false) }] });
  }

  // ===========================================================================
  // Jobs: watching, formatting, import
  // ===========================================================================

  /** A Job changes when it is saved to Aira; every card and drawer showing it repaints. */
  const watchers = new WeakMap();
  function watch(job, fn) {
    if (!watchers.has(job)) watchers.set(job, new Set());
    watchers.get(job).add(fn);
    return () => watchers.get(job) && watchers.get(job).delete(fn);
  }
  const touch = (job) => { for (const fn of [...(watchers.get(job) || [])]) fn(job); };

  /** Save one job to Aira through the duplicate-safe importer. The outcome is also written onto the job. */
  async function importJob(job) {
    const out = await api(`${BASE}/jobs/import`, { method: "POST", body: { job } });
    job.already_in_aira = true;
    job.aira_job_key = out.job && out.job.job_id ? out.job.job_id : job.aira_job_key;
    touch(job);
    return out;
  }
  function importMessage(out) {
    return out.created ? "Saved to Monosage and scored against your resume." : `Already in Monosage (matched by ${MATCHED_BY[out.matched_by] || "an earlier import"}); details were merged.`;
  }

  function salaryNode(job) {
    const salary = job.salary;
    if (!salary || !salary.text) return null;
    const annual = salary.annual_min != null || salary.annual_max != null ? `About ${fmt.money(salary.annual_min, salary.annual_max, salary.currency)} a year, annualized from the stated ${salary.period || "period"}.` : "Pay as stated in the listing.";
    return h("span", { class: "badge cr-salary", title: annual }, salary.text);
  }
  function matchNode(match) {
    if (!match || match.score === null || match.score === undefined) return null;
    const label = match.classification || "";
    return h("span", { class: `badge cr-match ${label ? `class-${label.split(" ")[0]}` : ""}`.trim(), title: `${label || "Match"}: a preview from your resume and profile. Save the job for the full breakdown.` },
      h("b", { text: `${Math.round(match.score)}%` }), h("span", { text: label ? ` ${label}` : " match" }));
  }
  const postedText = (job) => job.posted_at || "";
  const postedTitle = (job) => (job.posted_at_iso ? `About ${fmt.date(job.posted_at_iso)}` : "");
  const BENEFITS = { health_insurance: "Health insurance", dental_coverage: "Dental", paid_time_off: "Paid time off" };
  const primaryApply = (job) => (job.apply_options && job.apply_options[0]) || (safeUrl(job.share_link) ? { title: "Open on Google", link: job.share_link } : null);

  /** Plain text to paragraphs, bullet lists and sub-headings. Only text nodes are created. */
  function describe(text) {
    const root = h("div", { class: "cr-desc" });
    const bullet = /^\s*(?:[•·▪●◦■*–—-]|\d{1,2}[.)])\s+(.*)$/;
    const terminal = /[.!?:;)"”]$/;
    let list = null;
    let para = [];
    const flush = () => {
      if (!para.length) return;
      for (const piece of splitLong(para.join(" "))) root.append(h("p", { text: piece }));
      para = [];
    };
    const lines = String(text || "").replace(/\r/g, "").split("\n").map((line) => line.trim());
    const nextText = (from) => lines.slice(from + 1).find(Boolean) || "";
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (!line) { flush(); list = null; continue; }
      const item = bullet.exec(line);
      if (item) {
        flush();
        if (!list) { list = h("ul"); root.append(list); }
        list.append(h("li", { text: item[1] }));
        continue;
      }
      list = null;
      // A heading ends in a colon, is shouted in capitals, or is a short unpunctuated line that stands alone or leads a list.
      const standalone = (index === 0 || !lines[index - 1]) && (!lines[index + 1] || bullet.test(nextText(index)));
      const shouted = /^[A-Z0-9 &/,'’()-]{4,}$/.test(line) && /[A-Z]{3}/.test(line);
      const heading = line.length <= 64 && (/:$/.test(line) || shouted || (line.length <= 48 && !terminal.test(line) && /^[A-Z]/.test(line) && standalone));
      if (heading) { flush(); root.append(h("h4", { text: line.replace(/:$/, "") })); continue; }
      para.push(line);
      if (terminal.test(line)) flush(); // one source line per paragraph unless the sentence runs on to the next line
    }
    flush();
    if (!root.children.length) root.append(h("p", { class: "muted", text: "This listing has no description." }));
    return root;
  }
  /** A single block of several hundred characters is cut at sentence ends so it reads as paragraphs. */
  function splitLong(text, limit = 520) {
    if (text.length <= limit) return [text];
    const sentences = text.match(/[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+$/g) || [text];
    const out = [];
    let current = "";
    for (const sentence of sentences) {
      if (current && (current + sentence).length > limit) { out.push(current.trim()); current = ""; }
      current += sentence;
    }
    if (current.trim()) out.push(current.trim());
    return out;
  }

  // ===========================================================================
  // Job card
  // ===========================================================================

  /**
   * One result. ctx: {onOpen(job), isSelected(job), onSelect(job, on), onChange(job), dismiss(job)}; each hook is optional.
   * Returns {node, refresh()}; the card repaints itself when its job is saved (see watch()).
   */
  function jobCard(job, ctx = {}) {
    const titleId = uid("crt");
    const node = h("article", { class: "cr-job", "aria-labelledby": titleId });
    const paint = () => {
      const apply = primaryApply(job);
      const inAira = job.already_in_aira;
      const saveButton = inAira
        ? btn("Open in Monosage", { icon: "check", sm: true, kind: "secondary", cls: "cr-in-aira", title: "This posting is already in your Jobs", onClick: () => Aira.openJob(job.aira_job_key) })
        : btn("Save to Monosage", { icon: "bookmark", sm: true, kind: "", disabled: job.importable === false, title: job.importable === false ? "This listing has no link Monosage can use to import it." : "Add this posting to your Jobs and score it against your resume", onClick: (event) => busy(event.currentTarget, async () => {
          const out = await importJob(job);
          toast(importMessage(out), "success");
          if (ctx.onChange) ctx.onChange(job);
        }) });
      const pick = ctx.onSelect ? h("label", { class: "cr-pick" },
        h("input", { type: "checkbox", checked: ctx.isSelected ? ctx.isSelected(job) : false, "aria-label": `Select ${job.title}`, onchange: (event) => { node.classList.toggle("is-selected", event.currentTarget.checked); ctx.onSelect(job, event.currentTarget.checked); } })) : null;
      node.classList.toggle("is-selected", Boolean(ctx.isSelected && ctx.isSelected(job)));
      node.classList.toggle("in-aira", Boolean(inAira));
      fill(node,
        pick,
        serp().thumb(job.thumbnail, { alt: "", cls: "cr-logo", fallback: job.company || job.title }),
        h("div", { class: "cr-job-main" },
          h("h3", { class: "cr-job-title" }, h("button", { type: "button", id: titleId, class: "cr-link", "data-roving-item": "1", text: job.title, onclick: () => ctx.onOpen && ctx.onOpen(job) })),
          h("div", { class: "cr-job-sub" }, [job.company, job.location].filter(Boolean).join(" · ") || "Company and location not listed"),
          h("div", { class: "cr-badges" },
            salaryNode(job),
            job.work_from_home ? h("span", { class: "badge cr-remote", text: "Remote" }) : null,
            job.schedule_type ? h("span", { class: "badge", text: job.schedule_type }) : null,
            job.via ? h("span", { class: "badge source", text: job.via.replace(/^via\s+/i, "via ") }) : null,
            postedText(job) ? h("span", { class: "badge cr-posted", title: postedTitle(job), text: postedText(job) }) : null,
            inAira ? h("span", { class: "badge easy cr-inaira", title: "Already in your Jobs" }, ico("check"), h("span", { text: "In Monosage" })) : null),
          job.qualifications && job.qualifications.length ? h("p", { class: "cr-snippet", text: job.qualifications.slice(0, 2).join(" · ") }) : null,
          h("div", { class: "cr-actions" },
            saveButton,
            apply ? externalLink(apply.link, [ico("external"), h("span", { text: apply.title || "Open application page" })], { class: "btn secondary sm cr-apply", title: `Open the application page on ${hostOf(apply.link) || "the employer's site"}` }) : null,
            ctx.dismiss ? btn("Dismiss", { kind: "ghost", sm: true, onClick: () => ctx.dismiss(job), title: "Mark as reviewed" }) : null)),
        h("div", { class: "cr-job-side" }, matchNode(job.match)));
    };
    paint();
    watch(job, paint);
    node.addEventListener("click", (event) => { if (!event.target.closest("a, button, input, label") && ctx.onOpen) ctx.onOpen(job); });
    return { node, refresh: paint };
  }

  // ===========================================================================
  // Job drawer
  // ===========================================================================

  /** The detail drawer. ctx: {list: Job[] (for previous/next), alertFor(job)}. */
  function openJobDrawer(start, ctx = {}) {
    let job = start;
    const list = ctx.list && ctx.list.includes(start) ? ctx.list : [start];
    let unwatch = () => {};
    const body = h("div", { class: "cr-drawer" });
    const prev = btn("", { icon: "left", sm: true, kind: "ghost", aria: "Previous job", title: "Previous job (in this list)", onClick: () => go(-1) });
    const next = btn("", { icon: "right", sm: true, kind: "ghost", aria: "Next job", title: "Next job (in this list)", onClick: () => go(1) });
    const drawer = Aira.drawer({ title: job.title, subtitle: h("span", { text: "" }), body, headActions: list.length > 1 ? [prev, next] : [], onClose: () => unwatch() });

    function go(step) {
      const index = list.indexOf(job) + step;
      if (index < 0 || index >= list.length) return;
      job = list[index];
      paint();
      drawer.body.scrollTop = 0;
    }

    function paint() {
      unwatch();
      unwatch = watch(job, paint);
      const heading = drawer.head.querySelector("h2");
      if (heading) heading.textContent = job.title;
      const sub = drawer.head.querySelector(".sub");
      if (sub) fill(sub, [job.company, job.location].filter(Boolean).join(" · "));
      const index = list.indexOf(job);
      prev.disabled = index <= 0;
      next.disabled = index >= list.length - 1;
      const apply = primaryApply(job);
      const inAira = job.already_in_aira;
      const facts = [
        ["Company", job.company],
        ["Location", job.location],
        ["Posted", job.posted_at ? h("span", { title: postedTitle(job), text: job.posted_at }) : null],
        ["Type", job.schedule_type],
        ["Source", job.via ? job.via.replace(/^via\s+/i, "") : null],
        ["Pay", salaryNode(job)],
      ];
      const highlights = (job.highlights || []).filter((block) => block.items && block.items.length);
      const hasQualBlock = highlights.some((block) => /^qualification/i.test(block.title));
      fill(body,
        h("div", { class: "cr-dactions" },
          inAira
            ? btn("Open in Monosage", { icon: "check", kind: "", onClick: () => Aira.openJob(job.aira_job_key) })
            : btn("Save to Monosage", { icon: "bookmark", kind: "", disabled: job.importable === false, title: job.importable === false ? "This listing has no link Monosage can use to import it." : "", onClick: (event) => busy(event.currentTarget, async () => {
              const out = await importJob(job);
              toast(importMessage(out), "success");
              if (ctx.onChange) ctx.onChange(job);
            }) }),
          apply ? externalLink(apply.link, [ico("external"), h("span", { text: apply.title || "Open application page" })], { class: "btn secondary" }) : h("span", { class: "muted", text: "No application link was listed." }),
          ctx.alertFor ? btn("Alert for similar roles", { icon: "bell", kind: "ghost", onClick: () => ctx.alertFor(job) }) : null,
          safeUrl(job.share_link) ? serp().copyButton(job.share_link, { label: "Copy Google link", message: "Link copied", sm: false, text: true }) : null),
        job.importable === false && !inAira ? h("p", { class: "note warn" }, "Google did not list an application link for this posting, so Monosage cannot import it. You can still read it here and search for it on the employer's site.") : null,
        inAira ? h("p", { class: "note" }, "This posting is already in your Jobs. Open it there for notes, documents and the application checklist.") : null,
        job.match ? h("section", { class: "cr-dmatch", "aria-label": "Match preview" }, ui.score(job.match.score), h("div", {}, h("strong", { text: job.match.classification || "Match preview" }), h("p", { class: "muted", text: "A preview scored from your resume and profile with the same scorer the Jobs tab uses. Save the job to see the evidence behind each dimension." }))) : null,
        h("dl", { class: "kv cr-facts" }, ...facts.filter(([, v]) => !isEmpty(v)).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : String(v))])),
        job.benefits && Object.keys(job.benefits).length ? h("div", { class: "cr-chips", role: "group", "aria-label": "Benefits Google detected" }, h("span", { class: "cr-fname", text: "Benefits listed" }), ...Object.keys(job.benefits).map((key) => chip(BENEFITS[key] || humanize(key), "good"))) : null,
        job.apply_options && job.apply_options.length ? h("section", { "aria-label": "Where to apply" }, h("h3", { text: "Where to apply" }),
          h("ul", { class: "cr-apply-list" }, ...job.apply_options.map((option) => h("li", {}, externalLink(option.link, [h("span", { class: "cr-apply-name", text: option.title || hostOf(option.link) }), h("span", { class: "cr-apply-host", text: hostOf(option.link) }), ico("external")]))))) : null,
        highlights.length ? h("section", { "aria-label": "Highlights" }, h("div", { class: "cr-hl-grid" }, ...highlights.map((block) => h("div", { class: "cr-hl" }, h("h3", { text: block.title || "Highlights" }), h("ul", {}, ...block.items.map((item) => h("li", { text: item })))))))
          : null,
        !hasQualBlock && job.qualifications && job.qualifications.length ? h("section", { class: "cr-hl" }, h("h3", { text: "Qualifications" }), h("ul", {}, ...job.qualifications.map((item) => h("li", { text: item })))) : null,
        h("section", { "aria-label": "Description" }, h("h3", { text: "Description" }), describe(job.description)),
        h("p", { class: "cr-source-note muted", text: "Listing data comes from Google Jobs through SerpApi and is shown as text. Check the employer's page for the current posting." }));
    }
    paint();
    const first = drawer.panel.querySelector(".cr-dactions a, .cr-dactions button");
    if (first) first.focus();
    return drawer;
  }

  // ===========================================================================
  // Job alert dialog (create from a search, or edit an existing alert)
  // ===========================================================================

  const intervalLabel = (minutes) => (INTERVALS.find(([value]) => value === Number(minutes)) || [0, `Every ${minutes} minutes`])[1].toLowerCase();
  const monthly = (minutes) => Math.ceil((30 * 24 * 60) / Math.max(1, Number(minutes) || 1));

  /**
   * alert dialog. create: {search: {q, location, ...friendly filters}, name, limits}; edit: {alert}.
   * onSaved(alert) runs after a successful save.
   */
  function alertDialog({ alert = null, search = {}, limits = {}, onSaved } = {}) {
    const editing = Boolean(alert);
    const minInterval = limits.min_interval_minutes || 60;
    const defaultName = alert ? alert.name : [search.q, search.location ? `in ${search.location}` : ""].filter(Boolean).join(" ");
    const name = h("input", { type: "text", maxlength: 120, value: defaultName, required: true });
    const queryInput = h("input", { type: "search", maxlength: 200, value: editing ? [alert.q, alert.location].filter(Boolean).join(" · ") : search.q || "", required: true, disabled: editing });
    let pickedName = search.location || "";
    let pickedGl = search.gl || "";
    const locPicker = editing ? null : serp().locationPicker({
      value: search.location || "", id: uid("crl"), placeholder: "City, region or Remote",
      onPick: (picked) => { pickedName = picked.canonical_name || picked.name || ""; pickedGl = (picked.country_code || "").toLowerCase(); setTimeout(followName, 0); },
      onInput: (text) => { if (text !== pickedName) pickedGl = ""; followName(); },
    });
    // A name nobody typed follows the search ("line cook in Chicago"), so an alert made from the empty form still has one.
    let nameTouched = Boolean(alert) || Boolean(name.value.trim());
    const followName = () => { if (!nameTouched && !editing) name.value = [queryInput.value.trim(), locPicker && locPicker.get() ? `in ${locPicker.get()}` : ""].filter(Boolean).join(" ").slice(0, 120); };
    name.addEventListener("input", () => { nameTouched = true; });
    queryInput.addEventListener("input", followName);
    const locField = locPicker ? ui.field("Location", locPicker) : null;
    if (locField) locField.querySelector("label").setAttribute("for", locPicker.input.id);
    const intervalSelect = ui.select(INTERVALS.filter(([value]) => value >= minInterval).map(([value, label]) => [String(value), label]), String(alert ? alert.interval_minutes : limits.default_interval_minutes || 720));
    const cost = h("p", { class: "cr-cost-line", role: "status" });
    const autoImport = serp().toggleSwitch("Save good matches to Monosage automatically", alert ? alert.auto_import : false, () => sync(), { hint: "Only jobs whose preview score reaches the threshold below. Monosage never applies for you." });
    const minScore = h("input", { type: "number", min: 0, max: 100, step: 1, value: alert ? alert.min_score : limits.default_min_score || 75 });
    const scoreField = ui.field("Minimum match score", minScore, "A job that cannot be scored is listed but never saved.");
    const notify = serp().toggleSwitch("Notify me in Monosage when new jobs appear", alert ? alert.notify : true, () => {});
    const sync = () => {
      scoreField.hidden = !autoImport.get();
      const minutes = Number(intervalSelect.value);
      const perMonth = monthly(minutes);
      cost.className = `cr-cost-line${perMonth >= 100 ? " warn" : ""}`;
      cost.textContent = `${intervalLabel(minutes).replace(/^./, (c) => c.toUpperCase())}: about ${searchCount(perMonth)} a month. Each run uses one search and is never served from the cache, so it always sees fresh listings.${perMonth >= 100 ? " That is a large share of a free plan; a longer interval costs less." : ""}`;
    };
    intervalSelect.addEventListener("change", sync);
    sync();
    const carried = [];
    if (!editing) {
      if (search.remote) carried.push("Remote");
      if (search.posted) carried.push(`Posted: ${(POSTED.find(([v]) => v === search.posted) || [])[1] || search.posted}`);
      if (search.employment) carried.push((EMPLOYMENT.find(([v]) => v === search.employment) || [])[1] || search.employment);
      if (search.radius) carried.push(`${search.radius} km`);
      if (search.uds) carried.push("Google filter");
      for (const value of search.chips || []) carried.push(value.split(":").pop());
    }
    const body = h("div", { class: "cr-alert-form" },
      h("p", { class: "muted", text: editing ? "Change how often this alert runs and what it does with new jobs. To change the search itself, create a new alert." : "Monosage re-runs this search on a schedule and lists only the jobs it has not seen before. The first run records what is posted now so you are not flooded with the backlog." }),
      h("div", { class: "form-grid" }, ui.field("Alert name", name), ui.field(editing ? "Search (fixed)" : "Search", queryInput), locField, ui.field("How often", intervalSelect, `Not more often than every ${minInterval} minutes.`)),
      carried.length ? h("div", { class: "cr-carried" }, h("span", { class: "muted", text: "Carried over: " }), ...carried.map((c) => chip(c))) : null,
      cost,
      h("div", { class: "cr-switches" }, autoImport.node, scoreField, notify.node));
    Aira.modal({
      title: editing ? "Edit job alert" : "Create job alert",
      body,
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: editing ? "Save changes" : "Create alert", kind: "primary", onClick: async () => {
          if (!name.value.trim() || !queryInput.value.trim()) { toast("An alert needs a name and a search.", "error"); return false; }
          const score = Number(minScore.value);
          if (autoImport.get() && !(score >= 0 && score <= 100)) { toast("The match score must be between 0 and 100.", "error"); minScore.focus(); return false; }
          try {
            let saved;
            if (editing) {
              saved = await api(`${BASE}/alerts/${alert.id}`, { method: "PATCH", body: { name: name.value.trim(), interval_minutes: Number(intervalSelect.value), auto_import: autoImport.get(), min_score: autoImport.get() ? score : alert.min_score, notify: notify.get() } });
              toast("Alert updated.", "success");
            } else {
              saved = await api(`${BASE}/alerts`, { method: "POST", body: clean({ name: name.value.trim(), q: queryInput.value.trim(), location: locPicker.get(), gl: locPicker.get() ? pickedGl : "", lrad: search.radius && locPicker.get() ? Number(search.radius) : "", remote: search.remote ? true : "", posted_within: search.posted, employment_type: search.employment, chips: search.chips && search.chips.length ? search.chips : "", uds: search.uds, interval_minutes: Number(intervalSelect.value), auto_import: autoImport.get(), min_score: score, notify: notify.get() }) });
              toast(`Alert created. The first run (${searchCount(1)}) records what is posted now.`, "success");
            }
            if (onSaved) onSaved(saved);
          } catch (error) { Aira.reportError(error); return false; }
          return true;
        } },
      ],
    });
    (editing ? name : queryInput).focus();
  }

  // ===========================================================================
  // Jobs tab
  // ===========================================================================

  const emptyForm = () => ({ q: "", location: "", gl: "", radius: "", remote: false, posted: "", employment: "", chips: [], uds: "", udsLabel: "", provider: "" });
  const PROVIDERS = [["aira", "Jobs (free)"], ["google", "Google Jobs"]];
  /** Kept for the whole page session so switching tabs and coming back shows the same results. */
  const jobsState = { form: null, jobs: [], token: "", filters: [], meta: null, searched: false, selected: new Set(), loadedSaved: "", saved: null, alertLimits: null };

  /** The Google Jobs request a form means (the same mapping the server applies to the friendly fields). */
  function googleParams(form) {
    const chips = [...(form.chips || [])];
    if (form.posted && POSTED_CHIP[form.posted]) chips.push(POSTED_CHIP[form.posted]);
    if (form.employment && EMPLOYMENT_CHIP[form.employment]) chips.push(EMPLOYMENT_CHIP[form.employment]);
    const location = form.location.trim();
    return clean({ q: form.q.trim(), location, gl: location ? form.gl : "", lrad: location && form.radius ? Number(form.radius) : "", ltype: form.remote ? "1" : "", chips: [...new Set(chips)].join(","), uds: form.uds });
  }
  function airaParams(form) {
    return clean({ q: form.q.trim(), location: form.location.trim(), remote_only: form.remote ? true : "", employment_type: form.employment });
  }
  function formFromParams(params) {
    const form = emptyForm();
    form.q = String(params.q || "");
    form.location = String(params.location || "");
    form.gl = String(params.gl || "");
    form.radius = params.lrad ? String(params.lrad) : "";
    form.remote = String(params.ltype) === "1" || params.remote_only === true || params.remote_only === "true";
    if (params.employment_type) form.employment = String(params.employment_type);
    for (const value of String(params.chips || "").split(",").filter(Boolean)) {
      const posted = Object.entries(POSTED_CHIP).find(([, chipValue]) => chipValue === value);
      const employment = Object.entries(EMPLOYMENT_CHIP).find(([, chipValue]) => chipValue === value);
      if (posted && !form.posted) form.posted = posted[0];
      else if (employment && !form.employment) form.employment = employment[0];
      else form.chips.push(value);
    }
    form.uds = String(params.uds || "");
    form.udsLabel = form.uds ? "Saved Google filter" : "";
    return form;
  }
  /** The body of POST /api/careers/jobs/search for a form (friendly fields; the server maps them to chips and ltype). */
  function searchBody(form, token = "") {
    const location = form.location.trim();
    if (form.provider === "aira") return clean({ provider: "aira", q: form.q.trim(), location, remote: form.remote, employment_type: form.employment });
    return clean({ provider: "google", q: form.q.trim(), location, gl: location ? form.gl : "", lrad: location && form.radius ? Number(form.radius) : "", remote: form.remote, posted_within: form.posted, employment_type: form.employment, chips: form.chips, uds: form.uds, next_page_token: token });
  }
  const alertSeed = (form) => ({ q: form.q.trim(), location: form.location.trim(), gl: form.location.trim() ? form.gl : "", radius: form.location.trim() ? form.radius : "", remote: form.remote, posted: form.posted, employment: form.employment, chips: [...form.chips], uds: form.uds });

  async function loadAlertLimits() {
    if (!jobsState.alertLimits) {
      try { jobsState.alertLimits = (await api(`${BASE}/alerts`, { silent: true })).limits || {}; }
      catch { jobsState.alertLimits = {}; }
    }
    return jobsState.alertLimits;
  }
  async function openAlertFor(search, onSaved) {
    const limits = await loadAlertLimits();
    alertDialog({ search, limits, onSaved });
  }

  async function renderJobs(content, ctx) {
    const st = jobsState;
    const query = ctx.route.query;
    if (!st.form) st.form = { ...emptyForm(), ...recall("jobs.form", {}) };
    st.form.chips = Array.isArray(st.form.chips) ? st.form.chips : [];
    if (!st.form.provider || (st.form.provider === "google" && !ctx.configured)) st.form.provider = ctx.configured ? "google" : "aira";
    const canSearch = () => st.form.provider === "aira" || ctx.configured;
    const deepLinked = ["q", "location", "remote", "posted", "type", "radius"].some((key) => query[key] !== undefined);
    if (deepLinked) {
      st.form = { ...emptyForm(), ...st.form, q: query.q ?? st.form.q, location: query.location ?? st.form.location, remote: query.remote ? query.remote === "1" : st.form.remote, posted: query.posted ?? st.form.posted, employment: query.type ?? st.form.employment, radius: query.radius ?? st.form.radius };
      if (query.location !== undefined) st.form.gl = "";
      st.loadedSaved = "";
    }
    const profile = Aira.state.profile || {};
    const candidate = profile.candidate_profile || {};
    const profileRole = (profile.career_profiles && profile.career_profiles[candidate.career_profile]) || candidate.current_title || "";
    const profileLocation = candidate.location || "";
    if (!st.form.q && !st.searched && profileRole) { st.form.q = profileRole; if (!st.form.location) st.form.location = profileLocation; }
    if (!st.saved) st.saved = (ctx.overview.saved_searches || []).slice();

    let searchSeq = 0;
    let estimateSeq = 0;
    let pickedName = st.form.location;
    const initial = { ...st.form };

    // ---- controls ------------------------------------------------------------------------------------------------
    const qInput = h("input", { type: "search", id: "crQ", "data-search": "1", maxlength: 200, autocomplete: "off", placeholder: "Job title, skill or company", value: initial.q, "aria-required": "true" });
    const loc = serp().locationPicker({
      value: initial.location, id: "crLoc", placeholder: "City, region or Remote",
      onPick: (picked) => { pickedName = picked.canonical_name || picked.name || ""; st.form.gl = (picked.country_code || "").toLowerCase(); },
      onInput: (text) => { if (text !== pickedName) st.form.gl = ""; st.form.location = text; changed(); },
    });
    const radius = ui.select(RADII, initial.radius, { id: "crRadius" });
    const employment = ui.select(EMPLOYMENT, initial.employment, { id: "crEmployment" });
    const posted = serp().segmented(POSTED, initial.posted, (value) => { st.form.posted = value; changed(); }, { label: "Posted within" });
    const remote = serp().toggleSwitch("Remote only", initial.remote, (on) => { st.form.remote = on; changed(); }, { hint: "Work-from-home listings" });
    const costBox = h("span", { class: "cr-cost", role: "status", "aria-live": "polite" });
    const searchBtn = h("button", { type: "submit", class: "btn cr-go", disabled: !canSearch() || null }, ico("search"), h("span", { text: "Search jobs" }));
    const providerNote = h("p", { class: "cr-provider-note muted", role: "status" });
    const providerSeg = serp().segmented(PROVIDERS, st.form.provider, (value) => { st.form.provider = value; providerChanged(); changed(); }, { label: "Search with" });
    const savedSelect = h("select", { id: "crSaved", "aria-label": "Saved searches" });
    const deleteSaved = btn("Delete", { icon: "trash", kind: "ghost", sm: true, title: "Delete the loaded saved search", onClick: () => removeSaved() });
    const saveBtn = btn("Save search", { icon: "save", sm: true, onClick: () => saveSearch() });
    const alertBtn = btn("Add alert", { icon: "bell", sm: true, onClick: () => { changed(); if (st.form.provider === "aira") { Aira.modal({ title: "Alerts for Jobs", body: h("p", { class: "muted", text: "Job alerts run on Google Jobs and need a SerpApi key. To have Jobs searched on a schedule for free, create a search plan in Jobs > Auto-apply and choose \"Ask me first\": matching jobs are filled in and wait for your OK, nothing is sent on its own." }), actions: [{ label: "Close", kind: "ghost" }, { label: "Open Auto-apply", kind: "", onClick: () => { location.hash = "#/jobs/autopilot"; } }] }); return; } if (!st.form.q.trim()) { toast("Enter a search first, then add an alert for it.", "error"); qInput.focus(); return; } openAlertFor(alertSeed(st.form), () => { fill(alertsHint, "Alert created. ", h("a", { href: link("alerts"), text: "Manage alerts" })); }); } });
    const resetBtn = btn("Clear", { kind: "ghost", sm: true, title: "Clear the search and its results", onClick: () => reset() });
    const alertsHint = h("span", { class: "cr-saved-note", role: "status" });

    const radiusField = ui.field("Radius", radius);
    const locField = ui.field("Location", loc);
    locField.querySelector("label").setAttribute("for", "crLoc"); // the picker wraps its input
    const searchPanel = h("form", { class: "cr-search", role: "search", "aria-label": "Search jobs", novalidate: true },
      h("div", { class: "cr-provider" }, h("span", { class: "label", text: "Search with" }), providerSeg.node, providerNote),
      h("div", { class: "cr-search-grid" },
        h("div", { class: "cr-f-q" }, ui.field("Role or keywords", qInput)),
        h("div", { class: "cr-f-loc" }, locField),
        h("div", { class: "cr-f-radius" }, radiusField),
        h("div", { class: "cr-f-go" }, searchBtn)),
      h("div", { class: "cr-quick" },
        h("div", { class: "field cr-f-posted" }, h("span", { class: "label", text: "Posted within" }), posted.node),
        h("div", { class: "cr-f-employment" }, ui.field("Employment type", employment)),
        h("div", { class: "cr-f-remote" }, remote.node)),
      h("div", { class: "cr-saved" },
        h("div", { class: "cr-saved-left" }, h("label", { class: "visually-hidden", for: "crSaved", text: "Saved searches" }), savedSelect, deleteSaved, saveBtn, alertBtn, resetBtn, alertsHint),
        costBox));

    const filtersBox = h("section", { class: "cr-filters", "aria-label": "Refine results", hidden: true });
    const resultsBox = h("section", { class: "cr-results", "aria-label": "Results" });
    const setupHost = h("div");
    fill(content, live, setupHost, searchPanel, filtersBox, resultsBox);
    function providerChanged() {
      const aira = st.form.provider === "aira";
      fill(setupHost, !aira && !ctx.configured ? setupPanel({ compact: true, error: ctx.overview.key_error || "" }) : null);
      providerNote.textContent = aira
        ? `Monosage's own job search over free public job boards (Himalayas, The Muse, Remotive, Jobicy, Arbeitnow): no SerpApi key, no credits.${ctx.configured ? "" : " Connect a SerpApi key to add Google Jobs."}`
        : "Google Jobs through your SerpApi key: Google's own filters, one search per page.";
      for (const node of [radiusField, posted.node.closest(".field")]) if (node) node.hidden = aira;
      searchBtn.disabled = !canSearch();
      if (st.searched && st.meta && st.meta.provider && st.meta.provider !== st.form.provider) { st.searched = false; st.jobs = []; st.token = ""; renderFilters(); renderResults(); }
    }

    // ---- keeping the form, the controls and the stored copy in step ----------------------------------------------------
    function readInputs() {
      st.form.q = qInput.value;
      st.form.radius = radius.value;
      st.form.employment = employment.value;
    }
    function syncControls() {
      qInput.value = st.form.q;
      providerSeg.set(st.form.provider);
      pickedName = st.form.location;
      loc.set(st.form.location);
      if (st.form.radius && !RADII.some(([value]) => value === st.form.radius)) radius.append(h("option", { value: st.form.radius, text: `${st.form.radius} km` }));
      radius.value = st.form.radius;
      employment.value = st.form.employment;
      posted.set(st.form.posted);
      remote.set(st.form.remote);
      radius.disabled = !st.form.location.trim();
      radiusField.querySelector("label").textContent = st.form.location.trim() ? "Radius" : "Radius (needs a location)";
      saveBtn.disabled = !st.form.q.trim();
      deleteSaved.hidden = !st.loadedSaved;
      savedSelect.value = st.loadedSaved || "";
    }
    function changed() {
      readInputs();
      remember("jobs.form", st.form);
      radius.disabled = !st.form.location.trim();
      radiusField.querySelector("label").textContent = st.form.location.trim() ? "Radius" : "Radius (needs a location)";
      saveBtn.disabled = !st.form.q.trim();
      if (st.loadedSaved && savedSelect.value !== st.loadedSaved) savedSelect.value = st.loadedSaved;
      estimate();
    }
    const estimate = debounce(async () => {
      if (!st.form.q.trim()) { fill(costBox, h("span", { class: "muted", text: "Enter a search to see what it costs." })); return; }
      const mine = (estimateSeq += 1);
      if (st.form.provider === "aira") { fill(costBox, h("span", { class: "sk-credit cache", title: "Jobs is Monosage's own search: it never uses a SerpApi search." }, ico("check"), h("span", { text: "Free" }))); return; }
      try {
        const data = await api(`${BASE}/jobs/search`, { method: "POST", body: { ...searchBody(st.form), dry_run: true }, silent: true });
        if (mine !== estimateSeq) return;
        fill(costBox, data.cache_hit
          ? h("span", { class: "sk-credit cache", title: "An identical search is inside the cache window, so it is served locally." }, ico("check"), h("span", { text: "Cached, free" }))
          : h("span", { class: "sk-credit", title: "One SerpApi search against your plan. Identical searches within the cache window are free." }, h("span", { text: "1 search" })));
      } catch (error) {
        if (mine !== estimateSeq) return;
        fill(costBox, h("span", { class: "cr-cost-err", text: error.status === 400 ? error.message : "Cost unavailable" }));
      }
    }, 450);

    for (const control of [radius, employment]) control.addEventListener("change", changed);
    qInput.addEventListener("input", changed);
    searchPanel.addEventListener("submit", (event) => { event.preventDefault(); if (canSearch()) runSearch(); });

    // ---- saved searches --------------------------------------------------------------------------------------------------
    function fillSaved() {
      savedSelect.replaceChildren(h("option", { value: "", text: st.saved.length ? `Saved searches (${st.saved.length})` : "No saved searches yet" }), ...st.saved.map((item) => h("option", { value: item.id, text: item.name })));
      savedSelect.disabled = !st.saved.length;
      savedSelect.value = st.loadedSaved || "";
    }
    savedSelect.addEventListener("change", () => {
      const item = st.saved.find((entry) => entry.id === savedSelect.value);
      if (!item) { st.loadedSaved = ""; syncControls(); return; }
      st.form = { ...formFromParams(item.params || {}), provider: item.engine === "aira_jobs" ? "aira" : "google" };
      st.loadedSaved = item.id;
      syncControls();
      changed();
      announce(`Loaded the saved search ${item.name}. Press Search jobs to run it.`);
      searchBtn.focus();
    });
    function saveSearch() {
      readInputs();
      if (!st.form.q.trim()) return;
      const name = h("input", { type: "text", maxlength: 120, required: true, value: [st.form.q.trim(), st.form.location.trim() ? `in ${st.form.location.trim()}` : ""].filter(Boolean).join(" ") });
      Aira.modal({ title: "Save this search", body: h("div", {}, h("p", { class: "muted", text: "Saved searches live in API as well, so you can run, monitor or compare them there. Saving costs nothing." }), ui.field("Name", name)), actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save search", kind: "primary", onClick: async () => {
        if (!name.value.trim()) { toast("Give the search a name.", "error"); return false; }
        try {
          const aira = st.form.provider === "aira";
          const saved = await api("/api/airaapi/saved", { method: "POST", body: { name: name.value.trim(), engine: aira ? "aira_jobs" : "google_jobs", params: aira ? airaParams(st.form) : googleParams(st.form), tags: ["careers"] } });
          st.saved.unshift(saved);
          st.loadedSaved = saved.id;
          fillSaved();
          syncControls();
          toast("Search saved.", "success");
        } catch (error) { Aira.reportError(error); return false; }
        return true;
      } }] });
    }
    async function removeSaved() {
      const item = st.saved.find((entry) => entry.id === st.loadedSaved);
      if (!item) return;
      if (!(await Aira.confirm({ title: "Delete this saved search?", message: `"${item.name}" is removed from Careers and from API. Alerts and past results are not affected.`, confirmLabel: "Delete", danger: true }))) return;
      try {
        await api(`/api/airaapi/saved/${item.id}`, { method: "DELETE" });
        st.saved = st.saved.filter((entry) => entry.id !== item.id);
        st.loadedSaved = "";
        fillSaved();
        syncControls();
        toast("Saved search deleted.", "success");
      } catch (error) { Aira.reportError(error); }
    }
    function reset() {
      st.form = emptyForm();
      st.loadedSaved = "";
      st.jobs = []; st.token = ""; st.filters = []; st.meta = null; st.searched = false; st.selected.clear();
      searchSeq += 1;
      remember("jobs.form", st.form);
      syncControls();
      renderFilters();
      renderResults();
      estimate();
      qInput.focus();
    }

    // ---- filters offered with the results --------------------------------------------------------------------------------
    const friendlyOf = (value) => {
      const posted_ = Object.entries(POSTED_CHIP).find(([, chipValue]) => chipValue === value);
      if (posted_) return ["posted", posted_[0]];
      const employment_ = Object.entries(EMPLOYMENT_CHIP).find(([, chipValue]) => chipValue === value);
      return employment_ ? ["employment", employment_[0]] : null;
    };
    const isActive = (group, option) => {
      if (group.parameter === "uds") return st.form.uds === option.value;
      const friendly = friendlyOf(option.value);
      return friendly ? st.form[friendly[0]] === friendly[1] : st.form.chips.includes(option.value);
    };
    function toggleFilter(group, option) {
      if (group.parameter === "uds") {
        const off = st.form.uds === option.value;
        st.form.uds = off ? "" : option.value;
        st.form.udsLabel = off ? "" : group.name.toLowerCase() === option.text.toLowerCase() ? option.text : `${group.name}: ${option.text}`;
      } else {
        const friendly = friendlyOf(option.value);
        if (friendly) st.form[friendly[0]] = st.form[friendly[0]] === friendly[1] ? "" : friendly[1];
        else if (st.form.chips.includes(option.value)) st.form.chips = st.form.chips.filter((value) => value !== option.value);
        else st.form.chips = [...st.form.chips.filter((value) => value.split(":")[0] !== option.value.split(":")[0]), option.value];
      }
      syncControls();
      changed();
      runSearch();
    }
    /** One removable chip per refinement that is active, including the ones typed into the form above. */
    function activeRefinements() {
      const items = [];
      if (st.form.remote) items.push(["Remote only", () => { st.form.remote = false; }]);
      if (st.form.posted) items.push([`Posted: ${(POSTED.find(([v]) => v === st.form.posted) || [])[1]}`, () => { st.form.posted = ""; }]);
      if (st.form.employment) items.push([(EMPLOYMENT.find(([v]) => v === st.form.employment) || [])[1], () => { st.form.employment = ""; }]);
      if (st.form.uds) items.push([st.form.udsLabel || "Google filter", () => { st.form.uds = ""; st.form.udsLabel = ""; }]);
      for (const value of st.form.chips) items.push([value.split(":").pop().replaceAll("_", " "), () => { st.form.chips = st.form.chips.filter((entry) => entry !== value); }]);
      return items;
    }
    function renderFilters() {
      const groups = st.filters || [];
      const active = activeRefinements();
      filtersBox.hidden = !groups.length && !active.length;
      if (filtersBox.hidden) { filtersBox.replaceChildren(); return; }
      fill(filtersBox,
        active.length ? h("div", { class: "cr-active" },
          h("span", { class: "cr-fname", text: "Active" }),
          ...active.map(([label, remove]) => h("button", { type: "button", class: "cr-fchip on", "aria-label": `Remove filter ${label}`, onclick: () => { remove(); syncControls(); changed(); if (st.searched) runSearch(); } }, h("span", { text: label }), ico("close")))) : null,
        groups.length ? h("div", { class: "cr-fgroups" }, ...groups.map((group) => h("div", { class: "cr-fgroup", role: "group", "aria-label": group.name },
          group.options.length === 1 && group.options[0].text.toLowerCase() === group.name.toLowerCase() ? null : h("span", { class: "cr-fname", text: group.name }),
          h("div", { class: "cr-fchips" }, ...group.options.map((option) => h("button", { type: "button", class: "cr-fchip", "aria-pressed": String(isActive(group, option)), onclick: () => toggleFilter(group, option) }, h("span", { text: option.text }))))))) : null,
        groups.length ? h("p", { class: "cr-fnote", text: "Choosing a filter runs the search again with Google's own filter token (1 search, free if cached)." }) : null);
    }

    // ---- results ------------------------------------------------------------------------------------------------------------
    let listNode = null;
    let bulkNode = null;
    let headNode = null;
    let moreNode = null;
    let selectAll = null;
    const cards = [];

    const drawerCtx = () => ({ list: st.jobs, onChange: updateBulk, alertFor: (job) => openAlertFor({ ...alertSeed(st.form), q: job.title, location: st.form.location.trim() || (job.work_from_home ? "" : job.location || ""), gl: st.form.location.trim() ? st.form.gl : "", radius: "", chips: [], uds: "", remote: Boolean(job.work_from_home) || st.form.remote, posted: "", employment: "" }) });
    const cardCtx = {
      onOpen: (job) => openJobDrawer(job, drawerCtx()),
      isSelected: (job) => st.selected.has(job.key),
      onSelect: (job, on) => { if (on) st.selected.add(job.key); else st.selected.delete(job.key); updateBulk(); },
      onChange: () => updateBulk(),
    };
    const unsaved = () => st.jobs.filter((job) => !job.already_in_aira && job.importable !== false);

    function addCards(jobs) {
      for (const job of jobs) {
        const card = jobCard(job, cardCtx);
        cards.push({ job, card });
        listNode.append(card.node);
      }
    }
    function syncChecks() {
      for (const { job, card } of cards) {
        const input = card.node.querySelector(".cr-pick input");
        if (input) input.checked = st.selected.has(job.key);
        card.node.classList.toggle("is-selected", st.selected.has(job.key));
      }
    }
    function updateBulk() {
      if (!bulkNode) return;
      const picked = st.jobs.filter((job) => st.selected.has(job.key));
      const savable = picked.filter((job) => !job.already_in_aira && job.importable !== false);
      bulkNode.hidden = picked.length === 0;
      const free = unsaved();
      if (selectAll) { selectAll.checked = free.length > 0 && free.every((job) => st.selected.has(job.key)); selectAll.indeterminate = !selectAll.checked && picked.length > 0; selectAll.disabled = free.length === 0; }
      fill(bulkNode,
        h("strong", { text: `${picked.length} selected` }),
        h("span", { class: "muted", text: savable.length === picked.length ? "" : `${plural(picked.length - savable.length, "job")} already in Monosage or without a usable link will be skipped.` }),
        h("div", { class: "btn-row" },
          btn(savable.length ? `Save ${savable.length} to Monosage` : "Nothing to save", { icon: "bookmark", kind: "", disabled: savable.length === 0, onClick: (event) => bulkSave(event.currentTarget, savable) }),
          btn("Clear selection", { kind: "ghost", onClick: () => { st.selected.clear(); syncChecks(); updateBulk(); } })));
    }
    async function bulkSave(button, jobs) {
      await busy(button, async () => {
        let created = 0, merged = 0, failed = 0;
        for (let index = 0; index < jobs.length; index += 1) {
          button.querySelector("span:last-child").textContent = `Saving ${index + 1} of ${jobs.length}…`;
          try {
            const out = await importJob(jobs[index]);
            if (out.created) created += 1; else merged += 1;
            st.selected.delete(jobs[index].key);
          } catch (error) { failed += 1; if (failed === 1) Aira.reportError(error); }
        }
        syncChecks();
        const parts = [created ? `${plural(created, "new job")} saved` : "", merged ? `${merged} merged into jobs you already had` : "", failed ? `${failed} failed` : ""].filter(Boolean);
        toast(`${parts.join(", ")}.`, failed ? "error" : "success");
        announce(parts.join(", "));
        updateBulk();
      });
    }

    function renderResults() {
      cards.length = 0;
      if (!st.searched) {
        const recent = recall("jobs.recent", []);
        fill(resultsBox, h("section", { class: "cr-start" },
          h("h2", { text: "Find your next role" }),
          h("p", { class: "muted", text: "Search Google Jobs across employers and job boards. Save good postings straight into your Monosage pipeline, where they are scored against your resume, or set an alert and let Monosage watch for new ones." }),
          h("ul", { class: "cr-tips" },
            h("li", {}, h("strong", { text: "See the cost first. " }), "Each search is one SerpApi search; repeats within the cache window are free."),
            h("li", {}, h("strong", { text: "Refine with Google's own filters. " }), "They appear above the results and re-run the search."),
            h("li", {}, h("strong", { text: "Nothing is applied for you. " }), "Open the employer's page to apply; Monosage tracks the rest.")),
          profileRole ? h("div", { class: "cr-suggest" }, h("span", { class: "cr-fname", text: "From your profile" }),
            btn(`${profileRole}${profileLocation ? ` · ${profileLocation}` : ""}`, { kind: "secondary", sm: true, onClick: () => { st.form.q = profileRole; if (profileLocation) { st.form.location = profileLocation; st.form.gl = ""; } syncControls(); changed(); qInput.focus(); } })) : null,
          recent.length ? h("div", { class: "cr-suggest" }, h("span", { class: "cr-fname", text: "Recent searches" }), ...recent.map((item) => btn([item.q, item.location].filter(Boolean).join(" · "), { kind: "secondary", sm: true, onClick: () => { st.form = { ...emptyForm(), q: item.q, location: item.location || "", gl: item.gl || "" }; st.loadedSaved = ""; syncControls(); changed(); qInput.focus(); } }))) : null));
        return;
      }
      const meta = st.meta || {};
      selectAll = h("input", { type: "checkbox", id: "crSelectAll", onchange: (event) => { const free = unsaved(); for (const job of free) { if (event.currentTarget.checked) st.selected.add(job.key); else st.selected.delete(job.key); } syncChecks(); updateBulk(); } });
      headNode = h("div", { class: "cr-results-head" },
        h("div", { class: "cr-results-title" },
          st.jobs.length ? h("label", { class: "cr-selall" }, selectAll, h("span", { class: "visually-hidden", text: "Select every job not yet in Monosage" })) : null,
          h("h2", { text: st.jobs.length ? plural(st.jobs.length, "job") : "No jobs found" }),
          st.token ? h("span", { class: "muted", text: "more available" }) : null),
        h("div", { class: "cr-results-meta" },
          serp().creditChip({ status: "ok", cache_hit: Boolean(meta.cache_hit), credits: meta.spent ?? 1 }),
          meta.params ? btn("Playground", { icon: "play", kind: "ghost", sm: true, href: playgroundLink(meta.provider === "aira" ? "aira_jobs" : "google_jobs", meta.params), title: "Open this exact request in the API Playground" }) : null,
          meta.record_id ? btn("Stored result", { icon: "history", kind: "ghost", sm: true, title: meta.provider === "aira" ? "Open the stored Jobs response" : "Open the stored SerpApi response", onClick: () => Aira.airaapi && Aira.airaapi.openRecord(meta.record_id) }) : null));
      bulkNode = h("div", { class: "cr-bulk", role: "region", "aria-label": "Selected jobs", hidden: true });
      listNode = h("div", { class: "cr-jobs", "data-roving": "1" });
      moreNode = h("div", { class: "cr-more" });
      const notes = [];
      if (meta.provider === "aira" && meta.boards && meta.boards.length) {
        const used = meta.boards.filter((board) => board.results);
        const failed = meta.boards.filter((board) => board.error);
        notes.push(h("p", { class: "cr-boards muted" }, "Jobs from ", ...used.flatMap((board, index) => [index ? ", " : "", h("a", { href: board.homepage, target: "_blank", rel: "noopener noreferrer", text: board.name }), ` (${board.results})`]), used.length ? "" : "no board", ". Each job links back to its board.", failed.length ? ` Not reachable now: ${failed.map((board) => board.name).join(", ")}.` : ""));
      }
      if (meta.warnings && meta.warnings.length) notes.push(h("details", { class: "cr-warn" }, h("summary", {}, ico("warn"), h("span", { text: "Some filters may be ignored by Google" })), h("p", { text: meta.warnings.join(" ") }), meta.applied && meta.applied.length ? h("p", { class: "muted" }, "Sent: ", ...meta.applied.map((item) => chip(`${item.parameter}=${item.sent}`))) : null));
      if (!st.jobs.length) {
        fill(resultsBox, headNode, ...notes, ui.empty("No jobs matched", meta.note || "Try fewer filters, a broader title or a larger area.", st.form.uds || st.form.chips.length || st.form.posted || st.form.employment || st.form.remote ? btn("Clear filters", { kind: "secondary", onClick: () => { Object.assign(st.form, { remote: false, posted: "", employment: "", chips: [], uds: "", udsLabel: "" }); syncControls(); changed(); renderFilters(); runSearch(); } }) : null));
        return;
      }
      addCards(st.jobs);
      renderMore();
      fill(resultsBox, headNode, ...notes, bulkNode, listNode, moreNode);
      updateBulk();
    }
    function renderMore() {
      if (!moreNode) return;
      fill(moreNode, st.token ? btn("Load more jobs", { icon: "plus", kind: "secondary", cls: "cr-load", title: "Uses 1 search (free if cached)", onClick: (event) => busy(event.currentTarget, () => runSearch({ append: true })) }) : h("p", { class: "muted", text: st.jobs.length ? (st.meta && st.meta.provider === "aira" ? "That is every matching job the boards listed for this search." : "That is every job Google listed for this search.") : "" }),
        st.token ? h("span", { class: "muted", text: "Uses 1 search" }) : null);
    }

    async function runSearch({ append = false, quiet = false } = {}) {
      readInputs();
      if (!st.form.q.trim()) { toast("Enter a role or keywords to search for.", "error"); qInput.focus(); return; }
      remember("jobs.form", st.form);
      const mine = (searchSeq += 1);
      const body = searchBody(st.form, append ? st.token : "");
      if (!append) {
        fill(resultsBox, ui.skeleton(5));
        searchBtn.setAttribute("aria-busy", "true");
        searchBtn.disabled = true;
      }
      try {
        const data = await api(`${BASE}/jobs/search`, { method: "POST", body });
        if (mine !== searchSeq) return;
        const fresh = append ? data.jobs.filter((job) => !st.jobs.some((existing) => existing.key === job.key)) : data.jobs;
        if (append) {
          st.jobs = [...st.jobs, ...fresh];
          st.meta = { ...st.meta, cache_hit: st.meta.cache_hit && data.cache_hit, spent: (st.meta.spent || 0) + (data.credits || 0), record_id: data.record_id, params: data.params };
          if (!st.filters.length && data.filters.length) st.filters = data.filters;
        } else {
          st.jobs = data.jobs;
          st.selected.clear();
          st.filters = data.filters || [];
          st.meta = { cache_hit: data.cache_hit, spent: data.credits || 0, record_id: data.record_id, params: data.params, warnings: data.warnings, applied: data.applied, note: data.note, provider: data.provider || "google", boards: data.boards || [] };
          st.searched = true;
          pushRecent(st.form);
        }
        st.token = data.next_page_token || "";
        if (append) {
          addCards(fresh);
          renderMore();
          updateBulk();
          if (headNode) headNode.querySelector("h2").textContent = plural(st.jobs.length, "job");
          const firstNew = cards.find((entry) => entry.job === fresh[0]);
          if (firstNew) firstNew.card.node.querySelector(".cr-link").focus();
          announce(`${plural(fresh.length, "more job")} loaded. ${plural(st.jobs.length, "job")} in total.`);
        } else {
          renderFilters();
          renderResults();
          if (!quiet) announce(st.jobs.length ? `${plural(st.jobs.length, "job")} found${data.cache_hit ? ", served from the cache" : ""}.` : "No jobs matched.");
        }
      } catch (error) {
        if (mine !== searchSeq) return;
        if (append) { Aira.reportError(error); return; }
        fill(resultsBox, problem(error, () => runSearch()));
      } finally {
        if (!append && mine === searchSeq) { searchBtn.removeAttribute("aria-busy"); searchBtn.disabled = !canSearch(); }
      }
    }
    function pushRecent(source) {
      const item = { q: source.q.trim(), location: source.location.trim(), gl: source.gl };
      const rest = recall("jobs.recent", []).filter((entry) => !(entry.q === item.q && entry.location === item.location));
      remember("jobs.recent", [item, ...rest].slice(0, 6));
    }

    fillSaved();
    syncControls();
    renderFilters();
    renderResults();
    estimate();
    // A search that is still in the cache costs nothing, so the last results come back without a click.
    providerChanged();
    if (ctx.configured && st.form.provider === "google" && !st.searched && st.form.q.trim() && !deepLinked) {
      try {
        const probe = await api(`${BASE}/jobs/search`, { method: "POST", body: { ...searchBody(st.form), dry_run: true }, silent: true });
        if (probe.cache_hit) await runSearch({ quiet: true });
      } catch { /* the cost chip already says why */ }
    }
    if (deepLinked) qInput.focus();
  }

  // ===========================================================================
  // Reports (Company, Salary, Market, Interview prep, Travel, Learn)
  // ===========================================================================

  const TREND_WINDOWS = [["7d", "Past 7 days"], ["30d", "Past 30 days"], ["90d", "Past 90 days"], ["12m", "Past 12 months"], ["5y", "Past 5 years"], ["all", "Since 2004"]];
  const WINDOW_DAYS = { "7d": 7, "30d": 30, "90d": 90, "12m": 365, "5y": 1826 };
  const GEO_SUGGESTIONS = ["US", "GB", "CA", "AU", "DE", "FR", "IN", "SG", "AE", "NL", "IE", "US-CA", "US-NY", "US-TX", "US-WA"];
  const todayIso = () => new Date().toISOString().slice(0, 10);
  const money = (amount, currency) => {
    try { return Number(amount).toLocaleString([], { style: "currency", currency: currency || "USD", maximumFractionDigits: 0 }); }
    catch { return `${Number(amount).toLocaleString()} ${currency || ""}`.trim(); }
  };

  /** Field definitions per workspace. fields may be a function of the current values (Travel changes with its kind). */
  const REPORTS = {
    company: {
      kind: "company-intel", title: "Company intelligence", noun: "company report",
      blurb: "Who they are, what they are hiring for, what the press and employees say, and where their offices are. Built for the days before you apply or interview.",
      fields: [
        { name: "company", label: "Company", required: true, placeholder: "e.g. Stripe", max: 120, autofocus: true },
        { name: "role", label: "Role you are considering", placeholder: "e.g. Staff engineer", max: 120, hint: "Narrows the open roles and the jobs search link." },
        { name: "location", label: "Location", type: "location", hint: "Used for open roles and for finding offices nearby." },
      ],
    },
    salary: {
      kind: "salary", title: "Salary and market", noun: "salary report",
      blurb: "What employers state in current listings, what the web says a role pays, and what people report. The median is computed from listings that state pay; Monosage does not estimate figures it cannot see.",
      fields: [
        { name: "role", label: "Role", required: true, placeholder: "e.g. Solutions architect", max: 120, autofocus: true },
        { name: "location", label: "Location", type: "location" },
        { name: "company", label: "Company (optional)", max: 120, hint: "Adds a section for this employer's own listings." },
      ],
    },
    market: {
      kind: "trends", title: "Market trends", noun: "trend report",
      blurb: "How interest in skills or job titles is moving on Google, which searches are rising alongside them, and the latest news. Values are Google's relative interest, 0 to 100.",
      fields: [
        { name: "terms", label: "Skills or job titles to compare", type: "terms", required: true, placeholder: "e.g. kubernetes, terraform, pulumi", hint: "Up to 5, separated by commas. Related searches use the first term.", autofocus: true },
        { name: "geo", label: "Where", type: "geo", hint: "A country such as US, or a region such as US-CA. Empty means worldwide." },
        { name: "window", label: "Time window", type: "select", options: TREND_WINDOWS, default: "12m" },
      ],
    },
    interview: {
      kind: "interview-prep", title: "Interview prep", noun: "prep report",
      blurb: "Interview questions people report, first-hand candidate accounts, prep videos, recent news to have a view on, and what the postings ask for.",
      fields: [
        { name: "company", label: "Company", required: true, placeholder: "e.g. Datadog", max: 120, autofocus: true },
        { name: "role", label: "Role", required: true, placeholder: "e.g. Senior SRE", max: 120 },
      ],
    },
    travel: {
      kind: "travel", title: "Onsite travel", noun: "travel search",
      blurb: "Fares or stays for an onsite interview. One search per run, so it is cheap; Google's own price level is shown when it gives one.",
      fields: (values) => (values.kind === "hotels"
        ? [
          { name: "kind", label: "What to look up", type: "choice", options: [["flights", "Flights"], ["hotels", "Hotels"]], default: "flights" },
          { name: "q", label: "Destination", required: true, placeholder: "e.g. Austin, Texas", max: 120 },
          { name: "check_in_date", label: "Check in", type: "date", required: true },
          { name: "check_out_date", label: "Check out", type: "date", required: true },
          { name: "adults", label: "Guests", type: "number", min: 1, max: 9, default: "1" },
          { name: "sort_by", label: "Sort", type: "select", options: [["", "Relevance"], ["3", "Lowest price"], ["8", "Highest rating"], ["13", "Most reviewed"]] },
          { name: "rating", label: "Minimum rating", type: "select", options: [["", "Any"], ["7", "3.5+"], ["8", "4.0+"], ["9", "4.5+"]] },
          { name: "currency", label: "Currency", placeholder: "USD", max: 3, upper: true },
        ]
        : [
          { name: "kind", label: "What to look up", type: "choice", options: [["flights", "Flights"], ["hotels", "Hotels"]], default: "flights" },
          { name: "departure_id", label: "From (airport code)", required: true, placeholder: "e.g. AUS", max: 12, upper: true, hint: "Three-letter IATA code, or several separated by commas." },
          { name: "arrival_id", label: "To (airport code)", required: true, placeholder: "e.g. SFO", max: 12, upper: true },
          { name: "outbound_date", label: "Depart", type: "date", required: true },
          { name: "return_date", label: "Return", type: "date", hint: "Empty means one way." },
          { name: "travel_class", label: "Cabin", type: "select", options: [["", "Any"], ["1", "Economy"], ["2", "Premium economy"], ["3", "Business"], ["4", "First"]] },
          { name: "stops", label: "Stops", type: "select", options: [["", "Any"], ["1", "Nonstop only"], ["2", "1 stop or fewer"]] },
          { name: "adults", label: "Travelers", type: "number", min: 1, max: 9, default: "1" },
          { name: "currency", label: "Currency", placeholder: "USD", max: 3, upper: true },
        ]),
    },
    learn: {
      kind: "learn", title: "Learning", noun: "learning plan",
      blurb: "Courses and guides, video tutorials, advice from people who learned it, and how many open listings ask for the skill and what they pay.",
      fields: [
        { name: "skill", label: "Skill", required: true, placeholder: "e.g. Rust, Kubernetes, SQL", max: 120, autofocus: true },
        { name: "level", label: "Level", type: "select", options: [["", "Any"], ["beginner", "Beginner"], ["intermediate", "Intermediate"], ["advanced", "Advanced"]] },
      ],
    },
  };
  const fieldsOf = (cfg, values) => (typeof cfg.fields === "function" ? cfg.fields(values) : cfg.fields);

  /** In-memory state per workspace so switching tabs does not lose a form or a report. */
  const reportStates = {};
  const stateFor = (tab) => (reportStates[tab] ||= { values: null, report: null, plan: null, deselected: new Set(), stored: null });

  /** The request body for a form: trimmed, empty fields dropped, terms split into a list. */
  function reportPayload(cfg, values) {
    const payload = {};
    for (const field of fieldsOf(cfg, values)) {
      let value = values[field.name];
      if (value === undefined || value === null) continue;
      if (field.type === "terms") value = String(value).split(",").map((term) => term.trim()).filter(Boolean).filter((term, index, all) => all.findIndex((other) => other.toLowerCase() === term.toLowerCase()) === index);
      else value = String(value).trim();
      if (field.upper && typeof value === "string") value = value.toUpperCase();
      if (field.type === "number" && value !== "") value = Number(value);
      if (isEmpty(value)) continue;
      payload[field.name] = value;
    }
    return payload;
  }
  function missingFields(cfg, values) {
    const gaps = [];
    for (const field of fieldsOf(cfg, values)) if (field.required && isEmpty(reportPayload(cfg, values)[field.name])) gaps.push(field.label.replace(/ \(.*\)$/, ""));
    return gaps;
  }
  /** Problems the server would also reject, found before a click. */
  function formProblems(cfg, values) {
    const problems = [];
    const payload = reportPayload(cfg, values);
    if (cfg.kind === "trends" && Array.isArray(payload.terms) && payload.terms.length > 5) problems.push("Google Trends compares at most 5 terms.");
    if (cfg.kind === "travel" && values.kind !== "hotels" && payload.return_date && payload.outbound_date && payload.return_date < payload.outbound_date) problems.push("The return date is before the departure date.");
    if (cfg.kind === "travel" && values.kind === "hotels" && payload.check_in_date && payload.check_out_date && payload.check_out_date <= payload.check_in_date) problems.push("Check out must be after check in.");
    return problems;
  }

  /** Static fallback for the plan before the form is complete: the sections the server will run. */
  function playbookRows(cfg, overview, values) {
    if (cfg.kind === "travel") return [{ id: values.kind === "hotels" ? "hotels" : "flights", title: values.kind === "hotels" ? "Hotels" : "Flights", engine: values.kind === "hotels" ? "google_hotels" : "google_flights", why: values.kind === "hotels" ? "Stays with nightly and total rates." : "Best and other itineraries with Google's price insight.", credits: 1, cache_hit: false, nominal: true }];
    const book = (overview.playbooks || []).find((item) => item.id === cfg.kind);
    return book ? book.sections.map((section) => ({ ...section, credits: 1, cache_hit: false, nominal: true })) : [];
  }

  function buildField(field, values, onChange) {
    const id = uid("crf");
    const current = values[field.name] ?? field.default ?? "";
    values[field.name] = current;
    const set = (value) => { values[field.name] = value; onChange(field); };
    if (field.type === "choice") {
      const group = serp().segmented(field.options, current, (value) => set(value), { label: field.label });
      return { node: h("div", { class: "field cr-field-wide" }, h("span", { class: "label", text: field.label }), group.node) };
    }
    let control;
    if (field.type === "location") {
      control = serp().locationPicker({ value: String(current), id, placeholder: "City, region or country", onInput: (text) => set(text) });
    } else if (field.type === "select") {
      control = ui.select(field.options, String(current), { id });
      control.addEventListener("change", () => set(control.value));
    } else {
      const attrs = { id, type: field.type === "date" ? "date" : field.type === "number" ? "number" : "text", maxlength: field.max || (field.type === "terms" ? 400 : 200), placeholder: field.placeholder || "", value: String(current), autocomplete: "off", "aria-required": field.required ? "true" : null };
      if (field.type === "number") { attrs.min = field.min; attrs.max = field.max; }
      if (field.type === "date") attrs.min = todayIso();
      if (field.type === "geo") attrs.list = "crGeo";
      if (field.upper) attrs.style = { textTransform: "uppercase" };
      control = h("input", attrs);
      control.addEventListener("input", () => set(control.value));
      if (field.autofocus && !(control.value)) setTimeout(() => control.isConnected && control.focus({ preventScroll: true }), 0);
    }
    const wrapper = ui.field(field.label, control, field.hint);
    const labelNode = wrapper.querySelector("label");
    if (control.input) labelNode.setAttribute("for", control.input.id); // a location picker wraps its input
    if (field.required) labelNode.append(h("span", { class: "sk-req", "aria-hidden": "true", text: " *" }), h("span", { class: "visually-hidden", text: " (required)" }));
    return { node: wrapper };
  }

  async function renderReportTab(content, ctx) {
    const tab = ctx.tab;
    const cfg = REPORTS[tab];
    const st = stateFor(tab);
    const query = ctx.route.query;
    if (!st.values) st.values = { ...recall(`report.${tab}`, {}) };
    for (const field of fieldsOf(cfg, { kind: query.kind || "flights" })) if (query[field.name] !== undefined) st.values[field.name] = query[field.name];
    if (tab === "travel" && query.kind) st.values.kind = query.kind;
    if (!st.values.kind && cfg.kind === "travel") st.values.kind = "flights";
    if (cfg.kind === "salary" || cfg.kind === "company-intel") {
      const profile = Aira.state.profile || {};
      const candidate = profile.candidate_profile || {};
      const role = (profile.career_profiles && profile.career_profiles[candidate.career_profile]) || candidate.current_title || "";
      if (cfg.kind === "salary" && !st.values.role && role) st.values.role = role;
      if (!st.values.location && candidate.location) st.values.location = candidate.location;
    }

    const formNode = h("form", { class: "cr-rform", novalidate: true, "aria-label": `${cfg.title} inputs` });
    const planNode = h("section", { class: "cr-plan", "aria-label": "What will run and what it costs" });
    const storedNode = h("section", { class: "cr-stored", "aria-label": "Previous reports" });
    const resultNode = h("section", { class: "cr-rresult", "aria-label": "Report" });
    const geoList = h("datalist", { id: "crGeo" }, ...GEO_SUGGESTIONS.map((code) => h("option", { value: code })));
    const setup = ctx.configured ? null : setupPanel({ compact: true, error: ctx.overview.key_error || "" });
    fill(content,
      live, setup,
      h("div", { class: "cr-intro" }, h("div", {}, h("h2", { text: cfg.title }), h("p", { class: "muted", text: cfg.blurb }))),
      h("div", { class: "cr-split" },
        h("div", { class: "cr-side" }, h("div", { class: "cr-card" }, formNode, geoList), planNode, storedNode),
        resultNode));

    let planSeq = 0;
    let running = false;

    // ---- form --------------------------------------------------------------------------------------------------
    function renderForm() {
      const fields = fieldsOf(cfg, st.values);
      const grid = h("div", { class: "cr-fgrid" });
      for (const field of fields) {
        const built = buildField(field, st.values, (changedField) => {
          remember(`report.${tab}`, st.values);
          if (changedField.type === "choice") { st.plan = null; renderForm(); }
          schedulePlan();
          paintPlan();
        });
        grid.append(built.node);
      }
      fill(formNode, grid, h("div", { class: "cr-form-foot" }, btn("Clear", { kind: "ghost", sm: true, onClick: () => { st.values = cfg.kind === "travel" ? { kind: st.values.kind } : {}; remember(`report.${tab}`, st.values); st.plan = null; st.deselected.clear(); renderForm(); schedulePlan(); paintPlan(); } })));
      remember(`report.${tab}`, st.values);
    }
    formNode.addEventListener("submit", (event) => { event.preventDefault(); run(); });

    // ---- plan and cost ------------------------------------------------------------------------------------------
    const schedulePlan = debounce(async () => {
      const gaps = missingFields(cfg, st.values);
      if (gaps.length || formProblems(cfg, st.values).length) { st.plan = null; paintPlan(); return; }
      const mine = (planSeq += 1);
      try {
        const plan = await api(`${BASE}/${cfg.kind}`, { method: "POST", body: { ...reportPayload(cfg, st.values), dry_run: true }, silent: true });
        if (mine !== planSeq) return;
        st.plan = plan;
        st.planError = "";
      } catch (error) {
        if (mine !== planSeq) return;
        st.plan = null;
        st.planError = error.message;
      }
      paintPlan();
    }, 450);

    const rowsNow = () => (st.plan ? st.plan.plan : playbookRows(cfg, ctx.overview, st.values));
    const chosenRows = () => rowsNow().filter((row) => !st.deselected.has(row.id));
    const totalCredits = () => chosenRows().reduce((sum, row) => sum + (row.credits || 0), 0);
    const runLabel = () => {
      const rows = chosenRows();
      if (!rows.length) return "Choose a section to run";
      const total = totalCredits();
      return total === 0 ? `Run ${cfg.noun}, free (cached)` : `Run ${cfg.noun}, ${searchCount(total)}`;
    };

    function paintPlan() {
      const gaps = missingFields(cfg, st.values);
      const problems = formProblems(cfg, st.values);
      const exact = Boolean(st.plan);
      const rows = rowsNow();
      const list = h("ul", { class: "cr-plan-list" }, ...rows.map((row) => {
        const input = h("input", { type: "checkbox", checked: !st.deselected.has(row.id), disabled: rows.length === 1 || null, onchange: (event) => { if (event.currentTarget.checked) st.deselected.delete(row.id); else st.deselected.add(row.id); paintPlan(); } });
        return h("li", { class: "cr-plan-row" },
          h("label", {}, input, h("span", { class: "cr-plan-text" }, h("span", { class: "cr-plan-title", text: row.title }), row.why ? h("span", { class: "cr-plan-why", text: row.why }) : null)),
          h("span", { class: "cr-plan-meta" }, serp().engineBadge(row.engine),
            exact ? (row.cache_hit ? h("span", { class: "sk-credit cache", title: "Served from the local cache" }, ico("check"), h("span", { text: "Cached, free" })) : h("span", { class: "sk-credit", text: searchCount(row.credits) })) : h("span", { class: "sk-credit none", text: "1 search" })));
      }));
      const total = totalCredits();
      const cached = chosenRows().filter((row) => row.cache_hit).length;
      const runButton = h("button", { type: "button", class: "btn cr-run", disabled: running || !ctx.configured || gaps.length > 0 || problems.length > 0 || !chosenRows().length || !exact || null, onclick: () => run() }, ico("play"), h("span", { text: runLabel() }));
      let status;
      if (!ctx.configured) status = "Connect a SerpApi key to run this.";
      else if (gaps.length) status = `Fill in ${gaps.join(", ").replace(/, ([^,]*)$/, " and $1")} to see the exact cost.`;
      else if (problems.length) status = problems[0];
      else if (st.planError) status = st.planError;
      else if (!exact) status = "Working out the cost…";
      else status = total > CONFIRM_ABOVE ? `This uses more than ${CONFIRM_ABOVE} searches, so Monosage asks you to confirm first.` : cached ? `${cached} of ${chosenRows().length} sections are already cached and cost nothing.` : "Repeat runs inside the cache window are free.";
      fill(planNode,
        h("div", { class: "cr-plan-head" }, h("h3", { text: "What will run" }), h("span", { class: `cr-total${total > CONFIRM_ABOVE ? " warn" : ""}`, "aria-live": "polite", text: exact ? (total === 0 ? "Free" : searchCount(total)) : `Up to ${searchCount(rows.length)}` })),
        list,
        h("p", { class: `cr-plan-status${problems.length || st.planError ? " bad" : ""}`, role: "status", text: status }),
        runButton);
    }

    async function run() {
      if (running || !ctx.configured) return;
      const gaps = missingFields(cfg, st.values);
      if (gaps.length) { toast(`Fill in ${gaps.join(", ")} first.`, "error"); return; }
      if (!st.plan) { await schedulePlanNow(); if (!st.plan) return; }
      const rows = chosenRows();
      if (!rows.length) return;
      const total = totalCredits();
      if (total > CONFIRM_ABOVE && !(await serp().confirmCost(total, `The ${cfg.noun}`))) return;
      running = true;
      paintPlan();
      fill(resultNode, h("div", { class: "cr-running", role: "status" }, ui.skeleton(4), h("p", { class: "muted", text: `Running ${searchCount(rows.length)}… Sections run up to four at a time and a failed section does not stop the others.` })));
      const body = { ...reportPayload(cfg, st.values) };
      if (rows.length !== rowsNow().length) body.sections = rows.map((row) => row.id);
      try {
        const report = await api(`${BASE}/${cfg.kind}`, { method: "POST", body });
        st.report = report;
        announce(`${report.title}: ${report.sections.filter((section) => section.status === "ok").length} of ${report.sections.length} sections have results.`);
        st.plan = null; // costs changed: those searches are now cached
        loadStored();
      } catch (error) {
        fill(resultNode, problem(error, () => run()));
        running = false;
        paintPlan();
        return;
      }
      running = false;
      renderReport();
      paintPlan();
      schedulePlan();
    }
    const schedulePlanNow = async () => { schedulePlan.cancel(); const mine = (planSeq += 1); try { const plan = await api(`${BASE}/${cfg.kind}`, { method: "POST", body: { ...reportPayload(cfg, st.values), dry_run: true }, silent: true }); if (mine === planSeq) st.plan = plan; } catch (error) { Aira.reportError(error); } };

    // ---- result ---------------------------------------------------------------------------------------------------------
    function renderReport() {
      if (!st.report) {
        fill(resultNode, h("div", { class: "cr-empty" }, ui.empty(`Your ${cfg.noun} will appear here`, "Fill in the form, check what will run and what it costs, then run it. Every section is a recorded search you can reopen in the API Playground.")));
        return;
      }
      fill(resultNode, reportView(st.report, { cfg, tab, ctx, rerun: rerunSection, onDelete: null }));
    }
    async function rerunSection(section, button) {
      await busy(button, async () => {
        const report = st.report;
        const body = report.kind === "travel" ? { kind: report.input.kind, ...section.params } : { ...report.input };
        for (const key of Object.keys(body)) if (isEmpty(body[key])) delete body[key];
        body.sections = [section.id];
        const fresh = await api(`${BASE}/${report.kind}`, { method: "POST", body });
        const replacement = fresh.sections && fresh.sections[0];
        if (replacement) {
          report.sections = report.sections.map((entry) => (entry.id === section.id ? replacement : entry));
          report.credits = report.sections.reduce((sum, entry) => sum + (entry.credits || 0), 0);
          report.cache_hits = report.sections.filter((entry) => entry.cache_hit).length;
          report.errors = report.sections.filter((entry) => entry.status === "error").length;
        }
        renderReport();
        announce(`${section.title} ${replacement && replacement.status === "ok" ? "loaded" : "was tried again"}.`);
      });
    }

    // ---- previous reports -----------------------------------------------------------------------------------------------
    async function loadStored() {
      try {
        const data = await api(`${BASE}/reports?${new URLSearchParams({ kind: cfg.kind, limit: "8" })}`, { silent: true });
        st.stored = data.reports || [];
      } catch { st.stored = st.stored || []; }
      paintStored();
    }
    function paintStored() {
      const items = st.stored || [];
      if (!items.length) { storedNode.hidden = true; return; }
      storedNode.hidden = false;
      fill(storedNode, h("details", { class: "cr-card cr-stored-card", open: items.length <= 3 },
        h("summary", {}, h("h3", { text: "Previous reports" }), h("span", { class: "cr-count", text: String(items.length) })),
        h("ul", { class: "cr-stored-list" }, ...items.map((item) => h("li", {},
          h("button", { type: "button", class: "cr-stored-open", onclick: () => openStored(item) },
            h("span", { class: "cr-stored-title", text: item.title }),
            h("span", { class: "cr-stored-sub", text: `${fmt.ago(item.generated_at)} · ${item.credits ? searchCount(item.credits) : "free"}${item.errors ? ` · ${plural(item.errors, "section")} failed` : ""}` })),
          btn("", { icon: "trash", kind: "ghost", sm: true, aria: `Delete report ${item.title}`, title: "Delete this stored report", onClick: () => removeStored(item) }))))));
    }
    async function openStored(item) {
      fill(resultNode, ui.skeleton(4));
      try {
        const report = await api(`${BASE}/reports/${item.id}`);
        st.report = report;
        if (report.input && cfg.kind !== "travel") {
          for (const field of fieldsOf(cfg, st.values)) if (report.input[field.name] !== undefined) st.values[field.name] = Array.isArray(report.input[field.name]) ? report.input[field.name].join(", ") : report.input[field.name];
          renderForm(); schedulePlan(); paintPlan();
        }
        renderReport();
        resultNode.scrollIntoView({ block: "nearest" });
      } catch (error) { fill(resultNode, problem(error, () => openStored(item))); }
    }
    async function removeStored(item) {
      if (!(await Aira.confirm({ title: "Delete this report?", message: "The stored report is removed. The searches behind it stay in API history.", confirmLabel: "Delete", danger: true }))) return;
      try {
        await api(`${BASE}/reports/${item.id}`, { method: "DELETE" });
        st.stored = (st.stored || []).filter((entry) => entry.id !== item.id);
        if (st.report && st.report.id === item.id) { st.report = null; renderReport(); }
        paintStored();
        toast("Report deleted.", "success");
      } catch (error) { Aira.reportError(error); }
    }

    renderForm();
    paintPlan();
    renderReport();
    paintStored();
    loadStored();
    schedulePlan();
  }

  // ---- report rendering ------------------------------------------------------------------------------------------------

  const statTile = (label, value, sub) => h("div", { class: "stat cr-stat" }, h("span", { class: "label", text: label }), h("span", { class: "value", text: value }), sub ? h("span", { class: "sub", text: sub }) : null);
  const join = (...parts) => parts.filter((part) => !isEmpty(part)).join(" ");
  const compact = (n) => { const value = Number(n); return Number.isFinite(value) ? new Intl.NumberFormat([], { notation: "compact", maximumFractionDigits: 1 }).format(value) : String(n); };

  const META_CHIPS = [
    ["salary", (v) => chip(v, "pay")],
    ["remote", () => chip("Remote", "info")],
    ["schedule", (v) => chip(v)],
    ["via", (v) => chip(v)],
    ["posted", (v) => chip(v)],
    ["source", (v) => chip(v)],
    ["date", (v) => chip(v)],
    ["published", (v) => chip(v)],
    ["length", (v) => chip(v)],
    ["answers", (v) => chip(`${compact(v)} answers`)],
    ["rating", (v) => chip(`${v} rating`)],
    ["reviews", (v) => chip(`${compact(v)} reviews`)],
    ["hotel_class", (v) => chip(v)],
    ["travel_class", (v) => chip(v)],
    ["hours", (v) => chip(v)],
    ["phone", (v) => chip(v)],
  ];
  function metaChips(meta) {
    const nodes = META_CHIPS.map(([key, make]) => (meta && meta[key] !== undefined && meta[key] !== "" ? make(meta[key]) : null)).filter(Boolean);
    return nodes.length ? h("div", { class: "cr-chips" }, ...nodes) : null;
  }
  function itemRow(item, extra = []) {
    const url = safeUrl(item.url);
    const title = url ? externalLink(url, item.title, { class: "cr-item-title" }) : h("span", { class: "cr-item-title", text: item.title });
    return h("li", { class: "cr-item" },
      item.thumbnail ? serp().thumb(item.thumbnail, { alt: "", cls: "cr-item-thumb", fallback: item.title }) : null,
      h("div", { class: "cr-item-body" }, title, item.subtitle ? h("div", { class: "cr-item-sub", text: item.subtitle }) : null, extra.length ? h("div", { class: "cr-chips" }, ...extra) : null, metaChips(item.meta)));
  }
  const itemList = (items, extraFor = () => []) => h("ul", { class: "cr-items" }, ...items.map((item) => itemRow(item, extraFor(item))));

  function windowLabels(windowId, generatedAt, count) {
    const end = new Date(generatedAt || Date.now());
    const start = windowId === "all" ? new Date("2004-01-01T00:00:00Z") : new Date(end.getTime() - (WINDOW_DAYS[windowId] || 365) * 86400000);
    const long = (end - start) / 86400000 > 100;
    return Array.from({ length: count }, (_, i) => {
      const at = new Date(start.getTime() + ((end - start) * i) / Math.max(1, count - 1));
      return at.toLocaleDateString([], long ? { month: "short", year: "numeric" } : { month: "short", day: "numeric" });
    });
  }
  function trendBody(section, report) {
    const series = section.items.filter((item) => item.meta && Array.isArray(item.meta.points) && item.meta.points.length > 1);
    if (!series.length) return itemList(section.items);
    const count = Math.max(...series.map((item) => item.meta.points.length));
    const labels = windowLabels((report.input || {}).window, report.generated_at, count);
    return h("div", { class: "cr-trend" },
      serp().lineChart({ series: series.map((item) => ({ name: item.title, values: item.meta.points })), labels, height: 240, yMin: 0, yMax: 100, ariaLabel: `Search interest over time for ${series.map((item) => item.title).join(", ")}`, tableLabel: "Approximate date", format: (v) => String(Math.round(v)) }),
      h("p", { class: "cr-fnote", text: "Google's relative interest: 100 is the peak for the busiest term in the window. Dates are spread evenly across the window; the data table lists every point." }),
      h("div", { class: "cr-tiles" }, ...series.map((item) => {
        const meta = item.meta;
        const change = meta.change_pct;
        return h("div", { class: "cr-trend-tile" },
          h("strong", { text: item.title }),
          h("dl", {}, h("dt", { text: "Average" }), h("dd", { text: String(meta.average) }), h("dt", { text: "Peak" }), h("dd", { text: `${Math.round(meta.peak)}${meta.peak_date ? ` (${meta.peak_date})` : ""}` }), h("dt", { text: "Latest" }), h("dd", { text: String(Math.round(meta.latest)) })),
          change === null || change === undefined ? null : h("span", { class: `cr-delta ${change > 0 ? "up" : change < 0 ? "down" : ""}`, title: "Computed: the average of the last quarter of the window against the first quarter." }, h("span", { "aria-hidden": "true", text: change > 0 ? "▲" : change < 0 ? "▼" : "▬" }), h("span", { text: change === 0 ? "No change" : `${change > 0 ? "Up" : "Down"} ${Math.abs(change)}%` })));
      })));
  }
  function relatedBody(section) {
    const groups = [["rising", "Rising"], ["top", "Top"]];
    return h("div", { class: "cr-related" }, ...groups.map(([id, label]) => {
      const items = section.items.filter((item) => item.meta && item.meta.group === id);
      if (!items.length) return null;
      return h("div", { class: "cr-related-group" }, h("h4", { text: label }), h("ul", { class: "cr-pills" }, ...items.map((item) => h("li", {}, externalLink(item.url, [h("span", { text: item.title }), item.meta.value !== undefined ? h("small", { text: String(item.meta.value) }) : null], { class: "cr-pill" })))));
    }));
  }
  function jobsBody(section) {
    const stats = section.stats || {};
    const params = section.params || {};
    const tiles = [];
    if (stats.listings !== undefined) tiles.push(statTile("Listings read", String(stats.listings), `${stats.listings_with_pay || 0} state pay`));
    if (stats.median_annual !== null && stats.median_annual !== undefined) tiles.push(statTile("Median pay", money(stats.median_annual, stats.currency), `Computed from ${plural(stats.annualized, "listing")} with a stated pay period, annualized`));
    if (stats.min_annual !== null && stats.min_annual !== undefined) tiles.push(statTile("Stated range", `${money(stats.min_annual, stats.currency)} to ${money(stats.max_annual, stats.currency)}`, "Lowest and highest annualized figures"));
    return h("div", {}, tiles.length ? h("div", { class: "cr-tiles stats" }, ...tiles) : null, itemList(section.items), h("div", { class: "cr-sec-foot" }, btn("Search these in Jobs", { icon: "search", kind: "secondary", sm: true, href: link("jobs", { q: params.q, location: params.location }), title: "Open the Jobs search with this query. It does not run until you press Search." })));
  }
  function webBody(section) {
    const knowledge = section.items.find((item) => item.meta && item.meta.kind === "knowledge");
    const answer = section.items.find((item) => item.meta && item.meta.kind === "answer");
    const rest = section.items.filter((item) => item !== knowledge && item !== answer);
    const facts = knowledge ? Object.entries(knowledge.meta).filter(([key]) => key.startsWith("fact_")).map(([key, value]) => [humanize(key.slice(5)), String(value)]) : [];
    return h("div", {},
      knowledge ? h("div", { class: "cr-knowledge" }, knowledge.thumbnail ? serp().thumb(knowledge.thumbnail, { alt: "", cls: "cr-knowledge-img", fallback: knowledge.title }) : null,
        h("div", {}, h("h4", {}, safeUrl(knowledge.url) ? externalLink(knowledge.url, knowledge.title) : knowledge.title), knowledge.meta.type ? chip(knowledge.meta.type, "tone") : null, knowledge.subtitle ? h("p", { text: knowledge.subtitle }) : null,
          facts.length ? h("dl", { class: "kv cr-facts" }, ...facts.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])) : null)) : null,
      answer ? h("blockquote", { class: "cr-answer" }, h("p", { text: answer.title }), answer.subtitle ? h("footer", { text: answer.subtitle }) : null, safeUrl(answer.url) ? externalLink(answer.url, "Source", { class: "cr-answer-src" }) : null) : null,
      rest.length ? itemList(rest) : null);
  }
  function flightsBody(section) {
    const insights = section.price_insights || {};
    const tiles = [];
    if (insights.lowest_price !== undefined) tiles.push(statTile("Lowest price", String(insights.lowest_price), "As shown by Google"));
    if (insights.price_level) tiles.push(statTile("Price level", humanize(insights.price_level), "Google's own rating of these fares"));
    if (Array.isArray(insights.typical_price_range) && insights.typical_price_range.length === 2) tiles.push(statTile("Typical range", `${insights.typical_price_range[0]} to ${insights.typical_price_range[1]}`, "For this route and dates"));
    const prices = section.items.map((item) => item.meta && item.meta.price).filter((price) => typeof price === "number");
    const lowest = prices.length ? Math.min(...prices) : null;
    return h("div", {}, tiles.length ? h("div", { class: "cr-tiles stats" }, ...tiles) : null,
      itemList(section.items, (item) => [item.meta && item.meta.group === "best" ? chip("Best", "good") : null, item.meta && item.meta.price === lowest && lowest !== null ? chip("Lowest", "tone") : null, item.meta && item.meta.stops === 0 ? chip("Nonstop") : item.meta && item.meta.stops ? chip(plural(item.meta.stops, "stop")) : null].filter(Boolean)));
  }

  /** A section's items, drawn the way its content calls for. */
  function sectionBody(section, report) {
    if (section.id === "interest") return trendBody(section, report);
    if (section.id === "related_queries" || section.id === "related_topics") return relatedBody(section);
    if (section.engine === "google_jobs") return jobsBody(section);
    if (section.engine === "google_flights") return flightsBody(section);
    if (section.engine === "google") return webBody(section);
    return itemList(section.items);
  }

  function sectionCard(section, report, rerun) {
    const state = section.status;
    const badge = state === "ok" ? h("span", { class: "badge cr-ok" }, ico("check"), h("span", { text: "Done" })) : state === "empty" ? h("span", { class: "badge" }, h("span", { text: "No results" })) : h("span", { class: "badge cr-bad" }, ico("warn"), h("span", { text: "Failed" }));
    const cost = section.cache_hit ? h("span", { class: "sk-credit cache", title: "Served from the local cache. No search was used." }, ico("check"), h("span", { text: "Cached, free" })) : state === "error" ? h("span", { class: "sk-credit none", text: "No credit used" }) : h("span", { class: "sk-credit", text: `${section.credits} ${section.credits === 1 ? "search" : "searches"}` });
    const headingId = uid("crs");
    const hints = {
      invalid_api_key: ["SerpApi rejected the key. ", h("a", { href: accountLink(), text: "Replace it" }), "."],
      out_of_searches: ["No searches left on the SerpApi plan. ", h("a", { href: accountLink(), text: "Check the plan" }), "."],
      rate_limited: ["SerpApi is throttling this account. Try again in a few minutes."],
      local_rate_limit: ["Monosage's own hourly limit stopped this. Raise it in ", h("a", { href: accountLink(), text: "Account" }), "."],
    };
    const retryButton = state === "error" ? btn("Try again", { icon: "refresh", sm: true, kind: "secondary", title: "Run only this section again (1 search)", onClick: (event) => rerun(section, event.currentTarget) }) : null;
    return h("section", { class: `cr-sec ${state}`, "aria-labelledby": headingId },
      h("header", { class: "cr-sec-head" },
        h("div", { class: "cr-sec-titles" }, h("h3", { id: headingId, text: section.title }), serp().engineBadge(section.engine)),
        h("div", { class: "cr-sec-side" }, badge, cost)),
      section.summary && state !== "error" ? h("p", { class: "cr-sec-summary", text: section.summary }) : null,
      state === "error" ? h("div", { class: "cr-sec-error", role: "alert" }, h("p", { text: section.error || "This section failed." }), hints[section.code] ? h("p", { class: "muted" }, ...hints[section.code]) : null) : null,
      state === "ok" ? sectionBody(section, report) : null,
      state === "empty" ? h("p", { class: "muted", text: section.why || "Nothing matched this search. Try different wording." }) : null,
      h("footer", { class: "cr-sec-actions" },
        retryButton,
        btn("Open in Playground", { icon: "play", kind: "ghost", sm: true, href: playgroundLink(section.engine, section.params), title: "Open this exact request in the API Playground" }),
        section.record_id ? btn("Stored result", { icon: "history", kind: "ghost", sm: true, title: "Open the full stored SerpApi response", onClick: () => Aira.airaapi && Aira.airaapi.openRecord(section.record_id) }) : null));
  }

  /** Links from a report into the rest of Aira: jobs search, alerts, recruiters, interviews and related reports. */
  function linkActions(report, ctx) {
    const input = report.input || {};
    const roleQuery = join(input.company, input.role) || input.role || input.skill || "";
    const actions = [];
    const search = (label, q, location) => actions.push(btn(label, { icon: "search", sm: true, href: link("jobs", { q, location }) }));
    const alertFor = (q, location) => actions.push(btn("Create job alert", { icon: "bell", sm: true, onClick: () => openAlertFor({ q, location: location || "", gl: "", radius: "", remote: false, posted: "", employment: "", chips: [], uds: "" }) }));
    switch (report.kind) {
      case "company-intel":
        search("Search open roles", roleQuery, input.location);
        actions.push(btn("Add recruiter", { icon: "userplus", sm: true, onClick: () => recruiterDialog({ company: input.company, notes: `Found through the Careers company report for ${input.company}.` }) }));
        actions.push(btn("Add interview", { icon: "calendar", sm: true, onClick: () => interviewDialog({ company: input.company, role: input.role }) }));
        alertFor(roleQuery, input.location);
        break;
      case "salary":
        search("Search these roles", join(input.role, input.company), input.location);
        alertFor(join(input.role, input.company), input.location);
        actions.push(btn("Trend for this role", { icon: "search", sm: true, href: link("market", { terms: input.role }) }));
        break;
      case "trends":
        for (const term of (input.terms || []).slice(0, 3)) search(`Search jobs: ${term}`, term, "");
        break;
      case "interview-prep":
        actions.push(btn("Add interview", { icon: "calendar", sm: true, onClick: () => interviewDialog({ company: input.company, role: input.role }) }));
        actions.push(btn("Add recruiter", { icon: "userplus", sm: true, onClick: () => recruiterDialog({ company: input.company }) }));
        actions.push(btn("Company report", { icon: "search", sm: true, href: link("company", { company: input.company, role: input.role }) }));
        search("Search this role", roleQuery, "");
        break;
      case "travel":
        actions.push(btn("Add interview", { icon: "calendar", sm: true, onClick: () => interviewDialog({}) }));
        break;
      case "learn":
        search(`Search jobs asking for ${input.skill}`, input.skill, "");
        actions.push(btn("Trend for this skill", { icon: "search", sm: true, href: link("market", { terms: input.skill }) }));
        actions.push(btn("Salary for this skill", { icon: "search", sm: true, href: link("salary", { role: input.skill }) }));
        break;
      default:
        break;
    }
    return actions;
  }

  /** What the person already has in Aira for this company (their own local data; no search is spent). */
  async function pipelineNode(report) {
    const company = (report.input || {}).company;
    if (!company || !["company-intel", "interview-prep", "salary"].includes(report.kind)) return null;
    let jobs = [];
    let recruiters = [];
    try {
      const [jobData, recruiterData] = await Promise.all([
        api(`/api/jobs?${new URLSearchParams({ view: "all", q: company, page_size: "5" })}`, { silent: true }),
        api(`/api/recruiters?${new URLSearchParams({ company, limit: "5" })}`, { silent: true }),
      ]);
      jobs = jobData.jobs || [];
      recruiters = recruiterData.recruiters || [];
    } catch { return null; }
    if (!jobs.length && !recruiters.length) return h("p", { class: "cr-pipeline-none muted", text: `Nothing from ${company} in your Monosage pipeline yet.` });
    return h("section", { class: "cr-pipeline", "aria-label": `${company} in your Monosage pipeline` },
      h("h3", { text: `${company} in Monosage` }),
      jobs.length ? h("ul", { class: "cr-pipe-list" }, ...jobs.map((job) => h("li", {}, h("button", { type: "button", class: "cr-link", text: job.title || job.job_id, onclick: () => Aira.openJob(job.job_id) }), h("span", { class: "muted", text: [job.location, fmt.title(fmt.stage(job.stage))].filter(Boolean).join(" · ") })))) : null,
      recruiters.length ? h("ul", { class: "cr-pipe-list" }, ...recruiters.map((person) => h("li", {}, h("a", { href: `#/recruiters/${person.recruiter_id}`, text: person.name }), h("span", { class: "muted", text: person.title || "Recruiter" })))) : null);
  }

  function reportView(report, { cfg, tab, ctx, rerun }) {
    const ok = report.sections.filter((section) => section.status === "ok").length;
    const pipeline = h("div", { class: "cr-pipeline-slot" });
    pipelineNode(report).then((node) => { if (node) pipeline.append(node); });
    const actions = linkActions(report, ctx);
    return h("article", { class: "cr-report", "aria-label": report.title },
      h("header", { class: "cr-report-head" },
        h("div", {}, h("h2", { text: report.title }), h("p", { class: "muted", text: report.description || cfg.blurb })),
        h("div", { class: "cr-report-meta" },
          chip(`${ok} of ${report.sections.length} sections`, ok === report.sections.length ? "good" : "warn"),
          chip(report.credits ? `${report.credits} ${report.credits === 1 ? "search" : "searches"} used` : "No searches used"),
          report.cache_hits ? chip(`${report.cache_hits} cached`, "tone") : null,
          h("span", { class: "muted", title: fmt.datetime(report.generated_at), text: fmt.ago(report.generated_at) }))),
      actions.length ? h("div", { class: "cr-linkbar", role: "group", "aria-label": "Next steps in Monosage" }, ...actions) : null,
      pipeline,
      h("div", { class: "cr-secs" }, ...report.sections.map((section) => sectionCard(section, report, rerun))));
  }

  // ===========================================================================
  // Alerts tab
  // ===========================================================================

  async function renderAlerts(content, ctx) {
    let data = await api(`${BASE}/alerts`);
    jobsState.alertLimits = data.limits || {};
    const limits = jobsState.alertLimits;
    const open = new Set(ctx.route.query.alert ? [ctx.route.query.alert] : []);
    const details = new Map();
    const setup = ctx.configured ? null : setupPanel({ compact: true, error: ctx.overview.key_error || "" });
    const statsNode = h("div", { class: "stats cr-alert-stats" });
    const listNode = h("div", { class: "cr-alerts" });
    const newBtn = btn("New alert", { icon: "plus", kind: "", onClick: () => openAlertFor({ q: "", location: "", gl: "", radius: "", remote: false, posted: "", employment: "", chips: [], uds: "" }, () => reload()) });
    const making = Aira.careersTakeNew(ctx.route);
    fill(content, live, setup,
      h("div", { class: "cr-intro" }, h("div", {}, h("h2", { text: "Job alerts" }), h("p", { class: "muted", text: `A job alert is a saved Google Jobs search that Monosage re-runs on a schedule. It lists only jobs it has not seen before, can notify you, and can save strong matches into Jobs. Nothing is ever applied for you. Each run uses one search; you can keep up to ${limits.max_alerts || 10} alerts.` })), newBtn),
      statsNode, listNode);

    async function reload() {
      data = await api(`${BASE}/alerts`);
      paint();
    }
    function paint() {
      const alerts = data.alerts;
      const active = alerts.filter((alert) => alert.enabled);
      const perMonth = active.reduce((sum, alert) => sum + (alert.estimated_monthly_searches || 0), 0);
      const fresh = alerts.reduce((sum, alert) => sum + (alert.new_count || 0), 0);
      fill(statsNode,
        statTile("Alerts", `${alerts.length} of ${limits.max_alerts || 10}`, `${active.length} running`),
        statTile("New jobs waiting", String(fresh), fresh ? "Review them below" : "Nothing new right now"),
        statTile("Searches per month", String(perMonth), "Estimated from the schedules of running alerts"));
      if (!alerts.length) {
        fill(listNode, ui.empty("No job alerts yet", "Run a search in the Jobs tab and press Add alert, or create one here. Monosage checks on a schedule and tells you only about postings it has not shown you before.", btn("Create your first alert", { icon: "plus", kind: "", onClick: () => newBtn.click() })));
        return;
      }
      fill(listNode, ...alerts.map((alert) => alertCard(alert)));
    }

    function replaceAlert(next) { data.alerts = data.alerts.map((alert) => (alert.id === next.id ? { ...alert, ...next } : alert)); }

    function alertCard(alert) {
      const detailBox = h("div", { class: "cr-alert-new", hidden: !open.has(alert.id) });
      const toggle = btn(alert.new_count ? `Review ${alert.new_count} new` : "No new jobs", { icon: "search", kind: alert.new_count ? "" : "secondary", sm: true, disabled: !alert.new_count && !open.has(alert.id), onClick: () => { if (open.has(alert.id)) { open.delete(alert.id); detailBox.hidden = true; } else { open.add(alert.id); detailBox.hidden = false; loadDetail(alert, detailBox); } toggle.setAttribute("aria-expanded", String(open.has(alert.id))); } });
      toggle.setAttribute("aria-expanded", String(open.has(alert.id)));
      const runBtn = btn("Run now", { icon: "refresh", sm: true, title: "Runs the search now: 1 search, never served from the cache", disabled: !ctx.configured || !alert.monitor_id, onClick: (event) => busy(event.currentTarget, async () => {
        const out = await api(`${BASE}/alerts/${alert.id}/run`, { method: "POST", body: {} });
        replaceAlert(out.alert);
        details.delete(alert.id);
        if (out.found) open.add(alert.id);
        toast(out.summary || "Alert checked.", "success");
        announce(out.summary || "Alert checked.");
        paint();
      }) });
      const pauseBtn = btn(alert.enabled ? "Pause" : "Resume", { icon: alert.enabled ? "pause" : "play", kind: "ghost", sm: true, onClick: (event) => busy(event.currentTarget, async () => {
        const next = await api(`${BASE}/alerts/${alert.id}`, { method: "PATCH", body: { enabled: !alert.enabled } });
        replaceAlert(next);
        toast(next.enabled ? "Alert resumed." : "Alert paused. It will not use any searches until you resume it.", "success");
        paint();
      }) });
      const editBtn = btn("Edit", { icon: "edit", kind: "ghost", sm: true, onClick: () => alertDialog({ alert, limits, onSaved: (saved) => { replaceAlert(saved); paint(); } }) });
      const deleteBtn = btn("Delete", { icon: "trash", kind: "ghost", sm: true, onClick: async () => {
        if (!(await Aira.confirm({ title: "Delete this alert?", message: `"${alert.name}" stops running and its saved search and seen-jobs list are removed. Jobs you saved to Monosage stay.`, confirmLabel: "Delete alert", danger: true }))) return;
        try {
          await api(`${BASE}/alerts/${alert.id}`, { method: "DELETE" });
          data.alerts = data.alerts.filter((entry) => entry.id !== alert.id);
          toast("Alert deleted.", "success");
          paint();
        } catch (error) { Aira.reportError(error); }
      } });
      const statusBadge = alert.enabled ? h("span", { class: "badge cr-ok" }, ico("check"), h("span", { text: "Running" })) : h("span", { class: "badge" }, ico("pause"), h("span", { text: "Paused" }));
      const card = h("article", { class: `cr-alert${alert.enabled ? "" : " paused"}`, "aria-labelledby": `crAlert${alert.id}` },
        h("header", { class: "cr-alert-head" },
          h("div", { class: "cr-alert-titles" }, h("h3", { id: `crAlert${alert.id}`, text: alert.name }), [alert.q, alert.location].filter(Boolean).join(" · ") !== alert.name ? h("div", { class: "cr-job-sub", text: [alert.q, alert.location].filter(Boolean).join(" · ") }) : null),
          h("div", { class: "cr-alert-badges" }, statusBadge, alert.new_count ? h("span", { class: "badge cr-new", text: `${alert.new_count} new` }) : null)),
        h("div", { class: "cr-chips" },
          chip(alert.interval_minutes ? intervalLabel(alert.interval_minutes).replace(/^./, (c) => c.toUpperCase()) : "No schedule"),
          chip(`About ${searchCount(alert.estimated_monthly_searches || 0)} a month`),
          alert.auto_import ? chip(`Saves matches of ${alert.min_score}+ to Monosage`, "tone") : chip("Lists only, saves nothing"),
          alert.notify ? chip("Notifies you") : null),
        h("dl", { class: "kv cr-alert-facts" },
          h("dt", { text: "Last run" }), h("dd", { text: alert.last_run_at ? `${fmt.ago(alert.last_run_at)}${alert.last_summary ? `: ${alert.last_summary}` : ""}` : "Not yet" }),
          h("dt", { text: "Next run" }), h("dd", { text: alert.enabled && alert.next_run_at ? fmt.datetime(alert.next_run_at) : "Paused" })),
        !alert.baselined ? h("p", { class: "note" }, "The first run records the jobs that are posted now. Anything that appears after that is reported as new.") : null,
        h("div", { class: "cr-actions" }, toggle, runBtn, pauseBtn, editBtn,
          btn("Open in Jobs", { icon: "search", kind: "ghost", sm: true, title: "Load this search into the Jobs tab", onClick: () => { jobsState.form = formFromParams(alert.params || {}); jobsState.loadedSaved = ""; remember("jobs.form", jobsState.form); router.go(["careers"], { tab: "jobs" }); } }),
          btn("Playground", { icon: "play", kind: "ghost", sm: true, href: playgroundLink("google_jobs", alert.params || {}) }),
          deleteBtn),
        detailBox);
      if (open.has(alert.id)) loadDetail(alert, detailBox);
      return card;
    }

    async function loadDetail(alert, box) {
      fill(box, ui.skeleton(3));
      try {
        const detail = details.get(alert.id) || (await api(`${BASE}/alerts/${alert.id}`));
        details.set(alert.id, detail);
        const jobs = detail.new_jobs || [];
        if (!jobs.length) { fill(box, h("p", { class: "muted", text: "Nothing new to review. Jobs you dismiss disappear from this list." })); return; }
        const grid = h("div", { class: "cr-jobs", "data-roving": "1" });
        const dismiss = async (job) => {
          try {
            const out = await api(`${BASE}/alerts/${alert.id}/dismiss`, { method: "POST", body: { job_keys: [job.key] } });
            detail.new_jobs = detail.new_jobs.filter((entry) => entry !== job);
            replaceAlert(out.alert);
            details.set(alert.id, detail);
            paint();
          } catch (error) { Aira.reportError(error); }
        };
        const cardCtx = { onOpen: (job) => openJobDrawer(job, { list: jobs, alertFor: null }), dismiss };
        for (const job of jobs) grid.append(jobCard(job, cardCtx).node);
        fill(box,
          h("div", { class: "cr-new-head" }, h("strong", { text: `${plural(jobs.length, "new job")} found by this alert` }), h("div", { class: "btn-row" },
            btn("Dismiss all", { kind: "ghost", sm: true, onClick: async (event) => busy(event.currentTarget, async () => { const out = await api(`${BASE}/alerts/${alert.id}/dismiss`, { method: "POST", body: { all: true } }); replaceAlert(out.alert); details.delete(alert.id); open.delete(alert.id); paint(); toast("Marked as reviewed.", "success"); }) }))),
          grid);
      } catch (error) { fill(box, problem(error, () => loadDetail(alert, box))); }
    }

    paint();
    if (making === "alert") newBtn.click(); // "+ New > Create a job alert"
    if (ctx.route.query.alert) {
      const target = listNode.querySelector(`#crAlert${CSS.escape(ctx.route.query.alert)}`);
      if (target) target.scrollIntoView({ block: "start" });
    }
  }

  // ===========================================================================
  // Shell
  // ===========================================================================

  Aira.careers = { TABS, link, openJob: (job, ctx) => openJobDrawer(job, ctx) };

  Aira.views.careers = async (main, route) => {
    const tab = TAB_IDS.includes(route.query.tab) ? route.query.tab : "jobs";
    Aira.closeDrawer(); // a drawer from the previous tab must not survive navigation
    const label = (TABS.find(([id]) => id === tab) || TABS[0])[1];
    document.title = `${label} · Research · Careers · Monosage`;
    const crumbs = document.getElementById("crumbs");
    const sep = () => h("span", { class: "crumb-sep", "aria-hidden": "true", text: "/" });
    if (crumbs) crumbs.replaceChildren(h("span", { class: "crumb-app", text: "Careers" }), sep(), h("span", { text: "Research" }), sep(), h("span", { text: label }));

    const content = h("div", { class: "cr-content", id: "crContent", role: "region", "aria-label": label, tabindex: "-1" });
    const nav = h("nav", { class: "subnav cr-subnav careers-tabstrip", "aria-label": "Research workspaces" });
    const usage = h("span", { class: "cr-usage" });
    const renderNav = (alertCount) => fill(nav, ...TABS.map(([id, text]) => h("a", { href: link(id), "aria-current": id === tab ? "page" : "false" }, text, id === "alerts" && alertCount ? h("span", { class: "cr-nav-n", text: String(alertCount) }) : null)));
    renderNav(0);
    // The menu folders are drawn once the alert count is known (the menu does not redraw for a count alone).
    const folders = (alertCount) => Aira.careersFolders("careers", TABS.map(([id, text]) => ({ id, label: text, icon: TAB_ICONS[id], count: id === "alerts" ? alertCount : 0 })), tab);
    main.replaceChildren(
      h("div", { class: "page-head cr-head" },
        h("div", {}, h("h1", { text: "Research" }), h("p", { text: "Find jobs on Google or free job boards, research a company, check pay and demand, prepare for the interview, plan the trip and keep learning. Everything you keep lands in your Monosage pipeline." })),
        h("div", { class: "page-actions" }, usage)),
      nav, content);
    const active = nav.querySelector('[aria-current="page"]');
    if (active && active.scrollIntoView) active.scrollIntoView({ inline: "center", block: "nearest" });

    content.replaceChildren(ui.skeleton(5));
    let overview;
    try { overview = await api(`${BASE}/overview`); }
    catch (error) { folders(0); content.replaceChildren(problem(error, () => router.render())); return; }
    const fresh = (overview.alerts || []).reduce((sum, alert) => sum + (alert.new_count || 0), 0);
    renderNav(fresh);
    folders(fresh);
    const usage30 = overview.usage_30d || {};
    const searchesUsed = ((usage30.jobs || {}).searches || 0) + ((usage30.reports || {}).searches || 0);
    if (overview.configured && searchesUsed) fill(usage, h("a", { class: "btn ghost sm", href: "#/api?tab=history", title: "Careers searches in the last 30 days (they are tagged careers and intel in API history)" }, `${searchCount(searchesUsed)} in 30 days`));

    const ctx = { route, overview, tab, configured: Boolean(overview.configured) };
    try {
      if (tab === "jobs") await renderJobs(content, ctx);
      else if (tab === "alerts") await renderAlerts(content, ctx);
      else await renderReportTab(content, ctx);
    } catch (error) {
      console.error(error);
      content.replaceChildren(problem(error, () => router.render()));
    }
  };
})();

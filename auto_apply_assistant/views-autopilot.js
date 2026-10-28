/* Aira — Careers > Auto-apply: search plans that find matching jobs and apply on a schedule. The runs it holds
   for the person (questions, a filled form waiting for a click) are drawn on Needs you (part "needs"). */
"use strict";
(() => {
  const { h, api, fmt, ui, toast, withBusy } = Aira;
  const BASE = "/api/autopilot";
  const INTERVALS = [[60, "Every hour"], [120, "Every 2 hours"], [240, "Every 4 hours"], [480, "Every 8 hours"], [720, "Twice a day"], [1440, "Once a day"]];
  const BOARD_LABELS = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", smartrecruiters: "SmartRecruiters" };
  const STATUS = {
    queued: ["Queued", ""],
    running: ["Applying…", "info"],
    submitted: ["Applied", "good"],
    ready: ["Ready for your OK", "warn"],
    needs_review: ["Needs your answers", "warn"],
    failed: ["Failed", "bad"],
    unsupported: ["Apply manually", ""],
    already_applied: ["Already applied", ""],
    skipped: ["Skipped", ""],
  };
  const intervalLabel = (minutes) => (INTERVALS.find(([value]) => value === minutes) || [0, `Every ${minutes} minutes`])[1];
  const statusBadge = (status) => { const [label, tone] = STATUS[status] || [fmt.stage(status), ""]; return h("span", { class: `badge aap-status ${tone}`, text: label }); };
  const jobLabel = (run) => [run.job && run.job.title, run.job && run.job.company].filter(Boolean).join(" · ") || run.job_id;
  const toggle = (...args) => Aira.serp.toggleSwitch(...args);

  /** Renders Auto-apply into ``host`` and keeps it fresh while something runs.
   *  part "plans": status, readiness, search plans and activity (the Auto-apply page); part "needs": only the runs
   *  that wait for the person (Needs you), and ``onData`` hears each fresh answer; "all": everything. */
  Aira.renderAutopilot = async (host, { part = "all", onData = null } = {}) => {
    let data = null;
    let timer = null;
    const root = h("div", { class: "aap" }, ui.skeleton(6));
    host.replaceChildren(root);

    async function load({ quiet = false } = {}) {
      clearTimeout(timer);
      try { data = await api(BASE, { silent: quiet }); }
      catch (error) { if (!quiet) root.replaceChildren(ui.error(error, () => load())); return; }
      if (!root.isConnected) return;
      if (onData) onData(data);
      paint();
      // Poll while a run is going (or just finished) so results appear without a reload.
      if (data.status.running) timer = setTimeout(() => { if (root.isConnected) load({ quiet: true }); }, 2500);
    }
    const afterStart = (message) => { toast(message, "success"); setTimeout(() => load({ quiet: true }), 600); };

    function paint() {
      const sections = (part === "needs" ? [needsYou()] : part === "plans" ? [hero(), readiness(), plansSection(), activity()] : [hero(), readiness(), needsYou(), plansSection(), activity()]).filter(Boolean);
      root.replaceChildren(...sections);
    }

    // ---- status and controls ---------------------------------------------------------------
    function hero() {
      const s = data.status;
      const waiting = data.open.length;
      const running = s.running ? h("div", { class: "aap-running", role: "status" }, h("span", { class: "aap-spinner", "aria-hidden": "true" }), h("span", { text: `${s.running.detail || s.running.label}…` })) : null;
      const pause = h("button", { type: "button", class: `btn ${s.paused ? "" : "secondary"}`, text: s.paused ? "Resume auto-apply" : "Pause all" });
      pause.addEventListener("click", () => withBusy(pause, async () => {
        await api(`${BASE}/settings`, { method: "POST", body: { auto_apply_paused: !s.paused } });
        toast(s.paused ? "Auto-apply resumed." : "Paused. Nothing is searched or sent until you resume.", "success");
        load();
      }));
      const limit = ui.select([["0", "No daily limit"], ...[1, 2, 3, 5, 10, 15, 20, 30, 50].map((n) => [String(n), `${n} a day`])], String(s.daily_limit), { "aria-label": "Daily application limit" });
      if (![...limit.options].some((o) => o.value === String(s.daily_limit))) limit.append(h("option", { value: String(s.daily_limit), text: `${s.daily_limit} a day`, selected: true }));
      limit.addEventListener("change", async () => { try { await api(`${BASE}/settings`, { method: "POST", body: { daily_application_limit: Number(limit.value) } }); toast("Daily limit saved.", "success"); load({ quiet: true }); } catch (error) { Aira.reportError(error); } });
      const browser = toggle("Show the browser while applying", s.show_browser, async (on) => { try { await api(`${BASE}/settings`, { method: "POST", body: { apply_show_browser: on } }); } catch (error) { browser.set(!on); Aira.reportError(error); } }, { hint: "Watch Monosage fill each form" });
      const drafts = toggle("Review drafted answers", s.review_drafts !== false, async (on) => { try { await api(`${BASE}/settings`, { method: "POST", body: { review_drafted_answers: on } }); toast(on ? "Drafted answers now wait for your review." : "Confident drafted answers are now submitted; the rest wait for you.", "success"); } catch (error) { drafts.set(!on); Aira.reportError(error); } }, { hint: "On: answers the AI drafts from your resume wait for your OK. Off: confident ones are submitted. Sensitive questions always wait for you." });
      const sentText = s.daily_limit ? `${s.sent_today} of ${s.daily_limit} applications sent today` : `${s.sent_today} application${s.sent_today === 1 ? "" : "s"} sent today`;
      return h("section", { class: `aap-hero${s.paused ? " paused" : ""}`, "aria-label": "Auto-apply status" },
        h("div", { class: "aap-hero-main" },
          h("div", { class: "aap-state" }, h("span", { class: `aap-dot ${s.paused ? "off" : "on"}`, "aria-hidden": "true" }), h("strong", { text: s.paused ? "Auto-apply is paused" : data.plans.some((p) => p.enabled) ? "Auto-apply is on" : "No active search plan" })),
          h("p", { class: "aap-sub" }, sentText, waiting ? h("a", { href: part === "plans" ? "#/needs" : "#aapNeeds", class: "aap-waiting", text: ` · ${waiting} waiting for you in Needs you` }) : null),
          running),
        h("div", { class: "aap-hero-actions" },
          h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn", text: "New search plan", onclick: () => planEditor(null) }), pause),
          h("div", { class: "aap-hero-settings" }, ui.field("Daily limit", limit), browser.node, drafts.node)));
    }

    // ---- readiness ---------------------------------------------------------------------------
    function readiness() {
      const items = data.readiness;
      const problems = items.filter((item) => !item.ok || item.warn);
      if (!problems.length && data.plans.length) return null;
      return h("section", { class: "card aap-ready", "aria-labelledby": "aapReadyTitle" },
        h("div", { class: "aap-sec-head" }, h("h2", { id: "aapReadyTitle", text: "Before Monosage applies for you" }), h("button", { type: "button", class: "btn secondary sm", text: "Your answers", onclick: answersDialog })),
        h("p", { class: "muted", text: "Monosage only fills what it knows. Anything a form asks that is not covered here waits for you; nothing is guessed." }),
        h("ul", { class: "aap-checks" }, ...items.map((item) => h("li", { class: item.ok ? (item.warn ? "warn" : "ok") : item.required ? "bad" : "warn" },
          h("span", { class: "aap-check-icon", "aria-hidden": "true", text: item.ok && !item.warn ? "✓" : "!" }),
          h("div", {}, h("strong", { text: item.label }), h("span", { text: item.detail })),
          item.action === "answers" ? h("button", { type: "button", class: "btn secondary sm", text: item.ok ? "Review" : "Answer now", onclick: answersDialog })
            : item.action && (!item.ok || item.warn) ? h("a", { class: "btn secondary sm", href: item.action, text: "Fix" }) : h("span")))));
    }

    // ---- runs that wait for the user --------------------------------------------------------
    function needsYou() {
      if (!data.open.length) return null;
      return h("section", { id: "aapNeeds", "aria-labelledby": "aapNeedsTitle" },
        h("div", { class: "aap-sec-head" }, h("h2", { id: "aapNeedsTitle", text: part === "needs" ? `Held by Auto-apply (${data.open.length})` : `Needs you (${data.open.length})` })),
        h("div", { class: "aap-needs" }, ...data.open.map(openCard)));
    }

    function openCard(run) {
      const actions = h("div", { class: "btn-row" });
      const body = h("div", { class: "aap-open-body" });
      const head = h("header", {}, h("div", {}, h("button", { type: "button", class: "aap-job-link", text: jobLabel(run), onclick: () => Aira.openJob(run.job_id) }), h("div", { class: "aap-meta" }, statusBadge(run.status), run.score != null ? h("span", { class: "badge", text: `${Math.round(run.score)}% match` }) : null, run.ats ? h("span", { class: "badge source", text: run.ats }) : null, h("small", { class: "muted", text: fmt.ago(run.finished_at || run.created_at) }))));
      const skip = h("button", { type: "button", class: "btn ghost sm", text: "Skip this job", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`${BASE}/runs/${run.run_id}/skip`, { method: "POST", body: {} }); toast("Skipped. Monosage will not apply to it.", "success"); load(); }) });
      if (run.status === "ready") {
        body.append(h("p", { text: "Monosage filled the whole form. Look it over, then submit it with one click." }));
        const submit = h("button", { type: "button", class: "btn", text: "Submit application" });
        submit.addEventListener("click", () => withBusy(submit, async () => {
          if (!(await Aira.confirm({ title: "Submit this application?", message: `${jobLabel(run)}. Monosage fills the form again with the same answers and presses Submit.`, confirmLabel: "Submit" }))) return;
          await api(`${BASE}/runs/${run.run_id}/submit`, { method: "POST", body: {} });
          afterStart("Submitting… the result appears here in a moment.");
        }));
        actions.append(submit, run.screenshot_document_id ? h("button", { type: "button", class: "btn secondary sm", text: "See the filled form", onclick: () => runDrawer(run.run_id) }) : null, skip);
      } else {
        const questions = (run.unanswered || []).filter((q) => q.label);
        const drafted = questions.filter((q) => q.draft && q.draft.value);
        if (run.error && !questions.length) body.append(h("p", { class: "note warn", text: run.error }));
        if (drafted.length) body.append(h("p", { class: "note", text: `Monosage drafted ${drafted.length} answer${drafted.length === 1 ? "" : "s"} from your resume. Check and edit ${drafted.length === 1 ? "it" : "them"}, then approve.` }));
        const inputs = new Map();
        const form = h("form", { class: "aap-questions" });
        for (const q of questions) {
          const id = `q${Math.random().toString(36).slice(2, 8)}`;
          let input;
          const draft = q.draft && q.draft.value && !q.sensitive ? q.draft : null;
          if (q.options && q.options.length) input = ui.select([["", "Choose…"], ...q.options.map((o) => [o, o])], draft && q.options.includes(draft.value) ? draft.value : "", { id });
          else if (q.kind === "textarea" || q.label.length > 90) input = h("textarea", { id, rows: 2, maxlength: 4000 }, draft ? draft.value : "");
          else input = h("input", { id, type: q.kind === "number" ? "number" : "text", maxlength: 4000, value: draft ? draft.value : "" });
          inputs.set(q.label, input);
          const hint = q.sensitive ? "Sensitive: Monosage never drafts this. Answer it yourself."
            : draft ? `Drafted by the AI · ${draft.confidence} confidence${draft.based_on ? ` · based on ${draft.based_on}` : ""}` : q.draft_note || q.message;
          form.append(h("div", { class: `field${draft ? " aap-drafted" : ""}` }, h("label", { for: id, text: q.label + (q.required ? " *" : "") }), input, hint ? h("span", { class: "hint", text: hint }) : null));
        }
        const remember = h("input", { type: "checkbox", checked: true });
        if (questions.length) form.append(h("label", { class: "check" }, remember, "Remember these answers for future applications"));
        const go = h("button", { type: "submit", class: "btn", text: drafted.length ? (run.mode === "review" ? "Approve and fill the form" : "Approve and apply") : run.mode === "review" ? "Answer and fill the form" : "Answer and apply" });
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          withBusy(go, async () => {
            const answers = Object.fromEntries([...inputs].map(([label, input]) => [label, input.value.trim()]).filter(([, value]) => value));
            if (!Object.keys(answers).length) { toast("Answer the questions first.", "error"); return; }
            await api(`${BASE}/runs/${run.run_id}/answer`, { method: "POST", body: { answers, remember: remember.checked } });
            afterStart(run.mode === "review" ? "Filling the form with your answers…" : "Applying with your answers…");
          });
        });
        if (questions.length) { form.append(h("div", { class: "btn-row" }, go)); body.append(form); }
        actions.append(h("a", { class: "btn secondary sm", href: run.application_url || "#", target: "_blank", rel: "noopener noreferrer", text: "Apply on the site ↗" }), h("button", { type: "button", class: "btn ghost sm", text: "Details", onclick: () => runDrawer(run.run_id) }), skip);
      }
      return h("article", { class: `aap-open ${run.status}`, "aria-label": jobLabel(run) }, head, body, actions);
    }

    // ---- plans ---------------------------------------------------------------------------------
    function plansSection() {
      return h("section", { "aria-labelledby": "aapPlansTitle" },
        h("div", { class: "aap-sec-head" }, h("h2", { id: "aapPlansTitle", text: "Search plans" }), data.plans.length ? h("button", { type: "button", class: "btn secondary sm", text: "New search plan", onclick: () => planEditor(null) }) : null),
        data.plans.length ? h("div", { class: "aap-plans" }, ...data.plans.map(planCard))
          : ui.empty("Tell Monosage what you are looking for", "A search plan names the positions you want, where to look and how good a match must be. On its schedule Monosage searches, scores every job against your profile and applies to the best matches, up to your limits.", h("button", { type: "button", class: "btn", text: "Create your first search plan", onclick: () => planEditor(null) })));
    }

    function planCard(plan) {
      const where = [...plan.sources.map((s) => ({ linkedin: "LinkedIn", aira_jobs: "Free job boards", google_jobs: "Google Jobs" }[s] || s)), ...plan.boards.map((b) => `${BOARD_LABELS[b.source]}: ${b.identifier}`)];
      const enabled = toggle(plan.enabled ? "On schedule" : "Off", plan.enabled, async (on) => {
        try { await api(`${BASE}/plans/${plan.plan_id}`, { method: "PATCH", body: { enabled: on } }); toast(on ? "Plan on. Its first run starts within a minute." : "Plan off.", "success"); load({ quiet: true }); }
        catch (error) { enabled.set(!on); Aira.reportError(error); }
      });
      const runNow = h("button", { type: "button", class: "btn sm", text: "Run now", disabled: Boolean(data.status.running) });
      runNow.addEventListener("click", () => withBusy(runNow, async () => {
        if (plan.mode === "apply" && !(await Aira.confirm({ title: `Run "${plan.name}" now?`, message: `Monosage searches ${where.join(", ")} for ${plan.positions.join(", ")} and applies to up to ${plan.max_per_run} matching job${plan.max_per_run === 1 ? "" : "s"} scoring ${plan.min_score}% or more.`, confirmLabel: "Run now" }))) return;
        await api(`${BASE}/plans/${plan.plan_id}/run`, { method: "POST", body: {} });
        afterStart("Running… results appear below as they come in.");
      }));
      return h("article", { class: `aap-plan${plan.enabled ? "" : " off"}`, "aria-label": `Search plan ${plan.name}` },
        h("header", {}, h("div", {}, h("h3", { text: plan.name }), h("div", { class: "chips" }, ...plan.positions.map((p) => h("span", { class: "chip on", text: p })))),
          h("span", { class: `badge aap-mode ${plan.mode}`, text: plan.mode === "apply" ? "Applies automatically" : "Asks before submitting" })),
        ui.kv([
          ["Where", where.join(" · ")],
          ["Location", [plan.location || "Anywhere", plan.workplace ? fmt.stage(plan.workplace) : "", plan.job_type ? fmt.stage(plan.job_type) : ""].filter(Boolean).join(" · ")],
          ["Bar", `${plan.min_score}% match or better · up to ${plan.max_per_run} per run${plan.linkedin_easy_apply ? " · LinkedIn Easy Apply on" : ""}`],
          ["Schedule", `${intervalLabel(plan.interval_minutes)}${plan.enabled ? ` · next ${fmt.ago(plan.next_run_at)}` : ""}`],
          plan.google_searches_per_month ? ["SerpApi", `about ${plan.google_searches_per_month.toLocaleString()} searches a month`] : ["", null],
          ["Last run", plan.last_run_at ? `${fmt.ago(plan.last_run_at)} · ${plan.last_summary || plan.last_status}` : "Not run yet"],
        ]),
        h("footer", { class: "btn-row" }, runNow, enabled.node,
          h("button", { type: "button", class: "btn ghost sm", text: "Edit", onclick: () => planEditor(plan) }),
          h("button", { type: "button", class: "btn ghost sm", text: "History", onclick: () => passesDrawer(plan) }),
          h("button", { type: "button", class: "btn ghost sm danger", text: "Delete", onclick: async (event) => { if (!(await Aira.confirm({ title: `Delete "${plan.name}"?`, message: "The plan stops. Applications it sent stay in your pipeline.", confirmLabel: "Delete", danger: true }))) return; await withBusy(event.currentTarget, async () => { await api(`${BASE}/plans/${plan.plan_id}`, { method: "DELETE" }); toast("Plan deleted.", "success"); load(); }); } })));
    }

    // ---- activity --------------------------------------------------------------------------------
    function activity() {
      const runs = data.recent.filter((run) => !run.open);
      if (!runs.length && !data.passes.length) return null;
      const table = runs.length ? h("div", { class: "table-wrap" }, h("table", { class: "table aap-table" },
        h("caption", { class: "visually-hidden", text: "Recent automatic applications" }),
        h("thead", {}, h("tr", {}, ...["Job", "Status", "Match", "When", "Confirmation", ""].map((label) => h("th", { scope: "col", text: label })))),
        h("tbody", {}, ...runs.map((run) => h("tr", {},
          h("td", {}, h("button", { type: "button", class: "aap-job-link", text: jobLabel(run), onclick: () => Aira.openJob(run.job_id) }), run.error && run.status !== "submitted" ? h("div", { class: "aap-err", text: run.error }) : null),
          h("td", {}, statusBadge(run.status)),
          h("td", { class: "num", text: run.score != null ? `${Math.round(run.score)}%` : "—" }),
          h("td", { text: fmt.ago(run.finished_at || run.created_at) }),
          h("td", { text: run.confirmation_number || "—" }),
          h("td", {}, h("button", { type: "button", class: "btn ghost sm", text: "Details", onclick: () => runDrawer(run.run_id) }))))))) : null;
      const log = data.passes.length ? h("details", { class: "aap-log" }, h("summary", { text: `Run log (${data.passes.length})` }),
        h("ul", {}, ...data.passes.map((p) => h("li", {}, h("strong", { text: p.plan_name }), h("span", { text: ` · ${fmt.ago(p.started_at)} · ${p.trigger === "user" ? "run by you" : "scheduled"} · ` }), h("span", { class: p.status === "error" ? "aap-err" : "", text: p.summary || p.status }))))) : null;
      return h("section", { "aria-labelledby": "aapActivityTitle" }, h("div", { class: "aap-sec-head" }, h("h2", { id: "aapActivityTitle", text: "Recent activity" })), table, log);
    }

    // ---- plan editor -------------------------------------------------------------------------------
    function planEditor(plan) {
      const p = plan || { name: "", positions: [], location: Aira.recall("search.location", "United States"), workplace: "", job_type: "", sources: ["linkedin", "aira_jobs"], boards: [], exclude_keywords: [], exclude_companies: [], min_score: 70, max_per_run: 3, interval_minutes: 240, mode: "apply", linkedin_easy_apply: false, cover_letters: true, match_title: true };
      const name = h("input", { type: "text", maxlength: 120, value: p.name, placeholder: "e.g. Architect roles in Austin" });
      const positions = h("textarea", { rows: 2, maxlength: 700, placeholder: "One per line, up to 5: Solution Architect, Cloud Architect…" }, p.positions.join("\n"));
      const location = h("input", { type: "text", maxlength: 200, value: p.location, placeholder: "City, region or Remote" });
      const workplace = ui.select([["", "Any arrangement"], ["remote", "Remote"], ["hybrid", "Hybrid"], ["on_site", "On-site"]], p.workplace);
      const jobType = ui.select([["", "Any employment type"], ["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"], ["temporary", "Temporary"], ["internship", "Internship"]], p.job_type);
      const linkedin = h("input", { type: "checkbox", checked: p.sources.includes("linkedin") });
      const airaJobs = h("input", { type: "checkbox", checked: p.sources.includes("aira_jobs") });
      const google = h("input", { type: "checkbox", checked: p.sources.includes("google_jobs") });
      const boards = h("textarea", { rows: 2, maxlength: 2000, placeholder: "One per line, e.g. greenhouse: gitlab · lever: netflix · ashby: notion" }, p.boards.map((b) => `${b.source}: ${b.identifier}`).join("\n"));
      const minScore = ui.select([40, 50, 55, 60, 65, 70, 75, 80, 85, 90].map((n) => [String(n), `${n}% or better`]), String(p.min_score));
      const perRun = ui.select([1, 2, 3, 5, 8, 10].map((n) => [String(n), `${n} per run`]), String(p.max_per_run));
      const every = ui.select(INTERVALS.map(([v, l]) => [String(v), l]), String(p.interval_minutes));
      if (![...every.options].some((o) => o.value === String(p.interval_minutes))) every.append(h("option", { value: String(p.interval_minutes), text: intervalLabel(p.interval_minutes), selected: true }));
      const modeApply = h("input", { type: "radio", name: "aapMode", value: "apply", checked: p.mode === "apply" });
      const modeReview = h("input", { type: "radio", name: "aapMode", value: "review", checked: p.mode === "review" });
      const easy = h("input", { type: "checkbox", checked: p.linkedin_easy_apply });
      const letters = h("input", { type: "checkbox", checked: p.cover_letters });
      const titleMatch = h("input", { type: "checkbox", checked: p.match_title });
      const excludeWords = h("input", { type: "text", maxlength: 600, value: p.exclude_keywords.join(", "), placeholder: "e.g. clearance, unpaid, commission" });
      const excludeCompanies = h("input", { type: "text", maxlength: 1500, value: p.exclude_companies.join(", "), placeholder: "Companies to never apply to" });
      const easyWarn = h("p", { class: "note warn", hidden: !easy.checked, text: "LinkedIn does not allow automated applications in its User Agreement and may restrict accounts that use them. Turn this on only if you accept that risk; Monosage still never guesses an answer." });
      easy.addEventListener("change", () => { easyWarn.hidden = !easy.checked; });
      const body = h("div", { class: "aap-editor" },
        h("div", { class: "form-grid" }, ui.field("Plan name", name), ui.field("Location", location, "Leave empty for anywhere")),
        ui.field("Positions you are looking for", positions, "Monosage searches each one and only applies when the job title matches."),
        h("div", { class: "form-grid" }, ui.field("Arrangement", workplace), ui.field("Employment type", jobType)),
        h("fieldset", { class: "aap-fieldset" }, h("legend", { text: "Where to search" }),
          h("label", { class: "check" }, linkedin, "LinkedIn (needs LinkedIn connected)"),
          h("label", { class: "check" }, airaJobs, "Free job boards (Himalayas, The Muse, Remotive, Jobicy, Arbeitnow), no key needed"),
          h("label", { class: "check" }, google, "Google Jobs (uses one SerpApi search per position per run)"),
          ui.field("Company boards", boards, "Greenhouse, Lever, Ashby or SmartRecruiters, as source: identifier")),
        h("fieldset", { class: "aap-fieldset" }, h("legend", { text: "When to apply" }),
          h("div", { class: "form-grid" }, ui.field("Minimum match", minScore), ui.field("Applications per run", perRun), ui.field("Run", every)),
          h("label", { class: "check" }, modeApply, h("span", {}, h("strong", { text: "Apply automatically. " }), "Monosage submits forms it can fill completely; anything else waits for you.")),
          h("label", { class: "check" }, modeReview, h("span", {}, h("strong", { text: "Ask me first. " }), "Monosage fills each form and waits for your one-click OK before submitting."))),
        h("details", { class: "aap-more" }, h("summary", { text: "More options" }),
          ui.field("Never apply when the job mentions", excludeWords),
          ui.field("Never apply to these companies", excludeCompanies),
          h("label", { class: "check" }, titleMatch, "Only apply when the job title matches one of the positions"),
          h("label", { class: "check" }, letters, "Write a tailored cover letter with the local AI (when Ollama is running)"),
          h("label", { class: "check" }, easy, "Also apply through LinkedIn Easy Apply"), easyWarn));
      const parseList = (text) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      const parseBoards = (text) => text.split(/\n|·/).map((s) => s.trim()).filter(Boolean).map((line) => { const [source, ...rest] = line.split(":"); return { source: source.trim().toLowerCase(), identifier: rest.join(":").trim() }; });
      Aira.modal({ title: plan ? `Edit "${plan.name}"` : "New search plan", wide: true, body, actions: [
        { label: "Cancel", kind: "ghost" },
        { label: plan ? "Save plan" : "Create plan", kind: "", onClick: async () => {
          const payload = {
            name: name.value.trim(), positions: parseList(positions.value).slice(0, 5), location: location.value.trim(), workplace: workplace.value, job_type: jobType.value,
            sources: [linkedin.checked ? "linkedin" : "", airaJobs.checked ? "aira_jobs" : "", google.checked ? "google_jobs" : ""].filter(Boolean), boards: parseBoards(boards.value),
            min_score: Number(minScore.value), max_per_run: Number(perRun.value), interval_minutes: Number(every.value), mode: modeReview.checked ? "review" : "apply",
            exclude_keywords: parseList(excludeWords.value), exclude_companies: parseList(excludeCompanies.value), match_title: titleMatch.checked, cover_letters: letters.checked, linkedin_easy_apply: easy.checked,
          };
          if (!plan && payload.mode === "apply" && !(await Aira.confirm({ title: "Let Monosage apply for you?", message: `Every ${intervalLabel(payload.interval_minutes).replace(/^Every /, "").toLowerCase()}, Monosage searches for ${payload.positions.join(", ") || "your positions"} and submits applications to up to ${payload.max_per_run} jobs scoring ${payload.min_score}% or more, with your profile and confirmed answers. You can pause at any time.`, confirmLabel: "Yes, apply for me" }))) return false;
          try {
            await api(plan ? `${BASE}/plans/${plan.plan_id}` : `${BASE}/plans`, { method: plan ? "PATCH" : "POST", body: payload });
            toast(plan ? "Plan saved." : "Plan created. Its first run starts within a minute.", "success");
            load();
          } catch (error) { Aira.reportError(error); return false; }
          return undefined;
        } },
      ] });
      setTimeout(() => (plan ? name : positions).focus(), 30);
    }

    // ---- answers ---------------------------------------------------------------------------------------
    function answersDialog() {
      const rows = data.answers.map((answer) => {
        const input = answer.choices.length ? ui.select([["", "Not set"], ...answer.choices.map((c) => [c, c])], answer.value) : h("input", { type: "text", maxlength: 200, value: answer.value });
        return { answer, input };
      });
      const remembered = h("div", { class: "aap-remembered" });
      const paintRemembered = () => remembered.replaceChildren(...(data.remembered.length ? [h("h3", { text: "Answers you saved while applying" }), ...data.remembered.map((item) => h("div", { class: "row" }, h("div", {}, h("span", { class: "title", text: item.label }), h("span", { class: "sub", text: item.value })), h("button", { type: "button", class: "btn ghost sm danger", text: "Forget", onclick: (event) => withBusy(event.currentTarget, async () => { await api(`${BASE}/answers/${encodeURIComponent(item.label_key)}`, { method: "DELETE" }); data.remembered = data.remembered.filter((x) => x.label_key !== item.label_key); paintRemembered(); }) })))] : []));
      paintRemembered();
      const body = h("div", { class: "aap-answers" },
        h("p", { class: "muted", text: "Saving an answer confirms it: Monosage uses it on every form that asks the same question. Sensitive answers (work authorization, sponsorship, attestations) are only ever filled from here." }),
        ...rows.map(({ answer, input }) => ui.field(answer.label, input, answer.sensitivity === "sensitive" ? "Sensitive" : null)),
        h("p", { class: "muted", style: { fontSize: "12.5px" } }, "Demographic and other questions can be answered in ", h("a", { href: "#/profile?tab=answers", text: "Settings > Standard answers" }), "."),
        remembered);
      Aira.modal({ title: "Your answers", wide: true, body, actions: [
        { label: "Close", kind: "ghost" },
        { label: "Save answers", kind: "", onClick: async () => {
          try {
            for (const { answer, input } of rows) {
              const value = input.value.trim();
              if (!value || (value === answer.value && answer.confirmed)) continue;
              await api("/api/profile/answers", { method: "POST", body: { canonical_field: answer.canonical_field, value, confirmed: true, sensitivity: answer.sensitivity } });
            }
            toast("Answers saved.", "success");
            load({ quiet: true });
          } catch (error) { Aira.reportError(error); return false; }
          return undefined;
        } },
      ] });
    }

    // ---- drawers ----------------------------------------------------------------------------------------
    async function runDrawer(runId) {
      const body = h("div", {}, ui.skeleton(4));
      const drawer = Aira.drawer({ title: "Application", body });
      let run;
      try { run = (await api(`${BASE}/runs/${runId}`)).run; } catch (error) { body.replaceChildren(ui.error(error)); return; }
      drawer.head.querySelector("h2").textContent = jobLabel(run);
      const shot = h("div", { class: "aap-shot" });
      if (run.screenshot_document_id) {
        shot.append(h("button", { type: "button", class: "btn secondary sm", text: "Show screenshot", onclick: (event) => withBusy(event.currentTarget, async () => {
          const response = await api(`/api/documents/${run.screenshot_document_id}/download`, { raw: true });
          const url = URL.createObjectURL(await response.blob());
          shot.replaceChildren(h("img", { src: url, alt: `Screenshot of the application form for ${jobLabel(run)}` }));
        }) }));
      }
      const questions = run.questions || [];
      const drafted = run.drafted_answers || [];
      body.replaceChildren(
        h("div", { class: "btn-row", style: { marginBottom: "12px" } }, statusBadge(run.status), h("button", { type: "button", class: "btn secondary sm", text: "Open job", onclick: () => Aira.openJob(run.job_id) }), run.application_url ? h("a", { class: "btn ghost sm", href: run.application_url, target: "_blank", rel: "noopener noreferrer", text: "Application page ↗" }) : null),
        run.error ? h("p", { class: `note ${run.status === "submitted" ? "" : "warn"}`, text: run.error }) : null,
        ui.kv([["Mode", run.mode === "apply" ? "Apply automatically" : "Ask before submitting"], ["Started by", run.trigger === "user" ? "You" : "Schedule"], ["Form", run.ats || run.route || "—"], ["Confirmation", run.confirmation_number || (run.status === "submitted" ? "Shown on the page (see screenshot)" : "—")], ["Started", fmt.datetime(run.started_at || run.created_at)], ["Finished", run.finished_at ? fmt.datetime(run.finished_at) : "—"]]),
        shot,
        drafted.length ? h("p", { class: "note" }, h("span", { text: drafted.length === 1 ? "1 answer was drafted by the AI from your resume." : `${drafted.length} answers were drafted by the AI from your resume.` }), " ", h("span", { text: drafted.every((d) => d.approved_by === "you") ? "You approved them." : drafted.some((d) => d.approved_by === "auto") ? "Confident ones were used without review." : "" })) : null,
        questions.length ? h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", { text: `Questions on the form (${questions.length})` }), h("ul", { class: "aap-qlist" }, ...questions.map((q) => h("li", { class: q.status }, h("span", {}, q.label + (q.required ? " *" : ""), q.drafted ? h("span", { class: "badge aap-drafted-badge", title: q.drafted.based_on || "", text: q.drafted.approved_by === "auto" ? "AI-drafted" : q.drafted.edited ? "AI-drafted, edited by you" : "AI-drafted, approved by you" }) : null), h("span", { class: "muted", text: q.value ? `${q.value}` : q.message || fmt.stage(q.status) }))))) : null,
        (run.steps || []).length ? h("details", { class: "aap-steps", open: run.status !== "submitted" }, h("summary", { text: `What Monosage did (${run.steps.length} steps)` }), h("ol", {}, ...run.steps.map((step) => h("li", { text: step })))) : null);
    }

    async function passesDrawer(plan) {
      const body = h("div", {}, ui.skeleton(4));
      Aira.drawer({ title: `History · ${plan.name}`, body });
      try {
        const { passes } = await api(`${BASE}/plans/${plan.plan_id}/passes`);
        body.replaceChildren(passes.length ? h("ul", { class: "timeline" }, ...passes.map((p) => h("li", {}, h("strong", { text: `${fmt.datetime(p.started_at)} · ${p.trigger === "user" ? "run by you" : "scheduled"}` }), h("div", { text: p.summary || p.status }), p.log && p.log.length ? h("details", {}, h("summary", { text: "Details" }), h("ul", { class: "inline-list" }, ...p.log.map((line) => h("li", { text: line })))) : null))) : ui.empty("No runs yet", "Run the plan now or wait for its schedule."));
      } catch (error) { body.replaceChildren(ui.error(error)); }
    }

    await load();
  };

  /** The "Apply for me" card in a job's Apply tab. */
  Aira.renderAutoApplyCard = (job, reload) => {
    const runs = job.auto_runs || [];
    const active = runs.find((run) => ["queued", "running", "needs_review", "ready"].includes(run.status));
    const card = h("div", { class: "card aap-jobcard" }, h("h3", { text: "Let Monosage apply" }));
    if (job.submission) {
      const sent = runs.find((run) => run.status === "submitted");
      const drafted = sent ? (sent.drafted_answers || []).length : 0;
      card.append(h("p", { class: "muted", text: "This job already has a recorded application." }), drafted ? h("p", { class: "muted", text: drafted === 1 ? "1 answer was drafted by the AI from your resume." : `${drafted} answers were drafted by the AI from your resume.` }) : null);
      return card;
    }
    if (active) {
      card.append(h("p", {}, statusBadge(active.status), " ", active.status === "running" || active.status === "queued" ? "Monosage is working on it." : "It waits for you in Needs you."), active.status === "running" || active.status === "queued" ? h("a", { class: "btn secondary sm", href: "#/autopilot", text: "Open Auto-apply" }) : h("a", { class: "btn secondary sm", href: "#/needs", text: "Open Needs you" }));
      return card;
    }
    const last = runs[0];
    if (last) card.append(h("p", { class: "muted" }, "Last attempt: ", statusBadge(last.status), last.error ? ` ${last.error}` : ""));
    card.append(h("p", { class: "muted", text: "Monosage fills the employer's form with your profile and confirmed answers. Questions it cannot answer come back to you; nothing is guessed." }),
      h("p", { class: "muted", text: job.auto_review_drafts === false ? "The AI drafts answers from your resume for the rest; confident ones are submitted. Sensitive questions always wait for you." : "The AI drafts answers from your resume for the rest; you review them before anything is sent. Sensitive questions always wait for you." }));
    const start = (mode) => async (event) => withBusy(event.currentTarget, async () => {
      if (mode === "apply" && !(await Aira.confirm({ title: "Apply to this job now?", message: `${job.title || "This job"}${job.company ? ` at ${job.company}` : ""}. Monosage submits the application if it can answer every required question.`, confirmLabel: "Apply for me" }))) return;
      await api(`/api/jobs/${encodeURIComponent(job.job_id)}/auto-apply`, { method: "POST", body: { mode } });
      toast(mode === "apply" ? "Applying… follow it in Auto-apply." : "Filling the form… it will wait for your OK.", "success");
      setTimeout(() => reload("apply"), 1500);
    });
    card.append(h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn", text: "Apply for me", onclick: start("apply") }), h("button", { type: "button", class: "btn secondary", text: "Fill it, I'll submit", onclick: start("review") })));
    return card;
  };
})();

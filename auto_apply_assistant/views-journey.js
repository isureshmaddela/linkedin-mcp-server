/* Aira — Life (#/journey): goals, habits, the timeline of a life and the document vault, for one person.
 *
 * Tabs (?tab=): overview (active goals, life score, habits today, money goals, documents, what is coming up,
 * progress by area), goals (steps, check-ins, goals measured in a number or money, earning goals that count what
 * the account's apps earned, a public cheer page per goal, filters, CSV), habits (every day or a few days a week,
 * streaks, the last four weeks, a habit that adds to a savings goal), journal (a mood and a few lines a day, the
 * mood of the last 30 days, "on this day" memories, CSV), timeline (milestones by year, the household's shared ones
 * too, CSV) and documents (passports, certificates, insurance cards with expiry reminders; numbers and scans are kept
 * encrypted). A goal can take supporters: people back it from its public page and pay the owner through the owner's
 * own link; the owner marks each pledge received. Links: ?tab=goals&goal=<id> opens a goal, ?tab=documents&doc=<id> a
 * document, ?open=habit:<id> | entry:<id> | event:<id> shows that habit, journal day or event outlined, ?new=<kind>
 * opens the form to add one and ?tab=search&q=<words> lists everything that matches.
 *
 * In the Aira frame (shell.js): "+ New" offers a goal, a habit, today's journal, a timeline event and a document; the
 * search box across the top finds all five (GET /api/journey/search); the tabs are the left menu's folders.
 * Everything runs through /api/journey/* (aira/suite/journey). DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("journey", path, opts);
  const put = (node, ...children) => node.append(...children.filter(Boolean));

  // Glyphs for the left menu's folders, "+ New" and the search box (24x24 strokes, like app.js ICONS).
  const GLYPHS = {
    "jr-goal": '<svg viewBox="0 0 24 24"><path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/></svg>',
    "jr-habit": '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v4h-4"/><path d="m8.5 12 2.5 2.5 4.5-5"/></svg>',
    "jr-journal": '<svg viewBox="0 0 24 24"><path d="M6 3h11a1 1 0 0 1 1 1v17H7a2 2 0 0 1-2-2V5a2 2 0 0 1 1-2z"/><path d="M5 19a2 2 0 0 1 2-2h11M9 7h5M9 11h4"/></svg>',
    "jr-timeline": '<svg viewBox="0 0 24 24"><path d="M12 3v18"/><circle cx="12" cy="7" r="2"/><circle cx="12" cy="17" r="2"/><path d="M14 7h6M4 17h6"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;

  const CATEGORIES = [["career", "Career"], ["health", "Health"], ["money", "Money"], ["learning", "Learning"], ["family", "Family"], ["relationships", "Relationships"], ["home", "Home"], ["travel", "Travel"], ["creative", "Creative"], ["personal", "Personal"]];
  const CATEGORY = Object.fromEntries(CATEGORIES);
  const STATUSES = [["active", "Active"], ["paused", "Paused"], ["completed", "Completed"], ["dropped", "Dropped"]];
  const STATES = { on_track: ["On track", "good"], behind: ["Behind", "warn"], overdue: ["Overdue", "bad"], completed: ["Completed", "good"], paused: ["Paused", ""], dropped: ["Dropped", ""] };
  const MEASURES = [["percent", "By percent, or by ticking steps"], ["number", "A number (km, books, hours)"], ["money", "Money (save or earn an amount)"]];
  const KINDS = [["life", "Life"], ["birth", "Birth"], ["education", "Education"], ["career", "Career"], ["family", "Family"], ["relationship", "Relationship"], ["home", "Home"], ["health", "Health"], ["travel", "Travel"], ["achievement", "Achievement"]];
  const KIND = Object.fromEntries(KINDS);
  const KIND_MARK = { life: "🌱", birth: "👶", education: "🎓", career: "💼", family: "👪", relationship: "💞", home: "🏠", health: "🩺", travel: "✈️", achievement: "🏆" };
  const DOC_KINDS = [["passport", "Passport"], ["id_card", "ID card"], ["driving_licence", "Driving licence"], ["birth_certificate", "Birth certificate"], ["visa", "Visa or permit"], ["diploma", "Diploma or certificate"], ["insurance", "Insurance"], ["medical", "Medical record"], ["tax", "Tax"], ["property", "Property"], ["vehicle", "Vehicle"], ["bank", "Bank"], ["will", "Will"], ["other", "Other"]];
  const DOC_KIND = Object.fromEntries(DOC_KINDS);
  const DOC_STATES = { valid: ["Valid", "good"], expires_soon: ["Expires soon", "warn"], expired: ["Expired", "bad"], no_expiry: ["No expiry", ""] };
  const PER_WEEK = [["7", "Every day"], ["6", "6 days a week"], ["5", "5 days a week"], ["4", "4 days a week"], ["3", "3 days a week"], ["2", "2 days a week"], ["1", "Once a week"]];
  const HABIT_IDEAS = ["Walk 30 minutes", "Read 20 pages", "Drink 8 glasses of water", "Save a little money", "Call someone I love", "Learn something new"];
  const MOODS = [[5, "Great", "😄"], [4, "Good", "🙂"], [3, "Okay", "😐"], [2, "Low", "😕"], [1, "Hard", "😞"]];
  const MOOD = Object.fromEntries(MOODS.map(([value, label, mark]) => [value, { label, mark }]));
  const PLEDGE_STATES = { promised: ["Promised", "warn"], received: ["Received", "good"], cancelled: ["Cancelled", ""] };
  // A deep link (?goal=, ?doc=) opens its item once, not again on every redraw of the same address.
  let opened = "";

  const statePill = (state, map) => { const [label, tone] = map[state] || [state, ""]; return h("span", { class: `suite-pill as-written ${tone}`, text: label }); };
  const progressBar = (value, label) => h("div", { class: "jr-progress", role: "progressbar", "aria-label": label || "Progress", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(value) }, h("span", { style: { width: `${Math.max(0, Math.min(100, value))}%` } }));
  const size = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : `${bytes} B`);
  // An amount kept in hundredths, as a person types it ("12.5", "50000").
  const plain = (cents) => (cents === null || cents === undefined ? "" : String(Number(cents) / 100));
  function amount(goal, cents) {
    if (goal.measure === "money") return S.money(cents, goal.currency);
    const value = Number(cents || 0) / 100;
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${goal.unit ? ` ${goal.unit}` : ""}`;
  }
  const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
  const measured = (goal) => goal.measure === "number" || goal.measure === "money";
  const earning = (goal) => goal.source === "earnings";
  function inDays(days) {
    if (days === null || days === undefined) return "";
    if (days === 0) return "Today";
    if (days === 1) return "Tomorrow";
    if (days === -1) return "Yesterday";
    const n = Math.abs(days);
    // "In 1175 days" is hard to read: far dates are said in months or years.
    const span = n < 60 ? plural(n, "day", "days") : n < 730 ? plural(Math.round(n / 30.44), "month", "months") : plural(Math.round(n / 365.25), "year", "years");
    return days > 0 ? `In ${span}` : `${span} ago`;
  }
  const localDay = (when = new Date()) => `${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, "0")}-${String(when.getDate()).padStart(2, "0")}`;
  // The field (label and hint) around a form control, to show or hide it.
  const fieldOf = (form, name) => { let node = form.inputs[name].control; while (node.parentElement && node.parentElement !== form.node) node = node.parentElement; return node; };

  // ---------------------------------------------------------------- goals
  async function goalForm(goal, rerender, preset = {}) {
    const editing = !!goal;
    const [business, earnings] = await Promise.all([S.business(), api("/earnings").catch(() => ({ allowed: false }))]);
    const values = editing
      ? { ...goal, target: plain(goal.target_amount), source: earning(goal) }
      : { category: "personal", measure: "percent", currency: business.currency || "USD", ...preset, source: preset.source === "earnings" };
    const fields = [
      { name: "title", label: "Goal", required: true, placeholder: "Run a 5K" },
      { name: "category", label: "Area of life", type: "select", options: CATEGORIES },
      { name: "measure", label: "How will you measure it?", type: "select", options: MEASURES },
      { name: "unit", label: "Unit", placeholder: "km, books, hours" },
      { name: "target", label: "Target", placeholder: "100", hint: "The number you want to reach." },
      editing ? null : { name: "current", label: "What you have so far (optional)", placeholder: "0" },
      earnings.allowed ? { name: "source", label: "Count what my apps earn", type: "checkbox", hint: `Paid invoices, orders, bookings, tickets, courses and video earnings count by themselves from today.${earnings.this_month && earnings.this_month.cents ? ` This month so far: ${S.money(earnings.this_month.cents, earnings.currency)}.` : ""}` } : null,
      { name: "target_date", label: "Target date", type: "date", hint: "Optional. Monosage tells you when you fall behind." },
      { name: "description", label: "Why it matters", type: "textarea", rows: 3 },
      editing ? { name: "status", label: "Status", type: "select", options: STATUSES } : { name: "steps", label: "Steps (one per line)", type: "textarea", rows: 4, hint: "Progress follows the steps you tick. Leave empty to set progress yourself." },
    ].filter(Boolean);
    const form = S.form(fields, values);
    // Money goals belong to "Money" unless the person picked another area themselves.
    let areaPicked = editing;
    form.inputs.category.control.addEventListener("change", () => { areaPicked = true; });
    form.inputs.measure.control.addEventListener("change", () => {
      if (!areaPicked && form.inputs.measure.control.value === "money") form.inputs.category.control.value = "money";
    });
    const sync = () => {
      const measure = form.inputs.measure.control.value;
      fieldOf(form, "unit").hidden = measure !== "number";
      fieldOf(form, "target").hidden = measure === "percent";
      const source = form.inputs.source ? form.inputs.source.control.checked && measure === "money" : false;
      if (form.inputs.current) fieldOf(form, "current").hidden = measure === "percent" || source;
      if (form.inputs.source) fieldOf(form, "source").hidden = measure !== "money";
      if (form.inputs.steps) fieldOf(form, "steps").hidden = measure !== "percent";
      form.inputs.target.control.placeholder = measure === "money" ? "50000" : "100";
    };
    form.inputs.measure.control.addEventListener("change", sync);
    if (form.inputs.source) form.inputs.source.control.addEventListener("change", sync);
    if (editing && goal.steps.length) form.inputs.measure.control.disabled = true;
    let ideasRow = null;
    if (form.inputs.steps) {
      const note = h("span", { class: "muted small", role: "status" });
      const ideas = S.button("Suggest steps", async () => {
        const read = form.read();
        if (!read.title) { form.setError("Enter the goal first, then ask for steps."); form.inputs.title.control.focus(); return; }
        form.setError("");
        note.textContent = "Thinking of steps...";
        try {
          const result = await api("/goals/suggest", { method: "POST", body: { title: read.title, category: read.category, description: read.description } });
          const box = form.inputs.steps.control;
          const have = box.value.split("\n").map((line) => line.trim()).filter(Boolean);
          const fresh = result.steps.filter((step) => !have.some((line) => line.toLowerCase() === step.toLowerCase()));
          box.value = [...have, ...fresh].join("\n");
          note.textContent = result.source === "model" ? "Steps from Monosage's writing helper. Change them as you like." : "Ideas to start from. Change them as you like.";
        } catch (error) { note.textContent = ""; form.setError(error.message); }
      }, { kind: "secondary sm" });
      ideasRow = h("div", { class: "btn-row jr-ideas" }, ideas, note);
      fieldOf(form, "steps").after(ideasRow);
    }
    const syncIdeas = () => { if (ideasRow) ideasRow.hidden = form.inputs.measure.control.value !== "percent"; };
    form.inputs.measure.control.addEventListener("change", syncIdeas);
    sync();
    syncIdeas();
    Aira.modal({
      title: editing ? "Edit goal" : "New goal",
      wide: true,
      body: h("form", { onsubmit: (event) => event.preventDefault() }, form.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: editing ? "Save" : "Add goal", kind: "primary", onClick: async () => {
          form.setError("");
          const read = form.read();
          if (!read.title) { form.setError("Enter the goal."); form.inputs.title.control.focus(); return false; }
          const body = { title: read.title, category: read.category, measure: read.measure, target_date: read.target_date, description: read.description, source: read.measure === "money" && read.source ? "earnings" : "manual" };
          if (read.measure !== "percent") {
            if (!read.target) { form.setError("Enter the target."); form.inputs.target.control.focus(); return false; }
            body.target = read.target;
            if (read.measure === "number") body.unit = read.unit;
            if (read.measure === "money") body.currency = values.currency || business.currency;
            if (!editing && read.current && body.source === "manual") body.current = read.current;
          }
          if (editing) body.status = read.status;
          else if (read.measure === "percent") body.steps = (read.steps || "").split("\n").map((s) => s.trim()).filter(Boolean);
          try {
            await api(editing ? `/goals/${goal.id}` : "/goals", { method: editing ? "PATCH" : "POST", body });
          } catch (error) { form.setError(error.message); return false; }
          Aira.toast(editing ? "Goal saved." : "Goal added.", "success");
          rerender();
          return true;
        } },
      ],
    });
  }
  const newGoal = (rerender, preset) => goalForm(null, rerender, preset);

  async function checkIn(goal, rerender) {
    let fields;
    let title = `Update progress: ${goal.title}`;
    let submitLabel = "Save progress";
    if (earning(goal) || (!measured(goal) && goal.steps.length)) {
      title = `Add a note: ${goal.title}`;
      submitLabel = "Save note";
      fields = [{ name: "note", label: earning(goal) ? "How is it going? (your earnings count by themselves)" : "How is it going? (progress follows your steps)", type: "textarea", rows: 3, required: true }];
    } else if (measured(goal)) {
      title = `${goal.measure === "money" ? "Add money" : "Add to"}: ${goal.title}`;
      submitLabel = "Add";
      fields = [
        { name: "amount", label: goal.measure === "money" ? `Amount to add (${goal.currency})` : `How much to add${goal.unit ? ` (${goal.unit})` : ""}`, required: true, placeholder: "10", hint: `So far: ${amount(goal, goal.current_amount)} of ${amount(goal, goal.target_amount)}. Use a minus sign to take some out.` },
        { name: "note", label: "Note (optional)", type: "textarea", rows: 2 },
      ];
    } else {
      fields = [
        { name: "progress", label: "Progress (0 to 100%)", type: "number", min: 0, max: 100, step: 5, required: true },
        { name: "note", label: "What happened? (optional)", type: "textarea", rows: 2 },
      ];
    }
    await S.edit({
      title,
      fields,
      values: { progress: goal.progress },
      submitLabel,
      onSave: async (values) => {
        const result = await api(`/goals/${goal.id}/checkin`, { method: "POST", body: values });
        Aira.toast(result.goal.status === "completed" && goal.status !== "completed" ? "Goal reached. Well done! It is on your timeline." : "Saved.", "success");
        rerender();
      },
    });
  }
  async function setStatus(goal, status, rerender) {
    const result = await api(`/goals/${goal.id}`, { method: "PATCH", body: { status } });
    if (result.goal.status === "completed") Aira.toast("Goal reached. Well done! It is on your timeline.", "success");
    rerender();
  }

  function amountLine(goal) {
    if (!measured(goal)) return null;
    return h("p", { class: "jr-amount" }, h("strong", { text: amount(goal, goal.current_amount) }), h("span", { class: "muted", text: ` of ${amount(goal, goal.target_amount)}${earning(goal) ? " earned by your apps" : ""}` }));
  }

  // Supporters: people back the goal from its public page; the owner says how to pay and marks pledges received.
  function supportSection(current, update) {
    const totals = current.pledges || { supporters: 0, promised: 0, received: 0, currency: "" };
    const currency = totals.currency || current.currency || undefined;
    const link = h("input", { type: "url", value: current.pay_link || "", placeholder: "https://paypal.me/yourname", maxlength: 500 });
    const note = h("textarea", { rows: 2, maxlength: 300, placeholder: "UPI: name@bank, or bank details" });
    note.value = current.pay_note || "";
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const save = (support) => async () => {
      error.hidden = true;
      try {
        update((await api(`/goals/${current.id}/support`, { method: "PATCH", body: { support, pay_link: link.value.trim(), pay_note: note.value.trim() } })).goal);
        Aira.toast(support ? (current.support ? "Saved." : "Supporters can now back this goal from its page. Share the link.") : "This goal no longer takes support.", "success");
      } catch (err) { error.textContent = err.message; error.hidden = false; }
    };
    const settings = h("div", { class: "jr-support-form" },
      Aira.ui.field("Payment link (optional)", link, "Your own page to be paid: PayPal.me, a UPI or Stripe link. Monosage never takes the money."),
      Aira.ui.field("How to pay you (optional)", note, "Shown on the goal's page, for example bank details or \"cash when we meet\"."),
      error,
      h("div", { class: "btn-row" }, current.support
        ? [S.button("Save", save(true), { kind: "secondary sm" }), S.button("Stop taking support", save(false), { kind: "ghost sm" })]
        : [S.button("Let people back this goal", save(true), { kind: "secondary sm" })]));
    const pledges = (current.pledge_list || []).length ? h("ul", { class: "life-lines" }, ...current.pledge_list.map((pledge) => {
      const act = (label, status, kind = "ghost sm") => S.button(label, async () => { update((await api(`/pledges/${pledge.id}`, { method: "PATCH", body: { status } })).goal); }, { kind });
      return h("li", { class: "life-line jr-pledge" },
        h("div", { class: "jr-grow" },
          h("span", { class: "life-line-text", text: `${pledge.name} · ${S.money(pledge.amount, pledge.currency)}` }),
          h("span", { class: "muted life-line-sub", text: [pledge.message, Aira.fmt.ago(pledge.at)].filter(Boolean).join(" · ") })),
        statePill(pledge.status, PLEDGE_STATES),
        h("div", { class: "btn-row" },
          pledge.status === "promised" ? act("Mark received", "received", "secondary sm") : null,
          pledge.status === "promised" ? act("Cancel", "cancelled") : null,
          pledge.status !== "promised" ? act(pledge.status === "received" ? "Not received" : "Promised again", "promised") : null,
          S.button("Delete", async () => { if (await S.confirmDelete(`${pledge.name}'s pledge`)) { await api(`/pledges/${pledge.id}`, { method: "DELETE" }); update((await api(`/goals/${current.id}`)).goal); } }, { kind: "ghost sm" })));
    })) : h("p", { class: "muted", text: current.support ? "Nobody has backed this goal yet. Share its page with family and friends." : "" });
    return [
      h("h3", { class: "suite-h3", text: "Supporters" }),
      h("p", { class: "muted small", text: measured(current) && current.measure === "money" && !earning(current)
        ? "Family and friends can back this goal from its page with an amount. They pay you through your own link; when the money arrives, mark it received and it is added to this goal."
        : "Family and friends can back this goal from its page with an amount (a sponsored run, a course, a cause). They pay you through your own link; mark each pledge received when the money arrives." }),
      totals.supporters ? h("p", { class: "jr-amount" }, h("strong", { text: plural(totals.supporters, "supporter", "supporters") }), h("span", { class: "muted", text: ` · ${S.money(totals.received, currency)} received · ${S.money(totals.promised, currency)} still promised` })) : null,
      settings,
      current.support || (current.pledge_list || []).length ? pledges : null,
    ];
  }

  // Redrawing a dialog's content removes the focused button and focus falls to <body> (Escape and Tab then
  // start from the top of the page). keepFocus(container) remembers what was focused inside it and, after a
  // redraw, focuses the same control again (by its words, else the one in the same place, else the container).
  const FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']";
  function keepFocus(container) {
    let last = null;
    const keyOf = (node) => `${node.tagName}|${node.getAttribute("aria-label") || node.textContent.trim().slice(0, 80)}`;
    container.addEventListener("focusin", (event) => {
      const all = [...container.querySelectorAll(FOCUSABLE)];
      last = { key: keyOf(event.target), index: all.indexOf(event.target) };
    });
    if (!container.hasAttribute("tabindex")) container.setAttribute("tabindex", "-1");
    return () => {
      const active = document.activeElement;
      if (!last || (active && active !== document.body && active.isConnected)) return;
      const all = [...container.querySelectorAll(FOCUSABLE)];
      const target = all.find((node) => keyOf(node) === last.key) || all[Math.min(last.index, all.length - 1)] || container;
      target.focus({ preventScroll: true });
    };
  }

  async function goalDetail(summary, rerender) {
    let current = (await api(`/goals/${summary.id}`)).goal;
    const body = h("div", { class: "suite-body" });
    const restoreFocus = keepFocus(body);
    let ideas = [];
    let ideasNote = "";
    const draw = () => {
      const canStep = !measured(current);
      const stepInput = h("input", { type: "text", placeholder: "Add a step", "aria-label": "Add a step", maxlength: 160, class: "jr-grow" });
      const addStep = async () => {
        const title = stepInput.value.trim();
        if (!title) { stepInput.focus(); return; }
        current = { ...current, ...(await api(`/goals/${current.id}/steps`, { method: "POST", body: { title } })).goal };
        draw();
        rerender();
        body.querySelector("input[aria-label='Add a step']")?.focus();
      };
      stepInput.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addStep(); } });
      const addIdeas = async (list) => {
        for (const title of list) current = { ...current, ...(await api(`/goals/${current.id}/steps`, { method: "POST", body: { title } })).goal };
        ideas = ideas.filter((idea) => !list.includes(idea));
        draw();
        rerender();
      };
      const suggest = async () => {
        const result = await api("/goals/suggest", { method: "POST", body: { title: current.title, category: current.category, description: current.description } });
        ideas = result.steps.filter((idea) => !current.steps.some((step) => step.title.toLowerCase() === idea.toLowerCase()));
        ideasNote = result.source === "model" ? "Steps from Monosage's writing helper." : "Ideas to start from.";
        if (!ideas.length) Aira.toast("Your steps already cover these ideas. Add your own below.", "info");
        draw();
      };
      const ideaList = ideas.length ? h("div", { class: "jr-idea-box" },
        h("p", { class: "muted small", text: `${ideasNote} Add the ones you like.` }),
        h("ul", { class: "life-lines" }, ...ideas.map((idea) => h("li", { class: "life-line" }, h("span", { class: "life-line-text jr-grow", text: idea }), S.button("Add", () => addIdeas([idea]), { kind: "secondary sm" })))),
        h("div", { class: "btn-row" }, S.button("Add all", () => addIdeas([...ideas]), { kind: "secondary sm" }), S.button("No thanks", () => { ideas = []; draw(); }, { kind: "ghost sm" }))) : null;
      const steps = current.steps.length ? h("ul", { class: "life-lines" }, ...current.steps.map((step) => h("li", { class: `life-line ${step.done ? "done" : ""}` },
        h("button", { type: "button", class: `life-check ${step.done ? "done" : ""}`, "aria-pressed": String(step.done), "aria-label": `${step.done ? "Not done" : "Done"}: ${step.title}`, onclick: async (event) => {
          await Aira.withBusy(event.currentTarget, async () => { current = { ...current, ...(await api(`/steps/${step.id}`, { method: "PATCH", body: { done: !step.done } })).goal }; draw(); rerender(); });
        } }, h("span", { "aria-hidden": "true", text: step.done ? "✓" : "" }), h("span", { class: "life-check-label", text: step.done ? "Done" : "Do it" })),
        h("span", { class: "life-line-text jr-grow", text: step.title }),
        S.button("Remove", async () => { current = { ...current, ...(await api(`/steps/${step.id}`, { method: "DELETE" })).goal }; draw(); rerender(); }, { kind: "ghost sm" })))) : h("p", { class: "muted", text: "No steps yet. Break the goal into small steps and tick them off." });
      const earned = current.earnings && current.earnings.by_app.length
        ? h("ul", { class: "life-lines" }, ...current.earnings.by_app.map((app) => h("li", { class: "life-line" }, h("span", { class: "life-line-text jr-grow", text: app.name }), h("strong", { text: S.money(app.cents, current.currency) }))))
        : h("p", { class: "muted", text: "Nothing earned yet since this goal started. Paid invoices, orders, bookings, tickets, courses and video earnings count here by themselves." });
      const cheers = (current.cheers || []).length ? h("ul", { class: "life-lines" }, ...current.cheers.map((cheer) => h("li", { class: "life-line jr-cheer" },
        h("span", { class: "jr-mark", "aria-hidden": "true", text: "👏" }),
        h("div", { class: "jr-grow" }, h("span", { class: "life-line-text", text: cheer.message }), h("span", { class: "muted life-line-sub", text: `${cheer.name} · ${Aira.fmt.ago(cheer.at)}` })),
        S.button("Remove", async () => { await api(`/cheers/${cheer.id}`, { method: "DELETE" }); current = { ...current, cheers: current.cheers.filter((c) => c.id !== cheer.id) }; draw(); }, { kind: "ghost sm" })))) : h("p", { class: "muted", text: "No cheers yet. Share the link with someone who will cheer you on." });
      // replaceChildren() writes a null as the text "null": leave the missing parts out first.
      body.replaceChildren(...[
        h("div", { class: "jr-goal-head" }, statePill(current.state, STATES), h("span", { class: "muted", text: `${CATEGORY[current.category] || current.category}${current.target_date ? ` · Target ${S.day(current.target_date)} (${inDays(current.days_left)})` : ""}` })),
        current.description ? h("p", { text: current.description }) : null,
        h("div", { class: "jr-progress-row" }, progressBar(current.progress, current.title), h("strong", { text: `${current.progress}%` })),
        amountLine(current),
        earning(current) ? h("h3", { class: "suite-h3", text: `Earned since ${S.day(current.start_date)}` }) : null,
        earning(current) ? earned : null,
        canStep ? h("h3", { class: "suite-h3", text: `Steps (${current.steps_done} of ${current.steps.length})` }) : null,
        canStep ? steps : null,
        canStep ? h("div", { class: "btn-row" }, stepInput, S.button("Add step", addStep, { kind: "secondary" }), S.button("Suggest steps", suggest, { kind: "ghost" })) : null,
        canStep ? ideaList : null,
        h("h3", { class: "suite-h3", text: "Check-ins" }),
        current.checkins.length ? h("ul", { class: "life-lines" }, ...current.checkins.map((c) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: [measured(current) && c.amount !== null && c.amount !== undefined ? `${c.amount >= 0 ? "+" : "-"}${amount(current, Math.abs(c.amount))}` : `${c.progress}%`, c.note].filter(Boolean).join(" · ") }), h("span", { class: "muted life-line-sub", text: Aira.fmt.ago(c.at) }))))) : h("p", { class: "muted", text: measured(current) && !earning(current) ? "Use Add to record what you did." : "Use the button below to record how it is going." }),
        current.can_share ? h("h3", { class: "suite-h3", text: "Cheer page" }) : null,
        current.can_share ? S.share({
          title: "Share this goal",
          text: "Family and friends with the link see the goal, its progress and steps (never your notes, other goals or amounts of money) and can send you a cheer.",
          link: current.link,
          onCreate: async () => { const link = (await api(`/goals/${current.id}/link`, { method: "POST", body: {} })).link; current = { ...current, link }; rerender(); return link; },
          onRevoke: async () => {
            const result = await api(`/goals/${current.id}/link`, { method: "DELETE" });
            current = result.goal || { ...current, link: null };
            if (result.support_stopped) Aira.toast("The link is off, so people can no longer back this goal. Turn supporters on again to make a new link.", "info");
            draw();
            rerender();
          },
        }) : null,
        current.can_share ? h("h3", { class: "suite-h3", text: `Cheers (${(current.cheers || []).length})` }) : null,
        current.can_share ? cheers : null,
        ...(current.can_support || current.support ? supportSection(current, (goal) => { current = goal; draw(); rerender(); }) : []),
      ].filter(Boolean));
      restoreFocus();
    };
    draw();
    const label = earning(current) || (!measured(current) && current.steps.length) ? "Add a note" : measured(current) ? (current.measure === "money" ? "Add money" : "Add") : "Update progress";
    Aira.modal({ title: current.title, wide: true, body, actions: [current.status === "dropped" ? null : { label, kind: "secondary", onClick: () => { checkIn(current, rerender); } }, { label: "Close", kind: "primary" }].filter(Boolean) });
  }

  function goalCard(goal, rerender) {
    const meta = [CATEGORY[goal.category] || goal.category, goal.target_date ? `Target ${S.day(goal.target_date)}` : "", goal.status === "active" && goal.days_left !== null ? inDays(goal.days_left) : "", goal.steps.length ? `${goal.steps_done}/${goal.steps.length} steps` : "", goal.shared ? "Shared" : "", goal.cheers_unread ? `${goal.cheers_unread} new ${goal.cheers_unread === 1 ? "cheer" : "cheers"}` : "", goal.pledges && goal.pledges.supporters ? plural(goal.pledges.supporters, "supporter", "supporters") : "", goal.pledges && goal.pledges.unread ? `${goal.pledges.unread} new` : ""].filter(Boolean).join(" · ");
    const quick = goal.status !== "active" || earning(goal) || (!measured(goal) && goal.steps.length) ? null
      : S.button(measured(goal) ? (goal.measure === "money" ? "Add money" : "Add") : "Update progress", () => checkIn(goal, rerender), { kind: "sm" });
    const actions = h("div", { class: "btn-row" },
      quick,
      S.button(goal.steps.length ? "Steps and details" : "Details", () => goalDetail(goal, rerender), { kind: `${quick ? "secondary" : ""} sm` }),
      S.button("Edit", () => goalForm(goal, rerender), { kind: "ghost sm" }),
      goal.status === "active" && !earning(goal) ? S.button("Mark done", () => setStatus(goal, "completed", rerender), { kind: "ghost sm" }) : null,
      goal.status === "active" ? S.button("Pause", () => setStatus(goal, "paused", rerender), { kind: "ghost sm" }) : null,
      goal.status !== "active" ? S.button("Make active", () => setStatus(goal, "active", rerender), { kind: "ghost sm" }) : null,
      S.button("Delete", async () => { if (await S.confirmDelete(`"${goal.title}"`)) { await api(`/goals/${goal.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }));
    return h("article", { class: "card jr-card" },
      h("div", { class: "jr-card-head" }, h("h3", { text: goal.title }), statePill(goal.state, STATES)),
      goal.description ? h("p", { class: "muted jr-clip", text: goal.description }) : null,
      h("div", { class: "jr-progress-row" }, progressBar(goal.progress, goal.title), h("strong", { text: `${goal.progress}%` })),
      amountLine(goal),
      h("p", { class: "muted small", text: meta }),
      actions);
  }

  async function goalsTab(body, route, rerender) {
    const q = route.query;
    // "all" (not an empty value, which the address drops) shows every goal; no status at all means the active ones.
    const status = STATUSES.some(([value]) => value === q.status) || q.status === "all" ? q.status : "active";
    const params = new URLSearchParams();
    if (status !== "all") params.set("status", status);
    if (q.category) params.set("category", q.category);
    if (q.q) params.set("q", q.q);
    const data = await api(`/goals?${params}`);
    const go = (changes) => Aira.router.go(["journey"], { ...q, tab: "goals", ...changes });
    const statusSelect = Aira.ui.select([["all", "All goals"], ...STATUSES], status, { "aria-label": "Status" });
    statusSelect.addEventListener("change", () => go({ status: statusSelect.value }));
    const categorySelect = Aira.ui.select([["", "Every area"], ...CATEGORIES], q.category || "", { "aria-label": "Area of life" });
    categorySelect.addEventListener("change", () => go({ category: categorySelect.value }));
    const search = h("input", { type: "search", placeholder: "Search goals", "aria-label": "Search goals", value: q.q || "" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    const filtered = !(status === "active" && !q.category && !q.q);
    put(body,
      h("div", { class: "btn-row jr-filters" }, statusSelect, categorySelect, search, S.button("New goal", () => newGoal(rerender)), S.button("Export CSV", () => Aira.download("/api/journey/export/goals", "goals.csv"), { kind: "ghost" })),
      data.goals.length ? h("div", { class: "jr-cards" }, ...data.goals.map((goal) => goalCard(goal, rerender)))
        : Aira.ui.empty(filtered ? "No goals match" : "No goals yet", filtered ? "Try another status, area or search." : "A goal can be big or small: learn to swim, run 100 km, save for a trip, earn from a side business.", filtered ? S.button("Show all goals", () => go({ status: "all", category: "", q: "" }), { kind: "secondary" }) : S.button("Add your first goal", () => newGoal(rerender))));
    if (/^[0-9a-f]{32}$/.test(q.goal || "") && opened !== `goal:${q.goal}`) {
      opened = `goal:${q.goal}`;
      goalDetail({ id: q.goal }, rerender).catch((error) => Aira.reportError(error));
    }
  }

  // ---------------------------------------------------------------- habits
  async function habitForm(habit, rerender, title = "") {
    const goals = (await api("/goals?status=active")).goals.filter((g) => measured(g) && !earning(g));
    const options = [["", "No goal"], ...goals.map((g) => [g.id, g.title])];
    if (habit && habit.goal_id && !goals.some((g) => g.id === habit.goal_id)) options.push([habit.goal_id, habit.goal_title]);
    const form = S.form([
      { name: "title", label: "Habit", required: true, placeholder: "Walk 30 minutes" },
      { name: "per_week", label: "How often", type: "select", options: PER_WEEK },
      { name: "goal_id", label: "Helps a goal (optional)", type: "select", options, hint: goals.length ? "" : "Make a goal measured in a number or money to link it here." },
      { name: "amount", label: "Add this much to the goal each time", placeholder: "5", hint: "For example: save 5 each day, run 3 km." },
    ], habit ? { ...habit, per_week: String(habit.per_week), amount: habit.amount ? plain(habit.amount) : "" } : { title, per_week: "7" });
    const sync = () => { fieldOf(form, "amount").hidden = !form.inputs.goal_id.control.value; };
    form.inputs.goal_id.control.addEventListener("change", sync);
    sync();
    Aira.modal({
      title: habit ? "Edit habit" : "New habit",
      body: h("form", { onsubmit: (event) => event.preventDefault() }, form.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: habit ? "Save" : "Add habit", kind: "primary", onClick: async () => {
          form.setError("");
          const read = form.read();
          if (!read.title) { form.setError("Enter the habit."); form.inputs.title.control.focus(); return false; }
          const body = { title: read.title, per_week: Number(read.per_week), goal_id: read.goal_id, amount: read.goal_id ? read.amount || "0" : "0" };
          try { await api(habit ? `/habits/${habit.id}` : "/habits", { method: habit ? "PATCH" : "POST", body }); } catch (error) { form.setError(error.message); return false; }
          Aira.toast(habit ? "Habit saved." : "Habit added. Tick it here or on Today.", "success");
          rerender();
          return true;
        } },
      ],
    });
  }

  function streakText(habit) {
    if (!habit.streak) return habit.every_day ? "No streak yet" : "No weeks in a row yet";
    return `${habit.every_day ? plural(habit.streak, "day", "days") : plural(habit.streak, "week", "weeks")} in a row`;
  }

  function habitCard(habit, rerender) {
    const fmt = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    const grid = h("ol", { class: "jr-days", "aria-label": "The last four weeks" }, ...habit.days.map((d) => h("li", { class: `jr-day ${d.done ? "done" : ""}`, title: `${fmt(d.day)}: ${d.done ? "done" : "not done"}`, "aria-label": `${fmt(d.day)}: ${d.done ? "done" : "not done"}` })));
    const toggle = h("button", { type: "button", class: `life-check ${habit.done_today ? "done" : ""}`, "aria-pressed": String(habit.done_today), "aria-label": `${habit.done_today ? "Not done today" : "Done today"}: ${habit.title}` },
      h("span", { "aria-hidden": "true", text: habit.done_today ? "✓" : "" }), h("span", { class: "life-check-label", text: habit.done_today ? "Done today" : "Done" }));
    toggle.addEventListener("click", () => Aira.withBusy(toggle, async () => {
      const result = await api(`/habits/${habit.id}/${habit.done_today ? "undo" : "done"}`, { method: "POST", body: {} });
      if (!habit.done_today && result.habit.streak > 1 && [3, 7, 14, 21, 30, 50, 100, 365].includes(result.habit.streak)) Aira.toast(`${streakText(result.habit)}. Keep going!`, "success");
      rerender();
    }));
    const yesterday = new Date(Date.now() - 86400000);
    const yesterdayIso = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, "0")}-${String(yesterday.getDate()).padStart(2, "0")}`;
    const missedYesterday = habit.started_on <= yesterdayIso && habit.days.length > 1 && !habit.days[habit.days.length - 2].done && habit.days[habit.days.length - 2].day === yesterdayIso;
    return h("article", { class: "card jr-card jr-habit", dataset: { jrOpen: `habit:${habit.id}` } },
      h("div", { class: "jr-card-head" }, h("h3", { text: habit.title }), h("span", { class: `suite-pill as-written ${habit.streak ? "good" : ""}`, text: habit.streak ? `🔥 ${streakText(habit)}` : streakText(habit) })),
      h("p", { class: "muted small", text: [PER_WEEK.find(([v]) => Number(v) === habit.per_week)?.[1], habit.every_day ? "" : `${habit.this_week} of ${habit.per_week} this week`, habit.goal_title ? (habit.amount_label ? `Adds ${habit.amount_label} to "${habit.goal_title}"` : `Helps "${habit.goal_title}"`) : ""].filter(Boolean).join(" · ") }),
      grid,
      h("p", { class: "muted small", text: `Best: ${habit.every_day ? plural(habit.best_streak, "day", "days") : plural(habit.best_streak, "week", "weeks")} · ${habit.rate}% of the last 30 days · ${habit.total === 1 ? "once" : `${habit.total} times`} in all` }),
      h("div", { class: "btn-row" }, toggle,
        missedYesterday ? S.button("Done yesterday", async () => { await api(`/habits/${habit.id}/done`, { method: "POST", body: { day: yesterdayIso } }); rerender(); }, { kind: "secondary sm" }) : null,
        S.button("Edit", () => habitForm(habit, rerender), { kind: "ghost sm" }),
        S.button("Stop", async () => { await api(`/habits/${habit.id}`, { method: "PATCH", body: { archived: true } }); Aira.toast("Habit stopped. Its history is kept.", "success"); rerender(); }, { kind: "ghost sm" }),
        S.button("Delete", async () => { if (await S.confirmDelete(`"${habit.title}" and its history`)) { await api(`/habits/${habit.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })));
  }

  async function habitsTab(body, rerender) {
    const data = await api("/habits");
    const ideas = h("div", { class: "jr-ideas" }, h("span", { class: "muted small", text: "Ideas:" }), ...HABIT_IDEAS.filter((idea) => !data.habits.some((habit) => habit.title === idea)).slice(0, 4).map((idea) => S.button(idea, () => habitForm(null, rerender, idea), { kind: "ghost sm" })));
    const done = data.habits.filter((habit) => habit.done_today).length;
    put(body,
      data.habits.length ? S.kpis([
        { label: "Done today", value: `${done} of ${data.habits.length}`, tone: done === data.habits.length ? "good" : "" },
        { label: "Longest streak now", value: Math.max(0, ...data.habits.map((habit) => habit.streak)), hint: "Days or weeks in a row" },
        { label: "Last 30 days", value: `${Math.round(data.habits.reduce((sum, habit) => sum + habit.rate, 0) / data.habits.length)}%`, hint: "Of what you planned" },
      ]) : null,
      h("div", { class: "btn-row jr-filters" }, S.button("New habit", () => habitForm(null, rerender))),
      data.habits.length ? h("div", { class: "jr-cards" }, ...data.habits.map((habit) => habitCard(habit, rerender)))
        : Aira.ui.empty("No habits yet", "Small things you do often: walk, read, save a little, call your parents. Tick them off here or on Today and watch your streak grow.", S.button("Add your first habit", () => habitForm(null, rerender))),
      ideas.children.length > 1 ? ideas : null,
      data.stopped.length ? S.section("Stopped habits", h("ul", { class: "life-lines" }, ...data.stopped.map((habit) => h("li", { class: "life-line", dataset: { jrOpen: `habit:${habit.id}` } },
        h("div", { class: "jr-grow" }, h("span", { class: "life-line-text", text: habit.title }), h("span", { class: "muted life-line-sub", text: `${habit.total === 1 ? "Once" : `${habit.total} times`} in all · best ${habit.every_day ? plural(habit.best_streak, "day", "days") : plural(habit.best_streak, "week", "weeks")} in a row` })),
        S.button("Start again", async () => { await api(`/habits/${habit.id}`, { method: "PATCH", body: { archived: false } }); rerender(); }, { kind: "secondary sm" }),
        S.button("Delete", async () => { if (await S.confirmDelete(`"${habit.title}" and its history`)) { await api(`/habits/${habit.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }))))) : null);
  }

  // ---------------------------------------------------------------- timeline
  const eventFields = () => [
    { name: "title", label: "What happened", required: true, placeholder: "Started my first job" },
    { name: "event_date", label: "Date", type: "date", required: true },
    { name: "kind", label: "Kind", type: "select", options: KINDS },
    { name: "location", label: "Where (optional)", placeholder: "Hyderabad" },
    { name: "description", label: "Story (optional)", type: "textarea", rows: 3 },
    { name: "shared", label: "Share with my household", type: "checkbox", hint: "They can see it on their timeline. Nobody else can." },
  ];
  async function editEvent(event, rerender) {
    const now = new Date();
    const local = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    await S.edit({
      title: event ? "Edit event" : "Add to your timeline",
      fields: eventFields(),
      values: event || { kind: "life", event_date: local },
      submitLabel: event ? "Save" : "Add event",
      onSave: async (values) => {
        await api(event ? `/events/${event.id}` : "/events", { method: event ? "PATCH" : "POST", body: values });
        rerender();
      },
    });
  }

  async function timelineTab(body, route, rerender) {
    const q = route.query;
    const params = new URLSearchParams();
    if (q.kind) params.set("kind", q.kind);
    if (q.year) params.set("year", q.year);
    if (q.family === "1") params.set("family", "1");
    if (q.q) params.set("q", q.q);
    const data = await api(`/events?${params}`);
    const go = (changes) => Aira.router.go(["journey"], { ...q, tab: "timeline", ...changes });
    const kindSelect = Aira.ui.select([["", "Every kind"], ...KINDS], q.kind || "", { "aria-label": "Kind" });
    kindSelect.addEventListener("change", () => go({ kind: kindSelect.value }));
    const years = [...new Set([...(data.years || []), ...(q.year ? [q.year] : [])])];
    const yearSelect = Aira.ui.select([["", "Every year"], ...years.map((y) => [y, y])], q.year || "", { "aria-label": "Year" });
    yearSelect.addEventListener("change", () => go({ year: yearSelect.value }));
    const family = h("input", { type: "checkbox", checked: q.family === "1" });
    family.addEventListener("change", () => go({ family: family.checked ? "1" : "" }));
    const search = h("input", { type: "search", placeholder: "Search your story", "aria-label": "Search your timeline", value: q.q || "" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    put(body, h("div", { class: "btn-row jr-filters" }, kindSelect, years.length > 1 || q.year ? yearSelect : null, search, h("label", { class: "suite-check" }, family, h("span", { text: "Include what my household shared" })), S.button("Add event", () => editEvent(null, rerender)), S.button("Export CSV", () => Aira.download("/api/journey/export/events", "timeline.csv"), { kind: "ghost" })));
    if (!data.events.length) {
      const filtered = q.kind || q.q || q.year;
      body.append(Aira.ui.empty(filtered ? "Nothing matches" : "Your timeline is empty", filtered ? "Try another kind, year or search." : "Add the moments that shaped you: school, first job, moves, weddings, births, trips. Goals you reach appear here too.", filtered ? S.button("Show everything", () => go({ kind: "", q: "", year: "" }), { kind: "secondary" }) : S.button("Add your first event", () => editEvent(null, rerender))));
      return;
    }
    const byYear = new Map();
    for (const event of data.events) {
      const year = event.event_date.slice(0, 4);
      if (!byYear.has(year)) byYear.set(year, []);
      byYear.get(year).push(event);
    }
    const list = h("div", { class: "jr-timeline" });
    for (const [year, events] of byYear) {
      list.append(h("h2", { class: "jr-year", text: year }));
      list.append(h("ol", { class: "jr-events" }, ...events.map((event) => h("li", { class: "jr-event", dataset: { jrOpen: `event:${event.id}` } },
        h("span", { class: "jr-dot", "aria-hidden": "true", text: KIND_MARK[event.kind] || "•" }),
        h("div", { class: "card jr-event-card" },
          h("div", { class: "jr-card-head" }, h("h3", { text: event.title }), h("span", { class: "suite-pill info", text: KIND[event.kind] || event.kind })),
          event.description ? h("p", { text: event.description }) : null,
          h("p", { class: "muted small", text: [S.day(event.event_date), event.location, event.shared ? "Shared with household" : "Private", event.mine ? "" : `Added by ${event.owner_name}`].filter(Boolean).join(" · ") }),
          event.mine ? h("div", { class: "btn-row" }, S.button("Edit", () => editEvent(event, rerender), { kind: "ghost sm" }), S.button("Delete", async () => { if (await S.confirmDelete(`"${event.title}"`)) { await api(`/events/${event.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })) : null)))));
    }
    body.append(list);
  }

  // ---------------------------------------------------------------- documents
  function documentForm(doc, rerender) {
    const form = S.form([
      { name: "title", label: "Name", required: true, placeholder: "My passport" },
      { name: "kind", label: "Kind", type: "select", options: DOC_KINDS },
      { name: "issuer", label: "Issued by (optional)", placeholder: "Passport Office" },
      { name: "number", label: "Document number (optional)", type: "password", hint: doc && doc.number_hint ? `Saved: ${doc.number_hint}. Leave empty to keep it.` : "Kept encrypted. Only you can see it." },
      doc && doc.number_hint ? { name: "remove_number", label: "Remove the saved number", type: "checkbox" } : null,
      { name: "issued_on", label: "Issued on", type: "date" },
      { name: "expires_on", label: "Expires on", type: "date", hint: "Monosage reminds you 30 days, 7 days and 1 day before." },
      { name: "tags", label: "Tags (comma separated)", placeholder: "travel, identity" },
      { name: "notes", label: "Notes", type: "textarea", rows: 2 },
      doc && doc.file ? { name: "remove_file", label: `Remove the scan (${doc.file.name})`, type: "checkbox" } : null,
    ].filter(Boolean), doc ? { ...doc, number: "", tags: (doc.tags || []).join(", ") } : { kind: "passport" });
    const file = h("input", { type: "file", accept: "application/pdf,image/png,image/jpeg,image/webp,image/gif,text/plain,.txt" });
    form.node.insertBefore(Aira.ui.field(doc && doc.file ? `Replace the scan (now: ${doc.file.name}, ${size(doc.file.size)})` : "Scan or photo (optional)", file, "PDF, photo or text file, up to 10 MB. Stored encrypted."), form.node.lastChild);
    Aira.modal({
      title: doc ? "Edit document" : "Add a document",
      wide: true,
      body: h("form", { onsubmit: (event) => event.preventDefault() }, form.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: doc ? "Save" : "Add document", kind: "primary", onClick: async () => {
          form.setError("");
          const values = form.read();
          if (!values.title) { form.setError("Enter a name for the document."); return false; }
          const body = { ...values, tags: values.tags.split(",").map((t) => t.trim()).filter(Boolean) };
          delete body.remove_number;
          if (values.remove_number) body.number = "";
          else if (doc && !values.number) delete body.number;
          const picked = file.files && file.files[0];
          if (picked) {
            if (picked.size > 10 * 1024 * 1024) { form.setError("A document can be at most 10 MB."); return false; }
            body.file = { name: picked.name, data: await Aira.fileToBase64(picked) };
            delete body.remove_file;
          }
          try {
            await api(doc ? `/documents/${doc.id}` : "/documents", { method: doc ? "PATCH" : "POST", body });
          } catch (error) { form.setError(error.message); return false; }
          Aira.toast(doc ? "Document saved." : "Document added to your vault.", "success");
          rerender();
          return true;
        } },
      ],
    });
  }

  async function openFile(doc, mode) {
    const response = await Aira.api(`/api/journey/documents/${doc.id}/file`, { raw: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (mode === "download") {
      const link = h("a", { href: url, download: doc.file.name });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
    return url;
  }

  async function documentDetail(summary, rerender) {
    const doc = (await api(`/documents/${summary.id}`)).document;
    const number = h("span", { class: "jr-number", text: doc.number ? "••••••••" : "Not saved" });
    // A plain button: the kit's busy wrapper puts the old label back after a click, so the label could not change.
    const reveal = doc.number ? h("button", { type: "button", class: "btn ghost sm", "aria-pressed": "false", text: "Show number" }) : null;
    if (reveal) {
      reveal.addEventListener("click", () => {
        const shown = reveal.getAttribute("aria-pressed") !== "true";
        number.textContent = shown ? doc.number : "••••••••";
        reveal.textContent = shown ? "Hide number" : "Show number";
        reveal.setAttribute("aria-pressed", String(shown));
      });
    }
    const preview = h("div", { class: "jr-preview" });
    let previewUrl = "";
    if (doc.file && doc.file.type.startsWith("image/")) {
      preview.append(Aira.ui.skeleton(1));
      openFile(doc).then((url) => { previewUrl = url; preview.replaceChildren(h("img", { src: url, alt: `Scan of ${doc.title}` })); }).catch((error) => preview.replaceChildren(Aira.ui.error(error)));
    }
    const rows = [
      ["Kind", DOC_KIND[doc.kind] || doc.kind],
      ["Issued by", doc.issuer],
      ["Issued on", doc.issued_on ? S.day(doc.issued_on) : ""],
      ["Expires on", doc.expires_on ? `${S.day(doc.expires_on)} (${inDays(doc.days_left)})` : "No expiry"],
      ["Tags", (doc.tags || []).join(", ")],
      ["Notes", doc.notes],
      ["File", doc.file ? `${doc.file.name} · ${size(doc.file.size)}` : "No file"],
    ].filter(([, value]) => value);
    Aira.modal({
      title: doc.title,
      wide: true,
      onClose: () => { if (previewUrl) URL.revokeObjectURL(previewUrl); },
      body: h("div", { class: "suite-body" },
        h("div", { class: "jr-card-head" }, statePill(doc.state, DOC_STATES)),
        h("dl", { class: "jr-facts" }, h("dt", { text: "Number" }), h("dd", {}, number, reveal), ...rows.flatMap(([label, value]) => [h("dt", { text: label }), h("dd", { text: value })])),
        preview),
      actions: [
        doc.file ? { label: "Download", kind: "secondary", close: false, onClick: () => openFile(doc, "download") } : null,
        { label: "Edit", kind: "ghost", onClick: () => { documentForm(doc, rerender); } },
        { label: "Close", kind: "primary" },
      ].filter(Boolean),
    });
  }

  async function documentsTab(body, route, rerender) {
    const q = route.query;
    const params = new URLSearchParams();
    if (q.kind) params.set("kind", q.kind);
    if (q.state) params.set("state", q.state);
    if (q.q) params.set("q", q.q);
    const [data, all] = await Promise.all([api(`/documents?${params}`), q.kind || q.state || q.q ? api("/documents") : null]);
    const everything = (all || data).documents;
    const go = (changes) => Aira.router.go(["journey"], { ...q, tab: "documents", ...changes });
    const kindSelect = Aira.ui.select([["", "Every kind"], ...DOC_KINDS], q.kind || "", { "aria-label": "Kind" });
    kindSelect.addEventListener("change", () => go({ kind: kindSelect.value }));
    const stateSelect = Aira.ui.select([["", "Any state"], ["expires_soon", "Expires soon"], ["expired", "Expired"], ["valid", "Valid"], ["no_expiry", "No expiry"]], q.state || "", { "aria-label": "State" });
    stateSelect.addEventListener("change", () => go({ state: stateSelect.value }));
    const search = h("input", { type: "search", placeholder: "Search documents", "aria-label": "Search documents", value: q.q || "" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    const filtered = q.kind || q.state || q.q;
    put(body,
      S.kpis([
        { label: "Documents", value: everything.length },
        { label: "Storage used", value: size(data.bytes), hint: `of ${size(data.max_vault_bytes || 200 * 1048576)}` },
        { label: "Expire within 90 days", value: everything.filter((d) => d.state === "expires_soon").length, tone: everything.some((d) => d.state === "expires_soon") ? "warn" : "" },
        { label: "Expired", value: everything.filter((d) => d.state === "expired").length, tone: everything.some((d) => d.state === "expired") ? "bad" : "" },
      ]),
      h("div", { class: "btn-row jr-filters" }, kindSelect, stateSelect, search, S.button("Add document", () => documentForm(null, rerender)), everything.length ? S.button("Export list", () => Aira.download("/api/journey/export/documents", "documents.csv"), { kind: "ghost", title: "A list of your documents and dates. Numbers and scans are not included." }) : null),
      data.documents.length ? h("div", { class: "jr-cards" }, ...data.documents.map((doc) => h("article", { class: "card jr-card" },
        h("div", { class: "jr-card-head" }, h("h3", { text: doc.title }), statePill(doc.state, DOC_STATES)),
        h("p", { class: "muted small", text: [DOC_KIND[doc.kind] || doc.kind, doc.number_hint, doc.file ? size(doc.file.size) : "No file"].filter(Boolean).join(" · ") }),
        doc.expires_on ? h("p", { class: "small", text: `Expires ${S.day(doc.expires_on)} · ${inDays(doc.days_left)}` }) : null,
        doc.tags.length ? h("div", { class: "jr-tags" }, ...doc.tags.map((tag) => h("span", { class: "suite-pill", text: tag }))) : null,
        h("p", { class: "muted small", text: `Added ${S.day(doc.created_at)}` }),
        h("div", { class: "btn-row" },
          S.button("View", () => documentDetail(doc, rerender), { kind: "sm" }),
          doc.file ? S.button("Download", () => openFile(doc, "download"), { kind: "secondary sm" }) : null,
          S.button("Edit", () => documentForm(doc, rerender), { kind: "ghost sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`"${doc.title}"`)) { await api(`/documents/${doc.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })))))
        : Aira.ui.empty(filtered ? "No documents match" : "Your vault is empty", filtered ? "Try another kind, state or search." : "Keep your passport, certificates and insurance cards here with their expiry dates. Monosage reminds you before they run out.", filtered ? S.button("Show all documents", () => go({ kind: "", state: "", q: "" }), { kind: "secondary" }) : S.button("Add your first document", () => documentForm(null, rerender))));
    if (/^[0-9a-f]{32}$/.test(q.doc || "") && opened !== `doc:${q.doc}`) {
      opened = `doc:${q.doc}`;
      documentDetail({ id: q.doc }, rerender).catch((error) => Aira.reportError(error));
    }
  }

  // ---------------------------------------------------------------- journal
  function moodPicker(value, onPick) {
    let current = value || 0;
    const buttons = MOODS.map(([mood, label, mark]) => {
      const button = h("button", { type: "button", class: `jr-mood ${current === mood ? "on" : ""}`, "aria-pressed": String(current === mood) },
        h("span", { class: "jr-mood-mark", "aria-hidden": "true", text: mark }), h("span", { text: label }));
      button.addEventListener("click", () => {
        current = current === mood ? 0 : mood;
        buttons.forEach((other, index) => { const on = MOODS[index][0] === current; other.classList.toggle("on", on); other.setAttribute("aria-pressed", String(on)); });
        if (onPick) onPick(current);
      });
      return button;
    });
    const set = (mood) => {
      current = mood || 0;
      buttons.forEach((other, index) => { const on = MOODS[index][0] === current; other.classList.toggle("on", on); other.setAttribute("aria-pressed", String(on)); });
    };
    return { node: h("div", { class: "jr-moods-pick", role: "group", "aria-label": "How was the day?" }, ...buttons), value: () => current, set };
  }

  async function entryForm(entry, rerender) {
    const picker = moodPicker(entry ? entry.mood : 0);
    const form = S.form([
      { name: "day", label: "Day", type: "date", required: true },
      { name: "body", label: "What happened?", type: "textarea", rows: 5 },
      { name: "grateful", label: "One thing you are grateful for (optional)" },
    ], entry ? { ...entry } : { day: localDay() });
    form.inputs.day.control.max = localDay();
    if (entry) form.inputs.day.control.disabled = true;
    form.node.prepend(Aira.ui.field("How was the day?", picker.node));
    // "Write about another day" on a day already written: show what is there, so saving never wipes it.
    let loaded = entry ? entry.day : "";
    const already = h("p", { class: "muted small", role: "status", hidden: true });
    form.inputs.day.control.after(already);
    if (!entry) {
      const months = {};
      const look = async () => {
        const day = form.inputs.day.control.value;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day === loaded) return;
        const month = day.slice(0, 7);
        try { months[month] = months[month] || (await api(`/journal?month=${month}`)).entries; } catch { return; }
        if (form.inputs.day.control.value !== day) return;
        const found = months[month].find((item) => item.day === day);
        if (found) {
          picker.set(found.mood);
          form.inputs.body.control.value = found.body || "";
          form.inputs.grateful.control.value = found.grateful || "";
          already.textContent = `You already wrote about ${S.day(day)}. It is shown here: what you save replaces it.`;
          already.hidden = false;
          loaded = day;
        } else if (loaded) {
          picker.set(0);
          form.inputs.body.control.value = "";
          form.inputs.grateful.control.value = "";
          already.hidden = true;
          loaded = "";
        }
      };
      form.inputs.day.control.addEventListener("change", look);
      look();
    }
    Aira.modal({
      title: entry ? `Your journal: ${S.day(entry.day)}` : "Write about a day",
      wide: true,
      body: h("form", { onsubmit: (event) => event.preventDefault() }, form.node),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Save", kind: "primary", onClick: async () => {
          form.setError("");
          const read = form.read();
          if (!picker.value() && !read.body.trim() && !read.grateful.trim()) { form.setError("Pick how the day was or write a few words."); return false; }
          const day = entry ? entry.day : read.day;
          // new: the server refuses (instead of wiping) a day already written that this form did not load.
          try { await api("/journal", { method: "POST", body: { day, mood: picker.value(), body: read.body, grateful: read.grateful, new: loaded !== day } }); } catch (error) { form.setError(error.message); return false; }
          Aira.toast("Saved in your journal.", "success");
          rerender();
          return true;
        } },
      ],
    });
  }

  function moodChart(days) {
    const rated = days.filter((d) => d.mood);
    const fmt = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    return h("div", { class: "jr-mood-chart" },
      h("ol", { class: "jr-mood-bars", "aria-label": `Your mood on the last 30 days: ${rated.length} days rated` }, ...days.map((d) => h("li", { class: `jr-mood-bar m${d.mood}`, title: `${fmt(d.day)}: ${d.mood ? MOOD[d.mood].label : "not written"}`, "aria-label": `${fmt(d.day)}: ${d.mood ? MOOD[d.mood].label : "not written"}` }, h("span", { style: { height: `${d.mood ? 12 + d.mood * 17 : 0}%` } })))),
      h("p", { class: "muted small", text: "Each bar is a day, oldest on the left: the taller, the better the day. Days without a mood have no bar." }));
  }

  async function journalTab(body, route, rerender) {
    const q = route.query;
    const params = new URLSearchParams();
    if (q.month) params.set("month", q.month);
    if (q.mood) params.set("mood", q.mood);
    if (q.q) params.set("q", q.q);
    const data = await api(`/journal?${params}`);
    const sum = data.summary;
    const go = (changes) => Aira.router.go(["journey"], { ...q, tab: "journal", ...changes });
    const today = sum.entry_today;
    const picker = moodPicker(today ? today.mood : 0);
    const text = h("textarea", { rows: 4, maxlength: 5000, placeholder: "A few lines about today: what happened, how you feel, what you learnt." });
    text.value = today ? today.body : "";
    const grateful = h("input", { type: "text", maxlength: 300, placeholder: "My family, a sunny walk, a good cup of tea", value: today ? today.grateful : "" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const saveToday = async () => {
      error.hidden = true;
      if (!picker.value() && !text.value.trim() && !grateful.value.trim()) { error.textContent = "Pick how the day was or write a few words."; error.hidden = false; return; }
      try { await api("/journal", { method: "POST", body: { mood: picker.value(), body: text.value, grateful: grateful.value } }); } catch (err) { error.textContent = err.message; error.hidden = false; return; }
      Aira.toast("Saved in your journal.", "success");
      rerender();
    };
    const write = S.section(today ? "Today, in your journal" : "How was today?",
      picker.node,
      Aira.ui.field("What happened?", text),
      Aira.ui.field("One thing you are grateful for (optional)", grateful),
      error,
      h("div", { class: "btn-row" }, S.button(today ? "Save changes" : "Save today", saveToday), S.button("Write about another day", () => entryForm(null, rerender), { kind: "ghost" })),
      h("p", { class: "muted small", text: "Your journal is private: nobody else in your household or organization can read it." }));
    const monthLabel = (month) => new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
    const monthSelect = Aira.ui.select([["", "Every month"], ...[...new Set([...sum.months, ...(q.month ? [q.month] : [])])].map((m) => [m, monthLabel(m)])], q.month || "", { "aria-label": "Month" });
    monthSelect.addEventListener("change", () => go({ month: monthSelect.value }));
    const moodSelect = Aira.ui.select([["", "Every mood"], ...MOODS.map(([value, label, mark]) => [String(value), `${mark} ${label}`])], q.mood || "", { "aria-label": "Mood" });
    moodSelect.addEventListener("change", () => go({ mood: moodSelect.value }));
    const search = h("input", { type: "search", placeholder: "Search your journal", "aria-label": "Search your journal", value: q.q || "" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    const filtered = q.month || q.mood || q.q;
    const entries = data.entries.length ? h("div", { class: "jr-cards" }, ...data.entries.map((entry) => h("article", { class: "card jr-card jr-entry", dataset: { jrOpen: `entry:${entry.id}` } },
      h("div", { class: "jr-card-head" }, h("h3", { text: S.day(entry.day) }), entry.mood ? h("span", { class: "suite-pill as-written", text: `${MOOD[entry.mood].mark} ${entry.mood_label}` }) : null),
      entry.body ? h("p", { class: "jr-entry-body", text: entry.body }) : null,
      entry.grateful ? h("p", { class: "muted small", text: `Grateful for: ${entry.grateful}` }) : null,
      h("div", { class: "btn-row" },
        S.button("Edit", () => entryForm(entry, rerender), { kind: "ghost sm" }),
        S.button("Delete", async () => { if (await S.confirmDelete(`your journal for ${S.day(entry.day)}`)) { await api(`/journal/${entry.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })))))
      : Aira.ui.empty(filtered ? "Nothing matches" : "Your journal is empty", filtered ? "Try another month, mood or search." : "Write a few lines a day, or just pick how the day was. Looking back later is the best part.", filtered ? S.button("Show every day", () => go({ month: "", mood: "", q: "" }), { kind: "secondary" }) : null);
    put(body,
      S.kpis([
        { label: "Days in a row", value: sum.streak, hint: sum.best_streak ? `Best: ${plural(sum.best_streak, "day", "days")}` : "Write today to start", tone: sum.streak >= 7 ? "good" : "" },
        { label: "This month", value: sum.this_month, hint: plural(sum.total, "day written", "days written") + " in all" },
        { label: "Mood, last 30 days", value: sum.mood_word || "Not yet", hint: sum.mood_average ? `${sum.mood_average} out of 5` : "Pick a mood each day" },
      ]),
      S.grid(write, S.section("Your mood", sum.mood_days.some((d) => d.mood) ? moodChart(sum.mood_days) : h("p", { class: "muted", text: "Pick how each day was and your month appears here." }))),
      data.memories.length ? memoriesSection(data.memories) : null,
      sum.total ? h("div", { class: "btn-row jr-filters" }, monthSelect, moodSelect, search, S.button("Export CSV", () => Aira.download("/api/journey/export/journal", "journal.csv"), { kind: "ghost" })) : null,
      entries);
  }

  function memoriesSection(items) {
    return S.section("On this day", h("ul", { class: "life-lines" }, ...items.map((item) => h("li", { class: "life-line" },
      h("span", { class: "jr-mark", "aria-hidden": "true", text: item.type === "journal" ? "📔" : KIND_MARK[item.kind] || "📅" }),
      h("div", {}, h("a", { class: "life-line-text", href: item.type === "journal" ? `#/journey?tab=journal&month=${item.date.slice(0, 7)}` : `#/journey?tab=timeline&year=${item.date.slice(0, 4)}`, text: item.title }),
        h("span", { class: "muted life-line-sub", text: `${item.years_ago === 1 ? "1 year" : `${item.years_ago} years`} ago · ${S.day(item.date)}` }))))));
  }

  // ---------------------------------------------------------------- overview
  async function overviewTab(body, rerender) {
    const data = await api("/overview");
    const MARK = { event: "📅", goal: "🎯", document: "📄" };
    const WHAT = { event: "Event", goal: "Goal target", document: "Expires" };
    const LINK = { event: () => "#/journey?tab=timeline", goal: (id) => `#/journey?tab=goals&goal=${id}`, document: (id) => `#/journey?tab=documents&doc=${id}` };
    const best = data.habits.best[0];
    const hasEarning = data.money_goals.some((g) => g.source === "earnings");
    put(body,
      data.cheers_unread ? h("a", { class: "card jr-notice", href: "#/journey?tab=goals" }, h("span", { "aria-hidden": "true", text: "👏" }), h("span", { text: `You have ${data.cheers_unread} new ${data.cheers_unread === 1 ? "cheer" : "cheers"} from family and friends. Open the goal to read them.` })) : null,
      S.kpis([
        { label: "Active goals", value: data.goals.active, hint: `${data.goals.in_progress} in progress${data.goals.behind ? ` · ${data.goals.behind} behind` : ""}`, tone: data.goals.behind ? "warn" : "" },
        { label: "Life score", value: data.life_score, hint: data.goals.active ? `Average across ${data.goals.active} active goals` : "Add a goal to start", tone: data.life_score >= 60 ? "good" : "" },
        { label: "Habits today", value: data.habits.total ? `${data.habits.done_today} of ${data.habits.total}` : 0, hint: best ? `🔥 ${best.title}: ${best.every_day ? plural(best.streak, "day", "days") : plural(best.streak, "week", "weeks")} in a row` : "Small things you do often", tone: data.habits.total && data.habits.done_today === data.habits.total ? "good" : "" },
        { label: "Documents", value: data.documents.total, hint: `${data.documents.expires_soon} expiring soon${data.documents.expired ? ` · ${data.documents.expired} expired` : ""}`, tone: data.documents.expires_soon || data.documents.expired ? "warn" : "" },
        { label: "Journal", value: data.journal.streak, hint: data.journal.written_today ? `Days in a row · ${data.journal.mood_today ? `today: ${MOOD[data.journal.mood_today].label.toLowerCase()}` : "written today"}` : "Days in a row · not written today", tone: data.journal.written_today ? "good" : "" },
        { label: "Timeline events", value: data.events, hint: "Lifetime milestones" },
      ]),
      data.memories.length ? memoriesSection(data.memories) : null,
      S.grid(
        S.section("Recent goals", data.recent_goals.length ? h("ul", { class: "life-lines" }, ...data.recent_goals.map((goal) => h("li", { class: "life-line jr-goal-line" },
          h("div", { class: "jr-grow" }, h("a", { class: "life-line-text", href: `#/journey?tab=goals&goal=${goal.id}`, text: goal.title }), h("span", { class: "muted life-line-sub", text: [CATEGORY[goal.category] || goal.category, measured(goal) ? `${amount(goal, goal.current_amount)} of ${amount(goal, goal.target_amount)}` : ""].filter(Boolean).join(" · ") }), progressBar(goal.progress, goal.title)),
          h("div", { class: "jr-right" }, h("strong", { text: `${goal.progress}%` }), statePill(goal.state, STATES))))) : Aira.ui.empty("No goals yet", "Set a goal for any part of your life.", S.button("Add a goal", () => newGoal(rerender)))),
        S.section("Coming up", data.milestones.length ? h("ul", { class: "life-lines" }, ...data.milestones.map((item) => h("li", { class: "life-line" },
          h("span", { class: "jr-mark", "aria-hidden": "true", text: MARK[item.type] }),
          h("div", {}, h("a", { class: "life-line-text", href: LINK[item.type](item.id), text: item.title }), h("span", { class: "muted life-line-sub", text: `${WHAT[item.type]} · ${S.day(item.date)} · ${inDays(item.days_left)}` }))))) : h("p", { class: "muted", text: "Nothing in the next year. Add target dates to goals, future events or expiry dates to documents." })),
      ),
      data.money_goals.length || data.may_count_earnings || data.support.supporters ? S.section("Money goals",
        data.money_goals.length ? h("ul", { class: "life-lines" }, ...data.money_goals.map((goal) => h("li", { class: "life-line jr-goal-line" },
          h("div", { class: "jr-grow" }, h("a", { class: "life-line-text", href: `#/journey?tab=goals&goal=${goal.id}`, text: goal.title }), h("span", { class: "muted life-line-sub", text: `${goal.so_far} of ${goal.target}${goal.source === "earnings" ? " · counted from your apps" : " saved"}` }), progressBar(goal.progress, goal.title)),
          h("div", { class: "jr-right" }, h("strong", { text: `${goal.progress}%` }), statePill(goal.state, STATES))))) : h("p", { class: "muted", text: "Save for something, or set what you want to earn." }),
        h("div", { class: "btn-row" },
          S.button("Save for something", () => newGoal(rerender, { measure: "money", category: "money" }), { kind: "secondary sm" }),
          data.may_count_earnings && !hasEarning ? S.button("Set an earning goal", () => newGoal(rerender, { measure: "money", category: "career", source: "earnings" }), { kind: "secondary sm" }) : null),
        data.may_count_earnings && !hasEarning ? h("p", { class: "muted small", text: "An earning goal counts what you earn in Monosage's apps by itself: paid invoices in Sales, shop orders, bookings, event tickets, courses and your share of the ads on your videos." }) : null,
        data.support.supporters ? h("p", { class: "jr-amount" }, h("strong", { text: plural(data.support.supporters, "supporter", "supporters") }), h("span", { class: "muted", text: ` · ${data.support.totals.map((t) => `${t.received_label} received, ${t.promised_label} promised`).join(" · ")}` })) : null,
        data.may_take_support && !data.support.goals ? h("p", { class: "muted small", text: "Need help reaching a goal? Open it and turn on Supporters: family and friends back it from its page and pay you through your own link." }) : null) : null,
      data.categories.length ? S.section("Progress by area of life", h("ul", { class: "jr-areas" }, ...data.categories.map((c) => h("li", { class: "jr-area" },
        h("span", { class: "jr-area-name", text: `${CATEGORY[c.category] || c.category} (${c.goals})` }),
        progressBar(c.progress, CATEGORY[c.category] || c.category),
        h("strong", { text: `${c.progress}%` }))))) : null,
      // The frame's "+ New" offers these on a wide screen; a phone folds that menu away, so they stay here for it.
      h("div", { class: "btn-row jr-quick" }, S.button("New goal", () => newGoal(rerender)), S.button("New habit", () => habitForm(null, rerender), { kind: "secondary" }), S.button("Add to timeline", () => editEvent(null, rerender), { kind: "secondary" }), S.button("Add a document", () => documentForm(null, rerender), { kind: "secondary" })));
  }

  // ---------------------------------------------------------------- search (the box across the top, and ?tab=search)
  const RESULT_KINDS = [["goals", "Goals"], ["habits", "Habits"], ["entries", "Journal"], ["events", "Timeline"], ["documents", "Documents"]];
  const found = new Map(); // "limit|words" -> { at, promise }: the box asks again as each letter is typed
  function findRecords(words, limit) {
    const key = `${limit}|${words.toLowerCase()}`;
    const hit = found.get(key);
    if (hit && Date.now() - hit.at < 5000) return hit.promise;
    const promise = api(`/search?${new URLSearchParams({ q: words, limit: String(limit) })}`, { silent: true });
    promise.catch(() => found.delete(key));
    found.set(key, { at: Date.now(), promise });
    if (found.size > 40) found.delete(found.keys().next().value);
    return promise;
  }

  // Each record found, as the search box and the results page show it, with the link that opens it.
  function resultRows(data) {
    const statusWord = (status) => (STATUSES.find(([value]) => value === status) || [status, status])[1];
    const perWeek = (count) => (PER_WEEK.find(([value]) => Number(value) === count) || ["", ""])[1];
    const join = (...parts) => parts.filter(Boolean).join(" · ");
    return {
      goals: data.goals.map((goal) => ({ label: goal.title, sub: join("Goal", CATEGORY[goal.category] || goal.category, goal.status === "active" ? `${goal.progress}%` : statusWord(goal.status)), icon: "jr-goal", href: `#/journey?tab=goals${goal.status === "active" ? "" : "&status=all"}&goal=${goal.id}` })),
      habits: data.habits.map((habit) => ({ label: habit.title, sub: join("Habit", perWeek(habit.per_week), habit.archived ? "Stopped" : ""), icon: "jr-habit", href: `#/journey?tab=habits&open=habit:${habit.id}` })),
      entries: data.entries.map((entry) => ({ label: entry.snippet || "A day in your journal", sub: join("Journal", S.day(entry.day), entry.mood ? `${MOOD[entry.mood].mark} ${entry.mood_label}` : ""), icon: "jr-journal", href: `#/journey?tab=journal&month=${entry.day.slice(0, 7)}&open=entry:${entry.id}` })),
      events: data.events.map((event) => ({ label: event.title, sub: join("Timeline", S.day(event.event_date), event.location), icon: "jr-timeline", href: `#/journey?tab=timeline&open=event:${event.id}` })),
      documents: data.documents.map((doc) => ({ label: doc.title, sub: join("Document", DOC_KIND[doc.kind] || doc.kind, doc.expires_on ? `${doc.state === "expired" ? "Expired" : "Expires"} ${S.day(doc.expires_on)}` : ""), icon: "documents", href: `#/journey?tab=documents&doc=${doc.id}` })),
    };
  }

  Aira.appSearch = Aira.appSearch || {};
  Aira.appSearch.journey = {
    placeholder: "Search goals, habits, journal, timeline, documents",
    // Up to six records, one of each kind in turn, so many goals do not hide the journal or the documents.
    async suggest(query) {
      const rows = resultRows(await findRecords(query, 3));
      const lanes = RESULT_KINDS.map(([key]) => rows[key]);
      const out = [];
      for (let round = 0; out.length < 6 && lanes.some((lane) => lane.length > round); round += 1) for (const lane of lanes) if (lane[round] && out.length < 6) out.push(lane[round]);
      return out;
    },
    run(query) { Aira.router.go(["journey"], { tab: "search", q: query }); },
  };

  async function searchTab(body, query, rerender) {
    if (!query) {
      body.append(Aira.ui.empty("Search Life", "Type in the box at the top: a goal, a habit, a word from your journal, a place on your timeline or the name of a document."));
      return;
    }
    const data = await api(`/search?${new URLSearchParams({ q: query, limit: "50" })}`);
    if (!data.total) {
      body.append(Aira.ui.empty("Nothing matches", `Nothing in Life matches “${query}”. Try other words, or add something new.`,
        h("div", { class: "btn-row jr-empty-actions" }, S.button("New goal", () => newGoal(rerender)), S.button("New habit", () => habitForm(null, rerender), { kind: "secondary" }), S.button("Add to timeline", () => editEvent(null, rerender), { kind: "secondary" }), S.button("Add a document", () => documentForm(null, rerender), { kind: "secondary" }))));
      return;
    }
    const rows = resultRows(data);
    for (const [key, label] of RESULT_KINDS) {
      if (!rows[key].length) continue;
      body.append(S.section(`${label} (${rows[key].length})`, h("ul", { class: "jr-results" }, ...rows[key].map((row) => h("li", {},
        h("a", { class: "jr-result", href: row.href },
          h("span", { class: "jr-result-icon", "aria-hidden": "true" }, Aira.icon(row.icon)),
          h("span", { class: "jr-result-text" }, h("span", { class: "jr-result-label", text: row.label }), h("span", { class: "muted small", text: row.sub }))))))));
    }
  }

  // ---------------------------------------------------------------- "+ New"
  // Life is each person's own (children and every hosted member too), so every create is offered to everyone.
  const NEW_TAB = { goal: "goals", habit: "habits", entry: "journal", event: "timeline", document: "documents" };
  Aira.appNew = Aira.appNew || {};
  Aira.appNew.journey = () => [
    { label: "New goal", icon: "jr-goal", href: "#/journey?tab=goals&new=goal" },
    { label: "New habit", icon: "jr-habit", href: "#/journey?tab=habits&new=habit" },
    { label: "Write in your journal", icon: "jr-journal", href: "#/journey?tab=journal&new=entry" },
    { label: "Add to your timeline", icon: "jr-timeline", href: "#/journey?tab=timeline&new=event" },
    { label: "Add a document", icon: "documents", href: "#/journey?tab=documents&new=document" },
  ];

  // What "+ New" or the search box asked for, once the tab is drawn: open the form, or show the record found.
  function answer(ask, panel, rerender) {
    if (ask.new) {
      const focusJournal = () => {
        const mood = panel.querySelector(".jr-moods-pick .jr-mood.on") || panel.querySelector(".jr-moods-pick .jr-mood");
        if (mood) { mood.scrollIntoView({ block: "center" }); mood.focus({ preventScroll: true }); }
      };
      const make = { goal: () => newGoal(rerender), habit: () => habitForm(null, rerender), entry: focusJournal, event: () => editEvent(null, rerender), document: () => documentForm(null, rerender) }[ask.new];
      if (make) Promise.resolve(make()).catch((error) => Aira.reportError(error));
      return;
    }
    if (!ask.open) return;
    const node = [...panel.querySelectorAll("[data-jr-open]")].find((item) => item.dataset.jrOpen === ask.open);
    if (!node) { Aira.toast("That is not here any more.", "info"); return; }
    node.classList.add("jr-found");
    node.tabIndex = -1;
    node.scrollIntoView({ block: "center" });
    node.focus({ preventScroll: true });
    node.addEventListener("blur", () => node.classList.remove("jr-found"), { once: true });
  }

  // ---------------------------------------------------------------- the page
  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard", about: "Your goals, habits, journal, your story and the papers that matter, at a glance." },
    { id: "goals", label: "Goals", icon: "jr-goal", about: "Big or small: tick off steps, count a number, save money, or earn it with your apps." },
    { id: "habits", label: "Habits", icon: "jr-habit", about: "Small things you do often. Tick them here or on Today and watch your streak grow." },
    { id: "journal", label: "Journal", icon: "jr-journal", about: "How each day went, in a mood and a few lines. Only you can read it." },
    { id: "timeline", label: "Timeline", icon: "jr-timeline", about: "The moments that shaped your life, year by year." },
    { id: "documents", label: "Documents", icon: "documents", about: "Passports, certificates and insurance cards, kept encrypted, with reminders before they expire." },
  ];
  let pending = null; // what ?new= or ?open= asked for, kept while the address forgets it

  Aira.views.journey = async (main, route) => {
    // Take ?new= and ?open= out of the address first, so Back or a reload does not ask again; the page drawn next
    // answers them.
    if (route.query.new || route.query.open) {
      pending = { new: String(route.query.new || ""), open: String(route.query.open || "") };
      const rest = { ...route.query };
      delete rest.new;
      delete rest.open;
      if (NEW_TAB[pending.new]) rest.tab = NEW_TAB[pending.new];
      Aira.router.go(["journey"], rest, { replace: true });
      return;
    }
    let ask = pending;
    pending = null;
    found.clear();
    const query = String(route.query.q || "").trim();
    const tab = route.query.tab === "search" || TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "overview";
    // The page is named after the open folder (the app's own name is in the bar above), like a mail app's.
    const meta = TABS.find((t) => t.id === tab);
    const title = tab === "search" ? (query ? `Results for “${query}”` : "Search") : meta.label;
    const body = S.page(main, { title, subtitle: tab === "search" ? "Goals, habits, journal days, timeline events and documents that match." : meta.about });
    document.title = `${title} · Life · Monosage`;
    if (!route.query.goal && !route.query.doc) opened = "";
    const strip = Aira.ui.tabs(TABS, tab, (id) => Aira.router.go(["journey"], { tab: id }));
    strip.classList.add("jr-tabstrip"); // a phone's tabs; wider screens have them as the left menu's folders
    body.append(strip);
    if (Aira.shell && Aira.shell.tabs) Aira.shell.tabs("journey", TABS, tab === "search" ? "" : tab);
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(4));
    body.append(panel);
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "goals") await goalsTab(holder, route, render);
        else if (tab === "habits") await habitsTab(holder, render);
        else if (tab === "journal") await journalTab(holder, route, render);
        else if (tab === "timeline") await timelineTab(holder, route, render);
        else if (tab === "documents") await documentsTab(holder, route, render);
        else if (tab === "search") await searchTab(holder, query, render);
        else await overviewTab(holder, render);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); return; }
      if (ask) { const asked = ask; ask = null; answer(asked, panel, render); }
    };
    await render();
  };
})();

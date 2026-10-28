/* Aira — Health (#/care; app id "care"): medicines and reminders, refills, readings, a mood diary, appointments, a
 * report for the doctor, an emergency card, caregivers, and care work (earning by looking after people), for one
 * person at a time (life.js decides who). Made to be easy for older people: big buttons with words, plain language,
 * one thing per tab. Nothing here is medical advice.
 *
 * Tabs (?tab=): today (default), medicines, readings, diary, appointments, report, card, caregivers, work.
 * ?person=<id> shows someone who made you their caregiver, read-only. Everything goes through /api/care/*
 * (aira/suite/care). Styles live in app-care.css. DOM is built with h() and textContent only.
 *
 * In the Aira frame (shell.js): "+ New" offers a medicine, a reading, an appointment, a diary entry and (adults) a
 * care visit, each opening the form its tab has (?new=<kind>); the search box across the top finds this person's
 * medicines, appointments, kinds of reading, diary days, clients and people (GET /api/care/search) and opens the
 * one chosen (?open=<kind>:<id>), and Enter lists them all (?tab=search&q=). The tabs are also the left menu's
 * folders. ?new= and ?open= are taken out of the address before the page is drawn, so Back or a reload does not
 * ask again.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("care", path, opts);

  // Glyphs for the left menu's folders, "+ New" and the search box (24x24 strokes, like app.js ICONS).
  const GLYPHS = {
    "care-pill": '<svg viewBox="0 0 24 24"><path d="M10.5 20.5a5 5 0 0 1-7-7l6-6a5 5 0 0 1 7 7z"/><path d="m8.5 8.5 7 7"/></svg>',
    "care-pulse": '<svg viewBox="0 0 24 24"><path d="M3 12h4l2-5 4 10 2-5h6"/></svg>',
    "care-diary": '<svg viewBox="0 0 24 24"><path d="M6 3h11a1 1 0 0 1 1 1v17H7a2 2 0 0 1-2-2V5a2 2 0 0 1 1-2z"/><path d="M5 19a2 2 0 0 1 2-2h11M9 7h5M9 11h4"/></svg>',
    "care-card": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 12h5M9.5 9.5v5M15 10h3M15 14h3"/></svg>',
    "care-earn": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M14.8 9.3c-.5-.9-1.5-1.4-2.8-1.4-1.5 0-2.6.8-2.6 2s1.1 1.7 2.6 2.1 2.8.9 2.8 2.1-1.2 2-2.8 2c-1.3 0-2.4-.6-2.9-1.5M12 6v1.9M12 16.1V18"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;

  const TABS = [
    { id: "today", label: "Today", icon: "today", about: "Your medicines for today, how you feel and what is coming up." },
    { id: "medicines", label: "Medicines", icon: "care-pill", about: "What you take and when, how long each box lasts, and the last 30 days." },
    { id: "readings", label: "Readings", icon: "care-pulse", about: "Blood pressure, sugar, weight and more, with a chart of the last ones." },
    { id: "diary", label: "Diary", icon: "care-diary", about: "A line a day about how you feel. Only you can read it." },
    { id: "appointments", label: "Appointments", icon: "interviews", about: "Your visits, with reminders the day before and two hours before." },
    { id: "report", label: "Doctor report", icon: "documents", about: "One page for your next visit: medicines, readings and your questions." },
    { id: "card", label: "Emergency card", icon: "care-card", about: "What helpers need to know if you cannot tell them." },
    { id: "caregivers", label: "Caregivers", icon: "family", about: "The people who help you, and the people you look after." },
    { id: "work", label: "Care work", icon: "care-earn", about: "Get paid for looking after people: visits, statements and what you earned." },
  ];
  const OWN_ONLY = ["diary", "caregivers", "work"];
  const STATUS_WORDS = { taken: "Taken", skipped: "Skipped", snoozed: "Later", upcoming: "Later today", due: "Take now", missed: "Missed" };
  const STATUS_TONES = { taken: "good", skipped: "", snoozed: "info", upcoming: "", due: "warn", missed: "bad" };
  const MOODS = [[1, "😢", "Very low"], [2, "🙁", "Low"], [3, "😐", "Okay"], [4, "🙂", "Good"], [5, "😄", "Great"]];
  const FORMS = ["tablet", "capsule", "liquid", "inhaler", "injection", "drops", "cream", "patch", "other"];
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const pad = (n) => String(n).padStart(2, "0");
  const localDay = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const toIsoLocal = (value) => (value ? new Date(value).toISOString() : "");
  const toInputLocal = (iso) => { if (!iso) return ""; const d = new Date(iso); return `${localDay(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");
  const dayWords = (day) => (day ? new Date(`${day}T12:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : "");
  const hours = (minutes) => { const m = Number(minutes) || 0; const hrs = Math.floor(m / 60); const rest = m % 60; return [hrs ? `${hrs} h` : "", rest ? `${rest} min` : ""].filter(Boolean).join(" ") || "0 min"; };
  const big = (label, onClick, kind = "") => S.button(label, onClick, { kind: `${kind} care-big`.trim() });
  const note = (text) => h("p", { class: "muted small", text });
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } };

  function nav(tab, person) { Aira.router.go(["care"], { tab, person }); }

  async function done(message) { Aira.toast(message, "success"); if (Aira.life && Aira.life.tick) Aira.life.tick(); }

  // Print one part of the page (the emergency card, the doctor report): app-care.css hides everything else.
  function printPart(node) {
    node.classList.add("care-printable");
    document.body.classList.add("care-print");
    const clean = () => { document.body.classList.remove("care-print"); node.classList.remove("care-printable"); window.removeEventListener("afterprint", clean); };
    window.addEventListener("afterprint", clean);
    window.print();
    setTimeout(clean, 1000);
  }

  // ---------------------------------------------------------------- Today
  function slotRow(slot, canEdit, rerender) {
    const body = { medicine_id: slot.medicine_id, date: slot.date, time: slot.time };
    const open = !["taken", "skipped"].includes(slot.status);
    return h("li", { class: `care-slot ${slot.status}` },
      h("div", { class: "care-slot-time", text: slot.time }),
      h("div", { class: "care-slot-main" },
        h("strong", { text: `${slot.name} ${slot.dose || ""}`.trim() }),
        h("span", { class: "muted", text: [slot.with_food, slot.taken_at ? `taken ${when(slot.taken_at)}` : "", slot.reason].filter(Boolean).join(" · ") }),
        S.pill(STATUS_WORDS[slot.status] || slot.status, { [String(STATUS_WORDS[slot.status] || slot.status).toLowerCase()]: STATUS_TONES[slot.status] || "" })),
      canEdit && open ? h("div", { class: "btn-row care-slot-actions" },
        big("Taken", async () => { await api("/doses/take", { method: "POST", body }); await done(`${slot.name}: taken.`); rerender(); }),
        S.button("Later", async () => { await api("/doses/snooze", { method: "POST", body: { ...body, minutes: 15 } }); await done("We will remind you in 15 minutes."); rerender(); }, { kind: "secondary" }),
        S.button("Skip", async () => { const reason = await S.edit({ title: `Skip ${slot.name}`, fields: [{ name: "reason", label: "Why? (optional)", placeholder: "Felt sick, ran out, doctor said so" }], submitLabel: "Skip this dose", onSave: (values) => values }); if (!reason) return; await api("/doses/skip", { method: "POST", body: { ...body, reason: reason.reason } }); await done("Skipped."); rerender(); }, { kind: "ghost" })) : null);
  }

  // "Add a reading" from Today: choose what was measured first.
  function chooseReading(kinds, rerender) {
    let dialog = null;
    const choice = (id, spec) => h("button", { type: "button", class: "btn secondary care-big", onclick: () => { dialog.close(); addReading(id, rerender, kinds); } }, h("span", { text: spec.label }));
    dialog = Aira.modal({ title: "What did you measure?", body: h("div", { class: "care-choose" }, ...Object.entries(kinds).map(([id, spec]) => choice(id, spec))), actions: [{ label: "Cancel", kind: "ghost" }] });
  }

  async function todayTab(body, ctx) {
    const data = ctx.overview;
    const slots = data.slots || [];
    const a7 = data.adherence_7;
    const taken = slots.filter((s) => s.status === "taken").length;
    body.append(S.kpis([
      slots.length
        ? { label: "Taken today", value: `${taken} of ${slots.length}`, tone: slots.some((s) => s.status === "missed") ? "warn" : taken === slots.length ? "good" : "" }
        : { label: "Taken today", value: "Nothing due", hint: "No medicines with set times today" },
      { label: "Last 7 days", value: a7.percent === null ? "-" : `${a7.percent}%`, hint: a7.counted ? `${a7.taken} of ${a7.counted} doses taken` : "Starts once doses are due", tone: a7.percent !== null && a7.percent < 80 ? "warn" : "" },
      data.next_appointment ? { label: "Next appointment", value: data.next_appointment.title, hint: when(data.next_appointment.at) } : null,
    ]));
    for (const item of data.low_stock || []) {
      body.append(h("div", { class: "cloud-banner warning care-refill", role: "status" },
        h("strong", { text: "Time to refill" }),
        h("span", { text: `${item.name}: ${item.stock} left${item.days_left !== null && item.days_left !== undefined ? `, about ${item.days_left} ${item.days_left === 1 ? "day" : "days"}` : ""}.` }),
        ctx.canEdit ? S.button("I got a refill", () => refillMedicine(item, ctx.rerender), { kind: "secondary sm" }) : null));
    }
    body.append(S.section(ctx.canEdit ? "Your medicines today" : `${ctx.person.name}'s medicines today`,
      slots.length ? h("ul", { class: "care-slots" }, ...slots.map((slot) => slotRow(slot, ctx.canEdit, ctx.rerender))) : Aira.ui.empty("No medicines today", ctx.canEdit ? "Add a medicine and Aira reminds you when it is time." : ""),
      ctx.canEdit ? h("div", { class: "btn-row" }, S.button("Add a medicine", () => editMedicine(null, ctx.rerender), { kind: "secondary" })) : null));
    if (ctx.canEdit && (data.as_needed || []).length) {
      body.append(S.section("When you need them", h("div", { class: "btn-row" }, ...data.as_needed.map((medicine) => big(`I took ${medicine.name}`, async () => {
        const result = await api("/doses/as-needed", { method: "POST", body: { medicine_id: medicine.id } });
        if (result.warning) Aira.modal({ title: "Recorded, with a note", body: h("p", { text: result.warning }), actions: [{ label: "OK", kind: "primary" }] });
        else await done(`${medicine.name}: recorded.`);
        ctx.rerender();
      }, "secondary")))));
    }
    if (ctx.canEdit) {
      const today = (data.mood || []).find((entry) => entry.day === data.today);
      body.append(S.section("How do you feel today?", h("div", { class: "care-moods", role: "group", "aria-label": "Your mood today" }, ...MOODS.map(([value, emoji, label]) => h("button", { type: "button", class: "care-mood", "aria-pressed": String(!!today && today.mood === value), onclick: async () => { await api("/diary", { method: "POST", body: { mood: value, day: data.today } }); await done(`Saved: ${label}.`); ctx.rerender(); } }, h("span", { class: "care-mood-emoji", "aria-hidden": "true", text: emoji }), h("span", { text: label })))),
        h("div", { class: "btn-row" }, S.button("Add a reading", () => chooseReading(data.kinds || {}, ctx.rerender), { kind: "secondary" }), h("a", { class: "btn ghost", href: "#/care?tab=diary", text: "Open the diary" }), h("a", { class: "btn ghost", href: "#/care?tab=report", text: "Report for the doctor" }))));
    }
    body.append(note(data.not_advice));
  }

  // ---------------------------------------------------------------- Medicines
  function editMedicine(medicine, rerender) {
    const values = medicine ? { ...medicine, times: (medicine.times || []).join(", "), days: (medicine.days || []).map((d) => DAYS[d]).join(", ") } : { form: "tablet", active: true };
    return S.edit({
      title: medicine ? `Change ${medicine.name}` : "Add a medicine",
      values,
      fields: [
        { name: "name", label: "Name", required: true, placeholder: "Metformin" },
        { name: "dose", label: "How much", placeholder: "500 mg, 1 tablet" },
        { name: "form", label: "Kind", type: "select", options: FORMS.map((f) => [f, f[0].toUpperCase() + f.slice(1)]) },
        { name: "as_needed", label: "Only when needed (no set times)", type: "checkbox" },
        { name: "times", label: "Times of day", placeholder: "08:00, 20:00", hint: "24-hour times, separated by commas." },
        { name: "days", label: "Days", placeholder: "Every day", hint: "Leave empty for every day, or write days like Mon, Wed, Fri." },
        { name: "with_food", label: "Food", placeholder: "With breakfast" },
        { name: "stock", label: "How many are left", type: "number", min: 0, hint: "Aira counts down as you take them and tells you when to refill." },
        { name: "refill_at", label: "Remind me to refill at", type: "number", min: 0, hint: "When this many are left. Leave 0 to be told 5 days before they run out." },
        { name: "min_gap_hours", label: "Hours between doses (for 'when needed')", type: "number", min: 0, max: 72 },
        { name: "start_date", label: "Start", type: "date" },
        { name: "end_date", label: "Finish", type: "date" },
        { name: "notes", label: "Notes", type: "textarea", rows: 2 },
        ...(medicine ? [{ name: "active", label: "Still taking this", type: "checkbox" }] : []),
      ],
      onSave: async (form) => {
        const payload = { ...form, stock: form.stock === null ? "" : form.stock };
        if (medicine) await api(`/medicines/${medicine.id}`, { method: "PATCH", body: payload });
        else await api("/medicines", { method: "POST", body: payload });
        await done("Saved.");
        rerender();
      },
    });
  }

  function refillMedicine(medicine, rerender) {
    return S.edit({
      title: `Refill ${medicine.name}`,
      fields: [
        { name: "amount", label: "How many did you get?", type: "number", min: 1, required: true, placeholder: "30" },
        { name: "note", label: "Note (optional)", placeholder: "City Pharmacy" },
      ],
      submitLabel: "Add to the count",
      onSave: async (values) => {
        const result = await api(`/medicines/${medicine.id}/refill`, { method: "POST", body: values });
        await done(`${medicine.name}: ${result.medicine.stock} left now.`);
        rerender();
      },
    });
  }

  function supplyText(medicine) {
    if (medicine.stock === null || medicine.stock === undefined) return "";
    const left = `${medicine.stock} left`;
    if (medicine.days_left === null || medicine.days_left === undefined) return left;
    return `${left} · lasts about ${medicine.days_left} ${medicine.days_left === 1 ? "day" : "days"} (until ${dayWords(medicine.runs_out)})`;
  }

  function changeTimezone(current, rerender) {
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [current.name];
    const options = [...new Set(["UTC", ...zones, current.name])].map((zone) => [zone, zone.replaceAll("_", " ")]);
    return S.edit({
      title: "Your time zone",
      values: { timezone: current.name },
      fields: [{ name: "timezone", label: "Medicine times are in", type: "select", options, hint: "Reminders come at these times in this zone." }],
      onSave: async (values) => { await api("/settings", { method: "POST", body: values }); await done("Time zone saved."); rerender(); },
    });
  }

  async function medicinesTab(body, ctx) {
    const filters = ctx.filters;
    const query = new URLSearchParams(ctx.person && ctx.qs ? { person: ctx.person.id } : {});
    query.set("days", "30");
    if (filters.medicine) query.set("medicine", filters.medicine);
    if (filters.status) query.set("status", filters.status);
    const [data, history] = await Promise.all([api(`/medicines${ctx.qs}`), api(`/doses?${query}`)]);
    const tz = data.timezone;
    const medicineCard = (medicine) => {
      const a = medicine.adherence_30;
      return h("article", { class: `card care-med ${medicine.active ? "" : "paused"}`, dataset: { careOpen: `medicine:${medicine.id}` } },
        h("h3", { text: `${medicine.name} ${medicine.dose || ""}`.trim() }),
        h("p", { class: "muted", text: medicine.as_needed ? `When needed${medicine.min_gap_hours ? `, at least ${medicine.min_gap_hours} hours apart` : ""}` : `${(medicine.times || []).join(", ")} · ${(medicine.days || []).length ? medicine.days.map((d) => DAYS[d]).join(", ") : "every day"}` }),
        medicine.with_food ? h("p", { text: medicine.with_food }) : null,
        supplyText(medicine) ? h("p", { class: medicine.low ? "care-low" : "muted", text: `${medicine.low ? "Refill soon: " : ""}${supplyText(medicine)}` }) : null,
        a && a.counted ? h("p", { class: "muted small", text: `Last 30 days: ${a.taken} of ${a.counted} taken (${a.percent}%)` }) : null,
        (medicine.refills || []).length ? h("p", { class: "muted small", text: `Last refill ${dayWords(medicine.refills[0].at.slice(0, 10))}: +${medicine.refills[0].amount}${medicine.refills[0].note ? ` (${medicine.refills[0].note})` : ""}` }) : null,
        medicine.active ? null : S.pill("stopped"),
        ctx.canEdit ? h("div", { class: "btn-row" },
          S.button("Change", () => editMedicine(medicine, ctx.rerender), { kind: "secondary sm" }),
          medicine.active ? S.button("I got a refill", () => refillMedicine(medicine, ctx.rerender), { kind: "secondary sm" }) : null,
          medicine.active
            ? S.button("Stop", async () => { if (!(await Aira.confirm({ title: `Stop ${medicine.name}?`, message: "It stops reminding you. Its history is kept; you can start it again with Change.", confirmLabel: "Stop", danger: true }))) return; const r = await api(`/medicines/${medicine.id}`, { method: "DELETE" }); await done(r.outcome === "paused" ? "Stopped; its history is kept." : "Removed (it had no history yet)."); ctx.rerender(); }, { kind: "ghost sm" })
            : S.button("Delete", async () => { if (!(await Aira.confirm({ title: `Delete ${medicine.name}?`, message: "It is removed with its history. This cannot be undone.", confirmLabel: "Delete", danger: true }))) return; await api(`/medicines/${medicine.id}`, { method: "DELETE" }); await done("Deleted."); ctx.rerender(); }, { kind: "ghost sm" })) : null);
    };
    body.append(S.section(ctx.canEdit ? "What you take" : `What ${ctx.person.name} takes`,
      ctx.canEdit ? h("div", { class: "btn-row" }, big("Add a medicine", () => editMedicine(null, ctx.rerender))) : null,
      h("p", { class: "muted small care-tz" }, h("span", { text: `Times are ${tz.name.replaceAll("_", " ")} time.` }), ctx.canEdit && tz.can_change ? S.button("Change", () => changeTimezone(tz, ctx.rerender), { kind: "ghost sm" }) : null, ctx.canEdit && !tz.can_change ? h("span", { text: "It is the same for everyone; an adult who runs the account can change it." }) : null),
      data.medicines.length ? h("div", { class: "care-cards" }, ...data.medicines.map(medicineCard)) : Aira.ui.empty("No medicines yet", ctx.canEdit ? "Add the medicines you take, and when." : "")));
    const a = history.adherence;
    const pickMedicine = Aira.ui.select([["", "All medicines"], ...data.medicines.map((m) => [m.id, m.name])], filters.medicine || "", { "aria-label": "Medicine" });
    const pickStatus = Aira.ui.select([["", "Everything"], ["taken", "Taken"], ["skipped", "Skipped"], ["snoozed", "Later"]], filters.status || "", { "aria-label": "What happened" });
    const refilter = () => { filters.medicine = pickMedicine.value; filters.status = pickStatus.value; ctx.rerender(); };
    pickMedicine.addEventListener("change", refilter);
    pickStatus.addEventListener("change", refilter);
    const csv = new URLSearchParams(query);
    csv.set("format", "csv");
    body.append(S.section("The last 30 days",
      S.kpis([{ label: "Taken", value: a.taken }, { label: "Skipped", value: a.skipped }, { label: "Missed", value: a.missed, tone: a.missed ? "warn" : "" }, { label: "On track", value: a.percent === null ? "-" : `${a.percent}%` }]),
      h("div", { class: "care-filters" }, Aira.ui.field("Medicine", pickMedicine), Aira.ui.field("What happened", pickStatus), S.button("Download", () => Aira.download(`/api/care/doses?${csv}`, "doses.csv"), { kind: "ghost" })),
      S.table({ columns: [{ key: "slot_date", label: "Day", render: (r) => dayWords(r.slot_date) }, { key: "slot_time", label: "Time", render: (r) => r.slot_time || "when needed" }, { key: "medicine", label: "Medicine" }, { key: "status", label: "", render: (r) => S.pill(STATUS_WORDS[r.status] || r.status, { taken: "good", skipped: "", later: "info" }) }], rows: history.doses.slice(0, 80), empty: filters.medicine || filters.status ? "Nothing matches. Choose another medicine or 'Everything'." : "Nothing recorded yet." })));
  }

  // ---------------------------------------------------------------- Readings
  let readingKind = "blood_pressure";
  function addReading(kind, rerender, kinds) {
    const spec = (kinds || {})[kind] || { units: [""], fields: ["Value"], label: "Reading" };
    const fields = kind === "blood_pressure"
      ? [{ name: "value1", label: "Top number", type: "number", required: true }, { name: "value2", label: "Bottom number", type: "number", required: true }, { name: "value3", label: "Pulse (optional)", type: "number" }]
      : [{ name: "value1", label: spec.fields[0], type: "number", required: true, step: "any" }, ...(spec.units.length > 1 ? [{ name: "unit", label: "Unit", type: "select", options: spec.units }] : [])];
    return S.edit({ title: `Add ${String(spec.label).toLowerCase()}`, fields: [...fields, { name: "at", label: "When (leave empty for now)", type: "datetime-local", max: toInputLocal(new Date().toISOString()) }, { name: "note", label: "Note (optional)" }], values: { unit: spec.units[0] }, onSave: async (values) => {
      const result = await api("/readings", { method: "POST", body: { kind, ...values, at: toIsoLocal(values.at) } });
      if (result.reading.hint) Aira.modal({ title: result.reading.display, body: h("p", { text: result.reading.hint }), actions: [{ label: "OK", kind: "primary" }] });
      else await done(`Saved: ${result.reading.display}.`);
      rerender();
    } });
  }

  // A small chart of the last readings, scaled to their own range (a blood pressure is a bar from bottom to top).
  function trend(rows, kind) {
    const points = rows.slice(0, 14).reverse();
    if (points.length < 2) return null;
    const lows = points.map((r) => (kind === "blood_pressure" && r.value2 !== null ? Number(r.value2) : Number(r.value1)));
    const highs = points.map((r) => Number(r.value1));
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    const span = max - min || 1;
    const floor = min - span * 0.15;
    const scale = (v) => Math.round(((v - floor) * 100) / (max - floor || 1));
    return h("div", { class: "care-trend", role: "img", "aria-label": points.map((r) => `${dayWords(r.at.slice(0, 10))}: ${r.display}`).join(", ") },
      ...points.map((r, i) => {
        const top = scale(highs[i]);
        const bottom = kind === "blood_pressure" && r.value2 !== null ? scale(lows[i]) : 0;
        return h("div", { class: `care-trend-col${r.flagged ? " flagged" : ""}` },
          h("span", { class: "care-trend-value", text: kind === "blood_pressure" ? `${Number(r.value1)}/${Number(r.value2)}` : String(Number(r.value1)) }),
          h("span", { class: "care-trend-track" }, h("span", { class: "care-trend-bar", style: { bottom: `${bottom}%`, height: `${Math.max(3, top - bottom)}%` } })),
          h("span", { class: "care-trend-label", text: new Date(r.at).toLocaleDateString(undefined, { day: "numeric", month: "short" }) }));
      }));
  }

  function readingStats(rows, kind) {
    if (!rows.length) return null;
    const avg = (list) => Math.round((list.reduce((a, b) => a + b, 0) / list.length) * 10) / 10;
    const tops = rows.map((r) => Number(r.value1));
    const unit = rows[0].unit;
    const flagged = rows.filter((r) => r.flagged).length;
    if (kind === "blood_pressure") {
      const bottoms = rows.map((r) => Number(r.value2));
      return S.kpis([{ label: "Average", value: `${Math.round(avg(tops))}/${Math.round(avg(bottoms))}`, hint: unit }, { label: "Highest", value: `${Math.max(...tops)}/${bottoms[tops.indexOf(Math.max(...tops))]}` }, { label: "Outside the usual range", value: `${flagged} of ${rows.length}`, tone: flagged ? "warn" : "" }]);
    }
    return S.kpis([{ label: "Average", value: `${avg(tops)}`, hint: unit }, { label: "Lowest", value: `${Math.min(...tops)}` }, { label: "Highest", value: `${Math.max(...tops)}` }, { label: "Outside the usual range", value: `${flagged} of ${rows.length}`, tone: flagged ? "warn" : "" }]);
  }

  async function readingsTab(body, ctx) {
    const all = await api(`/readings${ctx.qs}`);
    const kinds = all.kinds;
    if (!kinds[readingKind]) readingKind = Object.keys(kinds)[0];
    const chips = h("div", { class: "care-chips", role: "group", "aria-label": "Kind of reading" }, ...Object.entries(kinds).map(([id, spec]) => h("button", { type: "button", class: "btn secondary sm", "aria-pressed": String(id === readingKind), onclick: () => { readingKind = id; ctx.rerender(); } }, h("span", { text: spec.label }), h("span", { class: "muted small", text: ` ${all.readings.filter((r) => r.kind === id).length || ""}` }))));
    const rows = all.readings.filter((r) => r.kind === readingKind);
    const list = rows.length ? h("ul", { class: "care-readings" }, ...rows.map((r) => h("li", { class: r.flagged ? "flagged" : "" },
      h("div", { class: "care-reading-main" }, h("strong", { text: r.display }), h("span", { class: "muted small", text: when(r.at) }), r.note ? h("span", { text: r.note }) : null, r.note_short ? h("span", { class: "care-hint", text: r.note_short }) : null),
      ctx.canEdit ? S.button("Delete", async () => { if (await S.confirmDelete(`this reading (${r.display})`)) { await api(`/readings/${r.id}`, { method: "DELETE" }); await done("Deleted."); ctx.rerender(); } }, { kind: "ghost sm" }) : null))) : Aira.ui.empty(`No ${kinds[readingKind].label.toLowerCase()} readings yet`, ctx.canEdit ? "Add one each time you measure. A chart appears after two." : "");
    const section = S.section(kinds[readingKind].label, chips,
      ctx.canEdit ? h("div", { class: "btn-row" }, big(`Add ${kinds[readingKind].label.toLowerCase()}`, () => addReading(readingKind, ctx.rerender, kinds)), S.button("Download all", () => Aira.download(`/api/care/readings?format=csv${ctx.qs ? `&${ctx.qs.slice(1)}` : ""}`, "readings.csv"), { kind: "ghost" })) : null,
      rows.length ? h("p", { class: "muted small", text: "The last 90 days" }) : null,
      readingStats(rows, readingKind),
      trend(rows, readingKind),
      list,
      note(all.not_advice));
    section.dataset.careOpen = `reading:${readingKind}`;
    body.append(section);
  }

  // ---------------------------------------------------------------- Diary
  async function diaryTab(body, ctx) {
    if (!ctx.canEdit) { body.append(Aira.ui.empty("A diary is private", "Only its owner can read it.")); return; }
    // An entry the search box found may be older than the month shown.
    const data = await api(String((ctx.ask && ctx.ask.open) || "").startsWith("diary:") ? "/diary?days=366" : "/diary");
    const form = S.form([
      { name: "day", label: "Day", type: "date", max: ctx.overview.today || localDay() },
      { name: "mood", label: "Mood", type: "select", options: MOODS.map(([v, e, l]) => [String(v), `${e} ${l}`]) },
      { name: "symptoms", label: "Anything you noticed", placeholder: "headache, tired, dizzy", hint: "Separate with commas." },
      { name: "note", label: "Note", type: "textarea", rows: 3 },
    ], { day: ctx.overview.today || localDay(), mood: "4" });
    body.append(S.section("Write today's entry", form.node, h("div", { class: "btn-row" }, big("Save", async () => { form.setError(""); try { await api("/diary", { method: "POST", body: form.read() }); await done("Saved."); ctx.rerender(); } catch (error) { form.setError(error.message); } }))),
      S.section("The last 30 days", data.entries.length ? h("ul", { class: "care-diary" }, ...data.entries.map((entry) => h("li", { dataset: { careOpen: `diary:${entry.day}` } }, h("span", { class: "care-mood-emoji", "aria-hidden": "true", text: entry.emoji }), h("div", {}, h("strong", { text: new Date(`${entry.day}T12:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) }), entry.symptoms.length ? h("div", { class: "muted", text: entry.symptoms.join(", ") }) : null, entry.note ? h("div", { text: entry.note }) : null), S.button("Delete", async () => { if (!(await S.confirmDelete("this diary entry"))) return; await api(`/diary?day=${encodeURIComponent(entry.day)}`, { method: "DELETE" }); ctx.rerender(); }, { kind: "ghost sm" })))) : Aira.ui.empty("Nothing written yet", "A line a day helps you and your doctor see how you are.")));
  }

  // ---------------------------------------------------------------- Appointments
  function editAppointment(appointment, rerender) {
    return S.edit({
      title: appointment ? "Change the appointment" : "Add an appointment",
      values: appointment ? { ...appointment, at: toInputLocal(appointment.at) } : {},
      fields: [
        { name: "title", label: "With", required: true, placeholder: "Dr Patel, the dentist" },
        { name: "at", label: "When", type: "datetime-local", required: true },
        { name: "place", label: "Where", placeholder: "Riverside Clinic, room 4" },
        { name: "purpose", label: "What for", placeholder: "Blood test results" },
        { name: "questions", label: "Questions to ask", type: "textarea", rows: 3 },
        { name: "notes", label: "Notes", type: "textarea", rows: 2 },
      ],
      onSave: async (values) => {
        const payload = { ...values, at: toIsoLocal(values.at) };
        if (appointment) await api(`/appointments/${appointment.id}`, { method: "PATCH", body: payload });
        else await api("/appointments", { method: "POST", body: payload });
        await done("Saved. Aira reminds you the day before and two hours before.");
        rerender();
      },
    });
  }

  async function appointmentsTab(body, ctx) {
    const data = await api(`/appointments${ctx.qs}`);
    const calendarPath = (a) => `/api/care/appointments/${a.id}/calendar${ctx.qs}`;
    const card = (a, upcoming) => h("article", { class: "card care-appt", dataset: { careOpen: `appointment:${a.id}` } }, h("h3", { text: a.title }), h("p", { class: "care-appt-when", text: when(a.at) }), a.place ? h("p", { text: a.place }) : null, a.purpose ? h("p", { class: "muted", text: a.purpose }) : null, a.questions ? h("p", { class: "suite-pre", text: a.questions }) : null,
      h("div", { class: "btn-row" },
        upcoming ? S.button("Add to my calendar", () => Aira.download(calendarPath(a), "appointment.ics"), { kind: "secondary sm" }) : null,
        ctx.canEdit ? S.button("Change", () => editAppointment(a, ctx.rerender), { kind: "secondary sm" }) : null,
        ctx.canEdit && !a.done ? S.button("Done", async () => { await api(`/appointments/${a.id}`, { method: "PATCH", body: { done: true } }); ctx.rerender(); }, { kind: "ghost sm" }) : null,
        ctx.canEdit ? S.button("Delete", async () => { if (await S.confirmDelete("this appointment")) { await api(`/appointments/${a.id}`, { method: "DELETE" }); ctx.rerender(); } }, { kind: "ghost sm" }) : null));
    body.append(S.section("Coming up", ctx.canEdit ? h("div", { class: "btn-row" }, big("Add an appointment", () => editAppointment(null, ctx.rerender))) : null, data.upcoming.length ? h("div", { class: "care-cards" }, ...data.upcoming.map((a) => card(a, true))) : Aira.ui.empty("No appointments coming up", ctx.canEdit ? "Add your next visit; Aira reminds you the day before and two hours before." : "")));
    // The last 12, and an older one the search box found.
    const asked = String((ctx.ask && ctx.ask.open) || "");
    const past = data.past.filter((a, i) => i < 12 || asked === `appointment:${a.id}`);
    if (past.length) body.append(S.section("Earlier", h("div", { class: "care-cards" }, ...past.map((a) => card(a, false)))));
  }

  // ---------------------------------------------------------------- Doctor report
  // A table that becomes one labelled block per row on a phone (app-care.css), so words never break apart.
  function reportTable(spec) {
    const node = S.table(spec);
    const labels = spec.columns.map((column) => column.label);
    for (const row of node.querySelectorAll("tbody tr")) [...row.children].forEach((cell, i) => cell.setAttribute("data-label", labels[i] || ""));
    node.classList.add("care-report-table");
    return node;
  }

  async function reportTab(body, ctx) {
    const filters = ctx.filters;
    const days = filters.reportDays || 30;
    const withDiary = ctx.canEdit && !!filters.reportDiary;
    const params = new URLSearchParams({ days: String(days) });
    if (ctx.qs) params.set("person", ctx.person.id);
    if (withDiary) params.set("diary", "1");
    const data = await api(`/report?${params}`);
    const r = data.report;
    const periods = h("div", { class: "care-chips", role: "group", "aria-label": "How far back" }, ...data.periods.map((d) => h("button", { type: "button", class: "btn secondary sm", "aria-pressed": String(d === days), onclick: () => { filters.reportDays = d; ctx.rerender(); } }, h("span", { text: `Last ${d} days` }))));
    const diaryBox = ctx.canEdit ? h("label", { class: "suite-check" }, h("input", { type: "checkbox", checked: withDiary, onchange: (event) => { filters.reportDiary = event.target.checked; ctx.rerender(); } }), h("span", { text: "Include my mood diary (it is private unless you choose this)" })) : null;
    const a = r.adherence;
    const paper = h("div", { class: "care-report" },
      h("h3", { text: `Health report: ${r.name}` }),
      h("p", { class: "muted", text: `${dayWords(r.from)} to ${dayWords(r.to)} · made ${when(r.made_at)}` }),
      h("dl", { class: "care-report-facts" },
        h("dt", { text: "Allergies" }), h("dd", { text: r.allergies || "None listed" }),
        h("dt", { text: "Conditions" }), h("dd", { text: r.conditions || "None listed" }),
        h("dt", { text: "Medicines taken on time" }), h("dd", { text: a.counted ? `${a.taken} of ${a.counted} doses (${a.percent}%), ${a.missed} missed, ${a.skipped} skipped` : "No doses due in this time" })),
      h("h4", { text: "Medicines" }),
      r.medicines.length ? reportTable({ columns: [{ key: "name", label: "Medicine", render: (m) => `${m.name} ${m.dose}`.trim() + (m.active ? "" : " (stopped)") }, { key: "schedule", label: "When" }, { key: "taken", label: "Taken", num: true, render: (m) => (m.schedule === "when needed" ? `${m.as_needed_taken} times` : m.taken) }, { key: "missed", label: "Missed", num: true }, { key: "percent", label: "On time", num: true, render: (m) => (m.percent === null ? "-" : `${m.percent}%`) }], rows: r.medicines }) : note("No medicines recorded."),
      h("h4", { text: "Readings" }),
      r.readings.length ? reportTable({ columns: [{ key: "label", label: "Reading" }, { key: "count", label: "How many", num: true }, { key: "average_display", label: "Average" }, { key: "lowest_display", label: "Lowest" }, { key: "highest_display", label: "Highest" }, { key: "flagged", label: "Outside usual range", num: true }], rows: r.readings }) : note("No readings in this time."),
      r.diary ? h("div", {}, h("h4", { text: "How I felt" }), h("p", { text: r.diary.days_written ? `${plural(r.diary.days_written, "day")} written; average mood ${r.diary.mood_average} of 5; ${plural(r.diary.low_days, "low day")}.` : "Nothing written in this time." }), r.diary.symptoms.length ? h("p", { text: `Noticed: ${r.diary.symptoms.map((s) => `${s.name} (${s.days} ${s.days === 1 ? "day" : "days"})`).join(", ")}` }) : null) : null,
      r.next_appointment ? h("div", {}, h("h4", { text: "Next appointment" }), h("p", { text: `${r.next_appointment.title}, ${when(r.next_appointment.at)}${r.next_appointment.place ? `, ${r.next_appointment.place}` : ""}` }), r.next_appointment.questions ? h("p", { class: "suite-pre", text: `Questions to ask:\n${r.next_appointment.questions}` }) : null) : null,
      note(r.not_advice));
    const csv = new URLSearchParams(params);
    csv.set("format", "csv");
    const parts = [S.section("What goes in",
      ctx.canEdit ? null : h("p", { class: "muted", text: `${r.name}'s medicines, readings and questions on one page (never their diary).` }),
      periods, diaryBox,
      h("div", { class: "btn-row" }, big("Print", () => printPart(paper)), S.button("Download", () => Aira.download(`/api/care/report?${csv}`, "health-report.csv"), { kind: "ghost" })),
      paper)];
    if (data.can_share) parts.push(S.section("Send it to your doctor", reportShare(data, ctx, days, withDiary)));
    body.append(...parts);
  }

  function reportShare(data, ctx, days, withDiary) {
    const box = h("div", { class: "suite-share" });
    const render = (link) => {
      if (!link) {
        box.replaceChildren(h("div", {}, h("strong", { text: "A link for your doctor" }), h("p", { class: "muted", text: `Anyone with the link can read this report for ${data.link_days} days; then it stops working. You can turn it off sooner.` })),
          S.button("Make a link", async () => { const made = await api("/report/link", { method: "POST", body: { days, include_diary: withDiary } }); render(made.link); }, { kind: "secondary" }));
        return;
      }
      const url = S.absolute(link.path);
      box.replaceChildren(
        h("div", { class: "suite-share-main" }, h("strong", { text: "A link for your doctor" }), h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "suite-share-url", text: url }), h("span", { class: "muted", text: `Last ${link.days} days${link.include_diary ? ", with your diary" : ""} · works until ${dayWords(link.expires_at.slice(0, 10))} · opened ${plural(link.views || 0, "time")}` })),
        h("div", { class: "btn-row" },
          S.button("Copy", () => S.copy(url), { kind: "secondary sm" }),
          S.button("Update to these settings", async () => { const made = await api("/report/link", { method: "POST", body: { days, include_diary: withDiary } }); render(made.link); await done("Updated; it works for another 14 days."); }, { kind: "ghost sm" }),
          S.button("Turn off", async () => { if (await Aira.confirm({ title: "Turn off the link", message: "Your doctor will no longer be able to open it.", confirmLabel: "Turn off", danger: true })) { await api("/report/link", { method: "DELETE" }); render(null); } }, { kind: "ghost sm" })));
    };
    render(data.link);
    return box;
  }

  // ---------------------------------------------------------------- Emergency card
  async function cardTab(body, ctx) {
    const data = await api(`/card${ctx.qs}`);
    const card = data.card;
    const contacts = [0, 1, 2].map((i) => card.contacts[i] || {});
    const fields = [
      { name: "blood_type", label: "Blood type", type: "select", options: data.blood_types.map((b) => [b, b || "Not set"]) },
      { name: "allergies", label: "Allergies", type: "textarea", rows: 2, placeholder: "Penicillin, peanuts" },
      { name: "conditions", label: "Conditions", type: "textarea", rows: 2, placeholder: "Diabetes, heart condition" },
      { name: "doctor", label: "Doctor" },
      { name: "doctor_phone", label: "Doctor's phone", type: "tel" },
      ...contacts.flatMap((_, i) => [{ type: "heading", label: `Emergency contact ${i + 1}` }, { name: `c${i}_name`, label: "Name" }, { name: `c${i}_relation`, label: "Relation", placeholder: "Daughter" }, { name: `c${i}_phone`, label: "Phone", type: "tel" }]),
      { name: "notes", label: "Anything else", type: "textarea", rows: 2 },
    ];
    const values = { ...card };
    contacts.forEach((c, i) => { values[`c${i}_name`] = c.name || ""; values[`c${i}_relation`] = c.relation || ""; values[`c${i}_phone`] = c.phone || ""; });
    const view = h("div", { class: "care-emergency" },
      h("h3", { text: `${card.name}${card.birthday ? ` · born ${card.birthday}` : ""}` }),
      Aira.ui.kv([["Blood type", card.blood_type || "Not set"], ["Allergies", card.allergies || "None listed"], ["Conditions", card.conditions || "None listed"], ["Medicines", card.medicines.map((m) => `${m.name} ${m.dose}`.trim()).join(", ") || "None"], ["Doctor", [card.doctor, card.doctor_phone].filter(Boolean).join(", ") || "Not set"], ...card.contacts.map((c) => [c.relation || "Contact", `${c.name}, ${c.phone}`]), ...(card.notes ? [["Notes", card.notes]] : [])]));
    const parts = [S.section(ctx.person && data.person.id !== ctx.me ? `${data.person.name}'s card` : "What helpers see", h("p", { class: "muted", text: "Share the link, or print it for your wallet or the fridge." }), view, h("div", { class: "btn-row" }, S.button("Print the card", () => printPart(view), { kind: "secondary" })))];
    if (data.can_edit) {
      const form = S.form(fields, values);
      parts.push(S.section("Change the card", form.node, h("div", { class: "btn-row" }, big("Save the card", async () => {
        form.setError("");
        const v = form.read();
        const payload = { blood_type: v.blood_type, allergies: v.allergies, conditions: v.conditions, doctor: v.doctor, doctor_phone: v.doctor_phone, notes: v.notes, contacts: [0, 1, 2].map((i) => ({ name: v[`c${i}_name`], relation: v[`c${i}_relation`], phone: v[`c${i}_phone`] })).filter((c) => c.name || c.phone) };
        try { await api(`/card${ctx.qs}`, { method: "POST", body: payload }); await done("Card saved."); ctx.rerender(); } catch (error) { form.setError(error.message); }
      }))));
    }
    if (data.can_share) {
      parts.push(S.share({ title: "Share the card", text: "Anyone with the link can see this card (only the card).", link: data.link, onCreate: async () => (await api(`/card/link${ctx.qs}`, { method: "POST", body: {} })).link, onRevoke: async () => { await api(`/card/link${ctx.qs}`, { method: "DELETE" }); } }));
    }
    if ((data.children || []).length) parts.push(S.section("Children's cards", h("div", { class: "btn-row" }, ...data.children.map((child) => S.button(`${child.emoji} ${child.name}`, () => nav("card", child.id), { kind: "secondary" })), ctx.person && ctx.qs ? S.button("My own card", () => nav("card", ""), { kind: "ghost" }) : null)));
    body.append(...parts);
  }

  // ---------------------------------------------------------------- Caregivers
  async function caregiversTab(body, ctx) {
    const data = await api("/caregivers");
    body.append(S.section("Your caregivers", h("p", { class: "muted", text: "People you trust to see your medicines, readings and appointments (not your diary). They are told if you miss a dose. They cannot change anything." }),
      data.caregivers.length ? h("ul", { class: "life-people" }, ...data.caregivers.map((c) => h("li", { class: "life-person", dataset: { careOpen: `caregiver:${c.id}` } }, h("span", { class: "life-face-small", "aria-hidden": "true", text: c.emoji || "✉️" }), h("div", {}, h("strong", { text: c.name || c.email }), h("span", { class: "muted small", text: ` ${c.email || "in your household"}${c.notify_missed ? " · told about missed doses" : ""}` })), S.button("Remove", async () => { if (await Aira.confirm({ title: `Remove ${c.name || c.email}?`, message: "They will no longer see your health.", confirmLabel: "Remove", danger: true })) { await api(`/caregivers/${c.id}`, { method: "DELETE" }); ctx.rerender(); } }, { kind: "ghost sm" })))) : Aira.ui.empty("No caregivers yet", "Add someone who helps you with your medicines."),
      h("div", { class: "btn-row" }, big("Add a caregiver", () => S.edit({ title: "Add a caregiver", fields: [
        { name: "profile_id", label: "Someone in the household", type: "select", options: [["", "Someone else (by email)"], ...data.candidates.map((p) => [p.id, `${p.emoji} ${p.name}`])] },
        { name: "name", label: "Their name (if not in the household)" },
        { name: "email", label: "Their email (if not in the household)", type: "email" },
        { name: "notify_missed", label: "Tell them when I miss a dose", type: "checkbox", value: true },
      ], values: { notify_missed: true }, onSave: async (values) => { await api("/caregivers", { method: "POST", body: values }); await done("Caregiver added."); ctx.rerender(); } })))));
    body.append(S.section("People you care for", data.caring_for.length ? h("ul", { class: "life-people" }, ...data.caring_for.map((p) => h("li", { class: "life-person" }, h("span", { class: "life-face-small", style: { background: p.color }, "aria-hidden": "true", text: p.emoji }), h("div", {}, h("strong", { text: p.name }), h("span", { class: p.missed_today ? "care-low" : "muted small", text: p.missed_today ? ` ${p.missed_today} missed today` : ` ${p.taken_today} of ${p.due_today} taken today` })), big(`See ${p.name}'s health`, () => nav("today", p.id), "secondary")))) : Aira.ui.empty("Nobody yet", "When someone makes you their caregiver, they appear here.")));
  }

  // ---------------------------------------------------------------- Care work (earning)
  function editClient(client, rerender) {
    return S.edit({
      title: client ? `Change ${client.name}` : "Add a client",
      values: client ? { ...client } : { active: true },
      fields: [
        { name: "name", label: "Name", required: true, placeholder: "Mrs Rao, or her son Arun who pays" },
        { name: "rate_cents", label: "Your rate for an hour", type: "money", hint: "What you charge for one hour of care." },
        { name: "email", label: "Email (to send statements)", type: "email" },
        { name: "phone", label: "Phone", type: "tel" },
        { name: "notes", label: "Notes", type: "textarea", rows: 2, placeholder: "Key under the mat, likes tea at 4" },
        ...(client ? [{ name: "active", label: "Still a client", type: "checkbox" }] : []),
      ],
      onSave: async (values) => {
        if (client) await api(`/work/clients/${client.id}`, { method: "PATCH", body: values });
        else await api("/work/clients", { method: "POST", body: values });
        await done("Saved.");
        rerender();
      },
    });
  }

  function editVisit(visit, clients, rerender, clientId = "", today = localDay()) {
    const active = clients.filter((c) => c.active || (visit && c.id === visit.client_id));
    if (!active.length) { Aira.toast("Add a client first.", "error"); return null; }
    const values = visit ? { ...visit, hours: Math.round((visit.minutes / 60) * 100) / 100 } : { client_id: clientId || active[0].id, day: today, hours: 1 };
    return S.edit({
      title: visit ? "Change the visit" : "Record a visit",
      values,
      fields: [
        { name: "client_id", label: "Client", type: "select", options: active.map((c) => [c.id, c.name]) },
        { name: "day", label: "Day", type: "date", required: true, max: today },
        { name: "start", label: "Started at (optional)", type: "time" },
        { name: "hours", label: "How long (hours)", type: "number", step: "0.25", min: 0.25, required: true, hint: "For example 1.5 for an hour and a half." },
        { name: "tasks", label: "What you did", placeholder: "medicines, lunch, a walk, shopping", hint: "Separate with commas. The client sees this." },
        ...(visit ? [{ name: "rate_cents", label: "Rate for an hour", type: "money" }] : []),
        { name: "notes", label: "Private notes", type: "textarea", rows: 2, hint: "Only you see these." },
      ],
      onSave: async (form) => {
        const { hours: length, ...rest } = form;
        if (!(Number(length) >= 0.1)) throw new Error("Enter how long the visit was, for example 1.5 for an hour and a half.");
        const payload = { ...rest, minutes: Math.round(Number(length) * 60) };
        if (visit) await api(`/work/visits/${visit.id}`, { method: "PATCH", body: payload });
        else await api("/work/visits", { method: "POST", body: payload });
        await done("Visit saved.");
        rerender();
      },
    });
  }

  // "Up to" is a day where the medicine times are read (the care zone), like a visit's day: the browser's own date
  // can be a day behind, which left a visit recorded "today" out of the statement.
  function billClient(client, rerender, today = localDay()) {
    return S.edit({
      title: `Bill ${client.name}`,
      values: { to: today },
      fields: [
        { name: "to", label: "Visits up to", type: "date", max: today, hint: `Every visit not billed yet, up to this day (${S.money(client.unbilled_cents)} so far).` },
        { name: "note", label: "A note on the statement (optional)", placeholder: "Thank you!" },
      ],
      submitLabel: "Make the statement",
      onSave: async (values) => {
        const made = await api("/work/statements", { method: "POST", body: { ...values, client_id: client.id } });
        await done(`Statement ${made.statement.number}: ${S.money(made.statement.total_cents)}.`);
        rerender();
      },
    });
  }

  function statementCard(st, ctx) {
    const email = st.email || null;
    const waiting = !!email && ["pending", "approved"].includes(email.status);
    const state = st.status === "open" && waiting ? "waiting" : st.status === "open" && email && email.status === "failed" ? "failed" : st.status;
    const tone = { open: "", waiting: "warn", failed: "bad", sent: "info", paid: "good" };
    const words = { open: "Not sent", waiting: email && email.status === "approved" ? "Email about to go" : "Email waiting for approval", failed: "Email did not go", sent: "Sent", paid: "Paid" };
    return h("article", { class: `card care-statement ${st.status}` },
      h("div", { class: "care-statement-head" }, h("h3", { text: `${st.number} · ${st.client}` }), S.pill(words[state] || state, { [String(words[state] || "").toLowerCase()]: tone[state] || "" })),
      h("p", { class: "care-statement-total", text: S.money(st.total_cents, st.currency) }),
      h("p", { class: "muted small", text: `${dayWords(st.period_from)} to ${dayWords(st.period_to)} · ${hours(st.minutes)}${st.paid_at ? ` · paid ${dayWords(st.paid_at.slice(0, 10))}` : ""}` }),
      st.client_says_paid_at && st.status !== "paid" ? h("p", { class: "care-good", text: `${st.client} says they paid (${when(st.client_says_paid_at)}). Check, then mark it paid.` }) : null,
      st.client_says_paid_at && st.status !== "paid" && st.client_paid_note ? h("p", { class: "care-note" }, h("span", { class: "muted small", text: "Their note: " }), h("span", { text: st.client_paid_note })) : null,
      waiting && st.status !== "paid" ? h("p", { class: "muted small" }, h("span", { text: email.status === "approved" ? "The email is approved and goes out with the next send. " : "The email waits for your approval. " }), h("a", { href: "#/hub?tab=outbox", text: "Open the outbox" })) : null,
      st.link ? h("p", { class: "small" }, h("a", { href: S.absolute(st.link.path), target: "_blank", rel: "noopener noreferrer", text: "Open the client's page" })) : null,
      h("div", { class: "btn-row" },
        st.status !== "paid" ? S.button("Mark paid", async () => { await api(`/work/statements/${st.id}/paid`, { method: "POST", body: { paid: true } }); await done(`${S.money(st.total_cents, st.currency)} received. It is in your earnings.`); ctx.rerender(); }, { kind: "sm" }) : S.button("Not paid after all", async () => { await api(`/work/statements/${st.id}/paid`, { method: "POST", body: { paid: false } }); ctx.rerender(); }, { kind: "ghost sm" }),
        S.button("Copy link", async () => { const made = await api(`/work/statements/${st.id}/link`, { method: "POST", body: {} }); await S.copy(S.absolute(made.link.path)); ctx.rerender(); }, { kind: "secondary sm" }),
        st.client_email && st.status !== "paid" && !waiting ? S.button(st.status === "sent" || state === "failed" ? "Email it again" : "Email it", async () => { const r = await api(`/work/statements/${st.id}/send`, { method: "POST", body: {} }); await done(r.already ? "An email for this statement is already waiting in the outbox." : "The email is in the Business hub outbox, waiting for your approval."); ctx.rerender(); }, { kind: "secondary sm" }) : null,
        st.status !== "paid" ? S.button("Delete", async () => { if (await Aira.confirm({ title: `Delete ${st.number}?`, message: "Its visits can be billed again. The client's link stops working.", confirmLabel: "Delete", danger: true })) { await api(`/work/statements/${st.id}`, { method: "DELETE" }); ctx.rerender(); } }, { kind: "ghost sm" }) : null));
  }

  async function workTab(body, ctx) {
    const filters = ctx.filters;
    const data = await api(`/work${filters.client ? `?client=${encodeURIComponent(filters.client)}` : ""}`);
    const sum = data.summary;
    ctx.clients = data.clients; // "+ New" > Record a care visit chooses among them
    const money = (cents) => S.money(cents, sum.currency);
    // The page's heading already says what Care work is for: this card is the month's money and the two main actions.
    body.append(S.section("This month",
      S.kpis([{ label: "Hours this month", value: String(sum.hours_this_month) }, { label: "Paid this month", value: money(sum.paid_this_month_cents), tone: sum.paid_this_month_cents ? "good" : "" }, { label: "Waiting to be paid", value: money(sum.waiting_cents), tone: sum.waiting_cents ? "warn" : "" }, { label: "Not billed yet", value: money(sum.unbilled_cents) }]),
      sum.payment_ready ? null : h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: "How will clients pay you?" }), h("span", { text: "Add a payment link or bank details in the Business hub profile; they appear on every statement." }), h("a", { class: "btn secondary sm", href: "#/hub", text: "Open the Business hub" })),
      h("div", { class: "btn-row" }, big("Record a visit", () => editVisit(null, data.clients, ctx.rerender, filters.client, ctx.overview.today)), S.button("Add a client", () => editClient(null, ctx.rerender), { kind: "secondary" }))));
    body.append(S.section("Clients", data.clients.length ? h("div", { class: "care-cards" }, ...data.clients.map((c) => h("article", { class: `card care-client ${c.active ? "" : "paused"}`, dataset: { careOpen: `client:${c.id}` } },
      h("h3", { text: c.name }),
      h("p", { class: "muted", text: `${money(c.rate_cents)} an hour${c.phone ? ` · ${c.phone}` : ""}` }),
      h("p", { text: c.unbilled_minutes ? `Not billed yet: ${hours(c.unbilled_minutes)}, ${money(c.unbilled_cents)}` : "Nothing to bill" }),
      c.active ? null : S.pill("past client"),
      h("div", { class: "btn-row" },
        c.active ? S.button("Record a visit", () => editVisit(null, data.clients, ctx.rerender, c.id, ctx.overview.today), { kind: "secondary sm" }) : null,
        c.unbilled_minutes ? S.button("Bill now", () => billClient(c, ctx.rerender, ctx.overview.today), { kind: "sm" }) : null,
        S.button("Change", () => editClient(c, ctx.rerender), { kind: "ghost sm" }),
        c.active
          ? S.button("Remove", async () => { if (!(await Aira.confirm({ title: `Remove ${c.name}?`, message: "A client with visits is kept as a past client, for your records.", confirmLabel: "Remove", danger: true }))) return; const r = await api(`/work/clients/${c.id}`, { method: "DELETE" }); await done(r.outcome === "archived" ? "Kept as a past client." : "Removed."); ctx.rerender(); }, { kind: "ghost sm" })
          : S.button("Delete", async () => { if (!(await Aira.confirm({ title: `Delete ${c.name} for good?`, message: "Their visits and statements are deleted too, and their links stop working. Money you marked paid stays in your earnings. Download the visits first if you want to keep them.", confirmLabel: "Delete for good", danger: true }))) return; await api(`/work/clients/${c.id}`, { method: "DELETE" }); await done(`${c.name} is deleted.`); ctx.rerender(); }, { kind: "ghost sm" }))))) : Aira.ui.empty("No clients yet", "Add the person you look after (or who pays you), with your rate for an hour.")));
    const clientPick = Aira.ui.select([["", "All clients"], ...data.clients.map((c) => [c.id, c.name])], filters.client || "", { "aria-label": "Client" });
    clientPick.addEventListener("change", () => { filters.client = clientPick.value; ctx.rerender(); });
    const csv = new URLSearchParams({ format: "csv" });
    if (filters.client) csv.set("client", filters.client);
    body.append(S.section("Visits (last 90 days)",
      h("div", { class: "care-filters" }, Aira.ui.field("Client", clientPick), S.button("Download all visits", () => Aira.download(`/api/care/work/visits?${csv}`, "care-visits.csv"), { kind: "ghost" })),
      data.visits.length ? h("ul", { class: "care-visits" }, ...data.visits.map((v) => h("li", {},
        h("div", { class: "care-visit-main" }, h("strong", { text: `${dayWords(v.day)}${v.start ? ` at ${v.start}` : ""} · ${v.client}` }), h("span", { text: `${hours(v.minutes)} · ${money(v.amount_cents)}${v.statement_id ? " · billed" : ""}` }), v.tasks.length ? h("span", { class: "muted small", text: v.tasks.join(", ") }) : null),
        v.statement_id ? null : h("div", { class: "btn-row" }, S.button("Change", () => editVisit(v, data.clients, ctx.rerender, "", ctx.overview.today), { kind: "ghost sm" }), S.button("Delete", async () => { if (await S.confirmDelete("this visit")) { await api(`/work/visits/${v.id}`, { method: "DELETE" }); ctx.rerender(); } }, { kind: "ghost sm" }))))) : Aira.ui.empty(filters.client ? "No visits for this client yet" : "No visits yet", "Record each visit after it happens: the day, how long and what you did.")));
    body.append(S.section("Statements", data.statements.length ? h("div", { class: "care-cards" }, ...data.statements.map((st) => statementCard(st, ctx))) : Aira.ui.empty("No statements yet", "When you have visits to bill, press 'Bill now' on the client.")));
    body.append(note("Aira does not take payments: clients pay you directly. When you mark a statement paid, it counts in your earnings and in the Business hub."));
  }

  // ---------------------------------------------------------------- search (the box across the top)
  const longDay = (day) => new Date(`${day}T12:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const viewingNow = () => { const r = Aira.router.current; return r && r.path[0] === "care" ? String(r.query.person || "") : ""; };
  const KINDS_FOUND = [["medicines", "Medicines"], ["appointments", "Appointments"], ["readings", "Readings"], ["diary", "Diary"], ["clients", "Care clients"], ["people", "People"]];
  const found = new Map(); // "<person>|<query>|<limit>" -> { at, promise }: the box asks again as you type
  function findRecords(query, limit, person) {
    const key = `${person}|${query.toLowerCase()}|${limit}`;
    const hit = found.get(key);
    if (hit && Date.now() - hit.at < 8000) return hit.promise;
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (person) params.set("person", person);
    const promise = api(`/search?${params}`, { silent: true }).then((data) => data.results).catch((error) => { found.delete(key); throw error; });
    found.set(key, { at: Date.now(), promise });
    if (found.size > 40) found.delete(found.keys().next().value);
    return promise;
  }
  // Each record the server found as { label, sub, icon, href }: the link opens its tab and shows it (?open=).
  function resultRows(results, person) {
    const who = person ? `&person=${encodeURIComponent(person)}` : "";
    const at = (tab, open) => `#/care?tab=${tab}&open=${encodeURIComponent(open)}${who}`;
    const line = (...parts) => parts.filter(Boolean).join(" · ");
    return {
      medicines: (results.medicines || []).map((m) => ({ label: `${m.name} ${m.dose || ""}`.trim(), sub: line("Medicine", m.as_needed ? "when needed" : (m.times || []).join(", "), m.active ? "" : "stopped"), icon: "care-pill", href: at("medicines", `medicine:${m.id}`) })),
      appointments: (results.appointments || []).map((a) => ({ label: a.title, sub: line("Appointment", when(a.at), a.place, a.upcoming ? "" : "earlier"), icon: "interviews", href: at("appointments", `appointment:${a.id}`) })),
      readings: (results.readings || []).map((r) => ({ label: r.label, sub: line("Readings", r.count ? plural(r.count, "reading") : "none yet"), icon: "care-pulse", href: at("readings", `reading:${r.kind}`) })),
      diary: (results.diary || []).map((e) => ({ label: `${e.emoji ? `${e.emoji} ` : ""}${longDay(e.day)}`, sub: line("Diary", e.note || e.symptoms.join(", ")), icon: "care-diary", href: at("diary", `diary:${e.day}`) })),
      clients: (results.clients || []).map((c) => ({ label: c.name, sub: line("Care client", c.unbilled_cents ? `${S.money(c.unbilled_cents)} not billed yet` : "", c.active ? "" : "past client"), icon: "care-earn", href: at("work", `client:${c.id}`) })),
      people: (results.people || []).map((p) => (p.role === "caring_for"
        ? { label: p.name, sub: "You look after them · see their health", icon: "family", href: `#/care?person=${encodeURIComponent(p.id)}` }
        : { label: p.name, sub: line("Your caregiver", p.email), icon: "family", href: at("caregivers", `caregiver:${p.id}`) })),
    };
  }

  // Enter in the search box (?tab=search&q=): everything that matches, by kind.
  async function searchTab(body, ctx) {
    if (!ctx.query) { body.append(S.section("", Aira.ui.empty("Type in the search box above", "Find a medicine, a doctor, a kind of reading, a day in your diary or a client."))); return; }
    const person = ctx.qs ? ctx.person.id : "";
    const rows = resultRows(await findRecords(ctx.query, 25, person), person);
    const groups = KINDS_FOUND.filter(([key]) => rows[key].length);
    if (!groups.length) {
      body.append(S.section("", Aira.ui.empty(`Nothing matches “${ctx.query}”`, "Try the first letters of a medicine, a doctor or a place.", ctx.canEdit ? h("div", { class: "btn-row" }, S.button("Add a medicine", () => editMedicine(null, ctx.rerender), { kind: "secondary" }), S.button("Add an appointment", () => editAppointment(null, ctx.rerender), { kind: "secondary" })) : null)));
      return;
    }
    for (const [key, title] of groups) {
      body.append(S.section(`${title} (${rows[key].length})`, h("ul", { class: "care-results" }, ...rows[key].map((row) => h("li", {},
        h("a", { class: "care-result", href: row.href }, h("span", { class: "care-result-icon", "aria-hidden": "true" }, Aira.icon(row.icon)), h("span", { class: "care-result-text" }, h("strong", { text: row.label }), h("span", { class: "muted small", text: row.sub }))))))));
    }
  }

  Aira.appSearch = Aira.appSearch || {};
  Aira.appSearch.care = {
    placeholder: "Search medicines, appointments, readings",
    // Up to six records, one of each kind in turn, so a long list of medicines does not hide the appointments.
    async suggest(query) {
      const person = viewingNow();
      const rows = resultRows(await findRecords(query, 4, person), person);
      const lanes = KINDS_FOUND.map(([key]) => rows[key]);
      const out = [];
      for (let round = 0; out.length < 6 && lanes.some((lane) => lane.length > round); round += 1) for (const lane of lanes) if (lane[round] && out.length < 6) out.push(lane[round]);
      return out;
    },
    run(query) { Aira.router.go(["care"], { tab: "search", q: query, person: viewingNow() }); },
  };

  // ---------------------------------------------------------------- "+ New"
  const grownUp = () => { const me = Aira.life && Aira.life.me; return !me || !me.age_group || ["adult", "senior"].includes(me.age_group); };
  // Each opens the same form as the button on its tab; Care work (earning) is for adults, like its tab.
  const NEW_TAB = { medicine: "medicines", reading: "readings", appointment: "appointments", diary: "diary", visit: "work" };
  const CREATE = {
    medicine: (ctx) => editMedicine(null, ctx.rerender),
    reading: (ctx) => chooseReading(ctx.overview.kinds || {}, ctx.rerender),
    appointment: (ctx) => editAppointment(null, ctx.rerender),
    diary: (ctx, panel) => { const field = panel.querySelector("[name=mood]"); if (field) { field.scrollIntoView({ block: "center" }); field.focus({ preventScroll: true }); } },
    visit: (ctx) => {
      if ((ctx.clients || []).some((c) => c.active)) return editVisit(null, ctx.clients, ctx.rerender, "", ctx.overview.today);
      Aira.toast("Add the person you look after first; then record your visits to them.", "info");
      return editClient(null, ctx.rerender);
    },
  };
  Aira.appNew = Aira.appNew || {};
  Aira.appNew.care = () => [
    { label: "Add a medicine", icon: "care-pill", href: "#/care?tab=medicines&new=medicine" },
    { label: "Add a reading", icon: "care-pulse", href: "#/care?tab=readings&new=reading" },
    { label: "Add an appointment", icon: "interviews", href: "#/care?tab=appointments&new=appointment" },
    { label: "Write in the diary", icon: "care-diary", href: "#/care?tab=diary&new=diary" },
    grownUp() ? { label: "Record a care visit", icon: "care-earn", href: "#/care?tab=work&new=visit" } : null,
  ].filter(Boolean);

  // What "+ New" or the search box asked for, once the tab is drawn: open the form, or show the record found.
  function answer(ask, panel, ctx) {
    if (ask.new) {
      if (ctx.canEdit && CREATE[ask.new] && (ask.new !== "visit" || ctx.overview.me_adult)) CREATE[ask.new](ctx, panel);
      return;
    }
    if (!ask.open) return;
    const node = [...panel.querySelectorAll("[data-care-open]")].find((item) => item.dataset.careOpen === ask.open);
    if (!node) { Aira.toast("That is not here any more.", "info"); return; }
    node.classList.add("care-found");
    node.tabIndex = -1;
    node.scrollIntoView({ block: "center" });
    node.focus({ preventScroll: true });
    node.addEventListener("blur", () => node.classList.remove("care-found"), { once: true });
  }

  // The left menu's folders link to #/care?tab=<id>; while you look at someone else's health they stay with them.
  function folderLinks(person) {
    if (!person) return;
    for (const link of document.querySelectorAll('.shell-subnav[data-section="care"] a.shell-sublink')) link.setAttribute("href", `#/care?tab=${encodeURIComponent(link.dataset.tab)}&person=${encodeURIComponent(person)}`);
  }

  // ---------------------------------------------------------------- the page
  const FILTERS = {}; // per page load: the chosen filters of each tab (medicine, status, report period, client)
  let pending = null; // what ?new= or ?open= asked for, kept while the address forgets it

  Aira.views.care = async (main, route) => {
    // Take ?new= and ?open= out of the address first, so Back or a reload does not ask again; the page drawn next
    // answers them.
    if (route.query.new || route.query.open) {
      pending = { new: String(route.query.new || ""), open: String(route.query.open || "") };
      const rest = { ...route.query };
      delete rest.new;
      delete rest.open;
      if (pending.new && !rest.tab) rest.tab = NEW_TAB[pending.new] || "";
      Aira.router.go(["care"], rest, { replace: true });
      return;
    }
    let ask = pending;
    pending = null;
    if (ask && ask.open.startsWith("reading:")) readingKind = ask.open.slice("reading:".length);
    const query = String(route.query.q || "").trim();
    const requested = route.query.tab === "search" || TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "today";
    const person = route.query.person || "";
    const qs = person ? `?person=${encodeURIComponent(person)}` : "";
    let zoneChecked = false;
    const render = async () => {
      let overview;
      try { overview = await api(`/overview${qs}`); } catch (error) { main.replaceChildren(Aira.ui.error(error, render)); return; }
      // The first time: medicine times follow this device's time zone unless the Business hub names one.
      const zone = browserZone();
      if (!zoneChecked && overview.can_edit && overview.timezone && overview.timezone.source === "default" && zone && zone !== "UTC") {
        zoneChecked = true;
        try { await api("/settings", { method: "POST", body: { timezone: zone }, silent: true }); overview = await api(`/overview${qs}`); } catch { /* keep UTC */ }
      }
      zoneChecked = true;
      const canEdit = overview.can_edit;
      const me = Aira.life && Aira.life.me ? Aira.life.me.id : "";
      const visible = TABS.filter((t) => (canEdit || !OWN_ONLY.includes(t.id)) && (t.id !== "work" || overview.me_adult));
      const tab = requested === "search" || visible.some((t) => t.id === requested) ? requested : "today";
      const ctx = { overview, canEdit, person: overview.person, qs, me, rerender: render, filters: FILTERS, ask, query };
      // The page is named after the open tab (the app's own name is in the bar above), like a mail app's folder.
      const meta = TABS.find((t) => t.id === tab);
      const title = tab === "search" ? (query ? `Results for “${query}”` : "Search") : meta.label;
      const subtitle = !canEdit ? `${overview.person.name}'s health. You are their caregiver: you can look, not change.`
        : tab === "search" ? "Medicines, appointments, readings, diary days, care clients and people that match." : meta.about;
      const body = S.page(main, { title, subtitle });
      document.title = `${title} · Health · Aira`;
      const away = OWN_ONLY.includes(tab) || tab === "search" ? "today" : tab; // where "Showing" and "Back to mine" go
      if (overview.caring_for.length || person) {
        const choose = Aira.ui.select([["", "Me"], ...overview.caring_for.map((p) => [p.id, `${p.emoji} ${p.name}`])], person, { "aria-label": "Whose health" });
        choose.addEventListener("change", () => nav(away, choose.value));
        body.append(h("div", { class: "care-whose" }, Aira.ui.field("Showing", choose)));
      }
      if (!canEdit) body.append(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `${overview.person.name}'s health` }), h("span", { text: "Read-only: only they can change it." }), S.button("Back to mine", () => nav(away, ""), { kind: "secondary sm" })));
      const strip = Aira.ui.tabs(visible, tab, (id) => nav(id, person));
      strip.classList.add("care-tabstrip"); // a phone's tabs; wider screens have them as the left menu's folders
      body.append(strip);
      if (Aira.shell && Aira.shell.tabs) { Aira.shell.tabs("care", visible, tab); folderLinks(person); }
      const panel = h("div", { class: "suite-body" });
      body.append(panel);
      try {
        if (tab === "medicines") await medicinesTab(panel, ctx);
        else if (tab === "readings") await readingsTab(panel, ctx);
        else if (tab === "diary") await diaryTab(panel, ctx);
        else if (tab === "appointments") await appointmentsTab(panel, ctx);
        else if (tab === "report") await reportTab(panel, ctx);
        else if (tab === "card") await cardTab(panel, ctx);
        else if (tab === "caregivers") await caregiversTab(panel, ctx);
        else if (tab === "work") await workTab(panel, ctx);
        else if (tab === "search") await searchTab(panel, ctx);
        else await todayTab(panel, ctx);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); return; }
      if (ask) { const asked = ask; ask = null; ctx.ask = null; answer(asked, panel, ctx); }
    };
    await render();
    // Today follows the clock (doses become due); Care work shows a client's "I have paid" without a reload.
    if (requested === "today" || requested === "work") S.live(render, 60000);
  };
})();

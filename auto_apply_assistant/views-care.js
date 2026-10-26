/* Aira — Health (#/care; app id "care"): medicines and reminders, readings, a mood diary, appointments, an emergency
 * card and caregivers, for one person at a time (life.js decides who). Made to be easy for older people: big
 * buttons with words, plain language, one thing per tab. Nothing here is medical advice.
 *
 * Tabs (?tab=): today (default), medicines, readings, diary, appointments, card, caregivers. ?person=<id> shows
 * someone who made you their caregiver, read-only. Everything goes through /api/care/* (aira/suite/care).
 * DOM is built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("care", path, opts);

  const TABS = [
    { id: "today", label: "Today" },
    { id: "medicines", label: "Medicines" },
    { id: "readings", label: "Readings" },
    { id: "diary", label: "Diary" },
    { id: "appointments", label: "Appointments" },
    { id: "card", label: "Emergency card" },
    { id: "caregivers", label: "Caregivers" },
  ];
  const STATUS_WORDS = { taken: "Taken", skipped: "Skipped", snoozed: "Later", upcoming: "Later today", due: "Take now", missed: "Missed" };
  const STATUS_TONES = { taken: "good", skipped: "", snoozed: "info", upcoming: "", due: "warn", missed: "bad" };
  const MOODS = [[1, "😢", "Very low"], [2, "🙁", "Low"], [3, "😐", "Okay"], [4, "🙂", "Good"], [5, "😄", "Great"]];
  const FORMS = ["tablet", "capsule", "liquid", "inhaler", "injection", "drops", "cream", "patch", "other"];
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const toIsoLocal = (value) => (value ? new Date(value).toISOString() : "");
  const toInputLocal = (iso) => { if (!iso) return ""; const d = new Date(iso); const pad = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");
  const big = (label, onClick, kind = "") => S.button(label, onClick, { kind: `${kind} care-big`.trim() });
  const note = (text) => h("p", { class: "muted small", text });

  function nav(tab, person) { Aira.router.go(["care"], { tab, person }); }

  async function done(message) { Aira.toast(message, "success"); if (Aira.life && Aira.life.tick) Aira.life.tick(); }

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

  async function todayTab(body, ctx) {
    const data = ctx.overview;
    const slots = data.slots || [];
    const a7 = data.adherence_7;
    body.append(S.kpis([
      { label: "Taken today", value: `${slots.filter((s) => s.status === "taken").length} of ${slots.length}`, tone: slots.some((s) => s.status === "missed") ? "warn" : "good" },
      { label: "Last 7 days", value: a7.percent === null ? "-" : `${a7.percent}%`, hint: a7.counted ? `${a7.taken} of ${a7.counted} doses taken` : "Starts once doses are due", tone: a7.percent !== null && a7.percent < 80 ? "warn" : "" },
      data.next_appointment ? { label: "Next appointment", value: data.next_appointment.title, hint: when(data.next_appointment.at) } : null,
    ]));
    for (const item of data.low_stock || []) body.append(h("div", { class: "cloud-banner warning", role: "status" }, h("strong", { text: "Time to refill" }), h("span", { text: `${item.name}: ${item.stock} left.` })));
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
        h("div", { class: "btn-row" }, S.button("Add a reading", () => addReading("blood_pressure", ctx.rerender), { kind: "ghost" }), h("a", { class: "btn ghost", href: "#/care?tab=diary", text: "Open the diary" }))));
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
        { name: "stock", label: "How many are left", type: "number", min: 0 },
        { name: "refill_at", label: "Remind me to refill at", type: "number", min: 0, hint: "When this many are left." },
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

  async function medicinesTab(body, ctx) {
    const [data, history] = await Promise.all([api(`/medicines${ctx.qs}`), api(`/doses${ctx.qs}${ctx.qs ? "&" : "?"}days=30`)]);
    body.append(S.section("Medicines",
      ctx.canEdit ? h("div", { class: "btn-row" }, big("Add a medicine", () => editMedicine(null, ctx.rerender))) : null,
      data.medicines.length ? h("div", { class: "care-cards" }, ...data.medicines.map((medicine) => h("article", { class: `card care-med ${medicine.active ? "" : "paused"}` },
        h("h3", { text: `${medicine.name} ${medicine.dose || ""}`.trim() }),
        h("p", { class: "muted", text: medicine.as_needed ? `When needed${medicine.min_gap_hours ? `, at least ${medicine.min_gap_hours} hours apart` : ""}` : `${(medicine.times || []).join(", ")} · ${(medicine.days || []).length ? medicine.days.map((d) => DAYS[d]).join(", ") : "every day"}` }),
        medicine.with_food ? h("p", { text: medicine.with_food }) : null,
        medicine.stock !== null && medicine.stock !== undefined ? h("p", { class: medicine.stock <= medicine.refill_at ? "care-low" : "muted", text: `${medicine.stock} left` }) : null,
        medicine.active ? null : S.pill("paused"),
        ctx.canEdit ? h("div", { class: "btn-row" }, S.button("Change", () => editMedicine(medicine, ctx.rerender), { kind: "secondary sm" }), S.button(medicine.active ? "Stop" : "Delete", async () => { if (!(await Aira.confirm({ title: `Stop ${medicine.name}?`, message: "It stops reminding you. Its history is kept.", confirmLabel: "Stop", danger: true }))) return; const r = await api(`/medicines/${medicine.id}`, { method: "DELETE" }); await done(r.outcome === "paused" ? "Stopped; its history is kept." : "Deleted."); ctx.rerender(); }, { kind: "ghost sm" })) : null))) : Aira.ui.empty("No medicines yet", ctx.canEdit ? "Add the medicines you take, and when." : "")));
    const a = history.adherence;
    body.append(S.section("The last 30 days",
      S.kpis([{ label: "Taken", value: a.taken }, { label: "Skipped", value: a.skipped }, { label: "Missed", value: a.missed, tone: a.missed ? "warn" : "" }, { label: "On track", value: a.percent === null ? "-" : `${a.percent}%` }]),
      S.table({ columns: [{ key: "slot_date", label: "Day" }, { key: "slot_time", label: "Time", render: (r) => r.slot_time || "when needed" }, { key: "medicine", label: "Medicine" }, { key: "status", label: "", render: (r) => S.pill(r.status) }], rows: history.doses.slice(0, 60), empty: "Nothing recorded yet." })));
  }

  // ---------------------------------------------------------------- Readings
  let readingKind = "blood_pressure";
  function addReading(kind, rerender, kinds) {
    const spec = (kinds || {})[kind] || { units: [""], fields: ["Value"], label: "Reading" };
    const fields = kind === "blood_pressure"
      ? [{ name: "value1", label: "Top number", type: "number", required: true }, { name: "value2", label: "Bottom number", type: "number", required: true }, { name: "value3", label: "Pulse (optional)", type: "number" }]
      : [{ name: "value1", label: spec.fields[0], type: "number", required: true, step: "any" }, ...(spec.units.length > 1 ? [{ name: "unit", label: "Unit", type: "select", options: spec.units }] : [])];
    return S.edit({ title: `Add ${String(spec.label).toLowerCase()}`, fields: [...fields, { name: "note", label: "Note (optional)" }], values: { unit: spec.units[0] }, onSave: async (values) => {
      const result = await api("/readings", { method: "POST", body: { kind, ...values } });
      if (result.reading.hint) Aira.modal({ title: result.reading.display, body: h("p", { text: result.reading.hint }), actions: [{ label: "OK", kind: "primary" }] });
      else await done(`Saved: ${result.reading.display}.`);
      rerender();
    } });
  }

  async function readingsTab(body, ctx) {
    const all = await api(`/readings${ctx.qs}`);
    const kinds = all.kinds;
    const chips = h("div", { class: "care-chips", role: "group", "aria-label": "Kind of reading" }, ...Object.entries(kinds).map(([id, spec]) => h("button", { type: "button", class: "btn secondary sm", "aria-pressed": String(id === readingKind), onclick: () => { readingKind = id; ctx.rerender(); } }, h("span", { text: spec.label }))));
    const rows = all.readings.filter((r) => r.kind === readingKind);
    const series = rows.slice(0, 14).reverse().map((r) => ({ label: new Date(r.at).toLocaleDateString(undefined, { day: "numeric", month: "short" }), value: r.value1 }));
    body.append(S.section("Readings", chips,
      ctx.canEdit ? h("div", { class: "btn-row" }, big(`Add ${kinds[readingKind].label.toLowerCase()}`, () => addReading(readingKind, ctx.rerender, kinds)), S.button("Download all", () => Aira.download(`/api/care/readings?format=csv${ctx.qs ? `&${ctx.qs.slice(1)}` : ""}`, "readings.csv"), { kind: "ghost" })) : null,
      series.length > 1 ? S.bars(series, { format: (v) => String(v) }) : null,
      S.table({ columns: [{ key: "at", label: "When", render: (r) => when(r.at) }, { key: "display", label: kinds[readingKind].label }, { key: "hint", label: "", render: (r) => (r.hint ? h("span", { class: "care-hint", text: r.hint }) : "") }, { key: "note", label: "Note" }, ...(ctx.canEdit ? [{ key: "id", label: "", render: (r) => S.button("Delete", async () => { await api(`/readings/${r.id}`, { method: "DELETE" }); ctx.rerender(); }, { kind: "ghost sm" }) }] : [])], rows, empty: "No readings of this kind yet." }),
      note(all.not_advice)));
  }

  // ---------------------------------------------------------------- Diary
  async function diaryTab(body, ctx) {
    if (!ctx.canEdit) { body.append(Aira.ui.empty("A diary is private", "Only its owner can read it.")); return; }
    const data = await api("/diary");
    const form = S.form([
      { name: "day", label: "Day", type: "date" },
      { name: "mood", label: "Mood", type: "select", options: MOODS.map(([v, e, l]) => [String(v), `${e} ${l}`]) },
      { name: "symptoms", label: "Anything you noticed", placeholder: "headache, tired, dizzy", hint: "Separate with commas." },
      { name: "note", label: "Note", type: "textarea", rows: 3 },
    ], { day: new Date().toISOString().slice(0, 10), mood: "4" });
    body.append(S.section("Write today's entry", form.node, h("div", { class: "btn-row" }, big("Save", async () => { form.setError(""); try { await api("/diary", { method: "POST", body: form.read() }); await done("Saved."); ctx.rerender(); } catch (error) { form.setError(error.message); } }))),
      S.section("The last 30 days", data.entries.length ? h("ul", { class: "care-diary" }, ...data.entries.map((entry) => h("li", {}, h("span", { class: "care-mood-emoji", "aria-hidden": "true", text: entry.emoji }), h("div", {}, h("strong", { text: new Date(`${entry.day}T12:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) }), entry.symptoms.length ? h("div", { class: "muted", text: entry.symptoms.join(", ") }) : null, entry.note ? h("div", { text: entry.note }) : null), S.button("Delete", async () => { await api(`/diary?day=${encodeURIComponent(entry.day)}`, { method: "DELETE" }); ctx.rerender(); }, { kind: "ghost sm" })))) : Aira.ui.empty("Nothing written yet", "A line a day helps you and your doctor see how you are.")));
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
    const card = (a) => h("article", { class: "card care-appt" }, h("h3", { text: a.title }), h("p", { class: "care-appt-when", text: when(a.at) }), a.place ? h("p", { text: a.place }) : null, a.purpose ? h("p", { class: "muted", text: a.purpose }) : null, a.questions ? h("p", { class: "suite-pre", text: a.questions }) : null,
      ctx.canEdit ? h("div", { class: "btn-row" }, S.button("Change", () => editAppointment(a, ctx.rerender), { kind: "secondary sm" }), !a.done ? S.button("Done", async () => { await api(`/appointments/${a.id}`, { method: "PATCH", body: { done: true } }); ctx.rerender(); }, { kind: "ghost sm" }) : null, S.button("Delete", async () => { if (await S.confirmDelete("this appointment")) { await api(`/appointments/${a.id}`, { method: "DELETE" }); ctx.rerender(); } }, { kind: "ghost sm" })) : null);
    body.append(S.section("Coming up", ctx.canEdit ? h("div", { class: "btn-row" }, big("Add an appointment", () => editAppointment(null, ctx.rerender))) : null, data.upcoming.length ? h("div", { class: "care-cards" }, ...data.upcoming.map(card)) : Aira.ui.empty("No appointments coming up", "")));
    if (data.past.length) body.append(S.section("Earlier", h("div", { class: "care-cards" }, ...data.past.slice(0, 12).map(card))));
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
      Aira.ui.kv([["Blood type", card.blood_type || "Not set"], ["Allergies", card.allergies || "None listed"], ["Conditions", card.conditions || "None listed"], ["Medicines", card.medicines.map((m) => `${m.name} ${m.dose}`.trim()).join(", ") || "None"], ["Doctor", [card.doctor, card.doctor_phone].filter(Boolean).join(", ")], ...card.contacts.map((c) => [c.relation || "Contact", `${c.name}, ${c.phone}`])]));
    const parts = [S.section(`Emergency card${ctx.person && data.person.id !== ctx.me ? `: ${data.person.name}` : ""}`, h("p", { class: "muted", text: "What helpers need to know if you cannot tell them: share the link or print it for your wallet or fridge." }), view)];
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
    if ((data.children || []).length) parts.push(S.section("Children's cards", h("div", { class: "btn-row" }, ...data.children.map((child) => S.button(`${child.emoji} ${child.name}`, () => nav("card", child.id), { kind: "secondary" })), ctx.person ? S.button("My own card", () => nav("card", ""), { kind: "ghost" }) : null)));
    body.append(...parts);
  }

  // ---------------------------------------------------------------- Caregivers
  async function caregiversTab(body, ctx) {
    const data = await api("/caregivers");
    body.append(S.section("Your caregivers", h("p", { class: "muted", text: "People you trust to see your medicines, readings and appointments (not your diary). They are told if you miss a dose. They cannot change anything." }),
      data.caregivers.length ? h("ul", { class: "life-people" }, ...data.caregivers.map((c) => h("li", { class: "life-person" }, h("span", { class: "life-face-small", "aria-hidden": "true", text: c.emoji || "✉️" }), h("div", {}, h("strong", { text: c.name || c.email }), h("span", { class: "muted small", text: ` ${c.email || "in your household"}${c.notify_missed ? " · told about missed doses" : ""}` })), S.button("Remove", async () => { if (await Aira.confirm({ title: `Remove ${c.name || c.email}?`, message: "They will no longer see your health.", confirmLabel: "Remove", danger: true })) { await api(`/caregivers/${c.id}`, { method: "DELETE" }); ctx.rerender(); } }, { kind: "ghost sm" })))) : Aira.ui.empty("No caregivers yet", "Add someone who helps you with your medicines."),
      h("div", { class: "btn-row" }, big("Add a caregiver", () => S.edit({ title: "Add a caregiver", fields: [
        { name: "profile_id", label: "Someone in the household", type: "select", options: [["", "Someone else (by email)"], ...data.candidates.map((p) => [p.id, `${p.emoji} ${p.name}`])] },
        { name: "name", label: "Their name (if not in the household)" },
        { name: "email", label: "Their email (if not in the household)", type: "email" },
        { name: "notify_missed", label: "Tell them when I miss a dose", type: "checkbox", value: true },
      ], values: { notify_missed: true }, onSave: async (values) => { await api("/caregivers", { method: "POST", body: values }); await done("Caregiver added."); ctx.rerender(); } })))));
    body.append(S.section("People you care for", data.caring_for.length ? h("ul", { class: "life-people" }, ...data.caring_for.map((p) => h("li", { class: "life-person" }, h("span", { class: "life-face-small", style: { background: p.color }, "aria-hidden": "true", text: p.emoji }), h("div", {}, h("strong", { text: p.name }), h("span", { class: p.missed_today ? "care-low" : "muted small", text: p.missed_today ? ` ${p.missed_today} missed today` : ` ${p.taken_today} of ${p.due_today} taken today` })), big(`See ${p.name}'s health`, () => nav("today", p.id), "secondary")))) : Aira.ui.empty("Nobody yet", "When someone makes you their caregiver, they appear here.")));
  }

  // ---------------------------------------------------------------- the page
  Aira.views.care = async (main, route) => {
    const tab = TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "today";
    const person = route.query.person || "";
    const qs = person ? `?person=${encodeURIComponent(person)}` : "";
    const render = async () => {
      let overview;
      try { overview = await api(`/overview${qs}`); } catch (error) { main.replaceChildren(Aira.ui.error(error, render)); return; }
      const canEdit = overview.can_edit;
      const me = Aira.life && Aira.life.me ? Aira.life.me.id : "";
      const ctx = { overview, canEdit, person: overview.person, qs, me, rerender: render };
      const body = S.page(main, { title: canEdit ? "Health" : `${overview.person.name}'s health`, subtitle: canEdit ? "Medicines, readings and appointments in one place." : "You are their caregiver: you can look, not change." });
      if (overview.caring_for.length || person) {
        const choose = Aira.ui.select([["", "Me"], ...overview.caring_for.map((p) => [p.id, `${p.emoji} ${p.name}`])], person, { "aria-label": "Whose health" });
        choose.addEventListener("change", () => nav(tab === "diary" ? "today" : tab, choose.value));
        body.append(h("div", { class: "care-whose" }, Aira.ui.field("Showing", choose)));
      }
      if (!canEdit) body.append(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `${overview.person.name}'s health` }), h("span", { text: "Read-only: only they can change it." }), S.button("Back to mine", () => nav(tab, ""), { kind: "secondary sm" })));
      body.append(Aira.ui.tabs(TABS.filter((t) => canEdit || !["diary", "caregivers"].includes(t.id)), tab, (id) => nav(id, person)));
      const panel = h("div", { class: "suite-body" });
      body.append(panel);
      try {
        if (tab === "medicines") await medicinesTab(panel, ctx);
        else if (tab === "readings") await readingsTab(panel, ctx);
        else if (tab === "diary") await diaryTab(panel, ctx);
        else if (tab === "appointments") await appointmentsTab(panel, ctx);
        else if (tab === "card") await cardTab(panel, ctx);
        else if (tab === "caregivers") await caregiversTab(panel, ctx);
        else await todayTab(panel, ctx);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
    if (tab === "today") S.live(render, 60000);
  };

  // Styles for Health only (the shared stylesheet is not edited by an app).
  if (!document.getElementById("careStyles")) {
    const style = document.createElement("style");
    style.id = "careStyles";
    style.textContent = [
      ".care-slots { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }",
      ".care-slot { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; align-items: center; padding: 14px; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); }",
      ".care-slot.due { border-color: var(--warning); box-shadow: inset 4px 0 0 var(--warning); }",
      ".care-slot.missed { border-color: var(--danger); box-shadow: inset 4px 0 0 var(--danger); }",
      ".care-slot.taken { opacity: 0.85; }",
      ".care-slot-time { font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; }",
      ".care-slot-main { display: grid; gap: 2px; justify-items: start; font-size: 16px; }",
      ".care-slot-actions { grid-column: 1 / -1; }",
      ".care-big { min-height: 48px; font-size: 16px; padding: 10px 20px; }",
      ".care-moods { display: flex; flex-wrap: wrap; gap: 10px; }",
      ".care-mood { display: grid; justify-items: center; gap: 4px; min-width: 88px; padding: 10px; border: 2px solid var(--line); border-radius: 14px; background: var(--surface); color: var(--ink); font: inherit; cursor: pointer; }",
      ".care-mood[aria-pressed='true'] { border-color: var(--brand); background: var(--brand-soft); }",
      ".care-mood-emoji { font-size: 32px; line-height: 1; }",
      ".care-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 12px; }",
      ".care-med h3, .care-appt h3 { margin: 0 0 4px; font-size: 17px; }",
      ".care-med.paused { opacity: 0.7; }",
      ".care-appt-when { font-size: 16px; font-weight: 700; margin: 0; }",
      ".care-low { color: var(--danger); font-weight: 700; }",
      ".care-hint { color: var(--warning); font-size: 13px; }",
      ".care-chips { display: flex; flex-wrap: wrap; gap: 6px; }",
      ".care-chips [aria-pressed='true'] { background: var(--brand-soft); border-color: var(--brand); }",
      ".care-diary { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }",
      ".care-diary li { display: flex; gap: 12px; align-items: flex-start; }",
      ".care-diary li > div { flex: 1; }",
      ".care-emergency { border: 3px solid var(--danger); border-radius: 14px; padding: 14px 16px; }",
      ".care-emergency h3 { margin: 0 0 8px; color: var(--danger); }",
      ".care-whose { max-width: 320px; }",
    ].join("\n");
    document.head.append(style);
  }
})();

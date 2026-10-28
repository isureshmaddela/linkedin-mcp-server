/* Aira — Health's public pages (aira/suite/care):
 * - "emergency_card": the card a person shares. Only the card: name, blood type, allergies, conditions, current
 *   medicines, doctor and emergency contacts. Large, plain and printable.
 * - "health_report": the report a person sends to their doctor for a few weeks (medicines taken on time, readings,
 *   and the diary only if they chose to include it).
 * - "care_statement": a carer's statement for a client: the visits, the total, how to pay, and "I have paid".
 * DOM is built with textContent only.
 */
"use strict";
(() => {
  const AiraPublic = window.AiraPublic;
  if (!AiraPublic) return;

  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const hoursText = (minutes) => { const m = Number(minutes) || 0; const hrs = Math.floor(m / 60); const rest = m % 60; return [hrs ? `${hrs} h` : "", rest ? `${rest} min` : ""].filter(Boolean).join(" ") || "0 min"; };

  AiraPublic.register("care", "emergency_card", (root, data, ctx) => {
    const { h } = ctx;
    const card = (data.page && data.page.card) || {};
    const row = (label, value, strong = false) => h("tr", {}, h("th", { scope: "row", text: label }), h("td", {}, strong ? h("strong", { text: value }) : h("span", { text: value })));
    const tel = (phone) => h("a", { href: `tel:${String(phone || "").replace(/[^0-9+]/g, "")}`, text: phone });
    document.title = `Emergency card · ${card.name || ""}`;
    root.append(...[
      h("section", { class: "pub-card", style: { borderColor: "#b42318", borderWidth: "3px" } },
        h("span", { class: "pub-badge bad", text: "Emergency information" }),
        h("h1", { text: card.name || "Emergency card" }),
        card.birthday ? h("p", { class: "pub-muted", text: `Born ${ctx.date(card.birthday)}` }) : null,
        h("table", { class: "pub-table" }, h("tbody", {},
          row("Blood type", card.blood_type || "Not known", true),
          row("Allergies", card.allergies || "None listed", true),
          row("Conditions", card.conditions || "None listed"),
          row("Medicines", (card.medicines || []).map((m) => `${m.name} ${m.dose || ""}`.trim()).join(", ") || "None listed"),
          card.notes ? row("Notes", card.notes) : null))),
      (card.contacts || []).length ? h("section", { class: "pub-card" }, h("h2", { text: "Who to call" }),
        ...card.contacts.map((c) => h("p", { style: { fontSize: "18px", margin: "0" } }, h("strong", { text: `${c.name}${c.relation ? ` (${c.relation})` : ""}: ` }), tel(c.phone)))) : null,
      card.doctor || card.doctor_phone ? h("section", { class: "pub-card" }, h("h2", { text: "Doctor" }), h("p", { style: { fontSize: "18px", margin: "0" } }, h("span", { text: `${card.doctor || ""} ` }), card.doctor_phone ? tel(card.doctor_phone) : null)) : null,
      h("div", { class: "pub-btn-row" }, ctx.button("Print this card", () => window.print(), "secondary")),
      h("p", { class: "pub-muted", text: card.updated_at ? `Last updated ${ctx.date(card.updated_at)}. In an emergency, call your local emergency number.` : "In an emergency, call your local emergency number." }),
    ].filter(Boolean));
  });

  AiraPublic.register("care", "health_report", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page || {};
    const r = page.report || {};
    const a = r.adherence || {};
    const table = (head, rows) => h("div", { style: { overflowX: "auto" } }, h("table", { class: "pub-table" }, h("thead", {}, h("tr", {}, ...head.map((label) => h("th", { scope: "col", text: label })))), h("tbody", {}, ...rows.map((cells) => h("tr", {}, ...cells.map((cell) => h("td", { text: String(cell) })))))));
    document.title = `Health report · ${r.name || ""}`;
    root.append(...[
      h("section", { class: "pub-card" },
        h("span", { class: "pub-badge", text: "Health report" }),
        h("h1", { text: r.name || "Health report" }),
        h("p", { class: "pub-muted", text: `${ctx.date(r.from)} to ${ctx.date(r.to)} · shared by ${r.name || "the patient"}; this link works until ${ctx.date(page.expires_at)}.` }),
        h("table", { class: "pub-table" }, h("tbody", {},
          h("tr", {}, h("th", { scope: "row", text: "Allergies" }), h("td", {}, h("strong", { text: r.allergies || "None listed" }))),
          h("tr", {}, h("th", { scope: "row", text: "Conditions" }), h("td", { text: r.conditions || "None listed" })),
          h("tr", {}, h("th", { scope: "row", text: "Medicines on time" }), h("td", { text: a.counted ? `${a.taken} of ${a.counted} doses (${a.percent}%), ${a.missed} missed, ${a.skipped} skipped` : "No doses due in this time" }))))),
      h("section", { class: "pub-card" }, h("h2", { text: "Medicines" }),
        (r.medicines || []).length ? table(["Medicine", "When", "Taken", "Missed", "On time"], r.medicines.map((m) => [`${m.name} ${m.dose || ""}`.trim() + (m.active ? "" : " (stopped)"), m.schedule, m.schedule === "when needed" ? `${m.as_needed_taken} times` : m.taken, m.missed, m.percent === null ? "-" : `${m.percent}%`])) : h("p", { class: "pub-muted", text: "No medicines recorded." })),
      h("section", { class: "pub-card" }, h("h2", { text: "Readings" }),
        (r.readings || []).length ? table(["Reading", "How many", "Average", "Lowest", "Highest", "Outside usual range"], r.readings.map((x) => [x.label, x.count, x.average_display, x.lowest_display, x.highest_display, x.flagged])) : h("p", { class: "pub-muted", text: "No readings in this time." })),
      r.diary ? h("section", { class: "pub-card" }, h("h2", { text: "How they felt" }),
        h("p", { text: r.diary.days_written ? `${plural(r.diary.days_written, "day")} written; average mood ${r.diary.mood_average} of 5; ${plural(r.diary.low_days, "low day")}.` : "Nothing written in this time." }),
        (r.diary.symptoms || []).length ? h("p", { text: `Noticed: ${r.diary.symptoms.map((s) => `${s.name} (${s.days} ${s.days === 1 ? "day" : "days"})`).join(", ")}` }) : null) : null,
      r.next_appointment && r.next_appointment.questions ? h("section", { class: "pub-card" }, h("h2", { text: "Questions for the next visit" }), h("p", { class: "pub-pre", text: r.next_appointment.questions })) : null,
      h("div", { class: "pub-btn-row" }, ctx.button("Print", () => window.print(), "secondary")),
      h("p", { class: "pub-muted", text: "Recorded by the patient in Aira Health. The numbers are what they wrote down; the notes are general information, not a diagnosis." }),
    ].filter(Boolean));
  });

  AiraPublic.register("care", "care_statement", (root, data, ctx) => {
    const { h } = ctx;
    const st = (data.page && data.page.statement) || {};
    const money = (cents) => ctx.money(cents, st.currency);
    document.title = `Statement ${st.number || ""}`;
    const paidBox = h("section", { class: "pub-card" });
    const renderPaid = (told) => {
      if (st.status === "paid") { paidBox.replaceChildren(h("span", { class: "pub-badge good", text: "Paid" }), h("p", { text: "Thank you: this statement is paid." })); return; }
      if (told || st.client_says_paid) { paidBox.replaceChildren(h("h2", { text: "Thank you" }), h("p", { text: `${st.carer || "Your carer"} has been told you paid. They will mark it once it arrives.` })); return; }
      const noteInput = h("input", { type: "text", maxlength: "200", placeholder: "Paid by bank transfer on Monday" });
      paidBox.replaceChildren(h("h2", { text: "Already paid?" }), ctx.field("A note for your carer (optional)", noteInput),
        h("div", { class: "pub-btn-row" }, ctx.button("I have paid", async () => { await ctx.action("paid", { note: noteInput.value }); renderPaid(true); })));
    };
    renderPaid(false);
    root.append(...[
      h("section", { class: "pub-card" },
        h("span", { class: `pub-badge ${st.status === "paid" ? "good" : ""}`.trim(), text: st.status === "paid" ? "Paid" : "Statement" }),
        h("h1", { text: `Care visits for ${st.client || ""}` }),
        h("p", { class: "pub-muted", text: `${st.number || ""} · from ${st.carer || "your carer"} · ${ctx.date(st.period_from)} to ${ctx.date(st.period_to)}` }),
        h("div", { style: { overflowX: "auto" } }, h("table", { class: "pub-table" },
          h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Day" }), h("th", { scope: "col", text: "How long" }), h("th", { scope: "col", text: "What was done" }), h("th", { scope: "col", text: "Amount" }))),
          h("tbody", {}, ...(st.visits || []).map((v) => h("tr", {}, h("td", { text: `${ctx.date(v.day)}${v.start ? ` ${v.start}` : ""}` }), h("td", { text: hoursText(v.minutes) }), h("td", { text: (v.tasks || []).join(", ") }), h("td", { text: money(v.amount_cents) })))))),
        h("p", { class: "pub-total", text: `Total: ${money(st.total_cents)} for ${hoursText(st.minutes)}` }),
        st.note ? h("p", { text: st.note }) : null),
      st.status === "paid" ? null : ctx.paymentBox(st.total_cents, st.number),
      paidBox,
    ].filter(Boolean));
  });
})();

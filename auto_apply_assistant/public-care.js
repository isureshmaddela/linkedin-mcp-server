/* Aira — Health: the emergency card a person shares (aira/suite/care, public kind "emergency_card").
 * Only the card: name, blood type, allergies, conditions, current medicines, doctor and emergency contacts. Large,
 * plain and printable. DOM is built with textContent only.
 */
"use strict";
(() => {
  const AiraPublic = window.AiraPublic;
  if (!AiraPublic) return;

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
})();

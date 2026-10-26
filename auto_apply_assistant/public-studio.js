/* Aira — Studio's public pages (aira/suite/studio): a proposal the client accepts, and an invoice the client pays.
 *
 * proposal: the introduction, scope and priced lines; the client accepts by typing their full name (a signature
 *           recorded with the time) or declines with an optional reason.
 * invoice:  the lines, what is still due and the business's own ways to be paid; the client can say they paid
 *           (with a reference) so the business knows to check. Nothing here marks an invoice paid.
 * Built with ctx.h and text only (public.js); styles from public.css.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  const STATUS = {
    sent: ["Awaiting your answer", ""], accepted: ["Accepted", "good"], declined: ["Declined", "bad"], expired: ["Expired", "bad"], draft: ["Draft", ""],
    partially_paid: ["Partly paid", ""], paid: ["Paid", "good"], overdue: ["Overdue", "bad"], void: ["Cancelled", "bad"],
  };
  const badge = (h, status) => { const [label, tone] = STATUS[status] || [status, ""]; return h("span", { class: `pub-badge ${tone}`, text: label }); };
  const party = (who) => [who.name, who.company].filter(Boolean).join(", ");

  function lines(ctx, doc) {
    const { h, money } = ctx;
    const rows = doc.items.map((item) => h("tr", {}, h("td", { text: item.description }), h("td", { class: "num", text: item.quantity }), h("td", { class: "num", text: money(item.unit_cents) }), h("td", { class: "num", text: money(item.amount_cents) })));
    const foot = [["Subtotal", money(doc.subtotal_cents)]];
    if (doc.discount_cents) foot.push(["Discount", `-${money(doc.discount_cents)}`]);
    if (doc.tax_rate_bp) foot.push([`${doc.tax_label || "Tax"} (${doc.tax_rate_bp / 100}%)`, money(doc.tax_cents)]);
    return h("div", { style: { overflowX: "auto" } },
      h("table", { class: "pub-table" },
        h("thead", {}, h("tr", {}, h("th", { text: "Description" }), h("th", { class: "num", text: "Qty" }), h("th", { class: "num", text: "Price" }), h("th", { class: "num", text: "Amount" }))),
        h("tbody", {}, ...rows),
        h("tfoot", {}, ...foot.map(([label, value]) => h("tr", {}, h("td", { colspan: "3", text: label }), h("td", { class: "num", text: value }))),
          h("tr", {}, h("td", { colspan: "3" }, h("strong", { text: "Total" })), h("td", { class: "num pub-total", text: money(doc.total_cents) })))));
  }

  P.register("studio", "proposal", (root, data, ctx) => {
    const { h, date, datetime } = ctx;
    const p = data.page;
    const answer = h("section", { class: "pub-card" });
    const renderAnswer = (proposal) => {
      if (proposal.status === "accepted") { answer.replaceChildren(h("p", { class: "pub-notice good", text: `Accepted by ${proposal.accepted_name}${proposal.accepted_at ? ` on ${datetime(proposal.accepted_at)}` : ""}. Thank you, we will be in touch about next steps.` })); return; }
      if (proposal.status === "declined") { answer.replaceChildren(h("p", { class: "pub-notice", text: "You declined this proposal. Thank you for letting us know." })); return; }
      if (proposal.status === "expired") { answer.replaceChildren(h("p", { class: "pub-notice bad", text: `This proposal expired on ${date(proposal.valid_until)}. Ask us for an updated one.` })); return; }
      const name = h("input", { type: "text", autocomplete: "name", maxlength: "120" });
      const agree = h("input", { type: "checkbox" });
      const reason = h("textarea", { rows: 3, maxlength: "1000" });
      const declineBox = h("div", { class: "pub-form", hidden: true }, ctx.field("Anything we should know? (optional)", reason), h("div", { class: "pub-btn-row" }, ctx.button("Decline proposal", async () => {
        const result = await ctx.action("decline", { reason: reason.value });
        renderAnswer(result.proposal);
      }, "ghost")));
      answer.replaceChildren(
        h("h2", { text: "Accept this proposal" }),
        h("div", { class: "pub-form" },
          ctx.field("Your full name", name, "Typing your name is your signature."),
          h("label", { style: { display: "flex", gap: "8px", alignItems: "flex-start" } }, agree, h("span", { text: `I accept this proposal for ${ctx.money(p.total_cents)} on behalf of ${party(p.prepared_for) || "my organisation"}.` })),
          h("div", { class: "pub-btn-row" },
            ctx.button("Accept proposal", async () => {
              if (!name.value.trim()) { name.focus(); throw new Error("Type your full name to accept."); }
              if (!agree.checked) throw new Error("Tick the box to confirm.");
              const result = await ctx.action("accept", { name: name.value.trim(), agree: true });
              renderAnswer(result.proposal);
              ctx.notice("Thank you. The proposal is accepted.", "good");
            }),
            h("button", { type: "button", class: "pub-btn secondary", text: "Decline", onclick: () => { declineBox.hidden = !declineBox.hidden; } }))),
        declineBox);
    };
    root.append(
      h("div", {}, badge(h, p.status), h("h1", { text: p.title }), h("p", { class: "pub-muted", text: `Proposal ${p.number}${party(p.prepared_for) ? ` · prepared for ${party(p.prepared_for)}` : ""} · valid until ${date(p.valid_until)}` })),
      p.intro ? h("section", { class: "pub-card" }, h("p", { class: "pub-pre", text: p.intro })) : null,
      p.scope ? h("section", { class: "pub-card" }, h("h2", { text: "Scope" }), h("p", { class: "pub-pre", text: p.scope })) : null,
      h("section", { class: "pub-card" }, h("h2", { text: "Investment" }), lines(ctx, p)),
      answer,
      h("div", { class: "pub-btn-row" }, h("button", { type: "button", class: "pub-btn ghost", text: "Print or save as PDF", onclick: () => window.print() })),
    );
    renderAnswer(p);
  });

  P.register("studio", "invoice", (root, data, ctx) => {
    const { h, money, date } = ctx;
    const inv = data.page;
    const due = inv.balance_cents > 0 && inv.status !== "void";
    const told = h("section", { class: "pub-card" });
    const renderTold = (already) => {
      if (!due) { told.hidden = true; return; }
      if (already) { told.replaceChildren(h("p", { class: "pub-notice good", text: "Thank you. We have your note and will confirm once the payment arrives." })); return; }
      const reference = h("input", { type: "text", maxlength: "120", placeholder: "Transaction or reference number" });
      const note = h("textarea", { rows: 2, maxlength: "500" });
      told.replaceChildren(h("h2", { text: "Already paid?" }), h("div", { class: "pub-form" }, ctx.field("Payment reference (optional)", reference), ctx.field("Note (optional)", note),
        h("div", { class: "pub-btn-row" }, ctx.button("Let them know I paid", async () => {
          await ctx.action("notify_paid", { reference: reference.value.trim(), note: note.value.trim() });
          renderTold(true);
        }, "secondary"))));
    };
    root.append(
      h("div", {}, badge(h, inv.status), h("h1", { text: `Invoice ${inv.number}` }),
        h("p", { class: "pub-muted", text: [party(inv.bill_to) && `Billed to ${party(inv.bill_to)}`, `Issued ${date(inv.issue_date)}`, `Due ${date(inv.due_date)}`].filter(Boolean).join(" · ") })),
      h("section", { class: "pub-card" },
        h("div", { style: { display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", alignItems: "baseline" } },
          h("span", { class: "pub-muted", text: inv.status === "paid" ? "Paid in full" : inv.status === "void" ? "This invoice was cancelled" : "Amount due" }),
          h("span", { class: "pub-total", text: money(inv.status === "void" ? 0 : inv.balance_cents) })),
        inv.paid_cents && due ? h("p", { class: "pub-muted", text: `${money(inv.paid_cents)} of ${money(inv.total_cents)} already received. Thank you.` }) : null),
      due ? ctx.paymentBox(inv.balance_cents, inv.number) : null,
      h("section", { class: "pub-card" }, h("h2", { text: "Details" }), lines(ctx, inv)),
      inv.notes ? h("section", { class: "pub-card" }, h("p", { class: "pub-pre", text: inv.notes })) : null,
      told,
      h("div", { class: "pub-btn-row" }, h("button", { type: "button", class: "pub-btn ghost", text: "Print or save as PDF", onclick: () => window.print() })),
    );
    renderTold(inv.notified);
  });
})();

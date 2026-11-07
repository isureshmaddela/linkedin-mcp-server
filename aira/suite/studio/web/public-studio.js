/* Aira — Studio's public pages (aira/suite/studio): a proposal the client accepts, and an invoice the client pays.
 *
 * proposal: the introduction, scope and priced lines; the client accepts by typing their full name (a signature
 *           recorded with the time) or declines with an optional reason.
 * invoice:  the lines, what is still due and the business's own ways to be paid; the client can say they paid
 *           (with a reference) so the business knows to check. Nothing here marks an invoice paid.
 * A proposal can ask for a deposit: the page says how much before the client accepts, and once they accept it shows
 * the deposit invoice with the business's ways to pay. An invoice can take off a deposit already invoiced.
 * Built with ctx.h and text only (public.js); styles from public.css. Element.append turns null into the text
 * "null", so optional parts are filtered out before they are appended.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  const STATUS = {
    sent: ["Awaiting your answer", ""], accepted: ["Accepted", "good"], declined: ["Declined", "bad"], expired: ["Expired", "bad"], draft: ["Draft", ""],
    partially_paid: ["Partly paid", ""], paid: ["Paid", "good"], overdue: ["Overdue", "bad"], void: ["Cancelled", "bad"],
  };
  // an invoice that was sent and is not paid yet is waiting for money, not for an answer
  const INVOICE_STATUS = { ...STATUS, sent: ["Not paid yet", ""] };
  const badge = (h, status, labels = STATUS) => { const [label, tone] = labels[status] || [status, ""]; return h("span", { class: `pub-badge ${tone}`, text: label }); };
  const party = (who) => [who.name, who.company].filter(Boolean).join(", ");
  const percent = (bp) => `${Number(bp || 0) / 100}%`;

  function lines(ctx, doc) {
    const { h, money } = ctx;
    const rows = doc.items.map((item) => h("tr", {},
      h("td", {}, h("div", { text: item.description }), h("div", { class: "pub-muted", text: `${item.quantity} × ${money(item.unit_cents)}` })),
      h("td", { class: "num", style: { whiteSpace: "nowrap", verticalAlign: "top" }, text: money(item.amount_cents) })));
    const foot = [["Subtotal", money(doc.subtotal_cents)]];
    if (doc.discount_cents) foot.push(["Discount", `-${money(doc.discount_cents)}`]);
    if (doc.tax_rate_bp) foot.push([`${doc.tax_label || "Tax"} (${doc.tax_rate_bp / 100}%)`, money(doc.tax_cents)]);
    if (doc.credit_cents) foot.push([doc.credit_label || "Deposit already invoiced", `-${money(doc.credit_cents)}`]);
    return h("div", { style: { overflowX: "auto" } },
      h("table", { class: "pub-table" },
        h("thead", {}, h("tr", {}, h("th", { text: "Description" }), h("th", { class: "num", text: "Amount" }))),
        h("tbody", {}, ...rows),
        h("tfoot", {}, ...foot.map(([label, value]) => h("tr", {}, h("td", { text: label }), h("td", { class: "num", style: { whiteSpace: "nowrap" }, text: value }))),
          h("tr", {}, h("td", {}, h("strong", { text: "Total" })), h("td", { class: "num pub-total", style: { whiteSpace: "nowrap" }, text: money(doc.total_cents) })))));
  }

  P.register("studio", "proposal", (root, data, ctx) => {
    const { h, date, datetime } = ctx;
    const p = data.page;
    const head = h("span", {}, badge(h, p.status));
    const answer = h("section", { class: "pub-card" });
    const depositCard = (proposal) => {
      const invoice = proposal.deposit_invoice;
      if (!invoice) return null;
      const open = invoice.balance_cents > 0 && invoice.status !== "void";
      return h("div", { class: "pub-form" }, ...[
        h("h2", { text: open ? "Pay your deposit to get started" : "Deposit" }),
        h("p", { class: "pub-muted", text: open ? `Invoice ${invoice.number} for ${ctx.money(invoice.balance_cents)} is ready. It is taken off the final invoice.` : invoice.status === "paid" ? `Thank you, the deposit (invoice ${invoice.number}) is paid.` : `Invoice ${invoice.number}.` }),
        open ? ctx.paymentBox(invoice.balance_cents, invoice.number) : null,
        invoice.path ? h("a", { class: "pub-btn secondary", href: invoice.path, text: "Open the deposit invoice" }) : null,
      ].filter(Boolean));
    };
    const renderAnswer = (proposal) => {
      head.replaceChildren(badge(h, proposal.status));
      if (proposal.status === "accepted") { answer.replaceChildren(...[h("p", { class: "pub-notice good", text: `Accepted by ${proposal.accepted_name}${proposal.accepted_at ? ` on ${datetime(proposal.accepted_at)}` : ""}. Thank you, we will be in touch about next steps.` }), depositCard(proposal)].filter(Boolean)); return; }
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
          p.deposit_cents ? h("p", { class: "pub-muted", text: `A deposit of ${percent(p.deposit_bp)} (${ctx.money(p.deposit_cents)}) is due when you accept. You will get an invoice for it right away, and it is taken off the final invoice.` }) : null,
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
    root.append(...[
      h("div", {}, head, h("h1", { text: p.title }), h("p", { class: "pub-muted", text: `Proposal ${p.number}${party(p.prepared_for) ? ` · prepared for ${party(p.prepared_for)}` : ""} · valid until ${date(p.valid_until)}` })),
      p.intro ? h("section", { class: "pub-card" }, h("p", { class: "pub-pre", text: p.intro })) : null,
      p.scope ? h("section", { class: "pub-card" }, h("h2", { text: "Scope" }), h("p", { class: "pub-pre", text: p.scope })) : null,
      h("section", { class: "pub-card" }, h("h2", { text: "Investment" }), lines(ctx, p), p.deposit_cents ? h("p", { class: "pub-muted", text: `Deposit when accepted: ${percent(p.deposit_bp)} (${ctx.money(p.deposit_cents)}).` }) : null),
      answer,
      h("div", { class: "pub-btn-row" }, h("button", { type: "button", class: "pub-btn ghost", text: "Print or save as PDF", onclick: () => window.print() })),
    ].filter(Boolean));
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
    root.append(...[
      h("div", {}, badge(h, inv.status, INVOICE_STATUS), h("h1", { text: inv.deposit ? `Deposit invoice ${inv.number}` : `Invoice ${inv.number}` }),
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
    ].filter(Boolean));
    renderTold(inv.notified);
  });
})();

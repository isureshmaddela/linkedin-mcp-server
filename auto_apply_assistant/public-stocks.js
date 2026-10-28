/* Aira — Stocks public pages (AiraPublic.register): an analyst's page of stock ideas (aira/suite/stocks).
 *
 * Anyone with the link sees the analyst's track record (every closed idea, paid ones too), the free open ideas and
 * how many paid ideas are open. A follower can ask to subscribe (the "subscribe" action answers with a payment
 * reference and the analyst's own payment link), and a paying subscriber opens the paid ideas with their email and
 * access code (the "paid" query; the code travels in a header). Rises and falls carry + and - signs and arrows.
 */
"use strict";
(() => {
  if (!window.AiraPublic) return;
  const signedPct = (value) => (value === null || value === undefined ? "-" : `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}%`);
  const arrow = (value) => (value > 0 ? "▲" : value < 0 ? "▼" : "■");
  const STATES = { open: "Open", target_hit: "Target hit", stopped: "Stopped out", expired: "Expired", closed: "Closed" };

  window.AiraPublic.register("stocks", "analyst", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const a = page.analyst;
    const r = page.record;
    const money = (cents, currency) => ctx.money(cents, currency);
    const tone = (value) => ({ color: value > 0 ? "var(--pub-good)" : value < 0 ? "var(--pub-bad)" : "inherit", fontWeight: "700" });
    const ret = (value) => (value === null || value === undefined ? h("span", { text: "-" }) : h("span", { style: tone(value) }, h("span", { "aria-hidden": "true", text: `${arrow(value)} ` }), h("span", { text: signedPct(value) })));
    /* A button whose error shows next to it (inside its own form), not in the banner at the top of a long page,
     * and is cleared by the next try. */
    const inlineButton = (label, out, onClick, kind) => ctx.button(label, async (event) => {
      out.replaceChildren();
      try { await onClick(event); } catch (error) {
        out.replaceChildren(h("p", { class: "pub-notice bad", role: "alert", text: error && error.message ? error.message : String(error) }));
        out.scrollIntoView({ block: "nearest" });
      }
    }, kind);
    const ideaTable = (ideas, closed) => {
      if (!ideas.length) return h("p", { class: "pub-muted", text: closed ? "No closed ideas yet." : "No free ideas are open right now." });
      return h("div", { style: { overflowX: "auto" } }, h("table", { class: "pub-table" },
        h("thead", {}, h("tr", {}, ...["Idea", "Entry", "Target / stop", closed ? "Result" : "Time left", "Return"].map((label) => h("th", { scope: "col", text: label })))),
        h("tbody", {}, ...ideas.map((i) => h("tr", {},
          h("td", {}, h("strong", { text: `${i.side === "buy" ? "Buy" : "Sell"} ${i.symbol}` }), h("div", { class: "pub-muted pub-hint", text: `${i.name} · ${ctx.date(i.opened_on)}${i.access === "paid" ? " · paid idea" : ""}` }), i.reasoning && !closed ? h("div", { class: "pub-hint", text: i.reasoning }) : null),
          h("td", { text: money(i.entry_cents, i.currency) }),
          h("td", { text: `${money(i.target_cents, i.currency)} / ${money(i.stop_cents, i.currency)}` }),
          h("td", { text: closed ? `${STATES[i.status] || i.status} · ${ctx.date(i.closed_on)}` : `${i.days_left} days` }),
          h("td", {}, ret(i.return_pct)))))));
    };

    root.append(
      h("section", { class: "pub-card" },
        h("h1", { text: `${a.display_name}: stock ideas` }),
        a.bio ? h("p", { text: a.bio }) : null,
        h("div", { class: "pub-grid" },
          h("div", { class: "pub-item" }, h("span", { class: "pub-muted", text: "Ideas published" }), h("strong", { class: "pub-price", text: String(r.ideas) }), h("span", { class: "pub-muted pub-hint", text: `${r.open} open, ${r.closed} closed` })),
          h("div", { class: "pub-item" }, h("span", { class: "pub-muted", text: "Win rate" }), h("strong", { class: "pub-price", text: r.win_rate_pct === null ? "-" : `${r.win_rate_pct}%` }), h("span", { class: "pub-muted pub-hint", text: r.closed ? `${r.wins} of ${r.closed} closed ideas made money` : "No closed ideas yet" })),
          h("div", { class: "pub-item" }, h("span", { class: "pub-muted", text: "Average return" }), h("strong", { class: "pub-price" }, ret(r.average_return_pct)), h("span", { class: "pub-muted pub-hint", text: r.best_pct === null ? "" : `Best ${signedPct(r.best_pct)}, worst ${signedPct(r.worst_pct)}` })),
          h("div", { class: "pub-item" }, h("span", { class: "pub-muted", text: "Followers paying" }), h("strong", { class: "pub-price", text: String(page.subscribers) })))),
      h("p", { class: "pub-notice", role: "note", text: `${page.disclaimer} Monosage tracks every idea automatically from the day it is published.` }),
      h("section", { class: "pub-card" }, h("h2", { text: "Open free ideas" }), ideaTable(page.free, false)));

    const paidCard = h("section", { class: "pub-card" });
    const showPaid = (ideas, until) => {
      paidCard.replaceChildren(h("h2", { text: "Your paid ideas" }), h("p", { class: "pub-muted", text: `Your subscription runs until ${ctx.date(until)}.` }), ideaTable(ideas, false));
    };
    const takesMoney = a.accepting && a.price_cents > 0;
    paidCard.append(
      h("h2", { text: "Paid ideas" }),
      h("p", { text: page.paid_open ? `${page.paid_open} paid idea${page.paid_open === 1 ? " is" : "s are"} open now.` : "No paid ideas are open right now." }),
      takesMoney ? h("p", {}, h("strong", { text: `${money(a.price_cents, a.currency)} a month. ` }), h("span", { class: "pub-muted", text: "You pay the analyst directly; they confirm your payment and you get an access code by email." })) : h("p", { class: "pub-muted", text: "This analyst is not taking new subscribers." }));
    if (takesMoney) {
      const name = h("input", { type: "text", maxlength: 80, autocomplete: "name", required: true });
      const email = h("input", { type: "email", maxlength: 254, autocomplete: "email", required: true });
      const note = h("input", { type: "text", maxlength: 300 });
      const result = h("div", { "aria-live": "polite" });
      paidCard.append(h("form", { class: "pub-form", onsubmit: (event) => event.preventDefault() },
        h("h3", { text: "Subscribe" }),
        ctx.field("Your name", name), ctx.field("Your email", email), ctx.field("Message (optional)", note),
        inlineButton("Ask to subscribe", result, async () => {
          const answer = await ctx.action("subscribe", { name: name.value, email: email.value, note: note.value });
          const parts = [h("p", { class: "pub-notice good", role: "status", text: answer.message })];
          if (answer.reference) {
            if (answer.payment_link && /^https?:\/\//i.test(answer.payment_link)) parts.push(h("a", { class: "pub-btn", href: answer.payment_link, target: "_blank", rel: "noopener noreferrer", text: `Pay ${money(answer.price_cents, answer.currency)}` }));
            if (answer.payment_instructions) parts.push(h("p", { class: "pub-pre", text: answer.payment_instructions }));
            parts.push(h("p", { text: "Your payment reference (add it to your payment):" }), h("p", { class: "pub-code", style: { whiteSpace: "nowrap", overflowWrap: "normal", fontSize: "22px" }, text: answer.reference }));
          }
          result.replaceChildren(...parts);
          result.scrollIntoView({ block: "nearest" });
        }),
        result));
    }
    const subEmail = h("input", { type: "email", maxlength: 254, autocomplete: "email" });
    const code = h("input", { type: "text", maxlength: 20, autocomplete: "one-time-code", placeholder: "ABCD-EFGH-JKLM" });
    const codeOut = h("div", { "aria-live": "polite" });
    paidCard.append(h("form", { class: "pub-form", onsubmit: (event) => event.preventDefault() },
      h("h3", { text: "I am a subscriber" }),
      ctx.field("Email", subEmail), ctx.field("Access code", code),
      inlineButton("Show my paid ideas", codeOut, async () => {
        const answer = await ctx.query("paid", { email: subEmail.value.trim(), secret: code.value.trim() });
        showPaid(answer.ideas, answer.paid_until);
        paidCard.scrollIntoView({ block: "start" });
      }, "secondary"),
      codeOut));
    root.append(paidCard, h("section", { class: "pub-card" }, h("h2", { text: "Track record: every closed idea" }), ideaTable(page.closed, true)));
  });
})();

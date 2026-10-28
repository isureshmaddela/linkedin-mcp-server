/* Aira — Events: the public pages customers use (aira/suite/events).
 *
 * event    the event page: details, tickets with live availability, promo code, buyer details, order; a waitlist
 *          form when it is sold out. A free order is confirmed at once; a paid one shows how to pay.
 * tickets  a buyer's tickets: large check-in codes, status, add to calendar, how to pay and "I have paid".
 * A partner's code in the address (?ref=CODE) goes with the order. Errors show next to the form they belong to
 * and clear on the next try. Built with textContent only (ctx.h); every limit is re-checked by the server.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  const fmt = (iso, zone, options) => {
    if (!iso) return "";
    try { return new Date(iso).toLocaleString(undefined, { ...options, timeZone: zone && zone !== "UTC" ? zone : undefined }); } catch { return new Date(iso).toLocaleString(); }
  };
  const whenText = (page) => {
    const zone = page.timezone;
    const start = fmt(page.event.starts_at, zone, { weekday: "long", month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
    const end = fmt(page.event.ends_at, zone, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
    return end ? `${start} – ${end}` : start;
  };
  const PHASE_TEXT = { draft: "Tickets are not on sale yet.", cancelled: "This event has been cancelled.", ended: "This event has ended." };

  // A partner's code from the address (?ref=CODE), kept for this visit and for this business only: every hosted
  // business shares one web address, so the saved code is keyed by the business's own scope from the page.
  const referral = (scope) => {
    let code = "";
    try { code = new URLSearchParams(location.search).get("ref") || ""; } catch { code = ""; }
    code = code.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 30);
    const key = /^[0-9a-f]{8,64}$/.test(String(scope || "")) ? `aira.ref.${scope}` : "";
    try {
      if (key && code) sessionStorage.setItem(key, code);
      else if (key) code = sessionStorage.getItem(key) || "";
    } catch { /* storage unavailable */ }
    return code;
  };
  // A form's own message line, replaced on every try (errors no longer pile up at the top of the page).
  const messageLine = (h) => {
    const node = h("p", { class: "pub-notice bad", role: "alert", hidden: true });
    return {
      node,
      show(text, tone = "bad") { node.className = `pub-notice ${tone}`; node.setAttribute("role", tone === "bad" ? "alert" : "status"); node.textContent = text; node.hidden = false; },
      clear() { node.hidden = true; node.textContent = ""; },
    };
  };
  const guarded = (line, fn) => async (event) => {
    line.clear();
    try { await fn(event); } catch (error) { line.show(error && error.message ? error.message : String(error)); }
  };

  function header(h, page) {
    const e = page.event;
    return [
      e.cover_url && /^https:\/\//i.test(e.cover_url) ? h("img", { src: e.cover_url, alt: "", style: { width: "100%", maxHeight: "320px", objectFit: "cover", borderRadius: "12px" } }) : null,
      h("div", {}, h("span", { class: "pub-badge", text: (e.category || "event").replace(/^./, (c) => c.toUpperCase()) }), h("h1", { text: e.title }), h("p", { class: "pub-muted", text: whenText(page) }),
        e.venue_name || e.venue_address ? h("p", {}, h("strong", { text: e.venue_name || "" }), e.venue_address ? h("span", { class: "pub-muted", text: ` ${e.venue_address}` }) : null) : e.online ? h("p", { class: "pub-muted", text: "Online event: the link is on your tickets page." }) : null),
    ].filter(Boolean);
  }

  // ---------------------------------------------------------------- the event page
  P.register("events", "event", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    root.append(...header(h, page));
    if (page.event.description) root.append(h("section", { class: "pub-card" }, h("p", { class: "pub-pre", text: page.event.description })));

    if (!page.on_sale) {
      const message = PHASE_TEXT[page.event.phase] || (page.sold_out ? "Sold out." : "Tickets are not on sale right now.");
      root.append(h("div", { class: `pub-notice ${page.event.phase === "cancelled" ? "bad" : ""}`, text: message }));
      if (page.waitlist_open) root.append(waitlistForm(h, ctx));
      if (!page.waitlist_open) return;
      return;
    }

    const quantities = {};
    const totals = h("div", { class: "pub-card" });
    const promo = h("input", { type: "text", autocomplete: "off", placeholder: "Code", maxLength: 30 });
    const promoLine = messageLine(h);
    const orderLine = messageLine(h);
    const ref = referral(page.ref_scope);
    let applied = "";
    const typeCards = page.types.map((type) => {
      const max = Math.max(0, type.remaining === null ? type.max_per_order : Math.min(type.max_per_order, type.remaining));
      const select = h("select", { "aria-label": `Number of ${type.name} tickets`, disabled: !type.on_sale });
      for (let n = 0; n <= max; n++) select.append(h("option", { value: String(n), text: String(n) }));
      select.addEventListener("change", () => { quantities[type.id] = Number(select.value); refresh(); });
      const left = type.on_sale ? (type.remaining !== null ? `${type.remaining} left` : "") : type.reason;
      return h("div", { class: "pub-item" },
        h("div", { style: { display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "baseline" } }, h("h3", { text: type.name }), h("span", { class: "pub-price", text: type.price_cents ? ctx.money(type.price_cents) : "Free" })),
        type.description ? h("p", { class: "pub-muted pub-pre", text: type.description }) : null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" } }, h("span", { class: `pub-muted ${type.on_sale ? "" : "pub-badge bad"}`.trim(), text: left || "" }), select));
    });
    const chosen = () => Object.entries(quantities).filter(([, q]) => q > 0).map(([ticket_type_id, quantity]) => ({ ticket_type_id, quantity }));
    const count = () => chosen().reduce((sum, item) => sum + item.quantity, 0);

    const holdersBox = h("div", { class: "pub-form" });
    const renderHolders = () => {
      const n = count();
      holdersBox.replaceChildren();
      if (n > 1) {
        holdersBox.append(h("p", { class: "pub-muted", text: "Names on the tickets (optional; we use yours when empty)." }));
        for (let i = 0; i < n; i++) holdersBox.append(ctx.field(`Ticket ${i + 1}`, h("input", { type: "text", maxLength: 120, dataset: { holder: String(i) } })));
      }
    };

    let lastError = "";
    async function refresh() {
      lastError = "";
      renderHolders();
      const items = chosen();
      if (!items.length) { totals.replaceChildren(h("p", { class: "pub-muted", text: "Choose your tickets above." })); submit.disabled = true; return; }
      try {
        const quote = await ctx.action("quote", { items, promo_code: applied });
        totals.replaceChildren(
          h("table", { class: "pub-table" }, h("tbody", {},
            ...quote.lines.map((line) => h("tr", {}, h("td", { text: `${line.quantity} × ${line.name}` }), h("td", { class: "num", text: line.price_cents ? ctx.money(line.price_cents * line.quantity) : "Free" }))),
            quote.discount_cents ? h("tr", {}, h("td", { text: `Promo ${applied}` }), h("td", { class: "num", text: `−${ctx.money(quote.discount_cents)}` })) : null,
            h("tr", {}, h("th", { text: "Total" }), h("th", { class: "num pub-total", text: quote.total_cents ? ctx.money(quote.total_cents) : "Free" })))),
        );
        submit.textContent = quote.total_cents ? `Order ${count()} ticket${count() === 1 ? "" : "s"} · ${ctx.money(quote.total_cents)}` : `Get ${count()} free ticket${count() === 1 ? "" : "s"}`;
        submit.disabled = false;
      } catch (error) {
        lastError = error.message;
        totals.replaceChildren(h("p", { class: "pub-notice bad", role: "alert", text: error.message }));
        submit.disabled = true;
      }
    }

    const name = h("input", { type: "text", autocomplete: "name", required: true, maxLength: 120 });
    const email = h("input", { type: "email", autocomplete: "email", required: true, maxLength: 254 });
    const phone = h("input", { type: "tel", autocomplete: "tel", maxLength: 30 });
    const submit = ctx.button("Choose tickets", guarded(orderLine, async () => {
      if (!name.value.trim()) { name.focus(); throw new Error("Enter your name so we can put it on your tickets."); }
      if (!email.value.trim() || !email.checkValidity()) { email.focus(); throw new Error("Enter your email address, like name@example.com, so we can send your tickets."); }
      const holders = [...holdersBox.querySelectorAll("input[data-holder]")].map((input) => input.value.trim());
      const result = await ctx.action("order", { items: chosen(), promo_code: applied, name: name.value.trim(), email: email.value.trim(), phone: phone.value.trim(), holders, ref });
      location.assign(result.tickets_path);
    }));
    submit.disabled = true;

    root.append(
      h("section", { class: "pub-card" }, h("h2", { text: "Tickets" }), h("div", { class: "pub-grid" }, ...typeCards)),
      page.promo_codes ? h("section", { class: "pub-card" }, ctx.field("Promo code", promo), promoLine.node, ctx.button("Apply", guarded(promoLine, async () => {
        const wanted = promo.value.trim();
        if (!chosen().length) throw new Error("Choose your tickets first, then apply the code.");
        applied = wanted;
        await refresh();
        if (submit.disabled && applied) { const reason = lastError; applied = ""; await refresh(); throw new Error(reason || "That promo code cannot be used."); }
        if (applied) promoLine.show(`Promo code ${applied.toUpperCase()} applied.`, "good");
      }), "secondary")) : null,
      totals,
      h("section", { class: "pub-card pub-form" }, h("h2", { text: "Your details" }), ctx.field("Name", name), ctx.field("Email", email, "Your tickets are sent here."), ctx.field("Phone", phone), holdersBox, orderLine.node, submit),
    );
    refresh();
  });

  function waitlistForm(h, ctx) {
    const name = h("input", { type: "text", autocomplete: "name", maxLength: 120 });
    const email = h("input", { type: "email", autocomplete: "email", maxLength: 254 });
    const quantity = h("select", {}, ...[1, 2, 3, 4, 5, 6].map((n) => h("option", { value: String(n), text: String(n) })));
    const line = messageLine(h);
    const box = h("section", { class: "pub-card pub-form" }, h("h2", { text: "Join the waitlist" }), h("p", { class: "pub-muted", text: "We will email you if tickets become available." }), ctx.field("Name", name), ctx.field("Email", email), ctx.field("Tickets wanted", quantity), line.node);
    box.append(ctx.button("Join the waitlist", guarded(line, async () => {
      if (!name.value.trim()) { name.focus(); throw new Error("Enter your name."); }
      if (!email.value.trim() || !email.checkValidity()) { email.focus(); throw new Error("Enter your email address, like name@example.com."); }
      await ctx.action("join_waitlist", { name: name.value.trim(), email: email.value.trim(), quantity: Number(quantity.value) });
      box.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: "You are on the waitlist. We will email you if seats free up." }));
    })));
    return box;
  }

  // ---------------------------------------------------------------- the buyer's tickets
  const icsText = (page, business) => {
    const stamp = (iso) => new Date(iso).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    const escape = (text) => String(text || "").replace(/[\\;,]/g, (c) => `\\${c}`).replace(/\r?\n/g, "\\n");
    const where = [page.event.venue_name, page.event.venue_address, page.event.online_url].filter(Boolean).join(", ");
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Aira//Events//EN", "BEGIN:VEVENT", `UID:${page.order.reference}@aira`, `DTSTAMP:${stamp(new Date().toISOString())}`, `DTSTART:${stamp(page.event.starts_at)}`, `DTEND:${stamp(page.event.ends_at)}`, `SUMMARY:${escape(page.event.title)}`, `LOCATION:${escape(where)}`, `DESCRIPTION:${escape(`Order ${page.order.reference}${business.name ? ` · ${business.name}` : ""}`)}`, "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  };

  P.register("events", "tickets", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const order = page.order;
    const STATUS = { confirmed: ["You're going!", "good"], pending_payment: ["Waiting for payment", ""], cancelled: ["Cancelled", "bad"], refunded: ["Refunded", "bad"] };
    const [label, tone] = STATUS[order.status] || [order.status, ""];
    root.append(
      h("div", {}, h("span", { class: `pub-badge ${tone}`, text: label }), h("h1", { text: page.event.title }), h("p", { class: "pub-muted", text: whenText(page) }),
        page.event.venue_name ? h("p", {}, h("strong", { text: page.event.venue_name }), page.event.venue_address ? h("span", { class: "pub-muted", text: ` ${page.event.venue_address}` }) : null) : null,
        page.event.online_url && /^https?:\/\//i.test(page.event.online_url) ? h("p", {}, h("a", { class: "pub-btn", href: page.event.online_url, target: "_blank", rel: "noopener noreferrer", text: "Join online" })) : null),
    );
    if (order.status === "pending_payment") {
      const box = ctx.paymentBox(order.total_cents, order.reference);
      if (box) root.append(box);
      const claim = h("section", { class: "pub-card" });
      const line = messageLine(h);
      const render = (claimed) => claim.replaceChildren(claimed ? h("p", { class: "pub-notice good", role: "status", text: "Thanks! We will confirm your tickets as soon as we see your payment." }) : h("div", { class: "pub-btn-row" }, h("span", { class: "pub-muted", text: "Paid already?" }), ctx.button("I have paid", guarded(line, async () => { await ctx.action("notify_paid", {}); render(true); }), "secondary"), line.node));
      render(order.payment_claimed);
      root.append(claim);
    }
    root.append(h("section", { class: "pub-card" }, h("h2", { text: `Order ${order.reference}` }),
      h("table", { class: "pub-table" }, h("tbody", {},
        h("tr", {}, h("td", { text: "Name" }), h("td", { class: "num", text: order.buyer_name })),
        order.discount_cents ? h("tr", {}, h("td", { text: `Discount${order.promo_code ? ` (${order.promo_code})` : ""}` }), h("td", { class: "num", text: `−${ctx.money(order.discount_cents)}` })) : null,
        h("tr", {}, h("th", { text: "Total" }), h("th", { class: "num pub-total", text: order.total_cents ? ctx.money(order.total_cents) : "Free" }))))));
    const live = page.tickets.filter((t) => t.status !== "cancelled");
    root.append(h("section", { class: "pub-card" }, h("h2", { text: live.length === 1 ? "Your ticket" : `Your ${live.length} tickets` }),
      order.status === "pending_payment" ? h("p", { class: "pub-muted", text: "These codes work at the door once your payment is confirmed." }) : h("p", { class: "pub-muted", text: "Show a code at the door. Each code works once." }),
      h("div", { class: "pub-grid" }, ...page.tickets.map((ticket) => h("div", { class: "pub-item" },
        h("span", { class: "pub-muted", text: `${ticket.type_name}${ticket.holder_name ? ` · ${ticket.holder_name}` : ""}` }),
        h("div", { class: "pub-code", text: ticket.code, style: ticket.status === "cancelled" ? { textDecoration: "line-through", opacity: "0.5" } : {} }),
        h("span", { class: `pub-badge ${ticket.status === "checked_in" ? "good" : ticket.status === "cancelled" ? "bad" : ""}`, text: ticket.status === "checked_in" ? "Used" : ticket.status === "cancelled" ? "Cancelled" : "Valid" }))))));
    if (["confirmed", "pending_payment"].includes(order.status) && page.event.phase !== "ended") {
      const href = `data:text/calendar;charset=utf-8,${encodeURIComponent(icsText(page, ctx.business))}`;
      root.append(h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn secondary", href, download: `${page.event.title.replace(/[^a-z0-9]+/gi, "-").slice(0, 40) || "event"}.ics`, text: "Add to calendar" }), ctx.button("Print", () => window.print(), "ghost"), ctx.button("Copy this page's link", () => ctx.copy(location.href), "ghost")));
    }
  });
})();

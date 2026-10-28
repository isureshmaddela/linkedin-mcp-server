/* Aira — Events (#/events): sell tickets, fill the room and run the door.
 *
 * Tabs (?tab=): overview (what is selling, what is waiting for payment, setup), events (list; ?event=<id> opens one:
 * details, ticket types, the public event page, its orders, messages to ticket holders), orders, check-in
 * (?event=<id>: type a code at the door or find a name), promo codes, sponsors, the waitlist, partners and
 * earnings (the last two are shared with Courses through Aira.xp, see views-academy.js).
 * Everything runs through /api/events/* (aira/suite/events/events_routes.py). Times are shown, and typed, in the
 * business's time zone (Business hub > Business profile), so the organiser sees the same time as the customers
 * whatever the time zone of the computer they use; with no time zone set (UTC), the computer's own is used.
 *
 * In the Aira frame the page is named after the open folder (or the open event), "+ New event" asks with
 * ?new=event and a search result with ?open=order:<id> (see views-academy.js, which plugs the whole app into the
 * frame); ?tab=search&q= shows the app's search results here too.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const call = (path, opts) => S.api("events", path, opts);

  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard", title: "Events overview", about: "What is selling, what is waiting for payment and what to set up next." },
    { id: "events", label: "Events", icon: "events", about: "Every event: open one for its tickets, its page, its orders and messages to ticket holders." },
    { id: "orders", label: "Orders", icon: "shop", about: "Every ticket order: mark payments received, resend tickets, refund." },
    { id: "checkin", label: "Check-in", icon: "xpCheck", about: "Run the door: type a ticket code or find a name on the guest list." },
    { id: "promos", label: "Promo codes", icon: "xpTag", about: "Codes buyers type on the event page: reward early birds, partners and your community." },
    { id: "sponsors", label: "Sponsors", icon: "xpRibbon", about: "Businesses that pay to reach your audience, from pitch to paid." },
    { id: "waitlist", label: "Waitlist", icon: "xpClock", about: "People waiting for seats at a sold-out event." },
    { id: "partners", label: "Partners", icon: "xpPartner", about: "People who share your courses and events with their own link and earn a commission." },
    { id: "earnings", label: "Earnings", icon: "xpMoney", about: "What every course and event earned, from the top earner to the lowest." },
  ];
  const CATEGORIES = ["concert", "comedy", "theatre", "party", "festival", "conference", "workshop", "meetup", "sports", "screening", "exhibition", "other"];
  const PHASE_TONES = { published: "good", draft: "", cancelled: "bad", ended: "" };
  const ORDER_TONES = { pending_payment: "warn", confirmed: "good", cancelled: "bad", refunded: "bad" };

  const zone = () => (S._business && S._business.timezone && S._business.timezone !== "UTC" ? S._business.timezone : undefined);
  const when = (iso) => (iso ? Aira.fmt.datetime(iso, zone()) : "");
  const go = (query) => Aira.router.go(["events"], query);
  const toolbar = (...children) => h("div", { class: "btn-row xp-toolbar" }, ...children.filter(Boolean));
  const pad = (n) => String(n).padStart(2, "0");
  // Minutes the time zone is ahead of UTC at this moment (summer time included).
  const zoneOffset = (date, timeZone) => {
    try {
      const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(date).filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
      const asUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour % 24, parts.minute);
      return Math.round((asUtc - Math.floor(date.getTime() / 60000) * 60000) / 60000);
    } catch { return -date.getTimezoneOffset(); }
  };
  // datetime-local inputs hold a wall-clock time: the business's own (or the computer's when none is set); the
  // server stores UTC.
  const toInput = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const tz = zone();
    if (!tz) return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const shifted = new Date(d.getTime() + zoneOffset(d, tz) * 60000);
    return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`;
  };
  const fromInput = (value) => {
    if (!value) return "";
    const tz = zone();
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
    if (!tz || !match) return new Date(value).toISOString();
    const wall = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]));
    let utc = wall - zoneOffset(new Date(wall), tz) * 60000;
    utc = wall - zoneOffset(new Date(utc), tz) * 60000; // a second pass settles the days summer time starts or ends
    return new Date(utc).toISOString();
  };
  const zoneHint = () => (zone() ? `In ${zone()} time (your business time zone).` : "In this computer's time zone. Set your business time zone in Business hub > Business profile.");
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // ---------------------------------------------------------------- editors
  const EVENT_FIELDS = () => [
    { name: "title", label: "Title", required: true, placeholder: "Friday Jazz Night" },
    { name: "category", label: "Category", type: "select", options: S.statusOptions(CATEGORIES) },
    { name: "starts_at", label: "Starts", type: "datetime-local", required: true, hint: zoneHint() },
    { name: "ends_at", label: "Ends", type: "datetime-local", hint: "Leave empty for two hours after the start." },
    { name: "venue_name", label: "Venue", placeholder: "The Blue Room" },
    { name: "venue_address", label: "Address", type: "textarea", rows: 2 },
    { name: "online_url", label: "Online link", type: "url", hint: "For online events. Only ticket holders with a confirmed order see it." },
    { name: "capacity", label: "Capacity", type: "number", min: 0, hint: "Most people allowed in, across all ticket types. 0 = only the ticket quantities limit it." },
    { name: "cover_url", label: "Cover image link", type: "url", placeholder: "https://..." },
    { name: "description", label: "Description", type: "textarea", rows: 6, hint: "Shown on the event page. Use Draft description to get a start." },
    { name: "notes", label: "Notes for your team", type: "textarea", rows: 3, hint: "Never shown to customers." },
  ];
  const eventValues = (event) => ({ ...event, starts_at: toInput(event.starts_at), ends_at: toInput(event.ends_at) });
  const eventBody = (values) => ({ ...values, starts_at: fromInput(values.starts_at), ends_at: fromInput(values.ends_at), capacity: values.capacity || 0 });

  async function editEvent(event) {
    return S.edit({
      title: event ? "Edit event" : "New event",
      wide: true,
      fields: event ? EVENT_FIELDS() : [...EVENT_FIELDS(), { type: "heading", label: "First ticket type" }, { name: "first_ticket", label: "Ticket name", placeholder: "General admission" }, { name: "first_price", label: "Price", type: "money", hint: "0 for free" }, { name: "first_quantity", label: "Quantity", type: "number", min: 1 }],
      values: event ? eventValues(event) : { category: "other", first_ticket: "General admission", first_quantity: 100 },
      submitLabel: event ? "Save" : "Create event",
      onSave: async (values) => {
        const body = eventBody(values);
        if (event) return (await call(`/events/${event.id}`, { method: "PATCH", body })).event;
        if (values.first_ticket) body.ticket_types = [{ name: values.first_ticket, price_cents: values.first_price || 0, quantity: values.first_quantity || 1 }];
        ["first_ticket", "first_price", "first_quantity"].forEach((key) => delete body[key]);
        return (await call("/events", { method: "POST", body })).event;
      },
    });
  }

  const TYPE_FIELDS = [
    { name: "name", label: "Name", required: true, placeholder: "Early bird, VIP, Student" },
    { name: "price_cents", label: "Price", type: "money", hint: "0 for free" },
    { name: "quantity", label: "Quantity", type: "number", min: 1, required: true },
    { name: "max_per_order", label: "Most per order", type: "number", min: 1, max: 50 },
    { name: "sales_start", label: "Sales start", type: "datetime-local", hint: "Empty = on sale as soon as the event is published. Times are your business time zone's." },
    { name: "sales_end", label: "Sales end", type: "datetime-local", hint: "Empty = until the event ends." },
    { name: "description", label: "What is included", type: "textarea", rows: 2 },
    { name: "active", label: "On sale", type: "checkbox" },
  ];
  async function editType(event, ticket) {
    return S.edit({
      title: ticket ? `Edit ${ticket.name}` : "New ticket type",
      fields: TYPE_FIELDS,
      values: ticket ? { ...ticket, sales_start: toInput(ticket.sales_start), sales_end: toInput(ticket.sales_end), active: !!ticket.active } : { max_per_order: 10, active: true, price_cents: 0 },
      onSave: async (values) => {
        const body = { ...values, price_cents: values.price_cents || 0, sales_start: fromInput(values.sales_start), sales_end: fromInput(values.sales_end) };
        return ticket ? call(`/ticket-types/${ticket.id}`, { method: "PATCH", body }) : call(`/events/${event.id}/ticket-types`, { method: "POST", body });
      },
    });
  }

  async function sellTickets(event, rerender) {
    const types = (event.ticket_types || []).filter((t) => t.active);
    if (!types.length) { Aira.toast("Add a ticket type first."); return; }
    const fields = [
      ...types.map((t) => ({ name: `qty_${t.id}`, label: `${t.name} (${S.money(t.price_cents)})`, type: "number", min: 0, max: 50 })),
      { name: "name", label: "Buyer name", required: true },
      { name: "email", label: "Email", type: "email", hint: "Tickets are emailed here (through the outbox)." },
      { name: "phone", label: "Phone", type: "tel" },
      { name: "promo_code", label: "Promo code" },
      { name: "ref", label: "Partner code", hint: "Optional. The partner who sent this buyer earns their commission once it is paid." },
      { name: "comp", label: "Complimentary (free, for guests and press)", type: "checkbox" },
      { name: "paid", label: "Already paid (cash or card at the door)", type: "checkbox" },
      { name: "notes", label: "Notes", type: "textarea", rows: 2 },
    ];
    const done = await S.edit({
      title: `Sell tickets: ${event.title}`,
      fields,
      values: Object.fromEntries(types.map((t, i) => [`qty_${t.id}`, i === 0 ? 1 : 0])),
      submitLabel: "Create order",
      onSave: async (values) => {
        const items = types.map((t) => ({ ticket_type_id: t.id, quantity: Number(values[`qty_${t.id}`] || 0) })).filter((i) => i.quantity > 0);
        return call("/orders", { method: "POST", body: { event_id: event.id, items, name: values.name, email: values.email, phone: values.phone, promo_code: values.promo_code, ref: values.comp ? "" : values.ref, comp: values.comp, paid: values.paid, notes: values.notes } });
      },
    });
    if (done) { Aira.toast(`Order ${done.order.reference} created.`, "success"); rerender(); }
  }

  async function messageHolders(event, thanks) {
    const done = await S.edit({
      title: thanks ? "Thank your attendees" : "Message ticket holders",
      fields: thanks ? [{ name: "body", label: "Message", type: "textarea", rows: 6, hint: "Leave empty for a friendly thank-you." }] : [{ name: "subject", label: "Subject", required: true }, { name: "body", label: "Message", type: "textarea", rows: 8, required: true }],
      values: thanks ? {} : { subject: `Update: ${event.title}` },
      submitLabel: "Prepare messages",
      onSave: (values) => call(`/events/${event.id}/announce`, { method: "POST", body: { ...values, thanks } }),
    });
    if (done) Aira.toast(`${plural(done.prepared, "message")} waiting in Business hub > Outbox.`, "success");
  }

  // ---------------------------------------------------------------- overview
  async function overview(body) {
    const [data, catalog] = await Promise.all([call("/overview"), Aira.xp && Aira.xp.catalogCard ? Aira.xp.catalogCard() : null]);
    const parts = [S.kpis(data.cards)];
    if (catalog) parts.push(catalog);
    if (data.claimed_orders) parts.push(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `${plural(data.claimed_orders, "buyer")} say they paid` }), h("span", { class: "muted", text: "Check your payments, then mark the orders paid to send their tickets." }), h("a", { class: "btn secondary sm", href: "#/events?tab=orders&status=pending_payment", text: "Review" })));
    parts.push(S.section("Upcoming events", Aira.xp.table({
      columns: [
        { key: "title", label: "Event", render: (e) => h("div", {}, h("span", { text: e.title }), Aira.xp.phoneMeta(when(e.starts_at), S.money(e.counts.revenue_cents))) },
        { key: "starts_at", label: "When", wide: true, render: (e) => when(e.starts_at) },
        { key: "sold", label: "Sold", num: true, render: (e) => `${e.counts.sold} / ${e.available || "-"}` },
        { key: "sell_through", label: "Sell-through", num: true, wide: true, render: (e) => `${e.sell_through}%` },
        { key: "revenue", label: "Revenue", num: true, wide: true, render: (e) => S.money(e.counts.revenue_cents) },
        { key: "phase", label: "Status", wide: true, render: (e) => S.pill(e.phase, PHASE_TONES) },
      ],
      rows: data.upcoming,
      onRow: (e) => go({ tab: "events", event: e.id }),
      empty: Aira.ui.empty("No upcoming events", "Create an event, add tickets and share its page.", S.button("New event", async () => { const e = await editEvent(); if (e) go({ tab: "events", event: e.id }); })),
    })));
    parts.push(S.grid(S.section("Get set up", S.checklist(data.checklist)), S.section("Recent activity", S.activity(data.activity))));
    body.append(...parts);
  }

  // ---------------------------------------------------------------- events
  async function eventsList(body, route) {
    const scope = route.query.scope || "upcoming";
    const q = route.query.q || "";
    const data = await call(`/events?scope=${encodeURIComponent(scope === "all" ? "" : scope)}&q=${encodeURIComponent(q)}`);
    const search = h("input", { type: "search", placeholder: "Search events", value: q, "aria-label": "Search events", "data-search": "1", onkeydown: (event) => { if (event.key === "Enter") go({ tab: "events", scope, q: search.value.trim() }); } });
    const scopeSelect = Aira.ui.select([["upcoming", "Upcoming"], ["past", "Past"], ["all", "All"]], scope, { "aria-label": "Which events", onchange: () => go({ tab: "events", scope: scopeSelect.value, q }) });
    body.append(
      toolbar(search, scopeSelect, S.button("New event", async () => { const e = await editEvent(); if (e) go({ tab: "events", event: e.id }); })),
      Aira.xp.table({
        columns: [
          { key: "title", label: "Event", render: (e) => h("div", {}, h("strong", { text: e.title }), Aira.xp.phoneMeta(when(e.starts_at), e.venue_name || (e.online_url ? "Online" : ""))) },
          { key: "starts_at", label: "When", wide: true, render: (e) => when(e.starts_at) },
          { key: "venue_name", label: "Venue", wide: true, render: (e) => e.venue_name || (e.online_url ? "Online" : "") },
          { key: "sold", label: "Sold", num: true, render: (e) => `${e.counts.sold} / ${e.available || "-"}` },
          { key: "revenue", label: "Revenue", num: true, wide: true, render: (e) => S.money(e.counts.revenue_cents) },
          { key: "phase", label: "Status", render: (e) => S.pill(e.phase, PHASE_TONES) },
        ],
        rows: data.events,
        onRow: (e) => go({ tab: "events", event: e.id }),
        empty: q ? Aira.ui.empty("No event matches", scope === "all" ? "Try another word." : "Try another word, or look in All.", scope === "all" ? null : h("a", { class: "btn secondary sm", href: `#/events?${new URLSearchParams({ tab: "events", scope: "all", q })}`, text: "Look in All" }))
          : Aira.ui.empty(scope === "past" ? "No past events" : "No events yet", "An event has a time, a place and ticket types. Publish it to get a page you can share.", scope === "past" || !Aira.xp.canManage() ? null : S.button("Create an event", async () => { const e = await editEvent(); if (e) go({ tab: "events", event: e.id }); })),
      }),
    );
  }

  async function eventDetail(body, eventId, rerender) {
    const { event } = await call(`/events/${eventId}`);
    const phase = event.phase;
    const actions = [
      S.button("Edit", async () => { if (await editEvent(event)) rerender(); }, { kind: "secondary sm" }),
      event.status === "draft" ? S.button("Publish", async () => { await call(`/events/${event.id}/status`, { method: "POST", body: { status: "published" } }); Aira.toast("Published. Share the page to start selling.", "success"); rerender(); }, { kind: "sm" }) : null,
      event.status === "published" ? S.button("Unpublish", async () => { await call(`/events/${event.id}/status`, { method: "POST", body: { status: "draft" } }); rerender(); }, { kind: "ghost sm" }) : null,
      phase !== "cancelled" ? S.button("Sell tickets", () => sellTickets(event, rerender), { kind: "secondary sm" }) : null,
      h("a", { class: "btn secondary sm", href: `#/events?tab=checkin&event=${event.id}`, text: "Check-in" }),
      S.button("Message holders", () => messageHolders(event, false), { kind: "ghost sm" }),
      phase === "ended" ? S.button("Thank attendees", () => messageHolders(event, true), { kind: "ghost sm" }) : null,
      S.button("Attendees CSV", () => Aira.download(`/api/events/events/${event.id}/attendees.csv`, "attendees.csv"), { kind: "ghost sm" }),
      S.button("Duplicate", async () => { const copy = await call(`/events/${event.id}/duplicate`, { method: "POST", body: { days_later: 7 } }); Aira.toast("Copied a week later, as a draft.", "success"); go({ tab: "events", event: copy.event.id }); }, { kind: "ghost sm" }),
      phase !== "cancelled" && phase !== "ended" ? S.button("Cancel event", async () => {
        const done = await S.edit({ title: "Cancel this event", fields: [{ name: "message", label: "Message to ticket holders", type: "textarea", rows: 5, hint: "Leave empty for a short apology. Orders stay so you can refund them." }], submitLabel: "Cancel event", onSave: (values) => call(`/events/${event.id}/cancel`, { method: "POST", body: values }) });
        if (done) { Aira.toast(`Cancelled. ${plural(done.notified, "ticket holder")} will be told.`, "success"); rerender(); }
      }, { kind: "danger secondary sm" }) : null,
      !event.counts.orders ? S.button("Delete", async () => { if (await S.confirmDelete("this event")) { await call(`/events/${event.id}`, { method: "DELETE" }); go({ tab: "events" }); } }, { kind: "ghost sm" }) : null,
    ];
    const orders = await call(`/orders?event_id=${event.id}`);
    if (currentMain) Aira.xp.setTitle(currentMain, event.title, `${when(event.starts_at)} · ${event.venue_name || (event.online_url ? "Online" : "No venue yet")}`);
    body.append(
      h("div", { class: "xp-detail-meta" }, h("a", { class: "btn ghost sm", href: "#/events?tab=events", text: "← All events" }), S.pill(phase, PHASE_TONES), event.category && event.category !== "other" ? h("span", { class: "muted", text: event.category.replace(/^./, (c) => c.toUpperCase()) }) : null),
      h("div", { class: "btn-row" }, ...actions.filter(Boolean)),
      S.kpis([
        { label: "Tickets sold", value: `${event.counts.sold} / ${event.available || "-"}`, hint: `${event.sell_through}% sold` },
        { label: "Revenue", value: S.money(event.counts.revenue_cents), tone: "good" },
        { label: "Awaiting payment", value: event.counts.pending, tone: event.counts.pending ? "warn" : "" },
        { label: "Checked in", value: event.counts.checked_in, hint: event.counts.sold ? `${event.check_in_rate}%` : "" },
        { label: "Waitlist", value: event.counts.waitlist },
      ]),
      event.status === "published" || event.link ? S.share({
        title: "Event page",
        text: "Customers buy tickets here. Share it on social media, in your newsletter and on posters.",
        link: event.link,
        onCreate: async () => (await call(`/events/${event.id}/link`, { method: "POST", body: {} })).link,
        onRevoke: () => call(`/events/${event.id}/link`, { method: "DELETE" }),
      }) : h("p", { class: "muted", text: "Publish the event to get a page customers can buy tickets on." }),
      S.section("Ticket types",
        h("div", { class: "btn-row" }, S.button("Add ticket type", async () => { if (await editType(event)) rerender(); }, { kind: "secondary sm" })),
        Aira.xp.table({
          columns: [
            { key: "name", label: "Ticket" },
            { key: "price_cents", label: "Price", num: true, render: (t) => (t.price_cents ? S.money(t.price_cents) : "Free") },
            { key: "sold", label: "Sold", num: true, render: (t) => `${t.sold} / ${t.quantity}` },
            { key: "window", label: "On sale", wide: true, render: (t) => (t.active ? [t.sales_start ? `from ${when(t.sales_start)}` : "", t.sales_end ? `until ${when(t.sales_end)}` : ""].filter(Boolean).join(" ") || "Yes" : "Off") },
            { key: "actions", label: "", render: (t) => h("div", { class: "btn-row" }, S.button("Edit", async () => { if (await editType(event, t)) rerender(); }, { kind: "ghost sm" }), S.button(t.sold ? "Stop selling" : "Delete", async () => { if (await Aira.confirm({ title: t.sold ? "Stop selling" : "Delete ticket type", message: t.sold ? "Tickets already sold stay valid." : "Delete this ticket type?", confirmLabel: t.sold ? "Stop selling" : "Delete", danger: true })) { await call(`/ticket-types/${t.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })) },
          ],
          rows: event.ticket_types,
          empty: "No ticket types yet. Add one to start selling.",
        })),
      S.section("Description",
        h("p", { class: "suite-pre", text: event.description || "No description yet." }),
        h("div", { class: "btn-row" }, S.button("Draft description", async () => {
          const draft = await call(`/events/${event.id}/draft-description`, { method: "POST", body: {} });
          const saved = await S.edit({ title: draft.source === "model" ? "Suggested description" : "Description template", fields: [{ name: "description", label: "Description", type: "textarea", rows: 10 }], values: { description: draft.description }, submitLabel: "Use it", onSave: (values) => call(`/events/${event.id}`, { method: "PATCH", body: { description: values.description } }) });
          if (saved) rerender();
        }, { kind: "ghost sm" }))),
      S.section("Orders", ordersTable(orders.orders, rerender)),
    );
  }

  // ---------------------------------------------------------------- orders
  function ordersTable(rows, rerender) {
    return Aira.xp.table({
      columns: [
        { key: "reference", label: "Order", wide: true },
        { key: "event_title", label: "Event", wide: true },
        { key: "buyer_name", label: "Buyer", render: (o) => h("span", {}, o.buyer_name, o.buyer_email ? h("div", { class: "muted small", text: o.buyer_email }) : null, Aira.xp.phoneMeta(o.reference, o.event_title, plural(o.tickets, "ticket"))) },
        { key: "tickets", label: "Tickets", num: true, wide: true },
        { key: "total_cents", label: "Total", num: true, render: (o) => S.money(o.total_cents) },
        { key: "status", label: "Status", render: (o) => h("span", {}, S.pill(o.status, ORDER_TONES), o.status === "pending_payment" && o.payment_claimed_at ? h("span", { class: "muted small", text: " says paid" }) : null) },
        { key: "created_at", label: "Ordered", wide: true, render: (o) => Aira.fmt.ago(o.created_at) },
      ],
      rows,
      onRow: (o) => openOrder(o.id, rerender),
      empty: Aira.ui.empty("No orders yet", "Orders appear here when someone buys on the event page, or when you use Sell tickets."),
    });
  }

  async function openOrder(orderId, rerender) {
    const data = await call(`/orders/${orderId}`);
    const order = data.order;
    const act = async (path, body, message) => { await call(`/orders/${orderId}/${path}`, { method: "POST", body: body || {} }); if (message) Aira.toast(message, "success"); Aira.closeDrawer(); rerender(); };
    const ticketsUrl = data.link ? S.absolute(data.link.path) : "";
    Aira.drawer({
      title: `Order ${order.reference}`,
      subtitle: data.event.title,
      body: h("div", { class: "suite-body" },
        h("div", {}, S.pill(order.status, ORDER_TONES), order.payment_claimed_at && order.status === "pending_payment" ? h("p", { class: "muted", text: `The buyer says they paid (${Aira.fmt.ago(order.payment_claimed_at)}). Check your account before marking it paid.` }) : null),
        Aira.ui.kv([["Buyer", order.buyer_name], ["Email", order.buyer_email], ["Phone", order.buyer_phone], ["Subtotal", S.money(order.subtotal_cents)], ["Discount", order.discount_cents ? `${S.money(order.discount_cents)} (${order.promo_code || "comp"})` : ""], ["Total", S.money(order.total_cents)], ["Source", order.source], ["Ordered", when(order.created_at)], ["Paid", when(order.paid_at)], ["Notes", order.notes]]),
        S.table({ columns: [{ key: "code", label: "Code" }, { key: "type_name", label: "Ticket" }, { key: "holder_name", label: "Name" }, { key: "status", label: "Status", render: (t) => S.pill(t.status, { valid: "info", checked_in: "good", cancelled: "bad" }) }], rows: data.tickets }),
        ticketsUrl ? h("p", {}, h("a", { href: ticketsUrl, target: "_blank", rel: "noopener noreferrer", text: "Open the buyer's tickets page" }), " ", S.button("Copy link", () => S.copy(ticketsUrl), { kind: "ghost sm" })) : null,
        h("div", { class: "btn-row" },
          order.status === "pending_payment" ? S.button("Mark paid", () => act("paid", {}, "Paid. The tickets are on their way.")) : null,
          ["pending_payment", "confirmed"].includes(order.status) && order.buyer_email ? S.button("Resend", () => act("resend", {}, "Queued in the outbox."), { kind: "secondary" }) : null,
          order.status === "confirmed" && order.paid_at ? S.button("Refund", async () => { if (await Aira.confirm({ title: "Refund this order", message: "Give the money back through your payment provider, then confirm. The seats are released and the revenue stops counting.", confirmLabel: "Mark refunded", danger: true })) await act("cancel", { refund: true }, "Refunded."); }, { kind: "danger secondary" }) : null,
          ["pending_payment", "confirmed"].includes(order.status) ? S.button("Cancel order", async () => { if (await Aira.confirm({ title: "Cancel this order", message: "Its tickets stop working and the seats are released.", confirmLabel: "Cancel order", danger: true })) await act("cancel", {}, "Cancelled."); }, { kind: "ghost" }) : null)),
    });
  }

  async function ordersTab(body, route, rerender) {
    const [events, data] = await Promise.all([call("/events"), call(`/orders?event_id=${encodeURIComponent(route.query.event || "")}&status=${encodeURIComponent(route.query.status || "")}&q=${encodeURIComponent(route.query.q || "")}`)]);
    const eventSelect = Aira.ui.select([["", "All events"], ...events.events.map((e) => [e.id, e.title])], route.query.event || "", { "aria-label": "Event", onchange: () => go({ tab: "orders", event: eventSelect.value, status: statusSelect.value, q: search.value.trim() }) });
    const statusSelect = Aira.ui.select([["", "Any status"], ...S.statusOptions(data.statuses)], route.query.status || "", { "aria-label": "Status", onchange: () => go({ tab: "orders", event: eventSelect.value, status: statusSelect.value, q: search.value.trim() }) });
    const search = h("input", { type: "search", placeholder: "Name, email or reference", value: route.query.q || "", "aria-label": "Search orders", "data-search": "1", onkeydown: (e) => { if (e.key === "Enter") go({ tab: "orders", event: eventSelect.value, status: statusSelect.value, q: search.value.trim() }); } });
    const pending = data.orders.filter((o) => o.status === "pending_payment");
    body.append(
      toolbar(search, eventSelect, statusSelect),
      S.kpis([
        { label: "Orders", value: data.orders.length },
        { label: "Awaiting payment", value: pending.length, hint: pending.length ? S.money(pending.reduce((sum, o) => sum + o.total_cents, 0)) : "", tone: pending.length ? "warn" : "" },
        { label: "Confirmed revenue", value: S.money(data.orders.filter((o) => o.status === "confirmed" && o.paid_at).reduce((sum, o) => sum + o.total_cents, 0)), tone: "good" },
      ]),
      ordersTable(data.orders, rerender),
    );
  }

  // ---------------------------------------------------------------- check-in
  async function checkinTab(body, route, rerender) {
    const events = (await call("/events?scope=upcoming")).events.concat((await call("/events?scope=past")).events.slice(0, 5));
    if (!events.length) { body.append(Aira.ui.empty("No events to check in", "Create and publish an event first.")); return; }
    const eventId = route.query.event || events[0].id;
    const picker = Aira.ui.select(events.map((e) => [e.id, `${e.title} · ${when(e.starts_at)}`]), eventId, { "aria-label": "Event", onchange: () => go({ tab: "checkin", event: picker.value }) });
    const data = await call(`/events/${eventId}/checkin`);
    const counts = h("div");
    const showCounts = (c) => counts.replaceChildren(S.kpis([{ label: "Checked in", value: c.checked_in, tone: "good" }, { label: "Sold", value: c.sold }, { label: "Still to arrive", value: Math.max(0, c.sold - c.checked_in) }, { label: "Capacity", value: data.event.capacity || "-" }]));
    showCounts(data.counts);
    const result = h("div", { "aria-live": "assertive" });
    const code = h("input", { type: "text", placeholder: "ABCD-2345", "aria-label": "Ticket code", autocomplete: "off", autocapitalize: "characters", style: { fontSize: "22px", letterSpacing: "0.1em", maxWidth: "260px" } });
    const tone = { checked_in: "good", already: "warn", unpaid: "warn", cancelled: "bad", invalid: "bad" };
    const doCheckIn = async (value) => {
      const outcome = await call(`/events/${eventId}/checkin`, { method: "POST", body: { code: value } });
      const who = outcome.ticket ? `${outcome.ticket.holder_name || outcome.ticket.buyer_name} · ${outcome.ticket.type_name || "Ticket"}` : "";
      result.replaceChildren(h("div", { class: `cloud-banner ${tone[outcome.result] === "good" ? "info" : tone[outcome.result] === "bad" ? "danger" : "warning"}`, role: "status" }, h("strong", { text: outcome.message }), who ? h("span", { text: who }) : null, outcome.result === "already" && outcome.checked_in_at ? h("span", { class: "muted", text: `at ${when(outcome.checked_in_at)}` }) : null));
      showCounts(outcome.counts);
      code.value = "";
      code.focus();
      return outcome;
    };
    const form = h("form", { class: "btn-row", onsubmit: async (event) => { event.preventDefault(); if (code.value.trim()) await doCheckIn(code.value.trim()); } }, code, h("button", { type: "submit", class: "btn", text: "Check in" }));
    const search = h("input", { type: "search", placeholder: "Find by name, email or order", value: route.query.q || "", "aria-label": "Find a guest", onkeydown: (e) => { if (e.key === "Enter") go({ tab: "checkin", event: eventId, q: search.value.trim() }); } });
    const list = route.query.q ? (await call(`/events/${eventId}/checkin?q=${encodeURIComponent(route.query.q)}`)).attendees : data.attendees;
    body.append(
      toolbar(picker),
      counts,
      S.section("At the door", h("p", { class: "muted", text: "Type the code from the ticket. A ticket works once; a second try says when it was used." }), form, result),
      S.section("Guest list", search, Aira.xp.table({
        columns: [
          { key: "holder_name", label: "Name", render: (a) => h("span", {}, a.holder_name || a.buyer_name, a.holder_name && a.holder_name !== a.buyer_name ? h("div", { class: "muted small", text: `Bought by ${a.buyer_name}` }) : null, Aira.xp.phoneMeta(a.type_name, a.code)) },
          { key: "type_name", label: "Ticket", wide: true },
          { key: "code", label: "Code", wide: true },
          { key: "status", label: "Status", render: (a) => (a.order_status === "pending_payment" ? S.pill("unpaid", { unpaid: "warn" }) : S.pill(a.status, { valid: "info", checked_in: "good" })) },
          { key: "act", label: "", render: (a) => (a.status === "checked_in" ? S.button("Undo", async () => { await call(`/tickets/${a.id}/undo-checkin`, { method: "POST", body: {} }); rerender(); }, { kind: "ghost sm" }) : a.order_status === "confirmed" ? S.button("Check in", async () => { await doCheckIn(a.code); rerender(); }, { kind: "secondary sm" }) : "") },
        ],
        rows: list,
        empty: route.query.q ? "Nobody matches" : "No tickets sold yet",
      })),
    );
    setTimeout(() => code.focus(), 0);
  }

  // ---------------------------------------------------------------- promo codes
  async function promosTab(body, rerender) {
    const [data, events] = await Promise.all([call("/promos"), call("/events")]);
    const eventOptions = [["", "All events"], ...events.events.map((e) => [e.id, e.title])];
    const editPromo = (promo) => S.edit({
      title: promo ? `Edit ${promo.code}` : "New promo code",
      fields: [
        { name: "code", label: "Code", required: true, placeholder: "EARLYBIRD" },
        { name: "kind", label: "Discount", type: "select", options: [["percent", "Percent off"], ["amount", "Amount off each order"]] },
        { name: "amount_text", label: "Value", required: true, hint: "Percent: 20 means 20% off. Amount: 10.00 off." },
        { name: "event_id", label: "Event", type: "select", options: eventOptions },
        { name: "max_uses", label: "Use limit", type: "number", min: 0, hint: "Orders that may use it. 0 = no limit." },
        { name: "expires_at", label: "Expires", type: "datetime-local" },
        { name: "active", label: "Active", type: "checkbox" },
      ],
      values: promo ? { ...promo, amount_text: promo.kind === "amount" ? S.fromCents(promo.value) : String(promo.value), expires_at: toInput(promo.expires_at), active: !!promo.active } : { kind: "percent", active: true, max_uses: 0 },
      onSave: async (values) => {
        const body = { code: values.code, kind: values.kind, event_id: values.event_id, max_uses: values.max_uses || 0, expires_at: fromInput(values.expires_at), active: values.active };
        if (values.kind === "amount") body.amount = values.amount_text; else body.value = values.amount_text;
        return promo ? call(`/promos/${promo.id}`, { method: "PATCH", body }) : call("/promos", { method: "POST", body });
      },
    });
    body.append(
      h("p", { class: "muted", text: "Codes buyers type on the event page: reward early birds, partners and your community." }),
      h("div", { class: "btn-row" }, S.button("New promo code", async () => { if (await editPromo()) rerender(); })),
      Aira.xp.table({
        columns: [
          { key: "code", label: "Code", render: (p) => h("div", {}, h("strong", { text: p.code }), Aira.xp.phoneMeta(p.event_title || "All events", p.max_uses ? `${p.used} / ${p.max_uses} used` : `${p.used} used`)) },
          { key: "value", label: "Discount", render: (p) => (p.kind === "percent" ? `${p.value}% off` : `${S.money(p.value)} off`) },
          { key: "event_title", label: "Event", wide: true, render: (p) => p.event_title || "All events" },
          { key: "used", label: "Used", num: true, wide: true, render: (p) => (p.max_uses ? `${p.used} / ${p.max_uses}` : String(p.used)) },
          { key: "expires_at", label: "Expires", wide: true, render: (p) => when(p.expires_at) || "Never" },
          { key: "active", label: "Status", render: (p) => S.pill(p.active ? "active" : "off", { off: "" }) },
          { key: "act", label: "", render: (p) => S.button(p.used ? "Turn off" : "Delete", async () => { await call(`/promos/${p.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" }) },
        ],
        rows: data.promos,
        onRow: async (p) => { if (await editPromo(p)) rerender(); },
        empty: Aira.ui.empty("No promo codes yet", "Reward early birds, students and partners with a code they type on the event page."),
      }),
    );
  }

  // ---------------------------------------------------------------- sponsors
  async function sponsorsTab(body, rerender) {
    const [data, events] = await Promise.all([call("/sponsors"), call("/events")]);
    const editSponsor = (sponsor) => S.edit({
      title: sponsor ? sponsor.name : "New sponsor",
      fields: [
        { name: "name", label: "Sponsor", required: true },
        { name: "event_id", label: "Event", type: "select", options: [["", "All events / season"], ...events.events.map((e) => [e.id, e.title])] },
        { name: "tier", label: "Package", placeholder: "Gold, Silver, Drinks partner" },
        { name: "amount_cents", label: "Amount", type: "money" },
        { name: "status", label: "Status", type: "select", options: S.statusOptions(data.statuses), hint: "Paid sponsorships count as revenue." },
        { name: "contact_name", label: "Contact" },
        { name: "contact_email", label: "Contact email", type: "email" },
        { name: "notes", label: "Notes", type: "textarea", rows: 3, placeholder: "What they get: logo on posters, stage mention, booth" },
      ],
      values: sponsor || { status: "pitched" },
      onSave: (values) => (sponsor ? call(`/sponsors/${sponsor.id}`, { method: "PATCH", body: { ...values, amount_cents: values.amount_cents || 0 } }) : call("/sponsors", { method: "POST", body: { ...values, amount_cents: values.amount_cents || 0 } })),
    });
    body.append(
      S.kpis([
        { label: "Pitched", value: S.money(data.pipeline.pitched || 0) },
        { label: "Agreed", value: S.money(data.pipeline.agreed || 0), tone: "warn" },
        { label: "Paid", value: S.money(data.pipeline.paid || 0), tone: "good" },
      ]),
      h("div", { class: "btn-row" }, S.button("New sponsor", async () => { if (await editSponsor()) rerender(); })),
      Aira.xp.table({
        columns: [
          { key: "name", label: "Sponsor", render: (s) => h("div", {}, h("strong", { text: s.name }), Aira.xp.phoneMeta(s.tier, s.event_title || "All events")) },
          { key: "tier", label: "Package", wide: true },
          { key: "event_title", label: "Event", wide: true, render: (s) => s.event_title || "All events" },
          { key: "amount_cents", label: "Amount", num: true, render: (s) => S.money(s.amount_cents) },
          { key: "status", label: "Status", render: (s) => S.pill(s.status, { pitched: "", agreed: "warn", paid: "good", declined: "bad" }) },
          { key: "act", label: "", render: (s) => S.button("Delete", async () => { if (await S.confirmDelete(s.name)) { await call(`/sponsors/${s.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) },
        ],
        rows: data.sponsors,
        onRow: async (s) => { if (await editSponsor(s)) rerender(); },
        empty: Aira.ui.empty("No sponsors yet", "Local businesses pay to reach your audience: list packages and track them from pitch to paid."),
      }),
    );
  }

  // ---------------------------------------------------------------- waitlist
  async function waitlistTab(body, route, rerender) {
    const [data, events] = await Promise.all([call(`/waitlist?event_id=${encodeURIComponent(route.query.event || "")}`), call("/events")]);
    const picker = Aira.ui.select([["", "All events"], ...events.events.map((e) => [e.id, e.title])], route.query.event || "", { "aria-label": "Event", onchange: () => go({ tab: "waitlist", event: picker.value }) });
    body.append(
      h("p", { class: "muted", text: "People who asked for tickets after an event sold out. When seats free up (a cancellation, more tickets), offer them: they get an email with the event page." }),
      toolbar(picker),
      Aira.xp.table({
        columns: [
          { key: "name", label: "Name", render: (w) => h("span", {}, w.name, h("div", { class: "muted small", text: w.email }), Aira.xp.phoneMeta(w.event_title, plural(w.quantity, "ticket"))) },
          { key: "event_title", label: "Event", wide: true },
          { key: "quantity", label: "Tickets", num: true, wide: true },
          { key: "created_at", label: "Joined", wide: true, render: (w) => Aira.fmt.ago(w.created_at) },
          { key: "status", label: "Status", render: (w) => S.pill(w.status, { waiting: "warn", offered: "info" }) },
          { key: "act", label: "", render: (w) => h("div", { class: "btn-row" }, S.button(w.status === "offered" ? "Offer again" : "Offer seats", async () => { await call(`/waitlist/${w.id}/offer`, { method: "POST", body: {} }); Aira.toast("Offer waiting in the outbox.", "success"); rerender(); }, { kind: "secondary sm" }), S.button("Remove", async () => { await call(`/waitlist/${w.id}/status`, { method: "POST", body: { status: "removed" } }); rerender(); }, { kind: "ghost sm" })) },
        ],
        rows: data.waitlist,
        empty: Aira.ui.empty("Nobody is waiting", "When an event sells out, its page offers a waitlist instead."),
      }),
    );
  }

  // ---------------------------------------------------------------- the view
  let currentMain = null;
  // The tab an ask from "+ New" or the search box opens on.
  const ASK_TAB = { event: "events", order: "orders", partner: "partners" };

  Aira.views.events = async (main, route) => {
    const xp = Aira.xp;
    if (xp.take("events", route, (ask) => ASK_TAB[ask.new || ask.open])) return;
    await S.business();
    currentMain = main;
    const query = String(route.query.q || "").trim();
    const tabId = route.query.tab === "search" || TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "overview";
    const meta = TABS.find((t) => t.id === tabId);
    const title = tabId === "search" ? (query ? `Results for “${query}”` : "Search") : meta.title || meta.label;
    const createEvent = async () => { const e = await editEvent(); if (e) go({ tab: "events", event: e.id }); };
    const body = xp.head(main, {
      title,
      about: tabId === "search" ? "Courses, events, students, ticket orders and partners that match." : meta.about,
      phoneNew: tabId === "events" ? null : S.button("New event", createEvent, { icon: "plus" }),
    });
    document.title = `${title} · Courses & Events · Aira`;
    const tab = S.tabs(body, route, "events", TABS, tabId);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      panel.replaceChildren(Aira.ui.skeleton(4));
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "events" && route.query.event) await eventDetail(holder, route.query.event, render);
        else if (tab === "events") await eventsList(holder, route);
        else if (tab === "orders") await ordersTab(holder, route, render);
        else if (tab === "checkin") await checkinTab(holder, route, render);
        else if (tab === "promos") await promosTab(holder, render);
        else if (tab === "sponsors") await sponsorsTab(holder, render);
        else if (tab === "waitlist") await waitlistTab(holder, route, render);
        else if (tab === "partners") await xp.partnersTab(holder, route, go);
        else if (tab === "earnings") await xp.earningsTab(holder, route, go);
        else if (tab === "search") await xp.searchTab(holder, query);
        else await overview(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
    xp.answer("events", {
      new: {
        event: createEvent,
        partner: async () => { if (!xp.newPartner) return; const created = await xp.newPartner(); if (created) go({ tab: "partners", open: `partner:${created.id}` }); },
      },
      open: {
        order: (id) => openOrder(id, render),
        partner: (id) => xp.openPartner(id, () => go({ ...route.query, tab: "partners", t: Date.now() })),
      },
    });
  };
})();

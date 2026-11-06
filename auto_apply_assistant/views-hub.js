/* Aira — the Business hub (#/hub): every industry app at a glance.
 *
 * Tabs (?tab=): overview (revenue this month, by month and by app; each app's setup progress and numbers; what
 * customers did on public pages), customers (one record per person across the apps: search, the contact's page
 * and timeline with ?contact=<id>, merging two records, Send to Sales), revenue (every payment recorded, CSV), outbox (messages the apps prepared for
 * customers: approve, edit, cancel, send through your own mail server), business (the profile every public page
 * shows: name, contact, currency, brand colour, tax, how customers pay) and email (the mail server, which kinds of
 * message go out without approval, and the incoming mailbox read for replies, new requests and bounces).
 * Everything runs through /api/suite/* (aira/suite/suite_routes.py).
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;

  // Revenue and messages are kept per package (Shop, Bookings): their menu labels, then the apps' names.
  const APP_NAMES = { ...Object.fromEntries(Aira.APPS.map((app) => [app.id, app.name])), ...Object.fromEntries(Aira.NAV.filter((item) => item.app !== "account").map((item) => [item.id, item.label])) };
  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "customers", label: "Customers" },
    { id: "revenue", label: "Revenue" },
    { id: "outbox", label: "Outbox" },
    { id: "business", label: "Business profile" },
    { id: "email", label: "Email" },
  ];

  // The apps this person can open here: on for the organization and, hosted, in their menu (a member's own space
  // has the business apps but not Ads, which stays the organization's).
  const opens = (app) => Aira.apps.state.enabled[app.id] !== false && (!Aira.cloud || Aira.NAV.some((item) => item.app === app.id));

  async function overview(body) {
    const data = await Aira.api("/api/suite/home");
    S._business = data.business;
    const revenue = data.revenue;
    const change = revenue.change_percent;
    const parts = [];
    if (!data.profile_complete) {
      parts.push(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: "Add your business details" }), h("span", { class: "muted", text: "Customers see your name, contact and how to pay on every booking page, invoice and storefront." }), h("a", { class: "btn secondary sm", href: "#/hub?tab=business", text: "Add details" })));
    }
    const banner = reachabilityBanner(data.public_links);
    if (banner) parts.push(banner);
    if (data.payments_attention) parts.push(h("div", { class: "cloud-banner warn", role: "status" }, h("strong", { text: `${data.payments_attention} payment${data.payments_attention === 1 ? "" : "s"} to check` }), h("span", { class: "muted", text: "A different amount arrived, or something was cancelled before it was paid." }), h("a", { class: "btn secondary sm", href: "#/hub?tab=business", text: "Check payments" })));
    parts.push(S.kpis([
      { label: "Revenue this month", value: S.money(revenue.this_month_cents, revenue.currency), hint: change === null ? "First month" : `${change >= 0 ? "+" : ""}${change}% vs last month`, tone: change !== null && change < 0 ? "warn" : "good" },
      { label: "Last month", value: S.money(revenue.last_month_cents, revenue.currency) },
      { label: "All time", value: S.money(revenue.total_cents, revenue.currency), hint: `${revenue.payments} payments` },
      { label: "Waiting to send", value: data.outbox.pending || 0, hint: data.email_ready ? "Messages for customers" : "Set up email to send", tone: data.outbox.pending ? "warn" : "" },
      ...otherCurrencies(revenue),
    ]));
    parts.push(goalCard(data.goal, revenue));
    const started = data.apps.some((app) => app.progress.done > 0);
    if (!started) { const card = startHere(data.apps); if (card) parts.push(card); }
    parts.push(S.grid(
      S.section("Revenue by month", S.bars(revenue.by_month.map((m) => ({ label: m.month.slice(5), value: m.cents })), { format: (v) => S.money(v, revenue.currency) })),
      S.section("Revenue by app", Object.keys(revenue.by_app).length
        ? S.table({ columns: [{ key: "app", label: "App", render: (r) => APP_NAMES[r.app] || r.app }, { key: "cents", label: "Revenue", num: true, render: (r) => S.money(r.cents, revenue.currency) }], rows: Object.entries(revenue.by_app).map(([app, cents]) => ({ app, cents })).sort((a, b) => b.cents - a.cents) })
        : Aira.ui.empty("No payments yet", "Payments from invoices, orders, bookings, tickets and courses add up here.")),
    ));
    const installed = data.apps.filter(opens);
    parts.push(S.section("Your apps", h("div", { class: "suite-app-cards" }, ...installed.map((app) => h("article", { class: "suite-app-card" },
      h("header", {}, h("span", { class: "app-tile-icon" }, Aira.icon(app.id)), h("div", {}, h("strong", { text: app.name }), h("div", { class: "muted small", text: app.tagline }))),
      app.cards.length ? S.kpis(app.cards.slice(0, 4)) : null,
      app.checklist.length && app.progress.done < app.progress.total ? S.checklist(app.checklist) : app.checklist.length ? h("p", { class: "muted small", text: "Set up. Keep it going." }) : h("p", { class: "muted small", text: app.industry }),
      h("div", { class: "btn-row" }, ...(app.sections && app.sections.length > 1 ? app.sections.map((section) => h("a", { class: "btn secondary sm", href: `#/${section.id}`, text: `Open ${APP_NAMES[section.id] || section.id}` })) : [h("a", { class: "btn secondary sm", href: `#/${app.home || app.id}`, text: `Open ${app.name}` })])))))));
    parts.push(S.section("Recent activity", S.activity(data.activity)));
    body.append(...parts);
  }

  // Money in other currencies is never added to the business's own: one card each (this month, and all time).
  function otherCurrencies(revenue) {
    return (revenue.currencies || []).filter((code) => code !== revenue.currency).map((code) => {
      const totals = (revenue.by_currency || {})[code] || {};
      return { label: `Revenue in ${code}`, value: S.money(totals.this_month_cents || 0, code), hint: `All time ${S.money(totals.total_cents || 0, code)}` };
    });
  }

  // A monthly revenue goal keeps the number that matters in front of the business.
  function goalCard(goal, revenue) {
    const set = () => S.edit({ title: "Monthly revenue goal", fields: [{ name: "monthly_cents", label: `Goal per month (${revenue.currency})`, type: "money", required: true }], values: { monthly_cents: goal.monthly_cents || null }, submitLabel: "Set goal", onSave: async (values) => { await Aira.api("/api/suite/goal", { method: "POST", body: { monthly_cents: values.monthly_cents || 0 } }); Aira.router.render(); } });
    if (!goal.monthly_cents) return S.section("Set a monthly goal", h("p", { class: "muted", text: "Pick the revenue you want each month. The hub shows how close you are and what is left." }), h("div", { class: "btn-row" }, S.button("Set a goal", set, { kind: "secondary" })));
    const done = goal.progress_percent >= 100;
    return S.section(done ? "Goal reached this month" : "This month's goal",
      h("div", { class: "suite-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(goal.progress_percent) }, h("span", { style: { width: `${goal.progress_percent}%` } })),
      h("p", { class: "muted", text: done ? `${S.money(revenue.this_month_cents, revenue.currency)} of ${S.money(goal.monthly_cents, revenue.currency)}. Well done: raise the bar?` : `${S.money(revenue.this_month_cents, revenue.currency)} of ${S.money(goal.monthly_cents, revenue.currency)} (${goal.progress_percent}%). ${S.money(goal.remaining_cents, revenue.currency)} to go.` }),
      h("div", { class: "btn-row" }, S.button("Change goal", set, { kind: "ghost sm" })));
  }

  // A new business picks what it does; each answer opens the app made for it.
  function startHere(apps) {
    const enabled = apps.filter(opens);
    if (!enabled.length) return null;
    return S.section("Start here: what kind of business are you?", h("p", { class: "muted", text: "Open the app made for your work. You can use several: they share your customers, revenue and email." }),
      h("div", { class: "suite-app-cards" }, ...enabled.map((app) => h("a", { class: "suite-app-card", href: `#/${app.home || app.id}`, style: { textDecoration: "none", color: "inherit" } },
        h("header", {}, h("span", { class: "app-tile-icon" }, Aira.icon(app.id)), h("div", {}, h("strong", { text: app.industry }), h("div", { class: "muted small" }, h("span", { text: app.name }), ": ", h("span", { text: app.tagline }))))))));
  }

  // ------------------------------------------------------------------ customers
  // One record per person across the apps (aira/suite/customers.py), matched by email or phone: search, the
  // contact's page with its timeline (?contact=<id>), merging two records, and Send to Sales.
  async function customersTab(body, route, rerender) {
    const query = (route && route.query) || {};
    if (query.contact) return contactPage(body, query.contact, rerender);
    const params = new URLSearchParams(Object.entries({ q: query.q || "", app: query.app || "" }).filter(([, value]) => value));
    const data = await Aira.api(`/api/suite/customers?${params}`);
    const search = h("input", { type: "search", placeholder: "Search name, email, phone, company or tag", value: query.q || "", "aria-label": "Search customers" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") Aira.router.go(["hub"], { tab: "customers", q: search.value.trim(), app: query.app || "" }); });
    const appFilter = Aira.ui.select([["", "Every app"], ...data.apps.map((app) => [app, APP_NAMES[app] || app])], query.app || "", { "aria-label": "Seen in" });
    appFilter.addEventListener("change", () => Aira.router.go(["hub"], { tab: "customers", q: query.q || "", app: appFilter.value }));
    body.append(
      S.section("Customers",
        h("p", { class: "muted", text: "Everyone who ordered, booked, filled in a form, opened a case, bought tickets or enrolled, once per person: the apps match them by email or phone." }),
        h("div", { class: "btn-row cm-filters" }, search, appFilter),
        h("p", { class: "muted small", text: `${data.total} ${data.total === 1 ? "customer" : "customers"}` }),
        S.table({
          columns: [
            { key: "name", label: "Name", render: (c) => h("span", {}, h("strong", { text: c.name || c.email || c.phone }), c.company ? h("div", { class: "muted small", text: c.company }) : null) },
            { key: "email", label: "Email or phone", render: (c) => [c.email, c.phone].filter(Boolean).join(" · ") },
            { key: "apps", label: "Seen in", render: (c) => c.apps.map((app) => APP_NAMES[app] || app).join(", ") },
            { key: "last_seen", label: "Last seen", render: (c) => Aira.fmt.ago(c.last_seen) },
          ],
          rows: data.contacts,
          onRow: (c) => Aira.router.go(["hub"], { tab: "customers", contact: c.id }),
          empty: params.toString() ? Aira.ui.empty("Nobody matches", "Try another name, email or phone.") : Aira.ui.empty("No customers yet", "People who order, book, fill in a form or write to you show up here, once each."),
          caption: "Customers",
        })),
    );
  }

  function consentText(value) {
    if (value === null || value === undefined) return "Not asked";
    return value ? "Yes" : "No";
  }

  async function contactPage(body, id, rerender) {
    const data = await Aira.api(`/api/suite/customers/${id}`);
    const c = data.contact;
    const label = c.name || c.email || c.phone;
    const edit = () => S.edit({
      title: `Edit ${label}`,
      fields: [{ name: "name", label: "Name" }, { name: "company", label: "Company" }, { name: "tags", label: "Tags", placeholder: "vip, wholesale" }, { name: "notes", label: "Notes", type: "textarea", rows: 4, hint: "Only your team sees these." }],
      values: { ...c, tags: c.tags.join(", ") },
      onSave: async (values) => { await Aira.api(`/api/suite/customers/${id}`, { method: "PATCH", body: values }); rerender(); },
    });
    const toSales = async () => {
      const result = await Aira.api(`/api/suite/customers/${id}/sales`, { method: "POST", body: {} });
      Aira.toast(result.created ? "Sent to Sales: a new deal is in your pipeline." : "This customer is in Sales already.", "success");
      Aira.router.go(["studio"], { tab: "deals", deal: result.deal.id });
    };
    const paid = (data.paid || []).map((entry) => ({ label: data.paid.length > 1 ? `Paid in ${entry.currency}` : "Paid", value: S.money(entry.cents, entry.currency), tone: entry.cents ? "good" : "" }));
    body.append(
      h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm", href: "#/hub?tab=customers", text: "All customers" })),
      S.section(label,
        Aira.ui.kv([
          ["Email", c.email ? h("a", { href: `mailto:${c.email}`, text: c.email }) : ""],
          ["Phone", c.phone ? h("a", { href: `tel:${c.phone.replace(/[^0-9+]/g, "")}`, text: c.phone }) : ""],
          ["Company", c.company],
          ["Tags", c.tags.join(", ")],
          ["Email news", consentText(c.email_consent)],
          ["Seen in", c.apps.map((app) => APP_NAMES[app] || app).join(", ")],
          ["First seen", S.when(c.first_seen)],
          ["Last seen", S.when(c.last_seen)],
          ["Notes", c.notes],
        ]),
        h("div", { class: "btn-row" },
          S.button("Edit", edit, { kind: "secondary sm" }),
          S.button("Merge with another record", () => mergeDialog(c, rerender), { kind: "secondary sm" }),
          data.sales_available ? S.button("Send to Sales", toSales, { kind: "sm" }) : null)),
      data.possible_duplicates ? h("div", { class: "cloud-banner warn", role: "status" }, h("strong", { text: "A payment may be counted twice" }), h("span", { class: "muted", text: "Two apps recorded the same amount within a week. If it is one payment, mark it not paid in one of them." })) : null,
      S.kpis([...paid, { label: "Payments", value: data.payments }, { label: "Sales deals", value: (data.deals || []).length }]),
      S.section("Timeline", timelineList(data.timeline)),
    );
  }

  function timelineList(items) {
    if (!items.length) return Aira.ui.empty("Nothing yet", "Orders, bookings, forms, cases, payments and emails show up here.");
    return h("ul", { class: "suite-activity hub-timeline" }, ...items.map((item) => h("li", {},
      h("span", { class: `suite-dot ${item.kind === "payment" ? "public" : ""}`, "aria-hidden": "true" }),
      h("div", {},
        item.href ? h("a", { href: item.href, text: item.title }) : h("strong", { text: item.title }),
        item.detail ? h("span", { class: "muted", text: ` ${item.detail}` }) : null,
        item.possible_duplicate ? h("span", { class: "suite-pill warn", text: "may be counted twice" }) : null,
        h("div", { class: "muted small", text: `${APP_NAMES[item.app] || item.app} · ${S.when(item.at)}` })))));
  }

  // Merge: find the other record of the same person; it joins this one (its orders, bookings and forms move here).
  function mergeDialog(keep, rerender) {
    const keepLabel = keep.name || keep.email || keep.phone;
    const input = h("input", { type: "search", placeholder: "Search name, email or phone", "aria-label": "Find the other record" });
    const results = h("div", { class: "suite-body" }, h("p", { class: "muted", text: "Search for the other record of this person." }));
    let dialog = null;
    const find = async () => {
      const text = input.value.trim();
      if (!text) return;
      const data = await Aira.api(`/api/suite/customers?${new URLSearchParams({ q: text, limit: "20" })}`);
      const others = data.contacts.filter((other) => other.id !== keep.id);
      results.replaceChildren(others.length ? h("ul", { class: "suite-activity" }, ...others.map((other) => h("li", {},
        h("div", {}, h("strong", { text: other.name || other.email || other.phone }), h("span", { class: "muted", text: ` ${[other.email, other.phone, other.company].filter(Boolean).join(" · ")}` })),
        S.button("Merge", async () => {
          const otherLabel = other.name || other.email || other.phone;
          if (!(await Aira.confirm({ title: "Merge two records", message: `Merge ${otherLabel} into ${keepLabel}? Their orders, bookings, forms and cases move to ${keepLabel}, and the other record is removed. This cannot be undone.`, confirmLabel: "Merge", danger: true }))) return;
          const result = await Aira.api(`/api/suite/customers/${keep.id}/merge`, { method: "POST", body: { other_id: other.id } });
          Aira.toast(`Merged: ${result.moved} ${result.moved === 1 ? "record" : "records"} moved.`, "success");
          if (dialog) dialog.close();
          rerender();
        }, { kind: "secondary sm" })))) : h("p", { class: "muted", text: "Nobody else matches." }));
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); find(); } });
    dialog = Aira.modal({
      title: `Merge with ${keepLabel}`,
      body: h("div", { class: "suite-body" }, h("div", { class: "btn-row" }, input, S.button("Search", find, { kind: "secondary sm" })), results),
      actions: [{ label: "Close", kind: "ghost" }],
    });
  }

  async function revenueTab(body) {
    const data = await Aira.api("/api/suite/revenue");
    const summary = data.summary;
    body.append(
      S.kpis([
        { label: "This month", value: S.money(summary.this_month_cents, summary.currency) },
        { label: "All time", value: S.money(summary.total_cents, summary.currency) },
        { label: "Payments", value: summary.payments },
        summary.refunded_cents ? { label: "Refunded", value: S.money(summary.refunded_cents, summary.currency), hint: "Counted in the month of each refund", tone: "warn" } : null,
        ...otherCurrencies(summary),
      ].filter(Boolean)),
      S.section("Payments", h("div", { class: "btn-row" }, S.button("Download CSV", () => Aira.download("/api/suite/revenue?format=csv", "revenue.csv"), { kind: "secondary sm" })),
        S.table({
          columns: [
            { key: "at", label: "Date", render: (r) => S.day(r.at) },
            { key: "app", label: "App", render: (r) => APP_NAMES[r.app] || r.app },
            // a sale recorded by two apps (a Marketing lead and its Sales invoice) counts once, in the later one
            { key: "label", label: "For", render: (r) => (r.covered_by ? h("span", {}, r.label, h("div", { class: "muted small", text: `Counted in ${APP_NAMES[r.covered_by.split(":")[0]] || r.covered_by.split(":")[0]} instead` })) : r.label) },
            { key: "customer", label: "Customer" },
            { key: "amount_cents", label: "Amount", num: true, render: (r) => (r.voided_at ? h("s", { text: S.money(r.amount_cents, r.currency) }) : S.money(r.amount_cents, r.currency)) },
            { key: "currency", label: "Currency" },
          ],
          rows: data.lines,
          empty: "No payments recorded yet",
        })),
    );
  }

  async function outboxTab(body, rerender) {
    const data = await Aira.api("/api/suite/outbox");
    const pending = data.messages.filter((m) => m.status === "pending" || m.status === "failed");
    const approved = data.messages.filter((m) => m.status === "approved");
    const ids = (rows) => rows.map((m) => m.id);
    const actions = h("div", { class: "btn-row" },
      pending.length ? S.button(`Approve ${pending.length}`, async () => { await Aira.api("/api/suite/outbox/approve", { method: "POST", body: { ids: ids(pending) } }); rerender(); }) : null,
      approved.length ? S.button(`Send ${approved.length} now`, async () => { const result = await Aira.api("/api/suite/outbox/send", { method: "POST", body: {} }); Aira.toast(`${result.sent} sent${result.failed ? `, ${result.failed} failed` : ""}.`, result.failed ? "error" : "success"); rerender(); }, { kind: "secondary" }) : null);
    body.append(
      S.kpis([
        { label: "Waiting for approval", value: data.counts.pending, tone: data.counts.pending ? "warn" : "" },
        { label: "Approved", value: data.counts.approved },
        { label: "Sent", value: data.counts.sent, tone: "good" },
        { label: "Failed", value: data.counts.failed, tone: data.counts.failed ? "bad" : "" },
      ]),
      S.section("Messages to customers", h("p", { class: "muted", text: "Reminders, confirmations, invoices and tickets the apps prepared. Nothing is sent until you approve it, unless you turned on automatic sending for that kind under Email." }), actions,
        S.table({
          columns: [
            { key: "created_at", label: "Created", render: (m) => Aira.fmt.ago(m.created_at) },
            { key: "app", label: "App", render: (m) => APP_NAMES[m.app] || m.app },
            { key: "recipient", label: "To" },
            { key: "subject", label: "Subject" },
            { key: "status", label: "Status", render: (m) => S.pill(m.status) },
          ],
          rows: data.messages,
          empty: "No messages yet",
          onRow: (message) => openMessage(message, rerender),
        })),
    );
  }

  function openMessage(message, rerender) {
    const editable = message.status === "pending" || message.status === "failed";
    const fields = [
      { name: "recipient", label: "To", type: "email", required: true },
      { name: "subject", label: "Subject", required: true },
      { name: "body", label: "Message", type: "textarea", rows: 10, required: true },
    ];
    if (!editable) {
      Aira.modal({ title: message.subject, body: h("div", {}, Aira.ui.kv([["To", message.recipient], ["Status", message.status], ["Sent", S.when(message.sent_at)], ["Error", message.error]]), h("pre", { class: "suite-pre", text: message.body })), actions: [{ label: "Close", kind: "secondary" }] });
      return;
    }
    S.edit({ title: "Message", fields, values: message, submitLabel: "Save and approve", onSave: async (values) => {
      await Aira.api(`/api/suite/outbox/${message.id}`, { method: "PATCH", body: values });
      await Aira.api("/api/suite/outbox/approve", { method: "POST", body: { ids: [message.id] } });
      rerender();
    } });
  }

  // Where customers' links point: the hub warns while they point to this computer (127.0.0.1), which nobody else can open.
  function reachabilityBanner(links) {
    if (Aira.cloud || !links || !links.loopback) return null;
    return h("div", { class: "cloud-banner warn", role: "status" }, h("strong", { text: "Customers cannot open your links yet" }), h("span", { class: "muted", text: links.warning }), h("a", { class: "btn secondary sm", href: "#/hub?tab=business", text: "Set a public address" }));
  }

  // Business profile > Payments: none, your own payment link, or Razorpay (UPI, cards, netbanking) with a payment
  // link for the exact amount of every order, invoice and booking. Secrets are typed here and never shown again.
  async function paymentsSection() {
    const data = await Aira.api("/api/suite/payments");
    const holder = h("div");
    const render = (view) => {
      const s = view.settings;
      const form = S.form([
        { name: "provider", label: "How customers pay", type: "select", options: [["none", "No online payment (show my payment instructions)"], ["manual", "My own payment link (from the profile above)"], ["razorpay", "Razorpay: UPI, cards and netbanking for the exact amount"]] },
        { type: "heading", label: "Razorpay" },
        { name: "razorpay_key_id", label: "Key id", placeholder: "rzp_test_... or rzp_live_...", hint: "Razorpay dashboard > Account & Settings > API keys. rzp_test_ keys take test payments only." },
        { name: "key_secret", label: view.has_key_secret ? "Key secret (saved; type to replace)" : "Key secret", type: "password", hint: "Stored encrypted on this computer and never shown again." },
        { name: "webhook_secret", label: view.has_webhook_secret ? "Webhook secret (saved; type to replace)" : "Webhook secret", type: "password", hint: "The secret you type when you add the webhook in the Razorpay dashboard." },
        { name: "notify_customer", label: "Razorpay also emails and texts the payment link to the customer", type: "checkbox" },
        { name: "link_expiry_hours", label: "A payment link for an invoice or a booking stays open (hours)", type: "number", min: 1, max: 4320 },
        { type: "heading", label: "UPI apps (optional)" },
        { name: "upi_id", label: "Your UPI ID", placeholder: "yourshop@okaxis", hint: "Adds a 'Pay with a UPI app' button on phones. You confirm these payments by hand." },
        { name: "upi_name", label: "Name shown in the UPI app", placeholder: "Your business name" },
      ], s);
      const status = view.ready ? S.pill(`ready · ${view.mode} mode`, { [`ready · ${view.mode} mode`]: view.mode === "live" ? "good" : "warn" }) : S.pill(s.provider === "razorpay" ? "not ready" : s.provider, { "not ready": "warn" });
      const webhook = view.webhook_url ? h("div", { class: "suite-form" },
        h("h3", { class: "suite-h3", text: "Webhook" }),
        h("p", { class: "muted", text: "In the Razorpay dashboard: Account & Settings > Webhooks > Add new webhook. Paste this address, type the same secret as above, and tick these events:" }),
        h("div", { class: "btn-row" }, h("code", { class: "suite-pre", text: view.webhook_url }), S.button("Copy", async () => { try { await navigator.clipboard.writeText(view.webhook_url); Aira.toast("Copied.", "success"); } catch { /* the address stays visible */ } }, { kind: "ghost sm" })),
        h("p", { class: "muted small", text: view.webhook_events.join(", ") }),
        view.webhook_reachable ? null : h("p", { class: "form-error", text: "Razorpay cannot reach this address: it is this computer. Set a public address above (your tunnel) first." })) : null;
      const attention = view.attention && view.attention.length ? S.section("Payments to check", S.table({
        columns: [
          { key: "created_at", label: "When", render: (r) => S.day(r.created_at) },
          { key: "app", label: "App", render: (r) => APP_NAMES[r.app] || r.app },
          { key: "description", label: "For" },
          { key: "amount_cents", label: "Asked", num: true, render: (r) => S.money(r.amount_cents, r.currency) },
          { key: "problem", label: "What happened" },
        ],
        rows: view.attention,
      })) : null;
      holder.replaceChildren(S.section("Payments", h("p", { class: "muted", text: "Take payments online for orders, invoices, bookings and courses. Each payment link carries the exact amount and its reference, and Razorpay tells Monosage when it is paid: the order, booking or enrolment is marked paid for you." }), status, form.node,
        h("div", { class: "btn-row" },
          S.button("Save payments", async () => {
            form.setError("");
            const values = form.read();
            if (!values.key_secret) delete values.key_secret;
            if (!values.webhook_secret) delete values.webhook_secret;
            try { render(await Aira.api("/api/suite/payments", { method: "POST", body: values })); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); }
          }),
          s.provider === "razorpay" ? S.button("Test connection", async () => {
            try { const result = await Aira.api("/api/suite/payments/test", { method: "POST", body: {} }); Aira.toast(`Razorpay answered: the keys work (${result.mode} mode).`, "success"); } catch (error) { form.setError(error.message); }
          }, { kind: "secondary" }) : null),
        webhook), attention || "");
    };
    render(data);
    return holder;
  }

  async function businessTab(body) {
    const data = await Aira.api("/api/suite/business");
    const states = [["", "Not set"], ...(data.gst_states || []).map((s) => [s.code, `${s.name} (${s.code})`])];
    const form = S.form([
      { type: "heading", label: "Who you are" },
      { name: "name", label: "Business name", required: true },
      { name: "tagline", label: "Tagline", placeholder: "Hair and colour studio in Austin" },
      { name: "email", label: "Email", type: "email" },
      { name: "phone", label: "Phone", type: "tel" },
      { name: "website", label: "Website", type: "url", placeholder: "https://" },
      { name: "address", label: "Address", type: "textarea", rows: 2 },
      { name: "logo_url", label: "Logo link", type: "url", placeholder: "https://.../logo.png" },
      { name: "accent", label: "Brand colour", type: "color" },
      { name: "sell_to", label: "Who you sell to", type: "select", options: [["consumers", "Consumers (B2C)"], ["businesses", "Businesses (B2B)"], ["both", "Businesses and consumers (B2B and B2C)"]], hint: "Selling to businesses adds trade accounts, trade prices, minimum quantities and payment terms to your shop." },
      { type: "heading", label: "Money" },
      { name: "currency", label: "Currency", type: "select", options: data.currencies },
      { name: "tax_label", label: "Tax name", placeholder: "GST, VAT, Sales tax" },
      { name: "tax_rate_bp", label: "Tax rate (hundredths of a percent)", type: "number", min: 0, max: 5000, hint: "750 = 7.5%. Added to invoices and orders. 0 for none." },
      { name: "tax_id", label: "Your tax or registration number", placeholder: "VAT, GSTIN, EIN", hint: "Printed on invoices to businesses." },
      { name: "prices_include_tax", label: "My prices already include tax", type: "checkbox", hint: "The tax is shown inside the price instead of added on top." },
      { name: "tax_mode", label: "Tax", type: "select", options: [["flat", "One rate on every invoice and order"], ["gst", "GST (India): CGST + SGST in my state, IGST to other states"]], hint: "GST needs your GSTIN as your tax number and prices in INR." },
      { name: "state_code", label: "Your state (GST)", type: "select", options: states, hint: "Read from your GSTIN when left empty." },
      { name: "payment_link", label: "Payment link", type: "url", hint: "Your own Stripe, PayPal, Razorpay or UPI payment page. Customers see it on invoices, orders and bookings when Payments below is set to 'My own payment link'." },
      { name: "payment_instructions", label: "Other ways to pay", type: "textarea", rows: 3, placeholder: "Bank transfer: ... or pay at the venue" },
      ...(Aira.cloud ? [] : [
        { type: "heading", label: "Public address" },
        { name: "public_address", label: "Public address", type: "url", placeholder: "https://shop.example.com", hint: "Monosage runs on this computer, so customers reach your pages through a tunnel: run Cloudflare Tunnel (cloudflared tunnel --url http://127.0.0.1:8765) or ngrok (ngrok http 8765) and enter the https address it gives you. Only your customer pages and the payment webhook answer there." },
      ]),
    ], { ...data.business, public_address: data.public_address || "" });
    body.append(...[reachabilityBanner(data.public_links), S.section("Business profile", h("p", { class: "muted", text: "Shown on every page you share with customers." }), form.node,
      h("div", { class: "btn-row" }, S.button("Save", async () => { form.setError(""); try { S._business = (await Aira.api("/api/suite/business", { method: "POST", body: form.read() })).business; Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); } })))].filter(Boolean));
    body.append(await paymentsSection());
  }

  // "Send without approval": one labelled box per kind of message, grouped. The server says which are on (until the
  // business picks, the transactional ones a customer is waiting for: confirmations, tickets, receipts).
  const KIND_GROUPS = [
    ["transactional", "Confirmations and receipts customers are waiting for"],
    ["replies", "Replies, invoices and payment requests"],
    ["reminders", "Reminders and follow-ups"],
    ["marketing", "Marketing"],
    ["other", "Other"],
  ];
  const kindField = (id) => `auto__${id}`;

  async function emailTab(body, rerender) {
    const [data, inbound] = await Promise.all([Aira.api("/api/suite/email"), Aira.api("/api/suite/inbound")]);
    const kinds = data.kinds || [];
    const kindFields = KIND_GROUPS.flatMap(([group, label]) => {
      const items = kinds.filter((kind) => kind.group === group);
      return items.length ? [{ type: "heading", label }, ...items.map((kind) => ({ name: kindField(kind.id), label: kind.label, type: "checkbox" }))] : [];
    });
    const form = S.form([
      { name: "from_name", label: "Sender name" },
      { name: "from_email", label: "Sender address", type: "email", required: true },
      { name: "reply_to", label: "Reply-to address", type: "email" },
      { name: "smtp_host", label: "Mail server", placeholder: "smtp.gmail.com", required: true },
      { name: "smtp_port", label: "Port", type: "number", min: 1, max: 65535 },
      { name: "smtp_security", label: "Security", type: "select", options: [["starttls", "STARTTLS (587)"], ["ssl", "SSL (465)"], ["none", "None"]] },
      { name: "smtp_username", label: "Username" },
      { name: "smtp_password", label: data.has_password ? "Password (saved; type to replace)" : "Password", type: "password", hint: "For Gmail, use an app password. Stored encrypted and never shown again." },
    ], data.settings);
    const sending = S.form(kindFields, Object.fromEntries(kinds.map((kind) => [kindField(kind.id), kind.on])));
    sending.node.classList.add("hub-auto-send");
    body.append(S.section("Email", h("p", { class: "muted", text: "Messages to your customers go out through your own mail server, from your own address." }), data.ready ? S.pill("ready", { ready: "good" }) : S.pill("not set up", { "not set up": "warn" }), form.node,
      h("h3", { class: "suite-h3", text: "Send without approval" }),
      h("p", { class: "muted", text: "Ticked messages go out as soon as an app makes them. Everything else waits in the Outbox for you to approve. Notices to your own team and caregivers always go out." }),
      sending.node,
      h("div", { class: "btn-row" }, S.button("Save", async () => {
        form.setError("");
        const values = form.read();
        const chosen = sending.read();
        values.auto_send = kinds.filter((kind) => chosen[kindField(kind.id)]).map((kind) => kind.id);
        if (!values.smtp_password) delete values.smtp_password;
        try { await Aira.api("/api/suite/email", { method: "POST", body: values }); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); }
      }))));
    body.append(inboundSection(inbound, rerender));
  }

  // Incoming mail: the business's own mailbox, read every few minutes for replies to cases and leads, new requests
  // (they become Service cases), unsubscribes and bounces. The mailbox itself is never changed.
  function inboundSection(data, rerender) {
    const settings = data.settings;
    const form = S.form([
      { name: "enabled", label: "Read incoming mail", type: "checkbox", hint: "Replies to your emails join their case or lead; new emails become Service cases; bounced addresses are not written to again." },
      { name: "imap_host", label: "Mail server (IMAP)", placeholder: "imap.gmail.com" },
      { name: "imap_port", label: "Port", type: "number", min: 1, max: 65535 },
      { name: "imap_security", label: "Security", type: "select", options: [["ssl", "SSL (993)"], ["starttls", "STARTTLS (143)"], ["none", "None (a mail bridge on this computer only)"]] },
      { name: "imap_username", label: "Username" },
      { name: "imap_password", label: data.has_password ? "Password (saved; type to replace)" : "Password", type: "password", hint: "For Gmail, use an app password. Stored encrypted and never shown again." },
      { name: "folder", label: "Folder", placeholder: "INBOX" },
    ], settings);
    const status = data.last_error
      ? h("p", { class: "form-error", role: "status", text: `Last check failed: ${data.last_error}` })
      : h("p", { class: "muted", text: data.last_checked_at ? `Last checked ${Aira.fmt.ago(data.last_checked_at)}. ${data.handled || 0} messages read so far.` : "Not checked yet. The first check starts from your newest email: older mail is not imported." });
    const recent = (data.recent || []).length ? S.table({
      caption: "Recent incoming mail",
      columns: [
        { key: "received_at", label: "Read", render: (m) => Aira.fmt.ago(m.received_at) },
        { key: "from_email", label: "From" },
        { key: "subject", label: "Subject" },
        { key: "detail", label: "What happened" },
      ],
      rows: data.recent,
    }) : null;
    const save = async () => {
      form.setError("");
      const values = form.read();
      if (!values.imap_password) delete values.imap_password;
      try { await Aira.api("/api/suite/inbound", { method: "POST", body: values }); Aira.toast("Saved.", "success"); rerender(); } catch (error) { form.setError(error.message); }
    };
    const check = async () => {
      form.setError("");
      try {
        const result = await Aira.api("/api/suite/inbound/check", { method: "POST", body: {} });
        if (result.result && result.result.error) form.setError(result.result.error);
        else { Aira.toast(`${(result.result && result.result.read) || 0} new messages read.`, "success"); rerender(); }
      } catch (error) { form.setError(error.message); }
    };
    const section = S.section("Incoming mail", h("p", { class: "muted", text: "Monosage reads your mailbox to follow up replies. Messages stay unread in your own mail program." }),
      settings.enabled ? S.pill("on", { on: "good" }) : S.pill("off", { off: "" }), status, form.node,
      h("div", { class: "btn-row" }, S.button("Save", save), settings.enabled ? S.button("Check now", check, { kind: "secondary" }) : null),
      recent);
    section.classList.add("hub-inbound");
    return section;
  }

  Aira.views.hub = async (main, route) => {
    const body = S.page(main, { title: "Business hub", subtitle: "Revenue, customers and setup across your industry apps." });
    const tab = S.tabs(body, route, "hub", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      panel.replaceChildren(Aira.ui.skeleton(4));
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "customers") await customersTab(holder, route, render);
        else if (tab === "revenue") await revenueTab(holder);
        else if (tab === "outbox") await outboxTab(holder, render);
        else if (tab === "business") await businessTab(holder);
        else if (tab === "email") await emailTab(holder, render);
        else await overview(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

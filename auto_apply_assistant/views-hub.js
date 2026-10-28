/* Aira — the Business hub (#/hub): every industry app at a glance.
 *
 * Tabs (?tab=): overview (revenue this month, by month and by app; each app's setup progress and numbers; what
 * customers did on public pages), revenue (every payment recorded, CSV), outbox (messages the apps prepared for
 * customers: approve, edit, cancel, send through your own mail server), business (the profile every public page
 * shows: name, contact, currency, brand colour, tax, how customers pay) and email (the mail server).
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
    { id: "revenue", label: "Revenue" },
    { id: "outbox", label: "Outbox" },
    { id: "business", label: "Business profile" },
    { id: "email", label: "Email" },
  ];

  async function overview(body) {
    const data = await Aira.api("/api/suite/home");
    S._business = data.business;
    const revenue = data.revenue;
    const change = revenue.change_percent;
    const parts = [];
    if (!data.profile_complete) {
      parts.push(h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: "Add your business details" }), h("span", { class: "muted", text: "Customers see your name, contact and how to pay on every booking page, invoice and storefront." }), h("a", { class: "btn secondary sm", href: "#/hub?tab=business", text: "Add details" })));
    }
    parts.push(S.kpis([
      { label: "Revenue this month", value: S.money(revenue.this_month_cents, revenue.currency), hint: change === null ? "First month" : `${change >= 0 ? "+" : ""}${change}% vs last month`, tone: change !== null && change < 0 ? "warn" : "good" },
      { label: "Last month", value: S.money(revenue.last_month_cents, revenue.currency) },
      { label: "All time", value: S.money(revenue.total_cents, revenue.currency), hint: `${revenue.payments} payments` },
      { label: "Waiting to send", value: data.outbox.pending || 0, hint: data.email_ready ? "Messages for customers" : "Set up email to send", tone: data.outbox.pending ? "warn" : "" },
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
    const installed = data.apps.filter((app) => Aira.apps.state.enabled[app.id] !== false);
    parts.push(S.section("Your apps", h("div", { class: "suite-app-cards" }, ...installed.map((app) => h("article", { class: "suite-app-card" },
      h("header", {}, h("span", { class: "app-tile-icon" }, Aira.icon(app.id)), h("div", {}, h("strong", { text: app.name }), h("div", { class: "muted small", text: app.tagline }))),
      app.cards.length ? S.kpis(app.cards.slice(0, 4)) : null,
      app.checklist.length && app.progress.done < app.progress.total ? S.checklist(app.checklist) : app.checklist.length ? h("p", { class: "muted small", text: "Set up. Keep it going." }) : h("p", { class: "muted small", text: app.industry }),
      h("div", { class: "btn-row" }, ...(app.sections && app.sections.length > 1 ? app.sections.map((section) => h("a", { class: "btn secondary sm", href: `#/${section.id}`, text: `Open ${APP_NAMES[section.id] || section.id}` })) : [h("a", { class: "btn secondary sm", href: `#/${app.home || app.id}`, text: `Open ${app.name}` })])))))));
    parts.push(S.section("Recent activity", S.activity(data.activity)));
    body.append(...parts);
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
    const enabled = apps.filter((app) => Aira.apps.state.enabled[app.id] !== false);
    if (!enabled.length) return null;
    return S.section("Start here: what kind of business are you?", h("p", { class: "muted", text: "Open the app made for your work. You can use several: they share your customers, revenue and email." }),
      h("div", { class: "suite-app-cards" }, ...enabled.map((app) => h("a", { class: "suite-app-card", href: `#/${app.home || app.id}`, style: { textDecoration: "none", color: "inherit" } },
        h("header", {}, h("span", { class: "app-tile-icon" }, Aira.icon(app.id)), h("div", {}, h("strong", { text: app.industry }), h("div", { class: "muted small" }, h("span", { text: app.name }), ": ", h("span", { text: app.tagline }))))))));
  }

  async function revenueTab(body) {
    const data = await Aira.api("/api/suite/revenue");
    const summary = data.summary;
    body.append(
      S.kpis([
        { label: "This month", value: S.money(summary.this_month_cents, summary.currency) },
        { label: "All time", value: S.money(summary.total_cents, summary.currency) },
        { label: "Payments", value: summary.payments },
      ]),
      S.section("Payments", h("div", { class: "btn-row" }, S.button("Download CSV", () => Aira.download("/api/suite/revenue?format=csv", "revenue.csv"), { kind: "secondary sm" })),
        S.table({
          columns: [
            { key: "at", label: "Date", render: (r) => S.day(r.at) },
            { key: "app", label: "App", render: (r) => APP_NAMES[r.app] || r.app },
            { key: "label", label: "For" },
            { key: "customer", label: "Customer" },
            { key: "amount_cents", label: "Amount", num: true, render: (r) => (r.voided_at ? h("s", { text: S.money(r.amount_cents, r.currency) }) : S.money(r.amount_cents, r.currency)) },
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

  async function businessTab(body) {
    const data = await Aira.api("/api/suite/business");
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
      { type: "heading", label: "Money" },
      { name: "currency", label: "Currency", type: "select", options: data.currencies },
      { name: "tax_label", label: "Tax name", placeholder: "GST, VAT, Sales tax" },
      { name: "tax_rate_bp", label: "Tax rate (hundredths of a percent)", type: "number", min: 0, max: 5000, hint: "750 = 7.5%. Added to invoices and orders. 0 for none." },
      { name: "payment_link", label: "Payment link", type: "url", hint: "Your own Stripe, PayPal, Razorpay or UPI payment page. Customers see it on invoices, orders and bookings." },
      { name: "payment_instructions", label: "Other ways to pay", type: "textarea", rows: 3, placeholder: "Bank transfer: ... or pay at the venue" },
    ], data.business);
    body.append(S.section("Business profile", h("p", { class: "muted", text: "Shown on every page you share with customers." }), form.node,
      h("div", { class: "btn-row" }, S.button("Save", async () => { form.setError(""); try { S._business = (await Aira.api("/api/suite/business", { method: "POST", body: form.read() })).business; Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); } }))));
  }

  async function emailTab(body) {
    const data = await Aira.api("/api/suite/email");
    const form = S.form([
      { name: "from_name", label: "Sender name" },
      { name: "from_email", label: "Sender address", type: "email", required: true },
      { name: "reply_to", label: "Reply-to address", type: "email" },
      { name: "smtp_host", label: "Mail server", placeholder: "smtp.gmail.com", required: true },
      { name: "smtp_port", label: "Port", type: "number", min: 1, max: 65535 },
      { name: "smtp_security", label: "Security", type: "select", options: [["starttls", "STARTTLS (587)"], ["ssl", "SSL (465)"], ["none", "None"]] },
      { name: "smtp_username", label: "Username" },
      { name: "smtp_password", label: data.has_password ? "Password (saved; type to replace)" : "Password", type: "password", hint: "For Gmail, use an app password. Stored encrypted and never shown again." },
      { name: "auto_send_text", label: "Send these without approval", placeholder: "booking_confirmation, ticket, enrolment", hint: "Kinds of message that go out as soon as they are made. Leave empty to approve everything yourself." },
    ], { ...data.settings, auto_send_text: (data.settings.auto_send || []).join(", ") });
    body.append(S.section("Email", h("p", { class: "muted", text: "Messages to your customers go out through your own mail server, from your own address." }), data.ready ? S.pill("ready", { ready: "good" }) : S.pill("not set up", { "not set up": "warn" }), form.node,
      h("div", { class: "btn-row" }, S.button("Save", async () => {
        form.setError("");
        const values = form.read();
        values.auto_send = String(values.auto_send_text || "").split(",").map((v) => v.trim()).filter(Boolean);
        delete values.auto_send_text;
        if (!values.smtp_password) delete values.smtp_password;
        try { await Aira.api("/api/suite/email", { method: "POST", body: values }); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); }
      }))));
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
        if (tab === "revenue") await revenueTab(holder);
        else if (tab === "outbox") await outboxTab(holder, render);
        else if (tab === "business") await businessTab(holder);
        else if (tab === "email") await emailTab(holder);
        else await overview(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

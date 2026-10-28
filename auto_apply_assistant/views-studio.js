/* Aira — Sales (#/studio): win and bill client work (agencies, freelancers, consultants, sales teams).
 *
 * Tabs (?tab=): overview (pipeline, what is owed, overdue, paid this month, activities due, setup), deals (a board of
 * stages moved with buttons or the keyboard, a list, the weighted forecast, adding a lead from Leads), clients (with
 * their contacts, deals and activities), activities (calls, meetings, tasks and notes),
 * proposals (priced line items, drafted from a brief, accepted by the client on a public page), projects (tasks,
 * time, invoicing unbilled time), invoices (payments, reminders, a public page with the business's payment link),
 * retainers (a monthly invoice), earnings (income by month, clients from top to lowest earner, what is owed by how
 * late it is, earnings per hour of each project) and settings (numbering, payment terms, default hourly rate and
 * deposit, chances of winning, and what Sales does by itself: retainer invoices, reminders, proposal follow-ups).
 * In the Aira frame (shell.js) the tabs are folders in the left menu, "+ New" opens a tab's create form (?new=) and
 * the search box across the top finds records through /api/studio/search (?tab=search lists every match).
 * Everything runs through /api/studio/* (aira/suite/studio/studio_routes.py). DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const API = (path, opts) => S.api("studio", path, opts);

  // Folder icons the shared set lacks (a receipt, a repeating month, a coin); the frame draws tab icons from Aira.ICONS.
  const OWN_ICONS = {
    "studio-invoice": '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6M9 16h3"/></svg>',
    "studio-repeat": '<svg viewBox="0 0 24 24"><path d="m17 2 3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12"/><path d="m7 22-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/></svg>',
    "studio-coins": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .9-3 2s1.3 1.7 3 2 3 .9 3 2-1.3 2-3 2c-1.4 0-2.5-.5-3-1.5M12 6v2M12 16v2"/></svg>',
  };
  for (const [name, svg] of Object.entries(OWN_ICONS)) if (!Aira.ICONS[name]) Aira.ICONS[name] = svg;

  // Each tab is a folder in the frame's left menu; its heading and line top the page (the logo already says Sales).
  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard", blurb: "What you are likely to win, what you are owed and what needs you today." },
    { id: "deals", label: "Deals", icon: "stocks", blurb: "Work you hope to win, moved stage by stage to won." },
    { id: "clients", label: "Clients", icon: "recruiters", blurb: "The people and companies you work for, with what they paid and owe." },
    { id: "activities", label: "Activities", icon: "interviews", blurb: "Calls, meetings, tasks and notes, so nothing slips." },
    { id: "proposals", label: "Proposals", icon: "documents", blurb: "Priced proposals your clients read and accept online." },
    { id: "projects", label: "Projects", icon: "applications", blurb: "Tasks and time on the work you won, ready to invoice." },
    { id: "invoices", label: "Invoices", icon: "studio-invoice", blurb: "Bill your clients, send reminders and see who has paid." },
    { id: "retainers", label: "Retainers", icon: "studio-repeat", blurb: "Monthly work at a fixed price, invoiced by itself." },
    { id: "reports", label: "Earnings", icon: "studio-coins", blurb: "What you earned, who pays you most and which work pays best." },
    { id: "settings", label: "Settings", icon: "settings", blurb: "Numbering, payment terms and what Sales does by itself." },
  ];
  const CLIENT_STATUSES = ["lead", "active", "past"];
  const PROJECT_STATUSES = ["planned", "active", "on_hold", "done"];
  const INVOICE_FILTERS = [["", "All"], ["unpaid", "Unpaid"], ["overdue", "Overdue"], ["paid", "Paid"], ["draft", "Draft"], ["void", "Void"]];
  const PROPOSAL_FILTERS = [["", "All"], ["draft", "Draft"], ["sent", "Waiting"], ["accepted", "Accepted"], ["declined", "Declined"], ["expired", "Expired"]];
  const METHODS = [["bank_transfer", "Bank transfer"], ["card", "Card"], ["cash", "Cash"], ["upi", "UPI"], ["paypal", "PayPal"], ["stripe", "Stripe"], ["cheque", "Cheque"], ["other", "Other"]];
  const TONES = { sent: "info", partially_paid: "warn", lead: "info", planned: "", on_hold: "warn", done: "good", todo: "", doing: "info", new: "", qualified: "info", proposal: "info", negotiation: "warn", won: "good", lost: "bad" };
  const STAGES = ["new", "qualified", "proposal", "negotiation", "won", "lost"];
  const OPEN_STAGES = ["new", "qualified", "proposal", "negotiation"];
  const STAGE_LABELS = { new: "New", qualified: "Qualified", proposal: "Proposal", negotiation: "Negotiation", won: "Won", lost: "Lost" };
  const SOURCES = [["lead", "A lead"], ["referral", "A referral"], ["website", "The website"], ["other", "Other"]];
  const ACTIVITY_KINDS = [["call", "Call"], ["meeting", "Meeting"], ["task", "Task"], ["note", "Note"]];
  const KIND_ICONS = { call: "📞", meeting: "🤝", task: "✅", note: "📝" };

  let rerender = () => {};
  // The board, toolbar and report styles are in app-studio.css.
  const money = (cents) => S.money(cents);
  const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
  const toolbar = (...children) => h("div", { class: "btn-row studio-toolbar" }, ...children);
  const day = (iso) => (iso ? new Date(`${String(iso).slice(0, 10)}T00:00:00`).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "");
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const hours = (minutes) => `${(Number(minutes || 0) / 60).toFixed(minutes % 60 ? 2 : 0)} h`;
  const pill = (status) => S.pill(status, TONES);
  const who = (row) => [row.client_name, row.client_company].filter(Boolean).join(" · ");
  const refresh = () => { Aira.closeDrawer(); rerender(); };

  let clientCache = null;
  async function clients(force = false) {
    if (!clientCache || force) clientCache = (await API("/clients")).clients;
    return clientCache;
  }
  const clientOptions = (list) => [["", "Choose a client"], ...list.map((c) => [c.id, c.company ? `${c.name} (${c.company})` : c.name])];

  // ===================================================================== line item editor (proposals, invoices)
  function itemsEditor(initial = [], onChange = () => {}) {
    const rows = [];
    const body = h("tbody");
    const add = (item = {}) => {
      const description = h("input", { type: "text", value: item.description || "", placeholder: "What you will do", "aria-label": "Description" });
      const quantity = h("input", { type: "text", inputmode: "decimal", value: item.quantity || "1", "aria-label": "Quantity", style: { width: "70px" } });
      const price = h("input", { type: "text", inputmode: "decimal", value: S.fromCents(item.unit_cents || 0), "aria-label": "Unit price", style: { width: "110px" } });
      const amount = h("td", { class: "num" });
      const row = { description, quantity, price, amount };
      const remove = h("button", { type: "button", class: "btn ghost sm", "aria-label": "Remove line", text: "✕", onclick: () => { rows.splice(rows.indexOf(row), 1); tr.remove(); onChange(); } });
      const tr = h("tr", {}, h("td", {}, description), h("td", {}, quantity), h("td", {}, price), amount, h("td", {}, remove));
      for (const input of [description, quantity, price]) input.addEventListener("input", onChange);
      rows.push(row);
      body.append(tr);
      onChange();
    };
    for (const item of initial) add(item);
    if (!initial.length) add();
    const read = () => rows.filter((r) => r.description.value.trim()).map((r) => ({ description: r.description.value.trim(), quantity: r.quantity.value.trim() || "1", unit_cents: S.toCents(r.price.value) || 0 }));
    const lineAmount = (r) => Math.round((Number(r.quantity.value) || 0) * (S.toCents(r.price.value) || 0));
    const update = () => { for (const r of rows) r.amount.textContent = money(lineAmount(r)); };
    const node = h("div", { class: "table-wrap" },
      h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Description" }), h("th", { text: "Qty" }), h("th", { text: "Unit price" }), h("th", { class: "num", text: "Amount" }), h("th", {}))), body),
      h("div", { class: "btn-row", style: { marginTop: "8px" } }, h("button", { type: "button", class: "btn secondary sm", text: "Add line", onclick: () => add() })));
    return { node, read, update, add, subtotal: () => rows.reduce((sum, r) => sum + (r.description.value.trim() ? lineAmount(r) : 0), 0), clear: () => { rows.splice(0); body.replaceChildren(); } };
  }

  // A proposal or an invoice in a wide modal: client, words (proposal), lines, discount, tax, dates, notes.
  async function documentEditor({ kind, doc = {}, presetClient = "" }) {
    const list = await clients(true);
    if (!list.length) { Aira.toast("Add a client first.", "error"); return null; }
    const business = await S.business();
    const isProposal = kind === "proposal";
    const totals = h("div", { class: "suite-kpis" });
    let items;
    const discount = h("input", { type: "text", inputmode: "decimal", value: S.fromCents(doc.discount_cents || 0) });
    const taxPct = h("input", { type: "text", inputmode: "decimal", value: String(((doc.tax_rate_bp !== undefined ? doc.tax_rate_bp : business.tax_rate_bp || 0) / 100)) });
    const recompute = () => {
      if (!items) return;
      items.update();
      const subtotal = items.subtotal();
      const off = Math.min(subtotal, S.toCents(discount.value) || 0);
      const tax = Math.round(((subtotal - off) * Math.round((Number(taxPct.value) || 0) * 100)) / 10000);
      // a final invoice takes off the deposit already invoiced (tax included), as the server does
      const credit = Math.max(0, Math.min(Number(doc.credit_cents) || 0, subtotal - off + tax));
      totals.replaceChildren(...S.kpis([{ label: "Subtotal", value: money(subtotal) }, { label: "Discount", value: money(off) }, { label: business.tax_label || "Tax", value: money(tax) }, ...(credit ? [{ label: "Deposit taken off", value: `-${money(credit)}`, hint: doc.credit_label || "" }] : []), { label: "Total", value: money(subtotal - off + tax - credit), tone: "good" }]).childNodes);
    };
    items = itemsEditor(doc.items || [], recompute);
    discount.addEventListener("input", recompute);
    taxPct.addEventListener("input", recompute);
    const client = Aira.ui.select(clientOptions(list), doc.client_id || presetClient || "");
    const title = h("input", { type: "text", value: doc.title || "", placeholder: "Website refresh for Bloom Bakery" });
    const intro = h("textarea", { rows: 5 }); intro.value = doc.intro || "";
    const scope = h("textarea", { rows: 7 }); scope.value = doc.scope || "";
    const brief = h("textarea", { rows: 2, placeholder: "A short brief: what the client needs, in a sentence or two" });
    const validUntil = h("input", { type: "date", value: doc.valid_until || "" });
    const settings = isProposal ? (await API("/settings")).settings : {};
    const depositValue = doc.id ? (doc.deposit_bp || 0) / 100 : settings.default_deposit_percent || 0;
    const deposit = h("input", { type: "number", min: "0", max: "100", step: "0.01", inputmode: "decimal", value: depositValue ? String(depositValue) : "" });
    const issue = h("input", { type: "date", value: doc.issue_date || "" });
    const due = h("input", { type: "date", value: doc.due_date || "" });
    const notes = h("textarea", { rows: 2, placeholder: "Shown to the client" }); notes.value = doc.notes || "";
    const internal = h("textarea", { rows: 2, placeholder: "Only you see this" }); internal.value = doc.internal_notes || "";
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const draftButton = S.button("Write it for me", async () => {
      if (!brief.value.trim()) { brief.focus(); Aira.toast("Write a short brief first.", "error"); return; }
      const { draft } = await API("/proposals/draft", { method: "POST", body: { brief: brief.value, client_id: client.value, title: title.value } });
      title.value = draft.title; intro.value = draft.intro; scope.value = draft.scope;
      items.clear(); for (const item of draft.items) items.add(item); recompute();
      Aira.toast(draft.source === "model" ? "Drafted. Check it and add your prices." : "Started from a template. Add your prices and details.", "success");
    }, { kind: "secondary sm" });
    const body = h("div", { class: "suite-form" },
      Aira.ui.field("Client *", client),
      isProposal ? h("div", { class: "card", style: { display: "grid", gap: "8px" } }, Aira.ui.field("Brief (optional)", brief, "Aira drafts the title, introduction, scope and lines. You set the prices."), h("div", { class: "btn-row" }, draftButton)) : null,
      isProposal ? Aira.ui.field("Title *", title) : null,
      isProposal ? Aira.ui.field("Introduction", intro) : null,
      isProposal ? Aira.ui.field("Scope", scope, "Phases, deliverables and what is not included.") : null,
      h("h3", { class: "suite-h3", text: "Lines" }), items.node,
      S.grid(Aira.ui.field("Discount", discount), Aira.ui.field(`${business.tax_label || "Tax"} %`, taxPct, "From your business profile; change it for this document if needed.")),
      totals,
      isProposal ? S.grid(Aira.ui.field("Valid until", validUntil), Aira.ui.field("Deposit when accepted (%)", deposit, "Empty for none. When the client accepts, Aira sends them an invoice for this share, and the final invoice takes it off.")) : S.grid(Aira.ui.field("Issue date", issue), Aira.ui.field("Due date", due, "Empty: your payment terms.")),
      isProposal ? null : Aira.ui.field("Note for the client", notes),
      isProposal ? null : Aira.ui.field("Internal notes", internal),
      error);
    recompute();
    return new Promise((resolve) => {
      let done = false;
      Aira.modal({
        title: doc.id ? `Edit ${doc.number || kind}` : isProposal ? "New proposal" : "New invoice",
        wide: true,
        body,
        onClose: () => { if (!done) resolve(null); },
        actions: [
          { label: "Cancel", kind: "ghost" },
          { label: doc.id ? "Save" : `Create ${kind}`, kind: "primary", onClick: async () => {
            error.hidden = true;
            const payload = { client_id: client.value, items: items.read(), discount_cents: S.toCents(discount.value) || 0, tax_rate_bp: Math.round((Number(taxPct.value) || 0) * 100) };
            if (isProposal) Object.assign(payload, { title: title.value.trim(), intro: intro.value, scope: scope.value, deposit_percent: deposit.value.trim(), ...(validUntil.value ? { valid_until: validUntil.value } : {}) });
            else Object.assign(payload, { notes: notes.value, internal_notes: internal.value, ...(issue.value ? { issue_date: issue.value } : {}), ...(due.value ? { due_date: due.value } : {}) });
            if (!payload.client_id) { error.textContent = "Choose a client."; error.hidden = false; return false; }
            try {
              const path = isProposal ? "/proposals" : "/invoices";
              const result = doc.id ? await API(`${path}/${doc.id}`, { method: "PATCH", body: payload }) : await API(path, { method: "POST", body: payload });
              done = true;
              resolve(result);
              return true;
            } catch (err) { error.textContent = err.message; error.hidden = false; return false; }
          } },
        ],
      });
    });
  }

  // ===================================================================== overview
  async function overview(body) {
    const data = await API("/overview");
    const sales = data.sales || {};
    const parts = [S.kpis([
      { label: "Pipeline", value: money(sales.pipeline_cents), hint: plural(sales.open_deals || 0, "open deal") },
      { label: "Weighted forecast", value: money(sales.weighted_cents), hint: "Value times the chance of winning" },
      { label: "Won this month", value: money(sales.won_this_month_cents), tone: sales.won_this_month_cents ? "good" : "" },
      { label: "Win rate", value: sales.win_rate === null || sales.win_rate === undefined ? "-" : `${sales.win_rate}%` },
    ]), S.kpis([
      { label: "Outstanding", value: money(data.outstanding_cents), hint: "Sent and not yet paid" },
      { label: "Overdue", value: money(data.overdue_cents), hint: data.overdue_count ? `${data.overdue_count} invoice${data.overdue_count === 1 ? "" : "s"}` : "Nothing overdue", tone: data.overdue_cents ? "bad" : "good" },
      { label: "Paid this month", value: money(data.paid_this_month_cents), tone: data.paid_this_month_cents ? "good" : "" },
      { label: "Proposals waiting", value: money(data.pipeline_cents), hint: `${data.awaiting_proposals.length} sent` },
      { label: "Active projects", value: data.active_projects },
    ])];
    if (data.checklist.some((step) => !step.done)) parts.push(S.section("Get set up", S.checklist(data.checklist)));
    parts.push(S.grid(
      S.section("Your activities today", activityList(data.activities_today || [], { empty: "Nothing due today. Plan a call or a meeting under Activities." })),
      S.section("Deals closing this month", S.table({ columns: dealColumns(), rows: data.closing_soon || [], onRow: (d) => openDeal(d.id), empty: Aira.ui.empty("Nothing closing this month", "Give your deals an expected close date to see them here.") })),
    ));
    const attention = [];
    for (const invoice of data.overdue_invoices) {
      attention.push(h("li", {}, h("div", {}, h("strong", { text: `${invoice.number} · ${who(invoice)}` }), h("div", { class: "muted small", text: `${money(invoice.balance_cents)} due ${day(invoice.due_date)} (${invoice.days_overdue} days late)` })),
        h("div", { class: "btn-row" }, S.button("Remind", async () => { const r = await API(`/invoices/${invoice.id}/remind`, { method: "POST", body: {} }); Aira.toast(r.message ? "Reminder is in the outbox (Business hub)." : r.note, r.message ? "success" : ""); }, { kind: "secondary sm" }), S.button("Open", () => openInvoice(invoice.id), { kind: "ghost sm" }))));
    }
    for (const proposal of data.accepted_to_convert) {
      attention.push(h("li", {}, h("div", {}, h("strong", { text: `${proposal.title} was accepted` }), h("div", { class: "muted small", text: `${who(proposal)} · ${money(proposal.total_cents)}` })),
        h("div", { class: "btn-row" }, S.button("Start project and invoice", async () => { await convert(proposal.id); }, { kind: "sm" }))));
    }
    parts.push(S.grid(
      S.section("Needs attention", attention.length ? h("ul", { class: "suite-activity studio-attention" }, ...attention) : Aira.ui.empty("All caught up", "Overdue invoices and accepted proposals show up here.")),
      S.section("Waiting on clients", S.table({ columns: [{ key: "title", label: "Proposal" }, { key: "client", label: "Client", render: who }, { key: "total_cents", label: "Total", num: true, render: (p) => money(p.total_cents) }, { key: "valid_until", label: "Valid until", render: (p) => day(p.valid_until) }], rows: data.awaiting_proposals, onRow: (p) => openProposal(p.id), empty: Aira.ui.empty("No proposals out", "Send one from Proposals.") })),
    ));
    parts.push(S.grid(
      S.section("Coming up (next 14 days)", data.deadlines.length || data.tasks_due.length
        ? h("ul", { class: "suite-activity" }, ...data.deadlines.map((d) => h("li", {}, h("span", { class: `suite-dot ${d.late ? "public" : ""}` }), h("div", {}, h("strong", { text: `${d.name} deadline` }), h("div", { class: "muted small", text: `${day(d.deadline)}${d.late ? " · late" : ""} · ${d.client_name || ""}` })))),
          ...data.tasks_due.map((t) => h("li", {}, h("span", { class: `suite-dot ${t.late ? "public" : ""}` }), h("div", {}, h("strong", { text: t.title }), h("div", { class: "muted small", text: `${day(t.due_date)}${t.late ? " · late" : ""} · ${t.project_name}` })))))
        : Aira.ui.empty("Nothing due soon", "Project deadlines and task due dates appear here.")),
      S.section("Top clients", S.table({ columns: [{ key: "name", label: "Client", render: (c) => [c.name, c.company].filter(Boolean).join(" · ") }, { key: "paid_cents", label: "Paid", num: true, render: (c) => money(c.paid_cents) }], rows: data.top_clients, onRow: (c) => openClient(c.id), empty: Aira.ui.empty("No payments yet", "Clients who pay you show up here.") })),
    ));
    body.append(...parts);
  }

  // ===================================================================== clients
  const clientFields = [
    { name: "name", label: "Name", required: true },
    { name: "company", label: "Company" },
    { name: "email", label: "Email", type: "email", hint: "Invoices and proposals are emailed here." },
    { name: "phone", label: "Phone", type: "tel" },
    { name: "status", label: "Status", type: "select", options: S.statusOptions(CLIENT_STATUSES) },
    { name: "tags", label: "Tags", placeholder: "retainer, design, referral" },
    { name: "notes", label: "Notes", type: "textarea", rows: 3 },
  ];
  const newClient = () => S.edit({ title: "New client", fields: clientFields, values: { status: "active" }, submitLabel: "Add client", onSave: async (values) => { await API("/clients", { method: "POST", body: values }); clientCache = null; Aira.toast("Client added.", "success"); rerender(); } });

  async function clientsTab(body, route) {
    const q = route.query.q || "";
    const status = route.query.status || "";
    const search = h("input", { type: "search", value: q, placeholder: "Search name, company, email", "data-search": "1", "aria-label": "Search clients" });
    const statusSelect = Aira.ui.select([["", "Any status"], ...S.statusOptions(CLIENT_STATUSES)], status, { "aria-label": "Status" });
    const go = () => Aira.router.go(["studio"], { tab: "clients", q: search.value.trim(), status: statusSelect.value });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    statusSelect.addEventListener("change", go);
    const params = new URLSearchParams({ ...(q ? { q } : {}), ...(status ? { status } : {}) }).toString();
    const list = (await API(`/clients${params ? `?${params}` : ""}`)).clients;
    body.append(
      toolbar(search, statusSelect, S.button("New client", newClient), S.button("Export CSV", () => Aira.download(`/api/studio/clients/export${params ? `?${params}` : ""}`, "studio-clients.csv"), { kind: "secondary" })),
      // on a phone the tags and billed columns give way (app-studio.css); the client's page has them
      h("div", { class: "studio-fit", dataset: { list: "clients" } }, S.table({
        columns: [
          { key: "name", label: "Client", render: (c) => h("div", {}, h("strong", { text: c.name }), c.company ? h("div", { class: "muted small", text: c.company }) : null) },
          { key: "status", label: "Status", render: (c) => pill(c.status) },
          { key: "tags", label: "Tags", render: (c) => c.tags.join(", ") },
          { key: "billed_cents", label: "Billed", num: true, render: (c) => money(c.billed_cents) },
          { key: "paid_cents", label: "Paid", num: true, render: (c) => money(c.paid_cents) },
          { key: "outstanding_cents", label: "Owed", num: true, render: (c) => money(c.outstanding_cents) },
        ],
        rows: list,
        onRow: (c) => openClient(c.id),
        empty: Aira.ui.empty(q || status ? "No clients match" : "No clients yet", q || status ? "Try another search." : "Add the people and companies you work for.", q || status ? null : h("button", { type: "button", class: "btn", text: "Add a client", onclick: newClient })),
      })));
  }

  async function openClient(id) {
    const data = await API(`/clients/${id}`);
    const c = data.client;
    const body = h("div", { class: "suite-body" },
      S.kpis([{ label: "Billed", value: money(c.billed_cents) }, { label: "Paid", value: money(c.paid_cents), tone: "good" }, { label: "Owed", value: money(c.outstanding_cents), tone: c.outstanding_cents ? "warn" : "" }]),
      Aira.ui.kv([["Company", c.company], ["Email", c.email ? h("a", { href: `mailto:${c.email}`, text: c.email }) : ""], ["Phone", c.phone], ["Status", pill(c.status)], ["Tags", c.tags.join(", ")], ["Notes", c.notes]]),
      h("div", { class: "btn-row" },
        S.button("New proposal", async () => { const r = await documentEditor({ kind: "proposal", presetClient: id }); if (r) openProposal(r.proposal.id); }, { kind: "secondary sm" }),
        S.button("New invoice", async () => { const r = await documentEditor({ kind: "invoice", presetClient: id }); if (r) openInvoice(r.invoice.id); }, { kind: "secondary sm" }),
        S.button("New project", () => projectForm({ client_id: id }), { kind: "secondary sm" }),
        S.button("New deal", () => dealForm({ client_id: id }), { kind: "secondary sm" })),
      S.section("Contacts", contactList(data.contacts || [], id), h("div", { class: "btn-row" }, S.button("Add a contact", () => contactForm(id), { kind: "secondary sm" }))),
      S.section("Deals", S.table({ columns: dealColumns(false), rows: data.deals || [], onRow: (d) => openDeal(d.id), empty: "No deals yet" })),
      S.section("Activities", activityList(data.activities || [], { empty: "No calls, meetings or notes yet." }), h("div", { class: "btn-row" }, S.button("Add an activity", () => activityForm({ client_id: id }), { kind: "secondary sm" }))),
      S.section("Invoices", S.table({ columns: invoiceColumns(false), rows: data.invoices, onRow: (i) => openInvoice(i.id), empty: "No invoices yet" })),
      S.section("Proposals", S.table({ columns: [{ key: "number", label: "No." }, { key: "title", label: "Title" }, { key: "status", label: "Status", render: (p) => pill(p.status) }, { key: "total_cents", label: "Total", num: true, render: (p) => money(p.total_cents) }], rows: data.proposals, onRow: (p) => openProposal(p.id), empty: "No proposals yet" })),
      S.section("Projects", S.table({ columns: [{ key: "name", label: "Project" }, { key: "status", label: "Status", render: (p) => pill(p.status) }, { key: "deadline", label: "Deadline", render: (p) => day(p.deadline) }], rows: data.projects, onRow: (p) => openProject(p.id), empty: "No projects yet" })),
    );
    Aira.drawer({ title: c.name, subtitle: c.company || "", body, headActions: [
      S.button("Edit", () => S.edit({ title: "Edit client", fields: clientFields, values: { ...c, tags: c.tags.join(", ") }, onSave: async (values) => { await API(`/clients/${id}`, { method: "PATCH", body: values }); clientCache = null; rerender(); openClient(id); } }), { kind: "secondary sm" }),
      S.button("Delete", async () => { if (await S.confirmDelete(c.name)) { await API(`/clients/${id}`, { method: "DELETE" }); clientCache = null; Aira.toast("Client deleted.", "success"); refresh(); } }, { kind: "ghost sm" }),
    ] });
  }

  // ===================================================================== contacts
  const contactFields = [
    { name: "name", label: "Name", required: true },
    { name: "role", label: "Role", placeholder: "Owner, marketing manager" },
    { name: "email", label: "Email", type: "email" },
    { name: "phone", label: "Phone", type: "tel" },
    { name: "is_primary", label: "Main contact", type: "checkbox" },
  ];
  function contactForm(clientId, contact = null) {
    return S.edit({
      title: contact ? "Edit contact" : "Add a contact",
      fields: contactFields,
      values: contact || {},
      onSave: async (values) => {
        if (contact) await API(`/contacts/${contact.id}`, { method: "PATCH", body: values });
        else await API(`/clients/${clientId}/contacts`, { method: "POST", body: values });
        Aira.toast("Saved.", "success");
        openClient(clientId);
      },
    });
  }
  function contactList(contacts, clientId) {
    if (!contacts.length) return h("p", { class: "muted", text: "No contacts yet. Add the people you talk to at this client." });
    return h("ul", { class: "suite-activity" }, ...contacts.map((c) => h("li", {},
      h("span", { class: `suite-dot ${c.is_primary ? "public" : ""}`, "aria-hidden": "true" }),
      h("div", { style: { flex: "1" } },
        h("strong", { text: c.name }), c.is_primary ? h("span", { class: "muted small", text: " · main contact" }) : null,
        h("div", { class: "muted small", text: [c.role, c.email, c.phone].filter(Boolean).join(" · ") })),
      h("div", { class: "btn-row" },
        S.button("Edit", () => contactForm(clientId, c), { kind: "ghost sm" }),
        S.button("Remove", async () => { if (await S.confirmDelete(c.name)) { await API(`/contacts/${c.id}`, { method: "DELETE" }); openClient(clientId); } }, { kind: "ghost sm" })))));
  }

  // ===================================================================== deals
  const stageOptions = (stages = STAGES) => stages.map((stage) => [stage, STAGE_LABELS[stage]]);
  function dealColumns(withClient = true) {
    return [
      { key: "title", label: "Deal", render: (d) => h("div", {}, h("strong", { text: d.title }), d.next_step ? h("div", { class: "muted small", text: `Next: ${d.next_step}` }) : null) },
      withClient ? { key: "client_name", label: "Client", render: who } : null,
      { key: "stage", label: "Stage", render: (d) => pill(d.stage) },
      { key: "value_cents", label: "Value", num: true, render: (d) => money(d.value_cents) },
      { key: "weighted_cents", label: "Weighted", num: true, render: (d) => (d.open ? money(d.weighted_cents) : "") },
      { key: "expected_close", label: "Expected close", render: (d) => day(d.expected_close) },
    ].filter(Boolean);
  }

  async function dealForm(values = {}, id = "") {
    const list = await clients(true);
    if (!list.length) { Aira.toast("Add a client first.", "error"); return null; }
    const fields = [
      { name: "title", label: "Deal name", required: true, placeholder: "Website for Bloom Bakery" },
      { name: "client_id", label: "Client", type: "select", options: clientOptions(list), required: true },
      { name: "value_cents", label: "Value", type: "money" },
      ...(id ? [] : [{ name: "stage", label: "Stage", type: "select", options: stageOptions(OPEN_STAGES) }]),
      { name: "expected_close", label: "Expected close date", type: "date" },
      { name: "owner_name", label: "Owner", placeholder: "Who is working on it" },
      { name: "next_step", label: "Next step", placeholder: "Send the proposal on Friday" },
      { name: "source", label: "Where it came from", type: "select", options: SOURCES },
    ];
    return S.edit({
      title: id ? "Edit deal" : "New deal",
      fields,
      values: { stage: "new", source: "other", ...values },
      onSave: async (form) => {
        const result = id ? await API(`/deals/${id}`, { method: "PATCH", body: form }) : await API("/deals", { method: "POST", body: form });
        Aira.toast(id ? "Deal saved." : "Deal added.", "success");
        rerender();
        openDeal(result.deal.id);
      },
    });
  }

  async function moveDeal(deal, stage) {
    if (stage === "lost") {
      return S.edit({
        title: `Mark "${deal.title}" as lost`,
        fields: [{ name: "lost_reason", label: "Why was it lost?", type: "textarea", rows: 3, required: true, placeholder: "Chose another supplier, no budget this year" }],
        submitLabel: "Mark as lost",
        onSave: async (values) => { await API(`/deals/${deal.id}/move`, { method: "POST", body: { stage, lost_reason: values.lost_reason } }); Aira.toast("Marked as lost.", "success"); rerender(); },
      });
    }
    await API(`/deals/${deal.id}/move`, { method: "POST", body: { stage } });
    Aira.toast(stage === "won" ? "Deal won. Well done!" : `Moved to ${STAGE_LABELS[stage]}.`, "success");
    rerender();
    return true;
  }

  function dealCard(deal) {
    const index = OPEN_STAGES.indexOf(deal.stage);
    const prev = index > 0 ? OPEN_STAGES[index - 1] : null;
    const next = index >= 0 && index < OPEN_STAGES.length - 1 ? OPEN_STAGES[index + 1] : null;
    const card = h("article", { class: "card studio-deal", tabindex: "0", "aria-label": `${deal.title}, ${money(deal.value_cents)}, ${STAGE_LABELS[deal.stage]}` },
      h("strong", { text: deal.title }),
      h("div", { class: "muted small", text: who(deal) }),
      h("div", { text: money(deal.value_cents) }),
      deal.expected_close ? h("div", { class: "muted small", text: `Closes ${day(deal.expected_close)}` }) : null,
      deal.open ? h("div", { class: "btn-row" },
        prev ? S.button("Back", () => moveDeal(deal, prev), { kind: "ghost sm", title: `Move back to ${STAGE_LABELS[prev]}` }) : null,
        next ? S.button("Next", () => moveDeal(deal, next), { kind: "ghost sm", title: `Move on to ${STAGE_LABELS[next]}` }) : null,
        S.button("Won", () => moveDeal(deal, "won"), { kind: "secondary sm" }),
        S.button("Lost", () => moveDeal(deal, "lost"), { kind: "ghost sm" })) : null);
    card.addEventListener("click", (event) => { if (!event.target.closest("button")) openDeal(deal.id); });
    // Keyboard: Enter opens, arrow keys move the deal between stages.
    card.addEventListener("keydown", (event) => {
      if (event.target !== card) return;
      if (event.key === "Enter") openDeal(deal.id);
      else if (event.key === "ArrowRight" && next) { event.preventDefault(); moveDeal(deal, next); }
      else if (event.key === "ArrowLeft" && prev) { event.preventDefault(); moveDeal(deal, prev); }
    });
    return card;
  }

  async function dealsTab(body, route) {
    const view = route.query.view === "list" ? "list" : "board";
    const q = route.query.q || "";
    const status = route.query.status || "";
    const search = h("input", { type: "search", value: q, placeholder: "Search deals and clients", "data-search": "1", "aria-label": "Search deals" });
    const statusSelect = Aira.ui.select([["", "All deals"], ["open", "Open"], ["won", "Won"], ["lost", "Lost"]], status, { "aria-label": "Show" });
    const go = (extra = {}) => Aira.router.go(["studio"], { tab: "deals", view, q: search.value.trim(), status: statusSelect.value, ...extra });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    statusSelect.addEventListener("change", () => go());
    const exportParams = new URLSearchParams({ ...(q ? { q } : {}), ...(status ? { status } : {}) }).toString();
    const bar = toolbar(search, statusSelect,
      S.button("New deal", () => dealForm()),
      S.button("Add from Leads", () => fromLeads(), { kind: "secondary" }),
      S.button(view === "board" ? "Show as a list" : "Show as a board", () => go({ view: view === "board" ? "list" : "board" }), { kind: "ghost" }),
      S.button("Export CSV", () => Aira.download(`/api/studio/deals/export${exportParams ? `?${exportParams}` : ""}`, "deals.csv"), { kind: "ghost" }));
    const forecast = await API("/forecast");
    const chart = S.section("Forecast",
      S.kpis([
        { label: "Weighted pipeline", value: money(forecast.weighted_cents), hint: `${plural(forecast.open_deals, "open deal")} worth ${money(forecast.pipeline_cents)}` },
        { label: "Won this quarter", value: money(forecast.won_this_quarter_cents), tone: forecast.won_this_quarter_cents ? "good" : "" },
        { label: "Average deal won", value: forecast.average_won_cents === null ? "-" : money(forecast.average_won_cents) },
        { label: "Days to win", value: forecast.cycle_days === null ? "-" : forecast.cycle_days, hint: "From adding a deal to winning it" },
      ]),
      S.bars(forecast.months.map((m) => ({ label: new Date(`${m.month}-01T00:00:00`).toLocaleDateString(undefined, { month: "short" }), value: m.weighted_cents })), { format: (v) => money(v) }),
      forecast.undated.deals ? h("p", { class: "muted small", text: `${plural(forecast.undated.deals, "open deal has", "open deals have")} no expected close date (${money(forecast.undated.weighted_cents)} weighted).` }) : null);
    if (view === "list") {
      const params = new URLSearchParams({ ...(q ? { q } : {}), ...(status ? { status } : {}) }).toString();
      const list = (await API(`/deals${params ? `?${params}` : ""}`)).deals;
      body.append(bar, h("div", { class: "studio-fit", dataset: { list: "deals" } }, S.table({ columns: dealColumns(), rows: list, onRow: (d) => openDeal(d.id), empty: Aira.ui.empty(q || status ? "No deals match" : "No deals yet", "Add the work you hope to win and track it to the finish.", h("button", { type: "button", class: "btn", text: "Add a deal", onclick: () => dealForm() })) })), chart);
      return;
    }
    const board = await API("/deals/board");
    const wanted = (deal) => (!q || `${deal.title} ${deal.client_name} ${deal.client_company}`.toLowerCase().includes(q.toLowerCase())) && (!status || (status === "open" ? deal.open : deal.stage === status));
    const columns = board.columns.filter((column) => !status || (status === "open" ? OPEN_STAGES.includes(column.stage) : column.stage === status));
    const lane = (column) => {
      const deals = column.deals.filter(wanted);
      return h("section", { class: "studio-lane", "aria-label": STAGE_LABELS[column.stage] },
        h("header", {}, h("strong", { text: STAGE_LABELS[column.stage] }), h("span", { class: "muted small", text: `${deals.length} · ${money(deals.reduce((sum, d) => sum + Number(d.value_cents), 0))} · ${column.probability}%` })),
        ...(deals.length ? deals.map(dealCard) : [h("p", { class: "muted small", text: "No deals here." })]));
    };
    const none = !board.columns.some((column) => column.deals.length);
    body.append(bar,
      ...(none ? [Aira.ui.empty("No deals yet", "A deal is work you hope to win. Add one, move it through the stages, and see what you are likely to earn.", h("button", { type: "button", class: "btn", text: "Add a deal", onclick: () => dealForm() }))] : []),
      h("div", { class: "studio-board", role: "list" }, ...columns.map(lane)), chart);
  }

  async function openDeal(id) {
    const data = await API(`/deals/${id}`);
    const d = data.deal;
    const stageSelect = Aira.ui.select(stageOptions(), d.stage, { "aria-label": "Stage" });
    stageSelect.addEventListener("change", async () => { if (await moveDeal(d, stageSelect.value)) openDeal(id); });
    const proposals = (await API(`/proposals?client_id=${encodeURIComponent(d.client_id)}`).catch(() => ({ proposals: [] }))).proposals || [];
    const contacts = data.contacts || [];
    const body = h("div", { class: "suite-body" },
      S.kpis([
        { label: "Value", value: money(d.value_cents) },
        { label: "Chance of winning", value: `${d.probability}%` },
        { label: "Weighted", value: d.open ? money(d.weighted_cents) : "-" },
      ]),
      Aira.ui.kv([["Client", who(d)], ["Contact", d.contact_name], ["Stage", pill(d.stage)], ["Expected close", day(d.expected_close)], ["Owner", d.owner_name], ["Next step", d.next_step], ["Came from", (SOURCES.find(([k]) => k === d.source) || ["", ""])[1]], ["Why it was lost", d.lost_reason]]),
      h("div", { class: "btn-row" }, Aira.ui.field("Move to", stageSelect),
        d.stage === "won" ? S.button(d.project_id ? "Open the project" : "Start project and invoice", async () => { const made = await API(`/deals/${id}/convert`, { method: "POST", body: {} }); Aira.toast("Project and draft invoice ready.", "success"); openProject(made.project.id); }) : null),
      contacts.length ? h("div", { class: "btn-row" }, Aira.ui.field("Contact", (() => { const select = Aira.ui.select([["", "No contact"], ...contacts.map((c) => [c.id, c.name])], d.contact_id, { "aria-label": "Contact" }); select.addEventListener("change", async () => { await API(`/deals/${id}`, { method: "PATCH", body: { contact_id: select.value } }); openDeal(id); }); return select; })())) : null,
      proposals.length ? h("div", { class: "btn-row" }, Aira.ui.field("Linked proposal", (() => { const select = Aira.ui.select([["", "None"], ...proposals.map((p) => [p.id, `${p.number} · ${p.title}`])], d.proposal_id, { "aria-label": "Linked proposal" }); select.addEventListener("change", async () => { await API(`/deals/${id}`, { method: "PATCH", body: { proposal_id: select.value } }); Aira.toast("When the client accepts this proposal the deal is won.", "success"); openDeal(id); }); return select; })())) : null,
      S.section("Activities", activityList(data.activities || [], { empty: "No calls, meetings or notes for this deal yet.", onChange: () => openDeal(id) }), h("div", { class: "btn-row" }, S.button("Add an activity", () => activityForm({ deal_id: id, client_id: d.client_id }, () => openDeal(id)), { kind: "secondary sm" }))),
    );
    Aira.drawer({ title: d.title, subtitle: who(d), body, headActions: [
      S.button("Edit", () => dealForm(d, id), { kind: "secondary sm" }),
      S.button("Delete", async () => { if (await S.confirmDelete(d.title)) { await API(`/deals/${id}`, { method: "DELETE" }); Aira.toast("Deal deleted.", "success"); refresh(); } }, { kind: "ghost sm" }),
    ] });
  }

  async function fromLeads() {
    const data = await API("/leads");
    if (!data.available || !data.leads.length) {
      Aira.modal({ title: "Add from Leads", body: h("p", { class: "muted", text: "There are no saved leads yet. Find and save leads in Leads, then add them here as deals." }), actions: [{ label: "Close", kind: "secondary" }] });
      return;
    }
    const rows = data.leads.map((lead) => h("li", {},
      h("div", { style: { flex: "1" } }, h("strong", { text: lead.company || lead.name || "Lead" }), h("div", { class: "muted small", text: [lead.name !== lead.company ? lead.name : "", lead.title, lead.email].filter(Boolean).join(" · ") })),
      lead.deal_id ? S.button("Open deal", () => { picker.close(); openDeal(lead.deal_id); }, { kind: "ghost sm" })
        : S.button("Add as a deal", async () => { const made = await API(`/leads/${lead.id}/import`, { method: "POST", body: {} }); clientCache = null; Aira.toast("Client, contact and deal added.", "success"); picker.close(); rerender(); openDeal(made.deal.id); }, { kind: "secondary sm" })));
    const picker = Aira.modal({ title: "Add from Leads", wide: true, body: h("ul", { class: "suite-activity" }, ...rows), actions: [{ label: "Close", kind: "secondary" }] });
  }

  // ===================================================================== activities
  function activityList(rows, { empty = "Nothing here yet.", onChange = null } = {}) {
    if (!rows.length) return h("p", { class: "muted", text: empty });
    return h("ul", { class: "suite-activity" }, ...rows.map((a) => h("li", {},
      h("span", { "aria-hidden": "true", text: KIND_ICONS[a.kind] || "•" }),
      h("div", { style: { flex: "1" } },
        h("strong", { text: a.subject, style: a.done ? { textDecoration: "line-through", color: "var(--muted)" } : {} }),
        h("div", { class: "muted small", text: [a.due_date ? `${a.overdue ? "Late: " : ""}${day(a.due_date)}` : "", a.deal_title, a.client_name].filter(Boolean).join(" · ") }),
        a.notes ? h("div", { class: "small", text: a.notes }) : null),
      a.kind !== "note" ? S.button(a.done ? "Not done" : "Done", async () => { await API(`/activities/${a.id}`, { method: "PATCH", body: { done: !a.done } }); if (onChange) onChange(); else rerender(); }, { kind: a.done ? "ghost sm" : "secondary sm" }) : null)));
  }

  async function activityForm(values = {}, after = null) {
    const list = await clients(true);
    return S.edit({
      title: "Add an activity",
      fields: [
        { name: "kind", label: "Kind", type: "select", options: ACTIVITY_KINDS },
        { name: "subject", label: "What is it about?", required: true, placeholder: "Call Ana about the quote" },
        { name: "due_date", label: "When", type: "date" },
        ...(values.client_id || values.deal_id ? [] : [{ name: "client_id", label: "Client", type: "select", options: [["", "No client"], ...clientOptions(list).slice(1)] }]),
        { name: "notes", label: "Notes", type: "textarea", rows: 3 },
      ],
      values: { kind: "call", due_date: todayIso(), ...values },
      onSave: async (form) => { await API("/activities", { method: "POST", body: { ...values, ...form } }); Aira.toast("Added.", "success"); if (after) after(); else rerender(); },
    });
  }

  async function activitiesTab(body, route) {
    const when = route.query.when || "open";
    const select = Aira.ui.select([["open", "To do"], ["today", "Due today or late"], ["upcoming", "Coming up"], ["done", "Done"], ["", "Everything"]], when, { "aria-label": "Show" });
    select.addEventListener("change", () => Aira.router.go(["studio"], { tab: "activities", when: select.value }));
    const rows = (await API(`/activities${when ? `?when=${when}` : ""}`)).activities;
    body.append(toolbar(select, S.button("Add an activity", () => activityForm())),
      S.section("", activityList(rows, { empty: "No activities here. Plan your next call or meeting." })));
  }

  // ===================================================================== proposals
  async function newProposal() { const r = await documentEditor({ kind: "proposal" }); if (r) { rerender(); openProposal(r.proposal.id); } }

  async function proposalsTab(body, route) {
    const status = route.query.status || "";
    const filter = Aira.ui.select(PROPOSAL_FILTERS, status, { "aria-label": "Status" });
    filter.addEventListener("change", () => Aira.router.go(["studio"], { tab: "proposals", status: filter.value }));
    const list = (await API(`/proposals${status ? `?status=${status}` : ""}`)).proposals;
    const create = newProposal;
    body.append(
      toolbar(filter, S.button("New proposal", create)),
      S.table({
        columns: [
          { key: "number", label: "No." },
          { key: "title", label: "Proposal", render: (p) => h("div", {}, h("strong", { text: p.title }), h("div", { class: "muted small", text: who(p) })) },
          { key: "status", label: "Status", render: (p) => h("span", {}, pill(p.status), p.status === "accepted" && !p.converted ? h("span", { class: "muted small", text: " · to start" }) : null) },
          { key: "valid_until", label: "Valid until", render: (p) => day(p.valid_until) },
          { key: "total_cents", label: "Total", num: true, render: (p) => money(p.total_cents) },
        ],
        rows: list,
        onRow: (p) => openProposal(p.id),
        empty: Aira.ui.empty(status ? "No proposals here" : "No proposals yet", "Write one from a short brief; your client accepts it online.", status ? null : h("button", { type: "button", class: "btn", text: "Write a proposal", onclick: create })),
      }));
  }

  function linesTable(doc, taxLabel) {
    return h("div", {},
      S.table({ columns: [{ key: "description", label: "Description" }, { key: "quantity", label: "Qty", num: true }, { key: "unit_cents", label: "Unit", num: true, render: (i) => money(i.unit_cents) }, { key: "amount_cents", label: "Amount", num: true, render: (i) => money(i.amount_cents) }], rows: doc.items, empty: "No lines" }),
      Aira.ui.kv([["Subtotal", money(doc.subtotal_cents)], ["Discount", doc.discount_cents ? `-${money(doc.discount_cents)}` : ""], [`${taxLabel || "Tax"} (${doc.tax_rate_bp / 100}%)`, doc.tax_rate_bp ? money(doc.tax_cents) : ""], [doc.credit_label || "Deposit already invoiced", doc.credit_cents ? `-${money(doc.credit_cents)}` : ""], ["Total", h("strong", { text: money(doc.total_cents) })]]));
  }

  async function convert(id) {
    const result = await API(`/proposals/${id}/convert`, { method: "POST", body: {} });
    Aira.toast(`Project started and invoice ${result.invoice.number} drafted.`, "success");
    rerender();
    openInvoice(result.invoice.id);
  }

  async function openProposal(id) {
    const [{ proposal: p, link }, business] = await Promise.all([API(`/proposals/${id}`), S.business()]);
    const locked = p.status === "accepted" || p.status === "declined";
    const actions = [];
    if (p.status === "accepted" && !p.converted) actions.push(S.button("Start project and invoice", () => convert(id)));
    if (!locked) actions.push(S.button(p.status === "draft" ? "Send to client" : "Send again", async () => { const r = await API(`/proposals/${id}/send`, { method: "POST", body: {} }); Aira.toast(r.message ? "The email is in the outbox (Business hub)." : r.note, r.message ? "success" : ""); rerender(); openProposal(id); }, { kind: p.status === "draft" ? "" : "secondary" }));
    if (!locked) actions.push(S.button("Edit", async () => { const r = await documentEditor({ kind: "proposal", doc: p }); if (r) { rerender(); openProposal(id); } }, { kind: "secondary" }));
    if (!locked) actions.push(S.button("Mark accepted", async () => { if (await Aira.confirm({ title: "Mark as accepted", message: "Record that the client agreed outside Aira (on a call or by email)?", confirmLabel: "Mark accepted" })) { await API(`/proposals/${id}/status`, { method: "POST", body: { status: "accepted" } }); rerender(); openProposal(id); } }, { kind: "ghost" }));
    if (!locked) actions.push(S.button("Mark declined", async () => { await API(`/proposals/${id}/status`, { method: "POST", body: { status: "declined" } }); rerender(); openProposal(id); }, { kind: "ghost" }));
    if (p.invoice_id) actions.push(S.button("Open invoice", () => openInvoice(p.invoice_id), { kind: "secondary" }));
    if (p.project_id) actions.push(S.button("Open project", () => openProject(p.project_id), { kind: "ghost" }));
    if (p.deposit_invoice_id) actions.push(S.button("Open deposit invoice", () => openInvoice(p.deposit_invoice_id), { kind: "ghost" }));
    const deposit = p.deposit || {};
    const body = h("div", { class: "suite-body" },
      h("div", { class: "btn-row" }, pill(p.status), h("span", { class: "muted", text: `${who(p)} · valid until ${day(p.valid_until)}` })),
      p.status === "accepted" ? h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: `Accepted by ${p.accepted_name}` }), h("span", { class: "muted", text: S.when(p.accepted_at) })) : null,
      p.status === "declined" ? h("div", { class: "cloud-banner warning", role: "status" }, h("strong", { text: "Declined" }), p.decline_reason ? h("span", { text: p.decline_reason }) : null) : null,
      deposit.total_cents ? h("p", { class: "muted" }, h("strong", { text: `Deposit: ${deposit.deposit_bp / 100}% (${money(deposit.total_cents)})` }), h("span", { text: p.deposit_invoice_id ? " · invoiced to the client when they accepted. The final invoice takes it off." : " · when the client accepts, Aira sends them an invoice for it. The final invoice takes it off." })) : null,
      !locked || link ? S.share({ title: "Client page", text: "Your client reads the proposal and accepts it here. Creating the link marks the proposal as sent.", link, onCreate: async () => { const r = await API(`/proposals/${id}/link`, { method: "POST", body: {} }); rerender(); return r.link; }, onRevoke: async () => { await API(`/proposals/${id}/link`, { method: "DELETE" }); } }) : null,
      h("div", { class: "btn-row" }, ...actions),
      p.intro ? S.section("Introduction", h("p", { class: "suite-pre", text: p.intro })) : null,
      p.scope ? S.section("Scope", h("p", { class: "suite-pre", text: p.scope })) : null,
      S.section("Investment", linesTable(p, business.tax_label)),
    );
    Aira.drawer({ title: `${p.number} · ${p.title}`, subtitle: money(p.total_cents), body, headActions: p.status === "accepted" ? [] : [S.button("Delete", async () => { if (await S.confirmDelete(`proposal ${p.number}`)) { await API(`/proposals/${id}`, { method: "DELETE" }); refresh(); } }, { kind: "ghost sm" })] });
  }

  // ===================================================================== projects
  const projectFields = (list) => [
    { name: "client_id", label: "Client", type: "select", options: clientOptions(list), required: true },
    { name: "name", label: "Project name", required: true },
    { name: "status", label: "Status", type: "select", options: S.statusOptions(PROJECT_STATUSES) },
    { name: "budget_cents", label: "Budget", type: "money" },
    { name: "deadline", label: "Deadline", type: "date" },
    { name: "notes", label: "Notes", type: "textarea", rows: 3 },
  ];
  async function projectForm(values = {}, id = "") {
    const list = await clients(true);
    if (!list.length) { Aira.toast("Add a client first.", "error"); return; }
    await S.edit({ title: id ? "Edit project" : "New project", fields: projectFields(list), values: { status: "active", ...values }, onSave: async (v) => {
      const r = id ? await API(`/projects/${id}`, { method: "PATCH", body: v }) : await API("/projects", { method: "POST", body: v });
      rerender();
      openProject(r.project.id);
    } });
  }

  async function projectsTab(body, route) {
    const status = route.query.status || "";
    const filter = Aira.ui.select([["", "Any status"], ...S.statusOptions(PROJECT_STATUSES)], status, { "aria-label": "Status" });
    filter.addEventListener("change", () => Aira.router.go(["studio"], { tab: "projects", status: filter.value }));
    const list = (await API(`/projects${status ? `?status=${status}` : ""}`)).projects;
    body.append(
      toolbar(filter, S.button("New project", () => projectForm())),
      h("div", { class: "studio-fit", dataset: { list: "projects" } }, S.table({
        columns: [
          { key: "name", label: "Project", render: (p) => h("div", {}, h("strong", { text: p.name }), h("div", { class: "muted small", text: who(p) })) },
          { key: "status", label: "Status", render: (p) => pill(p.status) },
          { key: "deadline", label: "Deadline", render: (p) => day(p.deadline) },
          { key: "tasks", label: "Tasks", render: (p) => (p.tasks_total ? `${p.tasks_total - p.tasks_open}/${p.tasks_total} done` : "") },
          { key: "minutes", label: "Time", num: true, render: (p) => (p.minutes ? hours(p.minutes) : "") },
          { key: "unbilled_cents", label: "Unbilled", num: true, render: (p) => (p.unbilled_cents ? money(p.unbilled_cents) : "") },
          { key: "budget_cents", label: "Budget", num: true, render: (p) => (p.budget_cents ? money(p.budget_cents) : "") },
        ],
        rows: list,
        onRow: (p) => openProject(p.id),
        empty: Aira.ui.empty("No projects yet", "Start one by hand, or from an accepted proposal.", h("button", { type: "button", class: "btn", text: "New project", onclick: () => projectForm() })),
      })));
  }

  async function openProject(id) {
    const data = await API(`/projects/${id}`);
    const p = data.project;
    const reopen = () => { rerender(); openProject(id); };
    const taskInput = h("input", { type: "text", placeholder: "Add a task and press Enter", "aria-label": "New task" });
    const taskDue = h("input", { type: "date", "aria-label": "Due date" });
    const addTask = async () => { if (!taskInput.value.trim()) return; await API(`/projects/${id}/tasks`, { method: "POST", body: { title: taskInput.value.trim(), due_date: taskDue.value } }); reopen(); };
    taskInput.addEventListener("keydown", (e) => { if (e.key === "Enter") Aira.withBusy(taskInput, addTask); });
    const tasks = h("ul", { class: "suite-checklist studio-tasks" }, ...data.tasks.map((t) => {
      const box = h("input", { type: "checkbox", checked: t.status === "done", "aria-label": `Done: ${t.title}` });
      box.addEventListener("change", async () => { await API(`/tasks/${t.id}`, { method: "PATCH", body: { status: box.checked ? "done" : "todo" } }); reopen(); });
      return h("li", { class: t.status === "done" ? "done" : "" }, box, h("span", {}, h("strong", { text: t.title }), t.due_date ? h("span", { class: "muted small", text: ` · due ${day(t.due_date)}` }) : null), h("button", { type: "button", class: "btn ghost sm", "aria-label": `Delete ${t.title}`, text: "✕", onclick: async () => { await API(`/tasks/${t.id}`, { method: "DELETE" }); reopen(); } }));
    }));
    const logTime = () => S.edit({ title: "Log time", fields: [
      { name: "day", label: "Date", type: "date", value: todayIso() },
      { name: "hours", label: "Hours", type: "number", step: "0.25", min: "0.05", required: true },
      { name: "note", label: "What you did" },
      { name: "rate_cents", label: "Hourly rate", type: "money" },
      { name: "billable", label: "Billable", type: "checkbox", value: true },
    ], onSave: async (v) => { await API(`/projects/${id}/time`, { method: "POST", body: { ...v, rate_cents: v.rate_cents === null ? undefined : v.rate_cents } }); reopen(); } });
    const used = p.budget_cents ? Math.round((p.invoiced_cents * 100) / p.budget_cents) : null;
    const body = h("div", { class: "suite-body" },
      S.kpis([
        { label: "Budget", value: p.budget_cents ? money(p.budget_cents) : "-", hint: used !== null ? `${used}% invoiced` : "" },
        { label: "Invoiced", value: money(p.invoiced_cents) },
        { label: "Time logged", value: hours(p.minutes), hint: `${hours(p.billable_minutes)} billable` },
        { label: "Unbilled", value: money(p.unbilled_cents), tone: p.unbilled_cents ? "warn" : "" },
      ]),
      h("div", { class: "btn-row" }, pill(p.status), p.deadline ? h("span", { class: "muted", text: `Deadline ${day(p.deadline)}` }) : null,
        S.button("Log time", logTime, { kind: "secondary sm" }),
        p.unbilled_cents ? S.button(`Invoice ${money(p.unbilled_cents)} of time`, async () => { const r = await API(`/projects/${id}/invoice-time`, { method: "POST", body: {} }); Aira.toast(`Invoice ${r.invoice.number} drafted.`, "success"); rerender(); openInvoice(r.invoice.id); }, { kind: "sm" }) : null),
      S.section("Tasks", h("div", { class: "btn-row" }, taskInput, taskDue, S.button("Add", addTask, { kind: "secondary sm" })), data.tasks.length ? tasks : h("p", { class: "muted", text: "No tasks yet." })),
      S.section("Time", S.table({ columns: [
        { key: "day", label: "Date", render: (t) => day(t.day) },
        { key: "note", label: "Note" },
        { key: "minutes", label: "Hours", num: true, render: (t) => hours(t.minutes) },
        { key: "rate_cents", label: "Rate", num: true, render: (t) => (t.billable ? money(t.rate_cents) : "not billable") },
        { key: "invoice_id", label: "", render: (t) => (t.invoice_id ? S.pill("billed", { billed: "good" }) : h("button", { type: "button", class: "btn ghost sm", text: "Delete", onclick: async () => { await API(`/time/${t.id}`, { method: "DELETE" }); reopen(); } })) },
      ], rows: data.time, empty: "No time logged yet" })),
      S.section("Invoices", S.table({ columns: invoiceColumns(false), rows: data.invoices, onRow: (i) => openInvoice(i.id), empty: "No invoices for this project yet" })),
      p.notes ? S.section("Notes", h("p", { class: "suite-pre", text: p.notes })) : null,
    );
    Aira.drawer({ title: p.name, subtitle: who(p), body, headActions: [
      S.button("Edit", () => projectForm(p, id), { kind: "secondary sm" }),
      S.button("Delete", async () => { if (await S.confirmDelete(`project ${p.name}`)) { await API(`/projects/${id}`, { method: "DELETE" }); refresh(); } }, { kind: "ghost sm" }),
    ] });
  }

  // ===================================================================== invoices
  function invoiceColumns(withClient = true) {
    return [
      { key: "number", label: "No." },
      withClient ? { key: "client", label: "Client", render: who } : null,
      { key: "issue_date", label: "Issued", render: (i) => day(i.issue_date) },
      { key: "due_date", label: "Due", render: (i) => day(i.due_date) },
      { key: "status", label: "Status", render: (i) => h("span", {}, pill(i.status), i.customer_notices && i.customer_notices.length && i.status !== "paid" ? h("span", { class: "muted small", text: " · client says paid" }) : null) },
      { key: "total_cents", label: "Total", num: true, render: (i) => money(i.total_cents) },
      { key: "balance_cents", label: "Still due", num: true, render: (i) => (i.balance_cents ? money(i.balance_cents) : "") },
    ].filter(Boolean);
  }

  async function newInvoice() { const r = await documentEditor({ kind: "invoice" }); if (r) { rerender(); openInvoice(r.invoice.id); } }

  async function invoicesTab(body, route) {
    const status = route.query.status || "";
    const q = route.query.q || "";
    const filter = Aira.ui.select(INVOICE_FILTERS, status, { "aria-label": "Status" });
    const search = h("input", { type: "search", value: q, placeholder: "Number or client", "data-search": "1", "aria-label": "Search invoices" });
    const go = () => Aira.router.go(["studio"], { tab: "invoices", status: filter.value, q: search.value.trim() });
    filter.addEventListener("change", go);
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    const params = new URLSearchParams({ ...(status ? { status } : {}), ...(q ? { q } : {}) }).toString();
    const list = (await API(`/invoices${params ? `?${params}` : ""}`)).invoices;
    const create = newInvoice;
    const owed = list.filter((i) => ["sent", "partially_paid", "overdue"].includes(i.status)).reduce((sum, i) => sum + i.balance_cents, 0);
    body.append(
      toolbar(search, filter, S.button("New invoice", create), S.button("Export CSV", () => Aira.download(`/api/studio/invoices/export${params ? `?${params}` : ""}`, "studio-invoices.csv"), { kind: "secondary" })),
      ...(list.length ? [h("p", { class: "muted", text: `${plural(list.length, "invoice")}${owed ? ` · ${money(owed)} still owed` : ""}` })] : []),
      // on a phone the dates give way so the money columns fit (app-studio.css); the invoice itself has them
      h("div", { class: "studio-invoices" }, S.table({ columns: invoiceColumns(true), rows: list, onRow: (i) => openInvoice(i.id), empty: Aira.ui.empty(status || q ? "No invoices here" : "No invoices yet", "Bill a client, convert an accepted proposal, or invoice a project's time.", status || q ? null : h("button", { type: "button", class: "btn", text: "Create an invoice", onclick: create })) })));
  }

  async function openInvoice(id) {
    const [{ invoice: inv, link }, business] = await Promise.all([API(`/invoices/${id}`), S.business()]);
    const reopen = () => { rerender(); openInvoice(id); };
    const open = ["sent", "partially_paid", "overdue"].includes(inv.status);
    const recordPayment = () => S.edit({ title: `Record a payment for ${inv.number}`, fields: [
      { name: "amount_cents", label: "Amount received", type: "money", value: inv.balance_cents, required: true },
      { name: "day", label: "Date received", type: "date", value: todayIso() },
      { name: "method", label: "Method", type: "select", options: METHODS },
      { name: "note", label: "Note", placeholder: "Transaction or cheque number" },
    ], submitLabel: "Record payment", onSave: async (v) => { await API(`/invoices/${id}/payments`, { method: "POST", body: v }); Aira.toast("Payment recorded.", "success"); reopen(); } });
    const actions = [];
    if (inv.status !== "void" && inv.balance_cents > 0) actions.push(S.button("Record payment", recordPayment), S.button("Mark paid in full", async () => { await API(`/invoices/${id}/payments`, { method: "POST", body: {} }); Aira.toast("Marked paid.", "success"); reopen(); }, { kind: "secondary" }));
    if (inv.status === "draft") actions.push(S.button("Send to client", async () => { const r = await API(`/invoices/${id}/send`, { method: "POST", body: {} }); Aira.toast(r.message ? "The email is in the outbox (Business hub)." : r.note, r.message ? "success" : ""); reopen(); }, { kind: "secondary" }));
    if (open) actions.push(S.button("Send reminder", async () => { const r = await API(`/invoices/${id}/remind`, { method: "POST", body: {} }); Aira.toast(r.message ? "The reminder is in the outbox (Business hub)." : r.note, r.message ? "success" : ""); reopen(); }, { kind: "secondary" }));
    if (inv.status !== "void" && !inv.paid_cents) actions.push(S.button("Edit", async () => { const r = await documentEditor({ kind: "invoice", doc: inv }); if (r) reopen(); }, { kind: "ghost" }));
    if (inv.status !== "void" && inv.status !== "draft") actions.push(S.button("Void", async () => { if (await Aira.confirm({ title: `Void ${inv.number}`, message: "A void invoice stays on record but is no longer owed. Its payments stop counting as revenue.", confirmLabel: "Void", danger: true })) { await API(`/invoices/${id}/void`, { method: "POST", body: {} }); reopen(); } }, { kind: "ghost" }));
    const notices = (inv.customer_notices || []).slice().reverse();
    const body = h("div", { class: "suite-body" },
      S.kpis([{ label: "Total", value: money(inv.total_cents) }, { label: "Paid", value: money(inv.paid_cents), tone: inv.paid_cents ? "good" : "" }, { label: "Still due", value: money(inv.balance_cents), tone: inv.status === "overdue" ? "bad" : inv.balance_cents ? "warn" : "" }]),
      h("div", { class: "btn-row" }, pill(inv.status), inv.kind === "deposit" ? S.pill("deposit", { deposit: "info" }) : null, h("span", { class: "muted", text: `${who(inv)} · issued ${day(inv.issue_date)} · due ${day(inv.due_date)}${inv.days_overdue ? ` (${inv.days_overdue} days late)` : ""}${inv.reminder_count ? ` · ${plural(inv.reminder_count, "reminder")} so far` : ""}` })),
      notices.length && inv.status !== "paid" ? h("div", { class: "cloud-banner info", role: "status" }, h("strong", { text: "Your client says they paid" }), h("span", { text: notices.map((n) => [n.reference && `Reference ${n.reference}`, n.note, Aira.fmt.ago(n.at)].filter(Boolean).join(" · ")).join(" | ") }), h("span", { class: "muted", text: "Check your account, then record the payment." })) : null,
      inv.status !== "void" ? S.share({ title: "Client page", text: "Your client sees the invoice and how to pay you. Creating the link marks a draft as sent.", link, onCreate: async () => { const r = await API(`/invoices/${id}/link`, { method: "POST", body: {} }); rerender(); return r.link; }, onRevoke: async () => { await API(`/invoices/${id}/link`, { method: "DELETE" }); } }) : null,
      business.payment_link || business.payment_instructions ? null : h("p", { class: "muted small" }, "Add a payment link or instructions in the ", h("a", { href: "#/hub?tab=business", text: "business profile" }), " so clients know how to pay."),
      h("div", { class: "btn-row" }, ...actions),
      S.section("Lines", linesTable(inv, business.tax_label)),
      S.section("Payments", S.table({ columns: [
        { key: "day", label: "Date", render: (p) => day(p.day) },
        { key: "method", label: "Method", render: (p) => (METHODS.find((m) => m[0] === p.method) || [0, p.method])[1] },
        { key: "note", label: "Note" },
        { key: "amount_cents", label: "Amount", num: true, render: (p) => money(p.amount_cents) },
        { key: "x", label: "", render: (p) => (inv.status === "void" ? "" : h("button", { type: "button", class: "btn ghost sm", text: "Remove", onclick: async () => { if (await S.confirmDelete("this payment")) { await API(`/invoices/${id}/payments/${p.id}`, { method: "DELETE" }); reopen(); } } })) },
      ], rows: inv.payments, empty: "No payments recorded" })),
      inv.notes ? S.section("Note for the client", h("p", { class: "suite-pre", text: inv.notes })) : null,
      inv.internal_notes ? S.section("Internal notes", h("p", { class: "suite-pre", text: inv.internal_notes })) : null,
    );
    Aira.drawer({ title: `Invoice ${inv.number}`, subtitle: who(inv), body, headActions: inv.status === "draft" ? [S.button("Delete", async () => { if (await S.confirmDelete(`invoice ${inv.number}`)) { await API(`/invoices/${id}`, { method: "DELETE" }); refresh(); } }, { kind: "ghost sm" })] : [] });
  }

  // ===================================================================== retainers
  async function retainersTab(body) {
    const list = (await API("/retainers")).retainers;
    const fields = (clientList) => [
      { name: "client_id", label: "Client", type: "select", options: clientOptions(clientList), required: true },
      { name: "title", label: "What it covers", required: true, placeholder: "Monthly SEO and reporting" },
      { name: "amount_cents", label: "Amount per month", type: "money", required: true },
      { name: "next_date", label: "First invoice date", type: "date", value: todayIso() },
    ];
    const create = async () => {
      const clientList = await clients(true);
      if (!clientList.length) { Aira.toast("Add a client first.", "error"); return; }
      await S.edit({ title: "New retainer", fields: fields(clientList), submitLabel: "Set up retainer", onSave: async (v) => { await API("/retainers", { method: "POST", body: v }); rerender(); } });
    };
    const run = S.button("Create due invoices", async () => { const r = await API("/retainers/run", { method: "POST", body: {} }); Aira.toast(r.created.length ? `${r.created.length} invoice${r.created.length === 1 ? "" : "s"} drafted.` : "Nothing is due yet.", "success"); rerender(); }, { kind: "secondary" });
    const monthly = list.filter((r) => r.active).reduce((sum, r) => sum + r.amount_cents, 0);
    body.append(
      S.kpis([{ label: "Recurring per month", value: money(monthly), tone: monthly ? "good" : "" }, { label: "Active retainers", value: list.filter((r) => r.active).length }]),
      h("div", { class: "btn-row" }, S.button("New retainer", create), list.length ? run : null),
      h("p", { class: "muted", text: "A retainer drafts one invoice a month on its date, by itself (Settings > What Sales does by itself). Create due invoices any time: a month is never invoiced twice. Pausing skips the months it is paused." }),
      S.table({
        columns: [
          { key: "title", label: "Retainer", render: (r) => h("div", {}, h("strong", { text: r.title }), h("div", { class: "muted small", text: who(r) })) },
          { key: "amount_cents", label: "Per month", num: true, render: (r) => money(r.amount_cents) },
          { key: "next_date", label: "Next invoice", render: (r) => day(r.next_date) },
          { key: "active", label: "Status", render: (r) => S.pill(r.active ? "active" : "paused", { paused: "warn" }) },
          { key: "x", label: "", render: (r) => h("div", { class: "btn-row" },
            h("button", { type: "button", class: "btn ghost sm", text: r.active ? "Pause" : "Resume", onclick: async () => { await API(`/retainers/${r.id}`, { method: "PATCH", body: { active: !r.active } }); rerender(); } }),
            h("button", { type: "button", class: "btn ghost sm", text: "Delete", onclick: async () => { if (await S.confirmDelete(`retainer ${r.title}`)) { await API(`/retainers/${r.id}`, { method: "DELETE" }); rerender(); } } })) },
        ],
        rows: list,
        empty: Aira.ui.empty("No retainers yet", "Monthly work at a fixed price: a steady income you never have to chase.", h("button", { type: "button", class: "btn", text: "Set up a retainer", onclick: create })),
      }));
  }

  // ===================================================================== earnings
  const monthLabel = (key) => new Date(`${key}-01T00:00:00`).toLocaleDateString(undefined, { month: "short" });
  const monthLong = (key) => new Date(`${key}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const hourly = (cents) => (cents === null || cents === undefined ? "-" : `${money(cents)} an hour`);

  // Clients ranked by what they paid, with a bar for their share; the lowest are marked so you can look at them.
  function rankList(clients) {
    if (!clients.length) return Aira.ui.empty("No payments in this period", "Record payments on your invoices to see who pays you most.");
    const top = clients[0].received_cents || 1;
    const lowFrom = clients.length > 3 ? clients.length - Math.min(3, Math.floor(clients.length / 3)) : clients.length;
    return h("ol", { class: "studio-rank" }, ...clients.map((c, index) => {
      const low = index >= lowFrom;
      return h("li", {},
        h("button", { type: "button", class: "btn ghost sm", style: { justifySelf: "start", textAlign: "left" }, text: [c.name, c.company].filter(Boolean).join(" · ") || "Client", onclick: () => openClient(c.id) }),
        h("strong", { text: money(c.received_cents) }),
        h("span", { class: `studio-share ${low ? "studio-low" : ""}`, role: "img", "aria-label": `${c.share}% of what you received` }, h("span", { style: { width: `${Math.max(2, Math.round((c.received_cents * 100) / top))}%` } })),
        h("span", { class: "muted small", text: [`${c.share}% of your income`, plural(c.payments, "payment"), c.outstanding_cents ? `${money(c.outstanding_cents)} still owed` : "", low ? "one of your lowest" : ""].filter(Boolean).join(" · ") }));
    }));
  }

  async function reportsTab(body, route) {
    const months = ["3", "6", "12", "24"].includes(route.query.months) ? route.query.months : "12";
    const range = Aira.ui.select([["3", "Last 3 months"], ["6", "Last 6 months"], ["12", "Last 12 months"], ["24", "Last 24 months"]], months, { "aria-label": "Period" });
    range.addEventListener("change", () => Aira.router.go(["studio"], { tab: "reports", months: range.value }));
    const data = await API(`/reports?months=${months}`);
    const late = data.aging.filter((bucket) => bucket.key !== "current").reduce((sum, bucket) => sum + bucket.cents, 0);
    body.append(
      toolbar(range, S.button("Export payments (CSV)", () => Aira.download(`/api/studio/payments/export?since=${data.months[0].month}-01&until=${todayIso()}`, "studio-payments.csv"), { kind: "secondary" })),
      S.kpis([
        { label: "Received", value: money(data.received_cents), hint: `In the last ${plural(Number(months), "month")}`, tone: data.received_cents ? "good" : "" },
        { label: "This year", value: money(data.received_this_year_cents) },
        { label: "Average a month", value: money(data.average_month_cents) },
        { label: "Best month", value: data.best_month ? money(data.best_month.received_cents) : "-", hint: data.best_month ? monthLong(data.best_month.month) : "No payments yet" },
        { label: "Days to get paid", value: data.average_days_to_pay === null ? "-" : data.average_days_to_pay, hint: data.paid_invoices ? `Average over ${plural(data.paid_invoices, "paid invoice")}` : "From invoice to the last payment" },
      ]),
      S.section("Money received each month",
        S.bars(data.months.map((m) => ({ label: monthLabel(m.month), value: m.received_cents })), { format: (v) => money(v) }),
        h("p", { class: "muted small", text: `Invoiced in the same months: ${money(data.months.reduce((sum, m) => sum + m.invoiced_cents, 0))}. Payments count in the month they arrived.` })),
      S.grid(
        S.section("Who pays you most", rankList(data.clients)),
        S.section("Still owed, by how late it is",
          S.kpis(data.aging.map((bucket) => ({ label: bucket.label, value: money(bucket.cents), hint: bucket.count ? plural(bucket.count, "invoice") : "None", tone: bucket.key === "current" ? "" : bucket.cents ? (bucket.key === "1_30" ? "warn" : "bad") : "" }))),
          late ? h("div", { class: "btn-row" }, h("span", { class: "muted", text: `${money(late)} is late.` }), S.button("See overdue invoices", () => Aira.router.go(["studio"], { tab: "invoices", status: "overdue" }), { kind: "secondary sm" })) : h("p", { class: "muted", text: "Nothing is late. Well done." }))),
      S.section("Which work pays best",
        h("p", { class: "muted", text: "What each project invoiced for every hour you logged on it. Low rates show work to price higher next time." }),
        S.table({
          columns: [
            { key: "name", label: "Project", render: (p) => h("div", {}, h("strong", { text: p.name }), h("div", { class: "muted small", text: who(p) })) },
            { key: "invoiced_cents", label: "Invoiced", num: true, render: (p) => money(p.invoiced_cents) },
            { key: "minutes", label: "Time", num: true, render: (p) => (p.minutes ? hours(p.minutes) : "No time logged") },
            { key: "hourly_cents", label: "Earned per hour", num: true, render: (p) => hourly(p.hourly_cents) },
          ],
          rows: data.projects,
          onRow: (p) => openProject(p.id),
          empty: Aira.ui.empty("No projects to compare yet", "Log time on your projects and invoice them to see what each hour earns."),
        })),
      ...(data.methods.length ? [S.section("How clients paid", S.table({ columns: [{ key: "method", label: "Method", render: (m) => (METHODS.find((x) => x[0] === m.method) || [0, m.method])[1] }, { key: "cents", label: "Received", num: true, render: (m) => money(m.cents) }], rows: data.methods }))] : []),
    );
  }

  // ===================================================================== settings
  async function settingsTab(body) {
    const { settings } = await API("/settings");
    const form = S.form([
      { name: "invoice_prefix", label: "Invoice number prefix", hint: "INV- gives INV-0001." },
      { name: "proposal_prefix", label: "Proposal number prefix" },
      { name: "payment_terms_days", label: "Payment terms (days)", type: "number", min: 0, max: 365, hint: "An invoice is due this many days after it is issued." },
      { name: "proposal_valid_days", label: "Proposals are valid for (days)", type: "number", min: 1, max: 365 },
      { name: "default_rate_cents", label: "Default hourly rate", type: "money", hint: "Used when you log time without a rate." },
      { name: "default_deposit_percent", label: "Deposit on new proposals (%)", type: "number", min: 0, max: 100, step: "0.01", hint: "0 for none. You can change it on each proposal." },
    ], settings);
    const autoForm = S.form([
      { name: "auto_retainers", label: "Draft each retainer's invoice on its date", type: "checkbox" },
      { name: "retainer_auto_send", label: "... and send it to the client", type: "checkbox", hint: "The email waits in the Business hub outbox for your approval, unless you send that kind automatically." },
      { name: "auto_reminders", label: "Remind clients about overdue invoices", type: "checkbox", hint: "A friendly email with the invoice link. It waits in the outbox for your approval." },
      { name: "reminder_after_days", label: "First reminder, days after the due date", type: "number", min: 0, max: 90 },
      { name: "reminder_every_days", label: "Days between reminders", type: "number", min: 1, max: 90 },
      { name: "reminder_max", label: "Reminders per invoice, at most", type: "number", min: 1, max: 10 },
      { name: "proposal_followup_days", label: "Follow up a proposal with no answer after (days)", type: "number", min: 0, max: 60, hint: "0 for never. One follow-up email per proposal." },
    ], settings);
    const saveAuto = async () => { autoForm.setError(""); try { await API("/settings", { method: "POST", body: autoForm.read() }); Aira.toast("Saved.", "success"); } catch (error) { autoForm.setError(error.message); } };
    const runNow = async () => {
      const { done } = await API("/automation/run", { method: "POST", body: {} });
      const parts = [done.retainer_invoices ? plural(done.retainer_invoices, "retainer invoice") : "", done.reminders ? plural(done.reminders, "reminder") : "", done.followups ? plural(done.followups, "follow-up") : ""].filter(Boolean);
      const missing = done.no_email ? `${done.no_email === 1 ? "1 late invoice or proposal was" : `${done.no_email} late invoices or proposals were`} not emailed because the client has no email address. Add it on the client's page.` : "";
      if (missing) Aira.toast(parts.length ? `Done: ${parts.join(", ")}. Emails wait in the outbox. ${missing}` : missing, "error", { timeout: 12000 });
      else Aira.toast(parts.length ? `Done: ${parts.join(", ")}. Emails wait in the outbox.` : "Nothing needed doing right now.", "success");
    };
    const { probabilities } = await API("/pipeline");
    const pipelineForm = S.form(OPEN_STAGES.map((stage) => ({ name: stage, label: `${STAGE_LABELS[stage]} (%)`, type: "number", min: 0, max: 100 })), probabilities);
    body.append(S.section("Chance of winning at each stage", h("p", { class: "muted", text: "Used for the weighted forecast. Won is always 100% and lost 0%." }), pipelineForm.node,
      h("div", { class: "btn-row" }, S.button("Save chances", async () => { pipelineForm.setError(""); try { await API("/pipeline", { method: "POST", body: pipelineForm.read() }); Aira.toast("Saved.", "success"); } catch (error) { pipelineForm.setError(error.message); } }))));
    body.append(S.section("What Sales does by itself", h("p", { class: "muted", text: "Every few minutes, even when Aira is closed, Sales can draft retainer invoices, remind clients who are late and follow up proposals. You approve every email before it goes." }), autoForm.node,
      h("div", { class: "btn-row" }, S.button("Save reminders and retainers", saveAuto), S.button("Check now", runNow, { kind: "secondary" }), h("a", { href: "#/hub?tab=outbox", text: "Open the outbox" }))));
    body.append(S.section("Sales settings", form.node, h("div", { class: "btn-row" }, S.button("Save settings", async () => { form.setError(""); try { await API("/settings", { method: "POST", body: form.read() }); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); } })),
      h("p", { class: "muted small" }, "Your business name, tax rate, currency and payment link are in the ", h("a", { href: "#/hub?tab=business", text: "Business hub" }), ".")));
  }

  // ===================================================================== search (the box across the top)
  // GET /search answers with a few of each kind (clients and the people at them, deals, proposals, invoices,
  // projects); answers are kept for a few seconds so typing back and forth does not ask again.
  const found = new Map();
  function find(q, limit) {
    const key = `${limit}|${q.toLowerCase()}`;
    const kept = found.get(key);
    if (kept && Date.now() - kept.at < 5000) return kept.data;
    for (const [old, entry] of found) if (Date.now() - entry.at >= 5000) found.delete(old);
    const data = API(`/search?q=${encodeURIComponent(q)}&limit=${limit}`, { silent: true });
    found.set(key, { at: Date.now(), data });
    data.catch(() => found.delete(key));
    return data;
  }
  const words = (status) => String(status || "").replaceAll("_", " ");
  const capital = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
  // Each kind: its heading, icon and tab, how a match reads in the list under the box, and the record it opens.
  const KINDS = [
    { key: "clients", label: "Clients", icon: "recruiters", tab: "clients", open: (c) => `#/studio?tab=clients&client=${c.id}`,
      line: (c) => ({ label: c.company ? `${c.name} (${c.company})` : c.name, sub: ["Client", c.contact ? `Contact: ${c.contact}` : "", c.email, capital(words(c.status))].filter(Boolean).join(" · ") }) },
    { key: "deals", label: "Deals", icon: "stocks", tab: "deals", open: (d) => `#/studio?tab=deals&deal=${d.id}`,
      line: (d) => ({ label: d.title, sub: ["Deal", who(d), STAGE_LABELS[d.stage], money(d.value_cents)].filter(Boolean).join(" · ") }) },
    { key: "invoices", label: "Invoices", icon: "studio-invoice", tab: "invoices", open: (i) => `#/studio?tab=invoices&invoice=${i.id}`,
      line: (i) => ({ label: `Invoice ${i.number}`, sub: [who(i), money(i.total_cents), capital(words(i.status))].filter(Boolean).join(" · ") }) },
    { key: "proposals", label: "Proposals", icon: "documents", tab: "proposals", open: (p) => `#/studio?tab=proposals&proposal=${p.id}`,
      line: (p) => ({ label: `${p.number} · ${p.title}`, sub: ["Proposal", who(p), money(p.total_cents), capital(words(p.status))].filter(Boolean).join(" · ") }) },
    { key: "projects", label: "Projects", icon: "applications", tab: "projects", open: (p) => `#/studio?tab=projects&project=${p.id}`,
      line: (p) => ({ label: p.name, sub: ["Project", who(p), capital(words(p.status))].filter(Boolean).join(" · ") }) },
  ];

  Aira.appSearch.studio = {
    placeholder: "Search Sales",
    // Up to six, taking turns between the kinds so one busy kind does not hide the others.
    async suggest(query) {
      const [data] = await Promise.all([find(query, 3), S.business()]);
      const lists = KINDS.map((kind) => (data[kind.key] || []).map((row) => ({ ...kind.line(row), icon: kind.icon, href: kind.open(row) })));
      const picked = [];
      for (let round = 0; picked.length < 6 && lists.some((list) => list.length > round); round += 1) {
        for (const list of lists) if (list[round] && picked.length < 6) picked.push(list[round]);
      }
      return picked;
    },
    run(query) { Aira.router.go(["studio"], { tab: "search", q: query }); },
  };

  // Enter in the search box: every kind that matches, each row opening its record.
  const RESULT_COLUMNS = {
    clients: [{ key: "name", label: "Client", render: (c) => h("div", {}, h("strong", { text: c.name }), c.company ? h("div", { class: "muted small", text: c.company }) : null) }, { key: "status", label: "Status", render: (c) => pill(c.status) }, { key: "contact", label: "Contact" }, { key: "email", label: "Email" }],
    deals: [{ key: "title", label: "Deal", render: (d) => h("strong", { text: d.title }) }, { key: "client_name", label: "Client", render: who }, { key: "stage", label: "Stage", render: (d) => pill(d.stage) }, { key: "value_cents", label: "Value", num: true, render: (d) => money(d.value_cents) }],
    invoices: [{ key: "number", label: "No." }, { key: "client_name", label: "Client", render: who }, { key: "status", label: "Status", render: (i) => pill(i.status) }, { key: "total_cents", label: "Total", num: true, render: (i) => money(i.total_cents) }, { key: "balance_cents", label: "Still due", num: true, render: (i) => (i.balance_cents ? money(i.balance_cents) : "") }],
    proposals: [{ key: "number", label: "No." }, { key: "title", label: "Proposal", render: (p) => h("strong", { text: p.title }) }, { key: "client_name", label: "Client", render: who }, { key: "status", label: "Status", render: (p) => pill(p.status) }, { key: "total_cents", label: "Total", num: true, render: (p) => money(p.total_cents) }],
    projects: [{ key: "name", label: "Project", render: (p) => h("strong", { text: p.name }) }, { key: "client_name", label: "Client", render: who }, { key: "status", label: "Status", render: (p) => pill(p.status) }, { key: "deadline", label: "Deadline", render: (p) => day(p.deadline) }],
  };
  const SEARCHABLE_TABS = ["clients", "deals", "invoices"]; // their own search shows the rest when a kind fills its 25
  async function searchTab(body, route) {
    const q = (route.query.q || "").trim();
    if (!q) { body.append(Aira.ui.empty("Search Sales", "Type a client, a company, a deal, or an invoice or proposal number in the search box at the top.")); return; }
    const data = await find(q, 25);
    const kinds = KINDS.filter((kind) => (data[kind.key] || []).length);
    if (!kinds.length) {
      body.append(Aira.ui.empty(`Nothing in Sales matches “${q}”`, "Check the spelling, or search for a client's name, a company, an email, or an invoice or proposal number.",
        h("div", { class: "btn-row" }, canCreate() ? h("button", { type: "button", class: "btn", text: "Add a client", onclick: newClient }) : null, h("a", { class: "btn secondary", href: "#/studio?tab=overview", text: "Back to the overview" }))));
      return;
    }
    body.append(...kinds.map((kind) => {
      const rows = data[kind.key];
      const full = rows.length >= 25;
      return S.section(`${kind.label} (${rows.length}${full ? "+" : ""})`,
        // the Contact column only when a client was found through one of its people
        h("div", { class: "studio-results", dataset: { kind: kind.key } }, S.table({ columns: RESULT_COLUMNS[kind.key].filter((column) => column.key !== "contact" || rows.some((row) => row.contact)), rows, onRow: (row) => { location.hash = kind.open(row); } })),
        full && SEARCHABLE_TABS.includes(kind.tab) ? h("p", { class: "small" }, h("a", { href: `#/studio?tab=${kind.tab}&q=${encodeURIComponent(q)}`, text: `See every match in ${kind.label}` })) : null);
    }));
  }

  // ===================================================================== "+ New" (above the left menu)
  // A child's or teen's profile cannot open Sales (the server refuses it), and in the hosted build a member who may
  // only read the business apps has nothing to create: neither gets the button.
  function canCreate() {
    const me = Aira.life && Aira.life.me;
    if (me && me.age_group && !["adult", "senior"].includes(me.age_group)) return false;
    const cloud = Aira.cloud;
    if (cloud && cloud.me && typeof cloud.can === "function" && !cloud.can("suite.manage")) return false;
    return true;
  }
  // Each opens the same form as the button on its tab (?new=<kind>, read by the view below).
  const CREATES = { deal: () => dealForm(), client: () => newClient(), proposal: () => newProposal(), invoice: () => newInvoice(), activity: () => activityForm() };
  Aira.appNew.studio = () => (canCreate() ? [
    { label: "New deal", icon: "stocks", href: "#/studio?tab=deals&new=deal" },
    { label: "New client", icon: "recruiters", href: "#/studio?tab=clients&new=client" },
    { label: "New proposal", icon: "documents", href: "#/studio?tab=proposals&new=proposal" },
    { label: "New invoice", icon: "studio-invoice", href: "#/studio?tab=invoices&new=invoice" },
    { label: "New activity", icon: "interviews", href: "#/studio?tab=activities&new=activity" },
  ] : []);

  // ===================================================================== the view
  let pendingNew = "";
  // A record a link names (?invoice=, ?client=, ... from the search box, Today or another app) opens over the page.
  const DEEP_LINKS = [["invoice", openInvoice], ["proposal", openProposal], ["deal", openDeal], ["client", openClient], ["project", openProject]];

  Aira.views.studio = async (main, route) => {
    // "+ New" comes here with ?new=<kind>: take it off the address first, so Back and refresh do not open the form
    // again, then open the form over the page that draws.
    if (route.query.new) {
      pendingNew = route.query.new;
      const rest = { ...route.query };
      delete rest.new;
      Aira.router.go(["studio"], rest, { replace: true });
      return;
    }
    await S.business(true);
    clientCache = null;
    const searching = route.query.tab === "search";
    const info = TABS.find((item) => item.id === route.query.tab) || TABS[0];
    const q = (route.query.q || "").trim();
    const body = S.page(main, searching
      ? { title: q ? `Results for “${q}”` : "Search", subtitle: "Clients, deals, invoices, proposals and projects in Sales." }
      : { title: info.label, subtitle: info.blurb });
    body.classList.add("studio-page");
    const tab = S.tabs(body, route, "studio", TABS, searching ? "search" : info.id);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    let ready = false;
    rerender = async () => {
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "search") await searchTab(holder, route);
        else if (tab === "deals") await dealsTab(holder, route);
        else if (tab === "activities") await activitiesTab(holder, route);
        else if (tab === "clients") await clientsTab(holder, route);
        else if (tab === "proposals") await proposalsTab(holder, route);
        else if (tab === "projects") await projectsTab(holder, route);
        else if (tab === "invoices") await invoicesTab(holder, route);
        else if (tab === "retainers") await retainersTab(holder);
        else if (tab === "reports") await reportsTab(holder, route);
        else if (tab === "settings") await settingsTab(holder);
        else await overview(holder);
        panel.replaceChildren(holder);
        ready = true;
      } catch (error) {
        ready = false;
        // a child's or teen's profile: Sales moves money and emails clients, so it is for the grown-ups only
        if (error && error.code === "studio_grown_ups") {
          const canSwitch = Aira.life && typeof Aira.life.switchPerson === "function";
          panel.replaceChildren(Aira.ui.empty("Sales is for the grown-ups", "It sends emails to clients and keeps track of money, so only the grown-ups in your household can open it.", canSwitch ? h("button", { type: "button", class: "btn", text: "Switch person", onclick: () => Aira.life.switchPerson() }) : null));
        } else panel.replaceChildren(Aira.ui.error(error, rerender));
      }
    };
    panel.replaceChildren(Aira.ui.skeleton(4));
    await rerender();
    const create = CREATES[pendingNew];
    pendingNew = "";
    if (!ready || !panel.isConnected) return;
    if (create && canCreate()) Promise.resolve().then(create).catch(Aira.reportError);
    for (const [name, open] of DEEP_LINKS) if (/^[0-9a-f]{32}$/.test(route.query[name] || "")) open(route.query[name]).catch(Aira.reportError);
  };
})();

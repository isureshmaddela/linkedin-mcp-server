/* Aira — Service (#/service): help your customers fast, and get paid for the help that is worth paying for.
 *
 * Tabs (?tab=): inbox (cases by view with filters, bulk actions and a drawer per case: the conversation, the reply
 * box with saved replies, suggested help articles and an AI suggestion, charges, and the customer's earlier
 * requests), customers (everyone who asked for help, with their history), chat (live chats from the public chat
 * page, answered live), help (the help centre: categories, articles and its public link), paid (paid services
 * customers ask for from the help centre, payments waiting and received), replies (saved replies with {customer}
 * and {case}), settings (due times, business hours, live chat) and reports (the team, due times kept, ratings,
 * topics, money). Everything runs through /api/service/* (aira/suite/service/service_routes.py).
 * In the Aira frame (shell.js): "+ New" offers a case, a help article, a paid service and a saved reply
 * (?new=<kind> opens that form once the tab is drawn); the search across the top suggests cases, customers and help
 * articles (?open=<id> opens one) and Enter searches all cases; the tabs are the left menu's folders.
 * DOM is built with h() and text only; nothing a customer wrote is parsed as HTML.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const API = (path, opts) => S.api("service", path, opts);

  // Each tab is a folder in the left menu; the page head names the open tab, like a mail app names the open folder.
  const TABS = [
    { id: "inbox", label: "Inbox", icon: "applications", title: "Inbox", intro: "Every customer question in one place, the most urgent first." },
    { id: "customers", label: "Customers", icon: "recruiters", title: "Customers", intro: "Everyone who asked you for help, with their requests, ratings and payments." },
    { id: "chat", label: "Live chat", icon: "social", title: "Live chat", intro: "Answer visitors from your live chat page while they are still there." },
    { id: "help", label: "Help centre", icon: "learn", title: "Help centre", intro: "Answers customers find themselves, on a page you share." },
    { id: "paid", label: "Paid help", icon: "shop", title: "Paid help", intro: "Sell the help that is worth paying for, and see what it earns." },
    { id: "replies", label: "Saved replies", icon: "documents", title: "Saved replies", intro: "Answers you send often. Insert one from the reply box of any case." },
    { id: "settings", label: "Settings", icon: "settings", title: "Service settings", intro: "Due times, business hours, live chat and when solved cases close." },
    { id: "reports", label: "Reports", icon: "analytics", title: "Reports", intro: "How fast you answer, what customers think and what paid help earns." },
  ];
  const VIEWS = [
    ["open", "Open"],
    ["mine", "My cases"],
    ["unassigned", "Unassigned"],
    ["due_soon", "Due soon"],
    ["overdue", "Overdue"],
    ["waiting", "Waiting on customer"],
    ["solved", "Solved"],
    ["closed", "Closed"],
    ["all", "All cases"],
  ];
  const PRIORITIES = [["low", "Low"], ["normal", "Normal"], ["high", "High"], ["urgent", "Urgent"]];
  const STATUSES = [["new", "New"], ["open", "Open"], ["waiting", "Waiting on customer"], ["solved", "Solved"], ["closed", "Closed"]];
  const STATUS_LABEL = Object.fromEntries(STATUSES);
  const CHANNELS = { web: "Web form", chat: "Live chat", email: "Email", phone: "Phone", manual: "Entered by hand" };
  const TONES = { urgent: "bad", high: "warn", normal: "", low: "", new: "info", open: "warn", waiting: "", solved: "good", closed: "", due: "warn", paid: "good" };
  const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  const pill = (value) => S.pill(value, TONES);
  const statusPill = (value) => h("span", { class: `suite-pill ${TONES[value] || ""}`, text: STATUS_LABEL[value] || value });
  const money = (cents) => S.money(cents);
  function duration(minutes) {
    if (minutes === null || minutes === undefined) return "-";
    if (minutes < 60) return `${Math.max(1, Math.round(minutes))} min`;
    if (minutes < 60 * 24) return `${Math.round(minutes / 6) / 10} h`;
    return `${Math.round(minutes / 144) / 10} days`;
  }
  function dueText(c) {
    if (["solved", "closed"].includes(c.status)) return "";
    const target = !c.first_responded_at && c.first_response_due ? c.first_response_due : c.resolution_due;
    if (!target) return "";
    const minutes = (new Date(target) - Date.now()) / 60000;
    if (c.overdue || minutes < 0) return "Overdue";
    return `Due in ${duration(minutes)}`;
  }
  const stars = (score) => (score ? `${"★".repeat(score)}${"☆".repeat(5 - score)}` : "");
  // A table row opens on click or Enter. Enter on a button, link or box inside the row (Mark paid, a tag, Delete,
  // the tick box) is for that control only: the row opens only when the row itself has the focus.
  const rowOpens = (open) => (row) => {
    const focused = document.activeElement;
    if (focused && focused.tagName !== "TR" && focused.closest("tr") && focused.matches("button, a, input, select, textarea, [role='button']")) return;
    open(row);
  };
  // Service holds customers' details and money: a child or teen household profile cannot open it (the server
  // refuses too), so "+ New" and the search offer them nothing.
  const grownUp = () => { const me = Aira.life && Aira.life.me; return !me || !me.age_group || ["adult", "senior"].includes(me.age_group); };
  // In the hosted build an auditor (or a billing contact) only reads Service: nothing to create (aira_cloud/rbac.py).
  const canCreate = () => grownUp() && !(Aira.cloud && typeof Aira.cloud.role === "function" && ["auditor", "billing", "candidate"].includes(Aira.cloud.role()));
  // What "+ New" or a search result asked for ({ make, open }): the address drops ?new= and ?open= at once, so Back
  // and refresh do not open it again, and the tab opens it once it is drawn.
  let pending = null;
  // Who a case is from, as the server keys customers (desk._customer_key): the email, else the name, else tel:digits.
  const customerKey = (c) => {
    const found = String(c.customer_email || c.customer_name || "").trim().toLowerCase();
    if (found) return found;
    const digits = String(c.customer_phone || "").replace(/\D/g, "");
    return digits ? `tel:${digits}` : "";
  };

  // ---------------------------------------------------------------- inbox
  async function inbox(body, route, wanted) {
    const view = route.query.view || "open";
    const filters = { q: route.query.q || "", priority: route.query.priority || "", channel: route.query.channel || "", tag: route.query.tag || "" };
    const active = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
    const params = new URLSearchParams({ view, ...active });
    const data = await API(`/cases?${params}`);
    const go = (changes) => Aira.router.go(["service"], { tab: "inbox", view, ...filters, ...changes });
    const chips = h("div", { class: "btn-row sv-views", role: "toolbar", "aria-label": "Views" }, ...VIEWS.map(([id, label]) => {
      const count = data.views[id];
      return h("button", { type: "button", class: `btn sm ${id === view ? "" : "secondary"}`, "aria-pressed": String(id === view), onclick: () => go({ view: id }) }, h("span", { text: label }), count !== undefined ? h("span", { class: "sv-count", text: ` ${count}` }) : null);
    }));
    const search = h("input", { type: "search", "data-search": "", placeholder: "Search by subject, customer, phone or number", value: filters.q, "aria-label": "Search cases" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    const priority = Aira.ui.select([["", "Any priority"], ...PRIORITIES], filters.priority, { "aria-label": "Priority" });
    priority.addEventListener("change", () => go({ priority: priority.value }));
    const channel = Aira.ui.select([["", "Any channel"], ...Object.entries(CHANNELS)], filters.channel, { "aria-label": "Channel" });
    channel.addEventListener("change", () => go({ channel: channel.value }));
    const hasFilters = Object.keys(active).length > 0;

    // Several cases at once: tick them, then act.
    const chosen = new Set();
    const boxes = [];
    const bulkCount = h("strong", { text: "" });
    const bulkBar = h("div", { class: "sv-bulk", role: "region", "aria-label": "Selected cases", hidden: true });
    const refreshBulk = () => {
      bulkBar.hidden = chosen.size === 0;
      bulkCount.textContent = `${chosen.size} selected`;
    };
    const runBulk = async (action, extra = {}) => {
      if (action === "delete" && !(await S.confirmDelete(`${chosen.size} ${chosen.size === 1 ? "case" : "cases"}`))) return;
      const result = await API("/cases/bulk", { method: "POST", body: { ids: [...chosen], action, ...extra } });
      const skipped = result.skipped.length ? ` ${result.skipped.length} skipped: ${result.skipped[0].reason}` : "";
      Aira.toast(`${result.done} ${result.done === 1 ? "case" : "cases"} updated.${skipped}`, result.skipped.length ? "" : "success");
      Aira.router.render();
    };
    bulkBar.append(bulkCount,
      S.button("Assign to me", async () => runBulk("assign", { assignee: data.me || "" }), { kind: "secondary sm" }),
      S.button("Mark solved", () => runBulk("solve"), { kind: "secondary sm" }),
      S.button("Close", () => runBulk("close"), { kind: "secondary sm" }),
      S.button("Delete", () => runBulk("delete"), { kind: "ghost sm" }),
      S.button("Clear", () => { chosen.clear(); boxes.forEach((box) => { box.checked = false; }); refreshBulk(); }, { kind: "ghost sm" }));
    const selectBox = (c) => {
      const box = h("input", { type: "checkbox", "aria-label": `Select case ${c.reference}` });
      box.addEventListener("change", () => { if (box.checked) chosen.add(c.id); else chosen.delete(c.id); refreshBulk(); });
      boxes.push(box);
      return box;
    };

    const csv = new URLSearchParams({ view, ...active, format: "csv" });
    // A search (from the box across the top or this one) says what it found and where else to look.
    const viewName = (VIEWS.find(([id]) => id === view) || [view, view])[1].toLowerCase();
    const results = filters.q ? h("p", { class: "sv-results", role: "status" },
      h("span", { text: `${data.cases.length} ${data.cases.length === 1 ? "case" : "cases"} in ${viewName} for ` }), h("strong", { translate: "no", text: `“${filters.q}”` }), h("span", { text: " · " }),
      view === "all" ? null : h("a", { href: `#/service?${new URLSearchParams({ tab: "inbox", view: "all", q: filters.q })}`, text: "Search all cases" }), view === "all" ? null : h("span", { text: " · " }),
      h("a", { href: `#/service?${new URLSearchParams({ tab: "customers", q: filters.q })}`, text: "Search customers" })) : null;
    const startNew = () => newCase((c) => { Aira.router.render(); openCase(c.id, () => Aira.router.render()); });
    body.append(...[
      chips,
      h("div", { class: "sv-filters" }, search, priority, channel,
        S.button("Download CSV", () => Aira.download(`/api/service/cases?${csv}`, "cases.csv"), { kind: "ghost sm" })),
      results,
      // One row of list tools: the tag being shown, clearing the filters and ticking every case.
      hasFilters || data.cases.length ? h("div", { class: "btn-row" },
        filters.tag ? h("span", { class: "muted small", text: `Tag: ${filters.tag}` }) : null,
        hasFilters ? S.button("Clear filters", () => Aira.router.go(["service"], { tab: "inbox", view }), { kind: "ghost sm" }) : null,
        data.cases.length ? S.button("Select all", () => { data.cases.forEach((c) => chosen.add(c.id)); boxes.forEach((box) => { box.checked = true; }); refreshBulk(); }, { kind: "ghost sm" }) : null) : null,
      bulkBar,
      h("div", { class: "sv-cases" }, S.table({
        caption: "Cases",
        columns: [
          { key: "pick", label: "", render: selectBox },
          { key: "reference", label: "Case" },
          { key: "subject", label: "Subject", render: (c) => h("span", { class: "sv-subject" }, h("strong", { text: c.subject }), ...c.tags.map((tag) => h("button", { type: "button", class: "sv-tag", title: `Show cases tagged ${tag}`, text: tag, onclick: () => go({ tag }) })),
            // On a phone the number, customer and due time ride under the subject instead of in their own columns.
            h("span", { class: "sv-case-meta muted small", translate: "no", text: [c.reference, c.customer_name || c.customer_email || c.customer_phone, dueText(c)].filter(Boolean).join(" · ") })) },
          { key: "customer", label: "Customer", render: (c) => c.customer_name || c.customer_email || c.customer_phone || "-" },
          { key: "priority", label: "Priority", render: (c) => pill(c.priority) },
          { key: "status", label: "Status", render: (c) => statusPill(c.status) },
          { key: "due", label: "Due", render: (c) => { const text = dueText(c); return text === "Overdue" ? S.pill("overdue", { overdue: "bad" }) : text; } },
          { key: "assignee", label: "Assigned to", render: (c) => c.assignee || "-" },
        ],
        rows: data.cases,
        empty: Aira.ui.empty(hasFilters ? "No cases match" : "No cases here", hasFilters ? "Try other words or clear the filters." : view === "open" ? "When customers write through your help centre or chat, their cases appear here. You can also add one yourself." : "Try another view.",
          !hasFilters && view === "open" ? h("div", { class: "btn-row sv-empty-actions" }, S.button("New case", startNew, { icon: "plus" }), h("a", { class: "btn secondary", href: "#/service?tab=help", text: "Share your help centre" })) : null),
        onRow: rowOpens((c) => openCase(c.id, () => Aira.router.render())),
      })),
    ].filter(Boolean));
    if (wanted && wanted.make === "case") startNew();
    else if (wanted && wanted.open) openCase(wanted.open, () => Aira.router.render());
  }

  function newCase(done) {
    return S.edit({
      title: "New case",
      fields: [
        { name: "subject", label: "Subject", required: true },
        { name: "description", label: "What the customer needs", type: "textarea", rows: 4 },
        { name: "customer_name", label: "Customer name" },
        { name: "customer_email", label: "Customer email", type: "email" },
        { name: "customer_phone", label: "Customer phone", type: "tel" },
        { name: "channel", label: "How they contacted you", type: "select", options: [["manual", "Entered by hand"], ["phone", "Phone"], ["email", "Email"]] },
        { name: "priority", label: "Priority", type: "select", options: PRIORITIES },
        { name: "tags", label: "Tags", placeholder: "refund, vip" },
      ],
      values: { priority: "normal", channel: "manual" },
      onSave: async (values) => { const data = await API("/cases", { method: "POST", body: values }); Aira.toast(`Case ${data.case.reference} created.`, "success"); done(data.case); },
    });
  }

  // ---------------------------------------------------------------- one case
  async function openCase(caseId, changed) {
    let detail;
    try { detail = await API(`/cases/${caseId}`); } catch (error) { Aira.reportError(error); return; }
    const [macros, overview, offers, articles] = await Promise.all([
      API("/macros", { silent: true }).catch(() => ({ macros: [] })),
      API("/overview", { silent: true }).catch(() => ({})),
      API("/offers", { silent: true }).catch(() => ({ offers: [], payment_link: "" })),
      API("/articles", { silent: true }).catch(() => ({ articles: [] })),
    ]);
    const me = overview.me || "";
    const helpLink = overview.help_link || null;
    const c = detail.case;
    const closed = c.status === "closed";
    const reopen = () => { changed(); openCase(caseId, changed); };
    const thread = h("ol", { class: "sv-thread", "aria-label": "Conversation" }, ...detail.messages.map((m) => h("li", { class: `sv-msg ${m.author_kind}${m.internal ? " internal" : ""}` },
      h("div", { class: "sv-msg-head" }, h("strong", { translate: "no", text: m.author_kind === "customer" ? (m.author_name || "Customer") : m.author_name || (m.author_kind === "system" ? "Monosage" : "You") }),
        m.internal && m.author_kind !== "system" ? S.pill("internal note", { "internal note": "warn" }) : null,
        h("span", { class: "muted small", text: S.when(m.created_at) })),
      h("p", { class: "sv-msg-body", translate: "no", text: m.body }),
      ...(m.attachments || []).map((link) => h("a", { href: link, target: "_blank", rel: "noopener noreferrer", class: "small", text: link })))));

    const status = Aira.ui.select(STATUSES, c.status, { "aria-label": "Status", disabled: closed });
    const priority = Aira.ui.select(PRIORITIES, c.priority, { "aria-label": "Priority", disabled: closed });
    const assignee = h("input", { type: "text", value: c.assignee || "", placeholder: "Unassigned", "aria-label": "Assigned to" });
    const tags = h("input", { type: "text", value: c.tags.join(", "), "aria-label": "Tags" });
    const save = async (changes, message = "Saved.") => {
      try { await API(`/cases/${caseId}`, { method: "PATCH", body: changes }); Aira.toast(message, "success"); reopen(); } catch (error) { Aira.reportError(error); }
    };

    const text = h("textarea", { rows: 5, placeholder: "Write your reply to the customer", "aria-label": "Reply" });
    const insert = (value) => { text.value = text.value ? `${text.value}\n\n${value}` : value; text.focus(); };
    const internal = h("input", { type: "checkbox" });
    const files = h("input", { type: "text", placeholder: "https:// links to files (optional)", "aria-label": "Attachment links" });
    const after = Aira.ui.select([["waiting", "Then wait for the customer"], ["open", "Keep it open"], ["solved", "Mark it solved"]], "waiting", { "aria-label": "After sending" });
    const saved = Aira.ui.select([["", macros.macros.length ? "Insert a saved reply" : "No saved replies yet"], ...macros.macros.map((m) => [m.id, m.title])], "", { "aria-label": "Saved replies" });
    saved.addEventListener("change", async () => {
      if (!saved.value) return;
      try { const filled = await API(`/macros/${saved.value}/use`, { method: "POST", body: { case_id: caseId } }); insert(filled.text); } catch (error) { Aira.reportError(error); }
      saved.value = "";
    });
    // A help article goes in as its title and link when the help centre is shared, otherwise as its text.
    const insertArticle = async (articleId) => {
      const { article } = await API(`/articles/${articleId}`);
      insert(helpLink ? `${article.title}: ${S.absolute(helpLink.path)}#${article.id}` : `${article.title}\n${article.body}`);
    };
    const published = (articles.articles || []).filter((a) => a.status === "published");
    const answerPick = Aira.ui.select([["", published.length ? "Insert a help article" : "No published articles yet"], ...published.map((a) => [a.id, a.title])], "", { "aria-label": "Help articles" });
    answerPick.addEventListener("change", async () => {
      if (!answerPick.value) return;
      try { await insertArticle(answerPick.value); } catch (error) { Aira.reportError(error); }
      answerPick.value = "";
    });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const send = S.button("Send", async () => {
      error.hidden = true;
      if (!text.value.trim()) { error.textContent = internal.checked ? "Write the note first." : "Write your reply first."; error.hidden = false; text.focus(); return; }
      try {
        await API(`/cases/${caseId}/reply`, { method: "POST", body: { body: text.value, internal: internal.checked, attachments: files.value.split(/\s+/).filter(Boolean), status_after: after.value } });
        Aira.toast(internal.checked ? "Note added." : "Reply ready in the outbox.", "success");
        reopen();
      } catch (err) { error.textContent = err.message; error.hidden = false; }
    });
    const suggest = S.button("Suggest a reply", async () => {
      const data = await API(`/cases/${caseId}/suggest`, { method: "POST", body: {} });
      text.value = data.reply;
      text.focus();
      if (data.source === "template") Aira.toast("A starting reply from a template. Edit it before sending.");
    }, { kind: "ghost sm" });
    internal.addEventListener("change", () => { send.querySelector("span").textContent = internal.checked ? "Add note" : "Send"; after.disabled = internal.checked; });

    const answers = detail.answers || [];
    const suggestions = answers.length && !closed ? h("div", { class: "sv-answers" }, h("span", { class: "muted small", text: "Articles that may answer this:" }),
      ...answers.map((a) => S.button(a.title, () => insertArticle(a.id), { kind: "ghost sm", title: "Insert into the reply" }))) : null;

    const editDetails = () => S.edit({
      title: "Edit case details",
      fields: [
        { name: "subject", label: "Subject", required: true },
        { name: "customer_name", label: "Customer name" },
        { name: "customer_email", label: "Customer email", type: "email" },
        { name: "customer_phone", label: "Customer phone", type: "tel" },
      ],
      values: { subject: c.subject, customer_name: c.customer_name, customer_email: c.customer_email, customer_phone: c.customer_phone },
      onSave: async (values) => { await API(`/cases/${caseId}`, { method: "PATCH", body: values }); Aira.toast("Saved.", "success"); reopen(); },
    });
    const deleteCase = async () => {
      if (!(await S.confirmDelete(`case ${c.reference}`))) return;
      try {
        const removed = await API(`/cases/${caseId}`, { method: "DELETE" });
        const merged = (removed.cases || 1) - 1;
        Aira.closeDrawer();
        Aira.toast(merged ? `Case ${c.reference} deleted with the ${merged === 1 ? "case" : `${merged} cases`} merged into it.` : `Case ${c.reference} deleted.`, "success");
        changed();
      } catch (err) { Aira.reportError(err); }
    };

    // Charges: paid help on this case, paid through the business's own payment link.
    const charges = detail.charges || [];
    const due = charges.filter((x) => x.status === "due").reduce((sum, x) => sum + x.amount_cents, 0);
    const addCharge = () => S.edit({
      title: "Charge for this case",
      fields: [
        offers.offers.length ? { name: "offer_id", label: "Paid service", type: "select", options: [["", "Something else"], ...offers.offers.filter((o) => o.active).map((o) => [o.id, `${o.title} (${money(o.price_cents)})`])] } : null,
        { name: "label", label: "What it is for", placeholder: "Repair visit", hint: offers.offers.length ? "Leave empty to use the paid service's name." : "" },
        { name: "amount_cents", label: "Amount", type: "money", hint: offers.offers.length ? "Leave empty to use the paid service's price." : "" },
      ].filter(Boolean),
      onSave: async (values) => {
        if (!values.offer_id && !String(values.label || "").trim()) throw new Error("Enter what the charge is for.");
        if (!values.offer_id && !values.amount_cents) throw new Error("Enter the amount, like 25.00.");
        await API(`/cases/${caseId}/charges`, { method: "POST", body: values });
        Aira.toast(c.customer_email ? "Charge added. The email with the amount is in the outbox." : "Charge added.", "success");
        reopen();
      },
    });
    const chargeRows = charges.length ? h("ul", { class: "sv-charges" }, ...charges.map((x) => h("li", {},
      h("span", { class: "sv-charge-label" }, h("strong", { text: x.label }), h("span", { class: "muted small", text: x.status === "paid" ? ` paid ${S.day(x.paid_at)}` : ` added ${S.day(x.created_at)}` })),
      h("span", { class: "sv-charge-amount", text: money(x.amount_cents) }), pill(x.status),
      h("span", { class: "btn-row" },
        x.status === "due" ? S.button("Mark paid", async () => { await API(`/charges/${x.id}/paid`, { method: "POST", body: { paid: true } }); Aira.toast("Payment recorded. It counts in your revenue.", "success"); reopen(); }, { kind: "secondary sm" })
          : S.button("Mark unpaid", async () => { await API(`/charges/${x.id}/paid`, { method: "POST", body: { paid: false } }); Aira.toast("Marked as not paid."); reopen(); }, { kind: "ghost sm" }),
        x.status === "due" ? S.button("Remove", async () => { if (await S.confirmDelete(`the charge "${x.label}"`)) { await API(`/charges/${x.id}`, { method: "DELETE" }); reopen(); } }, { kind: "ghost sm" }) : null)))) : null;
    const chargeSection = S.section("Charges",
      charges.length ? null : h("p", { class: "muted", text: "Charge for help that is worth paying for: a repair, a setup call, priority support. The customer sees the amount and how to pay on their request page." }),
      chargeRows,
      due ? h("p", {}, h("strong", { text: `Waiting to be paid: ${money(due)}` })) : null,
      !offers.payment_link ? h("p", { class: "muted small" }, h("span", { text: "Customers pay with your own payment link. Add it under " }), h("a", { href: "#/hub?tab=business", text: "Business profile" }), h("span", { text: "." })) : null,
      closed || c.merged_into ? null : h("div", { class: "btn-row" }, S.button("Add a charge", addCharge, { kind: "secondary sm" })));

    const history = detail.history || [];
    const historySection = history.length ? S.section(`Earlier requests from this customer (${history.length})`,
      h("ul", { class: "sv-history" }, ...history.map((x) => h("li", {}, h("button", { type: "button", class: "sv-link", onclick: () => openCase(x.id, changed) }, h("strong", { text: `${x.reference} ` }), h("span", { text: x.subject })), statusPill(x.status), x.csat_score ? h("span", { class: "muted small", text: ` ${stars(x.csat_score)}` }) : null, h("span", { class: "muted small", text: ` ${S.day(x.created_at)}` }))))) : null;

    const body = h("div", { class: "suite-body sv-case" },
      Aira.ui.kv([
        ["Customer", h("span", { translate: "no", text: [c.customer_name, c.customer_email, c.customer_phone].filter(Boolean).join(" · ") || "-" })],
        ["Channel", CHANNELS[c.channel] || c.channel],
        ["Opened", S.when(c.created_at)],
        ["First reply", c.first_responded_at ? S.when(c.first_responded_at) : dueText(c) ? `Due ${S.when(c.first_response_due)}` : "-"],
        ["Solution due", closed || c.status === "solved" ? "" : S.when(c.resolution_due)],
        ["Rating", c.csat_score ? `${stars(c.csat_score)} ${c.csat_score} out of 5${c.csat_comment ? `: ${c.csat_comment}` : ""}` : ""],
      ]),
      h("div", { class: "suite-grid" },
        Aira.ui.field("Status", status), Aira.ui.field("Priority", priority),
        Aira.ui.field("Assigned to", assignee, me ? "Your name or email" : ""), Aira.ui.field("Tags", tags)),
      h("div", { class: "btn-row" },
        closed ? null : S.button("Save changes", () => save({ status: status.value, priority: priority.value, assignee: assignee.value, tags: tags.value }), { kind: "secondary" }),
        me && !closed && c.assignee !== me ? S.button("Assign to me", () => save({ assignee: me }, "Assigned to you."), { kind: "ghost sm" }) : null,
        closed ? null : S.button("Edit details", editDetails, { kind: "ghost sm" }),
        S.button("Copy the customer's link", async () => { const made = await API(`/cases/${caseId}/link`, { method: "POST", body: {} }); await S.copy(made.url); }, { kind: "ghost sm" }),
        closed ? null : S.button("Merge into another case", () => S.edit({ title: "Merge this case", fields: [{ name: "number", label: "The case number to keep (for example 1001)", required: true, hint: "Only a case from the same customer. This case's messages and charges move there, and this case closes." }], onSave: async (values) => {
          const wanted = String(values.number || "").replace(/\D/g, "");
          const found = await API(`/cases?view=all&q=${encodeURIComponent(wanted)}`);
          const target = found.cases.find((x) => String(x.number) === wanted);
          if (!target) throw new Error("No case has that number.");
          await API(`/cases/${caseId}/merge`, { method: "POST", body: { into: target.id } });
          Aira.toast(`Merged into ${target.reference}.`, "success");
          changed();
          openCase(target.id, changed);
        } }), { kind: "ghost sm" }),
        S.button("Delete case", deleteCase, { kind: "ghost sm" })),
      S.section("Conversation", thread),
      closed ? h("p", { class: "muted", text: c.merged_into ? "This case was merged into another case." : "This case is closed." }) : S.section("Reply",
        h("div", { class: "sv-reply-tools" }, saved, answerPick, suggest),
        suggestions,
        text,
        h("label", { class: "suite-check" }, internal, h("span", { text: "Internal note: only your team sees it" })),
        files, after, error, h("div", { class: "btn-row" }, send)),
      chargeSection,
      historySection,
    );
    Aira.drawer({ title: `${c.reference} · ${c.subject}`, subtitle: [statusPill(c.status), " ", pill(c.priority)], body });
  }

  // ---------------------------------------------------------------- customers
  async function customersTab(body, route, wanted) {
    const q = route.query.q || "";
    const data = await API(`/customers?${new URLSearchParams(q ? { q } : {})}`);
    const search = h("input", { type: "search", placeholder: "Search by name, email or phone", value: q, "aria-label": "Search customers" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") Aira.router.go(["service"], { tab: "customers", q: search.value.trim() }); });
    const openCustomer = async (person) => {
      const { cases } = await API(`/customers/cases?${new URLSearchParams({ key: person.key })}`);
      const panel = h("div", { class: "suite-body" },
        Aira.ui.kv([["Email", person.email], ["Phone", person.phone], ["First contact", S.day(person.first_contact)], ["Last contact", S.when(person.last_contact)],
          ["Rating", person.csat_average === null ? "" : `${person.csat_average} out of 5`], ["Paid", person.paid_cents ? money(person.paid_cents) : ""], ["Waiting to be paid", person.due_cents ? money(person.due_cents) : ""]]),
        S.section("Requests", S.table({
          columns: [
            { key: "reference", label: "Case" },
            { key: "subject", label: "Subject", render: (x) => h("strong", { text: x.subject }) },
            { key: "status", label: "Status", render: (x) => statusPill(x.status) },
            { key: "created_at", label: "Opened", render: (x) => S.day(x.created_at) },
          ],
          rows: cases,
          empty: Aira.ui.empty("No requests", ""),
          onRow: rowOpens((x) => openCase(x.id, () => Aira.router.render())),
        })));
      Aira.drawer({ title: person.name || person.email || person.phone, subtitle: `${person.cases} ${person.cases === 1 ? "request" : "requests"}`, body: panel });
    };
    body.append(
      h("div", { class: "sv-filters sv-filters-short" }, search,
        S.button("Download CSV", () => Aira.download(`/api/service/customers?${new URLSearchParams({ ...(q ? { q } : {}), format: "csv" })}`, "customers.csv"), { kind: "ghost sm" })),
      h("div", { class: "sv-customers" }, S.table({
        caption: "Customers",
        columns: [
          { key: "name", label: "Customer", render: (p) => h("span", { class: "sv-person" }, h("strong", { translate: "no", text: p.name || p.email || p.phone }), p.name && p.email ? h("span", { class: "muted small", translate: "no", text: p.email }) : null) },
          { key: "cases", label: "Requests", num: true },
          { key: "open", label: "Open", num: true },
          { key: "last_contact", label: "Last contact", render: (p) => S.day(p.last_contact) },
          { key: "rating", label: "Rating", render: (p) => (p.csat_average === null ? "-" : `${p.csat_average} / 5`) },
          { key: "paid", label: "Paid", num: true, render: (p) => (p.paid_cents ? money(p.paid_cents) : "-") },
        ],
        rows: data.customers,
        empty: Aira.ui.empty(q ? "Nobody matches" : "No customers yet", q ? "Try another name, email or phone." : "People who write to you through the help centre, chat or a case you add appear here.",
          q ? S.button("Show everyone", () => Aira.router.go(["service"], { tab: "customers" }), { kind: "secondary" }) : h("a", { class: "btn secondary", href: "#/service?tab=help", text: "Share your help centre" })),
        onRow: rowOpens(openCustomer),
      })),
    );
    const person = wanted && wanted.open ? data.customers.find((p) => p.key === wanted.open) : null;
    if (person) openCustomer(person).catch(() => {}); // a failed request already showed its toast
  }

  // ---------------------------------------------------------------- live chat
  async function chatTab(body) {
    const overview = await API("/overview");
    let current = null;
    let cursor = 0;
    const list = h("div", { class: "sv-chat-list", role: "list" });
    const pane = h("div", { class: "sv-chat-pane" }, Aira.ui.empty("Choose a chat", "Its messages and the reply box open here."));
    const layout = h("div", { class: "sv-chat" }, list, pane);
    const share = S.share({
      title: "Live chat page",
      text: "Put this link on your website or social profiles. Visitors start a chat and you answer here.",
      link: overview.chat_link,
      onCreate: async () => (await API("/chat/link", { method: "POST", body: {} })).link,
      onRevoke: async () => { await API("/chat/link", { method: "DELETE" }); },
    });
    const renderList = async () => {
      const data = await API("/chats", { silent: true });
      const waiting = data.chats.filter((chat) => chat.waiting_for_us).length;
      Aira.renderNav({ service: waiting });
      // No chats yet: one clear message, not an empty list beside an empty pane.
      layout.classList.toggle("is-empty", !data.chats.length);
      list.replaceChildren(...(data.chats.length ? data.chats.map((chat) => h("button", { type: "button", role: "listitem", class: `sv-chat-item ${current === chat.id ? "active" : ""}`, onclick: () => openChat(chat.id) },
        h("strong", { translate: "no", text: chat.visitor_name || "Visitor" }), chat.waiting_for_us ? S.pill("waiting for you", { "waiting for you": "warn" }) : S.pill(chat.status),
        h("span", { class: "muted small", translate: "no", text: chat.last_message }))) : [Aira.ui.empty("No chats right now", data.online ? "Share your live chat page above so visitors can start one. New chats appear here as they arrive." : "Live chat is off. Turn it on under Settings.")]));
    };
    const messages = h("ol", { class: "sv-thread", "aria-live": "polite" });
    const addMessages = (rows) => {
      for (const m of rows) messages.append(h("li", { class: `sv-msg ${m.author_kind === "visitor" ? "customer" : "agent"}` }, h("div", { class: "sv-msg-head" }, h("strong", { translate: "no", text: m.author_name || (m.author_kind === "visitor" ? "Visitor" : "You") }), h("span", { class: "muted small", text: S.when(m.created_at) })), h("p", { class: "sv-msg-body", translate: "no", text: m.body })));
      messages.lastElementChild?.scrollIntoView({ block: "nearest" });
    };
    async function openChat(chatId) {
      current = chatId;
      const data = await API(`/chats/${chatId}`);
      cursor = data.cursor;
      messages.replaceChildren();
      addMessages(data.messages);
      const text = h("textarea", { rows: 2, placeholder: "Type a reply", "aria-label": "Reply" });
      const sendIt = async () => {
        if (!text.value.trim()) return;
        const sent = await API(`/chats/${chatId}/reply`, { method: "POST", body: { body: text.value } });
        text.value = "";
        addMessages(sent.messages.filter((m) => m.seq > cursor));
        cursor = sent.cursor;
        renderList();
      };
      text.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendIt(); } });
      const ended = data.chat.status === "ended";
      pane.replaceChildren(...[
        h("div", { class: "page-head" }, h("div", {}, h("h2", { translate: "no", text: data.chat.visitor_name || "Visitor" }), h("p", { class: "muted", translate: "no", text: data.chat.visitor_email || "" }))),
        messages,
        ended ? h("p", { class: "muted", text: "This chat has ended." }) : h("div", { class: "suite-body" }, text, h("div", { class: "btn-row" },
          S.button("Send", sendIt),
          S.button(data.chat.case_id ? "Open its case" : "Make it a case", async () => {
            const made = await API(`/chats/${chatId}/case`, { method: "POST", body: {} });
            if (!data.chat.case_id) { Aira.toast(`Case ${made.case.reference} created from this chat.`, "success"); await openChat(chatId); }
            openCase(made.case.id, () => openChat(chatId));
          }, { kind: "secondary" }),
          S.button("End chat", async () => { await API(`/chats/${chatId}/end`, { method: "POST", body: {} }); openChat(chatId); renderList(); }, { kind: "ghost" }))),
        ended && data.chat.case_id ? S.button("Open its case", () => openCase(data.chat.case_id, () => {}), { kind: "secondary" }) : null,
      ].filter(Boolean));
      renderList();
    }
    body.append(...[share, overview.chat_online ? null : h("div", { class: "cloud-banner warning", role: "status" }, h("strong", { text: "Live chat is off" }), h("span", { text: "Visitors see your away message and can leave a message instead." }), h("a", { class: "btn secondary sm", href: "#/service?tab=settings", text: "Settings" })),
      layout].filter(Boolean));
    await renderList();
    const stop = S.live(async () => {
      if (!list.isConnected) { stop(); return; } // another tab replaced this one
      await renderList();
      if (!current) return;
      const data = await API(`/chats/${current}?after=${cursor}`, { silent: true });
      if (data.messages.length) { addMessages(data.messages); cursor = data.cursor; }
    }, 3000);
  }

  // ---------------------------------------------------------------- help centre
  async function helpTab(body, wanted) {
    const [data, overview] = await Promise.all([API("/articles"), API("/overview")]);
    const names = Object.fromEntries(data.categories.map((c) => [c.id, c.name]));
    const editArticle = (article = {}) => S.edit({
      title: article.id ? "Edit article" : "New article",
      wide: true,
      fields: [
        { name: "title", label: "Title", required: true, placeholder: "How do I change my booking?" },
        { name: "category_id", label: "Category", type: "select", options: [["", "No category"], ...data.categories.map((c) => [c.id, c.name])] },
        { name: "body", label: "Answer", type: "textarea", rows: 12, hint: "Plain words. Leave an empty line between paragraphs." },
        { name: "status", label: "Visibility", type: "select", options: [["draft", "Draft (only your team)"], ["published", "Published (customers see it)"]] },
      ],
      values: { status: "draft", ...article },
      onSave: async (values) => { await API(article.id ? `/articles/${article.id}` : "/articles", { method: article.id ? "PATCH" : "POST", body: values }); Aira.toast(values.status === "published" ? "Published." : "Saved as a draft.", "success"); Aira.router.render(); },
    });
    const editCategory = (category = {}) => S.edit({
      title: category.id ? "Rename category" : "New category",
      fields: [{ name: "name", label: "Name", required: true, placeholder: "Orders and delivery" }, { name: "position", label: "Order on the page", type: "number", min: 0, max: 999, hint: "Lower numbers come first." }],
      values: { position: 0, ...category },
      onSave: async (values) => { await API(category.id ? `/categories/${category.id}` : "/categories", { method: category.id ? "PATCH" : "POST", body: values }); Aira.router.render(); },
    });
    body.append(...[
      S.share({
        title: "Help centre",
        text: "Customers search your answers here, ask for paid help, and can contact you when they still need help.",
        link: overview.help_link,
        onCreate: async () => (await API("/help/link", { method: "POST", body: {} })).link,
        onRevoke: async () => { await API("/help/link", { method: "DELETE" }); },
      }),
      S.section("Articles", h("div", { class: "btn-row" }, S.button("New article", () => editArticle()), S.button("New category", () => editCategory(), { kind: "secondary" })),
        h("div", { class: "sv-articles" }, S.table({
          caption: "Articles",
          columns: [
            { key: "title", label: "Title", render: (a) => h("strong", { text: a.title }) },
            { key: "category", label: "Category", render: (a) => names[a.category_id] || "-" },
            { key: "status", label: "Status", render: (a) => S.pill(a.status) },
            { key: "views", label: "Views", num: true },
            { key: "helpful", label: "Helpful", num: true, render: (a) => (a.helpful_yes + a.helpful_no ? `${Math.round((a.helpful_yes * 100) / (a.helpful_yes + a.helpful_no))}% of ${a.helpful_yes + a.helpful_no}` : "-") },
            { key: "actions", label: "", render: (a) => S.button("Delete", async () => { if (await S.confirmDelete(`"${a.title}"`)) { await API(`/articles/${a.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) },
          ],
          rows: data.articles,
          empty: Aira.ui.empty("No articles yet", "Write the answer to the question customers ask most. Publish it and share your help centre."),
          onRow: rowOpens((a) => editArticle(a)),
        }))),
      data.categories.length ? S.section("Categories", h("ul", { class: "sv-categories" }, ...data.categories.map((c) => h("li", {}, h("strong", { text: c.name }),
        h("span", { class: "btn-row" }, S.button("Rename", () => editCategory(c), { kind: "ghost sm" }), S.button("Delete", async () => { if (await S.confirmDelete(c.name)) { await API(`/categories/${c.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" })))))) : null,
    ].filter(Boolean));
    const found = wanted && wanted.open ? data.articles.find((a) => a.id === wanted.open) : null;
    if (wanted && wanted.make === "article") editArticle();
    else if (found) editArticle(found);
  }

  // ---------------------------------------------------------------- paid help
  async function paidTab(body, wanted) {
    const data = await API("/offers");
    const edit = (offer = {}) => S.edit({
      title: offer.id ? "Edit paid service" : "New paid service",
      fields: [
        { name: "title", label: "Name", required: true, placeholder: "One-hour setup call" },
        { name: "description", label: "What the customer gets", type: "textarea", rows: 3, placeholder: "We set everything up with you on a video call." },
        { name: "price_cents", label: "Price", type: "money", required: true },
        { name: "active", label: "Show it in the help centre", type: "checkbox" },
      ],
      values: { active: true, ...offer },
      onSave: async (values) => { await API(offer.id ? `/offers/${offer.id}` : "/offers", { method: offer.id ? "PATCH" : "POST", body: values }); Aira.toast("Saved.", "success"); Aira.router.render(); },
    });
    const markPaid = (charge) => S.button("Mark paid", async () => { await API(`/charges/${charge.id}/paid`, { method: "POST", body: { paid: true } }); Aira.toast("Payment recorded. It counts in your revenue.", "success"); Aira.router.render(); }, { kind: "secondary sm" });
    const caseCell = (x) => h("span", { class: "sv-person" }, h("strong", { text: `${x.reference} ${x.subject}` }), h("span", { class: "muted small", translate: "no", text: x.customer_name || x.customer_email }));
    const earned = data.earned;
    body.append(...[
      h("p", { class: "muted", text: "Sell the help that is worth paying for: a setup call, a repair, a priority answer. Customers ask for it from your help centre, see the price and pay with your own payment link; you mark the payment received and it counts in your revenue." }),
      data.payment_link ? null : h("div", { class: "cloud-banner warning", role: "status" }, h("strong", { text: "Add your payment link" }), h("span", { text: "Customers need it to pay you. Monosage never takes the money." }), h("a", { class: "btn secondary sm", href: "#/hub?tab=business", text: "Business profile" })),
      data.help_link ? null : h("div", { class: "cloud-banner", role: "status" }, h("strong", { text: "Share your help centre" }), h("span", { text: "Paid services appear there for customers to ask for." }), h("a", { class: "btn secondary sm", href: "#/service?tab=help", text: "Help centre" })),
      S.kpis([
        { label: "Earned this month", value: money(earned.this_month_cents), tone: earned.this_month_cents ? "good" : "" },
        { label: "Waiting to be paid", value: money(earned.due_cents), tone: earned.due_cents ? "warn" : "", hint: `${data.due.length} ${data.due.length === 1 ? "charge" : "charges"}` },
        { label: "Earned in total", value: money(earned.total_cents), hint: `${earned.payments} ${earned.payments === 1 ? "payment" : "payments"}` },
      ]),
      S.section("Paid services", h("div", { class: "btn-row" }, S.button("New paid service", () => edit())),
        S.table({
          caption: "Paid services",
          columns: [
            { key: "title", label: "Name", render: (o) => h("span", { class: "sv-person" }, h("strong", { text: o.title }), o.description ? h("span", { class: "muted small", text: o.description.slice(0, 100) }) : null) },
            { key: "price", label: "Price", num: true, render: (o) => money(o.price_cents) },
            { key: "active", label: "In the help centre", render: (o) => S.pill(o.active ? "shown" : "hidden", { shown: "good" }) },
            { key: "sold", label: "Paid", num: true },
            { key: "earned", label: "Earned", num: true, render: (o) => money(o.earned_cents) },
            { key: "actions", label: "", render: (o) => S.button("Delete", async () => { if (await S.confirmDelete(`"${o.title}"`)) { await API(`/offers/${o.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) },
          ],
          rows: data.offers,
          empty: Aira.ui.empty("No paid services yet", "Add the help you charge for, with a price. It appears in your help centre and you can add it to any case."),
          onRow: rowOpens((o) => edit(o)),
        })),
      S.section("Waiting to be paid", S.table({
        caption: "Charges waiting to be paid",
        columns: [
          { key: "case", label: "Case", render: caseCell },
          { key: "label", label: "For" },
          { key: "amount", label: "Amount", num: true, render: (x) => money(x.amount_cents) },
          { key: "created_at", label: "Added", render: (x) => S.day(x.created_at) },
          { key: "actions", label: "", render: markPaid },
        ],
        rows: data.due,
        empty: Aira.ui.empty("Nothing waiting", "Charges you add to a case, or that customers ask for, wait here until you mark them paid."),
        onRow: rowOpens((x) => openCase(x.case_id, () => Aira.router.render())),
      })),
      data.paid.length ? S.section("Latest payments", S.table({
        caption: "Latest payments",
        columns: [
          { key: "case", label: "Case", render: caseCell },
          { key: "label", label: "For" },
          { key: "amount", label: "Amount", num: true, render: (x) => money(x.amount_cents) },
          { key: "paid_at", label: "Paid", render: (x) => S.day(x.paid_at) },
        ],
        rows: data.paid,
        onRow: rowOpens((x) => openCase(x.case_id, () => Aira.router.render())),
      })) : null,
    ].filter(Boolean));
    if (wanted && wanted.make === "offer") edit();
  }

  // ---------------------------------------------------------------- saved replies
  async function repliesTab(body, wanted) {
    const data = await API("/macros");
    const edit = (macro = {}) => S.edit({
      title: macro.id ? "Edit saved reply" : "New saved reply",
      fields: [
        { name: "title", label: "Name", required: true, placeholder: "Order is on its way" },
        { name: "body", label: "Reply", type: "textarea", rows: 8, required: true, hint: "{customer} becomes the customer's first name and {case} the case number." },
      ],
      values: macro,
      onSave: async (values) => { await API(macro.id ? `/macros/${macro.id}` : "/macros", { method: macro.id ? "PATCH" : "POST", body: values }); Aira.router.render(); },
    });
    body.append(S.section("",
      h("div", { class: "btn-row" }, S.button("New saved reply", () => edit())),
      S.table({
        caption: "Saved replies",
        columns: [
          { key: "title", label: "Name", render: (m) => h("strong", { text: m.title }) },
          { key: "body", label: "Reply", render: (m) => m.body.slice(0, 120) },
          { key: "uses", label: "Used", num: true },
          { key: "actions", label: "", render: (m) => S.button("Delete", async () => { if (await S.confirmDelete(`"${m.title}"`)) { await API(`/macros/${m.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) },
        ],
        rows: data.macros,
        empty: Aira.ui.empty("No saved replies yet", "Save the answers you write again and again. {customer} and {case} fill themselves in."),
        onRow: rowOpens((m) => edit(m)),
      })));
    if (wanted && wanted.make === "reply") edit();
  }

  // ---------------------------------------------------------------- settings
  async function settingsTab(body) {
    const { settings } = await API("/settings");
    const hours = (minutes) => String(Math.round((minutes / 60) * 100) / 100);
    const slaInputs = {};
    const slaRows = PRIORITIES.map(([id, label]) => {
      const first = h("input", { type: "number", min: "0.1", step: "0.5", inputmode: "decimal", value: hours(settings.sla[id].first), "aria-label": `${label}: first reply within (hours)` });
      const resolve = h("input", { type: "number", min: "0.1", step: "0.5", inputmode: "decimal", value: hours(settings.sla[id].resolve), "aria-label": `${label}: solved within (hours)` });
      slaInputs[id] = { first, resolve, label };
      return h("tr", {}, h("th", { scope: "row", text: label }), h("td", {}, first), h("td", {}, resolve));
    });
    const form = S.form([
      { type: "heading", label: "Live chat" },
      { name: "chat_enabled", label: "Live chat is on", type: "checkbox" },
      { name: "chat_in_hours_only", label: "Only during business hours", type: "checkbox" },
      { name: "offline_message", label: "Away message", type: "textarea", rows: 2 },
      { type: "heading", label: "Business hours" },
      { name: "business_hours", label: "Count due times only during business hours", type: "checkbox" },
      { name: "start", label: "Opens at", type: "time" },
      { name: "end", label: "Closes at", type: "time" },
      ...DAYS.map((label, index) => ({ name: `day_${index}`, label, type: "checkbox" })),
      { type: "heading", label: "Closing" },
      { name: "auto_close_days", label: "Close solved cases after (days without a reply)", type: "number", min: 1, max: 60 },
    ], { ...settings, ...Object.fromEntries(DAYS.map((_, index) => [`day_${index}`, settings.days.includes(index)])) });
    body.append(
      S.section("Due times", h("p", { class: "muted", text: "How fast you aim to answer and solve, by priority, in hours (0.5 is half an hour). Cases show when they are due and turn overdue after it." }),
        h("div", { class: "table-wrap" }, h("table", { class: "table sv-sla" }, h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Priority" }), h("th", { scope: "col", text: "First reply within (hours)" }), h("th", { scope: "col", text: "Solved within (hours)" }))), h("tbody", {}, ...slaRows)))),
      S.section("Settings", form.node, h("div", { class: "btn-row" }, S.button("Save settings", async () => {
        form.setError("");
        const values = form.read();
        const sla = {};
        for (const [id, { first, resolve, label }] of Object.entries(slaInputs)) {
          const a = Number(first.value), b = Number(resolve.value);
          if (!first.value || !Number.isFinite(a) || a < 0.1) { form.setError(`Enter the ${label.toLowerCase()} first reply time in hours, at least 0.1.`); first.focus(); return; }
          if (!resolve.value || !Number.isFinite(b) || b < 0.1) { form.setError(`Enter the ${label.toLowerCase()} solve time in hours, at least 0.1.`); resolve.focus(); return; }
          if (b < a) { form.setError(`${label}: the solve time cannot be shorter than the first reply time.`); resolve.focus(); return; }
          sla[id] = { first: Math.max(5, Math.round(a * 60)), resolve: Math.max(5, Math.round(b * 60)) };
        }
        const days = DAYS.map((_, index) => (values[`day_${index}`] ? index : null)).filter((d) => d !== null);
        try {
          await API("/settings", { method: "POST", body: { sla, days, chat_enabled: values.chat_enabled, chat_in_hours_only: values.chat_in_hours_only, offline_message: values.offline_message, business_hours: values.business_hours, start: values.start, end: values.end, auto_close_days: values.auto_close_days } });
          Aira.toast("Saved.", "success");
        } catch (error) { form.setError(error.message); }
      }))),
    );
  }

  // ---------------------------------------------------------------- reports
  async function reportsTab(body) {
    const data = await API("/reports");
    const s = data.stats;
    const r = data.reports;
    const onTime = (value) => (value === null || value === undefined ? "Average" : `Average · ${value}% on time`);
    body.append(...[
      S.kpis([
        { label: "Open cases", value: s.open, hint: `${s.unassigned} unassigned` },
        { label: "Overdue", value: s.overdue, tone: s.overdue ? "bad" : "good" },
        { label: "First reply", value: duration(s.first_response_minutes), hint: onTime(r.sla.first_reply_percent) },
        { label: "Time to solve", value: duration(s.resolution_minutes), hint: onTime(r.sla.solution_percent) },
        { label: "Satisfaction", value: s.csat_average === null ? "-" : `${s.csat_average} / 5`, hint: `${s.csat_count} ${s.csat_count === 1 ? "rating" : "ratings"}`, tone: (s.csat_average || 0) >= 4 ? "good" : "" },
        { label: "Solved this week", value: s.solved_this_week, hint: `${s.new_this_week} new this week` },
        { label: "Paid help this month", value: money(r.money.this_month_cents), hint: r.money.due_cents ? `${money(r.money.due_cents)} waiting` : "", tone: r.money.this_month_cents ? "good" : "" },
      ]),
      S.section("Your team", S.table({
        caption: "Your team",
        columns: [
          { key: "assignee", label: "Team member", render: (m) => (m.assignee ? h("strong", { translate: "no", text: m.assignee }) : h("span", { class: "muted", text: "Not assigned" })) },
          { key: "open", label: "Open", num: true },
          { key: "solved", label: "Solved", num: true },
          { key: "first", label: "First reply", num: true, render: (m) => duration(m.first_response_minutes) },
          { key: "rating", label: "Rating", num: true, render: (m) => (m.csat_average === null ? "-" : `${m.csat_average} / 5 (${m.ratings})`) },
        ],
        rows: r.team,
        empty: Aira.ui.empty("No cases yet", "Assign cases to people to see how each one is doing."),
      })),
      S.grid(
        S.section("Latest ratings", r.ratings.length ? h("ul", { class: "sv-ratings" }, ...r.ratings.map((x) => h("li", {},
          h("button", { type: "button", class: "sv-link", onclick: () => openCase(x.id, () => Aira.router.render()) }, h("span", { class: "sv-stars", "aria-label": `${x.score} out of 5`, text: stars(x.score) }), h("strong", { text: ` ${x.reference}` }), h("span", { translate: "no", text: ` ${x.customer || ""}` })),
          x.comment ? h("p", { class: "sv-quote", translate: "no", text: x.comment }) : null))) : Aira.ui.empty("No ratings yet", "Customers rate their request once it is solved.")),
        S.section("What customers ask about", r.topics.length ? S.bars(r.topics.map((t) => ({ label: t.tag, value: t.cases }))) : Aira.ui.empty("No tags yet", "Tag cases (refund, delivery, login) to see what comes up most in the last 30 days.")),
      ),
      S.grid(
        S.section("Cases by channel", Object.keys(s.by_channel).length ? S.bars(Object.entries(s.by_channel).map(([id, value]) => ({ label: CHANNELS[id] || id, value }))) : Aira.ui.empty("No cases yet", "")),
        S.section("Getting started", S.checklist(data.checklist)),
      ),
      r.money.offers.length ? S.section("Paid services sold", S.table({
        caption: "Paid services sold",
        columns: [
          { key: "title", label: "Service", render: (o) => h("strong", { text: o.title }) },
          { key: "sold", label: "Paid", num: true },
          { key: "earned", label: "Earned", num: true, render: (o) => money(o.earned_cents) },
        ],
        rows: r.money.offers,
      })) : null,
    ].filter(Boolean));
  }

  Aira.views.service = async (main, route) => {
    if (route.query.new || route.query.open) {
      const { new: make = "", open = "", ...rest } = route.query;
      pending = { make, open };
      Aira.router.go(["service"], rest, { replace: true });
      return;
    }
    const wanted = pending;
    pending = null;
    await S.business();
    const current = TABS.find((item) => item.id === route.query.tab) || TABS[0];
    // "+ New" sits above the left menu; on a phone that menu is folded away, so the page keeps its own New case.
    const phoneNew = ["inbox", "customers"].includes(current.id) && canCreate() ? S.button("New case", () => newCase((c) => { Aira.router.render(); openCase(c.id, () => Aira.router.render()); }), { icon: "plus" }) : null;
    if (phoneNew) phoneNew.classList.add("sv-phone-new");
    const body = S.page(main, { title: current.title, subtitle: current.intro, actions: [phoneNew] });
    const tab = S.tabs(body, route, "service", TABS);
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(4));
    body.append(panel);
    const holder = h("div", { class: "suite-body sv-app" });
    try {
      if (tab === "customers") await customersTab(holder, route, wanted);
      else if (tab === "chat") await chatTab(holder);
      else if (tab === "help") await helpTab(holder, wanted);
      else if (tab === "paid") await paidTab(holder, wanted);
      else if (tab === "replies") await repliesTab(holder, wanted);
      else if (tab === "settings") await settingsTab(holder);
      else if (tab === "reports") await reportsTab(holder);
      else await inbox(holder, route, wanted);
      panel.replaceChildren(holder);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
  };

  // ---------------------------------------------------------------- the Aira frame: "+ New" and the search
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  Aira.appNew.service = () => (canCreate() ? [
    { label: "New case", icon: "applications", href: "#/service?tab=inbox&new=case" },
    { label: "New help article", icon: "learn", href: "#/service?tab=help&new=article" },
    { label: "New paid service", icon: "shop", href: "#/service?tab=paid&new=offer" },
    { label: "New saved reply", icon: "documents", href: "#/service?tab=replies&new=reply" },
  ] : []);

  // A few seconds of memory per request, so typing "par", "parc", "parce" does not ask for the articles each time.
  const recent = new Map();
  const cached = (path, seconds) => {
    const hit = recent.get(path);
    if (hit && Date.now() - hit.at < seconds * 1000) return hit.promise;
    const promise = API(path, { silent: true });
    recent.set(path, { at: Date.now(), promise });
    promise.catch(() => recent.delete(path));
    return promise;
  };
  const fold = (text) => String(text || "").toLowerCase();
  Aira.appSearch.service = {
    placeholder: "Search cases, customers and help articles",
    // Two requests: the cases that match (the server searches subject, customer, phone and number) and the help
    // articles (the whole list, kept for half a minute). Customers come from the matching cases.
    async suggest(query) {
      if (!grownUp()) return [];
      const q = fold(query.trim());
      const [found, help] = await Promise.all([
        cached(`/cases?${new URLSearchParams({ view: "all", q: query.trim() })}`, 5).catch(() => ({ cases: [] })),
        cached("/articles", 30).catch(() => ({ articles: [] })),
      ]);
      const people = new Map();
      for (const c of found.cases || []) {
        const key = customerKey(c);
        const said = [c.customer_name, c.customer_email, c.customer_phone].filter(Boolean);
        if (!key || people.has(key) || !said.some((value) => fold(value).includes(q))) continue;
        people.set(key, {
          label: c.customer_name || c.customer_email || c.customer_phone,
          sub: ["Customer", c.customer_name ? c.customer_email || c.customer_phone : ""].filter(Boolean).join(" · "),
          icon: "recruiters",
          href: `#/service?${new URLSearchParams({ tab: "customers", q: c.customer_email || c.customer_name || c.customer_phone, open: key })}`,
        });
      }
      const cases = (found.cases || []).slice(0, 4).map((c) => ({
        label: c.subject,
        sub: [c.reference, STATUS_LABEL[c.status] || c.status, c.customer_name || c.customer_email || c.customer_phone].filter(Boolean).join(" · "),
        icon: "applications",
        href: `#/service?${new URLSearchParams({ tab: "inbox", view: "all", open: c.id })}`,
      }));
      const articles = (help.articles || []).filter((a) => fold(a.title).includes(q)).slice(0, 2).map((a) => ({
        label: a.title,
        sub: `Help article · ${a.status === "published" ? "Published" : "Draft"}`,
        icon: "learn",
        href: `#/service?${new URLSearchParams({ tab: "help", open: a.id })}`,
      }));
      return [...[...people.values()].slice(0, 2), ...cases, ...articles].slice(0, 6);
    },
    // Enter: every case that matches, in the inbox's own search.
    run(query) {
      const q = query.trim();
      if (q) Aira.router.go(["service"], { tab: "inbox", view: "all", q });
    },
  };
})();

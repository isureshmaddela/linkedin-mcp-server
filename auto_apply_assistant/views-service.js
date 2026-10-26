/* Aira — Service (#/service): help your customers fast.
 *
 * Tabs (?tab=): inbox (cases by view, a drawer per case with the conversation, the reply box, saved replies and an
 * AI suggestion), chat (live chats from the public chat page, answered live), help (the help centre: categories and
 * articles, and its public link), replies (saved replies with {customer} and {case}), settings (due times, business
 * hours, live chat) and reports. Everything runs through /api/service/* (aira/suite/service/service_routes.py).
 * DOM is built with h() and text only; nothing a customer wrote is parsed as HTML.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const API = (path, opts) => S.api("service", path, opts);

  const TABS = [
    { id: "inbox", label: "Inbox" },
    { id: "chat", label: "Live chat" },
    { id: "help", label: "Help centre" },
    { id: "replies", label: "Saved replies" },
    { id: "settings", label: "Settings" },
    { id: "reports", label: "Reports" },
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
  const CHANNELS = { web: "Web form", chat: "Live chat", email: "Email", phone: "Phone", manual: "Entered by hand" };
  const TONES = { urgent: "bad", high: "warn", normal: "", low: "", new: "info", open: "warn", waiting: "", solved: "good", closed: "" };
  const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  const pill = (value) => S.pill(value, TONES);
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

  // ---------------------------------------------------------------- inbox
  async function inbox(body, route) {
    const view = route.query.view || "open";
    const filters = { q: route.query.q || "", priority: route.query.priority || "", channel: route.query.channel || "" };
    const params = new URLSearchParams({ view, ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) });
    const data = await API(`/cases?${params}`);
    const go = (changes) => Aira.router.go(["service"], { tab: "inbox", view, ...filters, ...changes });
    const chips = h("div", { class: "btn-row", role: "toolbar", "aria-label": "Views" }, ...VIEWS.map(([id, label]) => {
      const count = data.views[id];
      return h("button", { type: "button", class: `btn sm ${id === view ? "" : "secondary"}`, "aria-pressed": String(id === view), onclick: () => go({ view: id }) }, h("span", { text: label }), count !== undefined ? h("span", { class: "muted", text: ` ${count}` }) : null);
    }));
    const search = h("input", { type: "search", "data-search": "", placeholder: "Search by subject, customer or number", value: filters.q, "aria-label": "Search cases" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    const priority = Aira.ui.select([["", "Any priority"], ...PRIORITIES], filters.priority, { "aria-label": "Priority" });
    priority.addEventListener("change", () => go({ priority: priority.value }));
    const channel = Aira.ui.select([["", "Any channel"], ...Object.entries(CHANNELS)], filters.channel, { "aria-label": "Channel" });
    channel.addEventListener("change", () => go({ channel: channel.value }));
    body.append(
      chips,
      h("div", { class: "btn-row" }, search, priority, channel,
        S.button("Download CSV", () => Aira.download(`/api/service/cases?${new URLSearchParams({ view, format: "csv" })}`, "cases.csv"), { kind: "ghost sm" })),
      S.table({
        columns: [
          { key: "reference", label: "Case" },
          { key: "subject", label: "Subject", render: (c) => h("span", {}, h("strong", { text: c.subject }), c.tags.length ? h("span", { class: "muted small", text: ` ${c.tags.join(", ")}` }) : null) },
          { key: "customer", label: "Customer", render: (c) => c.customer_name || c.customer_email || "-" },
          { key: "priority", label: "Priority", render: (c) => pill(c.priority) },
          { key: "status", label: "Status", render: (c) => pill(c.status) },
          { key: "due", label: "Due", render: (c) => { const text = dueText(c); return text === "Overdue" ? S.pill("overdue", { overdue: "bad" }) : text; } },
          { key: "assignee", label: "Assigned to", render: (c) => c.assignee || "-" },
        ],
        rows: data.cases,
        empty: Aira.ui.empty("No cases here", view === "open" ? "When customers write through your help centre or chat, their cases appear here." : "Try another view."),
        onRow: (c) => openCase(c.id, () => Aira.router.render()),
      }),
    );
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

  async function openCase(caseId, changed) {
    let detail;
    try { detail = await API(`/cases/${caseId}`); } catch (error) { Aira.reportError(error); return; }
    const [macros, me] = await Promise.all([API("/macros", { silent: true }).catch(() => ({ macros: [] })), API("/overview", { silent: true }).then((o) => o.me).catch(() => "")]);
    const c = detail.case;
    const closed = c.status === "closed";
    const thread = h("ol", { class: "sv-thread", "aria-label": "Conversation" }, ...detail.messages.map((m) => h("li", { class: `sv-msg ${m.author_kind}${m.internal ? " internal" : ""}` },
      h("div", { class: "sv-msg-head" }, h("strong", { translate: "no", text: m.author_kind === "customer" ? (m.author_name || "Customer") : m.author_name || (m.author_kind === "system" ? "Aira" : "You") }),
        m.internal && m.author_kind !== "system" ? S.pill("internal note", { "internal note": "warn" }) : null,
        h("span", { class: "muted small", text: S.when(m.created_at) })),
      h("p", { class: "sv-msg-body", translate: "no", text: m.body }),
      ...(m.attachments || []).map((link) => h("a", { href: link, target: "_blank", rel: "noopener noreferrer", class: "small", text: link })))));

    const status = Aira.ui.select(STATUSES, c.status, { "aria-label": "Status", disabled: closed });
    const priority = Aira.ui.select(PRIORITIES, c.priority, { "aria-label": "Priority", disabled: closed });
    const assignee = h("input", { type: "text", value: c.assignee || "", placeholder: "Unassigned", "aria-label": "Assigned to" });
    const tags = h("input", { type: "text", value: c.tags.join(", "), "aria-label": "Tags" });
    const save = async (changes) => {
      try { await API(`/cases/${caseId}`, { method: "PATCH", body: changes }); Aira.toast("Saved.", "success"); changed(); openCase(caseId, changed); } catch (error) { Aira.reportError(error); }
    };

    const text = h("textarea", { rows: 5, placeholder: "Write your reply to the customer", "aria-label": "Reply" });
    const internal = h("input", { type: "checkbox" });
    const files = h("input", { type: "text", placeholder: "https:// links to files (optional)", "aria-label": "Attachment links" });
    const after = Aira.ui.select([["waiting", "Then wait for the customer"], ["open", "Keep it open"], ["solved", "Mark it solved"]], "waiting", { "aria-label": "After sending" });
    const saved = Aira.ui.select([["", "Insert a saved reply"], ...macros.macros.map((m) => [m.id, m.title])], "", { "aria-label": "Saved replies" });
    saved.addEventListener("change", async () => {
      if (!saved.value) return;
      try { const filled = await API(`/macros/${saved.value}/use`, { method: "POST", body: { case_id: caseId } }); text.value = text.value ? `${text.value}\n\n${filled.text}` : filled.text; text.focus(); } catch (error) { Aira.reportError(error); }
      saved.value = "";
    });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const send = S.button("Send", async () => {
      error.hidden = true;
      try {
        await API(`/cases/${caseId}/reply`, { method: "POST", body: { body: text.value, internal: internal.checked, attachments: files.value.split(/\s+/).filter(Boolean), status_after: after.value } });
        Aira.toast(internal.checked ? "Note added." : "Reply ready in the outbox.", "success");
        changed();
        openCase(caseId, changed);
      } catch (err) { error.textContent = err.message; error.hidden = false; }
    });
    const suggest = S.button("Suggest a reply", async () => {
      const data = await API(`/cases/${caseId}/suggest`, { method: "POST", body: {} });
      text.value = data.reply;
      text.focus();
      if (data.source === "template") Aira.toast("A starting reply from a template. Edit it before sending.");
    }, { kind: "ghost sm" });
    internal.addEventListener("change", () => { send.querySelector("span").textContent = internal.checked ? "Add note" : "Send"; after.disabled = internal.checked; });

    const body = h("div", { class: "suite-body" },
      Aira.ui.kv([
        ["Customer", h("span", { translate: "no", text: [c.customer_name, c.customer_email, c.customer_phone].filter(Boolean).join(" · ") || "-" })],
        ["Channel", CHANNELS[c.channel] || c.channel],
        ["Opened", S.when(c.created_at)],
        ["First reply", c.first_responded_at ? S.when(c.first_responded_at) : dueText(c) ? `Due ${S.when(c.first_response_due)}` : "-"],
        ["Solution due", S.when(c.resolution_due)],
        ["Rating", c.csat_score ? `${c.csat_score} out of 5${c.csat_comment ? `: ${c.csat_comment}` : ""}` : ""],
      ]),
      h("div", { class: "suite-grid" },
        Aira.ui.field("Status", status), Aira.ui.field("Priority", priority),
        Aira.ui.field("Assigned to", assignee, me ? "Your name or email" : ""), Aira.ui.field("Tags", tags)),
      closed ? null : h("div", { class: "btn-row" },
        S.button("Save changes", () => save({ status: status.value, priority: priority.value, assignee: assignee.value, tags: tags.value }), { kind: "secondary" }),
        me ? S.button("Assign to me", () => save({ assignee: me }), { kind: "ghost sm" }) : null,
        S.button("Copy the customer's link", async () => { const made = await API(`/cases/${caseId}/link`, { method: "POST", body: {} }); await S.copy(made.url); }, { kind: "ghost sm" }),
        S.button("Merge into another case", () => S.edit({ title: "Merge this case", fields: [{ name: "number", label: "The case number to keep (for example 1001)", required: true }], onSave: async (values) => {
          const wanted = String(values.number || "").replace(/\D/g, "");
          const found = await API(`/cases?view=all&q=${encodeURIComponent(wanted)}`);
          const target = found.cases.find((x) => String(x.number) === wanted);
          if (!target) throw new Error("No case has that number.");
          await API(`/cases/${caseId}/merge`, { method: "POST", body: { into: target.id } });
          Aira.toast(`Merged into ${target.reference}.`, "success");
          changed();
          openCase(target.id, changed);
        } }), { kind: "ghost sm" })),
      S.section("Conversation", thread),
      closed ? h("p", { class: "muted", text: c.merged_into ? "This case was merged into another case." : "This case is closed." }) : S.section("Reply",
        h("div", { class: "btn-row" }, saved, suggest),
        text,
        h("label", { class: "suite-check" }, internal, h("span", { text: "Internal note: only your team sees it" })),
        files, after, error, h("div", { class: "btn-row" }, send)),
    );
    Aira.drawer({ title: `${c.reference} · ${c.subject}`, subtitle: [pill(c.status), " ", pill(c.priority)], body });
  }

  // ---------------------------------------------------------------- live chat
  async function chatTab(body) {
    const overview = await API("/overview");
    let current = null;
    let cursor = 0;
    const list = h("div", { class: "sv-chat-list", role: "list" });
    const pane = h("div", { class: "sv-chat-pane" }, Aira.ui.empty("Choose a chat", "Chats from your live chat page appear here."));
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
      list.replaceChildren(...(data.chats.length ? data.chats.map((chat) => h("button", { type: "button", role: "listitem", class: `sv-chat-item ${current === chat.id ? "active" : ""}`, onclick: () => openChat(chat.id) },
        h("strong", { translate: "no", text: chat.visitor_name || "Visitor" }), chat.waiting_for_us ? S.pill("waiting", { waiting: "warn" }) : S.pill(chat.status),
        h("span", { class: "muted small", translate: "no", text: chat.last_message }))) : [h("p", { class: "muted", text: data.online ? "No chats right now." : "Live chat is off. Turn it on under Settings." })]));
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
      pane.replaceChildren(
        h("div", { class: "page-head" }, h("div", {}, h("h2", { translate: "no", text: data.chat.visitor_name || "Visitor" }), h("p", { class: "muted", translate: "no", text: data.chat.visitor_email || "" }))),
        messages,
        ended ? h("p", { class: "muted", text: "This chat has ended." }) : h("div", { class: "suite-body" }, text, h("div", { class: "btn-row" },
          S.button("Send", sendIt),
          S.button(data.chat.case_id ? "Open its case" : "Make it a case", async () => { const made = await API(`/chats/${chatId}/case`, { method: "POST", body: {} }); openCase(made.case.id, () => {}); }, { kind: "secondary" }),
          S.button("End chat", async () => { await API(`/chats/${chatId}/end`, { method: "POST", body: {} }); openChat(chatId); renderList(); }, { kind: "ghost" }))),
      );
      renderList();
    }
    body.append(share, overview.chat_online ? null : h("div", { class: "cloud-banner warning", role: "status" }, h("strong", { text: "Live chat is off" }), h("span", { text: "Visitors see your away message and can leave a message instead." }), h("a", { class: "btn secondary sm", href: "#/service?tab=settings", text: "Settings" })),
      h("div", { class: "sv-chat" }, list, pane));
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
  async function helpTab(body) {
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
      onSave: async (values) => { await API(article.id ? `/articles/${article.id}` : "/articles", { method: article.id ? "PATCH" : "POST", body: values }); Aira.router.render(); },
    });
    body.append(
      S.share({
        title: "Help centre",
        text: "Customers search your answers here and can contact you when they still need help.",
        link: overview.help_link,
        onCreate: async () => (await API("/help/link", { method: "POST", body: {} })).link,
        onRevoke: async () => { await API("/help/link", { method: "DELETE" }); },
      }),
      S.section("Articles", h("div", { class: "btn-row" }, S.button("New article", () => editArticle()), S.button("New category", () => S.edit({ title: "New category", fields: [{ name: "name", label: "Name", required: true, placeholder: "Orders and delivery" }], onSave: async (values) => { await API("/categories", { method: "POST", body: values }); Aira.router.render(); } }), { kind: "secondary" })),
        S.table({
          columns: [
            { key: "title", label: "Title", render: (a) => h("strong", { text: a.title }) },
            { key: "category", label: "Category", render: (a) => names[a.category_id] || "-" },
            { key: "status", label: "Status", render: (a) => S.pill(a.status) },
            { key: "views", label: "Views", num: true },
            { key: "helpful", label: "Helpful", num: true, render: (a) => `${a.helpful_yes} / ${a.helpful_yes + a.helpful_no}` },
            { key: "actions", label: "", render: (a) => S.button("Delete", async () => { if (await S.confirmDelete(`"${a.title}"`)) { await API(`/articles/${a.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) },
          ],
          rows: data.articles,
          empty: Aira.ui.empty("No articles yet", "Write the answer to the question customers ask most. Publish it and share your help centre."),
          onRow: (a) => editArticle(a),
        })),
      data.categories.length ? S.section("Categories", h("ul", { class: "suite-activity" }, ...data.categories.map((c) => h("li", {}, h("strong", { text: c.name }), S.button("Delete", async () => { if (await S.confirmDelete(c.name)) { await API(`/categories/${c.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }))))) : null,
    );
  }

  // ---------------------------------------------------------------- saved replies
  async function repliesTab(body) {
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
    body.append(S.section("Saved replies", h("p", { class: "muted", text: "Answers you send often. Insert one from the reply box of any case." }),
      h("div", { class: "btn-row" }, S.button("New saved reply", () => edit())),
      S.table({
        columns: [
          { key: "title", label: "Name", render: (m) => h("strong", { text: m.title }) },
          { key: "body", label: "Reply", render: (m) => m.body.slice(0, 120) },
          { key: "uses", label: "Used", num: true },
          { key: "actions", label: "", render: (m) => S.button("Delete", async () => { if (await S.confirmDelete(`"${m.title}"`)) { await API(`/macros/${m.id}`, { method: "DELETE" }); Aira.router.render(); } }, { kind: "ghost sm" }) },
        ],
        rows: data.macros,
        empty: Aira.ui.empty("No saved replies yet", "Save the answers you write again and again."),
        onRow: (m) => edit(m),
      })));
  }

  // ---------------------------------------------------------------- settings
  async function settingsTab(body) {
    const { settings } = await API("/settings");
    const hours = (minutes) => String(Math.round((minutes / 60) * 10) / 10);
    const slaInputs = {};
    const slaRows = PRIORITIES.map(([id, label]) => {
      const first = h("input", { type: "number", min: "0.1", step: "0.5", value: hours(settings.sla[id].first), "aria-label": `${label}: first reply within (hours)` });
      const resolve = h("input", { type: "number", min: "0.1", step: "0.5", value: hours(settings.sla[id].resolve), "aria-label": `${label}: solved within (hours)` });
      slaInputs[id] = { first, resolve };
      return h("tr", {}, h("td", { text: label }), h("td", {}, first), h("td", {}, resolve));
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
      S.section("Due times", h("p", { class: "muted", text: "How fast you aim to answer and solve, by priority. Cases show when they are due and turn overdue after it." }),
        h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Priority" }), h("th", { text: "First reply within (hours)" }), h("th", { text: "Solved within (hours)" }))), h("tbody", {}, ...slaRows)))),
      S.section("Settings", form.node, h("div", { class: "btn-row" }, S.button("Save", async () => {
        form.setError("");
        const values = form.read();
        const sla = Object.fromEntries(Object.entries(slaInputs).map(([id, { first, resolve }]) => [id, { first: Math.round(Number(first.value) * 60), resolve: Math.round(Number(resolve.value) * 60) }]));
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
    const data = await API("/overview");
    const s = data.stats;
    body.append(
      S.kpis([
        { label: "Open cases", value: s.open },
        { label: "Overdue", value: s.overdue, tone: s.overdue ? "bad" : "good" },
        { label: "First reply", value: duration(s.first_response_minutes), hint: "Average time" },
        { label: "Time to solve", value: duration(s.resolution_minutes), hint: "Average time" },
        { label: "Satisfaction", value: s.csat_average === null ? "-" : `${s.csat_average} / 5`, hint: `${s.csat_count} ratings`, tone: (s.csat_average || 0) >= 4 ? "good" : "" },
        { label: "Solved this week", value: s.solved_this_week, hint: `${s.new_this_week} new this week` },
      ]),
      S.grid(
        S.section("Cases by channel", Object.keys(s.by_channel).length ? S.bars(Object.entries(s.by_channel).map(([id, value]) => ({ label: CHANNELS[id] || id, value }))) : Aira.ui.empty("No cases yet", "")),
        S.section("Getting started", S.checklist(data.checklist)),
      ),
    );
  }

  Aira.views.service = async (main, route) => {
    const body = S.page(main, {
      title: "Service",
      subtitle: "Every customer question in one place: cases, live chat and your help centre.",
      actions: [S.button("New case", () => newCase((c) => openCase(c.id, () => Aira.router.render())))],
    });
    const tab = S.tabs(body, route, "service", TABS);
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(4));
    body.append(panel);
    const holder = h("div", { class: "suite-body" });
    try {
      if (tab === "chat") await chatTab(holder);
      else if (tab === "help") await helpTab(holder);
      else if (tab === "replies") await repliesTab(holder);
      else if (tab === "settings") await settingsTab(holder);
      else if (tab === "reports") await reportsTab(holder);
      else await inbox(holder, route);
      panel.replaceChildren(holder);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
  };

  // Styles for the conversation and the chat layout (the kit has none for these).
  if (!document.getElementById("svStyles")) {
    const style = document.createElement("style");
    style.id = "svStyles";
    style.textContent = [
      ".sv-thread{list-style:none;margin:0;padding:0;display:grid;gap:10px;max-height:52vh;overflow:auto}",
      ".sv-msg{padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}",
      ".sv-msg.agent{background:var(--brand-soft);border-color:transparent}",
      ".sv-msg.internal{background:var(--warning-soft);border-style:dashed}",
      ".sv-msg.system{background:transparent;border-style:dashed;color:var(--muted);font-size:13px}",
      ".sv-msg-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}",
      ".sv-msg-body{margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}",
      ".sv-chat{display:grid;grid-template-columns:minmax(200px,280px) minmax(0,1fr);gap:14px;align-items:start}",
      ".sv-chat-list{display:grid;gap:6px}",
      ".sv-chat-item{display:grid;gap:4px;text-align:left;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);font:inherit;cursor:pointer}",
      ".sv-chat-item.active{border-color:var(--brand);background:var(--brand-soft)}",
      ".sv-chat-pane{min-width:0;display:grid;gap:10px}",
      "@media (max-width:760px){.sv-chat{grid-template-columns:1fr}}",
    ].join("\n");
    document.head.append(style);
  }
})();

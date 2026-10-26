/* Aira — Planner (#/planner): running the home together, for every age.
 *
 * Tabs (?tab=): day (my day: today's chores with big Done buttons, overdue, coming up, bills soon, shopping),
 * chores (mine or the family's, with repeats and points), shopping (lists everyone ticks live, by aisle, with the
 * usual items one tap away and a link for relatives), budget and bills (adults), notes (shared or private) and
 * rewards (points from chores, rewards an adult approves). Everything runs through /api/planner/*
 * (aira/suite/planner). DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("planner", path, opts);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const AISLE_NAMES = { "fruit & veg": "Fruit and vegetables", bakery: "Bakery", dairy: "Milk, cheese and eggs", "meat & fish": "Meat and fish", pantry: "Cupboard", frozen: "Frozen", drinks: "Drinks", household: "Household", pharmacy: "Pharmacy", other: "Other" };

  // append() turns null into the text "null": only real nodes go in.
  const put = (node, ...children) => node.append(...children.filter(Boolean));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  function dueLabel(task, today) {
    if (!task.due_date) return "Any time";
    if (task.due_date < today) return `Overdue (was due ${S.day(task.due_date)})`;
    const time = task.due_time ? ` at ${task.due_time}` : "";
    if (task.due_date === today) return `Today${time}`;
    const tomorrow = new Date(`${today}T12:00:00`);
    tomorrow.setDate(tomorrow.getDate() + 1);
    if (task.due_date === tomorrow.toISOString().slice(0, 10)) return `Tomorrow${time}`;
    return `${S.day(task.due_date)}${time}`;
  }

  // ---------------------------------------------------------------- tasks
  function taskRow(task, today, rerender) {
    const done = task.done || task.done_by_me;
    const check = h("button", { type: "button", class: `life-check ${done ? "done" : ""}`, "aria-pressed": String(done), "aria-label": `${done ? "Not done" : "Done"}: ${task.title}`, onclick: async (event) => {
      await Aira.withBusy(event.currentTarget, async () => {
        await api(`/tasks/${task.id}/${done ? "undo" : "done"}`, { method: "POST", body: {} });
        if (!done) Aira.toast(task.points ? `Well done! +${task.points} points` : "Done. Well done!", "success");
        rerender();
        if (Aira.life && Aira.life.tick) Aira.life.tick();
      });
    } }, h("span", { "aria-hidden": "true", text: done ? "✓" : "" }), h("span", { class: "life-check-label", text: done ? "Done" : "Do it" }));
    const sub = [dueLabel(task, today), task.assignee_name !== "Anyone" || task.assignee === "" ? task.assignee_name : "", task.repeat_label, task.points ? `${task.points} points` : "", task.done_by && task.done_by.length && task.assignee === "everyone" ? `Done by ${task.done_by.join(", ")}` : ""].filter(Boolean).join(" · ");
    const actions = task.can_edit ? h("div", { class: "btn-row" },
      S.button("Edit", () => editTask(task, rerender), { kind: "ghost sm" }),
      S.button("Remove", async () => { if (await S.confirmDelete(`"${task.title}"`)) { await api(`/tasks/${task.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })) : null;
    return h("li", { class: `life-line ${done ? "done" : ""}`, style: { flexWrap: "wrap" } }, check,
      h("div", { style: { flex: "1", minWidth: "180px" } }, h("span", { class: "life-line-text", text: task.title }), h("span", { class: "muted life-line-sub", text: sub }), task.notes ? h("span", { class: "muted life-line-sub", text: task.notes }) : null),
      actions);
  }
  const taskList = (tasks, today, rerender, empty) => (tasks.length ? h("ul", { class: "life-lines" }, ...tasks.map((task) => taskRow(task, today, rerender))) : h("p", { class: "muted", text: empty }));

  let cache = { people: [], me: null };
  async function loadBasics() {
    const data = await api("/overview", { silent: true });
    cache = { people: data.people, me: data.me, today: data.today, lists: data.lists, data };
    return data;
  }

  function taskFields(task) {
    const me = cache.me || {};
    const people = me.adult ? cache.people.map((p) => [p.id, `${p.emoji} ${p.name}`]) : [[me.id, `Me (${me.name})`]];
    const assignees = [["", "Anyone"], ...people, ...(me.adult ? [["everyone", "Everyone (each person)"]] : [])];
    const days = (task && task.repeat_days) || [];
    return [
      { name: "title", label: "What needs doing", required: true, placeholder: "Feed the cat" },
      { name: "assignee", label: "Who", type: "select", options: assignees },
      { name: "due_date", label: "Day", type: "date" },
      { name: "due_time", label: "Time (optional, for a reminder)", type: "time" },
      { name: "repeat", label: "Repeat", type: "select", options: [["none", "Does not repeat"], ["daily", "Every day"], ["weekdays", "Every weekday (Mon to Fri)"], ["weekly", "Every week on the days below"], ["monthly", "Every month on the same day"], ["every_n", "Every few days"]] },
      { type: "heading", label: "Days of the week (for weekly)" },
      ...DAYS.map((label, index) => ({ name: `d${index}`, label, type: "checkbox", value: days.includes(index) })),
      { name: "repeat_every", label: "Every how many days (for every few days)", type: "number", min: 1, max: 365 },
      ...(me.adult ? [{ name: "points", label: "Points (for children)", type: "number", min: 0, max: 1000, hint: "Children earn these when it is done and spend them on rewards." }] : []),
      { name: "notes", label: "Notes", type: "textarea", rows: 2 },
    ];
  }
  function taskPayload(values) {
    const payload = { ...values, repeat_days: DAYS.map((_, index) => (values[`d${index}`] ? index : null)).filter((d) => d !== null) };
    DAYS.forEach((_, index) => delete payload[`d${index}`]);
    if (payload.points === null) delete payload.points;
    if (payload.repeat_every === null) delete payload.repeat_every;
    return payload;
  }
  async function newTask(rerender, defaults = {}) {
    if (!cache.me) await loadBasics();
    await S.edit({ title: "New task", fields: taskFields(null), values: { assignee: cache.me.adult ? "" : cache.me.id, due_date: cache.today, repeat: "none", ...defaults }, submitLabel: "Add task", onSave: async (values) => { await api("/tasks", { method: "POST", body: taskPayload(values) }); rerender(); } });
  }
  async function editTask(task, rerender) {
    if (!cache.me) await loadBasics();
    await S.edit({ title: "Edit task", fields: taskFields(task), values: task, onSave: async (values) => { await api(`/tasks/${task.id}`, { method: "PATCH", body: taskPayload(values) }); rerender(); } });
  }

  async function dayTab(body, rerender) {
    const data = await loadBasics();
    const today = data.today;
    const quick = h("input", { type: "text", placeholder: "Add something to do today", "aria-label": "Add something to do today", style: { flex: "1", minWidth: "200px" } });
    const add = async () => { const title = quick.value.trim(); if (!title) { quick.focus(); return; } await api("/tasks", { method: "POST", body: { title, assignee: data.me.id, due_date: today } }); rerender(); };
    quick.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(); } });
    put(body, 
      S.kpis([
        { label: "To do today", value: data.today_tasks.length },
        { label: "Overdue", value: data.overdue.length, tone: data.overdue.length ? "warn" : "" },
        { label: "My points", value: data.points, hint: "From chores", tone: "good" },
        data.me.adult ? { label: "Reward requests", value: data.pending_requests, tone: data.pending_requests ? "warn" : "", hint: data.pending_requests ? "Waiting for you" : "" } : null,
      ]),
      S.section("Today", h("div", { class: "btn-row" }, quick, S.button("Add", add)), taskList(data.today_tasks, today, rerender, "Nothing for today. Enjoy your day!")),
      data.overdue.length ? S.section("Overdue", taskList(data.overdue, today, rerender, "")) : null,
      S.grid(
        S.section("Coming up this week", taskList(data.upcoming, today, rerender, "Nothing planned for the next 7 days.")),
        data.anytime.length ? S.section("Any time", taskList(data.anytime, today, rerender, "")) : null,
        data.me.adult && data.bills_soon.length ? S.section("Bills this week", h("ul", { class: "life-lines" }, ...data.bills_soon.map((bill) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: `${bill.name}${bill.amount_cents ? ` ${S.money(bill.amount_cents)}` : ""}` }), h("span", { class: "muted life-line-sub", text: billWhen(bill) })))))) : null,
        S.section("Shopping", h("ul", { class: "life-lines" }, ...data.lists.map((list) => h("li", { class: "life-line" }, h("a", { href: `#/planner?tab=shopping&list=${list.id}`, class: "life-line-text", text: list.name }), h("span", { class: "muted", text: list.open ? plural(list.open, "item") : "Nothing to buy" }))))),
      ),
    );
  }

  async function choresTab(body, route, rerender) {
    if (!cache.me) await loadBasics();
    const scope = route.query.scope || (cache.me.adult ? "family" : "mine");
    const showDone = route.query.done === "1";
    const data = await api(`/tasks?scope=${scope}&status=${showDone ? "done" : "open"}`);
    const today = data.today;
    const groups = showDone ? [["Done recently", data.tasks]] : [
      ["Overdue", data.tasks.filter((t) => t.overdue)],
      ["Today", data.tasks.filter((t) => t.due_date === today)],
      ["Coming up", data.tasks.filter((t) => t.due_date && t.due_date > today)],
      ["Any time", data.tasks.filter((t) => !t.due_date)],
    ];
    const go = (query) => Aira.router.go(["planner"], { tab: "chores", scope, done: showDone ? "1" : "", ...query });
    put(body, 
      h("div", { class: "btn-row" },
        S.button("New task", () => newTask(rerender)),
        h("button", { type: "button", class: `btn ${scope === "mine" ? "" : "secondary"}`, "aria-pressed": String(scope === "mine"), text: "My tasks", onclick: () => go({ scope: "mine" }) }),
        h("button", { type: "button", class: `btn ${scope === "family" ? "" : "secondary"}`, "aria-pressed": String(scope === "family"), text: cache.me.adult ? "Everyone's tasks" : "Family tasks", onclick: () => go({ scope: "family" }) }),
        h("button", { type: "button", class: "btn ghost", text: showDone ? "Show to do" : "Show done", onclick: () => go({ done: showDone ? "" : "1" }) })),
      ...(data.tasks.length
        ? groups.filter(([, tasks]) => tasks.length).map(([title, tasks]) => S.section(title, taskList(tasks, today, rerender, "")))
        : [Aira.ui.empty(showDone ? "Nothing done yet" : "No tasks", showDone ? "Finished tasks show up here." : "Add one with New task.")]),
    );
  }

  // ---------------------------------------------------------------- shopping
  async function shoppingTab(body, route, rerender) {
    if (!cache.me) await loadBasics();
    const { lists } = await api("/lists");
    const current = lists.find((list) => list.id === route.query.list) || lists[0];
    const detail = await api(`/lists/${current.id}`);
    const items = new Map(detail.items.map((item) => [item.id, item]));
    let since = detail.now;
    const area = h("div", { class: "suite-body" });
    const draw = () => {
      const all = [...items.values()].filter((item) => !item.deleted);
      const open = all.filter((item) => !item.checked);
      const ticked = all.filter((item) => item.checked);
      const byAisle = {};
      for (const item of open) (byAisle[item.category] = byAisle[item.category] || []).push(item);
      const row = (item) => h("li", { class: `life-line ${item.checked ? "done" : ""}` },
        h("button", { type: "button", class: `life-check ${item.checked ? "done" : ""}`, "aria-pressed": String(item.checked), "aria-label": `${item.checked ? "Untick" : "Tick"} ${item.name}`, onclick: async () => {
          const updated = (await api(`/items/${item.id}`, { method: "PATCH", body: { checked: !item.checked } })).item;
          items.set(updated.id, updated);
          draw();
        } }, h("span", { "aria-hidden": "true", text: item.checked ? "✓" : "" }), h("span", { class: "life-check-label", text: item.checked ? "Got it" : "Get" })),
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: `${item.name}${item.quantity ? ` (${item.quantity})` : ""}` }), h("span", { class: "muted life-line-sub", text: item.checked && item.checked_by ? `Ticked by ${item.checked_by}` : item.added_by ? `Added by ${item.added_by}` : "" })),
        h("button", { type: "button", class: "btn ghost sm", "aria-label": `Remove ${item.name}`, text: "Remove", onclick: async () => { await api(`/items/${item.id}`, { method: "DELETE" }); items.delete(item.id); draw(); } }));
      area.replaceChildren(
        open.length ? h("div", { class: "suite-body" }, ...Object.keys(AISLE_NAMES).filter((key) => byAisle[key]).map((key) => S.section(AISLE_NAMES[key], h("ul", { class: "life-lines" }, ...byAisle[key].map(row))))) : Aira.ui.empty("Nothing to buy", "Add what you need above, or tap one of your usual items."),
        ticked.length ? S.section(`In the basket (${ticked.length})`, h("ul", { class: "life-lines" }, ...ticked.map(row)), h("div", { class: "btn-row" }, S.button("Clear ticked items", async () => { await api(`/lists/${current.id}/clear`, { method: "POST", body: {} }); for (const item of ticked) items.delete(item.id); draw(); }, { kind: "secondary" }))) : null,
      );
    };
    const name = h("input", { type: "text", placeholder: "Milk", "aria-label": "Item", style: { flex: "2", minWidth: "160px" } });
    const quantity = h("input", { type: "text", placeholder: "How many", "aria-label": "How many", style: { flex: "1", minWidth: "90px" } });
    const add = async (value, qty = "") => {
      const itemName = String(value || "").trim();
      if (!itemName) { name.focus(); return; }
      const item = (await api(`/lists/${current.id}/items`, { method: "POST", body: { name: itemName, quantity: qty } })).item;
      items.set(item.id, item);
      name.value = "";
      quantity.value = "";
      name.focus();
      draw();
    };
    for (const input of [name, quantity]) input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(name.value, quantity.value); } });
    const chips = h("div", { class: "btn-row" }, ...lists.map((list) => h("a", { class: `btn ${list.id === current.id ? "" : "secondary"} sm`, href: `#/planner?tab=shopping&list=${list.id}`, "aria-current": list.id === current.id ? "page" : "false", text: `${list.name}${list.open ? ` (${list.open})` : ""}` })),
      S.button("New list", () => S.edit({ title: "New shopping list", fields: [{ name: "name", label: "Name", required: true, placeholder: "Pharmacy" }], submitLabel: "Create", onSave: async (values) => { const made = (await api("/lists", { method: "POST", body: values })).list; Aira.router.go(["planner"], { tab: "shopping", list: made.id }); } }), { kind: "ghost sm" }),
      lists.length > 1 && cache.me.adult ? S.button("Remove this list", async () => { if (await S.confirmDelete(`the ${current.name} list`)) { await api(`/lists/${current.id}`, { method: "DELETE" }); Aira.router.go(["planner"], { tab: "shopping" }); } }, { kind: "ghost sm" }) : null);
    const usual = detail.usual.length ? S.section("Your usual items", h("p", { class: "muted", text: "Tap to add." }), h("div", { class: "btn-row" }, ...detail.usual.map((item) => S.button(`+ ${item.name}`, () => add(item.name), { kind: "secondary sm" })))) : null;
    put(body, 
      chips,
      S.section(`Add to ${current.name}`, h("div", { class: "btn-row" }, name, quantity, S.button("Add", () => add(name.value, quantity.value)))),
      usual,
      area,
      cache.me.adult ? S.share({ title: "Share this list", text: "A relative can open the link to see the list, tick items and add things, without an account.", link: detail.link, onCreate: async () => (await api(`/lists/${current.id}/link`, { method: "POST", body: {} })).link, onRevoke: async () => api(`/lists/${current.id}/link`, { method: "DELETE" }) }) : null,
      h("p", { class: "muted small", text: "This list updates by itself when someone else ticks or adds something." }),
    );
    draw();
    S.live(async () => {
      const changes = await api(`/lists/${current.id}?since=${encodeURIComponent(since)}`, { silent: true });
      since = changes.now;
      if (!changes.changed.length) return;
      for (const item of changes.changed) { if (item.deleted) items.delete(item.id); else items.set(item.id, item); }
      draw();
    }, 2500);
    void rerender;
  }

  // ---------------------------------------------------------------- budget and bills
  function billWhen(bill) {
    if (bill.status === "settled") return "Paid";
    if (bill.days_until === null) return "";
    if (bill.days_until < 0) return `Overdue since ${S.day(bill.next_due)}`;
    if (bill.days_until === 0) return "Due today";
    if (bill.days_until === 1) return "Due tomorrow";
    return `Due ${S.day(bill.next_due)} (in ${bill.days_until} days)`;
  }

  async function budgetTab(body, route, rerender) {
    const month = route.query.month || "";
    const data = await api(`/budget${month ? `?month=${month}` : ""}`);
    const picker = h("input", { type: "month", value: data.month, "aria-label": "Month" });
    picker.addEventListener("change", () => Aira.router.go(["planner"], { tab: "budget", month: picker.value }));
    const categoryNames = data.categories.map((c) => c.name);
    const addEntry = (kind) => S.edit({ title: kind === "income" ? "Money in" : "Money spent", fields: [
      { name: "amount_cents", label: "Amount", type: "money", required: true },
      { name: "day", label: "Day", type: "date" },
      { name: "category", label: "Category", type: "select", options: kind === "income" ? ["Salary", "Pension", "Benefits", "Gift", "Other"] : categoryNames },
      { name: "note", label: "Note", placeholder: kind === "income" ? "October salary" : "Weekly shop" },
    ], values: { day: new Date().toISOString().slice(0, 10) }, submitLabel: "Add", onSave: async (values) => { await api("/entries", { method: "POST", body: { ...values, kind } }); rerender(); } });
    put(body, 
      h("div", { class: "btn-row" }, Aira.ui.field("Month", picker), S.button("Money spent", () => addEntry("expense")), S.button("Money in", () => addEntry("income"), { kind: "secondary" }), S.button("Download CSV", () => Aira.download(`/api/planner/budget?format=csv&month=${data.month}`, `budget-${data.month}.csv`), { kind: "ghost" })),
      S.kpis([
        { label: "Money in", value: S.money(data.income_cents, data.currency), tone: "good" },
        { label: "Spent", value: S.money(data.expense_cents, data.currency) },
        { label: "Left over", value: S.money(data.net_cents, data.currency), tone: data.net_cents < 0 ? "bad" : "good" },
        { label: "Planned spending", value: S.money(data.planned_cents, data.currency), hint: "Total of the monthly budgets" },
      ]),
      S.grid(
        S.section("By category", S.table({
          columns: [
            { key: "name", label: "Category" },
            { key: "monthly_cents", label: "Budget", num: true, render: (c) => (c.monthly_cents ? S.money(c.monthly_cents, data.currency) : "none") },
            { key: "spent_cents", label: "Spent", num: true, render: (c) => S.money(c.spent_cents, data.currency) },
            { key: "left_cents", label: "Left", num: true, render: (c) => (c.left_cents === null ? "" : h("span", { style: { color: c.over ? "var(--danger)" : "inherit", fontWeight: c.over ? "700" : "400" }, text: c.over ? `${S.money(-c.left_cents, data.currency)} over` : S.money(c.left_cents, data.currency) })) },
          ],
          rows: data.categories,
          onRow: (c) => S.edit({ title: c.id ? `Budget for ${c.name}` : `Add ${c.name} as a category`, fields: [{ name: "name", label: "Name", required: true }, { name: "monthly_cents", label: "Budget each month", type: "money" }], values: c, onSave: async (values) => { await api(c.id ? `/budget/categories/${c.id}` : "/budget/categories", { method: c.id ? "PATCH" : "POST", body: values }); rerender(); } }),
        }), h("p", { class: "muted small", text: "Tap a category to set its monthly budget." })),
        S.section("Where the money went", S.bars(data.categories.filter((c) => c.spent_cents).map((c) => ({ label: c.name, value: c.spent_cents })), { format: (v) => S.money(v, data.currency) })),
      ),
      S.section("Entries this month", S.table({
        columns: [
          { key: "day", label: "Day", render: (e) => S.day(e.day) },
          { key: "category", label: "Category" },
          { key: "note", label: "Note" },
          { key: "who", label: "Who" },
          { key: "amount_cents", label: "Amount", num: true, render: (e) => `${e.kind === "income" ? "+" : "-"}${S.money(e.amount_cents, data.currency)}` },
          { key: "id", label: "", render: (e) => S.button("Remove", async () => { if (await S.confirmDelete("this entry")) { await api(`/entries/${e.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) },
        ],
        rows: data.entries,
        empty: "Nothing recorded this month. Add money spent or money in above.",
      })),
    );
  }

  function billFields(categories) {
    return [
      { name: "name", label: "Bill", required: true, placeholder: "Electricity" },
      { name: "amount_cents", label: "Usual amount", type: "money" },
      { name: "repeat", label: "How often", type: "select", options: [["monthly", "Every month"], ["yearly", "Every year"], ["once", "Once"]] },
      { name: "due_day", label: "Day of the month it is due (monthly)", type: "number", min: 1, max: 31 },
      { name: "due_date", label: "Date it is due (yearly or once)", type: "date" },
      { name: "category", label: "Budget category", type: "select", options: categories.map((c) => c.name) },
      { name: "autopay", label: "Paid automatically (direct debit)", type: "checkbox" },
      { name: "notes", label: "Notes", placeholder: "Account number, how to pay" },
    ];
  }

  async function billsTab(body, rerender) {
    const data = await api("/bills");
    const add = () => S.edit({ title: "Add a bill", fields: billFields(data.categories), values: { repeat: "monthly", due_day: 1, category: "Bills" }, submitLabel: "Add bill", onSave: async (values) => { await api("/bills", { method: "POST", body: values }); rerender(); } });
    const tones = { overdue: "bad", due: "warn", upcoming: "", settled: "good" };
    put(body, 
      h("div", { class: "btn-row" }, S.button("Add a bill", add)),
      data.bills.length ? h("div", { class: "life-cards" }, ...data.bills.map((bill) => h("section", { class: `life-card ${tones[bill.status] || ""}` },
        h("header", {}, h("h2", { text: bill.name }), S.pill(bill.status === "due" ? "due today" : bill.status, { "due today": "warn", overdue: "bad", upcoming: "info", settled: "good" })),
        h("p", { class: "life-line-text", text: `${bill.amount_cents ? S.money(bill.amount_cents) : "Amount varies"} · ${billWhen(bill)}` }),
        h("p", { class: "muted", text: [bill.repeat === "monthly" ? `Every month on day ${bill.due_day}` : bill.repeat === "yearly" ? "Every year" : "Once", bill.autopay ? "Paid automatically" : "", bill.last_paid_at ? `Last paid ${S.day(bill.last_paid_at)}` : ""].filter(Boolean).join(" · ") }),
        h("div", { class: "btn-row" },
          bill.status !== "settled" ? S.button("Paid", async () => { await api(`/bills/${bill.id}/paid`, { method: "POST", body: {} }); Aira.toast(`${bill.name} marked paid.`, "success"); rerender(); }) : null,
          bill.last_paid_period ? S.button("Undo last payment", async () => { await api(`/bills/${bill.id}/unpaid`, { method: "POST", body: { period: bill.last_paid_period } }); rerender(); }, { kind: "ghost sm" }) : null,
          S.button("Edit", () => S.edit({ title: `Edit ${bill.name}`, fields: billFields(data.categories), values: bill, onSave: async (values) => { await api(`/bills/${bill.id}`, { method: "PATCH", body: values }); rerender(); } }), { kind: "ghost sm" }),
          S.button("Remove", async () => { if (await S.confirmDelete(bill.name)) { await api(`/bills/${bill.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }))))) : Aira.ui.empty("No bills yet", "Add your regular bills and Aira reminds you before they are due."),
    );
  }

  // ---------------------------------------------------------------- notes
  async function notesTab(body, rerender) {
    const { notes } = await api("/notes");
    const fields = [
      { name: "title", label: "Title", placeholder: "Wifi password" },
      { name: "body", label: "Note", type: "textarea", rows: 5 },
      { name: "checklist_text", label: "Checklist (one item per line, optional)", type: "textarea", rows: 3 },
      { name: "shared", label: "Share with the family", type: "checkbox" },
      { name: "pinned", label: "Pin to the top", type: "checkbox" },
    ];
    const payload = (values, old) => {
      const lines = String(values.checklist_text || "").split("\n").map((line) => line.trim()).filter(Boolean);
      const previous = new Map(((old && old.checklist) || []).map((item) => [item.text, item.done]));
      const out = { ...values, checklist: lines.map((text) => ({ text, done: !!previous.get(text) })) };
      delete out.checklist_text;
      return out;
    };
    const add = () => S.edit({ title: "New note", fields, values: { shared: true }, submitLabel: "Save note", onSave: async (values) => { await api("/notes", { method: "POST", body: payload(values) }); rerender(); } });
    put(body, 
      h("div", { class: "btn-row" }, S.button("New note", add)),
      notes.length ? h("div", { class: "life-cards" }, ...notes.map((note) => h("section", { class: "life-card" },
        h("header", {}, h("h2", { text: note.title || "Note" }), note.pinned ? S.pill("pinned", { pinned: "info" }) : null, note.shared ? null : S.pill("private", { private: "" })),
        note.body ? h("p", { style: { whiteSpace: "pre-wrap", margin: "0" }, text: note.body }) : null,
        note.checklist.length ? h("ul", { class: "life-lines" }, ...note.checklist.map((item, index) => h("li", { class: `life-line ${item.done ? "done" : ""}` },
          h("button", { type: "button", class: `life-check ${item.done ? "done" : ""}`, "aria-pressed": String(item.done), "aria-label": `${item.done ? "Untick" : "Tick"} ${item.text}`, onclick: async () => {
            const checklist = note.checklist.map((entry, i) => (i === index ? { ...entry, done: !entry.done } : entry));
            await api(`/notes/${note.id}`, { method: "PATCH", body: { checklist } });
            rerender();
          } }, h("span", { "aria-hidden": "true", text: item.done ? "✓" : "" })),
          h("span", { class: "life-line-text", text: item.text })))) : null,
        h("p", { class: "muted small", text: `${note.mine ? "By you" : `By ${note.owner}`} · ${Aira.fmt.ago(note.updated_at)}` }),
        h("div", { class: "btn-row" },
          S.button("Edit", () => S.edit({ title: "Edit note", fields, values: { ...note, checklist_text: note.checklist.map((item) => item.text).join("\n") }, onSave: async (values) => { await api(`/notes/${note.id}`, { method: "PATCH", body: payload(values, note) }); rerender(); } }), { kind: "ghost sm" }),
          S.button("Remove", async () => { if (await S.confirmDelete("this note")) { await api(`/notes/${note.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }))))) : Aira.ui.empty("No notes yet", "Keep the wifi password, the plumber's number or a packing list here."),
    );
  }

  // ---------------------------------------------------------------- rewards
  async function rewardsTab(body, rerender) {
    const data = await api("/rewards");
    const spendable = data.balance - data.pending;
    const pending = data.requests.filter((r) => r.status === "pending");
    put(body, 
      S.kpis([
        { label: "My points", value: data.balance, tone: "good", hint: data.pending ? `${data.pending} waiting for approval` : "From chores" },
        { label: "Rewards", value: data.rewards.length },
      ]),
      data.can_manage && pending.length ? S.section("Waiting for your OK", h("ul", { class: "life-lines" }, ...pending.map((request) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: `${request.who}: ${request.title}` }), h("span", { class: "muted life-line-sub", text: `${request.points} points · ${Aira.fmt.ago(request.created_at)}` })),
        h("div", { class: "btn-row" },
          S.button("Approve", async () => { await api(`/requests/${request.id}/approve`, { method: "POST", body: {} }); Aira.toast("Approved.", "success"); rerender(); }),
          S.button("Not now", async () => { await api(`/requests/${request.id}/decline`, { method: "POST", body: {} }); rerender(); }, { kind: "secondary" })))))) : null,
      S.section("Rewards", data.rewards.length ? h("ul", { class: "life-lines" }, ...data.rewards.map((reward) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: reward.title }), h("span", { class: "muted life-line-sub", text: `${reward.points} points` })),
        h("div", { class: "btn-row" },
          spendable >= reward.points ? S.button("Ask for this", async () => { await api(`/rewards/${reward.id}/request`, { method: "POST", body: {} }); Aira.toast("Asked. A grown-up will say yes or no.", "success"); rerender(); }) : h("span", { class: "muted", text: `${reward.points - spendable} more points needed` }),
          data.can_manage ? S.button("Remove", async () => { if (await S.confirmDelete(reward.title)) { await api(`/rewards/${reward.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null)))) : h("p", { class: "muted", text: data.can_manage ? "Add rewards children can earn with chore points." : "No rewards yet. Ask a grown-up to add some." }),
        data.can_manage ? h("div", { class: "btn-row" }, S.button("Add a reward", () => S.edit({ title: "Add a reward", fields: [{ name: "title", label: "Reward", required: true, placeholder: "30 minutes extra screen time" }, { name: "points", label: "Points", type: "number", min: 1, required: true }], submitLabel: "Add", onSave: async (values) => { await api("/rewards", { method: "POST", body: values }); rerender(); } }), { kind: "secondary" })) : null),
      data.can_manage ? S.section("Everyone's points", S.table({ columns: [{ key: "name", label: "Person", render: (b) => `${b.emoji} ${b.name}` }, { key: "points", label: "Points", num: true }], rows: data.balances })) : null,
      S.grid(
        S.section("My requests", data.requests.filter((r) => !data.can_manage || r.profile_id === (cache.me && cache.me.id)).length ? h("ul", { class: "life-lines" }, ...data.requests.filter((r) => !data.can_manage || r.profile_id === (cache.me && cache.me.id)).map((r) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: r.title }), S.pill(r.status, { approved: "good", declined: "bad", pending: "warn" })))) : h("p", { class: "muted", text: "Nothing asked for yet." })),
        S.section("Points history", data.history.length ? h("ul", { class: "life-lines" }, ...data.history.map((entry) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: `${entry.delta > 0 ? "+" : ""}${entry.delta}` }), h("span", { class: "muted", text: `${entry.reason} · ${Aira.fmt.ago(entry.at)}` })))) : h("p", { class: "muted", text: "Finish chores to earn points." })),
      ),
    );
  }

  // ---------------------------------------------------------------- the page
  Aira.views.planner = async (main, route) => {
    let me = cache.me;
    if (!me) { try { await loadBasics(); me = cache.me; } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; } }
    const tabs = [
      { id: "day", label: "My day" },
      { id: "chores", label: "Chores" },
      { id: "shopping", label: "Shopping" },
      ...(me.adult ? [{ id: "budget", label: "Budget" }, { id: "bills", label: "Bills" }] : []),
      { id: "notes", label: "Notes" },
      { id: "rewards", label: "Rewards" },
    ];
    const body = S.page(main, { title: "Planner", subtitle: "Chores, shopping, money and notes for the whole home.", actions: [S.button("New task", () => newTask(() => Aira.router.render()))] });
    const tab = S.tabs(body, route, "planner", tabs, tabs.some((t) => t.id === route.query.tab) ? route.query.tab : "day");
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "chores") await choresTab(holder, route, render);
        else if (tab === "shopping") await shoppingTab(holder, route, render);
        else if (tab === "budget") await budgetTab(holder, route, render);
        else if (tab === "bills") await billsTab(holder, render);
        else if (tab === "notes") await notesTab(holder, render);
        else if (tab === "rewards") await rewardsTab(holder, render);
        else await dayTab(holder, render);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    panel.replaceChildren(Aira.ui.skeleton(4));
    await render();
  };
})();

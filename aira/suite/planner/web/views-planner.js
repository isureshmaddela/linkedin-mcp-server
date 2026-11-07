/* Aira — Planner (#/planner): running the home together, for every age.
 *
 * Tabs (?tab=): day (my day: today's chores with big Done buttons, overdue, coming up, today's meals, bills soon,
 * shopping), chores (mine or the family's, with repeats and points, search, a person filter and CSV), shopping
 * (lists everyone ticks live, by aisle, with the usual items one tap away and a link for relatives), meals (the
 * week's breakfasts, lunches and dinners, recipes, and one tap to put the ingredients on a shopping list), budget
 * budget, savings and bills (adults), sell (adults: things the household no longer needs and side jobs for
 * neighbours, a public page where people ask, money in when sold or when a job is done), notes (shared or private)
 * and rewards (points from chores, rewards an adult approves, and pocket money adults pay out for points).
 * Everything runs through /api/planner/* (aira/suite/planner). DOM is built with h() and text only.
 *
 * In the Aira frame (shell.js) the tabs are the left menu's folders and the page is named after the open one;
 * "+ New" offers a task, a shopping item, a meal, a note and (adults) something to sell; the search across the top
 * finds chores, shopping, recipes, notes, rewards and (adults) bills, sales and savings in one request
 * (/api/planner/search), and Enter lists every match (?tab=search&q=).
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("planner", path, opts);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const AISLE_NAMES = { "fruit & veg": "Fruit and vegetables", bakery: "Bakery", dairy: "Milk, cheese and eggs", "meat & fish": "Meat and fish", pantry: "Cupboard", frozen: "Frozen", drinks: "Drinks", household: "Household", pharmacy: "Pharmacy", other: "Other" };
  // Glyphs for the left menu's folders, "+ New" and the search box (24x24 strokes, like app.js ICONS).
  const GLYPHS = {
    "pn-day": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    "pn-chores": '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/></svg>',
    "pn-cart": '<svg viewBox="0 0 24 24"><circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.6 12.4a2 2 0 0 0 2 1.6h7.8a2 2 0 0 0 2-1.6L21 8H6"/></svg>',
    "pn-meal": '<svg viewBox="0 0 24 24"><path d="M5 2v7a3 3 0 0 0 6 0V2M8 2v20"/><path d="M19 15V2a4 4 0 0 0-4 4v6c0 1.1.9 2 2 2h2v8"/></svg>',
    "pn-budget": '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2z"/><path d="M16 14.5h.01"/></svg>',
    "pn-savings": '<svg viewBox="0 0 24 24"><path d="M19 9.5c1.2.4 2 1.4 2 2.5M5 11c0-3.3 3.1-6 7-6s7 2.7 7 6c0 1.9-1 3.6-2.6 4.7L16 19h-3l-.5-1.5h-1L11 19H8l-.5-3C5.9 14.9 5 13.1 5 11z"/><path d="M15 10h.01M10 5.3 8.5 3"/></svg>',
    "pn-bills": '<svg viewBox="0 0 24 24"><path d="M6 2h12v20l-3-2-3 2-3-2-3 2z"/><path d="M9 7h6M9 11h6M9 15h4"/></svg>',
    "pn-sell": '<svg viewBox="0 0 24 24"><path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><path d="M7.5 7.5h.01"/></svg>',
    "pn-notes": '<svg viewBox="0 0 24 24"><path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5M8 12h8M8 16h6"/></svg>',
    "pn-rewards": '<svg viewBox="0 0 24 24"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  // The page is named after the open tab (the app's name is in the bar above), with a line about it. `adult`
  // tabs are for the household's grown-ups only (the server checks too).
  const TABS = [
    { id: "day", label: "My day", icon: "pn-day", about: "What to do today, what is coming up, today's meals and your shopping." },
    { id: "chores", label: "Chores", icon: "pn-chores", about: "Who does what, when it repeats and the points it earns." },
    { id: "shopping", label: "Shopping", icon: "pn-cart", about: "Lists everyone adds to and ticks, sorted by aisle, live." },
    { id: "meals", label: "Meals", icon: "pn-meal", about: "The week's meals and your recipes; one tap puts the ingredients on a list." },
    { id: "budget", label: "Budget", icon: "pn-budget", adult: true, about: "Money in and money spent this month, by category." },
    { id: "savings", label: "Savings", icon: "pn-savings", adult: true, about: "Save for the things that matter. Money from things you sell and side jobs can go straight here." },
    { id: "bills", label: "Bills", icon: "pn-bills", adult: true, about: "Regular bills, when they are due and what is paid." },
    { id: "sell", label: "Sell", icon: "pn-sell", adult: true, about: "Earn from things you no longer need and from side jobs for neighbours. Monosage does not take payments: you and the buyer settle in person." },
    { id: "notes", label: "Notes", icon: "pn-notes", about: "Shared or private notes and checklists for the home." },
    { id: "rewards", label: "Rewards", icon: "pn-rewards", about: "Points from chores, rewards to spend them on, and pocket money." },
  ];
  // What "+ New" or the search box asked for (?new= or the id of a record to show), answered by the tab drawn next;
  // `hooks` are the open tab's own create actions, which "+ New" uses.
  let pending = null;
  let hooks = {};
  const FOUND_KEYS = ["task", "item", "recipe", "note", "bill", "sale", "goal", "reward"];

  // append() and replaceChildren() turn null into the text "null": only real nodes go in.
  const put = (node, ...children) => node.append(...children.filter(Boolean));
  const fill = (node, ...children) => node.replaceChildren(...children.filter(Boolean));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const addDays = (day, n) => {
    const [y, m, d] = day.split("-").map(Number);
    const moved = new Date(Date.UTC(y, m - 1, d + n));
    return moved.toISOString().slice(0, 10);
  };
  const longDay = (day) => {
    const [y, m, d] = day.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
  };
  function dueLabel(task, today) {
    if (!task.due_date) return "Any time";
    if (task.due_date < today) return `Overdue (was due ${S.day(task.due_date)})`;
    const time = task.due_time ? ` at ${task.due_time}` : "";
    if (task.due_date === today) return `Today${time}`;
    if (task.due_date === addDays(today, 1)) return `Tomorrow${time}`;
    return `${S.day(task.due_date)}${time}`;
  }
  const debounce = (fn, ms) => { let timer = 0; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; };
  const monthName = (key) => { const [y, m] = key.split("-").map(Number); return new Date(y, m - 1, 1).toLocaleDateString([], { month: "short", year: "numeric" }); };
  // Poll while this screen is on the page. Aira.suite.live alone stops only when the address's path changes, and
  // Planner's tabs and lists live in the query: without this every visit would add another loop.
  const live = (node, fn, ms) => {
    const stop = S.live(async () => { if (!node.isConnected) { stop(); return; } await fn(stop); }, ms);
    return stop;
  };
  // A form in a pop-up (Aira.suite.edit): Enter in a one-line field presses its main button (Put it aside, Save...),
  // as in any form. The pop-up is on the page as soon as S.edit returns.
  const edit = (options) => {
    const done = S.edit(options);
    const overlay = document.getElementById("overlay");
    const box = overlay && overlay.lastElementChild ? overlay.lastElementChild.querySelector(".modal") : null;
    if (box) {
      box.addEventListener("keydown", (event) => {
        const target = event.target;
        if (event.key !== "Enter" || event.isComposing || event.shiftKey || !(target instanceof HTMLInputElement)) return;
        if (["checkbox", "radio", "button", "submit", "reset", "file", "color", "range"].includes(target.type)) return;
        const main = box.querySelector(".actions .btn.primary");
        if (!main || main.disabled) return;
        event.preventDefault();
        main.click();
      });
    }
    return done;
  };

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
    const turns = task.rotation_names && task.rotation_names.length ? `Takes turns: ${task.rotation_names.join(", ")}${task.next_turn ? ` (next: ${task.next_turn})` : ""}` : "";
    const sub = [dueLabel(task, today), task.assignee_name, task.repeat_label, turns, task.points ? `${task.points} points` : "", task.done_by && task.done_by.length && task.assignee === "everyone" ? `Done by ${task.done_by.join(", ")}` : ""].filter(Boolean).join(" · ");
    const actions = task.can_edit ? h("div", { class: "btn-row" },
      S.button("Edit", () => editTask(task, rerender), { kind: "ghost sm" }),
      S.button("Remove", async () => { if (await S.confirmDelete(`"${task.title}"`)) { await api(`/tasks/${task.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })) : null;
    return h("li", { class: `life-line ${done ? "done" : ""}`, style: { flexWrap: "wrap" }, dataset: { found: task.id } }, check,
      h("div", { style: { flex: "1", minWidth: "180px" } }, h("span", { class: "life-line-text", text: task.title }), h("span", { class: "muted life-line-sub", text: sub }), task.notes ? h("span", { class: "muted life-line-sub", text: task.notes }) : null),
      actions);
  }
  const taskList = (tasks, today, rerender, empty) => (tasks.length ? h("ul", { class: "life-lines" }, ...tasks.map((task) => taskRow(task, today, rerender))) : h("p", { class: "muted", text: empty }));

  // Who is asking and the household. Loaded every time the page opens: switching person (or adding one) must not
  // leave the previous person's tabs and names on screen.
  let cache = { people: [], me: null };
  async function loadBasics() {
    const data = await api("/overview", { silent: true });
    cache = { people: data.people, me: data.me, today: data.today, lists: data.lists, data };
    return data;
  }
  const personOptions = () => cache.people.map((p) => [p.id, `${p.emoji} ${p.name}`]);

  function taskFields(task) {
    const me = cache.me || {};
    const people = me.adult ? personOptions() : [[me.id, `Me (${me.name})`]];
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
      ...(me.adult && cache.people.length > 1 ? [
        { type: "heading", label: "Take turns (optional, for a chore that repeats)" },
        ...cache.people.map((p) => ({ name: `t_${p.id}`, label: `${p.emoji} ${p.name}`, type: "checkbox", value: ((task && task.rotation) || []).includes(p.id), hint: "" })),
      ] : []),
      ...(me.adult ? [{ name: "points", label: "Points (for children)", type: "number", min: 0, max: 1000, hint: "Children earn these when it is done and spend them on rewards or pocket money." }] : []),
      { name: "notes", label: "Notes", type: "textarea", rows: 2 },
    ];
  }
  function taskPayload(values) {
    const payload = { ...values, repeat_days: DAYS.map((_, index) => (values[`d${index}`] ? index : null)).filter((d) => d !== null) };
    DAYS.forEach((_, index) => delete payload[`d${index}`]);
    // Take turns: the people ticked, in the household's order. Only an adult's form has these boxes.
    const turnKeys = Object.keys(values).filter((key) => key.startsWith("t_"));
    if (turnKeys.length) payload.rotation = turnKeys.filter((key) => values[key]).map((key) => key.slice(2));
    turnKeys.forEach((key) => delete payload[key]);
    if (payload.points === null) delete payload.points;
    if (payload.repeat_every === null) delete payload.repeat_every;
    return payload;
  }
  async function newTask(rerender, defaults = {}) {
    if (!cache.me) await loadBasics();
    await edit({ title: "New task", fields: taskFields(null), values: { assignee: cache.me.adult ? "" : cache.me.id, due_date: cache.today, repeat: "none", ...defaults }, submitLabel: "Add task", onSave: async (values) => { await api("/tasks", { method: "POST", body: taskPayload(values) }); rerender(); } });
  }
  async function editTask(task, rerender) {
    if (!cache.me) await loadBasics();
    // A chore that does not repeat every few days has 0 there: the box shows empty, not 0.
    await edit({ title: "Edit task", fields: taskFields(task), values: { ...task, repeat_every: task.repeat_every || null }, onSave: async (values) => { await api(`/tasks/${task.id}`, { method: "PATCH", body: taskPayload(values) }); rerender(); } });
  }

  async function dayTab(body, rerender, fresh) {
    const data = fresh || (await loadBasics());
    const today = data.today;
    const quick = h("input", { type: "text", placeholder: "Add something to do today", "aria-label": "Add something to do today", maxlength: "120", style: { flex: "1", minWidth: "0" } });
    const add = async () => { const title = quick.value.trim(); if (!title) { quick.focus(); return; } await api("/tasks", { method: "POST", body: { title, assignee: data.me.id, due_date: today } }); rerender(); };
    quick.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(); } });
    const meals = data.meals_today || [];
    put(body,
      S.kpis([
        { label: "To do today", value: data.today_tasks.length },
        { label: "Overdue", value: data.overdue.length, tone: data.overdue.length ? "warn" : "" },
        { label: "My points", value: data.points, hint: "From chores", tone: "good" },
        data.me.adult ? { label: "Reward requests", value: data.pending_requests, tone: data.pending_requests ? "warn" : "", hint: data.pending_requests ? "Waiting for you" : "" } : null,
        data.me.adult && data.sale_requests ? { label: "People are asking", value: data.sale_requests, tone: "good", hint: "See Sell" } : null,
        data.me.adult && data.earned_this_month_cents ? { label: "Earned this month", value: S.money(data.earned_this_month_cents, data.currency), tone: "good", hint: "Things sold and side jobs" } : null,
      ]),
      S.section("Today", h("div", { class: "btn-row planner-quick" }, quick, S.button("Add", add)), taskList(data.today_tasks, today, rerender, "Nothing for today. Enjoy your day!")),
      data.overdue.length ? S.section("Overdue", taskList(data.overdue, today, rerender, "")) : null,
      S.grid(
        S.section("Coming up this week", taskList(data.upcoming, today, rerender, "Nothing planned for the next 7 days.")),
        S.section("Meals today", meals.length ? h("ul", { class: "life-lines" }, ...meals.map((meal) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: meal.title }), h("span", { class: "muted life-line-sub", text: meal.slot_name }))))) : h("p", { class: "muted", text: "No meals planned for today." }), h("div", { class: "btn-row" }, h("a", { class: "btn secondary sm", href: "#/planner?tab=meals", text: "Plan meals" }))),
        data.anytime.length ? S.section("Any time", taskList(data.anytime, today, rerender, "")) : null,
        data.me.adult && data.bills_soon.length ? S.section("Bills this week", h("ul", { class: "life-lines" }, ...data.bills_soon.map((bill) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: `${bill.name}${bill.amount_cents ? ` ${S.money(bill.amount_cents)}` : ""}` }), h("span", { class: "muted life-line-sub", text: billWhen(bill) })))))) : null,
        S.section("Shopping", h("ul", { class: "life-lines" }, ...data.lists.map((list) => h("li", { class: "life-line" }, h("a", { href: `#/planner?tab=shopping&list=${list.id}`, class: "life-line-text", text: list.name }), h("span", { class: "muted", text: list.open ? plural(list.open, "item") : "Nothing to buy" }))))),
      ),
    );
  }

  async function choresTab(body, route, rerender) {
    const me = cache.me;
    const scope = route.query.scope || (me.adult ? "family" : "mine");
    const showDone = route.query.done === "1";
    let person = route.query.person || "";
    // ?q= comes from the search across the top (or a link): the box shows it and the list is filtered by it.
    let q = String(route.query.q || "").trim().slice(0, 80);
    const go = (query) => Aira.router.go(["planner"], { tab: "chores", scope, done: showDone ? "1" : "", person, q, ...query });
    const area = h("div", { class: "suite-body" });
    const search = h("input", { type: "search", placeholder: "Search tasks", "aria-label": "Search tasks", maxlength: "80", value: q, style: { flex: "1", minWidth: "0" } });
    hooks.task = () => newTask(() => draw());
    const query = () => {
      const params = new URLSearchParams({ scope, status: showDone ? "done" : "open" });
      if (q) params.set("q", q);
      if (person) params.set("person", person);
      return params;
    };
    const draw = async () => {
      const data = await api(`/tasks?${query()}`);
      const today = data.today;
      const groups = showDone ? [["Done recently", data.tasks]] : [
        ["Overdue", data.tasks.filter((t) => t.overdue)],
        ["Today", data.tasks.filter((t) => t.due_date === today && !t.overdue)],
        ["Coming up", data.tasks.filter((t) => t.due_date && t.due_date > today)],
        ["Any time", data.tasks.filter((t) => !t.due_date)],
      ];
      const empty = q || person ? Aira.ui.empty("No tasks match", "Try another word, or show everyone's tasks.", h("div", { class: "btn-row" }, S.button("Clear the search", () => { q = ""; search.value = ""; person = ""; if (personPicker) personPicker.value = ""; return draw(); }, { kind: "secondary sm" })))
        : showDone ? Aira.ui.empty("Nothing done yet", "Finished tasks show up here.")
          : Aira.ui.empty("No tasks", "Add the chores and to-dos for the home: who does them, when they repeat and the points they earn.", h("div", { class: "btn-row" }, S.button("New task", () => newTask(draw), { icon: "plus" })));
      fill(area, ...(data.tasks.length ? groups.filter(([, tasks]) => tasks.length).map(([title, tasks]) => S.section(title, taskList(tasks, today, draw, ""))) : [empty]));
    };
    search.addEventListener("input", debounce(() => { q = search.value.trim(); draw().catch(() => {}); }, 250));
    const personPicker = me.adult && scope === "family" ? Aira.ui.select([["", "Everyone"], ...personOptions()], person, { "aria-label": "Whose tasks" }) : null;
    if (personPicker) personPicker.addEventListener("change", () => { person = personPicker.value; draw().catch(() => {}); });
    put(body,
      h("div", { class: "btn-row" },
        S.button("New task", () => newTask(draw)),
        h("button", { type: "button", class: `btn ${scope === "mine" ? "" : "secondary"}`, "aria-pressed": String(scope === "mine"), text: "My tasks", onclick: () => go({ scope: "mine", person: "" }) }),
        h("button", { type: "button", class: `btn ${scope === "family" ? "" : "secondary"}`, "aria-pressed": String(scope === "family"), text: me.adult ? "Everyone's tasks" : "Family tasks", onclick: () => go({ scope: "family" }) }),
        h("button", { type: "button", class: "btn ghost", text: showDone ? "Show to do" : "Show done", onclick: () => go({ done: showDone ? "" : "1" }) })),
      h("div", { class: "btn-row planner-filters" }, search, personPicker, S.button("Download CSV", () => Aira.download(`/api/planner/tasks?format=csv&${new URLSearchParams({ scope, status: "all", q, person })}`, "chores.csv"), { kind: "ghost sm" })),
      area,
    );
    await draw();
    void rerender;
  }

  // ---------------------------------------------------------------- shopping
  async function shoppingTab(body, route) {
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
      const row = (item) => h("li", { class: `life-line ${item.checked ? "done" : ""}`, dataset: { found: item.id } },
        h("button", { type: "button", class: `life-check ${item.checked ? "done" : ""}`, "aria-pressed": String(item.checked), "aria-label": `${item.checked ? "Untick" : "Tick"} ${item.name}`, onclick: async () => {
          const updated = (await api(`/items/${item.id}`, { method: "PATCH", body: { checked: !item.checked } })).item;
          items.set(updated.id, updated);
          draw();
        } }, h("span", { "aria-hidden": "true", text: item.checked ? "✓" : "" }), h("span", { class: "life-check-label", text: item.checked ? "Got it" : "Get" })),
        h("div", { style: { flex: "1", minWidth: "0" } }, h("span", { class: "life-line-text", text: `${item.name}${item.quantity ? ` (${item.quantity})` : ""}` }), h("span", { class: "muted life-line-sub", text: item.checked && item.checked_by ? `Ticked by ${item.checked_by}` : item.added_by ? `Added by ${item.added_by}` : "" })),
        h("button", { type: "button", class: "btn ghost sm", "aria-label": `Remove ${item.name}`, text: "Remove", onclick: async () => { await api(`/items/${item.id}`, { method: "DELETE" }); items.delete(item.id); draw(); } }));
      fill(area,
        open.length ? h("div", { class: "suite-body" }, ...Object.keys(AISLE_NAMES).filter((key) => byAisle[key]).map((key) => S.section(AISLE_NAMES[key], h("ul", { class: "life-lines" }, ...byAisle[key].map(row))))) : Aira.ui.empty("Nothing to buy", "Add what you need above, or tap one of your usual items."),
        ticked.length ? S.section(`In the basket (${ticked.length})`, h("ul", { class: "life-lines" }, ...ticked.map(row)), h("div", { class: "btn-row" }, S.button("Clear ticked items", async () => { await api(`/lists/${current.id}/clear`, { method: "POST", body: {} }); for (const item of ticked) items.delete(item.id); draw(); await refreshUsual(); }, { kind: "secondary" }))) : null,
      );
    };
    const name = h("input", { type: "text", placeholder: "Milk", "aria-label": "Item", maxlength: "80", style: { flex: "2", minWidth: "0" } });
    const quantity = h("input", { type: "text", placeholder: "How many", "aria-label": "How many", maxlength: "30", style: { flex: "1", minWidth: "0" } });
    const add = async (value, qty = "") => {
      const itemName = String(value || "").trim();
      if (!itemName) { name.focus(); return; }
      const item = (await api(`/lists/${current.id}/items`, { method: "POST", body: { name: itemName, quantity: qty } })).item;
      items.set(item.id, item);
      name.value = "";
      quantity.value = "";
      name.focus();
      draw();
      // It is on the list now: its usual-item button goes until it is bought.
      const key = itemName.toLowerCase();
      if (usualItems.some((entry) => entry.name.toLowerCase() === key)) { usualItems = usualItems.filter((entry) => entry.name.toLowerCase() !== key); drawUsual(); }
    };
    for (const input of [name, quantity]) input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(name.value, quantity.value); } });
    // "+ New" > Add to the shopping list: the add box of the list on screen, ready to type in.
    hooks.item = () => { name.scrollIntoView({ block: "center" }); name.focus(); };
    const chips = h("div", { class: "btn-row" }, ...lists.map((list) => h("a", { class: `btn ${list.id === current.id ? "" : "secondary"} sm`, href: `#/planner?tab=shopping&list=${list.id}`, "aria-current": list.id === current.id ? "page" : "false", text: `${list.name}${list.open ? ` (${list.open})` : ""}` })),
      S.button("New list", () => edit({ title: "New shopping list", fields: [{ name: "name", label: "Name", required: true, placeholder: "Pharmacy" }], submitLabel: "Create", onSave: async (values) => { const made = (await api("/lists", { method: "POST", body: values })).list; Aira.router.go(["planner"], { tab: "shopping", list: made.id }); } }), { kind: "ghost sm" }),
      S.button("Rename", () => edit({ title: "Rename this list", fields: [{ name: "name", label: "Name", required: true }], values: { name: current.name }, onSave: async (values) => { await api(`/lists/${current.id}`, { method: "PATCH", body: values }); Aira.router.render(); } }), { kind: "ghost sm" }),
      lists.length > 1 && cache.me.adult ? S.button("Remove this list", async () => { if (await S.confirmDelete(`the ${current.name} list`)) { await api(`/lists/${current.id}`, { method: "DELETE" }); Aira.router.go(["planner"], { tab: "shopping" }); } }, { kind: "ghost sm" }) : null);
    const usualArea = h("div");
    let usualItems = detail.usual;
    const drawUsual = () => fill(usualArea, usualItems.length ? S.section("Your usual items", h("p", { class: "muted", text: "Tap to add." }), h("div", { class: "btn-row" }, ...usualItems.map((item) => S.button(`+ ${item.name}`, () => add(item.name), { kind: "secondary sm" })))) : null);
    const refreshUsual = async () => { usualItems = (await api(`/lists/${current.id}`, { silent: true })).usual; drawUsual(); };
    put(body,
      chips,
      S.section(`Add to ${current.name}`, h("div", { class: "btn-row planner-quick" }, name, quantity, S.button("Add", () => add(name.value, quantity.value)))),
      usualArea,
      area,
      cache.me.adult ? S.share({ title: "Share this list", text: "A relative can open the link to see the list, tick items and add things, without an account.", link: detail.link, onCreate: async () => (await api(`/lists/${current.id}/link`, { method: "POST", body: {} })).link, onRevoke: async () => api(`/lists/${current.id}/link`, { method: "DELETE" }) }) : null,
      h("p", { class: "muted small", text: "This list updates by itself when someone else ticks or adds something." }),
    );
    draw();
    drawUsual();
    live(area, async (stop) => {
      let changes;
      try { changes = await api(`/lists/${current.id}?since=${encodeURIComponent(since)}`, { silent: true }); } catch (error) { if (error.status === 404) stop(); return; }
      since = changes.now;
      if (!changes.changed.length) return;
      for (const item of changes.changed) { if (item.deleted) items.delete(item.id); else items.set(item.id, item); }
      draw();
    }, 2500);
  }

  // ---------------------------------------------------------------- meals
  function mealFields(week) {
    return [
      { name: "day", label: "Day", type: "date", required: true },
      { name: "slot", label: "Meal", type: "select", options: week.slots.map((slot) => [slot.id, slot.name]) },
      { name: "recipe_id", label: "Saved recipe", type: "select", options: [["", "No recipe: write the meal below"], ...week.recipes.map((recipe) => [recipe.id, recipe.title])] },
      { name: "title", label: "Meal", placeholder: "Chicken curry", hint: "Leave empty to use the recipe's name." },
      { name: "cook", label: "Who is cooking", type: "select", options: [["", "Not decided"], ...week.people.map((p) => [p.id, `${p.emoji} ${p.name}`])] },
      { name: "notes", label: "Note", placeholder: "Make extra for lunch" },
    ];
  }
  function planMeal(week, values, meal, rerender) {
    const recipeTitle = (id) => (week.recipes.find((recipe) => recipe.id === id) || {}).title || "";
    return edit({ title: meal ? "Change meal" : "Plan a meal", fields: mealFields(week), values, submitLabel: meal ? "Save" : "Plan it", onSave: async (form) => {
      const body = { ...form };
      // Choosing another recipe renames the meal unless a different name was typed.
      if (body.recipe_id && (!body.title || body.title === recipeTitle(values.recipe_id))) body.title = "";
      if (meal) await api(`/meals/${meal.id}`, { method: "PATCH", body });
      else await api("/meals", { method: "POST", body });
      rerender();
    } });
  }
  function recipeFields() {
    return [
      { name: "title", label: "Meal name", required: true, placeholder: "Spaghetti bolognese" },
      { name: "ingredients_text", label: "Ingredients (one per line)", type: "textarea", rows: 6, placeholder: "500 g minced beef\n2 onions\nSpaghetti" },
      { name: "minutes", label: "Minutes to make", type: "number", min: 0, max: 1440 },
      { name: "notes", label: "How to make it", type: "textarea", rows: 4 },
    ];
  }
  const recipePayload = (values) => {
    const out = { ...values, ingredients: String(values.ingredients_text || "").split("\n").map((line) => line.trim()).filter(Boolean) };
    delete out.ingredients_text;
    if (out.minutes === null) out.minutes = 0;
    return out;
  };
  async function shopFor(week, body) {
    const choose = week.lists.length > 1 ? await edit({ title: "Which shopping list?", fields: [{ name: "list_id", label: "List", type: "select", options: week.lists.map((list) => [list.id, list.name]) }], values: { list_id: week.lists[0].id }, submitLabel: "Add ingredients", onSave: async (values) => values }) : { list_id: week.lists[0] && week.lists[0].id };
    if (!choose) return;
    const result = await api("/meals/shop", { method: "POST", body: { ...body, list_id: choose.list_id } });
    if (result.added.length) Aira.toast(`Added ${plural(result.added.length, "item")} to the shopping list${result.skipped.length ? ` (${result.skipped.join(", ")} already on it)` : ""}.`, "success");
    else if (!result.recipes) Aira.toast("Choose saved recipes for your meals first: their ingredients go on the list.");
    else Aira.toast("Everything is already on the shopping list.", "success");
  }

  async function mealsTab(body, route, rerender) {
    const week = await api(`/meals${route.query.week ? `?start=${encodeURIComponent(route.query.week)}` : ""}`);
    const goWeek = (start) => Aira.router.go(["planner"], { tab: "meals", week: start });
    const thisWeek = week.today >= week.start && week.today <= week.end;
    const dayCard = (day) => h("section", { class: `life-card planner-day ${day.today ? "planner-today" : ""}`, "aria-label": longDay(day.day) },
      h("header", {}, h("h2", { text: longDay(day.day) }), day.today ? S.pill("today", { today: "info" }) : null),
      h("ul", { class: "life-lines" }, ...week.slots.map((slot) => {
        const meal = day.meals[slot.id];
        return h("li", { class: "life-line", style: { flexWrap: "wrap" } },
          h("div", { style: { flex: "1", minWidth: "140px" } }, h("span", { class: "muted life-line-sub", text: slot.name }), h("span", { class: "life-line-text", text: meal ? meal.title : "Nothing planned" }), meal && (meal.cook_name || meal.notes) ? h("span", { class: "muted life-line-sub", text: [meal.cook_name ? `${meal.cook_name} is cooking` : "", meal.notes].filter(Boolean).join(" · ") }) : null),
          h("div", { class: "btn-row" },
            !meal ? S.button("Plan", () => planMeal(week, { day: day.day, slot: slot.id }, null, rerender), { kind: "secondary sm" }) : null,
            meal && meal.can_edit ? S.button("Change", () => planMeal(week, meal, meal, rerender), { kind: "ghost sm" }) : null,
            meal && meal.recipe_id ? S.button("Shop for it", () => shopFor(week, { recipe_id: meal.recipe_id }), { kind: "ghost sm" }) : null,
            meal && meal.can_edit ? S.button("Remove", async () => { if (await S.confirmDelete(`${meal.title} (${slot.name.toLowerCase()})`)) { await api(`/meals/${meal.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null));
      })));
    const recipesArea = h("div", { class: "suite-body" });
    const asked = String(route.query.q || "").trim().slice(0, 80);
    const recipeSearch = h("input", { type: "search", placeholder: "Search recipes or ingredients", "aria-label": "Search recipes", maxlength: "80", value: asked, style: { flex: "1", minWidth: "0" } });
    const drawRecipes = (list) => fill(recipesArea, list.length ? h("ul", { class: "life-lines" }, ...list.map((recipe) => h("li", { class: "life-line", style: { flexWrap: "wrap" }, dataset: { found: recipe.id } },
      h("div", { style: { flex: "1", minWidth: "160px" } }, h("span", { class: "life-line-text", text: recipe.title }), h("span", { class: "muted life-line-sub", text: [recipe.ingredients.length ? plural(recipe.ingredients.length, "ingredient") : "No ingredients yet", recipe.minutes ? `${recipe.minutes} minutes` : ""].filter(Boolean).join(" · ") }), recipe.ingredients.length ? h("span", { class: "muted life-line-sub", text: recipe.ingredients.map((i) => `${i.quantity ? `${i.quantity} ` : ""}${i.name}`).join(", ") }) : null),
      h("div", { class: "btn-row" },
        S.button("Plan it", () => planMeal(week, { day: thisWeek ? week.today : week.start, slot: "dinner", recipe_id: recipe.id }, null, rerender), { kind: "secondary sm" }),
        recipe.ingredients.length ? S.button("Shop for it", () => shopFor(week, { recipe_id: recipe.id }), { kind: "ghost sm" }) : null,
        recipe.can_edit ? S.button("Edit", () => edit({ title: `Edit ${recipe.title}`, fields: recipeFields(), values: { ...recipe, ingredients_text: recipe.ingredients.map((i) => `${i.quantity ? `${i.quantity} ` : ""}${i.name}`).join("\n") }, onSave: async (values) => { await api(`/recipes/${recipe.id}`, { method: "PATCH", body: recipePayload(values) }); rerender(); } }), { kind: "ghost sm" }) : null,
        recipe.can_edit ? S.button("Remove", async () => { if (await S.confirmDelete(recipe.title)) { await api(`/recipes/${recipe.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null)))) : Aira.ui.empty(week.recipes.length ? "No recipes match" : "No recipes yet", week.recipes.length ? "Try another word." : "Save the meals you make often, with their ingredients. Then one tap puts them on the shopping list."));
    recipeSearch.addEventListener("input", debounce(async () => { drawRecipes((await api(`/recipes?q=${encodeURIComponent(recipeSearch.value.trim())}`, { silent: true })).recipes); }, 250));
    const planToday = () => planMeal(week, { day: thisWeek ? week.today : week.start, slot: "dinner" }, null, rerender);
    const newRecipe = () => edit({ title: "New recipe", fields: recipeFields(), submitLabel: "Save recipe", onSave: async (values) => { await api("/recipes", { method: "POST", body: recipePayload(values) }); rerender(); } });
    hooks.meal = planToday;
    hooks.recipe = newRecipe;
    put(body,
      h("div", { class: "btn-row planner-week-nav" },
        S.button("Previous week", () => goWeek(week.previous), { kind: "secondary sm" }),
        h("strong", { class: "planner-week-label", text: `${S.day(week.start)} to ${S.day(week.end)}` }),
        S.button("Next week", () => goWeek(week.next), { kind: "secondary sm" }),
        thisWeek ? null : S.button("This week", () => goWeek(""), { kind: "ghost sm" })),
      h("div", { class: "btn-row" },
        S.button("Plan a meal", planToday),
        S.button("Shop for this week's meals", () => shopFor(week, { start: week.start }), { kind: "secondary" }),
        S.button("Copy last week", async () => { const { copied } = await api("/meals/copy", { method: "POST", body: { start: week.start } }); Aira.toast(copied ? `Copied ${plural(copied, "meal")} from last week.` : "Nothing to copy: last week's meals are already here or there were none.", copied ? "success" : ""); rerender(); }, { kind: "ghost" })),
      week.planned ? null : h("p", { class: "muted", text: "Nothing planned this week yet. Tap Plan on a day, or copy last week." }),
      h("div", { class: "life-cards planner-week" }, ...week.days.map(dayCard)),
      S.section("Recipes", h("div", { class: "btn-row planner-filters" }, recipeSearch, S.button("New recipe", () => newRecipe(), { kind: "secondary" })), recipesArea),
    );
    // A search from the top of the page lands here with ?q=: the same match as the server's (name or ingredient).
    const needle = asked.toLowerCase();
    drawRecipes(needle ? week.recipes.filter((recipe) => recipe.title.toLowerCase().includes(needle) || recipe.ingredients.some((i) => i.name.toLowerCase().includes(needle))) : week.recipes);
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
    picker.addEventListener("change", () => { if (picker.value) Aira.router.go(["planner"], { tab: "budget", month: picker.value }); });
    const categoryNames = data.categories.filter((c) => c.id).map((c) => c.name);
    const incomeNames = ["Salary", "Pension", "Benefits", "Selling", "Gift", "Other"];
    // A new entry lands on a day of the month being looked at: today in this month, else its first day.
    const defaultDay = data.today.slice(0, 7) === data.month ? data.today : `${data.month}-01`;
    const entryFields = (kind) => [
      { name: "amount_cents", label: "Amount", type: "money", required: true },
      { name: "day", label: "Day", type: "date" },
      { name: "category", label: "Category", type: "select", options: kind === "income" ? incomeNames : categoryNames },
      { name: "note", label: "Note", placeholder: kind === "income" ? "October salary" : "Weekly shop" },
    ];
    const addEntry = (kind) => edit({ title: kind === "income" ? "Money in" : "Money spent", fields: entryFields(kind), values: { day: defaultDay, category: kind === "income" ? "Salary" : categoryNames.includes("Groceries") ? "Groceries" : categoryNames[0] }, submitLabel: "Add", onSave: async (values) => { await api("/entries", { method: "POST", body: { ...values, kind } }); rerender(); } });
    const editEntry = (entry) => edit({ title: entry.kind === "income" ? "Change money in" : "Change money spent", fields: entryFields(entry.kind).map((field) => (field.name === "category" && !field.options.includes(entry.category) ? { ...field, options: [entry.category, ...field.options] } : field)), values: entry, onSave: async (values) => { await api(`/entries/${entry.id}`, { method: "PATCH", body: values }); rerender(); } });
    const editCategory = (c) => edit({ title: c.id ? `Budget for ${c.name}` : c.name ? `Add ${c.name} as a category` : "Add a category", fields: [{ name: "name", label: "Name", required: true }, { name: "monthly_cents", label: "Budget each month", type: "money" }], values: c, onSave: async (values) => { await api(c.id ? `/budget/categories/${c.id}` : "/budget/categories", { method: c.id ? "PATCH" : "POST", body: values }); rerender(); } });
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
          onRow: editCategory,
        }), h("p", { class: "muted small", text: "Tap a category to set its monthly budget." }), h("div", { class: "btn-row" }, S.button("Add a category", () => editCategory({ name: "", monthly_cents: 0 }), { kind: "ghost sm" }))),
        S.section("Where the money went", data.categories.some((c) => c.spent_cents) ? S.bars(data.categories.filter((c) => c.spent_cents).map((c) => ({ label: c.name, value: c.spent_cents })), { format: (v) => S.money(v, data.currency) }) : h("p", { class: "muted", text: "Nothing spent this month yet." })),
      ),
      S.section("Last 6 months", S.table({
        columns: [
          { key: "month", label: "Month", render: (t) => monthName(t.month) },
          { key: "income_cents", label: "Money in", num: true, render: (t) => S.money(t.income_cents, data.currency) },
          { key: "expense_cents", label: "Spent", num: true, render: (t) => S.money(t.expense_cents, data.currency) },
          { key: "net_cents", label: "Left over", num: true, render: (t) => h("span", { style: { color: t.net_cents < 0 ? "var(--danger)" : "inherit", fontWeight: t.net_cents < 0 ? "700" : "400" }, text: t.net_cents < 0 ? `${S.money(-t.net_cents, data.currency)} over` : S.money(t.net_cents, data.currency) }) },
        ],
        rows: data.trend || [],
        caption: "Money in and spent in the last six months",
      })),
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
        onRow: editEntry,
        empty: "Nothing recorded this month. Add money spent or money in above.",
      }), data.entries.length ? h("p", { class: "muted small", text: "Tap an entry to change it." }) : null),
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
    const add = () => edit({ title: "Add a bill", fields: billFields(data.categories), values: { repeat: "monthly", due_day: 1, category: "Bills" }, submitLabel: "Add bill", onSave: async (values) => { await api("/bills", { method: "POST", body: values }); rerender(); } });
    hooks.bill = add;
    const tones = { overdue: "bad", due: "warn", upcoming: "", settled: "good" };
    const monthly = data.bills.reduce((sum, bill) => sum + (bill.repeat === "monthly" ? bill.amount_cents : bill.repeat === "yearly" ? Math.round(bill.amount_cents / 12) : 0), 0);
    put(body,
      h("div", { class: "btn-row" }, S.button("Add a bill", add)),
      data.bills.length ? S.kpis([
        { label: "Bills", value: data.bills.length },
        { label: "Each month, about", value: S.money(monthly), hint: "Yearly bills spread over 12 months" },
        { label: "Overdue", value: data.bills.filter((bill) => bill.status === "overdue").length, tone: data.bills.some((bill) => bill.status === "overdue") ? "bad" : "" },
      ]) : null,
      data.bills.length ? h("div", { class: "life-cards" }, ...data.bills.map((bill) => h("section", { class: `life-card ${tones[bill.status] || ""}`, dataset: { found: bill.id } },
        h("header", {}, h("h2", { text: bill.name }), S.pill(bill.status === "due" ? "due today" : bill.status, { "due today": "warn", overdue: "bad", upcoming: "info", settled: "good" })),
        h("p", { class: "life-line-text", text: `${bill.amount_cents ? S.money(bill.amount_cents) : "Amount varies"} · ${billWhen(bill)}` }),
        h("p", { class: "muted", text: [bill.repeat === "monthly" ? `Every month on day ${bill.due_day}` : bill.repeat === "yearly" ? "Every year" : "Once", bill.autopay ? "Paid automatically" : "", bill.last_paid_at ? `Last paid ${S.day(bill.last_paid_at)}` : ""].filter(Boolean).join(" · ") }),
        bill.notes ? h("p", { class: "muted small", text: bill.notes }) : null,
        h("div", { class: "btn-row" },
          bill.status !== "settled" ? S.button("Paid", async () => { await api(`/bills/${bill.id}/paid`, { method: "POST", body: {} }); Aira.toast(`${bill.name} marked paid.`, "success"); rerender(); }) : null,
          bill.last_paid_period ? S.button("Undo last payment", async () => { await api(`/bills/${bill.id}/unpaid`, { method: "POST", body: { period: bill.last_paid_period } }); rerender(); }, { kind: "ghost sm" }) : null,
          S.button("Edit", () => edit({ title: `Edit ${bill.name}`, fields: billFields(data.categories), values: bill, onSave: async (values) => { await api(`/bills/${bill.id}`, { method: "PATCH", body: values }); rerender(); } }), { kind: "ghost sm" }),
          S.button("Remove", async () => { if (await S.confirmDelete(bill.name)) { await api(`/bills/${bill.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }))))) : Aira.ui.empty("No bills yet", "Add your regular bills and Monosage reminds you before they are due.", h("div", { class: "btn-row" }, S.button("Add a bill", add, { icon: "plus" }))),
    );
  }

  // ---------------------------------------------------------------- selling and side jobs (adults)
  const SALE_TONES = { available: "info", reserved: "warn", sold: "good" };
  const SALE_LABELS = { available: "for sale", reserved: "kept for a buyer", sold: "sold" };
  const JOB_LABELS = { available: "taking jobs", paused: "paused" };
  const perUnit = (unit) => (unit === "hour" ? " an hour" : " a job");
  function contactLink(contact) {
    if (contact.includes("@")) return h("a", { href: `mailto:${contact}`, text: contact });
    return h("a", { href: `tel:${contact.replace(/[^0-9+]/g, "")}`, text: contact });
  }
  async function sellTab(body, route, rerender) {
    let status = route.query.status || "all";
    let q = String(route.query.q || "").trim().slice(0, 80);
    let data = await api(`/sell?${new URLSearchParams({ status, q })}`);
    const currency = data.currency;
    const itemFields = () => [
      { name: "title", label: "What are you selling?", required: true, placeholder: "Child's bike" },
      { name: "price_cents", label: "Price (leave empty to give it away)", type: "money" },
      { name: "condition", label: "Condition", type: "select", options: data.conditions },
      { name: "description", label: "Description", type: "textarea", rows: 3, placeholder: "Size, colour, when to collect" },
    ];
    const jobFields = () => [
      { name: "title", label: "What job do you offer?", required: true, placeholder: "Dog walking" },
      { name: "price_cents", label: "Price (leave empty to agree it each time)", type: "money" },
      { name: "price_unit", label: "The price is for", type: "select", options: data.units },
      { name: "description", label: "Description", type: "textarea", rows: 3, placeholder: "When you are free, the area you cover" },
    ];
    // Money from a sale or a job can go straight toward something the household is saving for.
    const goalField = () => (data.goals.length ? [{ name: "goal_id", label: "Put the money toward a savings goal (optional)", type: "select", options: [["", "No, just add it to the budget"], ...data.goals.map((goal) => [goal.id, goal.title])] }] : []);
    const area = h("div", { class: "suite-body" });
    const kpis = h("div");
    const drawKpis = () => fill(kpis, S.kpis([
      { label: "Earned this month", value: S.money(data.earned_this_month_cents, currency), tone: "good", hint: data.jobs_this_month_cents ? `${S.money(data.jobs_this_month_cents, currency)} from side jobs` : "" },
      { label: "Earned in total", value: S.money(data.earned_total_cents, currency), hint: [plural(data.sold, "thing") + " sold", data.jobs_done ? plural(data.jobs_done, "job") + " done" : ""].filter(Boolean).join(", ") },
      { label: "For sale", value: data.for_sale, hint: [data.asking_cents ? `Asking ${S.money(data.asking_cents, currency)}` : "", data.services ? `${plural(data.services, "side job")} offered` : ""].filter(Boolean).join(" · ") },
      { label: "Waiting for your answer", value: data.new_requests, tone: data.new_requests ? "warn" : "" },
    ]));
    // Redraw only when something changed, so a poll never resets what the person is looking at for nothing.
    let seen = JSON.stringify(data);
    let asked = 0;
    const refresh = async () => {
      const mine = ++asked;
      const fresh = await api(`/sell?${new URLSearchParams({ status, q })}`, { silent: true });
      const text = JSON.stringify(fresh);
      if (mine !== asked || text === seen) return; // a later look is on its way, or nothing changed
      data = fresh;
      seen = text;
      drawKpis();
      draw();
    };
    const act = async (path, body = {}, message = "") => { await api(path, { method: "POST", body }); if (message) Aira.toast(message, "success"); await refresh(); };
    const recordJob = (item, request) => edit({
      title: `Job done: ${item.title}`,
      fields: [
        ...(item.price_unit === "hour" ? [{ name: "hours", label: "How many hours", type: "number", min: 0, max: 744, step: "0.25", hint: item.price_cents ? `${S.money(item.price_cents, currency)} an hour.` : "" }] : []),
        { name: "amount_cents", label: "What you were paid", type: "money", hint: item.price_unit === "hour" && item.price_cents ? "Leave empty to work it out from the hours." : "Added to your budget as money in." },
        { name: "customer", label: "Who it was for", placeholder: "Mrs Patel next door" },
        { name: "day", label: "Day", type: "date" },
        ...goalField(),
      ],
      values: { amount_cents: item.price_unit === "hour" ? null : item.price_cents || null, customer: request ? request.name : "", day: cache.today, goal_id: "" },
      submitLabel: "Record the job",
      onSave: async (values) => {
        const payload = { ...values, request_id: request ? request.id : "" };
        if (payload.amount_cents === null) delete payload.amount_cents;
        if (payload.hours === null) delete payload.hours;
        const { job } = await api(`/sell/items/${item.id}/jobs`, { method: "POST", body: payload });
        Aira.toast(job.amount_cents ? `Well done! ${S.money(job.amount_cents, currency)} is in your budget.` : "Job recorded.", "success");
        await refresh();
      },
    });
    const requestRow = (item, request, actions) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
      h("div", { style: { flex: "1", minWidth: "160px" } },
        h("span", { class: "life-line-text", text: `${request.name}${request.offer_cents ? ` offers ${S.money(request.offer_cents, currency)}` : ""}` }),
        h("span", { class: "muted life-line-sub" }, contactLink(request.contact), h("span", { text: ` · ${Aira.fmt.ago(request.created_at)}` })),
        request.message ? h("span", { class: "muted life-line-sub", text: `“${request.message}”` }) : null),
      actions);
    const thingCard = (item) => h("section", { class: "life-card planner-sale", dataset: { found: item.id } },
      h("header", {}, h("h2", { text: item.title }), S.pill(SALE_LABELS[item.status] || item.status, { [SALE_LABELS[item.status]]: SALE_TONES[item.status] || "" })),
      h("p", { class: "life-line-text", text: [item.status === "sold" ? `Sold for ${S.money(item.sold_cents, currency)}` : item.price_cents ? S.money(item.price_cents, currency) : "Free", item.condition_name].join(" · ") }),
      item.description ? h("p", { class: "muted", style: { whiteSpace: "pre-wrap", margin: "0" }, text: item.description }) : null,
      item.buyer ? h("p", { class: "muted small", text: item.status === "sold" ? `Bought by ${item.buyer}${item.sold_at ? ` on ${S.day(item.sold_at)}` : ""}` : `Kept for ${item.buyer}` }) : null,
      item.status !== "sold" && item.requests.length ? h("ul", { class: "life-lines", "aria-label": `People who asked for ${item.title}` }, ...item.requests.map((request) => requestRow(item, request,
        request.status === "accepted" ? S.pill("kept for them", { "kept for them": "good" }) : h("div", { class: "btn-row" },
          S.button("Keep it for them", () => act(`/sell/requests/${request.id}/accept`, {}, `Kept for ${request.name}. Get in touch to arrange it.`), { kind: "secondary sm" }),
          S.button("No thanks", () => act(`/sell/requests/${request.id}/decline`), { kind: "ghost sm" }))))) : null,
      h("div", { class: "btn-row" },
        item.status !== "sold" ? S.button("Sold", () => edit({ title: `Sold: ${item.title}`, fields: [{ name: "sold_cents", label: "Sold for", type: "money", hint: "Added to your budget as money in." }, { name: "buyer", label: "Buyer (optional)" }, ...goalField()], values: { sold_cents: (item.requests.find((r) => r.status === "accepted") || {}).offer_cents || item.price_cents, buyer: item.buyer, goal_id: "" }, submitLabel: "Mark sold", onSave: async (values) => { await act(`/sell/items/${item.id}/sold`, { ...values, sold_cents: values.sold_cents === null ? 0 : values.sold_cents }, "Well done! The money is in your budget."); } })) : null,
        item.status === "reserved" ? S.button("Put back on sale", () => act(`/sell/items/${item.id}/available`), { kind: "ghost sm" }) : null,
        item.status === "sold" ? S.button("Not sold after all", async () => { if (await Aira.confirm({ title: "Not sold after all", message: "The money comes out of your budget (and any savings goal) and the thing is for sale again.", confirmLabel: "Put back on sale" })) await act(`/sell/items/${item.id}/unsold`); }, { kind: "ghost sm" }) : null,
        item.status !== "sold" ? S.button("Edit", () => edit({ title: `Edit ${item.title}`, fields: itemFields(), values: item, onSave: async (values) => { await api(`/sell/items/${item.id}`, { method: "PATCH", body: values }); await refresh(); } }), { kind: "ghost sm" }) : null,
        S.button("Remove", async () => { if (await S.confirmDelete(item.title)) { await api(`/sell/items/${item.id}`, { method: "DELETE" }); await refresh(); } }, { kind: "ghost sm" })));
    const jobCard = (item) => h("section", { class: "life-card planner-sale planner-job", dataset: { found: item.id } },
      h("header", {}, h("h2", { text: item.title }), S.pill(JOB_LABELS[item.status] || item.status, { "taking jobs": "info", paused: "" })),
      h("p", { class: "life-line-text", text: [item.price_cents ? `${S.money(item.price_cents, currency)}${perUnit(item.price_unit)}` : "Price agreed each time", "Side job"].join(" · ") }),
      item.description ? h("p", { class: "muted", style: { whiteSpace: "pre-wrap", margin: "0" }, text: item.description }) : null,
      h("p", { class: "muted small", text: item.jobs_count ? `${plural(item.jobs_count, "job")} done · earned ${S.money(item.earned_cents, currency)}${item.earned_this_month_cents ? ` (${S.money(item.earned_this_month_cents, currency)} this month)` : ""}` : "No jobs recorded yet." }),
      item.requests.length ? h("ul", { class: "life-lines", "aria-label": `People who asked for ${item.title}` }, ...item.requests.map((request) => requestRow(item, request, h("div", { class: "btn-row" },
        request.status === "accepted" ? S.pill("booked", { booked: "good" }) : S.button("Say yes", () => act(`/sell/requests/${request.id}/accept`, {}, `Booked ${request.name}. Get in touch to arrange it.`), { kind: "secondary sm" }),
        request.status === "accepted" ? S.button("Done and paid", () => recordJob(item, request), { kind: "secondary sm" }) : null,
        S.button("No thanks", () => act(`/sell/requests/${request.id}/decline`), { kind: "ghost sm" }))))) : null,
      item.jobs.length ? h("ul", { class: "life-lines", "aria-label": `Jobs done: ${item.title}` }, ...item.jobs.slice(0, 5).map((job) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1", minWidth: "160px" } }, h("span", { class: "life-line-text", text: `${job.customer || "A job"}: ${S.money(job.amount_cents, currency)}` }), h("span", { class: "muted life-line-sub", text: [S.day(job.day), job.hours ? `${job.hours} hours` : "", job.note].filter(Boolean).join(" · ") })),
        S.button("Undo", async () => { if (await Aira.confirm({ title: "Undo this job", message: "The money comes out of your budget (and any savings goal).", confirmLabel: "Undo job" })) { await api(`/sell/jobs/${job.id}`, { method: "DELETE" }); await refresh(); } }, { kind: "ghost sm" })))) : null,
      h("div", { class: "btn-row" },
        S.button("Record a job", () => recordJob(item, null)),
        item.status === "available" ? S.button("Pause", () => act(`/sell/items/${item.id}/pause`, {}, "Paused. It is off your page until you take jobs again."), { kind: "ghost sm" }) : S.button("Take jobs again", () => act(`/sell/items/${item.id}/available`, {}, "It is back on your page."), { kind: "ghost sm" }),
        S.button("Edit", () => edit({ title: `Edit ${item.title}`, fields: jobFields(), values: item, onSave: async (values) => { await api(`/sell/items/${item.id}`, { method: "PATCH", body: values }); await refresh(); } }), { kind: "ghost sm" }),
        S.button("Remove", async () => { if (await S.confirmDelete(item.title)) { await api(`/sell/items/${item.id}`, { method: "DELETE" }); await refresh(); } }, { kind: "ghost sm" })));
    const sellSomething = () => edit({ title: "Sell something", fields: itemFields(), values: { condition: "good" }, submitLabel: "Put it up for sale", onSave: async (values) => { await api("/sell/items", { method: "POST", body: values }); await refresh(); } });
    const offerJob = () => edit({ title: "Offer a side job", fields: jobFields(), values: { price_unit: "hour" }, submitLabel: "Offer it", onSave: async (values) => { await api("/sell/items", { method: "POST", body: { ...values, kind: "service" } }); await refresh(); } });
    hooks.sale = sellSomething;
    hooks.job = offerJob;
    const filtered = () => q || status !== "all";
    const draw = () => fill(area, data.items.length ? h("div", { class: "life-cards" }, ...data.items.map((item) => (item.kind === "service" ? jobCard(item) : thingCard(item))))
      : filtered() ? Aira.ui.empty("Nothing matches", "Try another word or show everything.", h("div", { class: "btn-row" }, S.button("Show everything", () => { q = ""; search.value = ""; status = "all"; filter.value = "all"; return refresh(); }, { kind: "secondary sm" })))
        : Aira.ui.empty("Nothing for sale yet", "Sell what the family no longer needs (outgrown clothes, toys, a pram) or offer a side job (babysitting, dog walking, gardening). Share the link with neighbours and friends; they ask, and you decide.", h("div", { class: "btn-row" }, S.button("Sell something", sellSomething, { icon: "plus" }), S.button("Offer a side job", offerJob, { kind: "secondary" }))));
    const search = h("input", { type: "search", placeholder: "Search", "aria-label": "Search things for sale", maxlength: "80", value: q, style: { flex: "1", minWidth: "0" } });
    search.addEventListener("input", debounce(() => { q = search.value.trim(); refresh().catch(() => {}); }, 250));
    const filter = Aira.ui.select([["all", "Everything"], ["available", "For sale"], ["reserved", "Kept for a buyer"], ["sold", "Sold"], ["services", "Side jobs"]], status, { "aria-label": "Show" });
    filter.addEventListener("change", () => { status = filter.value; refresh().catch(() => {}); });
    put(body,
      kpis,
      h("div", { class: "btn-row" },
        S.button("Sell something", sellSomething),
        S.button("Offer a side job", offerJob, { kind: "secondary" }),
        S.button("Download CSV", () => Aira.download("/api/planner/sell?format=csv", "things-for-sale.csv"), { kind: "ghost" })),
      S.share({ title: "Your sale page", text: "One link for everything you are selling and the side jobs you offer. Neighbours and friends see it and ask, without an account. They never see offers or other people's details.", link: data.link, onCreate: async () => (await api("/sell/link", { method: "POST", body: {} })).link, onRevoke: async () => api("/sell/link", { method: "DELETE" }) }),
      h("div", { class: "btn-row" }, S.button("Sale page title and note", () => edit({ title: "Your sale page", fields: [{ name: "title", label: "Title", placeholder: "Things for sale" }, { name: "note", label: "A note for buyers", type: "textarea", rows: 3, placeholder: "Collection from the High Street, evenings and weekends" }], values: data.settings, onSave: async (values) => { await api("/sell/settings", { method: "POST", body: values }); await refresh(); } }), { kind: "ghost sm" })),
      h("div", { class: "btn-row planner-filters" }, search, filter),
      area,
    );
    drawKpis();
    draw();
    // Stay up to date while open: a neighbour asks on the sale page, or a pop-up alert (Say yes) answers a request.
    // A pop-up closing is the moment an answer may have been given there, so look straight away; otherwise every
    // few seconds.
    live(area, () => refresh(), 8000);
    const overlay = document.getElementById("overlay");
    if (overlay) {
      const watcher = new MutationObserver((changes) => {
        if (!area.isConnected) { watcher.disconnect(); return; }
        if (changes.some((change) => change.removedNodes.length)) refresh().catch(() => {});
      });
      watcher.observe(overlay, { childList: true });
    }
    void rerender;
  }

  // ---------------------------------------------------------------- savings goals (adults)
  async function savingsTab(body, rerender) {
    const data = await api("/goals");
    const currency = data.currency;
    const goalFields = [
      { name: "title", label: "What are you saving for?", required: true, placeholder: "Summer holiday" },
      { name: "target_cents", label: "How much you need", type: "money", required: true },
      { name: "target_day", label: "Need it by (optional)", type: "date" },
    ];
    const moveMoney = (goal, takeOut) => edit({
      title: takeOut ? `Take money out: ${goal.title}` : `Put money aside: ${goal.title}`,
      fields: [{ name: "amount_cents", label: "Amount", type: "money", required: true, hint: takeOut ? `${S.money(goal.saved_cents, currency)} is put aside.` : "" }, { name: "note", label: "Note (optional)", placeholder: takeOut ? "Paid the deposit" : "From this month's salary" }],
      submitLabel: takeOut ? "Take it out" : "Put it aside",
      onSave: async (values) => {
        const { goal: after } = await api(`/goals/${goal.id}/${takeOut ? "take" : "add"}`, { method: "POST", body: values });
        if (!takeOut && after.status === "reached") Aira.toast(`You reached ${goal.title}! Well done.`, "success");
        rerender();
      },
    });
    const statusLine = (goal) => {
      if (goal.status === "reached") return "Reached. Well done!";
      const left = `${S.money(goal.left_cents, currency)} to go`;
      if (goal.status === "late") return `${left} · the day you chose (${S.day(goal.target_day)}) has passed`;
      if (goal.target_day) return `${left} · put aside about ${S.money(goal.per_month_cents, currency)} a month to have it by ${S.day(goal.target_day)}`;
      return left;
    };
    const newGoal = () => edit({ title: "Save for something", fields: goalFields, submitLabel: "Start saving", onSave: async (values) => { await api("/goals", { method: "POST", body: values }); rerender(); } });
    hooks.goal = newGoal;
    const barPercent = (goal) => Math.max(0, Math.min(100, Number(goal.percent) || 0));
    const card = (goal) => h("section", { class: "life-card planner-goal", dataset: { found: goal.id } },
      h("header", {}, h("h2", { text: goal.title }), goal.status === "reached" ? S.pill("reached", { reached: "good" }) : goal.status === "late" ? S.pill("date passed", { "date passed": "bad" }) : null),
      h("p", { class: "life-line-text", text: `${S.money(goal.saved_cents, currency)} of ${S.money(goal.target_cents, currency)} (${goal.percent}%)` }),
      h("div", { class: "planner-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(barPercent(goal)), "aria-label": `${goal.title}: ${barPercent(goal)}% saved` }, h("span", { style: { width: `${barPercent(goal)}%` } })),
      h("p", { class: "muted", text: statusLine(goal) }),
      goal.moves.length ? h("ul", { class: "life-lines", "aria-label": `Money for ${goal.title}` }, ...goal.moves.slice(0, 5).map((move) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1", minWidth: "160px" } }, h("span", { class: "life-line-text", text: `${move.amount_cents > 0 ? "+" : "-"}${S.money(Math.abs(move.amount_cents), currency)}` }), h("span", { class: "muted life-line-sub", text: [move.note, move.who, Aira.fmt.ago(move.at)].filter(Boolean).join(" · ") })),
        S.button("Undo", async () => { await api(`/goals/moves/${move.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })))) : null,
      h("div", { class: "btn-row" },
        S.button("Put money aside", () => moveMoney(goal, false), { kind: goal.status === "reached" ? "secondary" : "" }),
        goal.saved_cents > 0 ? S.button("Take some out", () => moveMoney(goal, true), { kind: "ghost sm" }) : null,
        S.button("Edit", () => edit({ title: `Edit ${goal.title}`, fields: goalFields, values: goal, onSave: async (values) => { await api(`/goals/${goal.id}`, { method: "PATCH", body: values }); rerender(); } }), { kind: "ghost sm" }),
        S.button("Remove", async () => { if (await Aira.confirm({ title: `Stop saving for ${goal.title}`, message: "The goal goes. The money you put aside is still yours to use.", confirmLabel: "Remove goal", danger: true })) { await api(`/goals/${goal.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })));
    put(body,
      data.goals.length ? S.kpis([
        { label: "Put aside", value: S.money(data.saved_cents, currency), tone: "good", hint: `of ${S.money(data.target_cents, currency)}` },
        { label: "This month", value: S.money(data.this_month_cents, currency) },
        { label: "Needed each month", value: S.money(data.per_month_cents, currency), hint: "To reach every goal on time" },
      ]) : null,
      data.goals.length ? h("div", { class: "btn-row" }, S.button("Save for something", newGoal)) : null,
      data.goals.length ? h("div", { class: "life-cards" }, ...data.goals.map(card)) : Aira.ui.empty("Nothing saved for yet", "Save for a holiday, a new bike or a rainy day. Monosage shows how much to put aside each month.", h("div", { class: "btn-row" }, S.button("Save for something", newGoal, { icon: "plus" }))),
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
    const add = () => edit({ title: "New note", fields, values: { shared: true }, submitLabel: "Save note", onSave: async (values) => { await api("/notes", { method: "POST", body: payload(values) }); rerender(); } });
    hooks.note = add;
    put(body,
      notes.length ? h("div", { class: "btn-row" }, S.button("New note", add)) : null,
      notes.length ? h("div", { class: "life-cards" }, ...notes.map((note) => h("section", { class: "life-card", dataset: { found: note.id } },
        h("header", {}, h("h2", { text: note.title || "Note" }), note.pinned ? S.pill("pinned", { pinned: "info" }) : null, note.shared ? null : S.pill("private", { private: "" })),
        note.body ? h("p", { style: { whiteSpace: "pre-wrap", margin: "0", overflowWrap: "anywhere" }, text: note.body }) : null,
        note.checklist.length ? h("ul", { class: "life-lines" }, ...note.checklist.map((item, index) => h("li", { class: `life-line ${item.done ? "done" : ""}` },
          h("button", { type: "button", class: `life-check ${item.done ? "done" : ""}`, "aria-pressed": String(item.done), "aria-label": `${item.done ? "Untick" : "Tick"} ${item.text}`, onclick: async () => {
            const checklist = note.checklist.map((entry, i) => (i === index ? { ...entry, done: !entry.done } : entry));
            await api(`/notes/${note.id}`, { method: "PATCH", body: { checklist } });
            rerender();
          } }, h("span", { "aria-hidden": "true", text: item.done ? "✓" : "" })),
          h("span", { class: "life-line-text", text: item.text })))) : null,
        h("p", { class: "muted small", text: `${note.mine ? "By you" : `By ${note.owner}`} · ${Aira.fmt.ago(note.updated_at)}` }),
        h("div", { class: "btn-row" },
          S.button("Edit", () => edit({ title: "Edit note", fields, values: { ...note, checklist_text: note.checklist.map((item) => item.text).join("\n") }, onSave: async (values) => { await api(`/notes/${note.id}`, { method: "PATCH", body: payload(values, note) }); rerender(); } }), { kind: "ghost sm" }),
          S.button("Remove", async () => { if (await S.confirmDelete("this note")) { await api(`/notes/${note.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }))))) : Aira.ui.empty("No notes yet", "Keep the wifi password, the plumber's number or a packing list here.", h("div", { class: "btn-row" }, S.button("New note", add, { icon: "plus" }))),
    );
  }

  // ---------------------------------------------------------------- rewards and pocket money
  async function pocketSection(rerender) {
    const data = await api("/pocket");
    const currency = data.currency;
    const setRate = () => edit({ title: "Pocket money", fields: [{ name: "per_100_cents", label: "100 points are worth", type: "money", hint: "For example 1.00: then a chore worth 20 points earns 0.20." }], values: { per_100_cents: data.per_100_cents || null }, onSave: async (values) => { await api("/pocket/rate", { method: "POST", body: { per_100_cents: values.per_100_cents || 0 } }); rerender(); } });
    const payOut = (person) => edit({ title: `Pocket money for ${person.name}`, fields: [{ name: "points", label: "Points to turn into money", type: "number", min: 1, max: person.available, required: true, hint: `${person.available} points can be paid out now.` }], values: { points: person.available }, submitLabel: "Pay out", onSave: async (values) => { const { payout } = await api(`/pocket/${person.profile_id}/pay`, { method: "POST", body: values }); Aira.toast(`${payout.name} gets ${S.money(payout.amount_cents, currency)}. It is in the budget under Children.`, "success"); rerender(); } });
    const earners = data.people.filter((p) => p.points > 0 || p.age_group === "child" || p.age_group === "teen");
    return S.section("Pocket money",
      h("p", { class: "muted", text: data.per_100_cents ? `100 points are worth ${S.money(data.per_100_cents, currency)}. Paying out spends the points and adds the money to the budget.` : "Turn chore points into real pocket money: set what 100 points are worth, then pay out when you hand over the money." }),
      h("div", { class: "btn-row" }, S.button(data.per_100_cents ? "Change what points are worth" : "Set up pocket money", setRate, { kind: "secondary sm" }), data.paid_this_month_cents ? h("span", { class: "muted", text: `Paid this month: ${S.money(data.paid_this_month_cents, currency)}` }) : null),
      data.per_100_cents ? S.table({
        columns: [
          { key: "name", label: "Person", render: (p) => `${p.emoji} ${p.name}` },
          { key: "available", label: "Points to pay", num: true },
          { key: "worth_cents", label: "Worth", num: true, render: (p) => S.money(p.worth_cents, currency) },
          { key: "profile_id", label: "", render: (p) => (p.available > 0 && p.worth_cents > 0 ? S.button("Pay out", () => payOut(p), { kind: "secondary sm" }) : h("span", { class: "muted", text: p.points > 0 ? "Points kept for a reward" : "Nothing to pay now" })) },
        ],
        rows: earners,
        empty: "Nobody has points yet. Children earn them by doing chores.",
      }) : null,
      data.payouts.length ? h("h3", { class: "suite-h3", text: "Paid so far" }) : null,
      data.payouts.length ? h("ul", { class: "life-lines", "aria-label": "Pocket money paid" }, ...data.payouts.slice(0, 10).map((p) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1", minWidth: "160px" } }, h("span", { class: "life-line-text", text: `${p.name}: ${S.money(p.amount_cents, currency)}` }), h("span", { class: "muted life-line-sub", text: `${p.points} points · paid by ${p.paid_by || "an adult"} · ${Aira.fmt.ago(p.at)}` })),
        S.button("Undo", async () => { if (await Aira.confirm({ title: "Undo this payout", message: `${p.name} gets the ${p.points} points back and the money leaves the budget.`, confirmLabel: "Undo payout" })) { await api(`/pocket/payouts/${p.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })))) : null);
  }

  async function rewardsTab(body, rerender) {
    const data = await api("/rewards");
    const spendable = data.balance - data.pending;
    const pending = data.requests.filter((r) => r.status === "pending");
    const mine = data.requests.filter((r) => !data.can_manage || r.profile_id === (cache.me && cache.me.id));
    put(body,
      S.kpis([
        { label: "My points", value: data.balance, tone: "good", hint: data.pending ? `${data.pending} waiting for approval` : "From chores" },
        { label: "Rewards", value: data.rewards.length },
      ]),
      !data.can_manage && data.pocket_money ? h("p", { class: "muted", text: "Your points can also become pocket money. Ask a grown-up." }) : null,
      data.can_manage && pending.length ? S.section("Waiting for your OK", h("ul", { class: "life-lines" }, ...pending.map((request) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: `${request.who}: ${request.title}` }), h("span", { class: "muted life-line-sub", text: `${request.points} points · ${Aira.fmt.ago(request.created_at)}` })),
        h("div", { class: "btn-row" },
          S.button("Approve", async () => { await api(`/requests/${request.id}/approve`, { method: "POST", body: {} }); Aira.toast("Approved.", "success"); rerender(); }),
          S.button("Not now", async () => { await api(`/requests/${request.id}/decline`, { method: "POST", body: {} }); rerender(); }, { kind: "secondary" })))))) : null,
      S.section("Rewards", data.rewards.length ? h("ul", { class: "life-lines" }, ...data.rewards.map((reward) => h("li", { class: "life-line", style: { flexWrap: "wrap" }, dataset: { found: reward.id } },
        h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: reward.title }), h("span", { class: "muted life-line-sub", text: `${reward.points} points` })),
        h("div", { class: "btn-row" },
          spendable >= reward.points ? S.button("Ask for this", async () => { await api(`/rewards/${reward.id}/request`, { method: "POST", body: {} }); Aira.toast("Asked. A grown-up will say yes or no.", "success"); rerender(); }) : h("span", { class: "muted", text: `${reward.points - Math.max(0, spendable)} more points needed` }),
          data.can_manage ? S.button("Edit", () => edit({ title: `Edit ${reward.title}`, fields: [{ name: "title", label: "Reward", required: true }, { name: "points", label: "Points", type: "number", min: 1, required: true }], values: reward, onSave: async (values) => { await api(`/rewards/${reward.id}`, { method: "PATCH", body: values }); rerender(); } }), { kind: "ghost sm" }) : null,
          data.can_manage ? S.button("Remove", async () => { if (await S.confirmDelete(reward.title)) { await api(`/rewards/${reward.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null)))) : h("p", { class: "muted", text: data.can_manage ? "Add rewards children can earn with chore points." : "No rewards yet. Ask a grown-up to add some." }),
        data.can_manage ? h("div", { class: "btn-row" }, S.button("Add a reward", () => edit({ title: "Add a reward", fields: [{ name: "title", label: "Reward", required: true, placeholder: "30 minutes extra screen time" }, { name: "points", label: "Points", type: "number", min: 1, required: true }], submitLabel: "Add", onSave: async (values) => { await api("/rewards", { method: "POST", body: values }); rerender(); } }), { kind: "secondary" })) : null),
      data.can_manage ? S.section("Everyone's points", S.table({ columns: [{ key: "name", label: "Person", render: (b) => `${b.emoji} ${b.name}` }, { key: "points", label: "Points", num: true }], rows: data.balances })) : null,
      data.can_manage ? await pocketSection(rerender) : null,
      S.grid(
        S.section("My requests", mine.length ? h("ul", { class: "life-lines" }, ...mine.map((r) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: r.title }), S.pill(r.status, { approved: "good", declined: "bad", pending: "warn" })))) : h("p", { class: "muted", text: "Nothing asked for yet." })),
        S.section("Points history", data.history.length ? h("ul", { class: "life-lines" }, ...data.history.map((entry) => h("li", { class: "life-line" }, h("span", { class: "life-line-text", text: `${entry.delta > 0 ? "+" : ""}${entry.delta}` }), h("span", { class: "muted", text: `${entry.reason} · ${Aira.fmt.ago(entry.at)}` })))) : h("p", { class: "muted", text: "Finish chores to earn points." })),
      ),
    );
  }

  // ---------------------------------------------------------------- in the Aira frame: search and "+ New"
  // Grown-ups only for selling and money (the server checks too). Before the page has loaded who is asking, the
  // household profile (Aira.life) answers.
  const grownUp = () => {
    if (cache.me) return !!cache.me.adult;
    const me = Aira.life && Aira.life.me;
    return !me || !me.age_group || ["adult", "senior"].includes(me.age_group);
  };
  const link = (tab, params = {}) => `#/planner?${new URLSearchParams({ tab, ...Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined)) })}`;
  // One request answers every kind (GET /api/planner/search); kept a few seconds so typing on does not ask again.
  const found = new Map(); // "query|limit" -> { at, promise }
  function searchAll(query, limit = 6) {
    const key = `${query.toLowerCase()}|${limit}`;
    const hit = found.get(key);
    if (hit && Date.now() - hit.at < 5000) return hit.promise;
    const promise = api(`/search?${new URLSearchParams({ q: query, limit: String(limit) })}`, { silent: true });
    promise.catch(() => found.delete(key));
    found.set(key, { at: Date.now(), promise });
    if (found.size > 30) found.delete(found.keys().next().value);
    return promise;
  }
  // What the search found, kind by kind, as rows that open the record on its tab.
  function foundRows(data) {
    const money = (cents) => S.money(cents, data.currency);
    return [
      ["tasks", "Chores", data.tasks.map((t) => ({ label: t.title, sub: ["Chore", t.done ? "done" : t.overdue ? "overdue" : t.due_date ? S.day(t.due_date) : "any time", t.assignee_name].filter(Boolean).join(" · "), icon: "pn-chores", href: link("chores", { scope: "family", done: t.done ? "1" : "", q: t.title, task: t.id }) }))],
      ["items", "Shopping", [
        ...data.lists.map((l) => ({ label: l.name, sub: "Shopping list", icon: "pn-cart", href: link("shopping", { list: l.id }) })),
        ...data.items.map((i) => ({ label: `${i.name}${i.quantity ? ` (${i.quantity})` : ""}`, sub: `${i.checked ? "In the basket" : "To buy"} · ${i.list_name}`, icon: "pn-cart", href: link("shopping", { list: i.list_id, item: i.id }) })),
      ]],
      ["recipes", "Recipes", data.recipes.map((r) => ({ label: r.title, sub: ["Recipe", r.ingredients ? plural(r.ingredients, "ingredient") : "", r.minutes ? `${r.minutes} minutes` : ""].filter(Boolean).join(" · "), icon: "pn-meal", href: link("meals", { q: r.title, recipe: r.id }) }))],
      ["notes", "Notes", data.notes.map((n) => ({ label: n.title || n.snippet || "Note", sub: ["Note", n.title ? n.snippet : "", n.shared ? "" : "private"].filter(Boolean).join(" · "), icon: "pn-notes", href: link("notes", { note: n.id }) }))],
      ["bills", "Bills", data.bills.map((b) => ({ label: b.name, sub: ["Bill", b.amount_cents ? money(b.amount_cents) : "", b.repeat === "monthly" ? "every month" : b.repeat === "yearly" ? "every year" : "once"].filter(Boolean).join(" · "), icon: "pn-bills", href: link("bills", { bill: b.id }) }))],
      ["sell", "Selling and side jobs", data.sell.map((s) => ({ label: s.title, sub: [s.kind === "service" ? "Side job" : s.status === "sold" ? "Sold" : "For sale", s.price_cents ? money(s.price_cents) : s.kind === "service" ? "" : "free"].filter(Boolean).join(" · "), icon: "pn-sell", href: link("sell", { q: s.title, sale: s.id }) }))],
      ["goals", "Savings", data.goals.map((g) => ({ label: g.title, sub: `Savings goal · ${money(g.target_cents)}`, icon: "pn-savings", href: link("savings", { goal: g.id }) }))],
      ["rewards", "Rewards", data.rewards.map((r) => ({ label: r.title, sub: `Reward · ${r.points} points`, icon: "pn-rewards", href: link("rewards", { reward: r.id }) }))],
    ].filter(([, , rows]) => rows.length);
  }

  Aira.appSearch = Aira.appSearch || {};
  Aira.appSearch.planner = {
    placeholder: "Search chores, shopping, recipes and notes",
    // Up to six, one of each kind in turn, so a long shopping list does not hide the chores and notes.
    async suggest(query) {
      const text = query.trim();
      if (!text) return [];
      const lanes = foundRows(await searchAll(text)).map(([, , rows]) => rows);
      const out = [];
      for (let round = 0; out.length < 6 && lanes.some((lane) => lane.length > round); round += 1) for (const lane of lanes) if (lane[round] && out.length < 6) out.push(lane[round]);
      return out;
    },
    // Enter: everything that matches, kind by kind.
    run(query) { Aira.router.go(["planner"], { tab: "search", q: query.trim() }); },
  };

  // "+ New": what a household makes most here, each opening the same form as the button on its tab.
  Aira.appNew = Aira.appNew || {};
  Aira.appNew.planner = () => [
    { label: "New task", icon: "pn-chores", href: link("chores", { new: "task" }) },
    { label: "Add to the shopping list", icon: "pn-cart", href: link("shopping", { new: "item" }) },
    { label: "Plan a meal", icon: "pn-meal", href: link("meals", { new: "meal" }) },
    { label: "New note", icon: "pn-notes", href: link("notes", { new: "note" }) },
    grownUp() ? { label: "Sell something", icon: "pn-sell", href: link("sell", { new: "sale" }) } : null,
  ].filter(Boolean);
  const NEW_TAB = { task: "chores", item: "shopping", meal: "meals", recipe: "meals", note: "notes", sale: "sell", job: "sell", bill: "bills", goal: "savings" };
  const FOUND_TAB = { task: "chores", item: "shopping", recipe: "meals", note: "notes", bill: "bills", sale: "sell", goal: "savings", reward: "rewards" };

  // Enter in the search box: every match, kind by kind, each opening its record.
  async function searchTab(body, route) {
    const q = String(route.query.q || "").trim().slice(0, 80);
    if (!q) { put(body, Aira.ui.empty("Search Planner", "Type in the search box at the top: chores, shopping, recipes, notes and more.")); return; }
    const data = await api(`/search?${new URLSearchParams({ q, limit: "25" })}`);
    const groups = foundRows(data);
    const row = (r) => h("li", { class: "life-line" }, h("a", { class: "planner-hit", href: r.href },
      h("span", { class: "planner-hit-icon", "aria-hidden": "true" }, Aira.icon(r.icon)),
      h("span", { class: "planner-hit-text" }, h("span", { class: "life-line-text", text: r.label }), h("span", { class: "muted life-line-sub", text: r.sub }))));
    put(body,
      groups.length ? h("p", { class: "muted", text: ((n) => `${n} ${n === 1 ? "match" : "matches"} for “${q}”.`)(groups.reduce((sum, [, , rows]) => sum + rows.length, 0)) }) : null,
      ...(groups.length ? groups.map(([, title, rows]) => S.section(`${title} (${rows.length})`, h("ul", { class: "life-lines" }, ...rows.map(row))))
        : [Aira.ui.empty(`Nothing in Planner matches “${q}”`, "Try another word, or add it now.", h("div", { class: "btn-row" },
          h("a", { class: "btn", href: link("chores", { new: "task" }), text: "New task" }),
          h("a", { class: "btn secondary", href: link("shopping", { new: "item" }), text: "Add to the shopping list" })))]),
    );
  }

  // After the tab is drawn: open the form "+ New" asked for, or show the record the search box chose.
  function answer(panel, ask) {
    if (!ask) return;
    if (ask.new) {
      const make = hooks[ask.new];
      if (make) make();
      return;
    }
    const node = panel.querySelector(`[data-found="${CSS.escape(ask.id)}"]`);
    if (!node) { Aira.toast("That is not here any more.", "info"); return; }
    node.classList.add("planner-found");
    node.tabIndex = -1;
    node.scrollIntoView({ block: "center" });
    node.focus({ preventScroll: true });
    node.addEventListener("blur", () => node.classList.remove("planner-found"), { once: true });
  }

  // ---------------------------------------------------------------- the page
  Aira.views.planner = async (main, route) => {
    // "+ New" and the search ask with ?new= or a record's id (?task=, ?item=...): remember the ask, take it out of
    // the address (Back or a refresh must not ask again) and draw the tab without it; the drawn tab then answers it.
    const query = route.query || {};
    const key = ["new", ...FOUND_KEYS].find((name) => query[name]);
    if (key) {
      pending = key === "new" ? { new: query.new } : { id: query[key] };
      const rest = { ...query };
      for (const name of ["new", ...FOUND_KEYS]) delete rest[name];
      if (!rest.tab) rest.tab = key === "new" ? NEW_TAB[query.new] || "day" : FOUND_TAB[key];
      Aira.router.go(["planner"], rest, { replace: true });
      return;
    }
    const ask = pending;
    pending = null;
    let fresh;
    try { fresh = await loadBasics(); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    const me = cache.me;
    const tabs = TABS.filter((t) => !t.adult || me.adult);
    const searching = query.tab === "search";
    const current = searching ? { id: "search", label: "Search results", about: query.q ? `Everything in Planner that matches “${query.q}”.` : "" } : tabs.find((t) => t.id === query.tab) || tabs[0];
    const body = S.page(main, { title: current.label, subtitle: current.about });
    body.classList.add("planner");
    const tab = S.tabs(body, route, "planner", tabs, current.id);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    let first = true;
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      // Each tab sets its own create actions; a task can be added from any of them.
      hooks = { task: () => newTask(render) };
      try {
        if (tab === "search") await searchTab(holder, route);
        else if (tab === "chores") await choresTab(holder, route, render);
        else if (tab === "shopping") await shoppingTab(holder, route);
        else if (tab === "meals") await mealsTab(holder, route, render);
        else if (tab === "budget") await budgetTab(holder, route, render);
        else if (tab === "savings") await savingsTab(holder, render);
        else if (tab === "bills") await billsTab(holder, render);
        else if (tab === "sell") await sellTab(holder, route, render);
        else if (tab === "notes") await notesTab(holder, render);
        else if (tab === "rewards") await rewardsTab(holder, render);
        else { await dayTab(holder, render, fresh); fresh = null; }
        panel.replaceChildren(holder);
        if (first) { first = false; answer(panel, ask); }
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    panel.replaceChildren(Aira.ui.skeleton(4));
    await render();
  };
})();

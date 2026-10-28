/* Aira — Academy (#/academy): sell courses and teach. The Courses section of the Courses & Events app.
 *
 * Tabs (?tab=): overview (students, activity, completion, revenue, rating, setup, the catalog page), courses (the
 * list; ?course=<id> opens the editor: details, publishing, the public course page, the curriculum builder with
 * lessons and quizzes, and an outline drafted by the writing model), students (enrolments with progress,
 * payments, links, CSV), cohorts, announcements, reviews (students' ratings; hide one), partners and earnings.
 * Everything runs through /api/academy/* (aira/suite/academy/academy_routes.py).
 *
 * Shared with Events (views-events.js) through Aira.xp: the Partners tab (people who share your pages with their
 * own link and earn a commission), the Earnings tab (every course and event from the top earner to the lowest,
 * with a payment log), the catalog page card and the search results page. #/experiences (the app's own id) opens
 * Courses.
 *
 * In the Aira frame (shell.js) this file plugs the app in for both sections: "+ New" (a course, a course drafted
 * with AI, an event, a student, a partner), the search box at the top (one request to /api/academy/search finds
 * courses, events, students, ticket orders and partners; Enter opens ?tab=search&q=) and icons for the tabs the
 * left menu shows as folders. "+ New" and a search result ask through the address (?new=<kind>, ?open=<kind>:<id>);
 * the view takes the ask out of the address at once (Back and refresh must not ask again) and answers it once the
 * tab is drawn. DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("academy", path, opts);

  // Icons for the folders that no shared icon fits.
  Object.assign(Aira.ICONS, {
    xpStar: '<svg viewBox="0 0 24 24"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
    xpMoney: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9.2c-.5-.8-1.6-1.3-3-1.3-1.7 0-3 .8-3 2s1.3 1.6 3 1.9 3 .9 3 2.1-1.3 2.1-3 2.1c-1.4 0-2.5-.5-3-1.4M12 6v12"/></svg>',
    xpPartner: '<svg viewBox="0 0 24 24"><circle cx="8" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M2.5 19c.6-3 2.8-5 5.5-5s4.9 2 5.5 5M14 14.6c.9-.4 1.9-.6 3-.6 2.3 0 4 1.6 4.5 4.5"/></svg>',
    xpTag: '<svg viewBox="0 0 24 24"><path d="M3 12.2V4h8.2L21 13.8 13.8 21z"/><circle cx="7.6" cy="8.6" r="1.4"/></svg>',
    xpClock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/></svg>',
    xpRibbon: '<svg viewBox="0 0 24 24"><circle cx="12" cy="9" r="6"/><path d="m8.6 14-1.6 7 5-2.8 5 2.8-1.6-7"/></svg>',
    xpCheck: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="m8.5 12 2.4 2.4 4.6-4.8"/></svg>',
  });

  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard", title: "Courses overview", about: "Students, money and what to do next across your courses." },
    { id: "courses", label: "Courses", icon: "academy", about: "Every course you teach: open one to edit its lessons, quizzes and page." },
    { id: "students", label: "Students", icon: "recruiters", about: "Who is enrolled, how far they got and who still has to pay." },
    { id: "cohorts", label: "Cohorts", icon: "family", about: "Groups of students who start together: a batch, a class or a live programme." },
    { id: "announcements", label: "Announcements", icon: "marketing", about: "Messages to a course's students, waiting in the outbox for your approval." },
    { id: "reviews", label: "Reviews", icon: "xpStar", about: "What students say about your courses." },
    { id: "partners", label: "Partners", icon: "xpPartner", about: "People who share your courses and events with their own link and earn a commission." },
    { id: "earnings", label: "Earnings", icon: "xpMoney", about: "What every course and event earned, from the top earner to the lowest." },
  ];
  const LEVELS = [["all", "All levels"], ["beginner", "Beginner"], ["intermediate", "Intermediate"], ["advanced", "Advanced"]];
  const STUDENT_STATUSES = [["", "Any status"], ["pending_payment", "Waiting for payment"], ["active", "Learning"], ["completed", "Completed"], ["refunded", "Refunded"]];
  const TONES = { pending_payment: "warn", active: "info", completed: "good", refunded: "bad", published: "good", draft: "", archived: "", hidden: "" };
  const go = (query) => Aira.router.go(["academy"], query);
  const toolbar = (...children) => h("div", { class: "btn-row xp-toolbar" }, ...children.filter(Boolean));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const stars = (rating) => {
    const value = Math.max(0, Math.min(5, Math.round(Number(rating) || 0)));
    return h("span", { class: "xp-stars", "aria-label": `${rating} out of 5`, text: "★".repeat(value) + "☆".repeat(5 - value) });
  };
  const ratingText = (rating) => (rating && rating.count ? `${rating.average} of 5 (${plural(rating.count, "review")})` : "No reviews yet");

  // ------------------------------------------------------------------ shared by Courses and Events (Aira.xp)
  const xp = (Aira.xp = Aira.xp || {});

  // Whether this person can open the app at all: a child's household profile never can (the server refuses it), nor
  // anyone while the organization has the app off or their role leaves it out (the page then says why).
  xp.canOpen = () => {
    const me = Aira.life && Aira.life.me;
    if (me && me.age_group === "child") return false;
    return !(Aira.apps && typeof Aira.apps.blocked === "function" && Aira.apps.blocked("academy"));
  };
  // Who may create here: in the hosted build an auditor or a billing contact only reads it (aira_cloud/rbac.py:
  // suite.manage).
  xp.canManage = () => {
    if (!xp.canOpen()) return false;
    const cloud = Aira.cloud;
    return !(cloud && cloud.me && typeof cloud.can === "function" && !cloud.can("suite.manage"));
  };

  // S.table, with the columns marked `wide: true` left out on a phone (their facts ride under the first column
  // instead), so a list never needs sideways scrolling to read.
  xp.table = (options) => {
    const node = S.table(options);
    const wide = options.columns.map((column) => Boolean(column.wide));
    if (wide.some(Boolean)) for (const row of node.querySelectorAll("tr")) [...row.children].forEach((cell, i) => { if (wide[i]) cell.classList.add("xp-wide"); });
    return node;
  };
  // The line under a row's title that only a phone shows (the facts of its wide columns).
  xp.phoneMeta = (...parts) => {
    const text = parts.filter((part) => part !== "" && part !== null && part !== undefined).join(" · ");
    return text ? h("div", { class: "muted small xp-phone-only", text }) : null;
  };

  // The page head, named after the open folder (the app's name is already in the bar above), with the section's
  // main create on a phone, where the menu and its "+ New" are folded away.
  xp.head = (main, { title, about, phoneNew }) => {
    const body = S.page(main, { title, subtitle: about, actions: [phoneNew && xp.canManage() ? phoneNew : null] });
    const action = main.querySelector(".page-head .btn-row");
    if (action) action.classList.add("xp-narrow-new");
    return body;
  };
  xp.setTitle = (main, title, about) => {
    const heading = main.querySelector(".page-head h1");
    if (heading) heading.textContent = title;
    const line = main.querySelector(".page-head p");
    if (line && about !== undefined) line.textContent = about;
    document.title = `${title} · Courses & Events · Monosage`;
  };

  // "+ New" and a search result ask through the address: ?new=<kind> or ?open=<kind>:<id>. take() keeps the ask,
  // puts the address back without it (replace: Back or a refresh must not ask again) and says the page will be
  // drawn again; the drawn page then calls answer().
  let pending = null;
  xp.take = (section, route, tabFor) => {
    if (!route.query.new && !route.query.open) return false;
    const [kind, id] = String(route.query.open || "").split(":");
    pending = route.query.new ? { section, new: route.query.new } : { section, open: kind, id };
    const rest = { ...route.query };
    delete rest.new;
    delete rest.open;
    rest.tab = tabFor(pending) || rest.tab || "";
    Aira.router.go([section], rest, { replace: true });
    return true;
  };
  xp.answer = (section, handlers) => {
    const ask = pending && pending.section === section ? pending : null;
    pending = null;
    if (!ask) return;
    const run = ask.new ? handlers.new && handlers.new[ask.new] : handlers.open && handlers.open[ask.open];
    if (run && (ask.open || xp.canManage())) run(ask.id);
  };

  const courseFields = [
    { name: "title", label: "Title", required: true, placeholder: "Python for beginners" },
    { name: "subtitle", label: "Subtitle", placeholder: "Write real programs in four weeks" },
    { name: "description", label: "Description", type: "textarea", rows: 5, hint: "What students will learn and who it is for." },
    { name: "level", label: "Level", type: "select", options: LEVELS },
    { name: "price_cents", label: "Price", type: "money", hint: "0 for a free course." },
    { name: "cover_url", label: "Cover image link", type: "url", placeholder: "https://.../cover.jpg" },
    { name: "enrol_code", label: "Enrolment code", hint: "Optional. Students must type it to enrol (for a class or a partner)." },
    { name: "capacity", label: "Seats", type: "number", min: 1, hint: "Optional. Leave empty for no limit." },
  ];

  async function coursesList() {
    return (await api("/courses")).courses;
  }

  // The one page listing every published course and upcoming event, for a bio, a poster or a partner.
  xp.catalogCard = async () => {
    const data = await api("/catalog");
    return S.share({
      title: "Your catalog page",
      text: `One page with all your courses and upcoming events (${plural(data.courses.length, "course")}, ${plural(data.events.length, "event")} on it now). Put it in your bio, on posters and in emails.`,
      link: data.link,
      onCreate: async () => (await api("/catalog/link", { method: "POST", body: {} })).link,
      onRevoke: () => api("/catalog/link", { method: "DELETE" }),
    });
  };

  const partnerFields = [
    { name: "name", label: "Name", required: true, placeholder: "Priya (yoga teacher), Campus Club, Radio host" },
    { name: "email", label: "Email", type: "email", hint: "Their partner page and payout notes are emailed here." },
    { name: "commission_percent", label: "Commission (%)", type: "number", min: 0, max: 90, hint: "Their share of what each referral pays." },
    { name: "code", label: "Code", placeholder: "Made for you when empty", hint: "Letters and digits. It goes in their links: ?ref=CODE" },
    { name: "notes", label: "Notes", type: "textarea", rows: 2, hint: "Only you see these." },
    { name: "active", label: "Active (their links count)", type: "checkbox" },
  ];
  const REFERRAL_TONES = { pending: "warn", earned: "info", paid: "good", void: "" };

  async function editPartner(partner) {
    return S.edit({
      title: partner ? `Edit ${partner.name}` : "New partner",
      fields: partnerFields,
      values: partner ? { ...partner } : { commission_percent: 10, active: true },
      submitLabel: partner ? "Save" : "Add partner",
      onSave: async (values) => {
        const body = { ...values, commission_percent: values.commission_percent === null ? 10 : values.commission_percent };
        return partner ? (await api(`/partners/${partner.id}`, { method: "PATCH", body })).partner : (await api("/partners", { method: "POST", body })).partner;
      },
    });
  }

  const refUrl = (path, code) => `${S.absolute(path)}?ref=${encodeURIComponent(code)}`;

  async function partnerDrawer(id, refresh) {
    const data = await api(`/partners/${id}`);
    const p = data.partner;
    const reopen = () => { Aira.closeDrawer(); refresh(); partnerDrawer(id, refresh); };
    const links = data.share_links.length
      ? h("ul", { class: "xp-links" }, ...data.share_links.map((item) => {
        const url = refUrl(item.path, p.code);
        return h("li", {}, h("div", { class: "xp-link-text" }, h("strong", { text: `${item.type === "course" ? "Course" : "Event"}: ${item.title}` }), h("span", { class: "muted xp-link-url", text: url })), S.button("Copy", () => S.copy(url), { kind: "ghost sm" }));
      }))
      : h("p", { class: "muted", text: "Publish a course or an event first: their pages are what partners share." });
    Aira.drawer({
      title: p.name,
      subtitle: `${p.code} · ${p.commission_percent}% commission`,
      body: h("div", { class: "suite-body" },
        h("div", { class: "btn-row" }, S.pill(p.active ? "active" : "off", { off: "" }), p.email ? h("span", { class: "muted", text: p.email }) : null),
        S.kpis([
          { label: "Referrals", value: p.referrals },
          { label: "Sales from them", value: S.money(p.sales_cents) },
          { label: "Owed to them", value: S.money(p.owed_cents), tone: p.owed_cents ? "warn" : "", hint: p.pending_cents ? `${S.money(p.pending_cents)} more once paid` : "" },
          { label: "Paid to them", value: S.money(p.paid_cents), tone: "good" },
        ]),
        h("div", { class: "btn-row" },
          p.owed_cents ? S.button(`Record payout (${S.money(p.owed_cents)})`, async () => {
            const done = await S.edit({ title: `Pay ${p.name}`, fields: [{ name: "note", label: "Note", placeholder: "Bank transfer, 14 Nov", hint: "Pay them yourself first; this records it and marks their commission paid." }], submitLabel: "Record payout", onSave: (values) => api(`/partners/${id}/payout`, { method: "POST", body: values }) });
            if (done) { Aira.toast(`Payout of ${S.money(done.payout.amount_cents)} recorded.`, "success"); reopen(); }
          }) : null,
          p.active ? S.button("Email them their page", async () => { await api(`/partners/${id}/invite`, { method: "POST", body: {} }); Aira.toast("Their page link is waiting in Business hub > Outbox.", "success"); reopen(); }, { kind: "secondary" }) : null,
          S.button("Edit", async () => { if (await editPartner(p)) reopen(); }, { kind: "secondary" }),
          // Any history (refunded referrals too) means the partner is switched off, never deleted.
          (p.active || !(p.history ?? p.referrals)) ? S.button((p.history ?? p.referrals) ? "Switch off" : "Delete", async () => {
            const keep = Boolean(p.history ?? p.referrals);
            if (await Aira.confirm({ title: keep ? "Switch off this partner" : "Delete this partner", message: keep ? "Their links stop counting and their page closes. Their history and anything owed stay." : "Delete this partner?", confirmLabel: keep ? "Switch off" : "Delete", danger: true })) {
              const done = await api(`/partners/${id}`, { method: "DELETE" });
              Aira.toast(done && done.deactivated ? `${p.name} is switched off. Their history stays.` : `${p.name} is deleted.`, "success");
              Aira.closeDrawer(); refresh();
            }
          }, { kind: "ghost" }) : null),
        p.active ? S.share({ title: "Their partner page", text: "Their links and what they have earned, without your customers' names. Send it to them.", link: data.link, onCreate: async () => (await api(`/partners/${id}/link`, { method: "POST", body: {} })).link, onRevoke: () => api(`/partners/${id}/link`, { method: "DELETE" }) }) : null,
        S.section("Their links", h("p", { class: "muted", text: "Anyone who enrols or buys tickets through these links counts as their referral." }), links),
        S.section("Referrals", S.table({
          columns: [
            { key: "created_at", label: "When", render: (r) => S.when(r.created_at) },
            { key: "label", label: "For" },
            { key: "customer", label: "Customer" },
            { key: "amount_cents", label: "Sale", num: true, render: (r) => (r.amount_cents ? S.money(r.amount_cents) : "Free") },
            { key: "commission_cents", label: "Commission", num: true, render: (r) => S.money(r.commission_cents) },
            { key: "status", label: "Status", render: (r) => S.pill(r.status === "void" ? "refunded" : r.status === "pending" ? "waiting for payment" : r.status, { ...REFERRAL_TONES, refunded: "", "waiting for payment": "warn" }) },
          ],
          rows: data.referrals,
          empty: "No referrals yet. Share their links with them.",
        })),
        data.payouts.length ? S.section("Payouts", S.table({ columns: [{ key: "created_at", label: "When", render: (r) => S.when(r.created_at) }, { key: "amount_cents", label: "Amount", num: true, render: (r) => S.money(r.amount_cents) }, { key: "referrals", label: "Referrals", num: true }, { key: "note", label: "Note" }], rows: data.payouts })) : null),
    });
  }

  xp.openPartner = (id, refresh) => partnerDrawer(id, refresh);
  xp.newPartner = () => editPartner();

  // The Partners tab: people who share your pages with their own link and earn a commission.
  xp.partnersTab = async (body, route, goTab) => {
    const q = route.query.q || "";
    const data = await api(`/partners${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    const t = data.totals;
    const refresh = () => goTab({ tab: "partners", q, t: Date.now() });
    const search = h("input", { type: "search", placeholder: "Name, email or code", value: q, "aria-label": "Search partners", "data-search": "1", onkeydown: (event) => { if (event.key === "Enter") goTab({ tab: "partners", q: search.value.trim() }); } });
    body.append(
      h("p", { class: "muted", text: "Partners share your courses and events with their own link: teachers, clubs, creators, past students. When someone they send pays, they earn a commission. You pay them yourself; Monosage keeps the count and gives each partner a page with their links and earnings." }),
      S.kpis([
        { label: "Active partners", value: t.partners },
        { label: "Referrals", value: t.referrals },
        { label: "Sales from partners", value: S.money(t.sales_cents), tone: "good" },
        { label: "Commission owed", value: S.money(t.owed_cents), tone: t.owed_cents ? "warn" : "", hint: t.pending_cents ? `${S.money(t.pending_cents)} more once paid` : "" },
        { label: "Commission paid", value: S.money(t.paid_cents) },
      ]),
      toolbar(search,
        S.button("New partner", async () => { const created = await editPartner(); if (created) { refresh(); partnerDrawer(created.id, refresh); } }),
        S.button("Download CSV", () => Aira.download("/api/academy/partners?format=csv", "partners.csv"), { kind: "secondary" })),
      xp.table({
        columns: [
          { key: "name", label: "Partner", render: (p) => h("div", {}, h("strong", { text: p.name }), p.email ? h("div", { class: "muted small", text: p.email }) : null, xp.phoneMeta(p.code, `${p.commission_percent}%`, plural(p.referrals, "referral"))) },
          { key: "code", label: "Code", wide: true },
          { key: "commission_percent", label: "Commission", num: true, wide: true, render: (p) => `${p.commission_percent}%` },
          { key: "referrals", label: "Referrals", num: true, wide: true },
          { key: "sales_cents", label: "Sales", num: true, wide: true, render: (p) => S.money(p.sales_cents) },
          { key: "owed_cents", label: "Owed", num: true, render: (p) => S.money(p.owed_cents) },
          { key: "paid_cents", label: "Paid", num: true, wide: true, render: (p) => S.money(p.paid_cents) },
          { key: "active", label: "Status", render: (p) => S.pill(p.active ? "active" : "off", { off: "" }) },
        ],
        rows: data.partners,
        onRow: (p) => partnerDrawer(p.id, refresh),
        empty: Aira.ui.empty(q ? "No partner matches" : "No partners yet", q ? "Try another name or code." : "Add someone who can send you students or guests. They get their own links and a page that shows what they earned.", q ? null : S.button("Add a partner", async () => { const created = await editPartner(); if (created) { refresh(); partnerDrawer(created.id, refresh); } })),
      }),
    );
  };

  const rankList = (rows, total, low) => h("ol", { class: `xp-rank ${low ? "low" : ""}`.trim() }, ...rows.map((row) => h("li", {},
    h("span", { class: "xp-rank-title" }, h("strong", { text: row.title }), h("span", { class: "muted small", text: ` · ${row.kind === "course" ? "course" : row.kind === "event" ? "event" : "sponsors"}` })),
    h("strong", { text: S.money(row.revenue_cents) }),
    h("span", { class: "xp-share", "aria-hidden": "true" }, h("span", { style: { width: `${total ? Math.max(2, Math.round((row.revenue_cents * 100) / total)) : 0}%` } })))));

  // The Earnings tab: what every course and event earned, top to lowest, and every payment.
  xp.earningsTab = async (body, route, goTab) => {
    const data = await api("/earnings");
    const t = data.totals;
    const money = (cents) => S.money(cents, data.currency);
    const kind = route.query.kind || "";
    const rows = data.items.filter((row) => !kind || row.kind === kind);
    const top = data.top.length ? data.top[0].revenue_cents : 0;
    const kindSelect = Aira.ui.select([["", "Courses and events"], ["course", "Courses only"], ["event", "Events only"]], kind, { "aria-label": "Show", onchange: () => goTab({ tab: "earnings", kind: kindSelect.value }) });
    const months = data.by_month.map((m) => ({ label: new Date(`${m.month}-15T00:00:00`).toLocaleDateString([], { month: "short" }), value: m.cents }));
    body.append(
      S.kpis([
        { label: "This month", value: money(t.this_month_cents), tone: "good", hint: t.change_percent === null ? `${money(t.last_month_cents)} last month` : `${t.change_percent >= 0 ? "+" : ""}${t.change_percent}% on last month` },
        { label: "All time", value: money(t.total_cents), hint: `Courses ${money(t.courses_cents)} · Events ${money(t.events_cents)}` },
        { label: "Refunded", value: money(t.refunded_cents), tone: t.refunded_cents ? "warn" : "" },
        { label: "Partner commission", value: money(t.commission_cents), hint: t.commission_owed_cents ? `${money(t.commission_owed_cents)} still to pay` : "" },
        { label: "You keep", value: money(t.kept_cents), tone: "good", hint: "All time, after partner commission" },
      ]),
      S.grid(
        S.section("Earning the most", data.top.length ? rankList(data.top, top, false) : Aira.ui.empty("Nothing earned yet", "Money shows here when you mark an enrolment or an order paid.")),
        S.section("Earning the least", data.lowest.length ? h("div", {}, rankList(data.lowest, top, true), h("p", { class: "muted small", text: "Give these a push: share the page again, add a promo code, ask a partner to share it or send an announcement." })) : Aira.ui.empty(data.top.length ? "Nothing else to compare yet" : "Nothing on sale yet", data.top.length ? "Everything on sale is in the list of top earners." : "Publish a course or an event.")),
      ),
      S.section("Last six months", S.bars(months, { format: money })),
      S.section("Every course and event",
        toolbar(kindSelect, S.button("Download CSV", () => Aira.download("/api/academy/earnings?format=csv", "earnings.csv"), { kind: "secondary" })),
        xp.table({
          columns: [
            { key: "title", label: "Course or event", render: (row) => h("div", {}, h("strong", { text: row.title }), h("div", { class: "muted small", text: row.kind === "course" ? "Course" : row.kind === "event" ? "Event" : "Sponsors" }), xp.phoneMeta(`${money(row.this_month_cents)} this month`, plural(row.sales, "payment"))) },
            { key: "status", label: "Status", wide: true, render: (row) => (row.status ? S.pill(row.status, TONES) : "") },
            { key: "revenue_cents", label: "Earned", num: true, render: (row) => money(row.revenue_cents) },
            { key: "this_month_cents", label: "This month", num: true, wide: true, render: (row) => money(row.this_month_cents) },
            { key: "sales", label: "Payments", num: true, wide: true },
            { key: "refunds", label: "Refunds", num: true, wide: true, render: (row) => (row.refunds ? `${row.refunds} (${money(row.refunded_cents)})` : "0") },
            { key: "commission_cents", label: "Commission", num: true, wide: true, render: (row) => money(row.commission_cents) },
          ],
          rows,
          onRow: (row) => { if (row.kind === "course") Aira.router.go(["academy"], { tab: "courses", course: row.id }); else if (row.kind === "event") Aira.router.go(["events"], { tab: "events", event: row.id }); },
          empty: Aira.ui.empty("Nothing to show", "Create a course or an event to start earning."),
        })),
      S.section("Payment log", h("p", { class: "muted", text: "Every payment you marked received, newest first. A refund stays here, marked refunded, and stops counting." }), xp.table({
        columns: [
          { key: "at", label: "When", render: (row) => h("div", {}, h("span", { text: S.when(row.at) }), xp.phoneMeta(row.label, row.customer)) },
          { key: "app", label: "From", wide: true, render: (row) => (row.app === "academy" ? "Courses" : row.source_type === "sponsor" ? "Sponsor" : "Events") },
          { key: "label", label: "For", wide: true },
          { key: "customer", label: "Customer", wide: true },
          { key: "amount_cents", label: "Amount", num: true, render: (row) => money(row.amount_cents) },
          { key: "refunded", label: "", render: (row) => (row.refunded ? S.pill("refunded") : "") },
        ],
        rows: data.log,
        empty: "No payments yet",
      })),
    );
  };

  // ------------------------------------------------------------------ overview
  async function overview(body) {
    const [data, catalog] = await Promise.all([api("/overview"), xp.catalogCard()]);
    const money = (cents) => S.money(cents, data.currency);
    const steps = data.checklist || [];
    const remind = data.inactive ? S.button(`Remind ${data.inactive} inactive student${data.inactive === 1 ? "" : "s"}`, async () => {
      const result = await api("/nudges", { method: "POST", body: {} });
      Aira.toast(`${result.queued} reminders are waiting in the outbox (Business hub).`, "success");
    }, { kind: "secondary" }) : null;
    body.append(
      S.kpis([
        { label: "Students", value: data.students, hint: `${data.completed} completed` },
        { label: "Active this week", value: data.active_this_week },
        { label: "Completion rate", value: `${data.completion_rate}%`, tone: data.completion_rate >= 50 ? "good" : "" },
        { label: "Revenue this month", value: money(data.revenue_this_month_cents), hint: `${money(data.revenue_total_cents)} all time`, tone: "good" },
        { label: "Waiting for payment", value: data.pending_payments, tone: data.pending_payments ? "warn" : "" },
        { label: "Rating", value: data.rating && data.rating.count ? `${data.rating.average} / 5` : "-", hint: data.rating && data.rating.count ? plural(data.rating.count, "review") : "No reviews yet" },
      ]),
      catalog,
      S.grid(
        steps.some((step) => !step.done) ? S.section("Get started", S.checklist(steps)) : null,
        S.section("Top courses", remind ? h("div", { class: "btn-row" }, remind) : null,
          xp.table({
            columns: [
              { key: "title", label: "Course", render: (row) => h("div", {}, h("span", { text: row.title }), xp.phoneMeta(`${row.completed || 0} completed`)) },
              { key: "status", label: "Status", render: (row) => S.pill(row.status, TONES) },
              { key: "students", label: "Students", num: true },
              { key: "completed", label: "Completed", num: true, wide: true, render: (row) => row.completed || 0 },
            ],
            rows: data.top_courses,
            empty: Aira.ui.empty("No courses yet", "Create a course, or let Monosage draft an outline from a topic.", S.button("Create a course", () => newCourse())),
            onRow: (row) => go({ tab: "courses", course: row.id }),
          })),
        S.section("Recent activity", S.activity(data.activity)),
      ),
    );
  }

  // ------------------------------------------------------------------ courses
  async function newCourse(values = {}) {
    const created = await S.edit({ title: "New course", fields: courseFields, values: { level: "all", price_cents: 0, ...values }, submitLabel: "Create course", onSave: async (form) => (await api("/courses", { method: "POST", body: clean(form) })).course });
    if (created) go({ tab: "courses", course: created.id });
  }

  function clean(values) {
    const out = { ...values };
    if (out.capacity === null || out.capacity === "" || Number.isNaN(out.capacity)) out.capacity = null;
    if (out.price_cents === null) out.price_cents = 0;
    return out;
  }

  async function outlineWizard() {
    const form = S.form([
      { name: "topic", label: "Topic", required: true, placeholder: "Digital marketing for small shops" },
      { name: "level", label: "Level", type: "select", options: LEVELS },
      { name: "price_cents", label: "Price", type: "money", hint: "0 for free" },
    ], { level: "all", price_cents: 0 });
    const preview = h("div", { class: "suite-body" });
    let outline = null;
    Aira.modal({
      title: "Draft a course with AI",
      wide: true,
      body: h("div", { class: "suite-body" }, h("p", { class: "muted", text: "Monosage drafts modules and lessons from your topic. You edit everything before students see it." }), form.node, preview),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Draft outline", kind: "secondary", onClick: async () => {
          const values = form.read();
          if (!values.topic) { form.setError("Enter a topic."); return false; }
          form.setError("");
          preview.replaceChildren(h("p", { class: "muted", role: "status", text: "Drafting… this can take a little while." }));
          try { outline = await api("/outline-draft", { method: "POST", body: { topic: values.topic, level: values.level } }); } catch (error) { preview.replaceChildren(); form.setError(error.message); return false; }
          preview.replaceChildren(
            h("p", { class: "muted small", text: outline.source === "model" ? "Drafted by the writing model." : "A starting template (no writing model is running). Edit it to fit." }),
            ...outline.modules.map((module) => h("div", {}, h("strong", { text: module.title }), h("ul", {}, ...module.lessons.map((lesson) => h("li", { text: lesson.title }))))),
          );
          return false;
        } },
        { label: "Create course", kind: "primary", onClick: async () => {
          const values = form.read();
          if (!values.topic) { form.setError("Enter a topic."); return false; }
          try {
            if (!outline) outline = await api("/outline-draft", { method: "POST", body: { topic: values.topic, level: values.level } });
            const course = (await api("/courses", { method: "POST", body: { title: values.topic, level: values.level, price_cents: values.price_cents || 0 } })).course;
            await api(`/courses/${course.id}/outline`, { method: "POST", body: { modules: outline.modules } });
            go({ tab: "courses", course: course.id });
            return true;
          } catch (error) { form.setError(error.message); return false; }
        } },
      ],
    });
  }

  async function courses(body, route) {
    if (route.query.course) return courseEditor(body, route.query.course);
    const q = route.query.q || "";
    const status = route.query.status || "";
    const params = new URLSearchParams(Object.entries({ q, status }).filter(([, v]) => v)).toString();
    const rows = (await api(`/courses${params ? `?${params}` : ""}`)).courses;
    const search = h("input", { type: "search", placeholder: "Search courses", value: q, "aria-label": "Search courses", "data-search": "1" });
    const statusSelect = Aira.ui.select([["", "Any status"], ["draft", "Draft"], ["published", "Published"], ["archived", "Archived"]], status, { "aria-label": "Status" });
    const apply = () => go({ tab: "courses", q: search.value.trim(), status: statusSelect.value });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    statusSelect.addEventListener("change", apply);
    const filtered = Boolean(q || status);
    body.append(
      toolbar(search, statusSelect, S.button("New course", () => newCourse()), S.button("Draft with AI", () => outlineWizard(), { kind: "secondary" })),
      xp.table({
        columns: [
          { key: "title", label: "Course", render: (row) => h("div", {}, h("strong", { text: row.title }), row.subtitle ? h("div", { class: "muted small", text: row.subtitle }) : null, xp.phoneMeta(plural(row.lessons, "lesson"), plural(row.students, "student"), row.pending ? `${row.pending} unpaid` : "")) },
          { key: "status", label: "Status", render: (row) => S.pill(row.status, TONES) },
          { key: "price_cents", label: "Price", num: true, render: (row) => (row.price_cents ? S.money(row.price_cents) : "Free") },
          { key: "lessons", label: "Lessons", num: true, wide: true },
          { key: "students", label: "Students", num: true, wide: true },
          { key: "pending", label: "Unpaid", num: true, wide: true },
          { key: "rating", label: "Rating", wide: true, render: (row) => (row.reviews ? h("span", {}, stars(row.rating), h("span", { class: "muted small", text: ` ${row.rating} (${row.reviews})` })) : "") },
          { key: "link", label: "Page", wide: true, render: (row) => (row.link && row.status === "published" ? h("a", { href: S.absolute(row.link.path), target: "_blank", rel: "noopener noreferrer", text: "Open", "aria-label": `Open the page of ${row.title}` }) : "") },
        ],
        rows,
        empty: filtered ? Aira.ui.empty("No course matches", "Try another word or status.") : Aira.ui.empty("No courses yet", "Create your first course: a title, a price and a few lessons is enough to start.", S.button("Create a course", () => newCourse())),
        onRow: (row) => go({ tab: "courses", course: row.id }),
      }),
    );
  }

  async function courseEditor(body, courseId) {
    const data = await api(`/courses/${courseId}`);
    const course = data.course;
    const reload = () => go({ tab: "courses", course: courseId, t: Date.now() });
    const setStatus = (status, message) => async () => { await api(`/courses/${courseId}/status`, { method: "POST", body: { status } }); if (message) Aira.toast(message, "success"); reload(); };
    const actions = h("div", { class: "btn-row" },
      S.button("Edit details", async () => { if (await S.edit({ title: "Course details", fields: courseFields, values: course, onSave: (values) => api(`/courses/${courseId}`, { method: "PATCH", body: clean(values) }) })) reload(); }, { kind: "secondary sm" }),
      course.status !== "published" ? S.button("Publish", setStatus("published", "Published. Share the course page to get students."), { kind: "sm" }) : S.button("Unpublish", setStatus("draft"), { kind: "secondary sm" }),
      course.status !== "archived" ? S.button("Archive", setStatus("archived"), { kind: "ghost sm" }) : null,
      S.button("Delete", async () => { if (await S.confirmDelete(`“${course.title}”`)) { await api(`/courses/${courseId}`, { method: "DELETE" }); go({ tab: "courses" }); } }, { kind: "danger secondary sm" }),
    );
    const share = course.status === "published"
      ? S.share({
        title: "Course page",
        text: "Students read the curriculum, try the free previews and enrol here.",
        link: data.link,
        onCreate: async () => (await api(`/courses/${courseId}/link`, { method: "POST", body: {} })).link,
        onRevoke: () => api(`/courses/${courseId}/link`, { method: "DELETE" }),
      })
      : h("p", { class: "muted", text: "Publish the course to get a page students can enrol from." });
    if (currentMain) xp.setTitle(currentMain, course.title, course.subtitle || "");
    body.append(
      h("div", { class: "xp-detail-meta" }, h("a", { class: "btn ghost sm", href: "#/academy?tab=courses", text: "← All courses" }), S.pill(course.status, TONES), h("span", { class: "muted", text: course.price_cents ? S.money(course.price_cents) : "Free" }), h("span", { class: "muted", text: LEVELS.find(([id]) => id === course.level)?.[1] || "" }), course.enrol_code ? h("span", { class: "muted", text: `Code: ${course.enrol_code}` }) : null),
      actions,
      S.kpis([
        { label: "Lessons", value: data.stats.lessons, hint: `${data.stats.minutes} minutes` },
        { label: "Quizzes", value: data.stats.quizzes },
        { label: "Students", value: data.stats.students, hint: course.capacity ? `${data.stats.seats_taken} of ${course.capacity} seats` : "" },
        { label: "Rating", value: data.rating && data.rating.count ? `${data.rating.average} / 5` : "-", hint: ratingText(data.rating) },
      ]),
      share,
      curriculumBuilder(courseId, data.modules, reload),
    );
  }

  function curriculumBuilder(courseId, modules, reload) {
    const order = async (moduleIds, lessonMap) => { await api(`/courses/${courseId}/reorder`, { method: "POST", body: { modules: moduleIds, lessons: lessonMap } }); reload(); };
    const moduleIds = modules.map((module) => module.id);
    const lessonMap = () => Object.fromEntries(modules.map((module) => [module.id, module.lessons.map((lesson) => lesson.id)]));
    const swap = (list, i, j) => { const copy = [...list]; [copy[i], copy[j]] = [copy[j], copy[i]]; return copy; };
    const lessonFields = [
      { name: "title", label: "Title", required: true },
      { name: "body", label: "Lesson text", type: "textarea", rows: 10, hint: "Plain text. Blank lines start a new paragraph." },
      { name: "video_url", label: "Video link", type: "url", placeholder: "https://youtube.com/..." },
      { name: "minutes", label: "Minutes", type: "number", min: 0 },
      { name: "preview", label: "Free preview (anyone can read it on the course page)", type: "checkbox" },
    ];
    const blocks = modules.map((module, mi) => {
      const lessons = module.lessons.map((lesson, li) => h("li", { class: "ac-lesson" },
        h("div", { class: "ac-lesson-main" }, h("strong", { text: `${li + 1}. ${lesson.title}` }), h("span", { class: "muted small", text: [lesson.minutes ? `${lesson.minutes} min` : "", lesson.preview ? "free preview" : "", lesson.question_count ? `quiz: ${lesson.question_count} questions, pass ${lesson.pass_mark}%` : "", lesson.video_url ? "video" : ""].filter(Boolean).join(" · ") })),
        h("div", { class: "btn-row" },
          li > 0 ? S.button("Move up", () => { const map = lessonMap(); map[module.id] = swap(map[module.id], li, li - 1); return order(null, map); }, { kind: "ghost sm", title: "Move this lesson up" }) : null,
          li < module.lessons.length - 1 ? S.button("Move down", () => { const map = lessonMap(); map[module.id] = swap(map[module.id], li, li + 1); return order(null, map); }, { kind: "ghost sm", title: "Move this lesson down" }) : null,
          S.button("Edit", async () => { if (await S.edit({ title: "Lesson", wide: true, fields: lessonFields, values: lesson, onSave: (values) => api(`/lessons/${lesson.id}`, { method: "PATCH", body: values }) })) reload(); }, { kind: "secondary sm" }),
          S.button(lesson.question_count ? "Quiz" : "Add quiz", () => quizEditor(lesson, reload), { kind: "secondary sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`the lesson “${lesson.title}”`)) { await api(`/lessons/${lesson.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" }))));
      return h("div", { class: "card ac-module" },
        h("div", { class: "ac-module-head" }, h("strong", { text: `Module ${mi + 1}: ${module.title}` }), h("div", { class: "btn-row" },
          mi > 0 ? S.button("Move up", () => order(swap(moduleIds, mi, mi - 1), null), { kind: "ghost sm", title: "Move this module up" }) : null,
          mi < modules.length - 1 ? S.button("Move down", () => order(swap(moduleIds, mi, mi + 1), null), { kind: "ghost sm", title: "Move this module down" }) : null,
          S.button("Rename", async () => { if (await S.edit({ title: "Module", fields: [{ name: "title", label: "Title", required: true }], values: module, onSave: (values) => api(`/modules/${module.id}`, { method: "PATCH", body: values }) })) reload(); }, { kind: "ghost sm" }),
          S.button("Delete", async () => { if (await S.confirmDelete(`the module “${module.title}” and its lessons`)) { await api(`/modules/${module.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" }))),
        lessons.length ? h("ol", { class: "ac-lessons" }, ...lessons) : h("p", { class: "muted", text: "No lessons in this module yet." }),
        h("div", { class: "btn-row" }, S.button("Add lesson", async () => { if (await S.edit({ title: "New lesson", wide: true, fields: lessonFields, values: { minutes: 10 }, submitLabel: "Add lesson", onSave: (values) => api(`/modules/${module.id}/lessons`, { method: "POST", body: values }) })) reload(); }, { kind: "secondary sm" })));
    });
    return S.section("Curriculum",
      modules.length ? null : Aira.ui.empty("No modules yet", "Add a module, then lessons inside it. A free-preview lesson helps students decide to enrol."),
      ...blocks,
      h("div", { class: "btn-row" }, S.button("Add module", async () => { if (await S.edit({ title: "New module", fields: [{ name: "title", label: "Title", required: true }], submitLabel: "Add module", onSave: (values) => api(`/courses/${courseId}/modules`, { method: "POST", body: values }) })) reload(); }, { kind: "secondary" })),
    );
  }

  function quizEditor(lesson, reload) {
    let questions = (lesson.questions || []).map((q) => ({ ...q, options: [...q.options] }));
    let open = true;
    const pass = h("input", { type: "number", min: 1, max: 100, value: String(lesson.pass_mark || 70) });
    const list = h("div", { class: "suite-body" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const status = h("p", { class: "muted", role: "status", hidden: true });
    const render = () => {
      list.replaceChildren(...questions.map((question, qi) => {
        const prompt = h("input", { type: "text", value: question.prompt, oninput: (e) => { question.prompt = e.target.value; } });
        const options = h("textarea", { rows: 4, text: question.options.join("\n"), oninput: (e) => { question.options = e.target.value.split("\n"); } });
        const correct = h("input", { type: "number", min: 1, max: 6, value: String((question.correct || 0) + 1), oninput: (e) => { question.correct = Number(e.target.value) - 1; } });
        const explanation = h("input", { type: "text", value: question.explanation || "", oninput: (e) => { question.explanation = e.target.value; } });
        return h("div", { class: "card suite-form" }, h("strong", { text: `Question ${qi + 1}` }),
          Aira.ui.field("Question", prompt), Aira.ui.field("Options (one per line, 2 to 6)", options), Aira.ui.field("Right option (its number in the list)", correct), Aira.ui.field("Explanation (shown after passing)", explanation),
          h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", text: "Remove question", onclick: () => { questions.splice(qi, 1); render(); } })));
      }));
      if (!questions.length) list.append(h("p", { class: "muted", text: "No questions. Add some, or draft them from the lesson text." }));
    };
    render();
    Aira.modal({
      title: `Quiz: ${lesson.title}`,
      wide: true,
      onClose: () => { open = false; },
      body: h("div", { class: "suite-body" }, h("p", { class: "muted", text: "Graded on the server: students never see the right options until they pass." }), Aira.ui.field("Pass mark (%)", pass), list,
        h("div", { class: "btn-row" },
          h("button", { type: "button", class: "btn secondary sm", text: "Add question", onclick: () => { questions.push({ prompt: "", options: ["", ""], correct: 0, explanation: "" }); render(); } }),
          S.button("Draft from the lesson", async () => {
            status.textContent = "Drafting questions from the lesson… this can take a little while.";
            status.hidden = false;
            try {
              const draft = await api(`/lessons/${lesson.id}/quiz-draft`, { method: "POST", body: {} });
              if (!open) return;
              questions = draft.questions.map((q) => ({ ...q, options: [...q.options] }));
              render();
              status.textContent = draft.source === "model" ? "Drafted. Check every answer before saving." : "A template from the lesson text. Edit it before saving.";
            } catch (err) { status.hidden = true; error.textContent = err.message; error.hidden = false; }
          }, { kind: "secondary sm" })),
        status, error),
      actions: [
        { label: "Cancel", kind: "ghost" },
        { label: "Save quiz", kind: "primary", onClick: async () => {
          error.hidden = true;
          const payload = questions.map((q) => ({ prompt: q.prompt, options: q.options.map((o) => o.trim()).filter(Boolean), correct: q.correct, explanation: q.explanation }));
          try { await api(`/lessons/${lesson.id}/quiz`, { method: "POST", body: { questions: payload, pass_mark: Number(pass.value) || 70 } }); reload(); return true; } catch (err) { error.textContent = err.message; error.hidden = false; return false; }
        } },
      ],
    });
  }

  // ------------------------------------------------------------------ students
  async function students(body, route) {
    const courseList = await coursesList();
    const query = { course_id: route.query.course_id || "", status: route.query.status || "", q: route.query.q || "" };
    const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v)).toString();
    const rows = (await api(`/students${params ? `?${params}` : ""}`)).students;
    const courseSelect = Aira.ui.select([["", "All courses"], ...courseList.map((c) => [c.id, c.title])], query.course_id, { "aria-label": "Course" });
    const statusSelect = Aira.ui.select(STUDENT_STATUSES, query.status, { "aria-label": "Status" });
    const search = h("input", { type: "search", placeholder: "Name, email or reference", value: query.q, "aria-label": "Search", "data-search": "1" });
    const apply = () => go({ tab: "students", course_id: courseSelect.value, status: statusSelect.value, q: search.value.trim() });
    courseSelect.addEventListener("change", apply);
    statusSelect.addEventListener("change", apply);
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    const filtered = Boolean(query.course_id || query.status || query.q);
    body.append(
      toolbar(search, courseSelect, statusSelect,
        S.button("Enrol a student", () => enrolStudent(courseList, query.course_id)),
        S.button("Download CSV", () => Aira.download(`/api/academy/students?format=csv${params ? `&${params}` : ""}`, "students.csv"), { kind: "secondary" })),
      xp.table({
        columns: [
          { key: "name", label: "Student", render: (row) => h("div", {}, h("strong", { text: row.name }), h("div", { class: "muted small", text: row.email }), xp.phoneMeta(row.course_title, `${row.progress}%`)) },
          { key: "course_title", label: "Course", wide: true, render: (row) => h("div", {}, h("span", { text: row.course_title }), row.cohort_name ? h("div", { class: "muted small", text: row.cohort_name }) : null) },
          { key: "status", label: "Status", render: (row) => S.pill(row.status, TONES) },
          { key: "progress", label: "Progress", num: true, wide: true, render: (row) => `${row.progress}%` },
          { key: "last_activity_at", label: "Last active", wide: true, render: (row) => (row.last_activity_at ? Aira.fmt.ago(row.last_activity_at) : "") },
        ],
        rows,
        empty: filtered ? Aira.ui.empty("No student matches", "Try another course, status or name.") : Aira.ui.empty("No students yet", courseList.length ? "Share a course page, or enrol someone yourself." : "Create and publish a course first."),
        onRow: (row) => studentDrawer(row.id, () => go({ ...query, tab: "students", t: Date.now() })),
      }),
    );
  }

  async function enrolStudent(courseList, courseId) {
    const open = courseList.filter((c) => c.status !== "archived");
    if (!open.length) { Aira.toast("Create a course first."); return; }
    const done = await S.edit({
      title: "Enrol a student",
      fields: [
        { name: "course_id", label: "Course", type: "select", options: open.map((c) => [c.id, c.title]) },
        { name: "name", label: "Name", required: true },
        { name: "email", label: "Email", type: "email", required: true },
        { name: "paid", label: "Already paid (records the payment for a paid course)", type: "checkbox" },
        { name: "ref", label: "Partner code", hint: "Optional. The partner who sent this student earns their commission." },
        { name: "notes", label: "Notes", type: "textarea", rows: 2 },
      ],
      values: { course_id: courseId && open.some((c) => c.id === courseId) ? courseId : open[0].id },
      submitLabel: "Enrol",
      onSave: (values) => api("/students", { method: "POST", body: values }),
    });
    if (done) { Aira.toast("Enrolled. Their welcome email is in the outbox.", "success"); go({ tab: "students", t: Date.now() }); }
  }

  async function studentDrawer(id, refresh) {
    const data = await api(`/students/${id}`);
    const s = data.student;
    const reopen = () => { Aira.closeDrawer(); refresh(); studentDrawer(id, refresh); };
    const cohortSelect = data.cohorts.length ? Aira.ui.select([["", "No cohort"], ...data.cohorts.map((c) => [c.id, c.name])], s.cohort_id || "", { "aria-label": "Cohort" }) : null;
    if (cohortSelect) cohortSelect.addEventListener("change", async () => { try { await api(`/students/${id}`, { method: "PATCH", body: { cohort_id: cohortSelect.value } }); Aira.toast("Cohort saved.", "success"); } catch { cohortSelect.value = s.cohort_id || ""; } });
    const actions = h("div", { class: "btn-row" },
      s.status === "pending_payment" ? S.button(`Mark paid (${S.money(s.price_cents)})`, async () => { await api(`/students/${id}/paid`, { method: "POST", body: {} }); Aira.toast("Payment recorded; lessons unlocked.", "success"); reopen(); }) : null,
      s.paid_cents && s.status !== "refunded" ? S.button("Refund", async () => { if (await Aira.confirm({ title: "Refund", message: `Mark ${S.money(s.paid_cents)} as refunded? Their lessons lock and the payment stops counting.`, confirmLabel: "Refund", danger: true })) { await api(`/students/${id}/refund`, { method: "POST", body: {} }); reopen(); } }, { kind: "danger secondary" }) : null,
      s.status !== "refunded" ? S.button("Email their link again", async () => { await api(`/students/${id}/resend`, { method: "POST", body: {} }); Aira.toast("Queued in the outbox.", "success"); }, { kind: "secondary" }) : null);
    Aira.drawer({
      title: s.name,
      subtitle: `${s.email} · ${data.course.title}`,
      body: h("div", { class: "suite-body" },
        h("div", { class: "btn-row" }, S.pill(s.status, TONES), h("span", { class: "muted", text: `Reference ${s.reference}` })),
        h("div", { class: "ac-progress", role: "progressbar", "aria-label": "Progress", "aria-valuenow": String(data.progress.percent), "aria-valuemin": "0", "aria-valuemax": "100" }, h("span", { style: { width: `${data.progress.percent}%` } })),
        Aira.ui.kv([["Progress", `${data.progress.done} of ${data.progress.total} lessons (${data.progress.percent}%)`], ["Quizzes passed", `${data.progress.quizzes_passed} of ${data.progress.quizzes_total}`], ["Enrolled", S.when(s.created_at)], ["Last active", S.when(s.last_activity_at)], ["Paid", s.paid_cents ? S.money(s.paid_cents) : ""], ["Certificate", s.certificate_code], ["Notes", s.notes]]),
        cohortSelect ? Aira.ui.field("Cohort", cohortSelect) : null,
        actions,
        S.share({ title: "Student's own page", text: "Their access to the lessons. Only share it with this student.", link: data.link, onCreate: async () => (await api(`/students/${id}/link`, { method: "POST", body: {} })).link, onRevoke: () => api(`/students/${id}/link`, { method: "DELETE" }) }),
        data.certificate ? S.share({ title: "Certificate", text: "", link: data.certificate }) : null,
        data.attempts.length ? S.section("Quiz attempts", S.table({ columns: [{ key: "lesson_title", label: "Lesson" }, { key: "score", label: "Score", num: true, render: (a) => `${a.score}%` }, { key: "passed", label: "", render: (a) => S.pill(a.passed ? "passed" : "not passed", { passed: "good", "not passed": "warn" }) }, { key: "at", label: "When", render: (a) => Aira.fmt.ago(a.at) }], rows: data.attempts })) : null),
    });
  }

  // ------------------------------------------------------------------ cohorts
  async function cohorts(body) {
    const [courseList, rows] = await Promise.all([coursesList(), api("/cohorts").then((d) => d.cohorts)]);
    const fields = [
      { name: "course_id", label: "Course", type: "select", options: courseList.map((c) => [c.id, c.title]) },
      { name: "name", label: "Name", required: true, placeholder: "November evening batch" },
      { name: "starts_on", label: "Starts on", type: "date" },
      { name: "capacity", label: "Seats", type: "number", min: 1, hint: "Optional." },
    ];
    const reload = () => go({ tab: "cohorts", t: Date.now() });
    const create = async () => { if (!courseList.length) { Aira.toast("Create a course first."); return; } if (await S.edit({ title: "New cohort", fields, values: { course_id: courseList[0].id }, submitLabel: "Create", onSave: (values) => api("/cohorts", { method: "POST", body: clean(values) }) })) reload(); };
    body.append(
      h("p", { class: "muted", text: "Cohorts group students who start together: a batch, a class or a live programme. Cohorts that have not started yet show on the course page." }),
      h("div", { class: "btn-row" }, S.button("New cohort", create)),
      xp.table({
        columns: [
          { key: "name", label: "Cohort", render: (row) => h("div", {}, h("span", { text: row.name }), xp.phoneMeta(row.course_title)) },
          { key: "course_title", label: "Course", wide: true },
          { key: "starts_on", label: "Starts", render: (row) => S.day(row.starts_on) },
          { key: "students", label: "Students", num: true, render: (row) => (row.capacity ? `${row.students} / ${row.capacity}` : row.students) },
          { key: "id", label: "", render: (row) => S.button("Delete", async () => { if (await S.confirmDelete(`the cohort “${row.name}” (students stay enrolled)`)) { await api(`/cohorts/${row.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" }) },
        ],
        rows,
        empty: Aira.ui.empty("No cohorts yet", "Start a batch: students pick it when they enrol.", courseList.length ? S.button("New cohort", create) : null),
        onRow: async (row) => { if (await S.edit({ title: "Cohort", fields: fields.slice(1), values: row, onSave: (values) => api(`/cohorts/${row.id}`, { method: "PATCH", body: clean(values) }) })) reload(); },
      }),
    );
  }

  // ------------------------------------------------------------------ announcements
  async function announcements(body) {
    const [courseList, rows, cohortRows] = await Promise.all([coursesList(), api("/announcements").then((d) => d.announcements), api("/cohorts").then((d) => d.cohorts)]);
    const create = async () => {
      if (!courseList.length) { Aira.toast("Create a course first."); return; }
      const sent = await S.edit({
        title: "New announcement",
        wide: true,
        fields: [
          { name: "course_id", label: "Course", type: "select", options: courseList.map((c) => [c.id, c.title]) },
          { name: "cohort_id", label: "Only this cohort", type: "select", options: [["", "Everyone in the course"], ...cohortRows.map((c) => [c.id, `${c.name} (${c.course_title})`])] },
          { name: "subject", label: "Subject", required: true },
          { name: "body", label: "Message", type: "textarea", rows: 8, required: true },
        ],
        values: { course_id: courseList[0].id },
        submitLabel: "Queue for students",
        onSave: (values) => api("/announcements", { method: "POST", body: values }),
      });
      if (sent) { Aira.toast(`Queued for ${plural(sent.announcement.recipients, "student")}.`, "success"); go({ tab: "announcements", t: Date.now() }); }
    };
    body.append(
      h("p", { class: "muted", text: "Tell a course's students about a live session, a new lesson or an offer. Messages wait in the Business hub outbox for your approval." }),
      h("div", { class: "btn-row" }, S.button("New announcement", create)),
      xp.table({
        columns: [
          { key: "created_at", label: "When", wide: true, render: (row) => S.when(row.created_at) },
          { key: "course_title", label: "Course", wide: true },
          { key: "subject", label: "Subject", render: (row) => h("div", {}, h("span", { text: row.subject }), xp.phoneMeta(row.course_title, S.when(row.created_at))) },
          { key: "recipients", label: "Students", num: true },
        ],
        rows,
        empty: Aira.ui.empty("No announcements yet", "Tell your students about a live session or a new lesson.", courseList.length ? S.button("New announcement", create) : null),
        onRow: (row) => Aira.modal({ title: row.subject, body: h("pre", { class: "suite-pre", text: row.body }), actions: [{ label: "Close", kind: "secondary" }] }),
      }),
    );
  }

  // ------------------------------------------------------------------ reviews
  async function reviewsTab(body, route) {
    const courseList = await coursesList();
    const query = { course_id: route.query.course_id || "", status: route.query.status || "" };
    const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v)).toString();
    const rows = (await api(`/reviews${params ? `?${params}` : ""}`)).reviews;
    const reload = () => go({ ...query, tab: "reviews", t: Date.now() });
    const courseSelect = Aira.ui.select([["", "All courses"], ...courseList.map((c) => [c.id, c.title])], query.course_id, { "aria-label": "Course" });
    const statusSelect = Aira.ui.select([["", "Shown and hidden"], ["published", "Shown on the course page"], ["hidden", "Hidden"]], query.status, { "aria-label": "Status" });
    const apply = () => go({ tab: "reviews", course_id: courseSelect.value, status: statusSelect.value });
    courseSelect.addEventListener("change", apply);
    statusSelect.addEventListener("change", apply);
    const shown = rows.filter((r) => r.status === "published");
    const average = shown.length ? (shown.reduce((sum, r) => sum + r.rating, 0) / shown.length).toFixed(1) : "-";
    body.append(
      h("p", { class: "muted", text: "Students rate a course from their own page once they have finished a lesson. Reviews show on the course page (first name and initial only) and help new students decide. Hide one that is off topic." }),
      S.kpis([
        { label: "Average rating", value: average === "-" ? "-" : `${average} / 5` },
        { label: "Reviews shown", value: shown.length },
        { label: "Hidden", value: rows.length - shown.length },
      ]),
      toolbar(courseSelect, statusSelect),
      rows.length
        ? S.section("", ...rows.map((r) => h("div", { class: `xp-review ${r.status === "hidden" ? "hidden-review" : ""}`.trim() },
          h("div", { class: "xp-review-head" }, h("span", {}, stars(r.rating), h("strong", { text: ` ${r.student_name || r.name}` }), h("span", { class: "muted small", text: ` · ${r.course_title} · ${Aira.fmt.ago(r.updated_at)}` })), S.pill(r.status === "published" ? "shown" : "hidden", { shown: "good", hidden: "" })),
          r.comment ? h("p", { class: "suite-pre", text: r.comment }) : h("p", { class: "muted small", text: "No comment, just a rating." }),
          h("div", { class: "btn-row" }, S.button(r.status === "published" ? "Hide from the course page" : "Show on the course page", async () => { await api(`/reviews/${r.id}/status`, { method: "POST", body: { status: r.status === "published" ? "hidden" : "published" } }); reload(); }, { kind: "ghost sm" })))))
        : Aira.ui.empty(params ? "No review matches" : "No reviews yet", "Students can rate a course from their own page after their first lesson. Remind them with an announcement."),
    );
  }

  // ------------------------------------------------------------------ search (the box at the top of the app)
  // One request finds every kind; a few seconds of memory per query, so the list under the box and the results
  // page on Enter share it, and typing back and forth does not ask again.
  const searchCache = new Map();
  xp.find = (query, limit) => {
    const key = `${limit}|${query}`;
    const hit = searchCache.get(key);
    if (hit && Date.now() - hit.at < 5000) return hit.promise;
    const promise = api(`/search?${new URLSearchParams({ q: query, limit: String(limit) })}`, { silent: true });
    searchCache.set(key, { at: Date.now(), promise });
    promise.catch(() => searchCache.delete(key));
    if (searchCache.size > 40) searchCache.delete(searchCache.keys().next().value);
    return promise;
  };
  xp.forgetSearch = () => searchCache.clear();
  const section = () => (Aira.router.current && Aira.router.current.path[0] === "events" ? "events" : "academy");
  const eventWhen = (iso) => (iso ? Aira.fmt.datetime(iso, S._business && S._business.timezone && S._business.timezone !== "UTC" ? S._business.timezone : undefined) : "");
  const ORDER_TONES = { pending_payment: "warn", confirmed: "good", cancelled: "bad", refunded: "bad" };
  const statusText = (status) => String(status || "").replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
  // Each kind: how a record reads in the list under the box and on the results page, where it opens, and the tab
  // that lists all of them with the same words in its own search.
  const KINDS = [
    { key: "courses", one: "Course", icon: "academy", label: (r) => r.title, status: (r) => r.status, sub: (r) => [r.price_cents ? S.money(r.price_cents) : "Free", r.subtitle].filter(Boolean).join(" · "), href: (r) => `#/academy?tab=courses&course=${r.id}`, all: (q) => `#/academy?${new URLSearchParams({ tab: "courses", q })}`, tab: "Courses" },
    { key: "events", one: "Event", icon: "events", label: (r) => r.title, status: (r) => r.phase, sub: (r) => [eventWhen(r.starts_at), r.venue_name || (r.online_url ? "Online" : "")].filter(Boolean).join(" · "), href: (r) => `#/events?tab=events&event=${r.id}`, all: (q) => `#/events?${new URLSearchParams({ tab: "events", scope: "all", q })}`, tab: "Events" },
    { key: "students", one: "Student", icon: "recruiters", label: (r) => r.name, status: (r) => r.status, sub: (r) => [r.course_title, r.email].filter(Boolean).join(" · "), href: (r) => `#/academy?tab=students&open=student:${r.id}`, all: (q) => `#/academy?${new URLSearchParams({ tab: "students", q })}`, tab: "Students" },
    { key: "orders", one: "Ticket order", icon: "shop", label: (r) => r.buyer_name, status: (r) => r.status, sub: (r) => [r.reference, r.event_title, S.money(r.total_cents)].join(" · "), href: (r) => `#/events?tab=orders&open=order:${r.id}`, all: (q) => `#/events?${new URLSearchParams({ tab: "orders", q })}`, tab: "Orders" },
    { key: "partners", one: "Partner", icon: "xpPartner", label: (r) => r.name, status: (r) => (r.active ? "active" : "off"), sub: (r) => [`Code ${r.code}`, `${r.commission_percent}% commission`].join(" · "), href: (r) => `#/${section()}?tab=partners&open=partner:${r.id}`, all: (q) => `#/${section()}?${new URLSearchParams({ tab: "partners", q })}`, tab: "Partners" },
  ];
  const visit = (href) => { if (location.hash === href) Aira.router.render(); else location.hash = href; };

  // The results page (?tab=search&q=, in either section): every kind that matches, each row opening its record.
  xp.searchTab = async (body, query) => {
    if (!query) {
      body.append(S.section(null, Aira.ui.empty("Search Courses & Events", "Type in the search box at the top: a course, an event, a student, a ticket buyer, an order number or a partner.")));
      return;
    }
    const found = await xp.find(query, 50);
    const sections = KINDS.filter((kind) => (found[kind.key] || []).length).map((kind) => {
      const rows = found[kind.key];
      const total = found.totals[kind.key] || rows.length;
      return S.section(`${kind.tab} (${total})`,
        xp.table({
          columns: [
            { key: "label", label: kind.one, render: (r) => h("div", { class: "xp-result" }, h("span", { class: "xp-result-icon", "aria-hidden": "true" }, Aira.icon(kind.icon)), h("div", {}, h("strong", { text: kind.label(r) }), h("div", { class: "muted small", text: kind.sub(r) }))) },
            { key: "status", label: "Status", render: (r) => S.pill(kind.status(r), kind.key === "orders" ? ORDER_TONES : { ...TONES, ended: "", cancelled: "bad", off: "" }) },
          ],
          rows,
          onRow: (r) => visit(kind.href(r)),
          caption: `${kind.tab} that match ${query}`,
        }),
        h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm", href: kind.all(query), text: total > rows.length ? `All ${total} in ${kind.tab}` : `Open in ${kind.tab}` })));
    });
    if (!sections.length) {
      body.append(S.section(null, Aira.ui.empty(`Nothing matches “${query}”`, "Try part of a name, an email, an order or student reference, a partner code, a title or a venue.",
        xp.canManage() ? h("div", { class: "btn-row" }, S.button("New course", () => newCourse(), { kind: "secondary sm" }), h("a", { class: "btn secondary sm", href: "#/events?tab=events&new=event", text: "New event" })) : null)));
      return;
    }
    body.append(...sections);
  };

  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people make most here, each opening the same form as the button on its tab.
  Aira.appNew.experiences = () => (xp.canManage() ? [
    { label: "New course", icon: "academy", href: "#/academy?tab=courses&new=course" },
    { label: "Draft a course with AI", icon: "ai", href: "#/academy?tab=courses&new=outline" },
    { label: "New event", icon: "events", href: "#/events?tab=events&new=event" },
    { label: "Enrol a student", icon: "recruiters", href: "#/academy?tab=students&new=student" },
    // Partners are shared by both sections: the new partner opens in the section you are in.
    { label: "New partner", icon: "xpPartner", run: () => Aira.router.go([section()], { tab: "partners", new: "partner" }) },
  ] : []);
  Aira.appSearch.experiences = {
    placeholder: "Search courses, events, students and orders",
    // Up to six records, one of each kind in turn, so a busy kind (students) does not hide the others. Someone who
    // cannot open the app (a child's profile, the app off) is not asked about.
    async suggest(query) {
      if (!xp.canOpen()) return [];
      const found = await xp.find(query.trim(), 4);
      const lanes = KINDS.map((kind) => (found[kind.key] || []).map((r) => ({ label: kind.label(r), sub: [kind.one, statusText(kind.status(r)), kind.sub(r)].filter(Boolean).join(" · "), icon: kind.icon, href: kind.href(r) })));
      const out = [];
      for (let round = 0; out.length < 6 && lanes.some((lane) => lane.length > round); round += 1) for (const lane of lanes) if (lane[round] && out.length < 6) out.push(lane[round]);
      return out;
    },
    // Enter: every match, on the results page of the section you are in.
    run(query) {
      const q = query.trim();
      if (q) Aira.router.go([section()], { tab: "search", q });
    },
  };

  // ------------------------------------------------------------------ the view
  // #/experiences (the app's own id, used by some links) opens Courses.
  Aira.views.experiences = async (main, route) => { Aira.router.go(["academy"], route.query, { replace: true }); };

  // The tab an ask from "+ New" or the search box opens on.
  const ASK_TAB = { course: "courses", outline: "courses", student: "students", partner: "partners" };
  let currentMain = null;

  Aira.views.academy = async (main, route) => {
    if (xp.take("academy", route, (ask) => ASK_TAB[ask.new || ask.open])) return;
    await S.business();
    currentMain = main;
    const query = String(route.query.q || "").trim();
    const tabId = route.query.tab === "search" || TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "overview";
    const meta = TABS.find((t) => t.id === tabId);
    const title = tabId === "search" ? (query ? `Results for “${query}”` : "Search") : meta.title || meta.label;
    const body = xp.head(main, {
      title,
      about: tabId === "search" ? "Courses, events, students, ticket orders and partners that match." : meta.about,
      phoneNew: tabId === "courses" ? null : S.button("New course", () => newCourse(), { icon: "plus" }),
    });
    document.title = `${title} · Courses & Events · Monosage`;
    const tab = S.tabs(body, route, "academy", TABS, tabId);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const refreshTo = (tabName) => () => go({ ...route.query, tab: tabName, t: Date.now() });
    const render = async () => {
      panel.replaceChildren(Aira.ui.skeleton(4));
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "courses") await courses(holder, route);
        else if (tab === "students") await students(holder, route);
        else if (tab === "cohorts") await cohorts(holder);
        else if (tab === "announcements") await announcements(holder);
        else if (tab === "reviews") await reviewsTab(holder, route);
        else if (tab === "partners") await xp.partnersTab(holder, route, go);
        else if (tab === "earnings") await xp.earningsTab(holder, route, go);
        else if (tab === "search") await xp.searchTab(holder, query);
        else await overview(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
    xp.answer("academy", {
      new: {
        course: () => newCourse(),
        outline: () => outlineWizard(),
        student: async () => enrolStudent(await coursesList(), route.query.course_id || ""),
        partner: async () => { const created = await editPartner(); if (created) go({ tab: "partners", open: `partner:${created.id}` }); },
      },
      open: {
        student: (id) => studentDrawer(id, refreshTo("students")),
        partner: (id) => partnerDrawer(id, refreshTo("partners")),
      },
    });
  };
})();

/* Aira — Marketing (#/marketing): grow an audience and turn it into customers.
 *
 * Tabs (?tab=): overview (headline numbers, setup, what is coming up, the newest leads), calendar (posts per channel
 * on a month or week grid, drafts written for each channel, repurposing, a 30-day plan), campaigns (budget, spend,
 * results, ROI), links (tracked links that count clicks and add UTM tags), forms (lead-capture forms and the leads
 * inbox: reply, follow-ups, payment requests, paid leads counted as revenue), bio (the link-in-bio page), kit (the media
 * kit: audience, rate card and sponsorship requests), subscribers (import, export, tags) and newsletters (queued to the
 * shared outbox, one message per subscribed address, each with its own unsubscribe link).
 * Everything runs through /api/marketing/* (aira/suite/marketing/marketing_routes.py). DOM is built with h() and
 * text only.
 *
 * In the Aira frame (shell.js): "+ New" offers a post, a campaign, a lead form, a newsletter and a lead search
 * (#/marketing?tab=<tab>&new=<kind>, #/leads?tab=find&new=search); the search box across the top finds the app's own
 * records (/api/marketing/search and the saved sales leads) and opens one with #/marketing?tab=<tab>&open=<kind>:<id>;
 * Enter shows every match on a results page (?tab=search&q=). The page takes ?new= and ?open= out of the address
 * before it draws, so Back or a refresh does not ask again.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const M = (path, opts) => S.api("marketing", path, opts);

  // A few glyphs the shared set lacks, for the menu's folders and the search list (added once, never replaced).
  const GLYPHS = {
    mkLink: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
    mkBio: '<svg viewBox="0 0 24 24"><rect x="6" y="2.5" width="12" height="19" rx="2.5"/><circle cx="12" cy="8" r="2.2"/><path d="M9 13h6M9 16h6"/></svg>',
    mkMail: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>',
    mkHistory: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
    mkPost: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;

  // `about` is the page's line under its title; `icon` the folder's glyph in the left menu.
  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard", about: "How your marketing is doing this month and what to do next." },
    { id: "calendar", label: "Calendar", icon: "interviews", about: "Plan posts for every channel, write them with AI and see what goes out when." },
    { id: "campaigns", label: "Campaigns", icon: "ads", about: "Budget, spend and what each push brought in: leads, customers and ROI." },
    { id: "links", label: "Links", icon: "mkLink", about: "Tracked links that count every visit and add UTM tags for your website's analytics." },
    { id: "forms", label: "Forms and leads", icon: "applications", about: "Lead-capture forms and the enquiries they bring: reply, follow up and get paid." },
    { id: "bio", label: "Bio page", icon: "mkBio", about: "One link for your profiles with everything people need." },
    { id: "kit", label: "Media kit", icon: "documents", about: "Your audience, your prices and a form brands use to ask for a sponsorship." },
    { id: "subscribers", label: "Subscribers", icon: "recruiters", about: "The people who agreed to hear from you by email." },
    { id: "newsletters", label: "Newsletters", icon: "news", about: "Emails to your list, each copy with its own unsubscribe link." },
  ];
  const CHANNELS = [["instagram", "Instagram"], ["facebook", "Facebook"], ["linkedin", "LinkedIn"], ["x", "X"], ["tiktok", "TikTok"], ["youtube", "YouTube"], ["email", "Email"], ["blog", "Blog"], ["whatsapp", "WhatsApp"]];
  const LIMITS = { instagram: 2200, facebook: 5000, linkedin: 3000, x: 280, tiktok: 2200, youtube: 5000, email: 10000, blog: 20000, whatsapp: 1000 };
  const CHANNEL_NAME = Object.fromEntries(CHANNELS);
  const STATUS_TONES = { idea: "", draft: "", scheduled: "info", published: "good", new: "warn", contacted: "info", won: "good", lost: "bad", subscribed: "good", unsubscribed: "", partial: "warn", queued: "good", active: "good", paused: "warn", ended: "" };
  let campaigns = [];
  let rerender = () => {};
  let newsletterCap = 500; // the server's per-queue cap, refreshed whenever the newsletters list loads
  // What "+ New" or the top search asked the page for: { new: kind } or { open: kind, id, record? }.
  let pending = null;

  // Marketing holds the business's customers and money: a child or teen profile is refused by the server, and in the
  // hosted build only a member who may manage the business's apps can create. "+ New" offers nothing to anyone else.
  const grownUp = () => { const me = Aira.life && Aira.life.me; return !me || !me.age_group || ["adult", "senior"].includes(me.age_group); };
  const canCreate = () => grownUp() && !(Aira.cloud && typeof Aira.cloud.can === "function" && !Aira.cloud.can("suite.manage"));

  // S.table opens a row on Enter but leaves the key press alive: the drawer opens, focus moves into it and the same
  // Enter then presses the drawer's close button, so a keyboard user could never open a row. Cancelling the key's
  // default action (in the capture phase, before the row's own handler) keeps the drawer open.
  const table = (options, extraClass = "") => {
    const node = S.table(options);
    if (options.onRow) node.addEventListener("keydown", (event) => { if (event.key === "Enter" && event.target instanceof Element && event.target.matches("tr.suite-row")) event.preventDefault(); }, true);
    if (extraClass) node.classList.add(extraClass);
    return node;
  };

  // ISO UTC <-> the browser's datetime-local value.
  const toLocalInput = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };
  const fromLocalInput = (value) => (value ? new Date(value).toISOString() : "");
  const campaignOptions = () => [["", "No campaign"], ...campaigns.map((c) => [c.id, c.name])];
  const campaignName = (id) => (campaigns.find((c) => c.id === id) || {}).name || "";
  const pct = (value) => (value === null || value === undefined ? "-" : `${value}%`);
  const csv = (path, filename) => Aira.download(`/api/marketing${path}`, filename);
  const timeZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } };
  const HOURS = Array.from({ length: 17 }, (_, index) => { const hour = index + 6; return [String(hour), new Date(2020, 0, 1, hour).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })]; });
  // Query string from an object, leaving out empty values.
  const qs = (params) => { const out = new URLSearchParams(); for (const [key, value] of Object.entries(params)) if (value) out.set(key, value); const text = out.toString(); return text ? `?${text}` : ""; };
  const filterBar = (...children) => h("div", { class: "mk-filters" }, ...children);

  async function loadCampaigns() {
    try { campaigns = (await M("/campaigns", { silent: true })).campaigns; } catch { campaigns = []; }
  }

  // ======================================================================= posts
  function postFields(channel) {
    return [
      { name: "channel", label: "Channel", type: "select", options: CHANNELS, required: true },
      { name: "title", label: "Title (for you)", placeholder: "Autumn colours carousel" },
      { name: "body", label: "Post text", type: "textarea", rows: 8, hint: `Limit: ${LIMITS[channel || "instagram"]} characters for ${CHANNEL_NAME[channel || "instagram"]}.` },
      { name: "hashtags", label: "Hashtags", placeholder: "#hair #autumn" },
      { name: "media_url", label: "Image or video link", type: "url", placeholder: "https://" },
      { name: "status", label: "Status", type: "select", options: S.statusOptions(["idea", "draft", "scheduled", "published"]) },
      { name: "scheduled_local", label: "Post on", type: "datetime-local" },
      { name: "campaign_id", label: "Campaign", type: "select", options: campaignOptions() },
    ];
  }

  async function editPost(post = {}) {
    const values = { channel: "instagram", status: post.scheduled_at ? "scheduled" : "draft", ...post, scheduled_local: toLocalInput(post.scheduled_at) };
    return S.edit({
      title: post.id ? "Edit post" : "New post",
      fields: postFields(values.channel),
      values,
      wide: true,
      onSave: async (form) => {
        const body = { ...form, scheduled_at: fromLocalInput(form.scheduled_local) };
        delete body.scheduled_local;
        if (body.body && body.body.length > LIMITS[body.channel]) throw new Error(`${CHANNEL_NAME[body.channel]} allows ${LIMITS[body.channel]} characters; this post has ${body.body.length}.`);
        const saved = post.id ? await M(`/posts/${post.id}`, { method: "PATCH", body }) : await M("/posts", { method: "POST", body });
        Aira.toast("Post saved.", "success");
        rerender();
        return saved.post;
      },
    });
  }

  async function writeWithAi(defaults = {}) {
    const brief = await S.edit({
      title: "Write a post",
      fields: [
        { name: "channel", label: "Channel", type: "select", options: CHANNELS },
        { name: "topic", label: "What is it about?", required: true, placeholder: "Our new autumn colour menu" },
        { name: "goal", label: "Goal", type: "select", options: [["leads", "Get enquiries"], ["sales", "Sell"], ["awareness", "Get seen"], ["engagement", "Start a conversation"]] },
      ],
      values: { channel: "instagram", goal: "leads", ...defaults },
      submitLabel: "Write it",
      onSave: async (form) => (await M("/draft", { method: "POST", body: form })).draft,
    });
    if (!brief) return;
    if (brief.source === "template") Aira.toast("No writing model is running, so this is a template to fill in.");
    await editPost({ channel: brief.channel, title: brief.title, body: brief.text, hashtags: brief.hashtags, status: "draft", source: "draft" });
  }

  function openPost(post) {
    const drawer = Aira.drawer({
      title: post.title || `${CHANNEL_NAME[post.channel]} post`,
      subtitle: h("span", {}, S.pill(post.status, STATUS_TONES), ` ${CHANNEL_NAME[post.channel]}${post.scheduled_at ? ` · ${S.when(post.scheduled_at)}` : ""}`),
      body: h("div", { class: "suite-body" },
        h("pre", { class: "suite-pre", text: post.body || "(no text yet)" }),
        post.hashtags ? h("p", { class: "muted", text: post.hashtags }) : null,
        Aira.ui.kv([["Campaign", campaignName(post.campaign_id)], ["Media", post.media_url], ["Published", post.published_url ? h("a", { href: post.published_url, target: "_blank", rel: "noopener noreferrer", text: post.published_url }) : ""]]),
        h("div", { class: "btn-row" },
          S.button("Edit", async () => { drawer.close(false); await editPost(post); }),
          S.button("Copy text", () => S.copy([post.body, post.hashtags].filter(Boolean).join("\n\n")), { kind: "secondary" }),
          post.status !== "published" ? S.button("Mark published", async () => {
            const done = await S.edit({ title: "Mark published", fields: [{ name: "url", label: "Link to the post", type: "url", placeholder: "https://" }], submitLabel: "Mark published", onSave: (form) => M(`/posts/${post.id}/publish`, { method: "POST", body: form }) });
            if (done) { drawer.close(false); rerender(); }
          }, { kind: "secondary" }) : null,
          S.button("Repurpose", async () => {
            const fields = CHANNELS.filter(([id]) => id !== post.channel).map(([id, label]) => ({ name: id, label, type: "checkbox" }));
            const made = await S.edit({ title: "Repurpose for other channels", fields, submitLabel: "Create drafts", onSave: async (form) => {
              const channels = Object.entries(form).filter(([, on]) => on).map(([id]) => id);
              return (await M(`/posts/${post.id}/repurpose`, { method: "POST", body: { channels } })).posts;
            } });
            if (made) { Aira.toast(`${made.length} drafts created.`, "success"); drawer.close(false); rerender(); }
          }, { kind: "secondary" }),
          S.button("Delete", async () => { if (await S.confirmDelete("this post")) { await M(`/posts/${post.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" })),
      ),
    });
  }

  async function planDialog() {
    const fields = [
      { name: "description", label: "What does your business do?", required: true, placeholder: "A hair and colour studio in Austin", value: (S._business && S._business.tagline) || "" },
      { name: "goals", label: "Goals for the month", placeholder: "Fill weekday appointments, grow Instagram" },
      { name: "start", label: "First day", type: "date", value: new Date(Date.now() + 86400000).toISOString().slice(0, 10) },
      { name: "days", label: "Days", type: "number", min: 7, max: 60, value: 30 },
      { name: "hour", label: "Time to post each day", type: "select", value: "9", options: HOURS },
      { type: "heading", label: "Channels to rotate" },
      ...CHANNELS.map(([id, label]) => ({ name: `ch_${id}`, label, type: "checkbox", value: ["instagram", "facebook", "linkedin"].includes(id) })),
    ];
    // The plan's times are where you are (your browser's time zone), not in UTC.
    const body = (form) => ({ description: form.description, goals: form.goals, start: form.start, days: form.days || 30, hour: Number(form.hour || 9), time_zone: timeZone(), channels: CHANNELS.map(([id]) => id).filter((id) => form[`ch_${id}`]) });
    let request = null;
    const plan = await S.edit({ title: "30-day content plan", fields, submitLabel: "Plan it", onSave: async (form) => { request = body(form); return M("/plan", { method: "POST", body: request }); } });
    if (!plan) return;
    const list = h("ol", { style: { display: "grid", gap: "8px", paddingLeft: "20px" } }, ...plan.ideas.map((idea) => h("li", {}, h("strong", { text: `${S.day(idea.day)} · ${CHANNEL_NAME[idea.channel]}` }), h("div", { text: idea.title }), h("div", { class: "muted small", text: idea.text }))));
    Aira.modal({
      title: `${plan.ideas.length} ideas${plan.source === "template" ? " (template)" : ""}`,
      wide: true,
      body: h("div", { style: { maxHeight: "55vh", overflow: "auto" } }, list),
      actions: [
        { label: "Close", kind: "ghost" },
        { label: "Add all to the calendar", kind: "primary", onClick: async () => { const saved = await M("/plan", { method: "POST", body: { ...request, save: true } }); Aira.toast(`${saved.saved} ideas added.`, "success"); rerender(); } },
      ],
    });
  }

  function dayKey(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }

  async function calendarTab(body, route) {
    const mode = route.query.view === "week" ? "week" : "month";
    const anchor = route.query.d ? new Date(`${route.query.d}T00:00:00`) : new Date();
    let start;
    let end;
    if (mode === "week") {
      start = new Date(anchor); start.setDate(anchor.getDate() - ((anchor.getDay() + 6) % 7));
      end = new Date(start); end.setDate(start.getDate() + 7);
    } else {
      start = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
      end = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 1);
    }
    const gridStart = new Date(start);
    if (mode === "month") gridStart.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    const data = await M(`/posts?from=${encodeURIComponent(gridStart.toISOString())}&to=${encodeURIComponent(new Date(end.getTime() + 7 * 86400000).toISOString())}`);
    // Drafts and ideas without a date (a draft written with AI, a repurposed copy) live below the grid.
    const unscheduled = (await M("/posts?unscheduled=1")).posts;
    const byDay = {};
    for (const post of data.posts) { if (!post.scheduled_at) continue; const key = dayKey(new Date(post.scheduled_at)); (byDay[key] = byDay[key] || []).push(post); }
    const go = (offset) => { const next = new Date(anchor); if (mode === "week") next.setDate(anchor.getDate() + offset * 7); else next.setMonth(anchor.getMonth() + offset, 1); Aira.router.go(["marketing"], { tab: "calendar", view: mode, d: dayKey(next) }); };
    const title = mode === "week" ? `Week of ${start.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}` : start.toLocaleDateString([], { month: "long", year: "numeric" });
    const toolbar = h("div", { class: "btn-row", style: { justifyContent: "space-between" } },
      h("div", { class: "btn-row" }, S.button("‹", () => go(-1), { kind: "secondary sm", title: "Previous" }), h("strong", { text: title }), S.button("›", () => go(1), { kind: "secondary sm", title: "Next" }), S.button("Today", () => Aira.router.go(["marketing"], { tab: "calendar", view: mode }), { kind: "ghost sm" })),
      h("div", { class: "btn-row" },
        S.button(mode === "week" ? "Month view" : "Week view", () => Aira.router.go(["marketing"], { tab: "calendar", view: mode === "week" ? "month" : "week", d: dayKey(anchor) }), { kind: "secondary sm" }),
        S.button("New post", () => editPost({ status: "draft" }), { kind: "sm" }),
        S.button("Write with AI", () => writeWithAi(), { kind: "secondary sm" }),
        S.button("30-day plan", () => planDialog(), { kind: "secondary sm" })));
    const cells = [];
    const days = mode === "week" ? 7 : 42;
    const today = dayKey(new Date());
    for (let index = 0; index < days; index += 1) {
      const date = new Date(gridStart); date.setDate(gridStart.getDate() + index);
      const key = dayKey(date);
      const outside = mode === "month" && date.getMonth() !== start.getMonth();
      const posts = byDay[key] || [];
      cells.push(h("div", { class: `mk-cal-day${key === today ? " today" : ""}${outside ? " outside" : ""}`, "aria-current": key === today ? "date" : null },
        h("div", { class: "muted small mk-cal-date", text: mode === "week" ? date.toLocaleDateString([], { weekday: "short", day: "numeric" }) : String(date.getDate()) }),
        ...posts.map((post) => {
          const at = new Date(post.scheduled_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
          const name = post.title || post.body || "";
          return h("button", { type: "button", class: `mk-cal-post ${post.status}`, title: post.title || post.body, "aria-label": `${at} · ${CHANNEL_NAME[post.channel]}: ${name}`, onclick: () => openPost(post) },
            h("span", { class: "muted small mk-cal-when" }, h("span", { text: at }), h("span", { class: "mk-cal-channel", text: ` · ${CHANNEL_NAME[post.channel]}` })),
            h("span", { class: "mk-cal-title", text: name }));
        })));
    }
    const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => h("div", { class: "muted small mk-cal-wd", "aria-hidden": "true", text: day }));
    body.append(
      S.section(null, toolbar,
        h("div", { class: "mk-cal-scroll" }, h("div", { class: `mk-cal ${mode}` }, ...(mode === "month" ? weekdays : []), ...cells))),
      S.section(mode === "week" ? "Posts this week" : "Posts this month", table({
        columns: [
          { key: "scheduled_at", label: "When", render: (p) => S.when(p.scheduled_at) },
          { key: "channel", label: "Channel", render: (p) => CHANNEL_NAME[p.channel] },
          { key: "title", label: "Post", render: (p) => p.title || (p.body || "").slice(0, 80) },
          { key: "status", label: "Status", render: (p) => S.pill(p.status, STATUS_TONES) },
        ],
        rows: data.posts.filter((post) => { const when = new Date(post.scheduled_at); return when >= start && when < end; }),
        onRow: openPost,
        empty: Aira.ui.empty("Nothing planned here", "Start with the 30-day plan, or write one post with AI.", h("div", { class: "btn-row" }, S.button("30-day plan", () => planDialog(), { kind: "sm" }), S.button("Write with AI", () => writeWithAi(), { kind: "secondary sm" }))),
      }, "mk-posts")),
      S.section("Not scheduled yet", h("p", { class: "muted", text: "Drafts and ideas without a date. Open one and choose when to post it to see it on the calendar." }), table({
        columns: [
          { key: "channel", label: "Channel", render: (p) => CHANNEL_NAME[p.channel] },
          { key: "title", label: "Post", render: (p) => p.title || (p.body || "").slice(0, 80) },
          { key: "status", label: "Status", render: (p) => S.pill(p.status, STATUS_TONES) },
          { key: "created_at", label: "Written", render: (p) => Aira.fmt.ago(p.created_at) },
        ],
        rows: unscheduled,
        onRow: openPost,
        empty: "No drafts waiting for a date",
      }, "mk-drafts")),
    );
  }

  // ======================================================================= overview
  async function overviewTab(body) {
    const data = await M("/overview");
    const growth = data.growth_percent;
    body.append(...[
      S.kpis([
        { label: "Subscribers", value: data.subscribers, hint: `+${data.new_subscribers_month} this month${growth === null ? "" : ` (${growth >= 0 ? "+" : ""}${growth}% vs last)`}`, tone: "good" },
        { label: "Leads this month", value: data.leads_month, hint: data.new_leads ? `${data.new_leads} waiting for a reply` : "All answered", tone: data.new_leads ? "warn" : "" },
        { label: "Form conversion", value: pct(data.conversion_percent), hint: `${data.form_submissions} of ${data.form_views} views` },
        { label: "Clicks this month", value: data.clicks_month, hint: "Tracked links and bio page" },
        { label: "Scheduled this week", value: data.scheduled_week, hint: `${data.published_month} published this month` },
        { label: "Paid by leads this month", value: S.money(data.earned_month_cents), hint: `${data.customers_month} paying customer${data.customers_month === 1 ? "" : "s"} · ${S.money(data.open_value_cents)} still open`, tone: data.earned_month_cents ? "good" : "" },
      ]),
      data.due_posts.length ? S.section("Ready to post now", h("p", { class: "muted", text: "Aira does not post for you: copy the text, post it, then mark it published." }), table({
        columns: [
          { key: "scheduled_at", label: "Due", render: (p) => S.when(p.scheduled_at) },
          { key: "channel", label: "Channel", render: (p) => CHANNEL_NAME[p.channel] },
          { key: "title", label: "Post", render: (p) => p.title || (p.body || "").slice(0, 60) },
          { key: "copy", label: "", render: (p) => S.button("Copy text", () => S.copy([p.body, p.hashtags].filter(Boolean).join("\n\n")), { kind: "ghost sm" }) },
        ],
        rows: data.due_posts,
        onRow: openPost,
      })) : null,
      data.follow_ups.length ? S.section(`Follow-ups due (${data.follow_ups_due})`, table({
        columns: [
          { key: "follow_up_on", label: "Due", render: (r) => S.day(r.follow_up_on) },
          { key: "name", label: "Who", render: (r) => r.company || r.name || r.email || r.phone },
          { key: "value_cents", label: "Value", num: true, render: (r) => (r.value_cents ? S.money(r.value_cents) : "") },
          { key: "status", label: "Status", render: (r) => S.pill(r.status, STATUS_TONES) },
        ],
        rows: data.follow_ups,
        onRow: openSubmission,
      }), h("div", { class: "btn-row" }, S.button("See all follow-ups", () => Aira.router.go(["marketing"], { tab: "forms", due: "1" }), { kind: "secondary sm" }))) : null,
      S.grid(
        S.section("Get set up", S.checklist(data.checklist), h("div", { class: "btn-row" }, S.button("30-day plan", () => planDialog(), { kind: "secondary sm" }), S.button("New form", () => editForm(), { kind: "secondary sm" }), S.button("Tracked link", () => editLink(), { kind: "secondary sm" }))),
        S.section("Best campaign", data.best_campaign
          ? h("div", {}, h("strong", { text: data.best_campaign.name }), S.kpis([{ label: "ROI", value: pct(data.best_campaign.roi_percent), tone: (data.best_campaign.roi_percent || 0) >= 0 ? "good" : "bad" }, { label: "Leads", value: data.best_campaign.leads }, { label: "Clicks", value: data.best_campaign.clicks }]))
          : Aira.ui.empty("No campaign results yet", "Tie links and forms to a campaign to compare what works.", canCreate() ? S.button("New campaign", () => editCampaign(), { kind: "secondary sm" }) : null)),
      ),
      S.grid(
        S.section("Coming up", table({ columns: [{ key: "scheduled_at", label: "When", render: (p) => S.when(p.scheduled_at) }, { key: "channel", label: "Channel", render: (p) => CHANNEL_NAME[p.channel] }, { key: "title", label: "Post", render: (p) => p.title || (p.body || "").slice(0, 60) }], rows: data.upcoming, onRow: openPost, empty: Aira.ui.empty("Nothing scheduled", "Plan a month of posts in one go, or open the calendar.", h("div", { class: "btn-row" }, S.button("30-day plan", () => planDialog(), { kind: "secondary sm" }), h("a", { class: "btn ghost sm", href: "#/marketing?tab=calendar", text: "Open the calendar" }))) })),
        S.section("Newest leads", table({ columns: [{ key: "created_at", label: "When", render: (r) => Aira.fmt.ago(r.created_at) }, { key: "name", label: "Who", render: (r) => r.name || r.email || r.phone }, { key: "status", label: "Status", render: (r) => S.pill(r.status, STATUS_TONES) }], rows: data.recent_leads, onRow: openSubmission, empty: Aira.ui.empty("No leads yet", "Share a lead form in your bio and posts: every enquiry lands here.", h("a", { class: "btn secondary sm", href: "#/marketing?tab=forms", text: "Open forms" })) })),
      ),
    ].filter(Boolean));
  }

  // ======================================================================= campaigns
  async function editCampaign(campaign = {}) {
    return S.edit({
      title: campaign.id ? "Edit campaign" : "New campaign",
      fields: [
        { name: "name", label: "Name", required: true, placeholder: "Autumn colour launch" },
        { name: "goal", label: "Goal", placeholder: "40 new bookings" },
        { name: "starts_on", label: "Starts", type: "date" },
        { name: "ends_on", label: "Ends", type: "date" },
        { name: "budget_cents", label: "Budget", type: "money" },
        { name: "spend_cents", label: "Spent so far", type: "money" },
        { name: "revenue_cents", label: "Other revenue it brought (your estimate)", type: "money", hint: "Leads from its forms that you mark paid add up by themselves; enter only sales Aira cannot see." },
        { name: "status", label: "Status", type: "select", options: S.statusOptions(["active", "paused", "ended"]) },
        { name: "utm_source", label: "UTM source", placeholder: "instagram" },
        { name: "utm_medium", label: "UTM medium", placeholder: "social" },
        { name: "utm_campaign", label: "UTM campaign", placeholder: "made from the name" },
      ],
      values: campaign,
      onSave: async (form) => {
        const saved = campaign.id ? await M(`/campaigns/${campaign.id}`, { method: "PATCH", body: form }) : await M("/campaigns", { method: "POST", body: form });
        rerender();
        return saved.campaign;
      },
    });
  }

  async function campaignsTab(body) {
    const data = await M("/campaigns");
    campaigns = data.campaigns;
    body.append(S.section("Campaigns", h("div", { class: "btn-row" }, S.button("New campaign", () => editCampaign())),
      table({
        columns: [
          { key: "name", label: "Campaign", render: (c) => h("span", {}, h("strong", { text: c.name }), c.goal ? h("div", { class: "muted small", text: c.goal }) : null) },
          { key: "status", label: "Status", render: (c) => S.pill(c.status, STATUS_TONES) },
          { key: "spend_cents", label: "Spend / budget", num: true, render: (c) => `${S.money(c.spend_cents)} / ${S.money(c.budget_cents)}` },
          { key: "clicks", label: "Clicks", num: true, render: (c) => c.results.clicks },
          { key: "leads", label: "Leads", num: true, render: (c) => c.results.leads },
          { key: "subs", label: "Subscribers", num: true, render: (c) => c.results.subscribers },
          { key: "revenue", label: "Revenue", num: true, render: (c) => S.money(c.results.revenue_total_cents) },
          { key: "cpl", label: "Cost per lead", num: true, render: (c) => (c.results.cost_per_lead_cents === null ? "-" : S.money(c.results.cost_per_lead_cents)) },
          { key: "roi", label: "ROI", num: true, render: (c) => (c.results.roi_percent === null ? "-" : S.pill(`${c.results.roi_percent}%`, { [`${c.results.roi_percent}%`]: c.results.roi_percent >= 0 ? "good" : "bad" })) },
        ],
        rows: data.campaigns,
        onRow: openCampaign,
        empty: Aira.ui.empty("No campaigns yet", "A campaign groups posts, links and forms so you can see what each push brought in.", S.button("New campaign", () => editCampaign())),
      }, "mk-campaigns")));
  }

  function openCampaign(campaign) {
    const drawer = Aira.drawer({ title: campaign.name, subtitle: S.pill(campaign.status, STATUS_TONES), body: h("div", { class: "suite-body" },
      S.kpis([{ label: "Clicks", value: campaign.results.clicks }, { label: "Leads", value: campaign.results.leads }, { label: "Subscribers", value: campaign.results.subscribers }, { label: "Posts", value: campaign.results.posts }, { label: "Paying customers", value: campaign.results.customers }, { label: "Paid by leads", value: S.money(campaign.results.paid_cents), tone: campaign.results.paid_cents ? "good" : "" }, { label: "Revenue", value: S.money(campaign.results.revenue_total_cents) }, { label: "ROI", value: pct(campaign.results.roi_percent), tone: campaign.results.roi_percent === null ? "" : campaign.results.roi_percent >= 0 ? "good" : "bad" }, { label: "Budget left", value: S.money(campaign.results.budget_left_cents), tone: campaign.results.budget_left_cents < 0 ? "bad" : "" }]),
      Aira.ui.kv([["Dates", [campaign.starts_on, campaign.ends_on].filter(Boolean).join(" to ")], ["UTM", `source=${campaign.utm_source} · medium=${campaign.utm_medium} · campaign=${campaign.utm_campaign}`]]),
      h("div", { class: "btn-row" }, S.button("Edit", async () => { drawer.close(false); await editCampaign(campaign); }), S.button("Delete", async () => { if (await S.confirmDelete(campaign.name)) { await M(`/campaigns/${campaign.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  // ======================================================================= links
  async function editLink(link = {}) {
    return S.edit({
      title: link.id ? "Edit tracked link" : "New tracked link",
      fields: [
        { name: "destination", label: "Where it goes", type: "url", required: true, placeholder: "https://yoursite.com/book", hint: "Must start with https://." },
        { name: "title", label: "Name", placeholder: "Instagram bio: booking" },
        { name: "slug", label: "Short name", placeholder: "made from the name" },
        { name: "campaign_id", label: "Campaign", type: "select", options: campaignOptions() },
        { name: "utm_source", label: "UTM source", placeholder: "instagram" },
        { name: "utm_medium", label: "UTM medium", placeholder: "social" },
        { name: "utm_campaign", label: "UTM campaign" },
        { name: "utm_content", label: "UTM content", placeholder: "story-1" },
      ],
      values: link,
      onSave: async (form) => {
        const saved = link.id ? await M(`/links/${link.id}`, { method: "PATCH", body: form }) : await M("/links", { method: "POST", body: form });
        rerender();
        return saved.link;
      },
    });
  }

  async function openLink(link) {
    const data = await M(`/links/${link.id}/clicks`);
    const drawer = Aira.drawer({ title: link.title || link.slug, subtitle: link.destination, body: h("div", { class: "suite-body" },
      S.share({ title: "Tracked link", text: "Share this link instead of your page address: every visit is counted, then the visitor lands on your page.", link: data.link.public, onCreate: async () => (await M(`/links/${link.id}/share`, { method: "POST", body: {} })).public, onRevoke: () => M(`/links/${link.id}/share`, { method: "DELETE" }) }),
      S.kpis([{ label: "Clicks", value: data.link.clicks }, { label: "This month", value: data.link.clicks_month }]),
      S.section("Clicks, last 30 days", S.bars(data.days.map((d) => ({ label: d.day.slice(8), value: d.clicks })))),
      Aira.ui.kv([["Lands on", data.link.final_url], ["Campaign", campaignName(link.campaign_id)]]),
      h("div", { class: "btn-row" }, S.button("Edit", async () => { drawer.close(false); await editLink(link); }), S.button("Delete", async () => { if (await S.confirmDelete("this link")) { await M(`/links/${link.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  async function linksTab(body) {
    const data = await M("/links");
    body.append(S.section("Tracked links", h("p", { class: "muted", text: "Use a tracked link in each post, story or ad to see which one brings visitors. UTM tags are added for your website's analytics." }), h("div", { class: "btn-row" }, S.button("New tracked link", () => editLink())),
      table({
        columns: [
          { key: "title", label: "Link", render: (l) => h("span", {}, h("strong", { text: l.title || l.slug }), h("div", { class: "muted small", text: l.destination })) },
          { key: "campaign_id", label: "Campaign", render: (l) => campaignName(l.campaign_id) },
          { key: "clicks_month", label: "This month", num: true },
          { key: "clicks", label: "All clicks", num: true },
          { key: "copy", label: "", render: (l) => (l.public ? S.button("Copy", () => S.copy(S.absolute(l.public.path)), { kind: "ghost sm" }) : "") },
        ],
        rows: data.links,
        onRow: openLink,
        empty: Aira.ui.empty("No tracked links yet", "Create one for your bio, a post or an ad.", S.button("New tracked link", () => editLink())),
      }, "mk-links")));
  }

  // ======================================================================= forms and leads
  const FIELD_LABELS = { name: "Name", email: "Email", phone: "Phone", company: "Company", message: "Message", custom: "Your own question" };

  async function editForm(form = {}) {
    const fields = form.fields || { name: { show: true, required: true }, email: { show: true, required: true }, message: { show: true } };
    const values = { add_subscriber: true, status: "active", ...form };
    for (const [name, spec] of Object.entries(fields)) { values[`show_${name}`] = !!spec.show; values[`req_${name}`] = !!spec.required; }
    return S.edit({
      title: form.id ? "Edit form" : "New lead form",
      wide: true,
      fields: [
        { name: "title", label: "Title", required: true, placeholder: "Book a free consultation" },
        { name: "intro", label: "Introduction", type: "textarea", rows: 3, placeholder: "Tell us what you need and we will reply within a day." },
        { type: "heading", label: "Questions" },
        ...Object.keys(FIELD_LABELS).flatMap((name) => [{ name: `show_${name}`, label: `Ask: ${FIELD_LABELS[name]}`, type: "checkbox" }, { name: `req_${name}`, label: `${FIELD_LABELS[name]} is required`, type: "checkbox" }]),
        { name: "custom_question", label: "Your own question", placeholder: "What date suits you?" },
        { type: "heading", label: "After they send it" },
        { name: "thank_you", label: "Thank-you message", type: "textarea", rows: 2 },
        { name: "add_subscriber", label: "Offer to join your email list", type: "checkbox" },
        { name: "consent_text", label: "Email opt-in text", placeholder: "Yes, send me news and offers by email." },
        { name: "tag", label: "Tag new subscribers", placeholder: "consultation" },
        { name: "campaign_id", label: "Campaign", type: "select", options: campaignOptions() },
        { name: "status", label: "Status", type: "select", options: [["active", "Open"], ["closed", "Closed"]] },
      ],
      values,
      onSave: async (raw) => {
        const body = { title: raw.title, intro: raw.intro, custom_question: raw.custom_question, thank_you: raw.thank_you, add_subscriber: raw.add_subscriber, consent_text: raw.consent_text, tag: raw.tag, campaign_id: raw.campaign_id, status: raw.status, fields: {} };
        for (const name of Object.keys(FIELD_LABELS)) body.fields[name] = { show: raw[`show_${name}`], required: raw[`req_${name}`] };
        const saved = form.id ? await M(`/forms/${form.id}`, { method: "PATCH", body }) : await M("/forms", { method: "POST", body });
        rerender();
        return saved.form;
      },
    });
  }

  function openForm(form) {
    const drawer = Aira.drawer({ title: form.title, subtitle: S.pill(form.status === "active" ? "open" : "closed", { open: "good", closed: "" }), body: h("div", { class: "suite-body" },
      S.share({ title: "Form page", text: "Share it in your bio, posts, ads and email signature.", link: form.public, onCreate: async () => { const made = (await M(`/forms/${form.id}/link`, { method: "POST", body: {} })).public; rerender(); return made; }, onRevoke: () => M(`/forms/${form.id}/link`, { method: "DELETE" }) }),
      S.kpis([{ label: "Views", value: form.views }, { label: "Leads", value: form.submissions }, { label: "Conversion", value: pct(form.conversion_percent) }, { label: "New", value: form.new_submissions, tone: form.new_submissions ? "warn" : "" }]),
      h("div", { class: "btn-row" }, S.button("Edit", async () => { drawer.close(false); await editForm(form); }), S.button("See leads", () => { drawer.close(false); Aira.router.go(["marketing"], { tab: "forms", form: form.id }); }, { kind: "secondary" }), S.button("Delete", async () => { if (await S.confirmDelete(form.title)) { await M(`/forms/${form.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  // A lead from a form or a sponsorship request: reply, set a value and a follow-up date, ask for payment, mark paid.
  function openSubmission(row) {
    const who = row.name || row.email || row.phone || "Lead";
    const form = S.form([
      { name: "status", label: "Status", type: "select", options: S.statusOptions(["new", "contacted", "won", "lost"]) },
      { name: "value_cents", label: "Value of this customer", type: "money", hint: "What the deal is worth. It counts as revenue only when you mark it paid." },
      { name: "follow_up_on", label: "Follow up on", type: "date" },
      { name: "notes", label: "Notes", type: "textarea", rows: 4 },
    ], row);
    const done = (message) => { Aira.toast(message, "success"); drawer.close(false); rerender(); };
    const reply = async () => {
      const template = (await M(`/submissions/${row.id}/reply`)).template;
      const sent = await S.edit({
        title: `Reply to ${who}`,
        wide: true,
        fields: [{ name: "subject", label: "Subject", required: true }, { name: "body", label: "Message", type: "textarea", rows: 10, required: true, hint: `To ${row.email}. It waits in Business hub > Outbox until you approve it.` }],
        values: template,
        submitLabel: "Put in the outbox",
        onSave: (values) => M(`/submissions/${row.id}/reply`, { method: "POST", body: values }),
      });
      if (sent) done("Reply is in the outbox.");
    };
    const askPayment = async () => {
      const asked = await S.edit({
        title: "Ask for payment",
        fields: [{ name: "value_cents", label: "Amount", type: "money", required: true }, { name: "for", label: "What it is for", placeholder: row.package || "Your booking" }],
        values: { value_cents: row.value_cents || null, for: row.package || "" },
        submitLabel: "Put in the outbox",
        onSave: (values) => M(`/submissions/${row.id}/payment-request`, { method: "POST", body: values }),
      });
      if (asked) done("Payment request is in the outbox. Mark the lead paid when the money arrives.");
    };
    const markPaid = async () => {
      const paid = await S.edit({
        title: "Mark paid",
        fields: [{ name: "value_cents", label: "Amount received", type: "money", required: true, hint: "Counts in your revenue in the Business hub." }],
        values: { value_cents: form.read().value_cents || row.value_cents || null },
        submitLabel: "Mark paid",
        onSave: (values) => M(`/submissions/${row.id}/paid`, { method: "POST", body: values }),
      });
      if (paid) done("Marked paid.");
    };
    const drawer = Aira.drawer({ title: row.company && row.source === "media_kit" ? row.company : who, subtitle: h("span", {}, row.paid ? S.pill("paid", { paid: "good" }) : S.pill(row.status, STATUS_TONES), ` ${row.source_label === "Sponsorship" ? "Sponsorship" : row.form_title || "Form"} · ${Aira.fmt.ago(row.created_at)}`), body: h("div", { class: "suite-body" },
      Aira.ui.kv([["Name", row.name], ["Email", row.email ? h("a", { href: `mailto:${row.email}`, text: row.email }) : ""], ["Phone", row.phone ? h("a", { href: `tel:${row.phone}`, text: row.phone }) : ""], ["Company", row.company], ["Package", row.package], [row.source === "media_kit" ? "Dates" : "Answer", row.answer], ["Wants news", row.consent ? "Yes" : "No"], ["Paid", row.paid ? `${S.money(row.value_cents)} · ${S.day(row.paid_at)}` : ""]]),
      row.message ? h("pre", { class: "suite-pre", text: row.message }) : null,
      h("div", { class: "btn-row" },
        row.email ? S.button("Reply by email", reply) : null,
        !row.paid ? S.button("Ask for payment", askPayment, { kind: "secondary" }) : null,
        row.paid ? S.button("Mark not paid", async () => { if (await Aira.confirm({ title: "Mark not paid", message: "Take this payment off your revenue? Use this for a mistake or a refund.", confirmLabel: "Mark not paid" })) { await M(`/submissions/${row.id}/paid`, { method: "DELETE" }); done("Marked not paid."); } }, { kind: "secondary" }) : S.button("Mark paid", markPaid, { kind: "secondary" })),
      form.node,
      h("div", { class: "btn-row" },
        S.button("Save", async () => { form.setError(""); try { await M(`/submissions/${row.id}`, { method: "PATCH", body: form.read() }); done("Saved."); } catch (error) { form.setError(error.message); } }),
        S.button("Delete", async () => { if (await S.confirmDelete("this lead")) { await M(`/submissions/${row.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  const leadFrom = (r) => (r.source === "media_kit" ? `Sponsorship${r.package ? `: ${r.package}` : ""}` : r.form_title || "Form");

  async function formsTab(body, route) {
    const query = route.query;
    const filters = { form_id: query.form || "", status: query.status || "", source: query.source || "", due: query.due || "", q: query.q || "" };
    const [forms, leads] = await Promise.all([M("/forms"), M(`/submissions${qs(filters)}`)]);
    const goWith = (change) => Aira.router.go(["marketing"], { ...query, tab: "forms", ...change });
    const search = h("input", { type: "search", placeholder: "Search name, email, company or notes", value: filters.q, "aria-label": "Search leads" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") goWith({ q: search.value.trim() }); });
    const statusFilter = Aira.ui.select([["", "Any status"], ...S.statusOptions(["new", "contacted", "won", "lost"])], filters.status, { "aria-label": "Status" });
    statusFilter.addEventListener("change", () => goWith({ status: statusFilter.value }));
    const sourceFilter = Aira.ui.select([["", "Forms and sponsorships"], ["form", "Forms"], ["media_kit", "Sponsorships"]], filters.source, { "aria-label": "Where it came from" });
    sourceFilter.addEventListener("change", () => goWith({ source: sourceFilter.value }));
    const due = h("label", { class: "suite-check" }, h("input", { type: "checkbox", checked: !!filters.due, onchange: (event) => goWith({ due: event.target.checked ? "1" : "" }) }), h("span", { text: "Follow-ups due" }));
    const filtered = Object.values(filters).some(Boolean);
    const exportQuery = qs({ ...filters, format: "csv" });
    body.append(
      S.section("Lead forms", h("div", { class: "btn-row" }, S.button("New form", () => editForm())),
        table({
          columns: [
            { key: "title", label: "Form", render: (f) => h("strong", { text: f.title }) },
            { key: "status", label: "Status", render: (f) => S.pill(f.status === "active" ? "open" : "closed", { open: "good", closed: "" }) },
            { key: "views", label: "Views", num: true },
            { key: "submissions", label: "Leads", num: true },
            { key: "conversion_percent", label: "Conversion", num: true, render: (f) => pct(f.conversion_percent) },
            { key: "shared", label: "Shared", render: (f) => (f.public ? "Yes" : "No") },
          ],
          rows: forms.forms,
          onRow: openForm,
          empty: Aira.ui.empty("No forms yet", "A form turns visitors into leads you can reply to.", S.button("New form", () => editForm())),
        }, "mk-forms")),
      S.section(filters.form_id ? "Leads from this form" : "Leads inbox",
        filterBar(search, statusFilter, sourceFilter, due),
        h("div", { class: "btn-row" }, filtered ? S.button("Show all leads", () => Aira.router.go(["marketing"], { tab: "forms" }), { kind: "ghost sm" }) : null, S.button("Download CSV", () => csv(`/submissions${exportQuery}`, "leads.csv"), { kind: "secondary sm" }), h("span", { class: "muted small", text: `${leads.total} lead${leads.total === 1 ? "" : "s"}` })),
        table({
          columns: [
            { key: "created_at", label: "When", render: (r) => Aira.fmt.ago(r.created_at) },
            { key: "name", label: "Who", render: (r) => h("span", {}, h("strong", { text: (r.source === "media_kit" && r.company) || r.name || r.email || r.phone }), h("div", { class: "muted small", text: [r.source === "media_kit" ? r.name : "", r.email, r.phone].filter(Boolean).join(" · ") }), h("div", { class: "muted small mk-phone-only", text: `${Aira.fmt.ago(r.created_at)} · ${leadFrom(r)}` })) },
            { key: "form_title", label: "From", render: leadFrom },
            { key: "message", label: "Message", render: (r) => (r.message || r.answer || "").slice(0, 80) },
            { key: "value_cents", label: "Value", num: true, render: (r) => (r.value_cents ? S.money(r.value_cents) : "") },
            { key: "status", label: "Status", render: (r) => h("span", {}, r.paid ? S.pill("paid", { paid: "good" }) : S.pill(r.status, STATUS_TONES), r.follow_up_due ? h("div", { class: "muted small", text: `Follow up ${S.day(r.follow_up_on)}` }) : null) },
          ],
          rows: leads.submissions,
          onRow: openSubmission,
          empty: filtered ? Aira.ui.empty("No leads match", "Change the filters or search for something else.") : Aira.ui.empty("No leads yet", "Share a form or your media kit: every enquiry lands here.", forms.forms.length ? S.button("Share a form", () => openForm(forms.forms[0]), { kind: "secondary sm" }) : S.button("New form", () => editForm(), { kind: "secondary sm" })),
        }, "mk-inbox")),
    );
  }

  // ======================================================================= bio page
  async function bioTab(body) {
    const data = await M("/bio");
    const form = S.form([
      { name: "title", label: "Page title", placeholder: "Your business name" },
      { name: "bio", label: "Short bio", type: "textarea", rows: 3, placeholder: "Colour specialists in Austin. Book in 30 seconds." },
      { name: "avatar_url", label: "Picture link", type: "url", placeholder: "https://.../photo.jpg" },
      { name: "featured_form_id", label: "Featured form (a big button at the top)", type: "select", options: [["", "None"], ...data.forms.map((f) => [f.id, `${f.title}${f.shared ? "" : " (share it first)"}`])] },
      { name: "featured_label", label: "Button label", placeholder: "Get in touch" },
    ], data.profile);
    const editBioLink = (link = {}) => S.edit({
      title: link.id ? "Edit link" : "Add link",
      fields: [{ name: "label", label: "Label", required: true, placeholder: "Book an appointment" }, { name: "url", label: "Link", type: "url", required: true, placeholder: "https://" }, { name: "highlight", label: "Highlight it", type: "checkbox" }],
      values: link,
      onSave: async (values) => { if (link.id) await M(`/bio/links/${link.id}`, { method: "PATCH", body: values }); else await M("/bio/links", { method: "POST", body: values }); rerender(); },
    });
    const rows = data.links.map((link) => h("li", { style: { display: "flex", gap: "8px", alignItems: "center", padding: "8px 0", borderBottom: "1px solid var(--line)" } },
      h("div", { style: { flex: "1", minWidth: "0" } }, h("strong", { text: `${link.highlight ? "★ " : ""}${link.label}` }), h("div", { class: "muted small", style: { overflowWrap: "anywhere" }, text: `${link.url} · ${link.clicks} clicks` })),
      S.button("↑", async () => { await M(`/bio/links/${link.id}/move`, { method: "POST", body: { direction: "up" } }); rerender(); }, { kind: "ghost sm", title: "Move up" }),
      S.button("↓", async () => { await M(`/bio/links/${link.id}/move`, { method: "POST", body: { direction: "down" } }); rerender(); }, { kind: "ghost sm", title: "Move down" }),
      S.button("Edit", () => editBioLink(link), { kind: "ghost sm" }),
      S.button("Remove", async () => { if (await S.confirmDelete(link.label)) { await M(`/bio/links/${link.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" })));
    body.append(
      S.share({ title: "Your link-in-bio page", text: "One link for your Instagram, TikTok and email signature with everything people need.", link: data.public, onCreate: async () => (await M("/bio/share", { method: "POST", body: {} })).public, onRevoke: () => M("/bio/share", { method: "DELETE" }) }),
      S.grid(
        S.section("Profile", form.node, h("div", { class: "btn-row" }, S.button("Save", async () => { form.setError(""); try { await M("/bio", { method: "POST", body: form.read() }); Aira.toast("Saved.", "success"); } catch (error) { form.setError(error.message); } }))),
        S.section("Links", rows.length ? h("ul", { style: { listStyle: "none", margin: "0", padding: "0" } }, ...rows) : Aira.ui.empty("No links yet", "Add your booking page, shop, latest video or menu."), h("div", { class: "btn-row" }, S.button("Add link", () => editBioLink()))),
      ),
    );
  }

  // ======================================================================= media kit
  // A public "work with me" page for creators and businesses: audience numbers, a rate card, and a form brands use
  // to ask for a sponsorship. Requests land in the leads inbox with their budget as the value.
  async function mediaKitTab(body) {
    const data = await M("/media-kit");
    const profile = data.profile;
    const AUDIENCE_LABELS = { email: "Email list elsewhere", blog: "Blog readers a month", whatsapp: "WhatsApp channel" };
    const audienceInputs = {};
    const audienceGrid = h("div", { class: "mk-audience" }, ...data.channels.map((channel) => {
      const input = h("input", { type: "number", min: 0, step: 1, inputmode: "numeric", name: `aud_${channel.id}`, placeholder: "0" });
      input.value = profile.audience[channel.id] ? String(profile.audience[channel.id]) : "";
      audienceInputs[channel.id] = input;
      return Aira.ui.field(AUDIENCE_LABELS[channel.id] || channel.label, input);
    }));
    const form = S.form([
      { name: "headline", label: "Headline", placeholder: "Home and garden videos for first-time buyers" },
      { name: "niche", label: "What you make content about", placeholder: "Home, DIY, gardening" },
      { name: "about", label: "About you", type: "textarea", rows: 4, placeholder: "Who you are, who watches you and what brands you have worked with." },
    ], profile);
    const settings = S.form([
      { name: "show_subscribers", label: `Show your email list size (${data.subscribers} subscribed)`, type: "checkbox" },
      { type: "heading", label: "Requests" },
      { name: "open", label: "Take sponsorship requests", type: "checkbox" },
      { name: "thank_you", label: "Message after a brand sends a request", type: "textarea", rows: 2, placeholder: "Thank you! We will reply by email within two working days." },
    ], profile);
    const save = async () => {
      settings.setError("");
      const raw = { ...form.read(), ...settings.read() };
      const audience = {};
      for (const [channel, input] of Object.entries(audienceInputs)) audience[channel] = input.value.trim() || 0;
      try {
        await M("/media-kit", { method: "POST", body: { headline: raw.headline, niche: raw.niche, about: raw.about, audience, show_subscribers: raw.show_subscribers, open: raw.open, thank_you: raw.thank_you } });
        Aira.toast("Saved.", "success");
        rerender();
      } catch (error) { settings.setError(error.message); }
    };
    const editPackage = (pkg = {}) => S.edit({
      title: pkg.id ? "Edit package" : "New package",
      fields: [
        { name: "name", label: "Name", required: true, placeholder: "One Instagram reel" },
        { name: "price_cents", label: "Price", type: "money", hint: "Leave empty for \"ask me\"." },
        { name: "details", label: "What is included", type: "textarea", rows: 3, placeholder: "A 30 to 60 second reel, one story with a link, usage rights for 30 days." },
        { name: "days", label: "Days to deliver", type: "number", min: 0, max: 365 },
        { name: "active", label: "Show it on the page", type: "checkbox" },
      ],
      values: { active: true, ...pkg },
      onSave: async (raw) => { const body = { ...raw, price_cents: raw.price_cents || 0, days: raw.days || 0 }; if (pkg.id) await M(`/media-kit/packages/${pkg.id}`, { method: "PATCH", body }); else await M("/media-kit/packages", { method: "POST", body }); rerender(); },
    });
    body.append(
      S.share({ title: "Your media kit", text: "Send it to brands, or put it in your bio: your audience, your prices and a form to ask for a sponsorship.", link: data.public, onCreate: async () => (await M("/media-kit/share", { method: "POST", body: {} })).public, onRevoke: () => M("/media-kit/share", { method: "DELETE" }) }),
      S.kpis([
        { label: "Total reach", value: data.reach.toLocaleString(), hint: "Followers plus your email list if shown" },
        { label: "Sponsorship requests", value: data.requests, hint: data.waiting ? `${data.waiting} waiting for a reply` : "All answered", tone: data.waiting ? "warn" : "" },
        { label: "Earned from sponsorships", value: S.money(data.earned_cents), tone: data.earned_cents ? "good" : "" },
      ]),
      S.grid(
        S.section("Profile", h("p", { class: "muted", text: "Your picture and name come from the Bio page." }), form.node, h("h3", { class: "suite-h3", text: "Your audience (followers or subscribers)" }), audienceGrid, settings.node, h("div", { class: "btn-row" }, S.button("Save", save))),
        S.section("Rate card",
          data.packages.length ? h("ul", { class: "mk-packages" }, ...data.packages.map((pkg) => h("li", {},
            h("div", { class: "mk-package-main" },
              h("strong", { text: pkg.name }),
              h("span", { class: "mk-price", text: pkg.price_cents ? S.money(pkg.price_cents) : "Ask me" }),
              pkg.details ? h("div", { class: "muted small", text: pkg.details }) : null,
              h("div", { class: "muted small", text: [pkg.days ? `Delivered in ${pkg.days} day${pkg.days === 1 ? "" : "s"}` : "", pkg.active ? "" : "Hidden"].filter(Boolean).join(" · ") })),
            h("div", { class: "btn-row" },
              S.button("Edit", () => editPackage(pkg), { kind: "ghost sm" }),
              S.button("Delete", async () => { if (await S.confirmDelete(pkg.name)) { await M(`/media-kit/packages/${pkg.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }))))) : Aira.ui.empty("No packages yet", "Add what brands can buy: a post, a reel, a video, a newsletter mention."),
          h("div", { class: "btn-row" }, S.button("Add package", () => editPackage()), S.button("See requests", () => Aira.router.go(["marketing"], { tab: "forms", source: "media_kit" }), { kind: "secondary" }))),
      ),
    );
  }

  // ======================================================================= subscribers
  async function subscribersTab(body, route) {
    const params = new URLSearchParams();
    if (route.query.status) params.set("status", route.query.status);
    if (route.query.tag) params.set("tag", route.query.tag);
    if (route.query.q) params.set("q", route.query.q);
    const data = await M(`/subscribers?${params}`);
    const search = h("input", { type: "search", placeholder: "Search email or name", value: route.query.q || "", "data-search": "1", "aria-label": "Search subscribers" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") Aira.router.go(["marketing"], { ...route.query, tab: "subscribers", q: search.value.trim() }); });
    const statusFilter = Aira.ui.select([["", "Everyone"], ["subscribed", "Subscribed"], ["unsubscribed", "Unsubscribed"]], route.query.status || "", { "aria-label": "Status" });
    statusFilter.addEventListener("change", () => Aira.router.go(["marketing"], { ...route.query, tab: "subscribers", status: statusFilter.value }));
    const tagFilter = Aira.ui.select([["", "Any tag"], ...data.tags.map((t) => [t, t])], route.query.tag || "", { "aria-label": "Tag" });
    tagFilter.addEventListener("change", () => Aira.router.go(["marketing"], { ...route.query, tab: "subscribers", tag: tagFilter.value }));
    const importMany = () => S.edit({ title: "Import subscribers", fields: [{ name: "csv", label: "Paste CSV: email,name,tags (one per line)", type: "textarea", rows: 10, required: true, hint: "Only add people who agreed to hear from you. Addresses that unsubscribed are skipped." }], submitLabel: "Import", onSave: async (values) => { const result = await M("/subscribers/import", { method: "POST", body: values }); Aira.toast(`${result.added} added, ${result.updated} updated, ${result.skipped} skipped, ${result.invalid} invalid.`, "success"); rerender(); } });
    body.append(
      S.kpis([{ label: "Subscribed", value: data.counts.subscribed, tone: "good" }, { label: "Unsubscribed", value: data.counts.unsubscribed }]),
      S.section("Subscribers",
        filterBar(search, statusFilter, tagFilter),
        h("div", { class: "btn-row" }, S.button("Add", addSubscriber, { kind: "sm" }), S.button("Import", importMany, { kind: "secondary sm" }), S.button("Download CSV", () => csv(`/subscribers?format=csv&${params}`, "subscribers.csv"), { kind: "secondary sm" })),
        table({
          columns: [
            { key: "email", label: "Email", render: (s) => h("span", {}, h("strong", { text: s.email }), s.name ? h("div", { class: "muted small", text: s.name }) : null) },
            { key: "tags", label: "Tags", render: (s) => s.tags.join(", ") },
            { key: "source", label: "Source" },
            { key: "status", label: "Status", render: (s) => S.pill(s.status, STATUS_TONES) },
            { key: "created_at", label: "Joined", render: (s) => S.day(s.created_at) },
          ],
          rows: data.subscribers,
          onRow: openSubscriber,
          empty: params.toString() ? Aira.ui.empty("Nobody matches", "Change the filters or search for something else.", S.button("Show everyone", () => Aira.router.go(["marketing"], { tab: "subscribers" }), { kind: "secondary sm" })) : Aira.ui.empty("No subscribers yet", "Turn on the email opt-in in a lead form, or import people who agreed to hear from you.", h("div", { class: "btn-row" }, S.button("Add subscriber", addSubscriber, { kind: "sm" }), S.button("Import", importMany, { kind: "secondary sm" }))),
        }, "mk-subscribers")),
    );
  }

  const addSubscriber = () => S.edit({ title: "Add subscriber", fields: [{ name: "email", label: "Email", type: "email", required: true }, { name: "name", label: "Name" }, { name: "tags", label: "Tags", placeholder: "vip, autumn" }], onSave: async (values) => { await M("/subscribers", { method: "POST", body: values }); rerender(); } });

  function openSubscriber(row) {
    const drawer = Aira.drawer({ title: row.name || row.email, subtitle: S.pill(row.status, STATUS_TONES), body: h("div", { class: "suite-body" },
      Aira.ui.kv([["Email", row.email], ["Source", row.source], ["Tags", row.tags.join(", ")], ["Opted in", S.when(row.consent_at)], ["Joined", S.when(row.created_at)], ["Unsubscribed", S.when(row.unsubscribed_at)]]),
      h("div", { class: "btn-row" },
        S.button("Edit", async () => { drawer.close(false); await S.edit({ title: "Edit subscriber", fields: [{ name: "name", label: "Name" }, { name: "tags", label: "Tags" }], values: { name: row.name, tags: row.tags.join(", ") }, onSave: async (values) => { await M(`/subscribers/${row.id}`, { method: "PATCH", body: values }); rerender(); } }); }),
        row.status === "subscribed" ? S.button("Unsubscribe", async () => { await M(`/subscribers/${row.id}`, { method: "PATCH", body: { status: "unsubscribed" } }); drawer.close(false); rerender(); }, { kind: "secondary" }) : null,
        S.button("Delete", async () => { if (await S.confirmDelete(row.email)) { await M(`/subscribers/${row.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  // ======================================================================= newsletters
  async function editNewsletter(letter = {}) {
    return S.edit({
      title: letter.id ? "Edit newsletter" : "New newsletter",
      wide: true,
      fields: [
        { name: "subject", label: "Subject", required: true, placeholder: "Three looks for autumn (and 10% off this week)" },
        { name: "body", label: "Message", type: "textarea", rows: 12, required: true, hint: "Each copy starts with the subscriber's first name and ends with their own unsubscribe link." },
        { name: "audience_tag", label: "Only subscribers tagged", placeholder: "leave empty for everyone" },
      ],
      values: letter,
      onSave: async (form) => { const saved = letter.id ? await M(`/newsletters/${letter.id}`, { method: "PATCH", body: form }) : await M("/newsletters", { method: "POST", body: form }); rerender(); return saved.newsletter; },
    });
  }

  async function queueNewsletter(letter) {
    if (!letter.remaining) { Aira.toast("Everyone in this audience already has it."); return; }
    const cap = newsletterCap;
    const count = Math.min(letter.remaining, cap);
    const ok = await Aira.confirm({ title: "Send this newsletter", message: `Queue "${letter.subject}" for ${count} subscriber${count === 1 ? "" : "s"}${letter.remaining > cap ? ` (the first ${cap} of ${letter.remaining}; queue again for the rest)` : ""}? The messages wait in Business hub > Outbox until you approve and send them.`, confirmLabel: `Queue ${count}` });
    if (!ok) return;
    const result = await M(`/newsletters/${letter.id}/queue`, { method: "POST", body: { confirm: true } });
    Aira.toast(`${result.queued} queued${result.remaining ? `, ${result.remaining} left` : ""}.`, "success");
    rerender();
  }

  function openNewsletter(letter) {
    const drawer = Aira.drawer({ title: letter.subject, subtitle: S.pill(letter.status, STATUS_TONES), body: h("div", { class: "suite-body" }, h("pre", { class: "suite-pre", text: letter.body }),
      h("div", { class: "btn-row" }, S.button("Edit", async () => { drawer.close(false); await editNewsletter(letter); }), letter.remaining ? S.button("Queue send", async () => { drawer.close(false); await queueNewsletter(letter); }, { kind: "secondary" }) : null, S.button("Delete", async () => { if (await S.confirmDelete(letter.subject)) { await M(`/newsletters/${letter.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  async function newslettersTab(body) {
    const data = await M("/newsletters");
    if (data.cap) newsletterCap = data.cap;
    const queue = queueNewsletter;
    body.append(S.section("Newsletters", h("p", { class: "muted", text: "Keep your list warm: one useful email a week or two. Messages go out from your own mail server after you approve them in the outbox." }), h("div", { class: "btn-row" }, S.button("New newsletter", () => editNewsletter()), h("a", { class: "btn secondary", href: "#/hub?tab=outbox", text: "Open the outbox" })),
      table({
        columns: [
          { key: "subject", label: "Subject", render: (n) => h("strong", { text: n.subject }) },
          { key: "audience_tag", label: "Audience", render: (n) => n.audience_tag || "Everyone" },
          { key: "status", label: "Status", render: (n) => S.pill(n.status, STATUS_TONES) },
          { key: "queued_count", label: "Queued", num: true },
          { key: "sent", label: "Sent", num: true },
          { key: "remaining", label: "Waiting", num: true },
          { key: "actions", label: "", render: (n) => (n.remaining ? h("div", { class: "btn-row" }, S.button("Queue send", () => queue(n), { kind: "sm" })) : h("span", { class: "muted small", text: n.queued_count ? "Everyone has it" : "Nobody to send to yet" })) },
        ],
        rows: data.newsletters,
        onRow: openNewsletter,
        empty: Aira.ui.empty("No newsletters yet", "Write one to your subscribers.", S.button("New newsletter", () => editNewsletter())),
      }, "mk-newsletters")));
  }

  // ======================================================================= search, "+ New" and opening a record
  // The kinds of record the search box finds (people first), how each is named in the list and which tab it lives in.
  const RESULT_TONES = { ...STATUS_TONES, open: "good", closed: "", paid: "good" };
  const KINDS = [
    { key: "submissions", kind: "submission", title: "Leads from forms and sponsorships", tab: "forms", icon: "leads", more: "forms",
      label: (r) => (r.source === "media_kit" && r.company) || r.name || r.email || r.phone || "Lead", sub: (r) => [r.email, leadFrom(r)].filter(Boolean).join(" · "), status: (r) => (r.paid ? "paid" : r.status) },
    { key: "subscribers", one: "Subscriber", kind: "subscriber", title: "Subscribers", tab: "subscribers", icon: "recruiters", more: "subscribers",
      label: (r) => r.name || r.email, sub: (r) => [r.name ? r.email : "", (r.tags || []).join(", ")].filter(Boolean).join(" · "), status: (r) => r.status },
    { key: "posts", one: "Post", kind: "post", title: "Posts", tab: "calendar", icon: "mkPost",
      label: (r) => r.title || (r.body || "").slice(0, 60) || `${CHANNEL_NAME[r.channel] || "A"} post`, sub: (r) => [CHANNEL_NAME[r.channel], r.scheduled_at ? S.when(r.scheduled_at) : "Not scheduled"].filter(Boolean).join(" · "), status: (r) => r.status },
    { key: "campaigns", one: "Campaign", kind: "campaign", title: "Campaigns", tab: "campaigns", icon: "ads",
      label: (r) => r.name, sub: (r) => [r.goal, r.results ? `${r.results.leads} lead${r.results.leads === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · "), status: (r) => r.status },
    { key: "forms", one: "Lead form", kind: "form", title: "Lead forms", tab: "forms", icon: "applications",
      label: (r) => r.title, sub: (r) => `${r.submissions} lead${r.submissions === 1 ? "" : "s"} · ${r.views} view${r.views === 1 ? "" : "s"}`, status: (r) => (r.status === "active" ? "open" : "closed") },
    { key: "links", one: "Tracked link", kind: "link", title: "Tracked links", tab: "links", icon: "mkLink",
      label: (r) => r.title || r.slug, sub: (r) => r.destination, status: () => "" },
    { key: "newsletters", one: "Newsletter", kind: "newsletter", title: "Newsletters", tab: "newsletters", icon: "news",
      label: (r) => r.subject, sub: (r) => (r.audience_tag ? `To subscribers tagged ${r.audience_tag}` : "To every subscriber"), status: (r) => r.status },
  ];
  const KIND = Object.fromEntries(KINDS.map((spec) => [spec.kind, spec]));
  const OPENERS = { submission: openSubmission, subscriber: openSubscriber, post: openPost, campaign: openCampaign, form: openForm, link: openLink, newsletter: openNewsletter };
  // The list each kind comes from, for an ?open= link that arrives without its record (a link someone pasted).
  const LISTS = { submission: ["/submissions", "submissions"], subscriber: ["/subscribers", "subscribers"], post: ["/posts", "posts"], campaign: ["/campaigns", "campaigns"], form: ["/forms", "forms"], link: ["/links", "links"], newsletter: ["/newsletters", "newsletters"] };
  const CREATE = {
    post: () => editPost({ status: "draft" }),
    ai: () => writeWithAi(),
    campaign: () => editCampaign(),
    form: () => editForm(),
    link: () => editLink(),
    newsletter: () => editNewsletter(),
    subscriber: () => addSubscriber(),
  };
  const CREATE_TAB = { post: "calendar", ai: "calendar", campaign: "campaigns", form: "forms", link: "links", newsletter: "newsletters", subscriber: "subscribers" };
  const hasLeads = () => !Aira.cloud || (typeof Aira.cloud.has === "function" && Aira.cloud.has("leads"));
  const salesLabel = (lead) => lead.name || lead.company || "Lead";
  const salesSub = (lead) => ["Sales lead", ...(lead.kind === "person" ? [lead.title, lead.company] : [lead.industry, lead.location])].filter(Boolean).join(" · ");

  // One round trip to each backend; the same words within a few seconds are answered from memory (the box asks on
  // every pause in typing). Edits clear it (rerender).
  const searchCache = new Map();
  async function findEverything(query, limit) {
    const key = `${limit}|${query.toLowerCase()}`;
    const hit = searchCache.get(key);
    if (hit && Date.now() - hit.at < 5000) return hit.value;
    const q = encodeURIComponent(query);
    const [mine, sales] = await Promise.all([
      M(`/search?q=${q}&limit=${limit}`, { silent: true }).catch(() => null),
      // A hosted plan without Leads would answer 402 and open the upgrade prompt on every keystroke: ask only with it.
      hasLeads() ? Aira.api(`/api/leads/items?q=${q}&limit=${limit}`, { silent: true }).catch(() => null) : null,
    ]);
    const value = { results: (mine && mine.results) || {}, sales: (sales && sales.leads) || [], salesTotal: (sales && sales.total) || 0, reached: !!(mine || sales) };
    searchCache.set(key, { at: Date.now(), value });
    if (searchCache.size > 40) searchCache.delete(searchCache.keys().next().value);
    return value;
  }

  // Open a record on its own tab: the page takes ?open= out of the address and opens the drawer once it is drawn.
  const openOnTab = (kind, record) => {
    pending = { open: kind, id: record.id, record };
    Aira.router.go(["marketing"], { tab: KIND[kind].tab, open: `${kind}:${record.id}` });
  };
  const openSalesLead = (lead) => Aira.router.go(["leads"], { tab: "leads", open: lead.id });

  async function fulfil(ask) {
    if (ask.new) {
      if (CREATE[ask.new] && canCreate()) CREATE[ask.new]();
      return;
    }
    const opener = OPENERS[ask.open];
    if (!opener || !ask.id) return;
    let record = ask.record && ask.record.id === ask.id ? ask.record : null;
    if (!record) {
      try {
        const [path, key] = LISTS[ask.open];
        record = ((await M(path, { silent: true }))[key] || []).find((row) => row.id === ask.id) || null;
      } catch { record = null; }
    }
    if (record) opener(record);
    else Aira.toast("That item is not here any more.", "info");
  }

  // Every match for what was typed in the search box (Enter), a section per kind; a row opens its record here.
  async function searchTab(body, query) {
    if (!query) {
      body.append(S.section(null, Aira.ui.empty("Search Marketing", "Type a name, an email, a post, a campaign or a form in the search box at the top.")));
      return;
    }
    const found = await findEverything(query, 25);
    const sections = [];
    for (const spec of KINDS) {
      const rows = found.results[spec.key] || [];
      if (!rows.length) continue;
      sections.push(S.section(`${spec.title} (${rows.length}${rows.length >= 25 ? "+" : ""})`,
        table({
          columns: [
            { key: "label", label: "Name", render: (r) => h("span", {}, h("strong", { text: spec.label(r) }), h("div", { class: "muted small mk-wrap", text: spec.sub(r) })) },
            { key: "status", label: "Status", render: (r) => (spec.status(r) ? S.pill(spec.status(r), RESULT_TONES) : "") },
          ],
          rows,
          onRow: (r) => OPENERS[spec.kind](r),
        }, "mk-results"),
        spec.more ? h("div", { class: "btn-row" }, S.button(`Open in ${TABS.find((t) => t.id === spec.more).label}`, () => Aira.router.go(["marketing"], { tab: spec.more, q: query }), { kind: "ghost sm" })) : null));
    }
    if (found.sales.length) {
      sections.push(S.section(`Sales leads (${found.salesTotal})`,
        table({
          columns: [
            { key: "name", label: "Name", render: (lead) => h("span", {}, h("strong", { text: salesLabel(lead) }), h("div", { class: "muted small mk-wrap", text: salesSub(lead) })) },
            { key: "status", label: "Status", render: (lead) => S.pill(lead.status, { new: "warn", contacted: "info", replied: "info", qualified: "good", customer: "good", disqualified: "" }) },
          ],
          rows: found.sales,
          onRow: openSalesLead,
        }, "mk-results"),
        h("div", { class: "btn-row" }, h("a", { class: "btn ghost sm", href: `#/leads?${new URLSearchParams({ tab: "leads", q: query })}`, text: "Open in Leads" }))));
    }
    if (!sections.length) {
      body.append(S.section(null, Aira.ui.empty(found.reached ? `Nothing matches “${query}”` : "Search is not available right now", found.reached ? "Try fewer words, or part of a name, an email or a title." : "Try again in a moment.",
        canCreate() ? h("div", { class: "btn-row" }, S.button("New post", () => editPost({ status: "draft" }), { kind: "secondary sm" }), S.button("New form", () => editForm(), { kind: "secondary sm" })) : null)));
      return;
    }
    body.append(...sections);
  }

  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": the things people make most here, each opening the same form as the button on its tab.
  Aira.appNew.marketing = () => (canCreate() ? [
    { label: "Post", icon: "mkPost", href: "#/marketing?tab=calendar&new=post" },
    { label: "Campaign", icon: "ads", href: "#/marketing?tab=campaigns&new=campaign" },
    { label: "Lead form", icon: "applications", href: "#/marketing?tab=forms&new=form" },
    { label: "Newsletter", icon: "news", href: "#/marketing?tab=newsletters&new=newsletter" },
    hasLeads() ? { label: "Find leads", icon: "leads", href: "#/leads?tab=find&new=search" } : null,
  ].filter(Boolean) : []);
  Aira.appSearch.marketing = {
    placeholder: "Search leads, posts, campaigns and subscribers",
    // Up to six records, one of each kind in turn so one busy kind does not hide the others.
    async suggest(query) {
      const found = await findEverything(query, 4);
      const lanes = [
        ...KINDS.slice(0, 1).map((spec) => (found.results[spec.key] || []).map((r) => ({ label: spec.label(r), sub: `Lead · ${spec.sub(r)}`, icon: spec.icon, run: () => openOnTab(spec.kind, r) }))),
        found.sales.map((lead) => ({ label: salesLabel(lead), sub: salesSub(lead), icon: "leads", run: () => openSalesLead(lead) })),
        ...KINDS.slice(1).map((spec) => (found.results[spec.key] || []).map((r) => ({ label: spec.label(r), sub: [spec.one, spec.sub(r)].filter(Boolean).join(" · "), icon: spec.icon, run: () => openOnTab(spec.kind, r) }))),
      ];
      const out = [];
      for (let round = 0; out.length < 6 && lanes.some((lane) => lane.length > round); round += 1) for (const lane of lanes) if (lane[round] && out.length < 6) out.push(lane[round]);
      return out;
    },
    // Enter: on the Leads page, the saved sales leads that match; anywhere else, every match on a results page.
    run(query) {
      const section = Aira.router.current && Aira.router.current.path[0];
      if (section === "leads") Aira.router.go(["leads"], { tab: "leads", q: query });
      else Aira.router.go(["marketing"], { tab: "search", q: query });
    },
  };

  // ======================================================================= the view
  Aira.views.marketing = async (main, route) => {
    // "+ New" and the search box ask with ?new=<kind> or ?open=<kind>:<id>: remember the ask, take it out of the
    // address (Back or a refresh must not ask again) and draw the page without it; the drawn page then answers it.
    if (route.query.new || route.query.open) {
      const [kind, id] = String(route.query.open || "").split(":");
      pending = route.query.new ? { new: route.query.new } : { open: kind, id, record: pending && pending.id === id ? pending.record : null };
      const rest = { ...route.query };
      delete rest.new; delete rest.open;
      if (!rest.tab) rest.tab = (route.query.new && CREATE_TAB[route.query.new]) || (KIND[kind] && KIND[kind].tab) || "";
      Aira.router.go(["marketing"], rest, { replace: true });
      return;
    }
    await S.business();
    await loadCampaigns();
    const tabId = route.query.tab === "search" || TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "overview";
    const meta = TABS.find((t) => t.id === tabId);
    const query = String(route.query.q || "").trim();
    // The page is named after the open tab (the app's own name is in the bar above), like a mail app's folder.
    const title = tabId === "search" ? (query ? `Results for “${query}”` : "Search") : meta.label;
    const body = S.page(main, {
      title,
      subtitle: tabId === "search" ? "Leads, subscribers, posts, campaigns, forms, links and newsletters that match, and your saved sales leads." : meta.about,
      actions: tabId === "overview" && canCreate() ? [S.button("Write with AI", () => writeWithAi(), { kind: "secondary", icon: "ai" }), S.button("New post", () => editPost({ status: "draft" }), { icon: "plus" })] : [],
    });
    document.title = `${title} · Marketing · Aira`;
    const tab = S.tabs(body, route, "marketing", TABS, tabId);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      panel.replaceChildren(Aira.ui.skeleton(4));
      const holder = h("div", { class: "suite-body" });
      try {
        const current = Aira.router.parse ? Aira.router.parse() : route;
        const live = { ...route, query: (current && current.query) || route.query };
        if (tab === "calendar") await calendarTab(holder, live);
        else if (tab === "campaigns") await campaignsTab(holder);
        else if (tab === "links") await linksTab(holder);
        else if (tab === "forms") await formsTab(holder, live);
        else if (tab === "bio") await bioTab(holder);
        else if (tab === "kit") await mediaKitTab(holder);
        else if (tab === "subscribers") await subscribersTab(holder, live);
        else if (tab === "newsletters") await newslettersTab(holder);
        else if (tab === "search") await searchTab(holder, query);
        else await overviewTab(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    rerender = async () => { searchCache.clear(); await loadCampaigns(); await render(); };
    await render();
    const ask = pending;
    pending = null;
    if (ask && main.isConnected) await fulfil(ask);
  };
})();

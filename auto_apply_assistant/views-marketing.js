/* Aira — Marketing (#/marketing): grow an audience and turn it into customers.
 *
 * Tabs (?tab=): overview (headline numbers, setup, what is coming up, the newest leads), calendar (posts per channel
 * on a month or week grid, drafts written for each channel, repurposing, a 30-day plan), campaigns (budget, spend,
 * results, ROI), links (tracked links that count clicks and add UTM tags), forms (lead-capture forms and the leads
 * inbox), bio (the link-in-bio page), subscribers (import, export, tags) and newsletters (queued to the shared outbox,
 * one message per subscribed address, each with its own unsubscribe link).
 * Everything runs through /api/marketing/* (aira/suite/marketing/marketing_routes.py). DOM is built with h() and
 * text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const M = (path, opts) => S.api("marketing", path, opts);

  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "calendar", label: "Calendar" },
    { id: "campaigns", label: "Campaigns" },
    { id: "links", label: "Links" },
    { id: "forms", label: "Forms and leads" },
    { id: "bio", label: "Bio page" },
    { id: "subscribers", label: "Subscribers" },
    { id: "newsletters", label: "Newsletters" },
  ];
  const CHANNELS = [["instagram", "Instagram"], ["facebook", "Facebook"], ["linkedin", "LinkedIn"], ["x", "X"], ["tiktok", "TikTok"], ["youtube", "YouTube"], ["email", "Email"], ["blog", "Blog"], ["whatsapp", "WhatsApp"]];
  const LIMITS = { instagram: 2200, facebook: 5000, linkedin: 3000, x: 280, tiktok: 2200, youtube: 5000, email: 10000, blog: 20000, whatsapp: 1000 };
  const CHANNEL_NAME = Object.fromEntries(CHANNELS);
  const STATUS_TONES = { idea: "", draft: "", scheduled: "info", published: "good", new: "warn", contacted: "info", won: "good", lost: "bad", subscribed: "good", unsubscribed: "", partial: "warn", queued: "good", active: "good", paused: "warn", ended: "" };
  let campaigns = [];
  let rerender = () => {};

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
      { type: "heading", label: "Channels to rotate" },
      ...CHANNELS.map(([id, label]) => ({ name: `ch_${id}`, label, type: "checkbox", value: ["instagram", "facebook", "linkedin"].includes(id) })),
    ];
    const body = (form) => ({ description: form.description, goals: form.goals, start: form.start, days: form.days || 30, channels: CHANNELS.map(([id]) => id).filter((id) => form[`ch_${id}`]) });
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
    const unscheduled = (await M("/posts?status=idea")).posts.filter((post) => !post.scheduled_at);
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
      cells.push(h("div", { style: { minHeight: mode === "week" ? "180px" : "92px", padding: "6px", border: "1px solid var(--line)", borderRadius: "8px", background: key === today ? "var(--brand-soft)" : "var(--surface)", opacity: outside ? "0.55" : "1", display: "grid", alignContent: "start", gap: "4px", minWidth: "0" } },
        h("div", { class: "muted small", text: mode === "week" ? date.toLocaleDateString([], { weekday: "short", day: "numeric" }) : String(date.getDate()) }),
        ...posts.map((post) => h("button", { type: "button", style: { display: "grid", gap: "1px", textAlign: "left", padding: "4px 6px", minWidth: "0", width: "100%", border: "1px solid var(--line)", borderLeft: `3px solid ${post.status === "published" ? "var(--good)" : post.status === "scheduled" ? "var(--brand)" : "var(--line-strong)"}`, borderRadius: "6px", background: "var(--surface)", color: "inherit", font: "inherit", cursor: "pointer" }, title: post.title || post.body, onclick: () => openPost(post) },
          h("span", { class: "muted small", text: `${new Date(post.scheduled_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} · ${CHANNEL_NAME[post.channel]}` }),
          h("span", { style: { fontSize: "12.5px", fontWeight: "600", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: post.title || post.body || "" })))));
    }
    const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => h("div", { class: "muted small", style: { textAlign: "center", fontWeight: "700" }, text: day }));
    body.append(
      S.section(null, toolbar,
        h("div", { style: { overflowX: "auto" } }, h("div", { style: { display: "grid", gridTemplateColumns: "repeat(7, minmax(90px, 1fr))", gap: "6px", minWidth: "640px" } }, ...(mode === "month" ? weekdays : []), ...cells))),
      S.section("All posts", S.table({
        columns: [
          { key: "scheduled_at", label: "When", render: (p) => (p.scheduled_at ? S.when(p.scheduled_at) : "Not scheduled") },
          { key: "channel", label: "Channel", render: (p) => CHANNEL_NAME[p.channel] },
          { key: "title", label: "Post", render: (p) => p.title || (p.body || "").slice(0, 80) },
          { key: "status", label: "Status", render: (p) => S.pill(p.status, STATUS_TONES) },
        ],
        rows: data.posts.concat(unscheduled),
        onRow: openPost,
        empty: Aira.ui.empty("No posts planned", "Start with the 30-day plan, or write one post with AI.", S.button("30-day plan", () => planDialog())),
      })),
    );
  }

  // ======================================================================= overview
  async function overviewTab(body) {
    const data = await M("/overview");
    const growth = data.growth_percent;
    body.append(
      S.kpis([
        { label: "Subscribers", value: data.subscribers, hint: `+${data.new_subscribers_month} this month${growth === null ? "" : ` (${growth >= 0 ? "+" : ""}${growth}% vs last)`}`, tone: "good" },
        { label: "Leads this month", value: data.leads_month, hint: data.new_leads ? `${data.new_leads} waiting for a reply` : "All answered", tone: data.new_leads ? "warn" : "" },
        { label: "Form conversion", value: pct(data.conversion_percent), hint: `${data.form_submissions} of ${data.form_views} views` },
        { label: "Clicks this month", value: data.clicks_month, hint: "Tracked links and bio page" },
        { label: "Scheduled this week", value: data.scheduled_week, hint: `${data.published_month} published this month` },
      ]),
      S.grid(
        S.section("Get set up", S.checklist(data.checklist), h("div", { class: "btn-row" }, S.button("30-day plan", () => planDialog(), { kind: "secondary sm" }), S.button("New form", () => editForm(), { kind: "secondary sm" }), S.button("Tracked link", () => editLink(), { kind: "secondary sm" }))),
        S.section("Best campaign", data.best_campaign
          ? h("div", {}, h("strong", { text: data.best_campaign.name }), S.kpis([{ label: "ROI", value: pct(data.best_campaign.roi_percent), tone: (data.best_campaign.roi_percent || 0) >= 0 ? "good" : "bad" }, { label: "Leads", value: data.best_campaign.leads }, { label: "Clicks", value: data.best_campaign.clicks }]))
          : Aira.ui.empty("No campaign results yet", "Tie links and forms to a campaign to compare what works.")),
      ),
      S.grid(
        S.section("Coming up", S.table({ columns: [{ key: "scheduled_at", label: "When", render: (p) => S.when(p.scheduled_at) }, { key: "channel", label: "Channel", render: (p) => CHANNEL_NAME[p.channel] }, { key: "title", label: "Post", render: (p) => p.title || (p.body || "").slice(0, 60) }], rows: data.upcoming, onRow: openPost, empty: "Nothing scheduled" })),
        S.section("Newest leads", S.table({ columns: [{ key: "created_at", label: "When", render: (r) => Aira.fmt.ago(r.created_at) }, { key: "name", label: "Who", render: (r) => r.name || r.email || r.phone }, { key: "status", label: "Status", render: (r) => S.pill(r.status, STATUS_TONES) }], rows: data.recent_leads, onRow: openSubmission, empty: "No leads yet: share a form" })),
      ),
    );
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
        { name: "revenue_cents", label: "Revenue it brought (your estimate)", type: "money" },
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
      S.table({
        columns: [
          { key: "name", label: "Campaign", render: (c) => h("span", {}, h("strong", { text: c.name }), c.goal ? h("div", { class: "muted small", text: c.goal }) : null) },
          { key: "status", label: "Status", render: (c) => S.pill(c.status, STATUS_TONES) },
          { key: "spend_cents", label: "Spend / budget", num: true, render: (c) => `${S.money(c.spend_cents)} / ${S.money(c.budget_cents)}` },
          { key: "clicks", label: "Clicks", num: true, render: (c) => c.results.clicks },
          { key: "leads", label: "Leads", num: true, render: (c) => c.results.leads },
          { key: "subs", label: "Subscribers", num: true, render: (c) => c.results.subscribers },
          { key: "cpl", label: "Cost per lead", num: true, render: (c) => (c.results.cost_per_lead_cents === null ? "-" : S.money(c.results.cost_per_lead_cents)) },
          { key: "roi", label: "ROI", num: true, render: (c) => (c.results.roi_percent === null ? "-" : S.pill(`${c.results.roi_percent}%`, { [`${c.results.roi_percent}%`]: c.results.roi_percent >= 0 ? "good" : "bad" })) },
        ],
        rows: data.campaigns,
        onRow: (campaign) => {
          const drawer = Aira.drawer({ title: campaign.name, subtitle: S.pill(campaign.status, STATUS_TONES), body: h("div", { class: "suite-body" },
            S.kpis([{ label: "Clicks", value: campaign.results.clicks }, { label: "Leads", value: campaign.results.leads }, { label: "Subscribers", value: campaign.results.subscribers }, { label: "Posts", value: campaign.results.posts }, { label: "Budget left", value: S.money(campaign.results.budget_left_cents), tone: campaign.results.budget_left_cents < 0 ? "bad" : "" }]),
            Aira.ui.kv([["Dates", [campaign.starts_on, campaign.ends_on].filter(Boolean).join(" to ")], ["UTM", `source=${campaign.utm_source} · medium=${campaign.utm_medium} · campaign=${campaign.utm_campaign}`]]),
            h("div", { class: "btn-row" }, S.button("Edit", async () => { drawer.close(false); await editCampaign(campaign); }), S.button("Delete", async () => { if (await S.confirmDelete(campaign.name)) { await M(`/campaigns/${campaign.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
        },
        empty: Aira.ui.empty("No campaigns yet", "A campaign groups posts, links and forms so you can see what each push brought in.", S.button("New campaign", () => editCampaign())),
      })));
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
      S.table({
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
      })));
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

  function openSubmission(row) {
    const status = Aira.ui.select(S.statusOptions(["new", "contacted", "won", "lost"]), row.status);
    const notes = h("textarea", { rows: 4 });
    notes.value = row.notes || "";
    const drawer = Aira.drawer({ title: row.name || row.email || row.phone || "Lead", subtitle: `${row.form_title || "Form"} · ${Aira.fmt.ago(row.created_at)}`, body: h("div", { class: "suite-body" },
      Aira.ui.kv([["Email", row.email ? h("a", { href: `mailto:${row.email}`, text: row.email }) : ""], ["Phone", row.phone ? h("a", { href: `tel:${row.phone}`, text: row.phone }) : ""], ["Company", row.company], ["Answer", row.answer], ["Wants news", row.consent ? "Yes" : "No"]]),
      row.message ? h("pre", { class: "suite-pre", text: row.message }) : null,
      Aira.ui.field("Status", status), Aira.ui.field("Notes", notes),
      h("div", { class: "btn-row" }, S.button("Save", async () => { await M(`/submissions/${row.id}`, { method: "PATCH", body: { status: status.value, notes: notes.value } }); Aira.toast("Saved.", "success"); drawer.close(false); rerender(); }), S.button("Delete", async () => { if (await S.confirmDelete("this lead")) { await M(`/submissions/${row.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
  }

  async function formsTab(body, route) {
    const [forms, leads] = await Promise.all([M("/forms"), M(`/submissions${route.query.form ? `?form_id=${encodeURIComponent(route.query.form)}` : ""}`)]);
    body.append(
      S.section("Lead forms", h("div", { class: "btn-row" }, S.button("New form", () => editForm())),
        S.table({
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
        })),
      S.section(route.query.form ? "Leads from this form" : "Leads inbox",
        h("div", { class: "btn-row" }, route.query.form ? S.button("All leads", () => Aira.router.go(["marketing"], { tab: "forms" }), { kind: "ghost sm" }) : null, S.button("Download CSV", () => csv(`/submissions?format=csv${route.query.form ? `&form_id=${encodeURIComponent(route.query.form)}` : ""}`, "leads.csv"), { kind: "secondary sm" })),
        S.table({
          columns: [
            { key: "created_at", label: "When", render: (r) => Aira.fmt.ago(r.created_at) },
            { key: "name", label: "Who", render: (r) => h("span", {}, h("strong", { text: r.name || r.email || r.phone }), h("div", { class: "muted small", text: [r.email, r.phone].filter(Boolean).join(" · ") })) },
            { key: "form_title", label: "Form" },
            { key: "message", label: "Message", render: (r) => (r.message || r.answer || "").slice(0, 80) },
            { key: "status", label: "Status", render: (r) => S.pill(r.status, STATUS_TONES) },
          ],
          rows: leads.submissions,
          onRow: openSubmission,
          empty: "No leads yet",
        })),
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

  // ======================================================================= subscribers
  async function subscribersTab(body, route) {
    const params = new URLSearchParams();
    if (route.query.status) params.set("status", route.query.status);
    if (route.query.tag) params.set("tag", route.query.tag);
    if (route.query.q) params.set("q", route.query.q);
    const data = await M(`/subscribers?${params}`);
    const search = h("input", { type: "search", placeholder: "Search email or name", value: route.query.q || "", "data-search": "1" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") Aira.router.go(["marketing"], { ...route.query, tab: "subscribers", q: search.value.trim() }); });
    const statusFilter = Aira.ui.select([["", "Everyone"], ["subscribed", "Subscribed"], ["unsubscribed", "Unsubscribed"]], route.query.status || "", { "aria-label": "Status" });
    statusFilter.addEventListener("change", () => Aira.router.go(["marketing"], { ...route.query, tab: "subscribers", status: statusFilter.value }));
    const tagFilter = Aira.ui.select([["", "Any tag"], ...data.tags.map((t) => [t, t])], route.query.tag || "", { "aria-label": "Tag" });
    tagFilter.addEventListener("change", () => Aira.router.go(["marketing"], { ...route.query, tab: "subscribers", tag: tagFilter.value }));
    const addOne = () => S.edit({ title: "Add subscriber", fields: [{ name: "email", label: "Email", type: "email", required: true }, { name: "name", label: "Name" }, { name: "tags", label: "Tags", placeholder: "vip, autumn" }], onSave: async (values) => { await M("/subscribers", { method: "POST", body: values }); rerender(); } });
    const importMany = () => S.edit({ title: "Import subscribers", fields: [{ name: "csv", label: "Paste CSV: email,name,tags (one per line)", type: "textarea", rows: 10, required: true, hint: "Only add people who agreed to hear from you. Addresses that unsubscribed are skipped." }], submitLabel: "Import", onSave: async (values) => { const result = await M("/subscribers/import", { method: "POST", body: values }); Aira.toast(`${result.added} added, ${result.updated} updated, ${result.skipped} skipped, ${result.invalid} invalid.`, "success"); rerender(); } });
    const open = (row) => {
      const drawer = Aira.drawer({ title: row.name || row.email, subtitle: S.pill(row.status, STATUS_TONES), body: h("div", { class: "suite-body" },
        Aira.ui.kv([["Email", row.email], ["Source", row.source], ["Tags", row.tags.join(", ")], ["Opted in", S.when(row.consent_at)], ["Joined", S.when(row.created_at)], ["Unsubscribed", S.when(row.unsubscribed_at)]]),
        h("div", { class: "btn-row" },
          S.button("Edit", async () => { drawer.close(false); await S.edit({ title: "Edit subscriber", fields: [{ name: "name", label: "Name" }, { name: "tags", label: "Tags" }], values: { name: row.name, tags: row.tags.join(", ") }, onSave: async (values) => { await M(`/subscribers/${row.id}`, { method: "PATCH", body: values }); rerender(); } }); }),
          row.status === "subscribed" ? S.button("Unsubscribe", async () => { await M(`/subscribers/${row.id}`, { method: "PATCH", body: { status: "unsubscribed" } }); drawer.close(false); rerender(); }, { kind: "secondary" }) : null,
          S.button("Delete", async () => { if (await S.confirmDelete(row.email)) { await M(`/subscribers/${row.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
    };
    body.append(
      S.kpis([{ label: "Subscribed", value: data.counts.subscribed, tone: "good" }, { label: "Unsubscribed", value: data.counts.unsubscribed }]),
      S.section("Subscribers",
        h("div", { class: "btn-row" }, search, statusFilter, tagFilter, S.button("Add", addOne, { kind: "sm" }), S.button("Import", importMany, { kind: "secondary sm" }), S.button("Download CSV", () => csv(`/subscribers?format=csv&${params}`, "subscribers.csv"), { kind: "secondary sm" })),
        S.table({
          columns: [
            { key: "email", label: "Email", render: (s) => h("span", {}, h("strong", { text: s.email }), s.name ? h("div", { class: "muted small", text: s.name }) : null) },
            { key: "tags", label: "Tags", render: (s) => s.tags.join(", ") },
            { key: "source", label: "Source" },
            { key: "status", label: "Status", render: (s) => S.pill(s.status, STATUS_TONES) },
            { key: "created_at", label: "Joined", render: (s) => S.day(s.created_at) },
          ],
          rows: data.subscribers,
          onRow: open,
          empty: Aira.ui.empty("No subscribers yet", "Turn on the email opt-in in a lead form, or import people who agreed to hear from you."),
        })),
    );
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

  async function newslettersTab(body) {
    const data = await M("/newsletters");
    const queue = async (letter) => {
      if (!letter.remaining) { Aira.toast("Everyone in this audience already has it."); return; }
      const count = Math.min(letter.remaining, data.cap);
      const ok = await Aira.confirm({ title: "Send this newsletter", message: `Queue "${letter.subject}" for ${count} subscriber${count === 1 ? "" : "s"}${letter.remaining > data.cap ? ` (the first ${data.cap} of ${letter.remaining}; queue again for the rest)` : ""}? The messages wait in Business hub > Outbox until you approve and send them.`, confirmLabel: `Queue ${count}` });
      if (!ok) return;
      const result = await M(`/newsletters/${letter.id}/queue`, { method: "POST", body: { confirm: true } });
      Aira.toast(`${result.queued} queued${result.remaining ? `, ${result.remaining} left` : ""}.`, "success");
      rerender();
    };
    body.append(S.section("Newsletters", h("p", { class: "muted", text: "Keep your list warm: one useful email a week or two. Messages go out from your own mail server after you approve them in the outbox." }), h("div", { class: "btn-row" }, S.button("New newsletter", () => editNewsletter()), h("a", { class: "btn secondary", href: "#/hub?tab=outbox", text: "Open the outbox" })),
      S.table({
        columns: [
          { key: "subject", label: "Subject", render: (n) => h("strong", { text: n.subject }) },
          { key: "audience_tag", label: "Audience", render: (n) => n.audience_tag || "Everyone" },
          { key: "status", label: "Status", render: (n) => S.pill(n.status, STATUS_TONES) },
          { key: "queued_count", label: "Queued", num: true },
          { key: "sent", label: "Sent", num: true },
          { key: "remaining", label: "Waiting", num: true },
          { key: "actions", label: "", render: (n) => h("div", { class: "btn-row" }, S.button("Queue send", () => queue(n), { kind: "sm" })) },
        ],
        rows: data.newsletters,
        onRow: (letter) => {
          const drawer = Aira.drawer({ title: letter.subject, subtitle: S.pill(letter.status, STATUS_TONES), body: h("div", { class: "suite-body" }, h("pre", { class: "suite-pre", text: letter.body }),
            h("div", { class: "btn-row" }, S.button("Edit", async () => { drawer.close(false); await editNewsletter(letter); }), S.button("Queue send", async () => { drawer.close(false); await queue(letter); }, { kind: "secondary" }), S.button("Delete", async () => { if (await S.confirmDelete(letter.subject)) { await M(`/newsletters/${letter.id}`, { method: "DELETE" }); drawer.close(false); rerender(); } }, { kind: "ghost" }))) });
        },
        empty: Aira.ui.empty("No newsletters yet", "Write one to your subscribers.", S.button("New newsletter", () => editNewsletter())),
      })));
  }

  // ======================================================================= the view
  Aira.views.marketing = async (main, route) => {
    await S.business();
    await loadCampaigns();
    const body = S.page(main, {
      title: "Marketing",
      subtitle: "Content, campaigns, forms and subscribers: grow an audience and turn it into customers.",
      actions: [S.button("Write with AI", () => writeWithAi(), { kind: "secondary" }), S.button("New post", () => editPost({ status: "draft" }))],
    });
    const tab = S.tabs(body, route, "marketing", TABS);
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
        else if (tab === "subscribers") await subscribersTab(holder, live);
        else if (tab === "newsletters") await newslettersTab(holder);
        else await overviewTab(holder);
        panel.replaceChildren(holder);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    rerender = async () => { await loadCampaigns(); await render(); };
    await render();
  };
})();

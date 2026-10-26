/* Aira — Ads (#/ads): advertise on Videos, and pay the creators whose videos the ads run on.
 *
 * Tabs (?tab=): overview (balance, this month, what needs doing), campaigns (?campaign=<id> for one campaign with its
 * ads), reports (per day and per ad, CSV), balance (hosted: top up through the organization's billing; here: the
 * payments the computer's owner recorded) and, for the computer's owner on a single-user installation, admin
 * (creators' share, ad review, payouts, closing months, clawbacks, recording an advertiser's payment).
 *
 * Everything runs through /api/ads/* (aira/suite/ads/ads_routes.py); a top-up in the hosted build is
 * POST /api/org/ads/topup. Money from the server is in micros of a dollar (1 cent = 10,000). DOM is built with h()
 * and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("ads", path, opts);
  const go = (query) => Aira.router.go(["ads"], query);

  const OBJECTIVE = { views: "Views (pay per 1,000 impressions)", clicks: "Clicks (pay per click)" };
  const STATUS_TONES = { draft: "", active: "good", paused: "warn", ended: "", pending: "warn", approved: "good", rejected: "bad", requested: "warn", paid: "good" };
  const PAUSED = { no_balance: "Paused: the balance is used up", budget_spent: "Ended: the budget is spent", schedule_over: "Ended: the end date passed" };
  const LEDGER = { topup: "Top-up (billed)", payment: "Payment recorded", refund: "Refund for invalid traffic" };
  const INVALID = { own_ads: "Seen by your own people", own_video: "Seen by the creator's own household", repeat: "Repeat views by one person", too_many: "Too many ads to one person in an hour", late: "Reported too late", budget: "No budget left", review_excess: "Removed at the month's review", clawback: "Taken back after a review", campaign_gone: "Campaign removed" };
  const usd = (micros) => {
    const value = Number(micros || 0) / 1e6;
    if (value > 0 && value < 0.01) return `$${value.toFixed(4)}`;
    return S.money(Math.round(value * 100), "USD");
  };
  const cents = (micros) => Math.round(Number(micros || 0) / 10000);
  const pill = (status) => S.pill(status, STATUS_TONES);

  async function upload(file, kind, onProgress) {
    if (!Aira.videos || !Aira.videos.uploadFile) throw new Error("Uploading needs the Videos screen, which is not loaded.");
    return Aira.videos.uploadFile(file, kind, { base: "/api/ads", onProgress });
  }

  function campaignFields(info) {
    return [
      { name: "name", label: "Campaign name", required: true },
      { name: "objective", label: "What do you pay for?", type: "select", options: Object.entries(OBJECTIVE) },
      { name: "bid_cents", label: "Your bid (USD): per 1,000 impressions, or per click", type: "money", required: true, hint: "Views: $0.50 to $100 per 1,000. Clicks: $0.05 to $50 each. The highest bid usually wins." },
      { name: "total_budget_cents", label: "Total budget (USD)", type: "money", required: true },
      { name: "daily_budget_cents", label: "Daily budget (USD)", type: "money", required: true, hint: "Spent evenly through the day." },
      { name: "starts_on", label: "Start date (optional)", type: "date" },
      { name: "ends_on", label: "End date (optional)", type: "date" },
      { name: "landing_url", label: "Your page (https://)", required: true },
      { name: "categories", label: "Only on these video categories (optional, separate with commas)", hint: info.categories.join(", ") },
      { name: "languages", label: "Only on videos in these languages (optional, like en, te, hi)" },
      { name: "countries", label: "Only to viewers in these countries (optional, like IN, US)" },
      { name: "frequency_cap", label: "Most times one person sees it in a day", type: "number", min: 1, max: 20 },
      { name: "family_safe", label: "Family-safe: fine for teens and on videos made for children", type: "checkbox", hint: "Reviewers check this. Nobody under 13 is ever shown an ad." },
      { name: "allow_mature", label: "Also run on videos rated for adults", type: "checkbox" },
    ];
  }
  const campaignValues = (campaign) => ({
    ...campaign,
    bid_cents: cents(campaign.bid_micros),
    total_budget_cents: cents(campaign.total_budget_micros),
    daily_budget_cents: cents(campaign.daily_budget_micros),
    categories: (campaign.categories || []).join(", "),
    languages: (campaign.languages || []).join(", "),
    countries: (campaign.countries || []).join(", "),
  });

  // ---------------------------------------------------------------- overview
  async function overviewTab(panel, info) {
    const month = info.month;
    panel.append(S.kpis([
      { label: "Balance", value: usd(info.balance_micros), tone: info.balance_micros > 0 ? "" : "warn", hint: info.hosted ? "prepaid, billed with your subscription" : "prepaid" },
      { label: "Spent this month", value: usd(month.spend_micros) },
      { label: "Impressions this month", value: month.impressions.toLocaleString() },
      { label: "Clicks this month", value: month.clicks.toLocaleString(), hint: `${month.ctr_percent}% of impressions` },
      { label: "Cost per 1,000 impressions", value: usd(month.ecpm_micros) },
    ]));
    const todo = [];
    if (info.balance_micros <= 0) todo.push(h("li", {}, h("span", { text: "Your balance is empty, so no ads run. " }), h("a", { href: "#/ads?tab=balance", text: info.hosted ? "Top up" : "See how to add money" })));
    if (info.paused_no_balance) todo.push(h("li", { text: info.paused_no_balance === 1 ? "1 campaign is paused until you add money." : `${info.paused_no_balance} campaigns are paused until you add money.` }));
    if (info.pending_review) todo.push(h("li", { text: info.pending_review === 1 ? "1 ad is waiting for review." : `${info.pending_review} ads are waiting for review.` }));
    if (info.rejected) todo.push(h("li", {}, h("span", { text: info.rejected === 1 ? "1 ad was not approved. " : `${info.rejected} ads were not approved. ` }), h("a", { href: "#/ads?tab=campaigns", text: "See why" })));
    panel.append(S.grid(
      S.section("What needs doing", todo.length ? h("ul", {}, ...todo) : h("p", { class: "muted", text: info.active ? "Your ads are running." : "Create a campaign to start." }),
        h("div", { class: "btn-row" }, S.button("New campaign", () => newCampaign(info), { kind: "primary" }))),
      S.section("How Ads works",
        h("p", { text: "Your ads run on videos people watch in Aira: a short video before the video (people can skip it after 5 seconds), a banner beside it, or a sponsored reel." }),
        h("p", { text: "You pay only for ads that played or were on screen for 2 seconds, or for clicks. Views by your own people, repeat views and other invalid traffic are not charged." }),
        h("p", { text: `Every ad is reviewed before it runs. ${info.settings.revenue_share_percent}% of what you pay goes to the creator of the video your ad ran on.` }))));
  }

  // ---------------------------------------------------------------- campaigns
  function newCampaign(info) {
    return S.edit({
      title: "New campaign",
      fields: campaignFields(info),
      values: { objective: "views", bid_cents: 200, total_budget_cents: 5000, daily_budget_cents: 1000, frequency_cap: 3 },
      submitLabel: "Create campaign",
      wide: true,
      onSave: async (values) => { const made = (await api("/campaigns", { method: "POST", body: values })).campaign; go({ tab: "campaigns", campaign: made.id }); },
    });
  }

  async function campaignsTab(panel, info, route) {
    if (route.query.campaign) { await campaignPage(panel, info, route.query.campaign); return; }
    const { campaigns } = await api("/campaigns");
    panel.append(h("div", { class: "btn-row" }, S.button("New campaign", () => newCampaign(info), { kind: "primary" })));
    panel.append(S.table({
      columns: [
        { key: "name", label: "Campaign", render: (row) => h("a", { href: `#/ads?tab=campaigns&campaign=${row.id}`, text: row.name }) },
        { key: "status", label: "Status", render: (row) => h("span", {}, pill(row.status), row.paused_reason ? h("span", { class: "muted small", text: ` ${PAUSED[row.paused_reason] || ""}` }) : null) },
        { key: "bid", label: "Bid", render: (row) => `${usd(row.bid_micros)} ${row.bid_type === "cpm" ? "per 1,000" : "per click"}` },
        { key: "spent", label: "Spent", num: true, render: (row) => `${usd(row.spent_micros)} of ${usd(row.total_budget_micros)}` },
        { key: "impressions", label: "Impressions", num: true, render: (row) => row.stats.impressions },
        { key: "clicks", label: "Clicks", num: true, render: (row) => row.stats.clicks },
        { key: "ads", label: "Ads", num: true, render: (row) => `${row.approved_count}/${row.creatives_count}` },
      ],
      rows: campaigns,
      onRow: (row) => go({ tab: "campaigns", campaign: row.id }),
      empty: Aira.ui.empty("No campaigns yet", "A campaign holds your budget, your bid and who sees your ads."),
    }));
  }

  function creativeNode(creative, reload) {
    const media = creative.mime.startsWith("video/")
      ? h("video", { src: creative.media_url, controls: true, muted: true, playsinline: true, preload: "metadata", class: "vid-preview", "aria-label": creative.headline })
      : h("img", { src: creative.media_url, alt: creative.headline, class: "vid-preview" });
    return h("div", { class: "card suite-section" },
      h("div", { class: "vid-row", style: { justifyContent: "space-between" } }, h("strong", { text: creative.headline }), pill(creative.review_status)),
      h("span", { class: "vid-meta", text: `${creative.kind_label} · ${creative.cta}${creative.body ? ` · ${creative.body}` : ""}` }),
      media,
      creative.review_status === "rejected" && creative.review_note ? h("p", { role: "status", text: `Not approved: ${creative.review_note}` }) : null,
      h("span", { class: "vid-meta", text: `${creative.stats.impressions} impressions · ${creative.stats.clicks} clicks · ${usd(creative.stats.spend_micros)}` }),
      h("div", { class: "btn-row" },
        S.button("Change text", () => S.edit({ title: "Change the ad", fields: [{ name: "headline", label: "Headline", required: true }, { name: "body", label: "Text (optional)" }], values: creative, submitLabel: "Save and send for review", onSave: async (values) => { await api(`/creatives/${creative.id}`, { method: "PATCH", body: values }); await reload(); } }), { kind: "ghost sm" }),
        S.button("Remove", async () => { if (await S.confirmDelete("this ad")) { await api(`/creatives/${creative.id}`, { method: "DELETE" }); await reload(); } }, { kind: "ghost sm" })));
  }

  function addCreative(info, campaign, reload) {
    const kind = Aira.ui.select(Object.entries(info.slots), "preroll", { "aria-label": "Kind of ad" });
    const file = h("input", { type: "file", accept: "video/mp4,video/webm,.mp4,.webm", "aria-label": "The ad's video" });
    kind.addEventListener("change", () => { file.accept = kind.value === "banner" ? "image/jpeg,image/png,image/webp" : "video/mp4,video/webm,.mp4,.webm"; file.setAttribute("aria-label", kind.value === "banner" ? "The banner's picture" : "The ad's video"); });
    const form = S.form([
      { name: "headline", label: "Headline", required: true },
      { name: "body", label: "Text (optional)" },
      { name: "cta", label: "Button", type: "select", options: info.ctas.map((cta) => [cta, cta]) },
    ], { cta: "Learn more" });
    const status = h("p", { class: "muted", "aria-live": "polite" });
    return S.section("Add an ad",
      Aira.ui.field("Kind of ad", kind, "Videos before a video can be skipped after 5 seconds; a sponsored reel is a tall video of up to a minute."),
      Aira.ui.field("Video or picture", file), form.node, status,
      h("div", { class: "btn-row" }, S.button("Upload and send for review", async () => {
        form.setError("");
        const chosen = file.files && file.files[0];
        if (!chosen) { form.setError("Choose the ad's video or picture."); return; }
        const values = form.read();
        if (!values.headline) { form.setError("Enter a headline."); return; }
        try {
          const media = await upload(chosen, kind.value === "banner" ? "image" : "video", (f) => { status.textContent = `Uploading ${Math.round(f * 100)}%`; });
          await api(`/campaigns/${campaign.id}/creatives`, { method: "POST", body: { ...values, kind: kind.value, file_id: media.id, duration: media.duration || 0 } });
          status.textContent = "";
          Aira.toast("Your ad was sent for review.", "success");
          await reload();
        } catch (error) { form.setError(error.message); status.textContent = ""; }
      }, { kind: "primary" })));
  }

  async function campaignPage(panel, info, id) {
    const reload = async () => { panel.replaceChildren(); await campaignPage(panel, info, id); };
    const { campaign } = await api(`/campaigns/${id}`);
    const setStatus = async (status) => { const result = (await api(`/campaigns/${campaign.id}/status`, { method: "POST", body: { status } })).campaign; if (result.paused_reason === "no_balance") Aira.toast("The campaign is ready, and starts as soon as your balance has money.", "info"); await reload(); };
    const stats = campaign.stats;
    panel.append(h("div", { class: "vid-row" }, h("a", { href: "#/ads?tab=campaigns", class: "btn ghost sm", text: "All campaigns" }), h("h2", { class: "suite-h2", style: { margin: "0" }, text: campaign.name }), pill(campaign.status), campaign.paused_reason ? h("span", { class: "muted", text: PAUSED[campaign.paused_reason] || "" }) : null));
    panel.append(S.kpis([
      { label: "Spent", value: usd(campaign.spent_micros), hint: `of ${usd(campaign.total_budget_micros)}` },
      { label: "Spent today", value: usd(campaign.spent_today_micros), hint: `of ${usd(campaign.daily_budget_micros)} a day` },
      { label: "Impressions", value: stats.impressions.toLocaleString() },
      { label: "Clicks", value: stats.clicks.toLocaleString(), hint: `${stats.ctr_percent}%` },
      { label: "Cost per 1,000", value: usd(stats.ecpm_micros) },
    ]));
    panel.append(h("div", { class: "btn-row" },
      campaign.status !== "active" && campaign.status !== "ended" ? S.button("Start", () => setStatus("active"), { kind: "primary" }) : null,
      campaign.status === "active" ? S.button("Pause", () => setStatus("paused"), { kind: "secondary" }) : null,
      campaign.status !== "ended" ? S.button("Edit", () => S.edit({ title: "Edit campaign", fields: campaignFields(info), values: campaignValues(campaign), wide: true, onSave: async (values) => { await api(`/campaigns/${campaign.id}`, { method: "PATCH", body: values }); await reload(); } }), { kind: "ghost" }) : null,
      campaign.status !== "ended" ? S.button("End", async () => { if (await Aira.confirm({ title: "End this campaign?", message: "Its ads stop for good. Its reports stay.", confirmLabel: "End", danger: true })) setStatus("ended"); }, { kind: "ghost" }) : null,
      S.button("Delete", async () => { if (await S.confirmDelete("this campaign")) { await api(`/campaigns/${campaign.id}`, { method: "DELETE" }); go({ tab: "campaigns" }); } }, { kind: "ghost" })));
    panel.append(S.section("Who sees it", Aira.ui.kv([
      ["Pays for", OBJECTIVE[campaign.objective]],
      ["Bid", `${usd(campaign.bid_micros)} ${campaign.bid_type === "cpm" ? "per 1,000 impressions" : "per click"}`],
      ["Dates", campaign.starts_on || campaign.ends_on ? `${campaign.starts_on || "now"} to ${campaign.ends_on || "no end"}` : "No end date"],
      ["Categories", campaign.categories.length ? campaign.categories.join(", ") : "All"],
      ["Languages", campaign.languages.length ? campaign.languages.join(", ") : "All"],
      ["Countries", campaign.countries.length ? campaign.countries.join(", ") : "All"],
      ["Family-safe", campaign.family_safe ? "Yes" : "No"],
      ["At most a day, per person", String(campaign.frequency_cap)],
      ["Your page", campaign.landing_url],
    ])));
    panel.append(S.section("Ads", campaign.creatives.length ? h("div", { class: "vid-grid" }, ...campaign.creatives.map((item) => creativeNode(item, reload))) : Aira.ui.empty("No ads yet", "Add a video or a banner below. It runs once it is approved.")));
    if (campaign.status !== "ended") panel.append(addCreative(info, campaign, reload));
  }

  // ---------------------------------------------------------------- reports
  async function reportsTab(panel, route) {
    const days = Number(route.query.days || 30);
    const data = await api(`/reports?days=${days}`);
    const picker = Aira.ui.select([["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"]], String(days), { "aria-label": "Period", onchange: (event) => go({ tab: "reports", days: event.target.value }) });
    panel.append(h("div", { class: "btn-row" }, picker, S.button("Download CSV", () => Aira.download(`/api/ads/reports?format=csv&days=${days}`, "ads-report.csv"), { kind: "secondary" })));
    const t = data.totals;
    panel.append(S.kpis([
      { label: "Impressions", value: t.impressions.toLocaleString() },
      { label: "Clicks", value: t.clicks.toLocaleString() },
      { label: "Click rate", value: `${t.ctr_percent}%` },
      { label: "Spent", value: usd(t.spend_micros) },
      { label: "Cost per 1,000", value: usd(t.ecpm_micros) },
    ]));
    if (data.days.length) panel.append(S.section("Spend per day", S.bars(data.days.map((row) => ({ label: row.day.slice(5), value: row.spend_micros / 1e6 })), { format: (value) => `$${Number(value).toFixed(2)}` })));
    const rateColumns = [
      { key: "impressions", label: "Impressions", num: true },
      { key: "clicks", label: "Clicks", num: true },
      { key: "ctr_percent", label: "Click rate", num: true, render: (row) => `${row.ctr_percent}%` },
      { key: "spend_micros", label: "Spent", num: true, render: (row) => usd(row.spend_micros) },
      { key: "ecpm_micros", label: "Per 1,000", num: true, render: (row) => usd(row.ecpm_micros) },
    ];
    panel.append(S.section("Per ad", S.table({ columns: [{ key: "headline", label: "Ad", render: (row) => `${row.headline} (${row.campaign})` }, ...rateColumns], rows: data.creatives, empty: Aira.ui.empty("No impressions yet", "Reports fill in once your ads run.") })));
    panel.append(S.section("Per day", S.table({ columns: [{ key: "day", label: "Day" }, ...rateColumns], rows: [...data.days].reverse(), empty: Aira.ui.empty("Nothing yet", "") })));
    if (data.invalid.length) panel.append(S.section("Not charged", h("p", { class: "muted", text: "Ads that were shown but did not count, so you did not pay for them." }), S.table({ columns: [{ key: "reason", label: "Why", render: (row) => INVALID[row.reason] || row.reason }, { key: "n", label: "Impressions", num: true }], rows: data.invalid })));
  }

  // ---------------------------------------------------------------- balance
  async function balanceTab(panel, info) {
    const data = await api("/balance");
    panel.append(S.kpis([{ label: "Balance", value: usd(data.balance_micros), tone: data.balance_micros > 0 ? "good" : "warn" }]));
    if (data.hosted) {
      panel.append(S.section("Top up",
        h("p", { text: "The amount is added to your organization's next invoice from its billing provider, and to your balance as soon as it is billed. Aira never takes card details itself. Owners and billing contacts can top up." }),
        h("div", { class: "btn-row" }, S.button("Top up", () => S.edit({
          title: "Top up your ad balance",
          fields: [{ name: "amount_cents", label: "Amount (USD)", type: "money", required: true, hint: `Between ${S.money(info.limits.topup_min_cents, "USD")} and ${S.money(info.limits.topup_max_cents, "USD")}.` }],
          values: { amount_cents: 2000 },
          submitLabel: "Add to my invoice",
          onSave: async (values) => { await Aira.api("/api/org/ads/topup", { method: "POST", body: values }); Aira.toast("Added to your balance and your next invoice.", "success"); Aira.router.render(); },
        }), { kind: "primary" }))));
    } else {
      panel.append(S.section("Adding money", h("p", { text: "On this computer, the owner of its first account records the money an advertiser paid them (cash, a transfer or a payment link), and it becomes your balance. Aira does not take payments itself." })));
    }
    panel.append(S.section("History", S.table({
      columns: [
        { key: "created_at", label: "When", render: (row) => S.when(row.created_at) },
        { key: "kind", label: "What", render: (row) => LEDGER[row.kind] || row.kind },
        { key: "amount_micros", label: "Amount", num: true, render: (row) => usd(row.amount_micros) },
        { key: "reference", label: "Reference", render: (row) => [row.reference, row.note].filter(Boolean).join(" · ") },
      ],
      rows: data.ledger,
      empty: Aira.ui.empty("Nothing yet", "Top-ups, payments and refunds appear here. Spending is in Reports."),
    })));
  }

  // ---------------------------------------------------------------- admin (the computer's owner)
  async function adminTab(panel) {
    let data;
    try { data = await api("/admin"); } catch (error) { panel.append(Aira.ui.empty("Only the computer's owner", error.message)); return; }
    const t = data.totals;
    panel.append(S.kpis([
      { label: "Paid by advertisers", value: usd(t.gross_micros) },
      { label: "Earned by creators", value: usd(t.creators_micros) },
      { label: "Kept by Aira", value: usd(t.platform_micros) },
      { label: "Waiting to be paid", value: usd(t.requested_micros), tone: t.requested_micros ? "warn" : "" },
      { label: "Paid to creators", value: usd(t.paid_micros) },
      { label: "Refunded", value: usd(t.refunded_micros) },
    ]));
    const settingsForm = S.form([
      { name: "revenue_share_percent", label: "Creators' share of each ad (percent)", type: "number", min: 0, max: 90 },
      { name: "min_payout_cents", label: "Smallest payout (USD)", type: "money" },
    ], { revenue_share_percent: t.settings.revenue_share_percent, min_payout_cents: cents(t.settings.min_payout_micros) });
    panel.append(S.section("Creators' share and payouts", settingsForm.node, h("div", { class: "btn-row" }, S.button("Save", async () => { try { await api("/admin/settings", { method: "PUT", body: settingsForm.read() }); Aira.toast("Saved.", "success"); Aira.router.render(); } catch (error) { settingsForm.setError(error.message); } }, { kind: "secondary" }))));
    panel.append(S.section(`Ads to review (${data.review.length})`, data.review.length ? h("div", { class: "vid-grid" }, ...data.review.map((item) => h("div", { class: "card suite-section" },
      h("strong", { text: item.headline }), h("span", { class: "vid-meta", text: `${item.advertiser} · ${item.kind_label} · ${item.campaign.name}${item.campaign.family_safe ? " · says family-safe" : ""}` }),
      item.mime.startsWith("video/") ? h("video", { src: item.media_url, controls: true, muted: true, preload: "metadata", class: "vid-preview", "aria-label": item.headline }) : h("img", { src: item.media_url, alt: item.headline, class: "vid-preview" }),
      item.body ? h("p", { text: item.body }) : null,
      h("span", { class: "vid-meta", text: item.campaign.landing_url }),
      h("div", { class: "btn-row" },
        S.button("Approve", async () => { await api(`/admin/creatives/${item.id}/review`, { method: "POST", body: { decision: "approve" } }); Aira.router.render(); }, { kind: "primary sm" }),
        S.button("Do not approve", () => S.edit({ title: "Why not?", fields: [{ name: "note", label: "Tell the advertiser what to change", type: "textarea", rows: 3, required: true }], onSave: async (values) => { await api(`/admin/creatives/${item.id}/review`, { method: "POST", body: { decision: "reject", note: values.note } }); Aira.router.render(); } }), { kind: "ghost sm" }))))) : h("p", { class: "muted", text: "Nothing to review." })));
    panel.append(S.section(`Payout requests (${data.payouts.length})`, S.table({
      columns: [
        { key: "channel", label: "Creator", render: (row) => (row.channel ? row.channel.name : "Removed") },
        { key: "amount_micros", label: "Amount", num: true, render: (row) => usd(row.amount_micros) },
        { key: "details", label: "Pay to" },
        { key: "requested_at", label: "Asked", render: (row) => S.day(row.requested_at) },
        { key: "actions", label: "", render: (row) => h("div", { class: "btn-row" },
          S.button("Mark paid", () => S.edit({ title: `Paid ${usd(row.amount_micros)}?`, fields: [{ name: "reference", label: "Payment reference (transfer or UPI number)", required: true }], submitLabel: "Mark paid", onSave: async (values) => { await api(`/admin/payouts/${row.id}`, { method: "POST", body: { action: "paid", reference: values.reference } }); Aira.router.render(); } }), { kind: "primary sm" }),
          S.button("Decline", () => S.edit({ title: "Decline this payout", fields: [{ name: "note", label: "Why?", required: true }], onSave: async (values) => { await api(`/admin/payouts/${row.id}`, { method: "POST", body: { action: "reject", note: values.note } }); Aira.router.render(); } }), { kind: "ghost sm" })) },
      ],
      rows: data.payouts,
      empty: Aira.ui.empty("No requests", "Creators ask to be paid from their Earnings page."),
    })));
    panel.append(S.section("Months", S.table({
      columns: [
        { key: "month", label: "Month" },
        { key: "gross_micros", label: "Paid by advertisers", num: true, render: (row) => usd(row.gross_micros) },
        { key: "creators_micros", label: "Creators", num: true, render: (row) => usd(row.creators_micros) },
        { key: "platform_micros", label: "Aira", num: true, render: (row) => usd(row.platform_micros) },
        { key: "invalid", label: "Not counted", num: true },
        { key: "closed", label: "", render: (row) => (row.closed ? S.pill("confirmed") : S.button("Close month", async () => { if (await Aira.confirm({ title: `Close ${row.month}?`, message: "Its traffic is checked once more, then creators' earnings for it are confirmed and can be paid.", confirmLabel: "Close month" })) { await api("/admin/close-month", { method: "POST", body: { month: row.month } }); Aira.router.render(); } }, { kind: "ghost sm" })) },
      ],
      rows: t.months,
      empty: Aira.ui.empty("No ads have run yet", ""),
    })));
    const advertisers = data.advertisers.map((item) => [item.home, `${item.name || "Advertiser"} (${usd(item.balance_micros)})`]);
    panel.append(S.grid(
      S.section("Record a payment", h("p", { class: "muted", text: "Money an advertiser on this computer paid you. It becomes their ad balance." }), h("div", { class: "btn-row" }, S.button("Record a payment", () => S.edit({
        title: "Record a payment",
        fields: [{ name: "home", label: "Advertiser", type: "select", options: advertisers.length ? advertisers : [["", "Your own account"]] }, { name: "amount_cents", label: "Amount (USD)", type: "money", required: true }, { name: "reference", label: "Reference (receipt or transfer number)", required: true }, { name: "note", label: "Note (optional)" }],
        onSave: async (values) => { await api("/admin/payments", { method: "POST", body: values }); Aira.toast("Recorded.", "success"); Aira.router.render(); },
      }), { kind: "secondary" }))),
      S.section("Take earnings back", h("p", { class: "muted", text: "When a channel's views were bought or faked: its counted ads for that month stop counting, advertisers are refunded and the earnings are removed." }), h("div", { class: "btn-row" }, S.button("Claw back", () => S.edit({
        title: "Take a channel's earnings back",
        fields: [{ name: "channel_id", label: "Channel id", required: true }, { name: "month", label: "Month (like 2026-10)", required: true }, { name: "reason", label: "Reason", required: true }],
        onSave: async (values) => { const result = await api("/admin/clawback", { method: "POST", body: values }); Aira.toast(`${result.impressions} impressions taken back.`, "success"); Aira.router.render(); },
      }), { kind: "ghost" })))));
  }

  // ---------------------------------------------------------------- the view
  Aira.views.ads = async (main, route) => {
    let info;
    try { info = await api("/overview"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    let operator = false;
    if (!info.hosted) { try { operator = !!(await S.api("videos", "/me", { silent: true })).operator; } catch { operator = false; } }
    const tabs = [
      { id: "overview", label: "Overview" },
      { id: "campaigns", label: "Campaigns" },
      { id: "reports", label: "Reports" },
      { id: "balance", label: "Balance" },
      operator ? { id: "admin", label: "Admin" } : null,
    ].filter(Boolean);
    const body = S.page(main, { title: "Ads", subtitle: info.advertiser ? `Advertising as ${info.advertiser}` : "Advertise on videos", actions: [S.button("New campaign", () => newCampaign(info), { kind: "primary" })] });
    const tab = S.tabs(body, route, "ads", tabs, tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "overview");
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    try {
      if (tab === "campaigns") await campaignsTab(panel, info, route);
      else if (tab === "reports") await reportsTab(panel, route);
      else if (tab === "balance") await balanceTab(panel, info);
      else if (tab === "admin") await adminTab(panel);
      else await overviewTab(panel, info);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
  };
})();

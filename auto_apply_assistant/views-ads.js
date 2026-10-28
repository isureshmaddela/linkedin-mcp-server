/* Aira — Ads (#/ads): advertise on Videos, and pay the creators whose videos the ads run on.
 *
 * Tabs (?tab=): overview (balance and how long it lasts, this month, what needs doing), campaigns (search, filter and
 * CSV; ?campaign=<id> for one campaign with its ads, what to expect, its sales and return on ad spend), reports (per
 * day, per campaign and per ad, filtered by campaign, CSV), balance (hosted: top up through the organization's
 * billing; here: the payments the computer's owner recorded) and, for the computer's owner on a single-user
 * installation, admin (creators' share, ad review, payouts, closing months, clawbacks, recording a payment).
 *
 * The campaign form shows a live forecast (POST /api/ads/estimate): views a day on matching videos, how often the ad
 * would win, the cost a day, how long the budget lasts and the bid that would win most often.
 *
 * In the Aira frame (shell.js): "+ New" offers a campaign, an ad, recorded sales and (hosted) a top-up or (the
 * computer's owner) a recorded payment; each asks with ?new=<kind>, which the view takes out of the address and
 * answers once the page is drawn. The search across the top suggests campaigns and ads (GET /api/ads/creatives) and
 * Enter shows the matches on Campaigns (?q=); ?ad=<id> on a campaign's page shows that ad.
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
  const RESTART_HINT = { budget_spent: "Raise the total budget with Edit, then start it again.", schedule_over: "Move the end date with Edit (or leave it empty), then start it again." };
  const LEDGER = { topup: "Top-up (billed)", payment: "Payment recorded", refund: "Refund for invalid traffic" };
  const INVALID = { own_ads: "Seen by your own people", own_video: "Seen by the creator's own household", repeat: "Repeat views by one person", too_many: "Too many ads to one person in an hour", late: "Reported too late", budget: "No budget left", review_excess: "Removed at the month's review", clawback: "Taken back after a review", campaign_gone: "Campaign removed" };
  const STATUS_FILTER = [["", "Every status"], ["active", "Running"], ["paused", "Paused"], ["draft", "Draft"], ["ended", "Ended"]];
  const LOW_DAYS = 3;
  const usd = (micros) => {
    const value = Number(micros || 0) / 1e6;
    if (value > 0 && value < 0.01) return `$${value.toFixed(4)}`;
    return S.money(Math.round(value * 100), "USD");
  };
  const cents = (micros) => Math.round(Number(micros || 0) / 10000);
  const pill = (status) => S.pill(status, STATUS_TONES);
  const count = (n, word, many) => `${Number(n || 0).toLocaleString()} ${Number(n) === 1 ? word : many || `${word}s`}`;
  const days = (n) => (n === null || n === undefined ? "" : n < 1 ? "less than a day" : `about ${count(Math.round(n), "day")}`);
  const roasText = (roas) => (roas === null || roas === undefined ? "Nothing spent yet" : usd(roas * 1e6));
  const today = () => new Date().toISOString().slice(0, 10);
  const ID = /^[0-9a-f]{32}$/;

  // A few glyphs the shared set lacks, for the menu's folders, "+ New" and the search list (added once, never replaced).
  const GLYPHS = {
    adsWallet: '<svg viewBox="0 0 24 24"><path d="M17 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v2"/><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M16 13.5h2"/></svg>',
    adsShield: '<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    adsFilm: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg>',
    adsSales: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.5"/></svg>',
    adsBack: '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;

  // The tabs, which the frame also shows as folders in the left menu; the page is named after the open one.
  const TABS = [
    { id: "overview", label: "Overview", icon: "dashboard" },
    { id: "campaigns", label: "Campaigns", icon: "ads", about: "Your budgets, bids and who sees your ads." },
    { id: "reports", label: "Reports", icon: "analytics", about: "What your ads did, and what they brought back for each dollar." },
    { id: "balance", label: "Balance", icon: "adsWallet", about: "The prepaid money your ads draw from." },
    { id: "admin", label: "Admin", icon: "adsShield", about: "Creators' share, ad review, payouts and closing months.", operator: true },
  ];

  // Who may create: a grown-up (a child's or a teen's profile is refused), and in the hosted build someone who manages
  // the organization's apps (an auditor only reads). The server refuses the rest anyway; this keeps the buttons away.
  const grownUp = () => { const me = Aira.life && Aira.life.me; return !me || !me.age_group || ["adult", "senior"].includes(me.age_group); };
  const cloudCan = (permission) => !(Aira.cloud && typeof Aira.cloud.can === "function") || Aira.cloud.can(permission);
  const canCreate = () => grownUp() && cloudCan("suite.manage");
  const canTopUp = () => !!(Aira.cloud && typeof Aira.cloud.can === "function" && Aira.cloud.can("billing.manage"));
  const known = { operator: false }; // the computer's owner, learned when the page is drawn
  let pending = null; // what "+ New" asked for (?new=), answered once the page is drawn
  const hooks = {}; // what the drawn page can answer itself: ad, sales (a campaign's page), topup, payment

  // List answers for the search box, kept for a few seconds (and dropped whenever the page is drawn again).
  const answers = new Map();
  const cached = (path, ms = 8000) => {
    const hit = answers.get(path);
    if (hit && Date.now() - hit.at < ms) return hit.promise;
    const promise = api(path, { silent: true }).catch(() => null);
    answers.set(path, { at: Date.now(), promise });
    return promise;
  };
  const folded = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  const STATUS_WORD = { draft: "Draft", active: "Running", paused: "Paused", ended: "Ended" };
  const REVIEW_WORD = { pending: "Waiting for review", approved: "Approved", rejected: "Not approved" };
  const campaignMatches = (row, needle) => folded([row.name, row.landing_url, ...(row.categories || [])].join(" ")).includes(needle);
  const adMatches = (row, needle) => folded([row.headline, row.body, row.cta].join(" ")).includes(needle);
  const adHref = (row) => `#/ads?tab=campaigns&campaign=${row.campaign_id}&ad=${row.id}`;

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
      { name: "utm", label: "Add tracking tags to my page link", type: "checkbox", hint: "Your website analytics then shows which visits came from this campaign." },
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

  // ---------------------------------------------------------------- the forecast
  function estimateNode(data, onUseBid) {
    if (!data.estimate) return h("p", { class: "muted", text: `To see what to expect: ${data.problem}` });
    const e = data.estimate;
    const perWhat = e.bid_type === "cpm" ? "per 1,000" : "per click";
    const lines = [];
    if (!e.views_per_day) {
      lines.push(h("p", { text: "No one has watched videos that match this audience in the last week, so the ads would not show yet. Try fewer limits (categories, languages or countries)." }));
    } else {
      lines.push(h("p", { text: `About ${count(e.views_per_day, "view")} a day on matching videos.` }));
      lines.push(h("p", { text: e.competitors ? `Your ad would show about ${count(e.impressions_per_day, "time")} a day: it wins about ${e.win_share_percent}% of the time against ${count(e.competitors, "other advertiser")}.` : `Your ad would show about ${count(e.impressions_per_day, "time")} a day. No other advertiser is bidding for these views now.` }));
      lines.push(h("p", { text: `About ${count(e.clicks_per_day, "click")} a day (${e.ctr_percent}% of people click).` }));
      lines.push(h("p", { text: `It costs about ${usd(e.spend_per_day_micros)} a day${e.budget_days ? `; your total budget lasts ${days(e.budget_days)}` : ""}.` }));
      if (e.spend_per_day_micros) lines.push(h("p", { class: "muted", text: `Creators earn about ${usd(e.creators_per_day_micros)} a day from it. ${data.balance_micros ? `Your balance pays for ${days(e.balance_days)}.` : "Your balance is empty: add money before it can run."}` }));
      if (e.budget_limited) lines.push(h("p", { class: "ads-note", text: "Your daily budget limits how often it shows. Raise it to reach more people." }));
    }
    const suggestion = h("div", { class: "ads-suggest" },
      h("span", { text: `Bid ${usd(e.suggested_bid_micros)} ${perWhat} to win most often.` }),
      onUseBid ? S.button("Use this bid", () => onUseBid(e.suggested_bid_micros), { kind: "ghost sm" }) : null);
    const note = { low: "A rough guess: few people watched matching videos this week.", some: "A fair guess from last week's viewing.", good: "A good guess from last week's viewing." }[e.confidence];
    return h("div", {}, ...lines, suggestion, h("p", { class: "muted small", text: `${note} Real results change with what people watch.` }));
  }

  function campaignDialog(info, { title, values, submitLabel, campaignId, onSave }) {
    const form = S.form(campaignFields(info), values);
    const box = h("div", { class: "ads-estimate", "aria-live": "polite" }, h("p", { class: "muted", text: "Fill in your bid and budget to see what to expect." }));
    let timer = 0;
    let latest = 0;
    const useBid = (micros) => {
      const control = form.inputs.bid_cents.control;
      control.value = S.fromCents(cents(micros));
      control.dispatchEvent(new Event("input", { bubbles: true }));
      control.focus();
    };
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const mine = ++latest;
        try {
          const data = await api("/estimate", { method: "POST", body: { ...form.read(), campaign_id: campaignId || "" }, silent: true });
          if (mine === latest) box.replaceChildren(estimateNode(data, useBid));
        } catch (error) {
          if (mine === latest) box.replaceChildren(h("p", { class: "muted", text: error.message }));
        }
      }, 350);
    };
    form.node.addEventListener("input", refresh);
    form.node.addEventListener("change", refresh);
    refresh();
    return new Promise((resolve) => {
      Aira.modal({
        title,
        wide: true,
        body: h("form", { onsubmit: (event) => event.preventDefault() }, form.node, h("h3", { class: "suite-h3", text: "What to expect" }), box),
        onClose: () => { clearTimeout(timer); resolve(null); },
        actions: [
          { label: "Cancel", kind: "ghost" },
          { label: submitLabel, kind: "primary", onClick: async () => {
            form.setError("");
            for (const { field, control } of Object.values(form.inputs)) {
              if (field.required && !String(control.value || "").trim()) { form.setError(`Enter ${field.label.toLowerCase()}.`); control.focus(); return false; }
            }
            try { resolve(await onSave(form.read())); return true; } catch (error) { form.setError(error && error.message ? error.message : String(error)); return false; }
          } },
        ],
      });
    });
  }

  // ---------------------------------------------------------------- overview
  async function overviewTab(panel, info) {
    const month = info.month;
    const low = info.balance_days !== null && info.balance_days !== undefined && info.balance_days < LOW_DAYS;
    panel.append(S.kpis([
      { label: "Balance", value: usd(info.balance_micros), tone: info.balance_micros > 0 && !low ? "" : "warn", hint: info.balance_days !== null && info.balance_days !== undefined ? `lasts ${days(info.balance_days)}` : info.hosted ? "prepaid, billed with your subscription" : "prepaid" },
      { label: "Spent this month", value: usd(month.spend_micros) },
      { label: "Impressions this month", value: month.impressions.toLocaleString() },
      { label: "Clicks this month", value: month.clicks.toLocaleString(), hint: `${month.ctr_percent}% of impressions` },
      { label: "Cost per 1,000 impressions", value: usd(month.ecpm_micros) },
    ]));
    const todo = [];
    if (!info.advertiser || info.advertiser === "Your business") todo.push(h("li", {}, h("span", { text: "Viewers see your business name on your ads. " }), h("a", { href: "#/hub?tab=business", text: "Add your business name" })));
    if (info.balance_micros <= 0) todo.push(h("li", {}, h("span", { text: "Your balance is empty, so no ads run. " }), h("a", { href: "#/ads?tab=balance", text: info.hosted ? "Top up" : "See how to add money" })));
    else if (low) todo.push(h("li", {}, h("span", { text: `Your balance runs out in ${days(info.balance_days)} at this week's spending. ` }), h("a", { href: "#/ads?tab=balance", text: info.hosted ? "Top up" : "Add money" })));
    if (info.paused_no_balance) todo.push(h("li", { text: info.paused_no_balance === 1 ? "1 campaign is paused until you add money." : `${info.paused_no_balance} campaigns are paused until you add money.` }));
    if (info.pending_review) todo.push(h("li", { text: info.pending_review === 1 ? "1 ad is waiting for review." : `${info.pending_review} ads are waiting for review.` }));
    if (info.rejected) todo.push(h("li", {}, h("span", { text: info.rejected === 1 ? "1 ad was not approved. " : `${info.rejected} ads were not approved. ` }), h("a", { href: "#/ads?tab=campaigns", text: "See why" })));
    panel.append(S.grid(
      S.section("What needs doing", todo.length ? h("ul", {}, ...todo) : h("p", { class: "muted", text: info.active ? "Your ads are running." : "Create a campaign to start." }),
        canCreate() ? h("div", { class: "btn-row ads-todo-new" }, S.button("New campaign", () => newCampaign(info), { kind: "primary", icon: "plus" })) : null),
      S.section("How Ads works",
        h("p", { text: "Your ads run on videos people watch in Monosage: a short video before the video (people can skip it after 5 seconds), a banner beside it, or a sponsored reel." }),
        h("p", { text: "You pay only for ads that played or were on screen for 2 seconds, or for clicks. Views by your own people, repeat views and other invalid traffic are not charged." }),
        h("p", { text: "Record the sales each campaign brings, and Reports shows what you get back for each dollar you spend." }),
        h("p", { text: `Every ad is reviewed before it runs. ${info.settings.revenue_share_percent}% of what you pay goes to the creator of the video your ad ran on.` }))));
  }

  // ---------------------------------------------------------------- campaigns
  function newCampaign(info) {
    return campaignDialog(info, {
      title: "New campaign",
      values: { objective: "views", bid_cents: 200, total_budget_cents: 5000, daily_budget_cents: 1000, frequency_cap: 3, utm: true },
      submitLabel: "Create campaign",
      onSave: async (values) => { const made = (await api("/campaigns", { method: "POST", body: values })).campaign; go({ tab: "campaigns", campaign: made.id }); return made; },
    });
  }

  // "+ New" asked for an ad or for sales without a campaign open: ask which campaign, then open it for that.
  async function pickCampaign(info, kind) {
    const { campaigns } = await api("/campaigns");
    const choices = campaigns.filter((row) => kind === "sales" || row.status !== "ended");
    if (!choices.length) {
      Aira.toast(kind === "ad" ? "An ad belongs to a campaign: make the campaign first." : "Sales are recorded on a campaign: make one first.", "info");
      await newCampaign(info);
      return;
    }
    const chosen = await S.edit({
      title: kind === "ad" ? "New ad" : "Record sales",
      fields: [{ name: "campaign", label: kind === "ad" ? "Which campaign is the ad for?" : "Which campaign brought the sales?", type: "select", options: choices.map((row) => [row.id, `${row.name} (${STATUS_WORD[row.status] || row.status})`]) }],
      values: { campaign: choices[0].id },
      submitLabel: "Continue",
      onSave: (values) => values.campaign,
    });
    if (chosen && ID.test(chosen)) go({ tab: "campaigns", campaign: chosen, new: kind });
  }

  async function campaignsTab(panel, info, route, setHead) {
    if (route.query.campaign) { await campaignPage(panel, info, route.query.campaign, route, setHead); return; }
    const [{ campaigns }, ads] = await Promise.all([api("/campaigns"), api("/creatives", { silent: true }).catch(() => ({ creatives: [] }))]);
    const search = h("input", { type: "search", class: "ads-search", placeholder: "Search campaigns and ads", "aria-label": "Search campaigns and ads", value: route.query.q || "" });
    const status = Aira.ui.select(STATUS_FILTER, route.query.status || "", { "aria-label": "Show campaigns by status" });
    const holder = h("div", {});
    const results = h("p", { class: "ads-results", role: "status" });
    const adHolder = h("div", {});
    const meta = (row) => h("span", { class: "ads-row-meta muted small", text: [STATUS_WORD[row.status] || row.status, `${usd(row.spent_micros)} of ${usd(row.total_budget_micros)}`, count(row.stats.impressions, "impression")].join(" · ") });
    const columns = [
      { key: "name", label: "Campaign", render: (row) => h("span", { class: "ads-row-name" }, h("a", { href: `#/ads?tab=campaigns&campaign=${row.id}`, text: row.name }), meta(row)) },
      { key: "status", label: "Status", render: (row) => h("span", {}, pill(row.status), row.paused_reason ? h("span", { class: "muted small", text: ` ${PAUSED[row.paused_reason] || ""}` }) : null) },
      { key: "bid", label: "Bid", render: (row) => `${usd(row.bid_micros)} ${row.bid_type === "cpm" ? "per 1,000" : "per click"}` },
      { key: "spent", label: "Spent", num: true, render: (row) => `${usd(row.spent_micros)} of ${usd(row.total_budget_micros)}` },
      { key: "impressions", label: "Impressions", num: true, render: (row) => row.stats.impressions.toLocaleString() },
      { key: "clicks", label: "Clicks", num: true, render: (row) => row.stats.clicks.toLocaleString() },
      { key: "roas", label: "Sales for $1 spent", num: true, render: (row) => (row.sales.orders ? roasText(row.sales.roas) : "") },
      { key: "ads", label: "Ads approved", num: true, render: (row) => `${row.approved_count} of ${row.creatives_count}` },
    ];
    const make = canCreate() ? h("div", { class: "btn-row ads-empty-actions" }, S.button("New campaign", () => newCampaign(info), { kind: "primary", icon: "plus" })) : null;
    const render = () => {
      const q = search.value.trim();
      const needle = folded(q);
      const shown = campaigns.filter((row) => (!status.value || row.status === status.value) && (!needle || campaignMatches(row, needle)));
      // Searching also finds ads by their headline, text or button, each opening its campaign's page at the ad.
      const adsFound = needle ? (ads.creatives || []).filter((row) => adMatches(row, needle) && (!status.value || row.campaign_status === status.value)) : [];
      results.hidden = !needle;
      results.textContent = needle ? `${count(shown.length, "campaign")} and ${count(adsFound.length, "ad")} match “${q}”.` : "";
      holder.replaceChildren(S.table({
        columns,
        rows: shown,
        caption: "Campaigns",
        onRow: (row) => go({ tab: "campaigns", campaign: row.id }),
        empty: campaigns.length
          ? Aira.ui.empty(adsFound.length ? "No campaigns match" : "Nothing matches", needle ? "Try another word, or clear the search." : "Choose another status.", h("div", { class: "btn-row ads-empty-actions" }, S.button("Clear the search", () => { search.value = ""; status.value = ""; render(); search.focus(); }, { kind: "secondary" })))
          : Aira.ui.empty("No campaigns yet", "A campaign holds your budget, your bid and who sees your ads. Make one, add a video or a banner, and start it.", make),
      }));
      adHolder.replaceChildren(adsFound.length ? S.section(`Ads that match (${adsFound.length})`, S.table({
        caption: "Ads that match",
        columns: [
          { key: "headline", label: "Ad", render: (row) => h("span", { class: "ads-row-name" }, h("a", { href: adHref(row), text: row.headline }), h("span", { class: "ads-row-meta muted small", text: [row.campaign_name, REVIEW_WORD[row.review_status]].filter(Boolean).join(" · ") })) },
          { key: "campaign_name", label: "Campaign" },
          { key: "kind_label", label: "Kind" },
          { key: "review_status", label: "Review", render: (row) => pill(row.review_status) },
        ],
        rows: adsFound,
        onRow: (row) => { location.hash = adHref(row); },
      })) : "");
    };
    search.addEventListener("input", render);
    status.addEventListener("change", render);
    panel.append(h("div", { class: "btn-row ads-filters" }, search, status, S.button("Download CSV", () => Aira.download("/api/ads/campaigns?format=csv", "ads-campaigns.csv"), { kind: "secondary" })), results, h("section", { class: "card suite-section ads-campaigns", "aria-label": "Campaigns" }, holder), h("div", { class: "ads-campaigns" }, adHolder));
    render();
  }

  function changeAd(info, creative, reload) {
    return S.edit({
      title: "Change the ad",
      fields: [
        { name: "headline", label: "Headline", required: true },
        { name: "body", label: "Text (optional)" },
        { name: "cta", label: "Button", type: "select", options: info.ctas.map((cta) => [cta, cta]) },
      ],
      values: creative,
      submitLabel: "Save and send for review",
      onSave: async (values) => { await api(`/creatives/${creative.id}`, { method: "PATCH", body: values }); Aira.toast("Saved. A changed ad is reviewed again before it runs.", "success"); await reload(); },
    });
  }

  function creativeNode(info, creative, reload) {
    const media = creative.mime.startsWith("video/")
      ? h("video", { src: creative.media_url, controls: true, muted: true, playsinline: true, preload: "metadata", class: "vid-preview", "aria-label": creative.headline })
      : h("img", { src: creative.media_url, alt: creative.headline, class: "vid-preview" });
    const stats = creative.stats;
    return h("div", { class: "card suite-section ads-creative", id: `ad-${creative.id}`, tabindex: "-1", "aria-label": `Ad: ${creative.headline}` },
      h("div", { class: "vid-row", style: { justifyContent: "space-between" } }, h("strong", { text: creative.headline }), pill(creative.review_status)),
      h("span", { class: "vid-meta", text: `${creative.kind_label} · ${creative.cta}${creative.body ? ` · ${creative.body}` : ""}` }),
      media,
      creative.review_status === "rejected" && creative.review_note ? h("p", { role: "status", text: `Not approved: ${creative.review_note}` }) : null,
      h("span", { class: "vid-meta", text: `${count(stats.impressions, "impression")} · ${count(stats.clicks, "click")}${stats.impressions ? ` (${stats.ctr_percent}%)` : ""} · ${usd(stats.spend_micros)}` }),
      canCreate() ? h("div", { class: "btn-row" },
        S.button("Change", () => changeAd(info, creative, reload), { kind: "ghost sm" }),
        S.button("Remove", async () => { if (await S.confirmDelete("this ad")) { await api(`/creatives/${creative.id}`, { method: "DELETE" }); await reload(); } }, { kind: "ghost sm" })) : null);
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
    const section = S.section("Add an ad",
      Aira.ui.field("Kind of ad", kind, "Videos before a video can be skipped after 5 seconds; a banner is a JPEG, PNG or WebP picture; a sponsored reel is a tall video of up to a minute."),
      Aira.ui.field("Video or picture", file), form.node, status,
      h("div", { class: "btn-row" }, S.button("Upload and send for review", async () => {
        form.setError("");
        const chosen = file.files && file.files[0];
        if (!chosen) { form.setError("Choose the ad's video or picture."); return; }
        const wrongFile = kind.value === "banner" ? /^(video|audio)\//.test(chosen.type) : /^(image|audio)\//.test(chosen.type);
        if (wrongFile) { form.setError(kind.value === "banner" ? "A banner is a picture: choose a JPEG, PNG or WebP image." : "This kind of ad is a video: choose an MP4 or WebM file."); return; }
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
    section.id = "adsAddAd";
    // "+ New ad" on this campaign: bring the form into view and start at its first choice.
    hooks.ad = () => { section.scrollIntoView({ block: "start", behavior: "smooth" }); kind.focus({ preventScroll: true }); };
    return section;
  }

  async function salesSection(campaign, reload) {
    const { sales } = await api(`/campaigns/${campaign.id}/sales`);
    const totals = campaign.sales;
    const record = () => S.edit({
      title: "Record sales from this campaign",
      fields: [
        { name: "day", label: "Day of the sales", type: "date", required: true, max: today() },
        { name: "orders", label: "Number of orders", type: "number", min: 1, max: 100000, required: true },
        { name: "value_cents", label: "What they were worth (USD)", type: "money", required: true },
        { name: "note", label: "Note (optional)", placeholder: "Like: 3 orders with utm_campaign in the shop" },
      ],
      values: { day: today(), orders: 1 },
      submitLabel: "Record sales",
      onSave: async (values) => { await api(`/campaigns/${campaign.id}/sales`, { method: "POST", body: values }); Aira.toast("Recorded.", "success"); await reload(); },
    });
    if (canCreate()) hooks.sales = record;
    return S.section("Sales from this campaign",
      h("p", { class: "muted", text: campaign.utm ? "Count the orders that came from this campaign. Visits from your ads carry utm_source=aira in your shop or website analytics." : "Count the orders that came from this campaign, for example from people who mention the ad." }),
      S.kpis([
        { label: "Orders", value: totals.orders.toLocaleString() },
        { label: "Sales", value: usd(totals.sales_micros) },
        { label: "Sales for each $1 spent", value: totals.roas === null ? "None yet" : roasText(totals.roas), hint: totals.roas === null ? "nothing spent yet" : "", tone: totals.roas === null ? "" : totals.roas >= 1 ? "good" : "warn" },
        { label: "Cost of one order", value: totals.cost_per_order_micros === null ? "None yet" : usd(totals.cost_per_order_micros), hint: totals.cost_per_order_micros === null ? "no orders yet" : "" },
        { label: "Left after the ads", value: usd(totals.profit_micros), tone: totals.profit_micros < 0 ? "warn" : "" },
      ]),
      canCreate() ? h("div", { class: "btn-row" }, S.button("Record sales", record, { kind: "secondary", icon: "plus" })) : null,
      S.table({
        columns: [
          { key: "day", label: "Day" },
          { key: "orders", label: "Orders", num: true },
          { key: "value_micros", label: "Worth", num: true, render: (row) => usd(row.value_micros) },
          { key: "note", label: "Note" },
          canCreate() ? { key: "remove", label: "", render: (row) => S.button("Remove", async () => { if (await S.confirmDelete("these sales")) { await api(`/sales/${row.id}`, { method: "DELETE" }); await reload(); } }, { kind: "ghost sm" }) } : null,
        ].filter(Boolean),
        rows: sales,
        empty: Aira.ui.empty("No sales recorded", "When this campaign brings orders, record them here to see what your ads earn you."),
      }));
  }

  async function campaignPage(panel, info, id, route, setHead) {
    const reload = async () => { delete hooks.ad; delete hooks.sales; panel.replaceChildren(); await campaignPage(panel, info, id, { query: {} }, setHead); };
    const { campaign } = await api(`/campaigns/${id}`);
    // The page is named after the campaign; the line under it says what it buys and where it sends people.
    setHead(campaign.name, `${OBJECTIVE[campaign.objective]} · ${campaign.landing_url}`);
    const writable = canCreate();
    const setStatus = async (status) => { const result = (await api(`/campaigns/${campaign.id}/status`, { method: "POST", body: { status } })).campaign; if (result.paused_reason === "no_balance") Aira.toast("The campaign is ready, and starts as soon as your balance has money.", "info"); await reload(); };
    const stats = campaign.stats;
    const ended = campaign.status === "ended";
    const editable = !ended || campaign.restartable;
    panel.append(h("div", { class: "vid-row ads-head" }, h("a", { href: "#/ads?tab=campaigns", class: "btn ghost sm ads-back" }, Aira.icon("adsBack"), h("span", { text: "All campaigns" })), pill(campaign.status), campaign.paused_reason ? h("span", { class: "muted", text: PAUSED[campaign.paused_reason] || "" }) : null));
    panel.append(S.kpis([
      { label: "Spent", value: usd(campaign.spent_micros), hint: `of ${usd(campaign.total_budget_micros)}` },
      { label: "Spent today", value: usd(campaign.spent_today_micros), hint: `of ${usd(campaign.daily_budget_micros)} a day` },
      { label: "Impressions", value: stats.impressions.toLocaleString() },
      { label: "Clicks", value: stats.clicks.toLocaleString(), hint: `${stats.ctr_percent}% of impressions` },
      { label: "Cost per 1,000", value: usd(stats.ecpm_micros) },
    ]));
    const canStart = !ended && campaign.status !== "active" && campaign.creatives_count > 0;
    if (writable) panel.append(h("div", { class: "btn-row ads-actions" },
      canStart ? S.button("Start", () => setStatus("active"), { kind: "primary" }) : null,
      campaign.ready_to_restart ? S.button("Start again", () => setStatus("active"), { kind: "primary" }) : null,
      campaign.status === "active" ? S.button("Pause", () => setStatus("paused"), { kind: "secondary" }) : null,
      editable ? S.button("Edit", () => campaignDialog(info, { title: "Edit campaign", values: campaignValues(campaign), campaignId: campaign.id, submitLabel: "Save", onSave: async (values) => { await api(`/campaigns/${campaign.id}`, { method: "PATCH", body: values }); await reload(); } }), { kind: "secondary" }) : null,
      S.button("Copy", async () => { const made = (await api(`/campaigns/${campaign.id}/duplicate`, { method: "POST", body: {} })).campaign; Aira.toast("Copied as a draft. Change what you like, then start it.", "success"); go({ tab: "campaigns", campaign: made.id }); }, { kind: "ghost" }),
      !ended ? S.button("End", async () => { if (await Aira.confirm({ title: "End this campaign?", message: "Its ads stop for good. Its reports and sales stay. You can copy it later.", confirmLabel: "End", danger: true })) setStatus("ended"); }, { kind: "ghost" }) : null,
      S.button("Delete", async () => { if (await Aira.confirm({ title: "Delete this campaign?", message: "Its ads stop and the sales you recorded for it are removed. What it already spent stays in your reports and balance history.", confirmLabel: "Delete", danger: true })) { await api(`/campaigns/${campaign.id}`, { method: "DELETE" }); go({ tab: "campaigns" }); } }, { kind: "ghost" })));
    if (writable && !ended && !campaign.creatives_count) panel.append(h("p", { class: "ads-note", role: "status", text: "Add an ad below, then start the campaign." }));
    if (campaign.restartable && !campaign.ready_to_restart) panel.append(h("p", { class: "ads-note", role: "status", text: RESTART_HINT[campaign.paused_reason] || "" }));
    if (campaign.ready_to_restart) panel.append(h("p", { class: "ads-note", role: "status", text: "Ready to run again: use Start again." }));
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
      ["Tracking tags", campaign.utm ? "Added to your page link" : "Off"],
    ])));
    panel.append(await salesSection(campaign, reload));
    if (editable) {
      const box = h("div", { class: "ads-estimate", "aria-live": "polite" }, h("p", { class: "muted", text: "Working it out..." }));
      panel.append(S.section("What to expect", box));
      api("/estimate", { method: "POST", body: { campaign_id: campaign.id }, silent: true })
        .then((data) => box.replaceChildren(estimateNode(data, null)))
        .catch((error) => box.replaceChildren(h("p", { class: "muted", text: error.message })));
    }
    panel.append(S.section("Ads", campaign.creatives.length ? h("div", { class: "vid-grid" }, ...campaign.creatives.map((item) => creativeNode(info, item, reload))) : Aira.ui.empty("No ads yet", writable && !ended ? "Add a video or a banner below. It runs once it is approved." : "This campaign has no ads.")));
    if (writable && !ended) panel.append(addCreative(info, campaign, reload));
    // The search box opened one ad (?ad=<id>): show it.
    const shown = ID.test(route.query.ad || "") ? panel.querySelector(`#ad-${route.query.ad}`) : null;
    if (shown) {
      shown.classList.add("ads-found");
      requestAnimationFrame(() => { shown.scrollIntoView({ block: "center" }); shown.focus({ preventScroll: true }); });
    }
  }

  // ---------------------------------------------------------------- reports
  async function reportsTab(panel, info, route) {
    const period = ["7", "30", "90"].includes(String(route.query.days)) ? Number(route.query.days) : 30;
    const chosen = /^[0-9a-f]{32}$/.test(route.query.campaign || "") ? route.query.campaign : "";
    const query = `days=${period}${chosen ? `&campaign=${chosen}` : ""}`;
    const [data, list] = await Promise.all([api(`/reports?${query}`), api("/campaigns")]);
    const picker = Aira.ui.select([["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"]], String(period), { "aria-label": "Period", onchange: (event) => go({ tab: "reports", days: event.target.value, campaign: chosen || undefined }) });
    const which = Aira.ui.select([["", "All campaigns"], ...list.campaigns.map((item) => [item.id, item.name])], chosen, { "aria-label": "Campaign", onchange: (event) => go({ tab: "reports", days: String(period), campaign: event.target.value || undefined }) });
    panel.append(h("div", { class: "btn-row ads-filters" }, picker, which, S.button("Download CSV", () => Aira.download(`/api/ads/reports?format=csv&${query}`, "ads-report.csv"), { kind: "secondary" })));
    const t = data.totals;
    if (!t.impressions && !t.orders && !data.campaigns.length && !data.creatives.length) {
      // Nothing ran and nothing was sold in the period: one clear next step instead of empty tables.
      const next = list.campaigns.length
        ? h("a", { class: "btn secondary", href: "#/ads?tab=campaigns", text: "Open your campaigns" })
        : canCreate() ? S.button("New campaign", () => newCampaign(info), { kind: "primary", icon: "plus" }) : null;
      panel.append(S.section(null, Aira.ui.empty(chosen ? "This campaign has nothing in this period" : "Nothing to report yet",
        list.campaigns.length ? "Reports fill in once your ads are approved and running. Start a campaign with an approved ad, and record the sales it brings on its page." : "Make a campaign, add a video or a banner, and start it. Its impressions, clicks, spending and sales show up here.",
        next ? h("div", { class: "btn-row ads-empty-actions" }, next) : null)));
      return;
    }
    panel.append(S.kpis([
      { label: "Impressions", value: t.impressions.toLocaleString() },
      { label: "Clicks", value: t.clicks.toLocaleString(), hint: `${t.ctr_percent}% click rate` },
      { label: "Spent", value: usd(t.spend_micros), hint: `${usd(t.ecpm_micros)} per 1,000` },
      { label: "Sales recorded", value: usd(t.sales_micros), hint: count(t.orders, "order") },
      { label: "Sales for each $1 spent", value: t.orders && t.roas !== null ? roasText(t.roas) : "None yet", hint: t.orders ? "" : "record sales on a campaign's page", tone: t.orders && t.roas !== null ? (t.roas >= 1 ? "good" : "warn") : "" },
    ]));
    if (data.days.some((row) => row.spend_micros > 0)) panel.append(S.section("Spend per day", S.bars(data.days.map((row) => ({ label: row.day.slice(5), value: row.spend_micros })), { format: usd })));
    const rateColumns = [
      { key: "impressions", label: "Impressions", num: true },
      { key: "clicks", label: "Clicks", num: true },
      { key: "ctr_percent", label: "Click rate", num: true, render: (row) => `${row.ctr_percent}%` },
      { key: "spend_micros", label: "Spent", num: true, render: (row) => usd(row.spend_micros) },
      { key: "ecpm_micros", label: "Per 1,000", num: true, render: (row) => usd(row.ecpm_micros) },
    ];
    if (!chosen) {
      panel.append(S.section("Per campaign", h("p", { class: "muted", text: "Best return first. Record a campaign's sales on its page." }), S.table({
        columns: [
          { key: "name", label: "Campaign", render: (row) => h("a", { href: `#/ads?tab=campaigns&campaign=${row.id}`, text: row.name }) },
          { key: "spend_micros", label: "Spent", num: true, render: (row) => usd(row.spend_micros) },
          { key: "orders", label: "Orders", num: true },
          { key: "sales_micros", label: "Sales", num: true, render: (row) => usd(row.sales_micros) },
          { key: "roas", label: "Sales for $1 spent", num: true, render: (row) => (row.orders ? roasText(row.roas) : "") },
          { key: "profit_micros", label: "Left after the ads", num: true, render: (row) => (row.orders ? usd(row.profit_micros) : "") },
        ],
        rows: data.campaigns,
        onRow: (row) => go({ tab: "campaigns", campaign: row.id }),
        empty: Aira.ui.empty("Nothing in this period", "Campaigns appear here once their ads run or you record their sales."),
      })));
    }
    panel.append(S.section("Per ad", S.table({ columns: [{ key: "headline", label: "Ad", render: (row) => `${row.headline} (${row.campaign})` }, ...rateColumns], rows: data.creatives, empty: Aira.ui.empty("No impressions yet", "Reports fill in once your ads run.") })));
    panel.append(S.section("Per day", S.table({ columns: [{ key: "day", label: "Day" }, ...rateColumns, { key: "sales_micros", label: "Sales", num: true, render: (row) => (row.orders ? usd(row.sales_micros) : "") }], rows: [...data.days].reverse(), empty: Aira.ui.empty("Nothing yet", "Each day your ads run gets a line here.") })));
    if (data.invalid.length) panel.append(S.section("Not charged", h("p", { class: "muted", text: "Ads that were shown but did not count, so you did not pay for them." }), S.table({ columns: [{ key: "reason", label: "Why", render: (row) => INVALID[row.reason] || row.reason }, { key: "n", label: "Impressions", num: true }], rows: data.invalid })));
  }

  // ---------------------------------------------------------------- balance
  async function balanceTab(panel, info) {
    const data = await api("/balance");
    const topUp = () => S.edit({
      title: "Top up your ad balance",
      fields: [{ name: "amount_cents", label: "Amount (USD)", type: "money", required: true, hint: `Between ${S.money(info.limits.topup_min_cents, "USD")} and ${S.money(info.limits.topup_max_cents, "USD")}.` }],
      values: { amount_cents: 2000 },
      submitLabel: "Add to my invoice",
      onSave: async (values) => { await Aira.api("/api/org/ads/topup", { method: "POST", body: values }); Aira.toast("Added to your balance and your next invoice.", "success"); Aira.router.render(); },
    });
    panel.append(S.kpis([
      { label: "Balance", value: usd(data.balance_micros), tone: data.balance_micros > 0 ? "good" : "warn", hint: info.balance_days !== null && info.balance_days !== undefined ? `lasts ${days(info.balance_days)}` : "" },
      { label: "Spending a day", value: usd(info.spend_per_day_micros), hint: "the last 7 days" },
    ]));
    if (data.hosted) {
      panel.append(S.section("Top up",
        h("p", { text: "The amount is added to your organization's next invoice from its billing provider, and to your balance as soon as it is billed. Monosage never takes card details itself. Owners and billing contacts can top up." }),
        h("div", { class: "btn-row" }, S.button("Top up", topUp, { kind: "primary", icon: "plus" }))));
      hooks.topup = topUp;
    } else {
      panel.append(S.section("Adding money", h("p", { text: "On this computer, the owner of its first account records the money an advertiser paid them (cash, a transfer or a payment link), and it becomes your balance. Monosage does not take payments itself." }),
        known.operator ? h("div", { class: "btn-row" }, h("a", { class: "btn secondary", href: "#/ads?tab=admin&new=payment", text: "Record a payment" })) : null));
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
      { label: "Kept by Monosage", value: usd(t.platform_micros) },
      { label: "Waiting to be paid", value: usd(t.requested_micros), tone: t.requested_micros ? "warn" : "" },
      { label: "Paid to creators", value: usd(t.paid_micros) },
      { label: "Refunded", value: usd(t.refunded_micros) },
    ]));
    const settingsForm = S.form([
      { name: "revenue_share_percent", label: "Creators' share of each ad (percent)", type: "number", min: 0, max: 90 },
      { name: "min_payout_cents", label: "Smallest payout (USD)", type: "money" },
    ], { revenue_share_percent: t.settings.revenue_share_percent, min_payout_cents: cents(t.settings.min_payout_micros) });
    settingsForm.node.classList.add("ads-narrow-form");
    panel.append(S.section("Creators' share and payouts", settingsForm.node, h("div", { class: "btn-row" }, S.button("Save", async () => { try { await api("/admin/settings", { method: "PUT", body: settingsForm.read() }); Aira.toast("Saved.", "success"); Aira.router.render(); } catch (error) { settingsForm.setError(error.message); } }, { kind: "secondary" }))));
    panel.append(S.section(`Ads to review (${data.review.length})`, data.review.length ? h("div", { class: "vid-grid" }, ...data.review.map((item) => h("div", { class: "card suite-section" },
      h("strong", { text: item.headline }), h("span", { class: "vid-meta", text: `${item.advertiser} · ${item.kind_label} · ${item.campaign.name}${item.campaign.family_safe ? " · says family-safe" : ""}` }),
      item.mime.startsWith("video/") ? h("video", { src: item.media_url, controls: true, muted: true, preload: "metadata", class: "vid-preview", "aria-label": item.headline }) : h("img", { src: item.media_url, alt: item.headline, class: "vid-preview" }),
      item.body ? h("p", { text: item.body }) : null,
      h("span", { class: "vid-meta", text: `${item.cta} → ${item.campaign.landing_url}` }),
      h("div", { class: "btn-row" },
        S.button("Approve", async () => { await api(`/admin/creatives/${item.id}/review`, { method: "POST", body: { decision: "approve" } }); Aira.toast("Approved. It can run now.", "success"); Aira.router.render(); }, { kind: "primary sm" }),
        S.button("Do not approve", () => S.edit({ title: "Why not?", fields: [{ name: "note", label: "Tell the advertiser what to change", type: "textarea", rows: 3, required: true }], onSave: async (values) => { await api(`/admin/creatives/${item.id}/review`, { method: "POST", body: { decision: "reject", note: values.note } }); Aira.router.render(); } }), { kind: "ghost sm" }))))) : h("p", { class: "muted", text: "Nothing to review. New and changed ads appear here." })));
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
        { key: "platform_micros", label: "Monosage", num: true, render: (row) => usd(row.platform_micros) },
        { key: "invalid", label: "Not counted", num: true },
        { key: "closed", label: "", render: (row) => (row.closed ? S.pill("confirmed") : S.button("Close month", async () => { if (await Aira.confirm({ title: `Close ${row.month}?`, message: "Its traffic is checked once more, then creators' earnings for it are confirmed and can be paid.", confirmLabel: "Close month" })) { await api("/admin/close-month", { method: "POST", body: { month: row.month } }); Aira.router.render(); } }, { kind: "ghost sm" })) },
      ],
      rows: t.months,
      empty: Aira.ui.empty("No ads have run yet", "Each month with ads gets a line here. Months close by themselves on the 2nd of the next month."),
    })));
    const advertisers = data.advertisers.map((item) => [item.home, `${item.name || "Advertiser"} (${usd(item.balance_micros)})`]);
    const recordPayment = () => S.edit({
      title: "Record a payment",
      fields: [{ name: "home", label: "Advertiser", type: "select", options: advertisers.length ? advertisers : [["", "Your own account"]] }, { name: "amount_cents", label: "Amount (USD)", type: "money", required: true }, { name: "reference", label: "Reference (receipt or transfer number)", required: true }, { name: "note", label: "Note (optional)" }],
      onSave: async (values) => { await api("/admin/payments", { method: "POST", body: values }); Aira.toast("Recorded.", "success"); Aira.router.render(); },
    });
    hooks.payment = recordPayment;
    panel.append(S.grid(
      S.section("Record a payment", h("p", { class: "muted", text: "Money an advertiser on this computer paid you. It becomes their ad balance." }), h("div", { class: "btn-row" }, S.button("Record a payment", recordPayment, { kind: "secondary", icon: "plus" }))),
      S.section("Take earnings back", h("p", { class: "muted", text: "When a channel's views were bought or faked: its counted ads for that month stop counting, advertisers are refunded and the earnings are removed." }), h("div", { class: "btn-row" }, S.button("Claw back", () => S.edit({
        title: "Take a channel's earnings back",
        fields: [{ name: "channel_id", label: "Channel id", required: true }, { name: "month", label: "Month (like 2026-10)", required: true }, { name: "reason", label: "Reason", required: true }],
        onSave: async (values) => { const result = await api("/admin/clawback", { method: "POST", body: values }); Aira.toast(`${count(result.impressions, "impression")} taken back.`, "success"); Aira.router.render(); },
      }), { kind: "secondary" })))));
  }

  // ---------------------------------------------------------------- in the Aira frame: "+ New" and the search
  // The tab each "+ New" lands on (an ad and sales on Campaigns, then on the chosen campaign's page).
  const NEW_TAB = { campaign: "campaigns", ad: "campaigns", sales: "campaigns", topup: "balance", payment: "admin" };
  const openCampaign = () => {
    const current = Aira.router.current;
    return current && current.path[0] === "ads" && ID.test((current.query && current.query.campaign) || "") ? current.query.campaign : undefined;
  };
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people make most here, each opening the same form as the button on its page. An ad and sales go
  // to the campaign that is open, or ask which one.
  Aira.appNew.ads = () => (canCreate() ? [
    { label: "New campaign", icon: "ads", href: "#/ads?tab=campaigns&new=campaign" },
    { label: "New ad", icon: "adsFilm", run: () => go({ tab: "campaigns", campaign: openCampaign(), new: "ad" }) },
    { label: "Record sales", icon: "adsSales", run: () => go({ tab: "campaigns", campaign: openCampaign(), new: "sales" }) },
    canTopUp() ? { label: "Top up the balance", icon: "adsWallet", href: "#/ads?tab=balance&new=topup" } : null,
    !Aira.cloud && known.operator ? { label: "Record a payment", icon: "adsWallet", href: "#/ads?tab=admin&new=payment" } : null,
  ].filter(Boolean) : []);
  Aira.appSearch.ads = {
    placeholder: "Search campaigns and ads",
    // Campaigns (name, page, categories) and ads (headline, text, button), up to six, both kinds getting a place.
    async suggest(query) {
      const needle = folded(query.trim());
      if (!needle || !grownUp()) return [];
      const [list, ads] = await Promise.all([cached("/campaigns"), cached("/creatives")]);
      const campaignRows = ((list && list.campaigns) || []).filter((row) => campaignMatches(row, needle)).map((row) => ({
        label: row.name,
        sub: ["Campaign", STATUS_WORD[row.status] || row.status, `${usd(row.spent_micros)} of ${usd(row.total_budget_micros)} spent`].join(" · "),
        icon: "ads",
        href: `#/ads?tab=campaigns&campaign=${row.id}`,
      }));
      const adRows = ((ads && ads.creatives) || []).filter((row) => adMatches(row, needle)).map((row) => ({
        label: row.headline,
        sub: ["Ad", row.kind_label, row.campaign_name, REVIEW_WORD[row.review_status]].filter(Boolean).join(" · "),
        icon: "adsFilm",
        href: adHref(row),
      }));
      return [...campaignRows.slice(0, 3), ...adRows.slice(0, 3), ...campaignRows.slice(3), ...adRows.slice(3)].slice(0, 6);
    },
    // Enter: every campaign and ad that matches, on Campaigns.
    run(query) { go({ tab: "campaigns", q: query.trim() || undefined }); },
  };

  async function fulfil(ask, info) {
    const kind = ask.new;
    if (hooks[kind]) { hooks[kind](); return; }
    if (kind === "campaign") { if (canCreate()) await newCampaign(info); return; }
    if ((kind === "ad" || kind === "sales") && canCreate()) await pickCampaign(info, kind);
  }

  // ---------------------------------------------------------------- the view
  Aira.views.ads = async (main, route) => {
    // "+ New" asks with ?new=<kind>: remember the ask, take it out of the address (Back or a refresh must not ask
    // again) and draw the page without it; the drawn page then answers it.
    if (route.query.new) {
      pending = { new: String(route.query.new) };
      const rest = { ...route.query };
      delete rest.new;
      if (!rest.tab) rest.tab = NEW_TAB[pending.new] || "overview";
      Aira.router.go(["ads"], rest, { replace: true });
      return;
    }
    const ask = pending;
    pending = null;
    answers.clear();
    for (const key of Object.keys(hooks)) delete hooks[key];
    let info;
    try { info = await api("/overview"); } catch (error) {
      if (error && error.code === "grown_up_only") {
        // a child's or a teen's profile: Ads spends the household's money, so offer to switch to a grown-up
        const swap = Aira.life && Aira.life.switchPerson ? S.button("Switch person", () => Aira.life.switchPerson(), { kind: "primary" }) : null;
        main.replaceChildren(h("div", { class: "ads-app ads-grown-ups" }, Aira.ui.empty("Ads is for grown-ups", error.message, swap)));
        return;
      }
      main.replaceChildren(Aira.ui.error(error, () => Aira.router.render()));
      return;
    }
    let operator = false;
    if (!info.hosted) { try { operator = !!(await S.api("videos", "/me", { silent: true })).operator; } catch { operator = false; } }
    if (operator !== known.operator) {
      known.operator = operator;
      if (Aira.renderNav) Aira.renderNav(); // "+ New" offers Record a payment to the computer's owner
    }
    const tabs = TABS.filter((item) => !item.operator || operator);
    const tabId = tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "overview";
    const meta = tabs.find((item) => item.id === tabId);
    // The page is named after the open tab (the app's own name is in the bar above), like a mail app's folder; a
    // campaign's page names the campaign.
    const subtitle = tabId === "overview" ? `Advertising as ${info.advertiser || "your business"} on videos people watch in Monosage` : meta.about;
    // On a phone the menu (and its "+ New") is folded away, so the page keeps its own New campaign there.
    const body = S.page(main, { title: meta.label, subtitle, actions: canCreate() ? [S.button("New campaign", () => newCampaign(info), { kind: "primary ads-phone-new", icon: "plus" })] : [] });
    const setHead = (title, line) => {
      const head = main.querySelector(".page-head");
      if (!head) return;
      head.querySelector("h1").textContent = title;
      const sub = head.querySelector("h1 + p");
      if (sub) sub.textContent = line;
      document.title = `${title} · Ads · Monosage`;
    };
    document.title = `${meta.label} · Ads · Monosage`;
    const tab = S.tabs(body, route, "ads", tabs, tabId);
    const panel = h("div", { class: "suite-body ads-app" });
    body.append(panel);
    try {
      if (tab === "campaigns") await campaignsTab(panel, info, route, setHead);
      else if (tab === "reports") await reportsTab(panel, info, route);
      else if (tab === "balance") await balanceTab(panel, info);
      else if (tab === "admin") await adminTab(panel);
      else await overviewTab(panel, info);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    if (ask && main.isConnected) await fulfil(ask, info);
  };
})();

/* Aira — News (#/news): today's headlines in your own language, and a newsletter you can earn from.
 *
 * Tabs (?tab=): top (top stories), topics (one section at a time, ?topic=), foryou (the words and topics you follow),
 * search (any news, ?q=, with "Follow this"), saved (with a CSV download), newsletter (adults: pick stories, add a
 * note, publish issues to readers, take payments for a paid newsletter) and settings (language, country, topics,
 * child-safe mode, keywords, your own sources, hidden sources). Each article is a card with its headline, source,
 * time and summary (text only, never the site's HTML): Read (the original, in a new tab), Save, Listen, Share, Hide
 * the source, and Add to my newsletter. "Listen to today's headlines" reads the briefing aloud. New headlines are
 * checked every two minutes while a list is open (one check at a time). Everything comes from /api/news/*
 * (aira/suite/news); the newsletter's public page is public-news.js.
 *
 * In the Aira frame (shell.js): "+ New" follows a word, adds a source (not children) or starts the next newsletter
 * issue (grown-ups), each through ?new= and the same form as the button on its tab; the search across the top
 * suggests the headlines already loaded (top stories and saved), the words you follow, the topics and, with a
 * newsletter, your readers, and Enter searches all the news; the tabs are the folders in the left menu.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("news", path, opts);
  // The app's own glyphs for its folders and "+ New" (added to Aira.ICONS only when the name is free).
  const GLYPHS = {
    "nw-topics": '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></svg>',
    "nw-foryou": '<svg viewBox="0 0 24 24"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>',
    "nw-search": '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
    "nw-saved": '<svg viewBox="0 0 24 24"><path d="M6 4h12v17l-6-4-6 4z"/></svg>',
    "nw-letter": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 6.5l8.5 6.5 8.5-6.5"/></svg>',
    "nw-source": '<svg viewBox="0 0 24 24"><path d="M5 4a15 15 0 0 1 15 15M5 10a9 9 0 0 1 9 9"/><circle cx="6" cy="18" r="1.6"/></svg>',
    "nw-reader": '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    "nw-grid": '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></svg>',
    "nw-rows": '<svg viewBox="0 0 24 24"><rect x="4" y="4.5" width="16" height="6" rx="1.5"/><rect x="4" y="13.5" width="16" height="6" rx="1.5"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  // about: the line under the page title. headlines: the tab lists today's headlines (Listen reads them out).
  const TABS = [
    { id: "top", label: "Top stories", icon: "news", about: "Today's biggest headlines, in your language.", headlines: true },
    { id: "topics", label: "Topics", icon: "nw-topics", about: "One section of the news at a time.", headlines: true },
    { id: "foryou", label: "For you", icon: "nw-foryou", about: "News about the words and topics you follow.", headlines: true },
    { id: "search", label: "Search", icon: "nw-search", about: "Any news, about anything. Follow a search to keep up with it." },
    { id: "saved", label: "Saved", icon: "nw-saved", about: "Headlines you kept for later." },
    { id: "newsletter", label: "My newsletter", icon: "nw-letter", about: "Pick stories, publish issues and earn from your readers.", adult: true },
    { id: "settings", label: "Sources & settings", icon: "settings", about: "Language, country, topics and the sources you read." },
  ];
  // What "+ New" asks for (?new=) and the tab that answers it.
  const NEW_TAB = { keyword: "settings", source: "settings", issue: "newsletter" };
  const TOPIC_NAMES = {
    NATION: "Your country",
    WORLD: "World",
    BUSINESS: "Business",
    TECHNOLOGY: "Technology",
    ENTERTAINMENT: "Entertainment",
    SPORTS: "Sports",
    SCIENCE: "Science",
    HEALTH: "Health",
  };
  const STATUS_NAMES = { active: "Free reader", pending: "Waiting for payment", paid: "Paid", cancelled: "Stopped" };
  const TONES = { active: "good", pending: "warn", paid: "good", cancelled: "", ended: "bad" };
  const BIG = { fontSize: "17px", minHeight: "48px" };

  // append/replaceChildren turn null into the text "null": only real nodes go in.
  const add = (node, ...children) => node.append(...children.filter(Boolean));
  const put = (node, ...children) => node.replaceChildren(...children.filter(Boolean));
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");

  // One list checks for new headlines at a time: switching tabs keeps the same page address, so the kit's live
  // check would not stop by itself.
  let stopLive = null;
  const endLive = () => { if (stopLive) stopLive(); stopLive = null; };

  function speak(text) {
    if (Aira.life && Aira.life.speak) Aira.life.speak(text);
    else Aira.toast("This browser cannot read aloud.");
  }

  function notice(text, tone = "info") {
    return h("div", { class: `cloud-banner ${tone}`, role: "status" }, h("span", { text }));
  }

  // The monthly price as typed: "49", "2.50", "Rs 49", "₹1,200". Empty is `empty` (free, or "keep asking");
  // anything else that is not a number is an error, never a free newsletter.
  function readPrice(text, { emptyIsFree }) {
    const raw = String(text || "").trim();
    if (!raw) {
      if (emptyIsFree) return 0;
      throw new Error("Enter the monthly price, or 0 to make the newsletter free.");
    }
    const clean = raw.replace(/[\s,]/g, "");
    const match = /^(?:[^\d.\-]{1,4}\.?)?(\d*\.\d{1,2}|\d+(?:\.\d{0,2})?)$/.exec(clean);
    if (!match) throw new Error(`"${raw}" is not a price. Type a number, like 49 or 2.50 (0 for free).`);
    return Math.round(Number(match[1]) * 100);
  }

  async function share(article) {
    if (navigator.share) {
      try { await navigator.share({ title: article.title, url: article.link }); return; } catch (error) { if (error && error.name === "AbortError") return; }
    }
    await S.copy(article.link);
  }

  // Opening a headline (Read, or a suggestion under the search box) counts it as read.
  function markRead(article) {
    api(`/articles/${article.id}/read`, { method: "POST", body: {}, silent: true }).catch(() => {});
  }
  function openArticle(article) {
    window.open(article.link, "_blank", "noopener,noreferrer");
    markRead(article);
  }

  // ------------------------------------------------------------------ one article
  // ctx: {settings, list} — settings says what the person may do; list is the element holding the cards.
  function articleCard(article, ctx = {}) {
    const settings = ctx.settings || {};
    let node = null;
    const saveButton = S.button(article.saved ? "Saved" : "Save", async () => {
      const data = await api(`/articles/${article.id}/save`, { method: article.saved ? "DELETE" : "POST", body: article.saved ? undefined : {} });
      Object.assign(article, data.article);
      Aira.toast(article.saved ? "Saved for later." : "Removed from saved.", "success");
      // Redraw the card once the button's busy state has finished (it restores its own label).
      setTimeout(() => { if (node && node.isConnected) node.replaceWith(articleCard(article, ctx)); }, 0);
    }, { kind: "secondary" });
    saveButton.setAttribute("aria-pressed", String(!!article.saved));
    const read = h("a", {
      class: "btn",
      href: article.link,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "Read",
      onclick: () => markRead(article),
    });
    const listen = S.button("Listen", () => speak(`${article.title}. ${article.summary || ""}`), { kind: "ghost" });
    const more = [
      S.button("Share", () => share(article), { kind: "ghost sm" }),
      settings.has_newsletter ? S.button("Add to my newsletter", () => S.edit({
        title: "Add to my newsletter",
        fields: [{ name: "note", label: "Why it matters to your readers (optional)", type: "textarea", rows: 3, placeholder: "One or two lines in your own words." }],
        submitLabel: "Add to the next issue",
        onSave: async (values) => {
          await api("/newsletter/picks", { method: "POST", body: { article_id: article.id, note: values.note } });
          Aira.toast("Added to your next issue.", "success");
        },
      }), { kind: "ghost sm" }) : null,
      article.source ? S.button(`Hide ${article.source}`, async () => {
        await api("/muted", { method: "POST", body: { source: article.source } });
        Aira.toast(`News from ${article.source} is hidden. Show it again under Sources & settings.`, "success");
        const holder = ctx.list || (node && node.parentElement);
        for (const card of holder ? holder.querySelectorAll("article.nw-article") : []) if (card.dataset.source === article.source.toLowerCase()) card.remove();
      }, { kind: "ghost sm", title: `Hide all news from ${article.source}` }) : null,
    ];
    let image = null;
    if (article.image && /^https:\/\//i.test(article.image)) {
      image = h("img", { class: "nw-image", src: article.image, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
      image.addEventListener("error", () => image.remove()); // a picture that does not load leaves no empty box
    }
    // The words on the left, the picture (when there is one) on the right, the actions across the bottom.
    node = h("article", { class: `card nw-article${article.read ? " nw-read" : ""}`, "data-source": (article.source || "").toLowerCase(), "data-article": article.id },
      h("div", { class: "nw-article-main" },
        h("div", { class: "nw-article-text" },
          h("p", { class: "muted small nw-meta", text: [article.source, article.ago].filter(Boolean).join(" · ") }),
          h("h2", { class: "nw-title", text: article.title }),
          article.summary ? h("p", { class: "nw-summary", text: article.summary }) : null),
        image),
      h("div", { class: "btn-row nw-actions" }, read, saveButton, listen, ...more.filter(Boolean)));
    return node;
  }

  // Grid (cards side by side) or list (one headline per row): the person's choice, kept on this device.
  const LAYOUTS = [["grid", "Grid", "nw-grid"], ["list", "List", "nw-rows"]];
  const layout = () => (Aira.recall("newsLayout", "grid") === "list" ? "list" : "grid");
  function layoutSwitch(page) {
    const group = h("div", { class: "nw-layout", role: "radiogroup", "aria-label": "Show headlines as" });
    const draw = () => {
      const chosen = layout();
      page.classList.toggle("nw-as-list", chosen === "list");
      for (const button of group.children) {
        const on = button.dataset.layout === chosen;
        button.setAttribute("aria-checked", String(on));
        button.tabIndex = on ? 0 : -1;
      }
    };
    const choose = (value) => { Aira.remember("newsLayout", value); draw(); };
    for (const [value, label, icon] of LAYOUTS) {
      group.append(h("button", { type: "button", role: "radio", class: "nw-layout-btn", dataset: { layout: value }, title: label, "aria-label": label, onclick: () => choose(value) }, Aira.icon(icon)));
    }
    // Arrow keys move between the two, like any radio group.
    group.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      const next = layout() === "grid" ? "list" : "grid";
      choose(next);
      [...group.children].find((button) => button.dataset.layout === next).focus();
    });
    draw();
    return group;
  }

  // ctx.empty: what to do next when there are no headlines (a button or a link), under the empty message.
  function articleList(articles, emptyText, ctx = {}) {
    if (!articles.length) return Aira.ui.empty(ctx.emptyTitle || "No headlines here yet", emptyText, ctx.empty ? h("div", { class: "btn-row nw-empty-actions" }, ...[].concat(ctx.empty).filter(Boolean)) : null);
    const list = h("div", { class: "nw-list" });
    list.append(...articles.map((article) => articleCard(article, { ...ctx, list })));
    return list;
  }

  // ------------------------------------------------------------------ a list that updates itself
  async function liveList(holder, topic, emptyText, settings, empty = null) {
    endLive();
    let data;
    try { data = await api(`/feed?topic=${encodeURIComponent(topic)}`); } catch (error) { put(holder, Aira.ui.error(error, () => liveList(holder, topic, emptyText, settings, empty))); return; }
    if (topic === "top") remember("/feed?topic=top", data);
    const banner = h("div", { hidden: true });
    let since = data.now;
    let waiting = 0;
    put(holder,
      data.error ? notice(data.error, "warning") : null,
      data.child_safe ? notice("Child-safe mode is on: only Science, Technology, Sports and Entertainment news.") : null,
      banner,
      articleList(data.articles, emptyText || (topic === "foryou" ? "Follow a word or a topic, or search for one and press Follow this: its news shows here." : "Try again in a few minutes, or refresh the news now."), { settings, empty: empty || (topic === "foryou" ? followActions(settings) : refreshAction(holder, topic, settings)) }));
    stopLive = S.live(async () => {
      if (!holder.isConnected) { endLive(); return; }
      const fresh = await api(`/feed?topic=${encodeURIComponent(topic)}&since=${encodeURIComponent(since)}`, { silent: true });
      since = fresh.now;
      waiting += fresh.articles.length;
      if (!waiting) return;
      banner.hidden = false;
      put(banner, h("div", { class: "cloud-banner info", role: "status" },
        h("strong", { text: waiting === 1 ? "1 new headline" : `${waiting} new headlines` }),
        h("button", { type: "button", class: "btn secondary sm", text: "Show them", onclick: () => liveList(holder, topic, emptyText, settings, empty) })));
    }, 120000);
  }

  // Under an empty list: refresh the news now, or follow something (For you).
  function refreshAction(holder, topic, settings) {
    return S.button("Refresh now", async () => {
      const result = await api("/refresh", { method: "POST", body: {} });
      if (result.error) { Aira.toast(result.error, "error"); return; }
      await liveList(holder, topic, "", settings);
    }, { kind: "secondary" });
  }
  function followActions() {
    return [
      S.button("Follow a word or topic", () => followDialog(() => Aira.router.render()), { icon: "nw-foryou" }),
      h("a", { class: "btn secondary", href: "#/news?tab=search", text: "Search the news" }),
    ];
  }

  // The forms "+ New" opens, the same as the buttons on their tabs.
  function followDialog(rerender) {
    return S.edit({
      title: "Follow a word or topic",
      fields: [{ name: "query", label: "Word or topic", required: true, placeholder: "Hyderabad metro, cricket, rain" }, { name: "alert", label: "Alert me when it is in the news", type: "checkbox" }],
      submitLabel: "Follow",
      onSave: async (values) => {
        await api("/keywords", { method: "POST", body: values });
        forget("/settings");
        Aira.toast(`You follow ${values.query.trim()}. Its news shows under For you.`, "success");
        rerender();
      },
    });
  }
  function sourceDialog(rerender) {
    return S.edit({
      title: "Add a news source",
      fields: [{ name: "url", label: "Feed address (RSS or Atom)", type: "url", required: true, placeholder: "https://example.com/rss" }, { name: "title", label: "Name (optional)" }],
      submitLabel: "Add",
      onSave: async (values) => { await api("/sources", { method: "POST", body: values }); rerender(); },
    });
  }

  async function listenToHeadlines(button) {
    await Aira.withBusy(button, async () => {
      const brief = await api("/briefing");
      if (!brief.headlines.length) { Aira.toast(brief.error || "There are no headlines yet."); return; }
      speak(brief.summary);
    });
  }

  // ------------------------------------------------------------------ tabs
  async function topicsTab(body, route, settings) {
    const available = settings.topics_available.filter((t) => t.code !== "top");
    const current = (route.query.topic && available.some((t) => t.code === route.query.topic)) ? route.query.topic : (available[0] || {}).code;
    const chips = h("div", { class: "btn-row", role: "tablist", "aria-label": "Topics" }, ...available.map((topic) => h("button", {
      type: "button",
      role: "tab",
      class: `btn ${topic.code === current ? "" : "secondary"}`,
      "aria-selected": String(topic.code === current),
      text: TOPIC_NAMES[topic.code] || topic.name,
      onclick: () => Aira.router.go(["news"], { tab: "topics", topic: topic.code }),
    })));
    const holder = h("div", {}, Aira.ui.skeleton(3));
    add(body, chips, holder);
    if (current) await liveList(holder, current, "There is no news in this topic right now.", settings);
  }

  async function searchTab(body, route, settings) {
    const wanted = String(route.query.q || "").trim();
    const input = h("input", { type: "search", name: "q", value: wanted, maxlength: "60", "aria-label": "Search the news", placeholder: "A word, a place or a person", enterkeyhint: "search", style: { flex: "1 1 220px", minHeight: "48px" } });
    const form = h("form", { class: "btn-row nw-search", role: "search", onsubmit: (event) => {
      event.preventDefault();
      const q = input.value.trim();
      if (!q) { input.focus(); Aira.toast("Type what to search for."); return; }
      Aira.router.go(["news"], { tab: "search", q });
    } }, input, h("button", { type: "submit", class: "btn", style: BIG, text: "Search" }));
    const safe = settings.child_safe ? notice("Child-safe mode is on: search looks only in Science, Technology, Sports and Entertainment news.") : null;
    add(body, form, safe);
    if (!wanted) {
      add(body, Aira.ui.empty(settings.child_safe ? "Search your news" : "Search any news", "Type a word, a place or a person and press Search. Follow it to see its news under For you."));
      setTimeout(() => input.focus(), 0);
      return;
    }
    const data = await api(`/search?q=${encodeURIComponent(wanted)}`);
    const follow = data.following
      ? h("span", { class: "muted", text: "You follow this: its news is also under For you." })
      : S.button("Follow this", async () => {
        await api("/keywords", { method: "POST", body: { query: data.query } });
        Aira.toast(`You follow ${data.query}. Its news shows under For you.`, "success");
        follow.replaceWith(h("span", { class: "muted", text: "You follow this: its news is also under For you." }));
      }, { kind: "secondary" });
    add(body,
      h("div", { class: "nw-search-head" }, h("h2", { class: "suite-h2", text: `News about ${data.query}` }), follow),
      data.error ? notice(data.error, "warning") : null,
      articleList(data.articles, "Nothing found. Try other words.", { settings }));
  }

  async function savedTab(body, settings) {
    const data = await api("/saved");
    remember("/saved", data);
    add(body,
      data.articles.length ? h("div", { class: "btn-row" }, S.button("Download as a spreadsheet (CSV)", () => Aira.download("/api/news/saved?format=csv", "saved-news.csv"), { kind: "secondary" })) : null,
      articleList(data.articles, "Press Save on any headline to keep it here for later.", { settings, emptyTitle: "Nothing saved yet", empty: h("a", { class: "btn secondary", href: "#/news?tab=top", text: "See today's top stories" }) }));
  }

  // ------------------------------------------------------------------ the newsletter
  function startForm(body, data, rerender) {
    add(body,
      S.section("Earn from the news you follow",
        h("p", { text: "Pick the stories that matter to people you know, add a line on why, and send them as a newsletter. Make it free, or charge a small monthly price and earn from every reader." }),
        h("ul", { class: "nw-ideas" },
          h("li", { text: "A local digest: what happened in your town this week." }),
          h("li", { text: "A trade digest: crop prices, rules and tenders for farmers or shopkeepers." }),
          h("li", { text: "A study digest: current affairs for exam candidates." })),
        h("p", { class: "muted", text: "Readers pay you directly through your own payment link. You mark each payment received, and it shows in your earnings and the Business hub." }),
        h("div", { class: "btn-row" }, S.button("Start my newsletter", () => startDialog(data, rerender)))));
    hooks.issue = () => startDialog(data, rerender);
  }

  function startDialog(data, rerender) {
    return S.edit({
      title: "Start my newsletter",
      fields: [
        { name: "name", label: "Name", required: true, placeholder: "Hyderabad This Week" },
        { name: "about", label: "What it is about", type: "textarea", rows: 3, placeholder: "The week's news for our neighbourhood, in five minutes." },
        { name: "price", label: `Monthly price in ${data.currency} (leave empty or 0 for free)`, placeholder: "0.00" },
      ],
      submitLabel: "Start",
      onSave: async (values) => {
        const priceCents = readPrice(values.price, { emptyIsFree: true });
        await api("/newsletter", { method: "POST", body: { name: values.name, about: values.about, price_cents: priceCents } });
        forget("/settings");
        Aira.toast("Your newsletter is ready. Press Add to my newsletter on any headline.", "success");
        rerender();
      },
    });
  }

  function pickRow(pick, index, count, rerender) {
    return h("li", { class: "nw-pick" },
      h("div", { class: "nw-pick-text" },
        h("strong", { text: `${index + 1}. ${pick.title}` }),
        h("span", { class: "muted small", text: pick.source || "" }),
        pick.note ? h("p", { style: { margin: "4px 0 0" }, text: pick.note }) : h("p", { class: "muted small", style: { margin: "4px 0 0" }, text: "No note yet." })),
      h("div", { class: "btn-row" },
        S.button(pick.note ? "Edit note" : "Add a note", () => S.edit({
          title: "Your note",
          fields: [{ name: "note", label: "Why it matters to your readers", type: "textarea", rows: 3 }],
          values: { note: pick.note },
          onSave: async (values) => { await api(`/newsletter/picks/${pick.id}`, { method: "PATCH", body: { note: values.note } }); rerender(); },
        }), { kind: "ghost sm" }),
        index > 0 ? S.button("Move up", async () => { await api(`/newsletter/picks/${pick.id}`, { method: "PATCH", body: { move: "up" } }); rerender(); }, { kind: "ghost sm" }) : null,
        index < count - 1 ? S.button("Move down", async () => { await api(`/newsletter/picks/${pick.id}`, { method: "PATCH", body: { move: "down" } }); rerender(); }, { kind: "ghost sm" }) : null,
        S.button("Remove", async () => { await api(`/newsletter/picks/${pick.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })));
  }

  function nextIssue(data, rerender) {
    const letter = data.newsletter;
    const title = h("input", { type: "text", maxlength: "120", placeholder: `${letter.name}, issue ${data.issues.length ? data.issues[0].number + 1 : 1}` });
    const intro = h("textarea", { rows: 4, maxlength: "2000", placeholder: "A few words to open the issue (optional)." });
    const picks = data.picks;
    const who = letter.price_cents ? (data.recipients === 1 ? "1 paid-up reader" : `${data.recipients} paid-up readers`) : (data.recipients === 1 ? "1 reader" : `${data.recipients} readers`);
    const section = S.section("Next issue",
      picks.length
        ? h("ol", { class: "nw-picks" }, ...picks.map((pick, index) => pickRow(pick, index, picks.length, rerender)))
        : Aira.ui.empty("No stories yet", "Press Add to my newsletter on any headline (Top stories, Topics, For you, Search or Saved).", h("div", { class: "btn-row nw-empty-actions" }, h("a", { class: "btn secondary", href: "#/news?tab=top", text: "Pick from today's top stories" }))),
      picks.length ? Aira.ui.field("Title", title) : null,
      picks.length ? Aira.ui.field("Opening", intro) : null,
      picks.length ? h("div", { class: "btn-row" },
        S.button("Suggest a title and opening", async () => {
          const made = await api("/newsletter/suggest", { method: "POST", body: {} });
          title.value = made.title;
          intro.value = made.intro;
        }, { kind: "secondary" }),
        S.button("Publish this issue", async () => {
          const where = data.page_off ? `Your page is turned off, so it goes by email only, to ${who}.` : `It goes on your newsletter's page and, by email, to ${who}.`;
          const ok = await Aira.confirm({ title: "Publish this issue", message: `${where} Emails wait in the Business hub outbox until you approve them, unless you turned on automatic sending.`, confirmLabel: "Publish" });
          if (!ok) return;
          const made = (await api("/newsletter/issues", { method: "POST", body: { title: title.value, intro: intro.value } })).issue;
          Aira.toast(made.emailed ? `Issue ${made.number} is published and ready to email to ${made.emailed === 1 ? "1 reader" : `${made.emailed} readers`}.` : made.link ? `Issue ${made.number} is published on your page.` : `Issue ${made.number} is saved. Nobody gets it yet: your page is turned off and no reader is due an email.`, "success");
          rerender();
        })) : null,
      picks.length ? h("p", { class: "muted small", text: `Goes to ${who}.` }) : null);
    section.classList.add("nw-next-issue");
    section.tabIndex = -1;
    // "+ New" > New newsletter issue: the title to type in, or (no stories yet) the section that says how to add them.
    hooks.issue = () => {
      (picks.length ? title : section).focus();
      if (!picks.length) Aira.toast("Press Add to my newsletter on any headline to put it in the next issue.");
    };
    return section;
  }

  function issuesList(data, rerender) {
    if (!data.issues.length) return S.section("Published issues", h("p", { class: "muted", text: "Nothing published yet. Your first issue shows here." }));
    return S.section("Published issues", h("ul", { class: "life-people" }, ...data.issues.map((issue) => h("li", { class: "life-person" },
      h("div", {}, h("strong", { text: `${issue.number}. ${issue.title}` }), h("div", { class: "muted small", text: `${day(issue.published_at)} · ${issue.stories === 1 ? "1 story" : `${issue.stories} stories`} · ${issue.emailed === 1 ? "emailed to 1 reader" : `emailed to ${issue.emailed} readers`}` })),
      S.button("Delete", async () => {
        if (!(await S.confirmDelete(`issue ${issue.number}`))) return;
        await api(`/newsletter/issues/${issue.id}`, { method: "DELETE" });
        rerender();
      }, { kind: "ghost sm" })))));
  }

  async function readersSection(data, route, rerender) {
    const status = ["", "pending", "paid", "active", "cancelled"].includes(route.query.status || "") ? route.query.status || "" : "";
    const q = String(route.query.who || "");
    const rows = (await api(`/newsletter/subscribers?status=${status}&q=${encodeURIComponent(q)}`)).subscribers;
    const filter = Aira.ui.select([["", "Everyone"], ["pending", "Waiting for payment"], ["paid", "Paid"], ["active", "Free readers"], ["cancelled", "Stopped"]], status, { "aria-label": "Show readers" });
    filter.addEventListener("change", () => Aira.router.go(["news"], { tab: "newsletter", status: filter.value, who: q }));
    const find = h("input", { type: "search", value: q, placeholder: "Name, email or reference", "aria-label": "Find a reader" });
    find.addEventListener("keydown", (event) => { if (event.key === "Enter") Aira.router.go(["news"], { tab: "newsletter", status, who: find.value.trim() }); });
    const paid = !!data.newsletter.price_cents;
    const row = (reader) => h("li", { class: "life-person nw-reader" },
      h("div", { style: { minWidth: "0" } },
        h("strong", { text: reader.name }),
        h("div", { class: "muted small", style: { overflowWrap: "anywhere" }, text: reader.email }),
        h("div", { class: "small" },
          h("span", { class: `suite-pill ${TONES[reader.status === "paid" && !reader.paid_up ? "ended" : reader.status] || ""}`, text: reader.status === "paid" ? (reader.paid_up ? `Paid until ${day(reader.paid_until)}` : `Ended ${day(reader.paid_until)}`) : STATUS_NAMES[reader.status] || reader.status }),
          reader.status === "pending" ? h("span", { class: "muted", text: ` Reference ${reader.reference}` }) : null)),
      h("div", { class: "btn-row" },
        paid && reader.status !== "cancelled" ? S.button(reader.status === "paid" ? "Another month paid" : "Payment received", async () => {
          const result = await api(`/newsletter/subscribers/${reader.id}/paid`, { method: "POST", body: {} });
          Aira.modal({
            title: "Payment recorded",
            body: h("div", { style: { display: "grid", gap: "8px" } },
              h("p", { text: `${result.amount} from ${reader.name} is in your earnings. They are subscribed until ${day(result.subscriber.paid_until)}.` }),
              h("p", { text: result.email === "queued" ? (result.email_status === "approved" ? "Their access code is on its way by email." : "Their access code is waiting in the Business hub outbox: approve it to send.") : "No email could be prepared." }),
              h("p", {}, h("span", { text: "Their access code: " }), h("strong", { text: result.code })),
              h("p", { class: "muted small", text: "You can also give them this code yourself. A new payment makes a new code." })),
            actions: [{ label: "Done", kind: "primary" }],
            onClose: rerender,
          });
        }, { kind: "sm" }) : null,
        paid && reader.status === "paid" ? S.button("Undo last payment", async () => {
          if (!(await Aira.confirm({ title: "Undo the last payment", message: `The last payment from ${reader.name} stops counting in your earnings, and their subscription is 30 days shorter.`, confirmLabel: "Undo payment", danger: true }))) return;
          await api(`/newsletter/subscribers/${reader.id}/undo`, { method: "POST", body: {} });
          rerender();
        }, { kind: "ghost sm" }) : null,
        reader.status !== "cancelled" ? S.button("Remove", async () => {
          if (!(await Aira.confirm({ title: "Remove this reader", message: `${reader.name} will get no more issues. Their payments stay in your records.`, confirmLabel: "Remove", danger: true }))) return;
          await api(`/newsletter/subscribers/${reader.id}`, { method: "DELETE" });
          rerender();
        }, { kind: "ghost sm" }) : null));
    return S.section("Readers",
      h("div", { class: "btn-row nw-filters" }, filter, find, S.button("Download CSV", () => Aira.download(`/api/news/newsletter/subscribers?format=csv&status=${encodeURIComponent(status)}&q=${encodeURIComponent(q)}`, "newsletter-readers.csv"), { kind: "secondary" })),
      rows.length ? h("ul", { class: "life-people" }, ...rows.map(row)) : h("p", { class: "muted", text: status || q ? "Nobody matches. Try another filter." : "No readers yet. Share your newsletter's page so people can subscribe." }));
  }

  async function newsletterTab(body, route, rerender) {
    const data = await api("/newsletter");
    if (!data.newsletter) { startForm(body, data, rerender); return; }
    const letter = data.newsletter;
    const n = data.numbers;
    const money = (cents) => S.money(cents, letter.currency || data.currency);
    const edit = S.button("Change name or price", () => S.edit({
      title: "Your newsletter",
      fields: [
        { name: "name", label: "Name", required: true },
        { name: "about", label: "What it is about", type: "textarea", rows: 3 },
        { name: "price", label: `Monthly price in ${data.currency} (0 for free)`, placeholder: "0.00" },
      ],
      values: { ...letter, price: letter.price_cents ? S.fromCents(letter.price_cents) : "0" },
      onSave: async (values) => {
        const priceCents = readPrice(values.price, { emptyIsFree: !letter.price_cents });
        // A change between free and paid affects readers: say what happens before it does.
        if (letter.price_cents && !priceCents && n.waiting) {
          const ok = await Aira.confirm({ title: "Make the newsletter free", message: `${n.waiting === 1 ? "1 reader waiting to pay becomes a free reader" : `${n.waiting} readers waiting to pay become free readers`} and gets a welcome email.`, confirmLabel: "Make it free" });
          if (!ok) throw new Error("Not saved. Change the price, or press Save again.");
        }
        if (!letter.price_cents && priceCents && n.free) {
          const ok = await Aira.confirm({ title: "Charge for the newsletter", message: `${n.free === 1 ? "Your free reader stops" : `Your ${n.free} free readers stop`} getting issues until they pay. Each one gets an email with the new price and how to keep reading.`, confirmLabel: "Set the price" });
          if (!ok) throw new Error("Not saved. Change the price, or press Save again.");
        }
        const saved = await api("/newsletter", { method: "POST", body: { name: values.name, about: values.about, price_cents: priceCents } });
        if (saved.told) Aira.toast(saved.told === 1 ? "Saved. Your free reader was told about the price (the email is in the Business hub outbox)." : `Saved. Your ${saved.told} free readers were told about the price (the emails are in the Business hub outbox).`, "success");
        else if (saved.freed) Aira.toast(saved.freed === 1 ? "Saved. 1 waiting reader is now a free reader." : `Saved. ${saved.freed} waiting readers are now free readers.`, "success");
        else Aira.toast("Saved.", "success");
        rerender();
      },
    }), { kind: "ghost" });
    add(body,
      h("div", { class: "nw-letter-head" },
        h("div", {}, h("h2", { class: "suite-h2", text: letter.name }), h("p", { class: "muted", text: letter.price_cents ? `${money(letter.price_cents)} a month` : "Free newsletter" })),
        edit),
      S.kpis([
        { label: letter.price_cents ? "Paid readers" : "Readers", value: letter.price_cents ? n.paid : n.free + n.paid },
        letter.price_cents ? { label: "Waiting for payment", value: n.waiting, tone: n.waiting ? "warn" : "" } : null,
        letter.price_cents && n.free ? { label: "Free readers who have not paid", value: n.free, tone: "warn" } : null,
        { label: "Earned this month", value: money(n.month_cents), tone: n.month_cents ? "good" : "" },
        { label: "Earned in total", value: money(n.earned_cents) },
      ]),
      letter.price_cents && !data.payment_ready ? notice("Add your payment link or payment instructions in the Business hub (Settings) so readers know how to pay you.", "warning") : null,
      letter.price_cents && n.free ? notice(`${n.free === 1 ? "1 reader joined" : `${n.free} readers joined`} while the newsletter was free. They get new issues once they pay: press Payment received when they do.`) : null,
      data.page_off ? notice("Your newsletter's page is turned off. Issues still go to readers by email; create a new link below to open the page again.") : null,
      S.share({
        title: "Your newsletter's page",
        text: "People read your issues and subscribe here. Share it on WhatsApp, Facebook or by email.",
        link: data.link,
        onCreate: async () => (await api("/newsletter/link", { method: "POST", body: {} })).link,
        onRevoke: async () => { await api("/newsletter/link", { method: "DELETE" }); },
      }),
      nextIssue(data, rerender),
      await readersSection(data, route, rerender),
      issuesList(data, rerender));
  }

  // ------------------------------------------------------------------ settings
  async function settingsTab(body, data, rerender) {
    const languageSelect = Aira.ui.select(data.languages.map((l) => [l.code, l.native === l.name ? l.name : `${l.native} (${l.name})`]), data.lang, { "aria-label": "News language" });
    const regionFor = (code) => (data.languages.find((l) => l.code === code) || data.languages[0]).regions;
    const regionSelect = Aira.ui.select(regionFor(data.lang).map((r) => [r.code, r.name]), data.region, { "aria-label": "Country" });
    languageSelect.addEventListener("change", () => {
      const regions = regionFor(languageSelect.value);
      put(regionSelect, ...regions.map((r) => h("option", { value: r.code, text: r.name })));
    });
    const topicBoxes = data.topics_available.map((topic) => {
      const box = h("input", { type: "checkbox", checked: data.topics.includes(topic.code), value: topic.code });
      return h("label", { class: "life-toggle" }, box, h("span", {}, h("strong", { text: topic.code === "top" ? "Top stories" : TOPIC_NAMES[topic.code] || topic.name })));
    });
    const childSafe = h("input", { type: "checkbox", checked: data.child_safe, disabled: !data.can_turn_off_child_safe });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const saveButton = S.button("Save settings", async () => {
      error.hidden = true;
      const topics = topicBoxes.map((label) => label.querySelector("input")).filter((box) => box.checked).map((box) => box.value);
      try {
        await api("/settings", { method: "POST", body: { lang: languageSelect.value, region: regionSelect.value, topics, ...(data.can_turn_off_child_safe ? { child_safe: childSafe.checked } : {}) } });
        Aira.toast("Saved. Your news is now in the language and topics you chose.", "success");
        rerender();
      } catch (problem) { error.textContent = problem.message; error.hidden = false; }
    });

    const keywordRows = data.keywords.map((keyword) => {
      const alert = h("input", { type: "checkbox", checked: keyword.alert, "aria-label": `Alert me about ${keyword.query}` });
      alert.addEventListener("change", async () => {
        try {
          await api(`/keywords/${keyword.id}`, { method: "PATCH", body: { alert: alert.checked } });
          Aira.toast(alert.checked ? "You will get an alert for new headlines about this." : "No more alerts for this.", "success");
        } catch { alert.checked = !alert.checked; } // the error was shown; the box goes back to what is saved
      });
      return h("li", { class: "life-person" },
        h("strong", { text: keyword.query }),
        h("label", { class: "suite-check" }, alert, h("span", { text: "Alert me" })),
        S.button("Remove", async () => { await api(`/keywords/${keyword.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" }));
    });
    const addKeyword = S.button("Follow a word or topic", () => followDialog(rerender), { kind: "secondary" });

    const sourceRows = data.sources.map((source) => h("li", { class: "life-person" },
      h("div", { style: { minWidth: "0" } }, h("strong", { text: source.title }), h("div", { class: "muted small", style: { overflowWrap: "anywhere" }, text: source.error ? `Could not refresh: ${source.error}` : source.url })),
      S.button("Remove", async () => { await api(`/sources/${source.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })));
    const addSource = data.can_add_sources ? S.button("Add a news source", () => sourceDialog(rerender), { kind: "secondary" }) : null;
    const mutedRows = (data.muted || []).map((row) => h("li", { class: "life-person" },
      h("strong", { text: row.source }),
      S.button("Show again", async () => { await api(`/muted/${row.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })));

    add(body,
      S.section("Language and country",
        h("p", { class: "muted", text: "Headlines come in this language, for this country." }),
        Aira.ui.field("Language", languageSelect),
        Aira.ui.field("Country", regionSelect)),
      S.section("Topics you follow", ...topicBoxes),
      S.section("Child-safe mode",
        h("label", { class: "life-toggle" }, childSafe, h("span", {}, h("strong", { text: "Only news for children" }), h("span", { class: "muted", text: data.can_turn_off_child_safe ? "Shows only Science, Technology, Sports and Entertainment, and hides upsetting headlines." : "Child-safe mode stays on for children." })))),
      h("div", { class: "btn-row" }, saveButton), error,
      S.section("Words and topics you follow",
        h("p", { class: "muted", text: "News about these shows under For you." }),
        keywordRows.length ? h("ul", { class: "life-people" }, ...keywordRows) : h("p", { class: "muted", text: "You do not follow any words yet." }),
        h("div", { class: "btn-row" }, addKeyword)),
      S.section("Your own news sources",
        h("p", { class: "muted", text: data.can_add_sources ? "Add the RSS or Atom feed of a newspaper, broadcaster or blog you like." : "Ask an adult to add a news source for you." }),
        sourceRows.length ? h("ul", { class: "life-people" }, ...sourceRows) : h("p", { class: "muted", text: "You have not added any sources." }),
        addSource ? h("div", { class: "btn-row" }, addSource) : null),
      S.section("Sources you hid",
        h("p", { class: "muted", text: "Headlines from these do not show. Press Hide on any headline to add one." }),
        mutedRows.length ? h("ul", { class: "life-people" }, ...mutedRows) : h("p", { class: "muted", text: "You have not hidden any sources." })),
      S.section("Refresh",
        h("p", { class: "muted", text: "News refreshes by itself every 30 minutes." }),
        h("div", { class: "btn-row" }, S.button("Refresh now", async () => {
          const result = await api("/refresh", { method: "POST", body: {} });
          Aira.toast(result.error || (result.added === 1 ? "1 new headline." : `${result.added} new headlines.`), result.error ? "error" : "success");
        }, { kind: "secondary" }))));
  }

  // ------------------------------------------------------------------ in the Aira frame
  // Lists the search box reads, kept a few seconds so typing does not ask the server at every key.
  const CACHE_MS = 20000;
  const cache = new Map(); // path -> { at, data }
  function remember(path, data) { cache.set(path, { at: Date.now(), data }); }
  function forget(path) { cache.delete(path); }
  async function cached(path) {
    const hit = cache.get(path);
    if (hit && Date.now() - hit.at < CACHE_MS) return hit.data;
    const data = await api(path, { silent: true });
    remember(path, data);
    return data;
  }
  const known = () => { const hit = cache.get("/settings"); return hit ? hit.data : null; };
  const folded = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");

  // "+ New": what people make here, each opening the same form as the button on its tab. Before the settings have
  // loaded, the profile says who may add sources (not children) and publish (grown-ups; unknown is offered and the
  // newsletter tab then decides).
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  Aira.appNew.news = () => {
    const mine = known();
    const me = Aira.life && Aira.life.me;
    const canAdd = mine ? mine.can_add_sources : !(me && me.age_group === "child");
    const canPublish = mine ? mine.can_publish : !(me && me.age_group && !["adult", "senior"].includes(me.age_group));
    return [
      { label: "Follow a word or topic", icon: "nw-foryou", href: "#/news?tab=settings&new=keyword" },
      canAdd ? { label: "Add a news source", icon: "nw-source", href: "#/news?tab=settings&new=source" } : null,
      // Without a newsletter yet, the issue starts with the form that makes one.
      canPublish ? { label: "New newsletter issue", icon: "nw-letter", href: "#/news?tab=newsletter&new=issue" } : null,
    ].filter(Boolean);
  };

  Aira.appSearch.news = {
    placeholder: "Search news",
    // The headlines already here (top stories and saved), the words you follow, the topics and your newsletter's
    // readers; up to six, headlines first. A headline opens the story, like Read.
    async suggest(query) {
      const needle = folded(query.trim());
      if (!needle) return [];
      const mine = await cached("/settings");
      const [top, saved, readers] = await Promise.all([
        cached("/feed?topic=top").catch(() => ({ articles: [] })),
        cached("/saved").catch(() => ({ articles: [] })),
        mine.has_newsletter ? cached("/newsletter/subscribers?status=&q=").catch(() => ({ subscribers: [] })) : { subscribers: [] },
      ]);
      const seen = new Set();
      const headlines = [...(saved.articles || []).map((a) => ({ ...a, saved: true })), ...(top.articles || [])].filter((article) => {
        if (seen.has(article.id) || !folded(`${article.title} ${article.source} ${article.summary}`).includes(needle)) return false;
        seen.add(article.id);
        return true;
      }).map((article) => ({ label: article.title, sub: [article.source, article.ago, article.saved ? "Saved" : ""].filter(Boolean).join(" · "), icon: article.saved ? "nw-saved" : "news", run: () => openArticle(article) }));
      const words = (mine.keywords || []).filter((k) => folded(k.query).includes(needle)).map((k) => ({ label: k.query, sub: "You follow this · its news", icon: "nw-foryou", href: `#/news?tab=search&q=${encodeURIComponent(k.query)}` }));
      const topics = (mine.topics_available || []).filter((t) => t.code !== "top" && folded(TOPIC_NAMES[t.code] || t.name).includes(needle)).map((t) => ({ label: TOPIC_NAMES[t.code] || t.name, sub: "Topic", icon: "nw-topics", href: `#/news?tab=topics&topic=${encodeURIComponent(t.code)}` }));
      const people = (readers.subscribers || []).filter((r) => folded(`${r.name} ${r.email}`).includes(needle)).map((r) => ({ label: r.name, sub: `Newsletter reader · ${r.email}`, icon: "nw-reader", href: `#/news?tab=newsletter&who=${encodeURIComponent(r.email)}` }));
      return [...headlines.slice(0, 3), ...words.slice(0, 1), ...topics.slice(0, 1), ...people.slice(0, 1), ...headlines.slice(3), ...words.slice(1), ...topics.slice(1), ...people.slice(1)].slice(0, 6);
    },
    // Enter: any news about the words, on Search (where it can be followed).
    run(query) { Aira.router.go(["news"], { tab: "search", q: query.trim() }); },
  };

  // What "+ New" asked for (?new=): taken off the address first, so Back or a reload does not ask again, and
  // answered by the tab drawn next (hooks are what the drawn tab offers; the rest open their form here).
  let pending = null;
  const hooks = {};
  function fulfil(ask, settings, rerender) {
    if (hooks[ask]) { hooks[ask](); return; }
    if (ask === "keyword") followDialog(rerender);
    else if (ask === "source" && settings.can_add_sources) sourceDialog(rerender);
  }

  Aira.views.news = async (main, route) => {
    endLive();
    const query = route.query || {};
    if (query.new) {
      pending = query.new;
      const rest = { ...query };
      delete rest.new;
      if (!rest.tab) rest.tab = NEW_TAB[pending] || "top";
      Aira.router.go(["news"], rest, { replace: true });
      return;
    }
    const ask = pending;
    pending = null;
    for (const key of Object.keys(hooks)) delete hooks[key];
    let settings;
    try { settings = await api("/settings"); } catch (error) {
      S.page(main, { title: "News" }).append(Aira.ui.error(error, () => Aira.router.render()));
      return;
    }
    remember("/settings", settings);
    const tabs = TABS.filter((t) => !t.adult || settings.can_publish);
    const current = tabs.find((t) => t.id === query.tab) || tabs[0];
    // The page is named for its tab (the app's name is in the bar across the top); a list of headlines can be read aloud.
    let listenButton = null;
    if (current.headlines) {
      listenButton = h("button", { type: "button", class: "btn secondary nw-listen" }, Aira.icon("speaker"), h("span", { text: "Listen to today's headlines" }));
      listenButton.addEventListener("click", () => listenToHeadlines(listenButton));
    }
    // Tabs that list headlines (Search and Saved too) can show them as a grid or a list.
    const listsHeadlines = current.headlines || current.id === "search" || current.id === "saved";
    const layoutHolder = listsHeadlines ? h("div", { class: "nw-layout-holder" }) : null;
    const body = S.page(main, { title: current.label, subtitle: current.about, actions: [listenButton, layoutHolder] });
    body.classList.add("nw-page");
    if (layoutHolder) layoutHolder.replaceWith(layoutSwitch(body));
    const tab = S.tabs(body, route, "news", tabs, current.id);
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(4));
    body.append(panel);
    const rerender = () => Aira.router.render();
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "topics") await topicsTab(holder, route, settings);
        else if (tab === "search") await searchTab(holder, route, settings);
        else if (tab === "saved") await savedTab(holder, settings);
        else if (tab === "newsletter") await newsletterTab(holder, route, rerender);
        else if (tab === "settings") await settingsTab(holder, settings, rerender);
        else await liveList(holder, tab === "foryou" ? "foryou" : "top", "", settings);
        put(panel, holder);
        if (ask) fulfil(ask, settings, rerender);
      } catch (error) { put(panel, Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

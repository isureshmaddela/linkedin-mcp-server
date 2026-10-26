/* Aira — News (#/news): today's headlines in your own language.
 *
 * Tabs (?tab=): top (top stories), topics (one section at a time, ?topic=), foryou (the words and topics you follow),
 * saved, and settings (language, country, topics, child-safe mode, keywords, your own sources). Each article is a
 * card with its headline, source, time and summary (text only, never the site's HTML), Read (the original, in a new
 * tab), Save and Listen. "Listen to today's headlines" reads the briefing aloud. New headlines are checked every two
 * minutes while the page is open. Everything comes from /api/news/* (aira/suite/news).
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("news", path, opts);
  const TABS = [
    { id: "top", label: "Top stories" },
    { id: "topics", label: "Topics" },
    { id: "foryou", label: "For you" },
    { id: "saved", label: "Saved" },
    { id: "settings", label: "Sources & settings" },
  ];
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
  const BIG = { fontSize: "17px", minHeight: "48px" };

  // append/replaceChildren turn null into the text "null": only real nodes go in.
  const add = (node, ...children) => node.append(...children.filter(Boolean));
  const put = (node, ...children) => node.replaceChildren(...children.filter(Boolean));

  function speak(text) {
    if (Aira.life && Aira.life.speak) Aira.life.speak(text);
    else Aira.toast("This browser cannot read aloud.");
  }

  function notice(text, tone = "info") {
    return h("div", { class: `cloud-banner ${tone}`, role: "status" }, h("span", { text }));
  }

  // ------------------------------------------------------------------ one article
  function articleCard(article) {
    let node = null;
    const saveButton = S.button(article.saved ? "Saved" : "Save", async () => {
      const data = await api(`/articles/${article.id}/save`, { method: article.saved ? "DELETE" : "POST", body: article.saved ? undefined : {} });
      Object.assign(article, data.article);
      Aira.toast(article.saved ? "Saved for later." : "Removed from saved.", "success");
      // Redraw the card once the button's busy state has finished (it restores its own label).
      setTimeout(() => { if (node && node.isConnected) node.replaceWith(articleCard(article)); }, 0);
    }, { kind: "secondary" });
    saveButton.setAttribute("aria-pressed", String(!!article.saved));
    const read = h("a", {
      class: "btn",
      href: article.link,
      target: "_blank",
      rel: "noopener noreferrer",
      style: BIG,
      text: "Read",
      onclick: () => { api(`/articles/${article.id}/read`, { method: "POST", body: {}, silent: true }).catch(() => {}); },
    });
    const listen = S.button("Listen", () => speak(`${article.title}. ${article.summary || ""}`), { kind: "ghost" });
    const image = article.image && /^https:\/\//i.test(article.image)
      ? h("img", { src: article.image, alt: "", loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", maxHeight: "220px", objectFit: "cover", borderRadius: "10px", background: "var(--surface-2)" } })
      : null;
    node = h("article", { class: "card", style: { display: "grid", gap: "8px", opacity: article.read ? "0.85" : "1" } },
      image,
      h("h2", { style: { margin: "0", fontSize: "19px", lineHeight: "1.35" }, text: article.title }),
      h("p", { class: "muted", style: { margin: "0" }, text: [article.source, article.ago].filter(Boolean).join(" · ") }),
      article.summary ? h("p", { style: { margin: "0", fontSize: "15px" }, text: article.summary }) : null,
      h("div", { class: "btn-row" }, read, saveButton, listen));
    return node;
  }

  function articleList(articles, emptyText) {
    if (!articles.length) return Aira.ui.empty("No headlines here yet", emptyText);
    return h("div", { style: { display: "grid", gap: "12px" } }, ...articles.map((article) => articleCard(article)));
  }

  // ------------------------------------------------------------------ a list that updates itself
  async function liveList(holder, topic, emptyText) {
    let data;
    try { data = await api(`/feed?topic=${encodeURIComponent(topic)}`); } catch (error) { put(holder, Aira.ui.error(error, () => liveList(holder, topic, emptyText))); return; }
    const banner = h("div", { hidden: true });
    let since = data.now;
    let waiting = 0;
    put(holder,
      data.error ? notice(data.error, "warning") : null,
      data.child_safe ? notice("Child-safe mode is on: only Science, Technology, Sports and Entertainment news.") : null,
      banner,
      articleList(data.articles, topic === "foryou" ? "Add a word or topic under Sources & settings, and its news shows here." : "Try again in a few minutes, or press Refresh now under Sources & settings."));
    S.live(async () => {
      const fresh = await api(`/feed?topic=${encodeURIComponent(topic)}&since=${encodeURIComponent(since)}`, { silent: true });
      since = fresh.now;
      waiting += fresh.articles.length;
      if (!waiting) return;
      banner.hidden = false;
      put(banner, h("div", { class: "cloud-banner info", role: "status" },
        h("strong", { text: waiting === 1 ? "1 new headline" : `${waiting} new headlines` }),
        h("button", { type: "button", class: "btn secondary sm", text: "Show them", onclick: () => liveList(holder, topic, emptyText) })));
    }, 120000);
  }

  async function listenToHeadlines(button) {
    await Aira.withBusy(button, async () => {
      const brief = await api("/briefing");
      if (!brief.headlines.length) { Aira.toast(brief.error || "There are no headlines yet."); return; }
      speak(brief.summary);
    });
  }

  // ------------------------------------------------------------------ tabs
  async function topicsTab(body, route) {
    const settings = await api("/settings");
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
    if (current) await liveList(holder, current, "There is no news in this topic right now.");
  }

  async function savedTab(body) {
    const data = await api("/saved");
    add(body, articleList(data.articles, "Press Save on a headline to keep it here."));
  }

  async function settingsTab(body, rerender) {
    const data = await api("/settings");
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
      alert.addEventListener("change", async () => { await api(`/keywords/${keyword.id}`, { method: "PATCH", body: { alert: alert.checked } }); Aira.toast(alert.checked ? "You will get an alert for new headlines about this." : "No more alerts for this.", "success"); });
      return h("li", { class: "life-person" },
        h("strong", { text: keyword.query }),
        h("label", { class: "suite-check" }, alert, h("span", { text: "Alert me" })),
        S.button("Remove", async () => { await api(`/keywords/${keyword.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" }));
    });
    const addKeyword = S.button("Follow a word or topic", () => S.edit({
      title: "Follow a word or topic",
      fields: [{ name: "query", label: "Word or topic", required: true, placeholder: "Hyderabad metro, cricket, rain" }, { name: "alert", label: "Alert me when it is in the news", type: "checkbox" }],
      submitLabel: "Follow",
      onSave: async (values) => { await api("/keywords", { method: "POST", body: values }); rerender(); },
    }), { kind: "secondary" });

    const sourceRows = data.sources.map((source) => h("li", { class: "life-person" },
      h("div", {}, h("strong", { text: source.title }), h("div", { class: "muted small", text: source.error ? `Could not refresh: ${source.error}` : source.url })),
      S.button("Remove", async () => { await api(`/sources/${source.id}`, { method: "DELETE" }); rerender(); }, { kind: "ghost sm" })));
    const addSource = S.button("Add a news source", () => S.edit({
      title: "Add a news source",
      fields: [{ name: "url", label: "Feed address (RSS or Atom)", type: "url", required: true, placeholder: "https://example.com/rss" }, { name: "title", label: "Name (optional)" }],
      submitLabel: "Add",
      onSave: async (values) => { await api("/sources", { method: "POST", body: values }); rerender(); },
    }), { kind: "secondary" });

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
        h("p", { class: "muted", text: "Add the RSS or Atom feed of a newspaper, broadcaster or blog you like." }),
        sourceRows.length ? h("ul", { class: "life-people" }, ...sourceRows) : h("p", { class: "muted", text: "You have not added any sources." }),
        h("div", { class: "btn-row" }, addSource)),
      S.section("Refresh",
        h("p", { class: "muted", text: "News refreshes by itself every 30 minutes." }),
        h("div", { class: "btn-row" }, S.button("Refresh now", async () => {
          const result = await api("/refresh", { method: "POST", body: {} });
          Aira.toast(result.error || (result.added === 1 ? "1 new headline." : `${result.added} new headlines.`), result.error ? "error" : "success");
        }, { kind: "secondary" }))));
  }

  Aira.views.news = async (main, route) => {
    const listenButton = h("button", { type: "button", class: "btn", style: BIG }, Aira.icon("speaker"), h("span", { text: "Listen to today's headlines" }));
    listenButton.addEventListener("click", () => listenToHeadlines(listenButton));
    const body = S.page(main, { title: "News", subtitle: "Today's headlines in your language.", actions: [listenButton] });
    const tab = S.tabs(body, route, "news", TABS, TABS.some((t) => t.id === route.query.tab) ? route.query.tab : "top");
    const panel = h("div", { class: "suite-body" }, Aira.ui.skeleton(4));
    body.append(panel);
    const render = async () => {
      const holder = h("div", { class: "suite-body" });
      try {
        if (tab === "topics") await topicsTab(holder, route);
        else if (tab === "saved") await savedTab(holder);
        else if (tab === "settings") await settingsTab(holder, () => Aira.router.render());
        else await liveList(holder, tab === "foryou" ? "foryou" : "top", "");
        put(panel, holder);
      } catch (error) { put(panel, Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

/* Aira — Learn (#/learn): a little learning every day, for every age.
 *
 * Tabs (?tab=): today (goal, streak, level, badges, settings), study (one card at a time: show the answer, then
 * Again / Hard / Good / Easy, keys Space and 1-4; &mode=weak practises the hardest cards), decks (your decks, the
 * household's shared decks, starter decks, a deck on any topic, search and filters; a deck's drawer has its cards,
 * import from pasted text or CSV, a CSV download, sharing and selling), progress (a twelve-week calendar, how much you
 * remember, the next week's reviews, your hardest cards, the learning log as CSV), quizzes (multiple choice from a
 * deck), maths (practice by level, relaxed or timed), reading (books and minutes), leaderboard (the family's week) and
 * earn (adults: decks for sale, buyers' requests, confirm a payment to send the access code). Everything is graded
 * and scheduled on the server (/api/learn/*, aira/suite/learn). DOM is built with h() and text only.
 *
 * In the Aira frame (shell.js): the tabs are the left menu's folders and the page is named after the open one; "+ New"
 * offers a deck, a deck on any topic, cards, a book and reading time (?new=<kind>); the search across the top finds
 * decks, cards and books (GET /api/learn/search), and Enter shows them all on Decks (?q=). ?open=<deck>[&card=<id>]
 * opens a deck's drawer at a card and ?book=<id> picks out a book on Reading.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("learn", path, opts);
  // Glyphs for the left menu's folders, "+ New" and the search box (24x24 strokes, like app.js ICONS).
  const GLYPHS = {
    "ln-today": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    "ln-study": '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/></svg>',
    "ln-decks": '<svg viewBox="0 0 24 24"><rect x="3" y="7" width="14" height="14" rx="2"/><path d="M7 3h12a2 2 0 0 1 2 2v12"/></svg>',
    "ln-card": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10h10M7 14h6"/></svg>',
    "ln-quiz": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7v.5M12 17h.01"/></svg>',
    "ln-maths": '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 12h2M14 12h2M8 16h2M14 16h2"/></svg>',
    "ln-book": '<svg viewBox="0 0 24 24"><path d="M2 5h6a4 4 0 0 1 4 4v11a3 3 0 0 0-3-3H2z"/><path d="M22 5h-6a4 4 0 0 0-4 4v11a3 3 0 0 1 3-3h7z"/></svg>',
    "ln-timer": '<svg viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9 2h6"/></svg>',
    "ln-spark": '<svg viewBox="0 0 24 24"><path d="m12 3 1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z"/><path d="m19 15 .8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z"/></svg>',
    "ln-earn": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M14.8 9.3c-.5-.9-1.5-1.4-2.8-1.4-1.5 0-2.6.8-2.6 2s1.1 1.7 2.6 2.1 2.8.9 2.8 2.1-1.2 2-2.8 2c-1.3 0-2.4-.6-2.9-1.5M12 6v1.9M12 16.1V18"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;

  // The page is named after the open tab (the app's name is in the bar above), with a line about it.
  const TABS = [
    { id: "today", label: "Today", icon: "ln-today", about: "A little practice every day. Cards come back just before you would forget them." },
    { id: "study", label: "Study", icon: "ln-study", about: "One card at a time: Space shows the answer, then 1 to 4 says how well you knew it." },
    { id: "decks", label: "Decks", icon: "ln-decks", about: "Your decks, the household's shared decks and ready-made starter decks." },
    { id: "progress", label: "Progress", icon: "analytics", about: "Your practice calendar, how much you remember and what comes back next." },
    { id: "quizzes", label: "Quizzes", icon: "ln-quiz", about: "Multiple-choice questions from your own decks." },
    { id: "maths", label: "Maths", icon: "ln-maths", about: "Practice by level, relaxed or against the clock." },
    { id: "reading", label: "Reading", icon: "ln-book", about: "Your books and the minutes you read. Reading counts toward your goal." },
    { id: "leaderboard", label: "Family", icon: "family", about: "Cheer each other on: XP comes from cards, quizzes, maths and reading." },
  ];
  const EARN_TAB = { id: "earn", label: "Earn", icon: "ln-earn", about: "Sell the decks you made, and send buyers their access code once they have paid." };
  // Selling decks is for adults (the server checks too); hosted members and a household's adults see Earn.
  const isAdult = () => { const me = Aira.life && Aira.life.me; return !me || !me.age_group || ["adult", "senior"].includes(me.age_group); };
  const BIG = { fontSize: "18px", minHeight: "52px", padding: "12px 20px" };
  const GRADE_BUTTONS = [
    { grade: "again", label: "Again", key: "1", hint: "I didn't know it", kind: "danger secondary" },
    { grade: "hard", label: "Hard", key: "2", hint: "I got it, just", kind: "secondary" },
    { grade: "good", label: "Good", key: "3", hint: "I knew it", kind: "" },
    { grade: "easy", label: "Easy", key: "4", hint: "Too easy", kind: "secondary" },
  ];

  // append/replaceChildren turn null into the text "null": only real nodes go in.
  const add = (node, ...children) => node.append(...children.filter(Boolean));
  const put = (node, ...children) => node.replaceChildren(...children.filter(Boolean));

  // What "+ New" or the search asked for (?new=, ?open=, ?book=), taken out of the address and answered once the tab
  // is drawn; each tab puts the answers it can give in `hooks`.
  let pending = null;
  const hooks = {};
  const NEW_TAB = { deck: "decks", topic: "decks", card: "decks", book: "reading", minutes: "reading" };

  // The search across the top and Enter's results on Decks: one request for a few seconds per text.
  const found = new Map();
  const searchAll = (text, limit = 20) => {
    const key = `${limit}|${text.trim().toLowerCase()}`;
    const hit = found.get(key);
    if (hit && Date.now() - hit.at < 5000) return hit.promise;
    const promise = api(`/search?${new URLSearchParams({ q: text.trim(), limit: String(limit) })}`, { silent: true });
    found.set(key, { at: Date.now(), promise });
    promise.catch(() => found.delete(key));
    if (found.size > 40) found.delete(found.keys().next().value);
    return promise;
  };
  const forgetSearch = () => found.clear();

  // A row the search led to: in view and marked for a moment.
  const pick = (node) => {
    if (!node) return;
    node.classList.add("ln-picked");
    node.scrollIntoView({ block: "center" });
    setTimeout(() => node.classList.remove("ln-picked"), 2600);
  };

  let keyHandler = null;
  function setKeys(handler) {
    if (keyHandler) document.removeEventListener("keydown", keyHandler);
    keyHandler = handler;
    if (handler) document.addEventListener("keydown", handler);
  }
  const typing = (event) => ["input", "textarea", "select"].includes((event.target.tagName || "").toLowerCase());

  function progressBar(value, max, label) {
    const percent = max ? Math.min(100, Math.round((value * 100) / max)) : 0;
    return h("div", { class: "suite-progress", role: "progressbar", "aria-label": label, "aria-valuemin": "0", "aria-valuemax": String(max), "aria-valuenow": String(value), style: { height: "12px" } }, h("span", { style: { width: `${percent}%` } }));
  }

  function celebrate(result) {
    const badges = result.new_badges || [];
    if (result.goal && result.goal.just_met) Aira.toast(`Goal met for today! +20 XP bonus${result.goal.freeze_used ? " · your streak freeze kept the streak" : ""}`, "success", { timeout: 6000 });
    if (!badges.length) return;
    Aira.modal({
      title: badges.length === 1 ? "You earned a badge!" : "You earned badges!",
      body: h("div", { style: { display: "grid", gap: "12px", textAlign: "center" } }, ...badges.map((badge) => h("div", {}, h("div", { style: { fontSize: "56px" }, "aria-hidden": "true", text: badge.emoji }), h("strong", { style: { fontSize: "20px" }, text: badge.label }), h("p", { class: "muted", text: badge.about })))),
      actions: [{ label: "Hooray!", kind: "primary" }],
    });
    if (Aira.life && Aira.life.prefs && (Aira.life.prefs.read_aloud || Aira.life.prefs.easy_mode)) Aira.life.speak(`You earned ${badges.map((b) => b.label).join(" and ")}!`);
  }

  // -------------------------------------------------------------------------- today
  async function today(body) {
    const data = await api("/overview");
    const goal = data.goal;
    const level = data.level;
    const waiting = data.counts.due + data.counts.new;
    add(body,
      S.kpis([
        { label: "Streak", value: `🔥 ${data.streak} day${data.streak === 1 ? "" : "s"}`, hint: data.freeze_available ? "1 streak freeze left this week" : "Freeze used this week", tone: data.streak ? "good" : "" },
        { label: "Today's goal", value: goal.label, hint: goal.done ? "Done! 🎉" : "Keep going", tone: goal.done ? "good" : "" },
        { label: "Level", value: `Level ${level.level}`, hint: `${level.xp} XP · ${Math.max(0, level.next_at - level.xp)} to the next` },
        { label: "Cards waiting", value: waiting, hint: `${data.counts.due} to review, ${data.counts.new} new`, tone: waiting ? "warn" : "" },
      ]),
      S.section(goal.done ? "Goal met. Well done!" : "Today's goal",
        progressBar(goal.progress, goal.goal, "Goal progress"),
        h("div", { class: "btn-row" },
          waiting ? h("a", { class: "btn", href: "#/learn?tab=study", style: BIG, text: "Start practising" }) : h("a", { class: "btn secondary", href: "#/learn?tab=decks", style: BIG, text: data.decks ? "Add more cards" : "Choose your first deck" }),
          S.button("Change goal", () => settings(data.settings), { kind: "ghost" }))),
      S.section("Level", progressBar(level.xp - level.this_at, level.next_at - level.this_at, "Level progress"), h("p", { class: "muted", text: `${level.next_at - level.xp} XP to level ${level.level + 1}. Every card, quiz and reading minute counts.` })),
      S.section("Badges", h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: "10px" } }, ...data.badges.map((badge) => h("div", { class: "card", style: { textAlign: "center", opacity: badge.earned ? "1" : "0.45" }, title: badge.about },
        h("div", { style: { fontSize: "34px" }, "aria-hidden": "true", text: badge.emoji }),
        h("strong", { text: badge.label }),
        h("div", { class: "muted small", text: badge.earned ? `Earned ${S.day(badge.earned_on)}` : badge.about }))))),
      data.recent.length ? S.section("Recent quizzes", S.table({ columns: [{ key: "title", label: "Quiz" }, { key: "score", label: "Score", render: (r) => `${r.score} of ${r.total}${r.late ? " (late)" : ""}` }, { key: "finished_at", label: "When", render: (r) => Aira.fmt.ago(r.finished_at) }], rows: data.recent })) : null,
    );
  }

  function settings(current) {
    S.edit({
      title: "Your daily goal",
      fields: [
        { name: "goal_kind", label: "Count my goal in", type: "select", options: [["cards", "Cards"], ["minutes", "Minutes"]] },
        { name: "goal_value", label: "How many each day", type: "number", min: 1, max: 500, required: true },
        { name: "new_per_day", label: "New cards a day", type: "number", min: 0, max: 200, hint: "Fewer new cards make each day lighter." },
        { name: "reviews_per_day", label: "Reviews a day (at most)", type: "number", min: 1, max: 1000 },
        { name: "reading_counts", label: "Reading counts toward my goal", type: "checkbox" },
      ],
      values: current,
      onSave: async (values) => { await api("/settings", { method: "POST", body: values }); Aira.toast("Saved.", "success"); Aira.router.render(); },
    });
  }

  // -------------------------------------------------------------------------- study
  async function study(body, route) {
    const deckId = route.query.deck || "";
    const weak = route.query.mode === "weak";
    const decks = (await api("/decks")).decks;
    const picker = Aira.ui.select([["", "All my decks"], ...decks.map((d) => [d.id, `${d.emoji} ${d.title}`])], deckId, { "aria-label": "Deck to study" });
    picker.addEventListener("change", () => Aira.router.go(["learn"], { tab: "study", deck: picker.value, ...(weak ? { mode: "weak" } : {}) }));
    const stage = h("div", { class: "suite-body" });
    add(body, h("div", { class: "btn-row ln-study-head" }, Aira.ui.field("Study", picker),
      weak ? h("a", { class: "btn ghost", href: `#/learn?tab=study${deckId ? `&deck=${deckId}` : ""}`, text: "Back to today's cards" }) : h("a", { class: "btn ghost", href: `#/learn?tab=study&mode=weak${deckId ? `&deck=${deckId}` : ""}`, text: "Practise my hardest cards" })),
    weak ? h("p", { class: "muted", text: "Your hardest cards: the ones you missed before. Extra practice helps them stick, and they still come back on their usual day." }) : null, stage);
    let queue = [];
    let index = 0;
    let started = Date.now();
    let done = 0;
    let rounds = 0;
    const load = async () => {
      if (weak && rounds > 0) {
        setKeys(null);
        stage.replaceChildren(S.section("Hardest cards done! 💪", h("p", { style: { fontSize: "18px" }, text: `You practised ${done} card${done === 1 ? "" : "s"}. They will come back when they are due.` }),
          h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/learn?tab=study", text: "Today's cards" }), h("a", { class: "btn secondary", href: "#/learn?tab=progress", text: "See my progress" }))));
        return;
      }
      rounds += 1;
      const params = new URLSearchParams();
      if (deckId) params.set("deck_id", deckId);
      if (weak) params.set("mode", "weak");
      const data = await api(`/study${params.toString() ? `?${params}` : ""}`);
      queue = data.cards;
      index = 0;
      if (!queue.length && weak) {
        setKeys(null);
        const practised = data.done_today || 0;
        stage.replaceChildren(S.section(practised ? "Hardest cards practised for today 💪" : "No hard cards yet",
          h("p", { style: { fontSize: "18px" }, text: practised ? `You got ${practised} of your hardest cards right today. Practise them again tomorrow.` : "Cards you miss show up here, so you can practise them a little extra." }),
          h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/learn?tab=study", text: "Today's cards" }))));
        return;
      }
      if (!queue.length) {
        setKeys(null);
        stage.replaceChildren(S.section(data.goal.done ? "All done for today! 🎉" : "No cards waiting",
          h("p", { style: { fontSize: "18px" }, text: data.goal.done ? `You met your goal: ${data.goal.label}. Come back tomorrow for more.` : data.counts.new_total ? "You've studied all the new cards for today. Come back tomorrow!" : "Add a deck to start learning." }),
          h("div", { class: "btn-row" }, h("a", { class: "btn secondary", href: "#/learn?tab=decks", text: "Decks" }), h("a", { class: "btn secondary", href: "#/learn?tab=quizzes", text: "Take a quiz" }))));
        return;
      }
      show();
    };
    const show = () => {
      const card = queue[index];
      if (!card) { load(); return; }
      started = Date.now();
      let flipped = false;
      const answer = h("div", { hidden: true, style: { display: "grid", gap: "8px" } },
        h("hr", { style: { width: "100%", border: "0", borderTop: "1px solid var(--line)" } }),
        h("div", { style: { fontSize: "28px", fontWeight: "800" }, text: card.back }),
        card.example ? h("p", { class: "muted", text: card.example }) : null);
      const hint = card.hint ? h("button", { type: "button", class: "btn ghost sm", text: "Show a hint", onclick: (event) => { event.currentTarget.replaceWith(h("p", { class: "muted", text: `Hint: ${card.hint}` })); } }) : null;
      const grades = h("div", { hidden: true, class: "btn-row", style: { justifyContent: "center" } }, ...GRADE_BUTTONS.map((g) => h("button", { type: "button", class: `btn ${g.kind}`, style: { ...BIG, minWidth: "120px", flexDirection: "column", gap: "2px" }, "aria-keyshortcuts": g.key, onclick: () => grade(g.grade) }, h("span", { text: g.label }), h("span", { style: { fontSize: "12px", fontWeight: "500" }, text: `${g.hint} · ${g.key}` }))));
      const flip = h("button", { type: "button", class: "btn", style: { ...BIG, minWidth: "220px" }, text: "Show answer", "aria-keyshortcuts": "Space" });
      const reveal = () => { if (flipped) return; flipped = true; answer.hidden = false; grades.hidden = false; flip.hidden = true; const first = grades.querySelector("button:nth-child(3)"); if (first) first.focus(); };
      flip.addEventListener("click", reveal);
      const grade = async (value) => {
        if (!flipped) return;
        setKeys(null);
        try {
          const result = await api("/study/answer", { method: "POST", body: { card_id: card.id, grade: value, seconds: Math.round((Date.now() - started) / 1000) } });
          done += 1;
          const said = value === "again" ? "We'll try it again soon." : result.practice ? `Nice! It still comes back ${result.when.toLowerCase()}.` : `Nice! ${result.when}.`;
          Aira.toast(`${said}${result.xp_gained ? ` +${result.xp_gained} XP` : ""}`, value === "again" ? "" : "success", { timeout: 1800 });
          index += 1;
          if (value === "again") queue.push(card);
          show();
          celebrate(result); // after the next card, so a celebration keeps the focus
        } catch (error) { Aira.reportError(error); setKeys(handler); }
      };
      const handler = (event) => {
        if (typing(event) || event.ctrlKey || event.metaKey || event.altKey) return;
        if (document.querySelector("#overlay .modal, .drawer")) return; // a dialog is open: its keys are its own
        if (!location.hash.startsWith("#/learn")) { setKeys(null); return; }
        if ((event.key === " " || event.key === "Enter") && !flipped) { event.preventDefault(); reveal(); return; }
        const match = GRADE_BUTTONS.find((g) => g.key === event.key);
        if (match && flipped) { event.preventDefault(); grade(match.grade); }
      };
      setKeys(handler);
      stage.replaceChildren(
        progressBar(index, queue.length, "Session progress"),
        h("p", { class: "muted", text: `Card ${index + 1} of ${queue.length}${card.is_new ? " · new" : ""} · ${card.deck_title}${done ? ` · ${done} done this session` : ""}` }),
        h("section", { class: "card", style: { display: "grid", gap: "14px", justifyItems: "center", textAlign: "center", padding: "32px 18px", minHeight: "260px", alignContent: "center" } },
          h("div", { style: { fontSize: "32px", fontWeight: "800", lineHeight: "1.25", overflowWrap: "anywhere" }, text: card.front }),
          hint, answer, flip, grades));
      flip.focus();
    };
    await load();
  }

  // -------------------------------------------------------------------------- decks
  async function decks(body, rerender, route) {
    const data = await api("/decks");
    const deckCard = (deck) => h("article", { class: "life-card" },
      h("header", {}, h("span", { style: { fontSize: "30px" }, "aria-hidden": "true", text: deck.emoji }), h("div", {}, h("h2", { text: deck.title }), h("span", { class: "muted small", text: `${deck.mine ? (deck.shared ? "Shared with the household" : "Only you") : `Shared by ${deck.owner_name}`}${deck.mine && deck.price_cents ? ` · on sale for ${S.money(deck.price_cents)}` : ""}` }))),
      progressBar(deck.progress.learned, deck.progress.total, "Learned"),
      h("p", { class: "muted", text: `${deck.progress.learned} of ${deck.progress.total} learned · ${deck.progress.due} due · ${deck.progress.new} new` }),
      h("div", { class: "btn-row" },
        h("a", { class: "btn", href: `#/learn?tab=study&deck=${deck.id}`, text: "Study" }),
        h("a", { class: "btn secondary", href: `#/learn?tab=quizzes&deck=${deck.id}`, text: "Quiz" }),
        S.button("Open", () => openDeck(deck.id, rerender), { kind: "ghost" })));
    const query = (route && route.query) || {};
    const search = h("input", { type: "search", value: query.q || "", placeholder: "Search decks", "aria-label": "Search decks", autocomplete: "off" });
    const FILTERS = [["all", "All decks"], ["mine", "Made by me"], ["shared", "Shared with me"], ["due", "Cards due now"], ["sale", "On sale"]];
    const filter = Aira.ui.select(FILTERS, FILTERS.some(([id]) => id === query.show) ? query.show : "all", { "aria-label": "Show" });
    const results = h("div", { class: "suite-body" });
    const startersBox = h("div", { class: "suite-body" });
    // The cards and books that hold the words too (the search across the top lands here with ?q=).
    const more = h("div", { class: "suite-body" });
    let asked = 0;
    const findMore = async () => {
      const text = search.value.trim();
      const mine = ++asked;
      if (!text) { put(more); return; }
      let hits;
      try { hits = await searchAll(text, 50); } catch { return; }
      if (mine !== asked) return;
      put(more,
        hits.cards.length ? S.section(`Cards that match (${hits.cards.length}${hits.cards.length >= 50 ? "+" : ""})`, h("ul", { class: "life-lines ln-hits" }, ...hits.cards.map((card) => h("li", { class: "life-line" },
          h("span", { class: "ln-hit-icon", "aria-hidden": "true" }, Aira.icon("ln-card")),
          h("div", { class: "ln-grow" }, h("span", { class: "life-line-text", text: card.front }), h("span", { class: "muted life-line-sub", text: `${card.back} · ${card.deck_emoji} ${card.deck_title}` })),
          S.button("Open", () => openDeck(card.deck_id, rerender, { card: card.id }), { kind: "ghost sm" }))))) : null,
        hits.books.length ? S.section(`Books that match (${hits.books.length})`, h("ul", { class: "life-lines ln-hits" }, ...hits.books.map((book) => h("li", { class: "life-line" },
          h("span", { class: "ln-hit-icon", "aria-hidden": "true" }, Aira.icon("ln-book")),
          h("div", { class: "ln-grow" }, h("span", { class: "life-line-text", text: book.title }), h("span", { class: "muted life-line-sub", text: [book.author, book.status === "finished" ? "finished" : "reading"].filter(Boolean).join(" · ") })),
          h("a", { class: "btn ghost sm", href: `#/learn?tab=reading&book=${book.id}`, text: "Open" }))))) : null);
    };
    const matches = (text) => { const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean); const hay = text.toLowerCase(); return words.every((w) => hay.includes(w)); };
    const draw = () => {
      const wanted = filter.value;
      const list = data.decks.filter((d) => matches(`${d.title} ${d.description} ${d.owner_name || ""}`)
        && (wanted === "all" || (wanted === "mine" && d.mine) || (wanted === "shared" && !d.mine) || (wanted === "due" && d.progress.due + d.progress.new > 0) || (wanted === "sale" && d.mine && d.price_cents > 0)));
      const mine = list.filter((d) => d.mine);
      const shared = list.filter((d) => !d.mine);
      const starters = data.starters.filter((s) => matches(`${s.title} ${s.description}`));
      const filtering = search.value.trim() || wanted !== "all";
      put(results,
        !data.decks.length ? S.section("Your decks", Aira.ui.empty("No decks yet", "Add a starter deck below, make your own, or let Aira make one on any topic."))
          : filtering && !list.length ? S.section("Your decks", Aira.ui.empty("No decks match", "Try other words, or show all decks."), h("div", { class: "btn-row" }, S.button("Show all decks", () => { search.value = ""; filter.value = "all"; draw(); findMore(); }, { kind: "secondary" })))
            : null,
        mine.length ? S.section(`Your decks (${mine.length})`, h("div", { class: "life-cards" }, ...mine.map(deckCard))) : (!filtering && data.decks.length ? S.section("Your decks", Aira.ui.empty("No decks of your own yet", "Make one, or add a starter deck below.")) : null),
        shared.length ? S.section(`Shared by your household (${shared.length})`, h("div", { class: "life-cards" }, ...shared.map(deckCard))) : null,
      );
      // A starter you already added opens your copy instead of making another one.
      const copyOf = (starter) => data.decks.find((d) => d.mine && d.source === `starter:${starter.key}`);
      put(startersBox, wanted === "all" ? S.section("Starter decks", h("p", { class: "muted", text: "Ready to study. Adding one makes your own copy." }),
        starters.length ? h("div", { class: "life-tiles" }, ...starters.map((starter) => { const copy = copyOf(starter); return h("div", { class: "life-tile" },
          h("span", { style: { fontSize: "34px" }, "aria-hidden": "true", text: starter.emoji }),
          h("strong", { text: starter.title }),
          h("span", { class: "muted", text: `${starter.description} ${starter.cards} cards.` }),
          copy ? S.pill("added", { added: "good" }) : starter.for_you ? S.pill("for you", { "for you": "good" }) : null,
          copy ? S.button("Open", () => openDeck(copy.id, rerender), { kind: "ghost sm" })
            : S.button("Add", async () => { const added = await api("/starters", { method: "POST", body: { key: starter.key } }); forgetSearch(); Aira.toast(`${added.deck.title} added.`, "success"); rerender(); }, { kind: "secondary sm" })); }))
          : h("p", { class: "muted", text: "No starter deck matches. Try \"Make a deck on any topic\"." })) : null);
    };
    let timer = 0;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { draw(); findMore(); }, 150); });
    filter.addEventListener("change", draw);
    hooks.deck = () => newDeck(rerender);
    hooks.topic = () => generate(rerender);
    hooks.card = () => addCards(data.decks, rerender);
    hooks.open = (ask) => openDeck(ask.open, rerender, { card: ask.card });
    add(body,
      h("div", { class: "btn-row" },
        S.button("New deck", () => newDeck(rerender), { icon: "plus" }),
        S.button("Make a deck on any topic", () => generate(rerender), { kind: "secondary", icon: "ln-spark" })),
      data.decks.length || data.starters.length ? h("div", { class: "btn-row ln-filters" }, search, filter) : null,
      query.q ? h("p", { class: "muted ln-results-for" }, `Results for “${query.q}”. `, h("a", { href: "#/learn?tab=decks", text: "Show every deck" })) : null,
      results, more, startersBox);
    draw();
    findMore();
  }

  const deckFields = [
    { name: "title", label: "Title", required: true },
    { name: "emoji", label: "Picture", type: "select", options: ["📚", "🧠", "🌍", "🔢", "✏️", "🎵", "🧪", "🌱", "🏛️", "💬", "⭐", "🐾"] },
    { name: "description", label: "What it's about", type: "textarea", rows: 2 },
    { name: "shared", label: "Share with my household (everyone studies it with their own progress)", type: "checkbox" },
  ];

  const cardFields = [
    { name: "front", label: "Front (the question)", type: "textarea", rows: 2, required: true },
    { name: "back", label: "Back (the answer)", type: "textarea", rows: 2, required: true },
    { name: "hint", label: "Hint (optional)" },
    { name: "example", label: "Example (optional)", type: "textarea", rows: 2 },
  ];

  // "+ New" > Add cards: to the only deck you made, or the one you pick; then the deck opens at the new card.
  async function addCards(all, rerender) {
    const own = all.filter((d) => d.mine);
    if (!own.length) { Aira.toast("Make a deck first, then add cards to it.", "info"); newDeck(rerender); return; }
    const deckId = own.length === 1 ? own[0].id : await S.edit({ title: "Add cards to which deck?", fields: [{ name: "deck", label: "Deck", type: "select", options: own.map((d) => [d.id, `${d.emoji} ${d.title}`]) }], values: { deck: own[0].id }, submitLabel: "Next", onSave: async (v) => v.deck });
    const deck = own.find((d) => d.id === deckId);
    if (!deck) return;
    const made = await S.edit({ title: `Add a card to ${deck.title}`, fields: cardFields, submitLabel: "Add the card", onSave: async (values) => (await api(`/decks/${deck.id}/cards`, { method: "POST", body: values })).card });
    if (!made) return;
    forgetSearch();
    Aira.toast("Card added.", "success");
    await rerender();
    openDeck(deck.id, rerender, { card: made.id });
  }

  function newDeck(rerender) {
    S.edit({ title: "New deck", fields: deckFields, values: { emoji: "📚" }, onSave: async (values) => { const made = await api("/decks", { method: "POST", body: values }); rerender(); openDeck(made.deck.id, rerender); } });
  }

  function generate(rerender) {
    S.edit({
      title: "Make a deck on any topic",
      fields: [
        { name: "topic", label: "Topic", required: true, placeholder: "Birds in my garden, the Roman Empire, Italian for travel..." },
        { name: "age_group", label: "For", type: "select", options: [["adult", "Adults"], ["child", "Children"], ["teen", "Teens"], ["senior", "Older adults"]] },
        { name: "count", label: "Number of cards", type: "number", min: 5, max: 30 },
      ],
      values: { count: 12, age_group: (Aira.life && Aira.life.me && Aira.life.me.age_group) || "adult" },
      submitLabel: "Make it",
      onSave: async (values) => {
        const made = await api("/decks/generate", { method: "POST", body: values });
        Aira.toast(`${made.cards_added} cards made. ${made.note}`, "success", { timeout: 7000 });
        rerender();
        openDeck(made.deck.id, rerender);
      },
    });
  }

  async function openDeck(deckId, rerender, { card: cardId = "" } = {}) {
    const deck = (await api(`/decks/${deckId}`)).deck;
    const reload = () => { forgetSearch(); Aira.closeDrawer(); openDeck(deckId, rerender); rerender(); };
    const rows = deck.cards.map((card) => h("li", { class: "life-line", "data-card": card.id, "data-text": `${card.front} ${card.back} ${card.hint || ""}`.toLowerCase(), style: { borderBottom: "1px solid var(--line)", paddingBottom: "8px" } },
      h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: card.front }), h("span", { class: "muted life-line-sub", text: card.back })),
      deck.mine ? h("div", { class: "btn-row" },
        S.button("Edit", () => S.edit({ title: "Edit card", fields: cardFields, values: card, onSave: async (values) => { await api(`/cards/${card.id}`, { method: "PATCH", body: values }); reload(); } }), { kind: "ghost sm" }),
        S.button("Delete", async () => { if (await S.confirmDelete("this card")) { await api(`/cards/${card.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" })) : null));
    const list = deck.cards.length ? h("ul", { class: "life-lines" }, ...rows) : null;
    const none = h("p", { class: "muted", hidden: true, text: "No card matches." });
    const cardSearch = deck.cards.length > 8 ? h("input", { type: "search", placeholder: "Search cards", "aria-label": "Search cards", autocomplete: "off", oninput: (event) => {
      const words = event.target.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      for (const row of list.children) { const hit = words.every((w) => row.dataset.text.includes(w)); row.hidden = !hit; shown += hit ? 1 : 0; }
      none.hidden = shown > 0;
    } }) : null;
    Aira.drawer({
      title: `${deck.emoji} ${deck.title}`,
      subtitle: deck.mine ? (deck.shared ? "Shared with the household" : "Only you") : `Shared by ${deck.owner_name}`,
      body: h("div", { class: "suite-body" },
        deck.description ? h("p", { text: deck.description }) : null,
        h("div", { class: "btn-row" },
          h("a", { class: "btn", href: `#/learn?tab=study&deck=${deck.id}`, onclick: () => Aira.closeDrawer(), text: "Study" }),
          deck.mine ? S.button("Add a card", () => S.edit({ title: "Add a card", fields: cardFields, onSave: async (values) => { await api(`/decks/${deck.id}/cards`, { method: "POST", body: values }); reload(); } }), { kind: "secondary" }) : null,
          deck.mine ? S.button("Edit deck", () => S.edit({ title: "Edit deck", fields: deckFields, values: deck, onSave: async (values) => { await api(`/decks/${deck.id}`, { method: "PATCH", body: values }); reload(); } }), { kind: "ghost" }) : null,
          deck.mine ? S.button("Add many cards", () => importCards(deck, reload), { kind: "ghost" }) : null,
          deck.can_copy ? S.button("Make my own copy", async () => { const made = await api(`/decks/${deck.id}/copy`, { method: "POST", body: {} }); Aira.toast("Your copy is ready to change.", "success"); Aira.closeDrawer(); rerender(); openDeck(made.deck.id, rerender); }, { kind: "secondary" }) : null,
          deck.cards.length ? S.button("Download CSV", () => Aira.download(`/api/learn/decks/${deck.id}/export`, `${deck.title.replace(/[^\w -]+/g, "").trim() || "deck"}.csv`), { kind: "ghost" }) : null,
          deck.mine ? S.button("Delete deck", async () => { if (await S.confirmDelete(deck.title)) { await api(`/decks/${deck.id}`, { method: "DELETE" }); Aira.closeDrawer(); rerender(); } }, { kind: "ghost" }) : null),
        deck.can_sell ? sellCard(deck, reload) : null,
        !deck.mine && !deck.can_copy ? h("p", { class: "muted", text: `${deck.owner_name} sells this deck, so it cannot be copied. You can still study it here.` }) : null,
        deck.can_share_publicly ? S.share({
          title: "A page to study online",
          // A deck on sale keeps its page: buyers pay there and study there with their code.
          text: deck.price_cents ? "This is your deck's sale page: people try the free cards and buy it here, and buyers study here with their code. It stays on while the deck is on sale." : "Anyone with the link can flip through these cards in their browser (nothing they do is saved). Good for grandparents and cousins.",
          link: deck.link,
          onCreate: async () => (await api(`/decks/${deck.id}/link`, { method: "POST", body: {} })).link,
          onRevoke: deck.price_cents ? undefined : async () => { await api(`/decks/${deck.id}/link`, { method: "DELETE" }); },
        }) : null,
        S.section(`${deck.cards.length} card${deck.cards.length === 1 ? "" : "s"}`, cardSearch, list || Aira.ui.empty("No cards yet", deck.mine ? "Add your first card, or add many at once from a list." : "The owner has not added cards yet."), none)),
    });
    if (cardId && list) pick(list.querySelector(`[data-card="${CSS.escape(cardId)}"]`));
  }

  function importCards(deck, reload) {
    S.edit({
      title: `Add many cards to ${deck.title}`,
      wide: true,
      submitLabel: "Add the cards",
      fields: [
        { name: "text", label: "One card per line: the front, then the back", type: "textarea", rows: 10, required: true, placeholder: "Hola = Hello\nGracias = Thank you\nAdiós = Goodbye",
          hint: "Split the front and back with =, a dash, a comma, a semicolon or a tab. You can paste from a spreadsheet, or a CSV file with the columns front, back, hint, example. Cards already in the deck are skipped." },
      ],
      onSave: async (values) => {
        const result = await api(`/decks/${deck.id}/import`, { method: "POST", body: { text: values.text } });
        if (!result.added && result.problems.length) throw new Error(`No cards were added. Line ${result.problems[0].line}: ${result.problems[0].reason}`);
        const parts = [`${result.added} card${result.added === 1 ? "" : "s"} added`];
        if (result.skipped) parts.push(`${result.skipped} already in the deck`);
        if (result.problems.length) parts.push(`${result.problems.length} line${result.problems.length === 1 ? "" : "s"} could not be read (line ${result.problems.map((p) => p.line).filter(Boolean).slice(0, 5).join(", ")})`);
        Aira.toast(`${parts.join(", ")}.`, result.added ? "success" : "", { timeout: 7000 });
        reload();
      },
    });
  }

  // How buyers pay the seller: their own link or instructions (locally the business profile's can stand in;
  // hosted, a member is paid directly, never through the organization's link).
  const paymentFields = (payment) => {
    const fallback = payment && !payment.hosted && (payment.business_link || payment.business_instructions);
    return [
      { type: "heading", label: "How buyers pay you" },
      { name: "payment_link", label: "Your payment link", type: "url", placeholder: "https://...", hint: payment && payment.hosted ? "Your own link (PayPal, Stripe, UPI...). Buyers pay you directly, not your organization." : "A link where people can pay you (PayPal, Stripe, UPI...)." },
      { name: "payment_instructions", label: "Or how to pay you", type: "textarea", rows: 2, hint: fallback ? "Leave both empty to use the payment link in your business profile." : "For example your bank details. Buyers see this with a reference to pay with." },
    ];
  };
  const paymentValues = (payment) => ({ payment_link: (payment && payment.payment_link) || "", payment_instructions: (payment && payment.payment_instructions) || "" });
  const canBePaid = (payment, values) => !!(values.payment_link || values.payment_instructions || (payment && !payment.hosted && (payment.business_link || payment.business_instructions)));

  // Selling a deck: the price, the free preview, how buyers pay, the page link and what has sold.
  function sellCard(deck, reload) {
    const money = (cents) => S.money(cents, deck.currency);
    const setPrice = () => S.edit({
      title: deck.price_cents ? "Change the price" : "Sell this deck",
      fields: [
        { name: "price_cents", label: `Price (${deck.currency || "USD"})`, type: "money", required: true, hint: "What a buyer pays once to study every card. Enter 0 to make the page free again." },
        { name: "preview_count", label: "Free cards to try first", type: "number", min: 1, max: 20, hint: "Anyone with the link can study these before buying." },
        ...paymentFields(deck.payment),
      ],
      values: { price_cents: deck.price_cents || 299, preview_count: deck.preview_count || 5, ...paymentValues(deck.payment) },
      submitLabel: "Save",
      onSave: async (values) => {
        if (values.price_cents === null) throw new Error("Enter the price as a number, like 4.99.");
        if (values.price_cents && !canBePaid(deck.payment, values)) throw new Error("Add your payment link or how to pay you, so buyers can pay.");
        await api(`/decks/${deck.id}/price`, { method: "POST", body: values });
        Aira.toast(values.price_cents ? "Your deck is on sale. Share the link with people who would like it." : "The page is free again.", "success", { timeout: 6000 });
        reload();
      },
    });
    if (!deck.price_cents) {
      return h("div", { class: "suite-share" }, h("div", {}, h("strong", { text: "Earn from this deck" }), h("p", { class: "muted", text: "Put a price on it. People try a few cards for free, pay you through your own payment link, and get an access code to study the rest." })),
        S.button("Sell this deck", setPrice, { kind: "secondary" }));
    }
    return h("div", { class: "suite-share" },
      h("div", {}, h("strong", { text: `On sale for ${money(deck.price_cents)}` }), h("p", { class: "muted", text: `${deck.preview_count} free card${deck.preview_count === 1 ? "" : "s"} to try · ${deck.sold || 0} sold · Buyers' requests are under Earn.` })),
      h("div", { class: "btn-row" },
        S.button("Change price", setPrice, { kind: "secondary sm" }),
        h("a", { class: "btn ghost sm", href: "#/learn?tab=earn", onclick: () => Aira.closeDrawer(), text: "Open Earn" }),
        S.button("Stop selling", async () => {
          if (!(await Aira.confirm({ title: "Stop selling this deck", message: "The page shows every card for free again. Requests still waiting for payment are closed. People who bought it keep their access.", confirmLabel: "Stop selling" }))) return;
          await api(`/decks/${deck.id}/price`, { method: "POST", body: { price_cents: 0 } });
          reload();
        }, { kind: "ghost sm" })));
  }

  // -------------------------------------------------------------------------- quizzes
  async function quizzes(body, route) {
    const [deckData, history] = await Promise.all([api("/decks"), api("/quizzes")]);
    const usable = deckData.decks.filter((d) => d.progress.total >= 2);
    const deckSelect = Aira.ui.select(usable.map((d) => [d.id, `${d.emoji} ${d.title}`]), route.query.deck || (usable[0] && usable[0].id) || "", { "aria-label": "Deck" });
    const countSelect = Aira.ui.select([["5", "5 questions"], ["10", "10 questions"], ["20", "20 questions"]], "10", { "aria-label": "Questions" });
    const stage = h("div", { class: "suite-body" });
    const start = async () => {
      const quiz = (await api("/quizzes", { method: "POST", body: { deck_id: deckSelect.value, count: Number(countSelect.value) } })).quiz;
      runQuiz(stage, quiz, async (answers) => api(`/quizzes/${quiz.id}/submit`, { method: "POST", body: { answers } }));
    };
    add(body,
      usable.length ? S.section("Quiz yourself", h("div", { class: "btn-row" }, Aira.ui.field("Deck", deckSelect), Aira.ui.field("Length", countSelect)), h("div", { class: "btn-row" }, S.button("Start the quiz", start)))
        : S.section("", Aira.ui.empty("No decks to quiz yet", "Add a deck with at least two cards first."), h("div", { class: "btn-row", style: { justifyContent: "center" } }, h("a", { class: "btn", href: "#/learn?tab=decks", text: "Choose a deck" }), h("a", { class: "btn secondary", href: "#/learn?tab=maths", text: "Try maths practice" }))),
      stage,
      history.history.length ? S.section("Your results", S.table({ columns: [{ key: "title", label: "Quiz" }, { key: "score", label: "Score", render: (r) => `${r.score} of ${r.total}` }, { key: "finished_at", label: "When", render: (r) => Aira.fmt.ago(r.finished_at) }], rows: history.history })) : null,
    );
  }

  function runQuiz(stage, quiz, submit) {
    const chosen = new Array(quiz.questions.length).fill(null);
    const items = quiz.questions.map((question, qIndex) => {
      const buttons = question.options.map((option, oIndex) => h("button", { type: "button", class: "btn secondary", role: "radio", "aria-checked": "false", style: { justifyContent: "flex-start", textAlign: "left", whiteSpace: "normal", minHeight: "48px" }, text: option, onclick: (event) => {
        chosen[qIndex] = oIndex;
        for (const other of event.currentTarget.parentNode.children) { other.setAttribute("aria-checked", "false"); other.classList.add("secondary"); }
        event.currentTarget.setAttribute("aria-checked", "true");
        event.currentTarget.classList.remove("secondary");
      } }));
      return h("section", { class: "card", style: { display: "grid", gap: "10px" } }, h("strong", { style: { fontSize: "18px" }, text: `${qIndex + 1}. ${question.prompt}` }), h("div", { role: "radiogroup", "aria-label": question.prompt, style: { display: "grid", gap: "8px" } }, ...buttons));
    });
    const hand = S.button("Hand in", async () => {
      if (chosen.includes(null) && !(await Aira.confirm({ title: "Some questions have no answer", message: "Hand in anyway? Unanswered questions count as wrong.", confirmLabel: "Hand in" }))) return;
      const result = await submit(chosen);
      showResults(stage, result, (r) => (r.your === null ? "No answer" : r.options ? r.options[r.your] : r.your));
    });
    stage.replaceChildren(h("h2", { class: "suite-h2", text: quiz.title }), ...items, h("div", { class: "btn-row" }, hand));
    stage.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showResults(stage, result, yours) {
    celebrate(result);
    stage.replaceChildren(
      S.section(`${result.score} of ${result.total}`, h("p", { style: { fontSize: "20px", fontWeight: "700" }, text: result.message }), h("p", { class: "muted", text: `+${result.xp_gained} XP` }),
        h("ul", { class: "life-lines" }, ...result.results.map((r) => h("li", { class: "life-line" },
          h("span", { class: `life-check ${r.right ? "done" : ""}`, "aria-hidden": "true" }, h("span", { text: r.right ? "✓" : "✗" })),
          h("div", {}, h("span", { class: "life-line-text", text: r.prompt }), h("span", { class: "muted life-line-sub", text: r.right ? `Right: ${r.answer}` : `You said ${yours(r)}. The answer is ${r.answer}.` })))))),
    );
    if (Aira.life && (Aira.life.prefs.read_aloud || Aira.life.prefs.easy_mode)) Aira.life.speak(`${result.score} of ${result.total}. ${result.message}`);
  }

  // -------------------------------------------------------------------------- maths
  async function maths(body) {
    const levels = (await api("/quizzes")).levels;
    const stage = h("div", { class: "suite-body" });
    let mode = "relaxed";
    const modeRow = h("div", { class: "life-sizes", role: "radiogroup", "aria-label": "Mode" }, ...[["relaxed", "Relaxed", "No clock"], ["timed", "Timed", "Beat the clock"]].map(([value, label, sub]) => h("button", { type: "button", role: "radio", class: "life-size", "aria-checked": String(value === mode), onclick: (event) => { mode = value; for (const b of modeRow.children) b.setAttribute("aria-checked", "false"); event.currentTarget.setAttribute("aria-checked", "true"); } }, h("strong", { text: label }), h("span", { class: "muted small", text: sub }))));
    const start = async (level) => {
      const quiz = (await api("/maths", { method: "POST", body: { level, mode, count: 10 } })).quiz;
      const inputs = quiz.questions.map((q, index) => h("input", { type: "text", inputmode: level === "fractions" ? "text" : "numeric", autocomplete: "off", "aria-label": `Answer ${index + 1}: ${q.prompt}`, style: { fontSize: "22px", maxWidth: "160px" } }));
      const timer = h("p", { class: "muted", role: "timer", "aria-live": "off" });
      let left = quiz.time_limit;
      let interval = 0;
      const send = async () => {
        clearInterval(interval);
        const result = await api(`/maths/${quiz.id}/submit`, { method: "POST", body: { answers: inputs.map((i) => i.value) } });
        showResults(stage, result, (r) => (r.your === "" ? "nothing" : r.your));
      };
      if (left) {
        timer.textContent = `${left} seconds`;
        interval = setInterval(() => { left -= 1; timer.textContent = left > 0 ? `${left} seconds` : "Time's up!"; if (!location.hash.startsWith("#/learn")) clearInterval(interval); if (left <= 0) { clearInterval(interval); send(); } }, 1000);
      }
      put(stage,
        h("h2", { class: "suite-h2", text: quiz.title }), left ? timer : null,
        h("div", { style: { display: "grid", gap: "12px" } }, ...quiz.questions.map((q, index) => h("label", { style: { display: "flex", alignItems: "center", gap: "14px", fontSize: "24px", fontWeight: "700" } }, h("span", { style: { minWidth: "150px" }, text: `${q.prompt} =` }), inputs[index]))),
        h("p", { class: "muted", text: level === "fractions" ? "Write fractions like 3/4 or 1 1/4." : "Type a number in each box." }),
        h("div", { class: "btn-row" }, S.button("Check my answers", send)));
      inputs[0].focus();
      inputs.forEach((input, index) => input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); if (inputs[index + 1]) inputs[index + 1].focus(); else send(); } }));
    };
    body.append(
      S.section("Maths practice", h("p", { class: "muted", text: "Pick a level. Relaxed has no clock; timed gives a little time for each question." }), modeRow,
        h("div", { class: "life-tiles" }, ...Object.entries(levels).map(([id, label]) => h("button", { type: "button", class: "life-tile", style: { cursor: "pointer", font: "inherit", textAlign: "left" }, onclick: () => start(id) }, h("strong", { text: label }), h("span", { class: "muted", text: { add20: "For starters", times: "2 to 12", division: "Sharing equally", fractions: "Same bottom number", percent: "Everyday maths" }[id] || "" }))))),
      stage,
    );
  }

  // -------------------------------------------------------------------------- reading
  const bookFields = [{ name: "title", label: "Title", required: true }, { name: "author", label: "Author" }];
  const addBook = (rerender) => S.edit({ title: "Add a book", fields: bookFields, onSave: async (v) => { await api("/books", { method: "POST", body: v }); forgetSearch(); rerender(); } });

  async function reading(body, rerender) {
    const data = await api("/books");
    const books = data.books;
    const reading = books.filter((b) => b.status === "reading");
    // The book being read (the newest one) is the usual answer to "what did you read?".
    const bookSelect = Aira.ui.select([["", "No particular book"], ...reading.map((b) => [b.id, b.title])], reading.length ? reading[0].id : "", { "aria-label": "Book" });
    const bookActions = (b) => h("div", { class: "btn-row ln-book-actions" },
      b.status === "finished" ? S.pill("finished", { finished: "good" }) : S.button("Finished it!", async () => { await api(`/books/${b.id}`, { method: "PATCH", body: { finished: true } }); Aira.toast("Well done! 📖", "success"); rerender(); }, { kind: "secondary sm" }),
      b.status === "finished" ? S.button("Reading it again", async () => { await api(`/books/${b.id}`, { method: "PATCH", body: { finished: false } }); rerender(); }, { kind: "ghost sm" }) : null,
      S.button("Edit", () => S.edit({ title: "Edit book", fields: bookFields, values: b, onSave: async (v) => { await api(`/books/${b.id}`, { method: "PATCH", body: v }); rerender(); } }), { kind: "ghost sm" }),
      S.button("Delete", async () => { if (await S.confirmDelete(b.title)) { await api(`/books/${b.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }));
    const log = async (minutes, bookId = bookSelect.value) => { const result = await api("/reading", { method: "POST", body: { minutes, book_id: bookId } }); Aira.toast(`${minutes} minutes logged. +${result.xp_gained} XP`, "success"); celebrate(result); rerender(); };
    // Any number of minutes, for the book chosen above (or another one being read).
    const logOther = () => S.edit({
      title: "Minutes read",
      fields: [{ name: "minutes", label: "Minutes", type: "number", min: 1, max: 600, required: true }, ...(reading.length ? [{ name: "book_id", label: "Book", type: "select", options: [["", "No particular book"], ...reading.map((b) => [b.id, b.title])] }] : [])],
      values: { book_id: bookSelect.value },
      submitLabel: "Log it",
      onSave: async (v) => log(Number(v.minutes), v.book_id || ""),
    });
    hooks.book = () => addBook(rerender);
    hooks.minutes = logOther;
    body.append(
      S.kpis([{ label: "Read today", value: `${data.minutes_today} min` }, { label: "This week", value: `${data.minutes_week} min` }, { label: "Books", value: books.length }]),
      S.section("I read for...", Aira.ui.field("Book", bookSelect),
        h("div", { class: "btn-row" }, ...[10, 20, 30].map((m) => S.button(`${m} minutes`, () => log(m), { kind: "secondary" })),
          S.button("Other", logOther, { kind: "ghost" }))),
      S.section("My books", h("div", { class: "btn-row" }, S.button("Add a book", () => addBook(rerender), { kind: "secondary", icon: "plus" })),
        books.length ? h("ul", { class: "life-lines ln-books" }, ...books.map((b) => h("li", { class: "life-line", "data-book": b.id },
          h("div", { class: "ln-grow" }, h("span", { class: "life-line-text", text: b.title }), h("span", { class: "muted life-line-sub", text: `${b.author ? `${b.author} · ` : ""}${b.minutes} minute${b.minutes === 1 ? "" : "s"} read${b.status === "finished" && b.finished_at ? ` · finished ${S.day(b.finished_at)}` : ""}` })),
          bookActions(b))))
          : Aira.ui.empty("No books yet", "Add the one you are reading, then log the minutes you read.")),
    );
    hooks.pickBook = (id) => pick(body.querySelector(`[data-book="${CSS.escape(id)}"]`));
  }

  // -------------------------------------------------------------------------- leaderboard
  async function leaderboard(body) {
    const data = await api("/leaderboard");
    body.append(S.section("This week in the family",
      h("ol", { class: "life-people" }, ...data.people.map((p, index) => h("li", { class: "life-person", style: p.profile_id === data.me ? { background: "var(--brand-soft)", borderRadius: "12px", padding: "6px" } : {} },
        h("strong", { style: { width: "28px", fontSize: "20px" }, text: index < 3 && p.xp_week ? ["🥇", "🥈", "🥉"][index] : String(index + 1) }),
        h("span", { class: "life-face-small", style: { background: p.color }, "aria-hidden": "true", text: p.emoji }),
        h("div", {}, h("strong", { text: p.name }), h("div", { class: "muted small", text: `Level ${p.level} · 🔥 ${p.streak} · ${p.cards_week} cards` })),
        h("strong", { style: { fontSize: "18px", marginLeft: "auto" }, text: `${p.xp_week} XP` })))),
      // Alone on the board: say how the others join (the people on this device are set up under Display and reading).
      data.people.length === 1 && !Aira.cloud ? h("div", { class: "ln-alone" }, h("p", { class: "muted", text: "Only you so far. Add the people you live with, and each gets their own decks, streak and place here." }),
        h("a", { class: "btn secondary sm", href: "#/display", text: "Add people" })) : null));
  }

  // -------------------------------------------------------------------------- progress
  const level = (cards) => (cards <= 0 ? 0 : cards < 5 ? 1 : cards < 15 ? 2 : cards < 30 ? 3 : 4);

  async function progressTab(body) {
    const data = await api("/stats");
    const t = data.totals;
    const weeks = [];
    for (let i = 0; i < data.calendar.length; i += 7) weeks.push(data.calendar.slice(i, i + 7));
    const cell = (day) => {
      const label = `${S.day(day.day)}: ${day.cards} card${day.cards === 1 ? "" : "s"}, ${day.minutes} minute${day.minutes === 1 ? "" : "s"}${day.goal_met ? ", goal met" : day.frozen ? ", kept by a streak freeze" : ""}`;
      return h("span", { class: `ln-cal-day ln-l${level(day.cards + Math.floor(day.minutes / 2))}${day.goal_met ? " ln-goal" : ""}${day.frozen ? " ln-frozen" : ""}${day.day === data.today ? " ln-today" : ""}`, role: "img", "aria-label": label, title: label }, day.goal_met ? h("span", { "aria-hidden": "true", text: "✓" }) : day.frozen ? h("span", { "aria-hidden": "true", text: "❄" }) : null);
    };
    const studied = t.cards > 0 || data.calendar.some((d) => d.minutes > 0);
    const dayName = (iso, index) => (index === 0 ? "Today" : index === 1 ? "Tomorrow" : new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short" }));
    add(body,
      S.kpis([
        { label: "Streak", value: `🔥 ${t.streak} day${t.streak === 1 ? "" : "s"}`, hint: `Best: ${t.best_streak} day${t.best_streak === 1 ? "" : "s"}`, tone: t.streak ? "good" : "" },
        { label: "You remember", value: data.retention === null ? "-" : `${data.retention}%`, hint: data.retention === null ? "After a few reviews" : `of ${data.retention_reviews} reviews in 30 days`, tone: data.retention === null ? "" : data.retention >= 80 ? "good" : data.retention < 60 ? "warn" : "" },
        { label: "This week", value: `${t.cards_week} cards`, hint: `${t.minutes_week} minutes` },
        { label: "Cards studied", value: t.cards, hint: `${t.days} day${t.days === 1 ? "" : "s"} practised · ${t.mastered} known well` },
      ]),
      S.section("The last 12 weeks",
        studied ? null : h("p", { class: "muted", text: "Each day you practise fills a square. Study a few cards to start your calendar." }),
        h("div", { class: "ln-cal", role: "group", "aria-label": "Practice in the last 12 weeks, one column a week" }, ...weeks.map((week) => h("div", { class: "ln-cal-week" }, ...week.map(cell)))),
        h("div", { class: "ln-cal-legend muted small" }, h("span", { text: "Less" }), ...[0, 1, 2, 3, 4].map((n) => h("span", { class: `ln-cal-day ln-l${n}`, "aria-hidden": "true" })), h("span", { text: "More · ✓ goal met · ❄ streak freeze" }))),
      S.section("Coming up", h("p", { class: "muted", text: "Cards that come back for review each day." }),
        data.forecast.some((d) => d.cards) ? S.bars(data.forecast.map((d, i) => ({ label: dayName(d.day, i), value: d.cards })), { format: (v) => `${v} card${v === 1 ? "" : "s"}` }) : Aira.ui.empty("Nothing to review yet", "Cards you study come back here on the day they are due.")),
      S.section("Your hardest cards",
        data.hardest.length ? h("ul", { class: "life-lines" }, ...data.hardest.map((c) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: c.front }), h("span", { class: "muted life-line-sub", text: `${c.back} · ${c.deck_title} · missed ${c.lapses} time${c.lapses === 1 ? "" : "s"}` })))))
          : Aira.ui.empty("No hard cards yet", "Cards you miss show up here."),
        data.hardest.length ? h("div", { class: "btn-row" }, h("a", { class: "btn", href: "#/learn?tab=study&mode=weak", text: "Practise these now" })) : null),
      h("div", { class: "btn-row" }, S.button("Download my learning log (CSV)", () => Aira.download("/api/learn/stats?format=csv", "learning-log.csv"), { kind: "secondary" })),
    );
  }

  // -------------------------------------------------------------------------- earn
  async function earn(body, rerender, route) {
    const query = (route && route.query) || {};
    const params = new URLSearchParams();
    if (query.status) params.set("status", query.status);
    if (query.q) params.set("q", query.q);
    const data = await api(`/sales${params.toString() ? `?${params}` : ""}`);
    const sum = data.summary;
    const money = (cents) => S.money(cents, sum.currency);
    const mineData = await api("/decks");
    const sellable = mineData.decks.filter((d) => d.mine && !d.price_cents && !d.copy_of_other);
    const go = (changes) => Aira.router.go(["learn"], { tab: "earn", ...Object.fromEntries(Object.entries({ status: query.status || "", q: query.q || "", ...changes }).filter(([, v]) => v)) });
    // The code, and the email ready to send from the seller's own mail (hosted, the organization's outbox is not
    // theirs; locally it is a way round a mail server that is not set up yet).
    const showCode = (result) => {
      const mail = result.message || {};
      const mailto = mail.to ? `mailto:${encodeURIComponent(mail.to)}?subject=${encodeURIComponent(mail.subject || "")}&body=${encodeURIComponent(mail.body || "")}` : "";
      const sending = result.email === "queued"
        ? (result.email_status === "approved" ? `An email with the code and the link is on its way to ${result.sale.buyer_email}.` : `An email with the code and the link to ${result.sale.buyer_email} is waiting in the Business hub outbox: approve it there to send it, or send it yourself below.`)
        : `Send ${result.sale.buyer_email} the code and the link: press Email the buyer to open the email, ready to send from your own mail.`;
      Aira.modal({
        title: "Payment confirmed",
        body: h("div", { class: "suite-body" },
          h("p", { text: `${result.sale.buyer_name} can now study every card of ${result.sale.deck_title}.` }),
          h("p", {}, "Their access code: ", h("strong", { class: "ln-code", text: result.code })),
          h("p", { class: "muted", text: sending }),
          mailto ? h("div", { class: "btn-row" }, h("a", { class: "btn secondary", href: mailto, text: "Email the buyer" }), S.button("Copy the email", () => S.copy(`${mail.subject}\n\n${mail.body}`), { kind: "ghost" })) : null),
        actions: [{ label: "Copy the code", kind: "secondary", onClick: () => { S.copy(result.code); return false; } }, { label: "Done", kind: "primary" }],
      });
    };
    const payment = sum.payment || {};
    const editPayment = () => S.edit({
      title: "How buyers pay you",
      fields: paymentFields(payment).slice(1),
      values: paymentValues(payment),
      onSave: async (values) => { await api("/seller", { method: "POST", body: values }); Aira.toast("Saved. Buyers see this on your decks' pages.", "success"); rerender(); },
    });
    const act = async (sale, what) => {
      if (what === "paid") { const result = await api(`/sales/${sale.id}/paid`, { method: "POST", body: {} }); showCode(result); rerender(); return; }
      if (what === "resend") {
        if (!(await Aira.confirm({ title: "Send a new code", message: `${sale.buyer_name} gets a new access code by email. The old code stops working.`, confirmLabel: "Send a new code" }))) return;
        showCode(await api(`/sales/${sale.id}/paid`, { method: "POST", body: {} })); rerender(); return;
      }
      if (what === "unpaid") {
        if (!(await Aira.confirm({ title: "Mark as not paid", message: "The money comes off your earnings and the buyer's access code stops working.", confirmLabel: "Mark as not paid", danger: true }))) return;
        await api(`/sales/${sale.id}/unpaid`, { method: "POST", body: {} }); rerender(); return;
      }
      if (!(await Aira.confirm({ title: "Cancel this request", message: `${sale.buyer_name}'s request to buy ${sale.deck_title} will be closed.`, confirmLabel: "Cancel the request", danger: true }))) return;
      await api(`/sales/${sale.id}/cancel`, { method: "POST", body: {} }); rerender();
    };
    const search = h("input", { type: "search", value: query.q || "", placeholder: "Search buyers or decks", "aria-label": "Search sales", autocomplete: "off" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") go({ q: search.value.trim() }); });
    search.addEventListener("search", () => go({ q: search.value.trim() }));
    const status = Aira.ui.select([["", "Every status"], ["pending", "Waiting for payment"], ["paid", "Paid"], ["cancelled", "Cancelled"]], query.status || "", { "aria-label": "Status" });
    status.addEventListener("change", () => go({ status: status.value }));
    const STATUS_WORDS = { pending: "waiting", paid: "paid", cancelled: "cancelled" };
    const rows = data.sales.map((sale) => h("li", { class: "life-line ln-sale" },
      h("div", { class: "ln-grow" },
        h("span", { class: "life-line-text", text: `${sale.buyer_name} · ${sale.deck_title}` }),
        h("span", { class: "muted life-line-sub", text: `${money(sale.amount_cents)} · ref ${sale.reference} · ${sale.buyer_email} · ${sale.status === "paid" ? `paid ${S.day(sale.paid_at)}` : `asked ${Aira.fmt.ago(sale.created_at)}`}` })),
      h("div", { class: "btn-row" }, S.pill(STATUS_WORDS[sale.status] || sale.status, { waiting: "warn", paid: "good", cancelled: "" }),
        sale.status === "pending" ? S.button("Payment received", () => act(sale, "paid"), { kind: "sm" }) : null,
        sale.status === "pending" ? S.button("Cancel", () => act(sale, "cancel"), { kind: "ghost sm" }) : null,
        sale.status === "paid" ? S.button("Send a new code", () => act(sale, "resend"), { kind: "ghost sm" }) : null,
        sale.status === "paid" ? S.button("Not paid", () => act(sale, "unpaid"), { kind: "ghost sm" }) : null)));
    add(body,
      S.kpis([
        { label: "Earned this month", value: money(sum.month_cents), tone: sum.month_cents ? "good" : "" },
        { label: "Earned in all", value: money(sum.earned_cents), hint: `${sum.sold} sold` },
        { label: "Waiting for payment", value: sum.waiting, hint: sum.waiting ? money(sum.waiting_cents) : "", tone: sum.waiting ? "warn" : "" },
        { label: "Decks on sale", value: sum.decks.length },
      ]),
      sum.payment_ready ? null : h("div", { class: "card suite-section ln-notice", role: "note" }, h("strong", { text: "Tell buyers how to pay you" }), h("p", { class: "muted", text: "Add your payment link or bank details. Buyers see it on your deck's page, with a reference to pay with." }), h("div", { class: "btn-row" }, S.button("Add how buyers pay you", editPayment, { kind: "secondary" }))),
      sum.payment_ready ? S.section("How buyers pay you",
        h("p", { class: "muted", text: payment.using === "business" ? `With the payment link in your business profile${payment.business_link ? `: ${payment.business_link}` : "."}` : [payment.payment_link, payment.payment_instructions].filter(Boolean).join(" · ") }),
        h("div", { class: "btn-row" }, S.button(payment.using === "business" ? "Use my own instead" : "Change", editPayment, { kind: "ghost sm" }))) : null,
      S.section("How earning works",
        h("ol", { class: "ln-steps" },
          h("li", { text: "Put a price on a deck you made (open it under Decks, then Sell this deck)." }),
          h("li", { text: "Share its link. People try a few cards for free and ask to buy it." }),
          h("li", { text: "They pay you through your own payment link. When the money arrives, press Payment received." }),
          h("li", { text: sum.hosted ? "You get their access code and an email ready to send them. The money is yours: it is not counted in your organization's revenue." : "Aira emails them an access code that opens every card. Your earnings show here and in the Business hub." })),
        sellable.length ? h("div", { class: "btn-row" }, S.button("Sell one of my decks", async () => {
          const picked = await S.edit({ title: "Which deck?", fields: [{ name: "deck", label: "Deck", type: "select", options: sellable.map((d) => [d.id, `${d.emoji} ${d.title}`]) }], values: { deck: sellable[0].id }, submitLabel: "Next", onSave: async (v) => v.deck });
          if (picked) await openDeck(picked, rerender); // after the picker has closed
        }, { kind: "secondary" })) : null),
      S.section("Decks on sale", sum.decks.length ? h("ul", { class: "life-lines ln-for-sale" }, ...sum.decks.map((d) => h("li", { class: "life-line" },
        h("div", { class: "ln-grow" }, h("span", { class: "life-line-text", text: `${d.emoji} ${d.title}` }), h("span", { class: "muted life-line-sub", text: `${money(d.price_cents)} · ${d.card_count} cards, ${d.preview_count} free · ${d.sold} sold · ${money(d.earned)} earned` })),
        h("div", { class: "btn-row" },
          d.link ? S.button("Copy link", () => S.copy(S.absolute(d.link.path)), { kind: "secondary sm" }) : null,
          S.button("Open", () => openDeck(d.id, rerender), { kind: "ghost sm" })))))
        : Aira.ui.empty("No decks on sale yet", "Open a deck you made and choose Sell this deck.")),
      S.section("Buyers", h("div", { class: "btn-row ln-filters" }, search, status, S.button("Download CSV", () => { const wanted = new URLSearchParams({ format: "csv" }); if (query.status) wanted.set("status", query.status); if (query.q) wanted.set("q", query.q); Aira.download(`/api/learn/sales?${wanted}`, "deck-sales.csv"); }, { kind: "secondary" })),
        rows.length ? h("ul", { class: "life-lines" }, ...rows) : Aira.ui.empty(query.q || query.status ? "No sales match" : "No buyers yet", query.q || query.status ? "Try other words or another status." : "When someone asks to buy one of your decks, they show up here.")),
    );
  }

  // -------------------------------------------------------------------------- in the Aira frame
  // "+ New": what people make most here, each opening the same form as the button on its tab.
  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  Aira.appNew.learn = () => [
    { label: "New deck", icon: "ln-decks", href: "#/learn?tab=decks&new=deck" },
    { label: "Make a deck on any topic", icon: "ln-spark", href: "#/learn?tab=decks&new=topic" },
    { label: "Add cards to a deck", icon: "ln-card", href: "#/learn?tab=decks&new=card" },
    { label: "Add a book", icon: "ln-book", href: "#/learn?tab=reading&new=book" },
    { label: "Log reading time", icon: "ln-timer", href: "#/learn?tab=reading&new=minutes" },
  ];
  Aira.appSearch.learn = {
    placeholder: "Search decks, cards and books",
    // Decks, cards (either side) and books, up to six, each kind getting a place.
    async suggest(query) {
      const text = query.trim();
      if (!text) return [];
      const hits = await searchAll(text);
      const deckRows = hits.decks.map((d) => ({ label: `${d.emoji} ${d.title}`, sub: ["Deck", `${d.card_count} card${d.card_count === 1 ? "" : "s"}`, d.mine ? "" : `shared by ${d.owner_name}`].filter(Boolean).join(" · "), icon: "ln-decks", href: `#/learn?tab=decks&open=${d.id}` }));
      const cardRows = hits.cards.map((c) => ({ label: c.front, sub: `Card · ${c.back} · ${c.deck_title}`, icon: "ln-card", href: `#/learn?tab=decks&open=${c.deck_id}&card=${c.id}` }));
      const bookRows = hits.books.map((b) => ({ label: b.title, sub: ["Book", b.author, b.status === "finished" ? "finished" : "reading"].filter(Boolean).join(" · "), icon: "ln-book", href: `#/learn?tab=reading&book=${b.id}` }));
      return [...deckRows.slice(0, 2), ...cardRows.slice(0, 3), ...bookRows.slice(0, 1), ...deckRows.slice(2), ...cardRows.slice(3), ...bookRows.slice(1)].slice(0, 6);
    },
    // Enter: every deck, card and book that matches, on Decks.
    run(query) { Aira.router.go(["learn"], { tab: "decks", q: query.trim() }); },
  };

  Aira.views.learn = async (main, route) => {
    setKeys(null);
    // "+ New" and the search ask with ?new=, ?open= (&card=) or ?book=: remember the ask, take it out of the address
    // (Back or a refresh must not ask again) and draw the tab without it; the drawn tab then answers it.
    const query = route.query || {};
    if (query.new || query.open || query.book) {
      pending = { new: query.new || "", open: query.open || "", card: query.card || "", book: query.book || "" };
      const rest = { ...query };
      for (const key of ["new", "open", "card", "book"]) delete rest[key];
      if (!rest.tab) rest.tab = pending.new ? NEW_TAB[pending.new] || "today" : pending.book ? "reading" : "decks";
      Aira.router.go(["learn"], rest, { replace: true });
      return;
    }
    const ask = pending;
    pending = null;
    const tabs = isAdult() ? [...TABS, EARN_TAB] : TABS;
    const asked = query.tab;
    const current = tabs.find((t) => t.id === asked) || tabs[0];
    const body = S.page(main, { title: current.label, subtitle: current.about });
    body.classList.add("ln-page");
    const tab = S.tabs(body, route, "learn", tabs, current.id);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      setKeys(null);
      for (const key of Object.keys(hooks)) delete hooks[key];
      const holder = h("div", { class: "suite-body" });
      panel.replaceChildren(Aira.ui.skeleton(3));
      try {
        if (tab === "study") await study(holder, route);
        else if (tab === "decks") await decks(holder, render, route);
        else if (tab === "progress") await progressTab(holder);
        else if (tab === "earn" && isAdult()) await earn(holder, render, route);
        else if (tab === "quizzes") await quizzes(holder, route);
        else if (tab === "maths") await maths(holder);
        else if (tab === "reading") await reading(holder, render);
        else if (tab === "leaderboard") await leaderboard(holder);
        else await today(holder);
        panel.replaceChildren(holder);
        if (tab === "study") { const flip = holder.querySelector(".card .btn"); if (flip) flip.focus(); }
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
    if (!ask || !main.isConnected) return;
    if (ask.new && hooks[ask.new]) hooks[ask.new]();
    else if (ask.open && hooks.open) hooks.open(ask);
    else if (ask.book && hooks.pickBook) hooks.pickBook(ask.book);
  };
})();

/* Aira — Learn (#/learn): a little learning every day, for every age.
 *
 * Tabs (?tab=): today (goal, streak, level, badges, settings), decks (your decks, the household's shared decks,
 * starter decks, a deck on any topic, cards, sharing), study (one card at a time: show the answer, then Again /
 * Hard / Good / Easy, keys Space and 1-4), quizzes (multiple choice from a deck), maths (practice by level, relaxed
 * or timed), reading (books and minutes) and leaderboard (the family's week). Everything is graded and scheduled on
 * the server (/api/learn/*, aira/suite/learn). DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("learn", path, opts);
  const TABS = [
    { id: "today", label: "Today" },
    { id: "study", label: "Study" },
    { id: "decks", label: "Decks" },
    { id: "quizzes", label: "Quizzes" },
    { id: "maths", label: "Maths" },
    { id: "reading", label: "Reading" },
    { id: "leaderboard", label: "Family" },
  ];
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
    if (result.goal && result.goal.just_met) Aira.toast(`Goal met for today! +${result.xp_gained} XP${result.goal.freeze_used ? " · your streak freeze kept the streak" : ""}`, "success", { timeout: 6000 });
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
    const decks = (await api("/decks")).decks;
    const picker = Aira.ui.select([["", "All my decks"], ...decks.map((d) => [d.id, `${d.emoji} ${d.title}`])], deckId, { "aria-label": "Deck to study" });
    picker.addEventListener("change", () => Aira.router.go(["learn"], { tab: "study", deck: picker.value }));
    const stage = h("div", { class: "suite-body" });
    body.append(h("div", { class: "btn-row" }, Aira.ui.field("Study", picker)), stage);
    let queue = [];
    let index = 0;
    let started = Date.now();
    let done = 0;
    const load = async () => {
      const data = await api(`/study${deckId ? `?deck_id=${encodeURIComponent(deckId)}` : ""}`);
      queue = data.cards;
      index = 0;
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
          Aira.toast(`${value === "again" ? "We'll try it again soon." : `Nice! ${result.when}.`} +${result.xp_gained} XP`, value === "again" ? "" : "success", { timeout: 1800 });
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
  async function decks(body, rerender) {
    const data = await api("/decks");
    const mine = data.decks.filter((d) => d.mine);
    const shared = data.decks.filter((d) => !d.mine);
    const deckCard = (deck) => h("article", { class: "life-card" },
      h("header", {}, h("span", { style: { fontSize: "30px" }, "aria-hidden": "true", text: deck.emoji }), h("div", {}, h("h2", { text: deck.title }), h("span", { class: "muted small", text: deck.mine ? (deck.shared ? "Shared with the household" : "Only you") : `Shared by ${deck.owner_name}` }))),
      progressBar(deck.progress.learned, deck.progress.total, "Learned"),
      h("p", { class: "muted", text: `${deck.progress.learned} of ${deck.progress.total} learned · ${deck.progress.due} due · ${deck.progress.new} new` }),
      h("div", { class: "btn-row" },
        h("a", { class: "btn", href: `#/learn?tab=study&deck=${deck.id}`, text: "Study" }),
        h("a", { class: "btn secondary", href: `#/learn?tab=quizzes&deck=${deck.id}`, text: "Quiz" }),
        S.button("Open", () => openDeck(deck.id, rerender), { kind: "ghost" })));
    add(body,
      h("div", { class: "btn-row" },
        S.button("New deck", () => newDeck(rerender)),
        S.button("Make a deck on any topic", () => generate(rerender), { kind: "secondary" })),
      S.section("Your decks", mine.length ? h("div", { class: "life-cards" }, ...mine.map(deckCard)) : Aira.ui.empty("No decks yet", "Add a starter deck below, make your own, or let Aira make one on any topic.")),
      shared.length ? S.section("Shared by your household", h("div", { class: "life-cards" }, ...shared.map(deckCard))) : null,
      S.section("Starter decks", h("p", { class: "muted", text: "Ready to study. Adding one makes your own copy." }),
        h("div", { class: "life-tiles" }, ...data.starters.map((starter) => h("div", { class: "life-tile" },
          h("span", { style: { fontSize: "34px" }, "aria-hidden": "true", text: starter.emoji }),
          h("strong", { text: starter.title }),
          h("span", { class: "muted", text: `${starter.description} ${starter.cards} cards.` }),
          starter.for_you ? S.pill("for you", { "for you": "good" }) : null,
          S.button("Add", async () => { const added = await api("/starters", { method: "POST", body: { key: starter.key } }); Aira.toast(`${added.deck.title} added.`, "success"); rerender(); }, { kind: "secondary sm" }))))),
    );
  }

  const deckFields = [
    { name: "title", label: "Title", required: true },
    { name: "emoji", label: "Picture", type: "select", options: ["📚", "🧠", "🌍", "🔢", "✏️", "🎵", "🧪", "🌱", "🏛️", "💬", "⭐", "🐾"] },
    { name: "description", label: "What it's about", type: "textarea", rows: 2 },
    { name: "shared", label: "Share with my household (everyone studies it with their own progress)", type: "checkbox" },
  ];

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

  async function openDeck(deckId, rerender) {
    const deck = (await api(`/decks/${deckId}`)).deck;
    const reload = () => { Aira.closeDrawer(); openDeck(deckId, rerender); rerender(); };
    const cardFields = [
      { name: "front", label: "Front (the question)", type: "textarea", rows: 2, required: true },
      { name: "back", label: "Back (the answer)", type: "textarea", rows: 2, required: true },
      { name: "hint", label: "Hint (optional)" },
      { name: "example", label: "Example (optional)", type: "textarea", rows: 2 },
    ];
    const rows = deck.cards.map((card) => h("li", { class: "life-line", style: { borderBottom: "1px solid var(--line)", paddingBottom: "8px" } },
      h("div", { style: { flex: "1" } }, h("span", { class: "life-line-text", text: card.front }), h("span", { class: "muted life-line-sub", text: card.back })),
      deck.mine ? h("div", { class: "btn-row" },
        S.button("Edit", () => S.edit({ title: "Edit card", fields: cardFields, values: card, onSave: async (values) => { await api(`/cards/${card.id}`, { method: "PATCH", body: values }); reload(); } }), { kind: "ghost sm" }),
        S.button("Delete", async () => { if (await S.confirmDelete("this card")) { await api(`/cards/${card.id}`, { method: "DELETE" }); reload(); } }, { kind: "ghost sm" })) : null));
    Aira.drawer({
      title: `${deck.emoji} ${deck.title}`,
      subtitle: deck.mine ? (deck.shared ? "Shared with the household" : "Only you") : `Shared by ${deck.owner_name}`,
      body: h("div", { class: "suite-body" },
        deck.description ? h("p", { text: deck.description }) : null,
        h("div", { class: "btn-row" },
          h("a", { class: "btn", href: `#/learn?tab=study&deck=${deck.id}`, onclick: () => Aira.closeDrawer(), text: "Study" }),
          deck.mine ? S.button("Add a card", () => S.edit({ title: "Add a card", fields: cardFields, onSave: async (values) => { await api(`/decks/${deck.id}/cards`, { method: "POST", body: values }); reload(); } }), { kind: "secondary" }) : null,
          deck.mine ? S.button("Edit deck", () => S.edit({ title: "Edit deck", fields: deckFields, values: deck, onSave: async (values) => { await api(`/decks/${deck.id}`, { method: "PATCH", body: values }); reload(); } }), { kind: "ghost" }) : null,
          deck.mine ? S.button("Delete deck", async () => { if (await S.confirmDelete(deck.title)) { await api(`/decks/${deck.id}`, { method: "DELETE" }); Aira.closeDrawer(); rerender(); } }, { kind: "ghost" }) : null),
        deck.can_share_publicly ? S.share({
          title: "A page to study online",
          text: "Anyone with the link can flip through these cards in their browser (nothing they do is saved). Good for grandparents and cousins.",
          link: deck.link,
          onCreate: async () => (await api(`/decks/${deck.id}/link`, { method: "POST", body: {} })).link,
          onRevoke: async () => { await api(`/decks/${deck.id}/link`, { method: "DELETE" }); },
        }) : null,
        S.section(`${deck.cards.length} card${deck.cards.length === 1 ? "" : "s"}`, deck.cards.length ? h("ul", { class: "life-lines" }, ...rows) : Aira.ui.empty("No cards yet", deck.mine ? "Add your first card." : "The owner has not added cards yet."))),
    });
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
      usable.length ? S.section("Quiz yourself", h("div", { class: "btn-row" }, Aira.ui.field("Deck", deckSelect), Aira.ui.field("Length", countSelect)), h("div", { class: "btn-row" }, S.button("Start the quiz", start))) : Aira.ui.empty("No decks to quiz yet", "Add a deck with at least two cards first."),
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
  async function reading(body, rerender) {
    const data = await api("/books");
    const books = data.books;
    const bookSelect = Aira.ui.select([["", "No particular book"], ...books.filter((b) => b.status === "reading").map((b) => [b.id, b.title])], "", { "aria-label": "Book" });
    const log = async (minutes) => { const result = await api("/reading", { method: "POST", body: { minutes, book_id: bookSelect.value } }); Aira.toast(`${minutes} minutes logged. +${result.xp_gained} XP`, "success"); celebrate(result); rerender(); };
    body.append(
      S.kpis([{ label: "Read today", value: `${data.minutes_today} min` }, { label: "This week", value: `${data.minutes_week} min` }, { label: "Books", value: books.length }]),
      S.section("I read for...", Aira.ui.field("Book", bookSelect),
        h("div", { class: "btn-row" }, ...[10, 20, 30].map((m) => S.button(`${m} minutes`, () => log(m), { kind: "secondary" })),
          S.button("Other", () => S.edit({ title: "Minutes read", fields: [{ name: "minutes", label: "Minutes", type: "number", min: 1, max: 600, required: true }], onSave: async (v) => log(Number(v.minutes)) }), { kind: "ghost" }))),
      S.section("My books", h("div", { class: "btn-row" }, S.button("Add a book", () => S.edit({ title: "Add a book", fields: [{ name: "title", label: "Title", required: true }, { name: "author", label: "Author" }], onSave: async (v) => { await api("/books", { method: "POST", body: v }); rerender(); } }), { kind: "secondary" })),
        S.table({ columns: [
          { key: "title", label: "Book", render: (b) => `${b.title}${b.author ? ` · ${b.author}` : ""}` },
          { key: "minutes", label: "Minutes", num: true },
          { key: "status", label: "", render: (b) => (b.status === "finished" ? S.pill("finished", { finished: "good" }) : S.button("Finished it!", async () => { await api(`/books/${b.id}`, { method: "PATCH", body: { finished: true } }); Aira.toast("Well done! 📖", "success"); rerender(); }, { kind: "ghost sm" })) },
        ], rows: books, empty: "No books yet. Add the one you are reading." })),
    );
  }

  // -------------------------------------------------------------------------- leaderboard
  async function leaderboard(body) {
    const data = await api("/leaderboard");
    body.append(S.section("This week in the family", h("p", { class: "muted", text: "Everyone who learns with Aira here. XP comes from cards, quizzes, maths and reading." }),
      h("ol", { class: "life-people" }, ...data.people.map((p, index) => h("li", { class: "life-person", style: p.profile_id === data.me ? { background: "var(--brand-soft)", borderRadius: "12px", padding: "6px" } : {} },
        h("strong", { style: { width: "28px", fontSize: "20px" }, text: index < 3 && p.xp_week ? ["🥇", "🥈", "🥉"][index] : String(index + 1) }),
        h("span", { class: "life-face-small", style: { background: p.color }, "aria-hidden": "true", text: p.emoji }),
        h("div", {}, h("strong", { text: p.name }), h("div", { class: "muted small", text: `Level ${p.level} · 🔥 ${p.streak} · ${p.cards_week} cards` })),
        h("strong", { style: { fontSize: "18px" }, text: `${p.xp_week} XP` }))))));
  }

  Aira.views.learn = async (main, route) => {
    setKeys(null);
    const body = S.page(main, { title: "Learn", subtitle: "A little practice every day. Cards come back just before you would forget them." });
    const tab = S.tabs(body, route, "learn", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      setKeys(null);
      const holder = h("div", { class: "suite-body" });
      panel.replaceChildren(Aira.ui.skeleton(3));
      try {
        if (tab === "study") await study(holder, route);
        else if (tab === "decks") await decks(holder, render);
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
  };
})();

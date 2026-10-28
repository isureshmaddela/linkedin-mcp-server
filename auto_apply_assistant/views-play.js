/* Aira — Play (#/play): games, the daily challenge, quizzes people write, live family trivia and quiz nights,
 * scores and the watch list.
 *
 * Tabs (?tab=): games (challenges waiting for you, then big tiles; choose a level and relaxed or timed, then play),
 * daily (today's challenge for everyone and the family board), quizzes (write a quiz, with suggested questions;
 * play it, see its family board, host it live), live (host or join live trivia; quiz nights with an entry fee the
 * host marks as paid; the room updates every 1.5 s), scores (personal bests, streak, family leaderboard, a
 * spreadsheet of every game) and watch (films, shows, books, podcasts and games to enjoy together: search, filter,
 * edit, pick something for tonight, plan it together), and search (?tab=search&q=: what the box across the top
 * found). In the Aira frame (shell.js) "+ New" offers a quiz, live trivia, a paid quiz night (grown-ups) and the watch
 * list; the box suggests games, quizzes, the watch list and live games. Everything runs through /api/play/*
 * (aira/suite/play).
 * The browser never sends a score: it sends moves and answers, and the server works the score out. DOM is built
 * with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("play", path, opts);
  const post = (path, body = {}) => api(path, { method: "POST", body });

  // Play's own glyphs for the left menu's folders, "+ New" and the search box (added only when Aira has none).
  const GLYPHS = {
    "play-dice": '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h.01M15 9h.01M12 12h.01M9 15h.01M15 15h.01" stroke-width="3"/></svg>',
    "play-daily": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    "play-quiz": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17.5h.01"/></svg>',
    "play-live": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/></svg>',
    "play-trophy": '<svg viewBox="0 0 24 24"><path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8 21h8M9 17h6"/></svg>',
    "play-film": '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>',
    "play-book": '<svg viewBox="0 0 24 24"><path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3M9 7h6"/></svg>',
    "play-pen": '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;

  // Each tab is a folder in the left menu; the page is named after the open one (the app's name is in the bar).
  const TABS = [
    { id: "games", label: "Games", icon: "play-dice", about: "Pick a game and a level. Every game can be played without a clock." },
    { id: "daily", label: "Daily challenge", icon: "play-daily", about: "One puzzle for everyone today: no clock, one try each, and a family board." },
    { id: "quizzes", label: "Quizzes", icon: "play-quiz", about: "Write a quiz about your family, a hobby or a class. Play it alone, challenge someone, or host it live." },
    { id: "live", label: "Live trivia", icon: "play-live", about: "Everyone plays on their own phone or computer. Grown-ups can run a paid quiz night." },
    { id: "scores", label: "Scores", icon: "play-trophy", about: "Your best scores and play streak, and the family leaderboard." },
    { id: "watch", label: "Watch and read", icon: "play-film", about: "Films, shows, books, podcasts and games the family wants to enjoy together." },
  ];
  const KIND_ICONS = { film: "play-film", show: "play-film", book: "play-book", podcast: "mic", game: "play-dice" };
  const GAME_ICONS = { memory: "🃏", scramble: "🔤", maths: "➕", trivia: "❓", sudoku: "🔢", word: "🟩", quiz: "📝" };
  const KIND_LABELS = { film: "Film", show: "Show", book: "Book", podcast: "Podcast", game: "Game" };
  const STATUS_LABELS = { want: "Want to", doing: "Watching / reading", done: "Finished" };
  const reduceMotion = () => document.body.classList.contains("reduce-motion") || (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const plural = (n, word, many) => `${n} ${n === 1 ? word : many || `${word}s`}`;
  const me = () => (Aira.life && Aira.life.me) || null;
  const isChild = () => !!(me() && me().age_group === "child");
  const isAdult = () => !!(me() && (me().age_group === "adult" || me().age_group === "senior"));
  const others = () => ((Aira.life && Aira.life.profiles) || []).filter((p) => !me() || p.id !== me().id);

  let stopLive = null;
  let stopClock = null;
  function stopTimers() {
    if (stopLive) { stopLive(); stopLive = null; }
    if (stopClock) { stopClock(); stopClock = null; }
  }

  const big = { minHeight: "52px", fontSize: "17px" };
  const list = (...items) => h("ul", { class: "play-list" }, ...items);
  const fill = (node, ...children) => node.replaceChildren(...children.filter(Boolean)); // DOM would show a null as "null"
  const showError = (node, error) => { node.textContent = error && error.message ? error.message : String(error); node.hidden = false; };

  // ---------------------------------------------------------------------------
  // Games: challenges for me, choose a game, play, result
  // ---------------------------------------------------------------------------
  async function gamesTab(panel, ask) {
    stopTimers();
    const [catalog, challenges] = await Promise.all([api("/games"), api("/challenges")]);
    // The search box chose a game: open its level and mode straight away.
    const asked = ask && ask.game ? catalog.games.find((game) => game.id === ask.game) : null;
    if (asked) { setup(panel, catalog, asked); return; }
    const tiles = catalog.games.map((game) => h("button", { type: "button", class: "play-tile", onclick: () => setup(panel, catalog, game) },
      h("span", { class: "play-tile-icon", "aria-hidden": "true", text: GAME_ICONS[game.id] || "🎲" }),
      h("strong", { class: "play-tile-name", text: game.name }),
      h("span", { class: "muted", text: game.tagline })));
    fill(panel,
      challengesSection(panel, challenges),
      h("div", { class: "play-tiles" }, ...tiles,
        h("a", { class: "play-tile", href: "#/play?tab=quizzes" },
          h("span", { class: "play-tile-icon", "aria-hidden": "true", text: GAME_ICONS.quiz }),
          h("strong", { class: "play-tile-name", text: "Family quizzes" }),
          h("span", { class: "muted", text: "Write your own quiz and play it together" }))));
  }

  function challengesSection(panel, data) {
    const incoming = data.incoming || [];
    const results = (data.results || []).slice(0, 5);
    const outgoing = data.outgoing || [];
    if (!incoming.length && !results.length && !outgoing.length) return null;
    const outcomeText = (c) => {
      const score = c.incoming ? c.score : c.target;
      const theirs = c.incoming ? c.target : c.score;
      const verdict = c.outcome === "won" ? "You won" : c.outcome === "lost" ? `${c.who} won` : "A draw";
      return `${verdict}: ${score} to ${theirs} in ${c.name}`;
    };
    return S.section("Challenges",
      incoming.length ? list(...incoming.map((c) => h("li", { class: "play-row" },
        h("div", { class: "play-row-main" }, h("strong", { text: `${c.who_emoji || ""} ${c.who} challenged you to ${c.name}`.trim() }), h("div", { class: "muted", text: `Beat ${c.target} points · ${c.level_label} · ${c.mode === "timed" ? "timed" : "relaxed"}. You get the very same puzzle.` })),
        h("div", { class: "btn-row" },
          S.button(c.started ? "Carry on" : "Take it on", async () => play(panel, (await post(`/challenges/${c.id}/play`)).session)),
          S.button("Not now", async () => { await api(`/challenges/${c.id}`, { method: "DELETE" }); gamesTab(panel); }, { kind: "ghost" }))))) : null,
      outgoing.length ? h("p", { class: "muted", text: `Waiting for ${outgoing.map((c) => `${c.who} (${c.name})`).join(", ")} to take your challenge.` }) : null,
      results.length ? h("div", {}, h("h3", { class: "play-h3", text: "Results" }), list(...results.map((c) => h("li", { class: "play-row", text: `${c.outcome === "won" ? "🏆 " : ""}${outcomeText(c)}${c.incoming ? "" : ` (${c.who} took your challenge)`}` })))) : null);
  }

  function choiceRow(label, options, selected, onPick) {
    const group = h("div", { role: "radiogroup", "aria-label": label, class: "play-choices" });
    const render = (value) => group.replaceChildren(...options.map((option) => h("button", { type: "button", role: "radio", "aria-checked": String(option.id === value), class: `btn ${option.id === value ? "" : "secondary"}`, style: big, onclick: () => { onPick(option.id); render(option.id); }, text: option.label })));
    render(selected);
    return h("div", { class: "play-field" }, h("strong", { text: label }), group);
  }

  function setup(panel, catalog, game) {
    stopTimers();
    let level = game.levels.some((l) => l.id === game.suggested) ? game.suggested : game.levels.some((l) => l.id === catalog.suggested_level) ? catalog.suggested_level : game.levels[0].id;
    let mode = catalog.default_mode;
    const start = async () => {
      const session = (await api("/sessions", { method: "POST", body: { game: game.id, level, mode } })).session;
      play(panel, session);
    };
    panel.replaceChildren(S.section(`${GAME_ICONS[game.id] || ""} ${game.name}`,
      h("p", { class: "muted", text: game.tagline }),
      choiceRow("Level", game.levels, level, (value) => { level = value; }),
      game.id === "word" ? null : choiceRow("How to play", [{ id: "relaxed", label: "Relaxed (no clock)" }, { id: "timed", label: "Timed (bonus for speed)" }], mode, (value) => { mode = value; }),
      h("div", { class: "btn-row" }, S.button("Start", start), h("button", { type: "button", class: "btn ghost", text: "Back to games", onclick: () => gamesTab(panel) }))));
  }

  function header(session) {
    const clock = h("span", { class: "muted", "aria-live": "off" });
    if (session.mode === "timed") {
      const started = Date.parse(session.started_at);
      const tick = () => { const seconds = Math.max(0, Math.round((Date.now() - started) / 1000)); clock.textContent = `Time: ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; };
      tick();
      const timer = setInterval(tick, 1000);
      stopClock = () => clearInterval(timer);
    }
    const label = session.daily_date ? " · today's challenge" : session.challenge ? ` · beat ${session.challenge.target}` : "";
    return h("div", { class: "play-head" },
      h("h2", { class: "suite-h2", style: { fontSize: "20px" }, text: `${GAME_ICONS[session.game] || ""} ${session.name}${label}` }),
      h("span", {}, S.pill(session.mode === "timed" ? "timed" : "relaxed", { timed: "warn", relaxed: "good" }), " ", clock));
  }

  function play(panel, session) {
    stopTimers();
    const area = h("div", { class: "play-area" });
    panel.replaceChildren(S.section("", header(session), area));
    const done = (finished) => result(panel, finished);
    const renderers = { memory: memoryGame, scramble: answersGame, maths: answersGame, trivia: triviaGame, quiz: triviaGame, sudoku: sudokuGame, word: wordGame };
    (renderers[session.game] || answersGame)(area, session, done);
  }

  // What the player answered next to the right answer, so a result teaches something.
  function review(session) {
    const r = session.result || {};
    if (!Array.isArray(r.marks)) return null;
    const given = r.given || [];
    const rows = r.marks.map((right, i) => {
      if (session.game === "trivia" || session.game === "quiz") {
        const q = session.puzzle.questions[i];
        const answer = q.choices[r.answers[i]];
        const mine = given[i] === null || given[i] === undefined ? "" : q.choices[given[i]];
        return { right, text: right ? `${q.text} → ${answer}` : `${q.text} → the answer is ${answer}${mine ? ` (you chose ${mine})` : ""}` };
      }
      const expected = session.game === "maths" ? String(r.answers[i]) : String(r.words[i]).toUpperCase();
      const shown = session.game === "maths" ? `${session.puzzle.questions[i]} = ${expected}` : `${session.puzzle.rounds[i].letters} → ${expected}`;
      const typed = given[i] ? String(given[i]).toUpperCase() : "";
      return { right, text: right ? shown : `${shown}${typed ? ` (you wrote ${typed})` : " (no answer)"}` };
    });
    return h("ol", { class: "play-review" }, ...rows.map((row) => h("li", { class: row.right ? "right" : "wrong" }, h("span", { "aria-hidden": "true", text: row.right ? "✓ " : "✗ " }), h("span", { class: "sr-only", text: row.right ? "Right: " : "Not right: " }), h("span", { text: row.text }))));
  }

  function result(panel, session) {
    stopTimers();
    const details = [];
    const r = session.result || {};
    if (r.total) details.push(`${r.correct} of ${r.total} right`);
    if (r.moves) details.push(`${r.moves} moves for ${r.pairs} pairs${r.perfect ? " (perfect!)" : ""}`);
    if (r.tries) details.push(r.solved ? `Found in ${plural(r.tries, "try", "tries")}` : `The word was ${String(r.answer || "").toUpperCase()}`);
    if (r.hints) details.push(`${plural(r.hints, "hint")} used`);
    if (session.mode === "timed" && r.seconds !== undefined) details.push(plural(r.seconds, "second"));
    let challengeNote = null;
    const ch = session.challenge;
    if (ch && ch.status === "done") {
      const text = session.score > ch.target ? `🏆 You beat ${ch.who}'s ${ch.target}!` : session.score < ch.target ? `${ch.who}'s ${ch.target} still stands. So close!` : `A draw with ${ch.who}: ${ch.target} each!`;
      challengeNote = h("p", { class: "play-banner", role: "status", text });
    }
    const title = session.best ? "New personal best!" : session.score > 0 ? "Well played!" : "Good try!";
    const canChallenge = !session.daily_date && !(session.game === "word" && session.level === "daily") && !ch && others().length > 0;
    panel.replaceChildren(S.section(title,
      h("p", { class: "play-score", text: `${session.score} points` }),
      details.length ? h("p", { class: "muted", style: { fontSize: "17px" }, text: details.join(" · ") }) : null,
      challengeNote,
      review(session),
      h("div", { class: "btn-row" },
        session.daily_date ? h("a", { class: "btn", href: "#/play?tab=daily", text: "See the family board" }) : S.button("Play again", async () => { play(panel, (await api("/sessions", { method: "POST", body: session.game === "quiz" ? { quiz: session.level, mode: session.mode } : { game: session.game, level: session.level, mode: session.mode } })).session); }),
        canChallenge ? S.button("Challenge someone", () => challengeSomeone(session), { kind: "secondary" }) : null,
        session.game === "quiz" ? h("button", { type: "button", class: "btn secondary", text: "Back to quizzes", onclick: () => quizzesTab(panel) }) : h("button", { type: "button", class: "btn secondary", text: "Back to games", onclick: () => gamesTab(panel) }),
        h("a", { class: "btn ghost", href: "#/play?tab=scores", text: "My scores" }))));
    if (Aira.life && Aira.life.prefs && (Aira.life.prefs.read_aloud || Aira.life.prefs.easy_mode)) Aira.life.speak(`${session.best ? "New personal best! " : ""}${session.score} points.`);
  }

  function challengeSomeone(session) {
    const people = others();
    return S.edit({
      title: `Who can beat ${session.score}?`,
      fields: [...people.map((p) => ({ name: `who_${p.id}`, label: `${p.emoji || ""} ${p.name}`.trim(), type: "checkbox" }))],
      values: people.length === 1 ? { [`who_${people[0].id}`]: true } : {},
      submitLabel: "Send the challenge",
      onSave: async (values) => {
        const to = people.filter((p) => values[`who_${p.id}`]).map((p) => p.id);
        if (!to.length) throw new Error("Choose at least one person.");
        await post("/challenges", { session_id: session.id, to });
        Aira.toast(`Challenge sent. They get the very same ${session.name}.`, "success");
      },
    });
  }

  // Memory: the server shows each card as it is turned (the layout never reaches the browser).
  function memoryGame(area, session, done) {
    const cards = session.puzzle.cards;
    const known = { ...(session.progress.matched || {}) };
    let open = session.progress.pending ? { [session.progress.pending.index]: session.progress.pending.card } : {};
    let busy = false;
    const status = h("p", { class: "muted", "aria-live": "polite", text: `Find the ${session.puzzle.pairs} pairs. Moves: ${session.progress.moves || 0}` });
    const columns = cards <= 16 || window.innerWidth < 640 ? 4 : 6; // four across on a phone
    const board = h("div", { role: "grid", "aria-label": "Cards", class: "play-memory", style: { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` } });
    const render = () => {
      board.replaceChildren(...Array.from({ length: cards }, (_, index) => {
        const face = known[index] || open[index];
        return h("button", { type: "button", class: `play-card${known[index] ? " found" : ""}${face ? " up" : ""}`, "aria-label": face ? `Card ${index + 1}: ${face}` : `Card ${index + 1}, face down`, disabled: !!known[index], text: face || "?", onclick: () => flip(index) });
      }));
    };
    const flip = async (index) => {
      if (busy || known[index] || open[index]) return;
      busy = true;
      try {
        const res = await post(`/sessions/${session.id}/move`, { action: "flip", index });
        open[index] = res.card;
        if (!res.first) {
          if (res.matched) { known[index] = res.card; known[res.other] = res.other_card; open = {}; render(); }
          else { open[res.other] = res.other_card; render(); await new Promise((r) => setTimeout(r, reduceMotion() ? 1400 : 1000)); open = {}; }
          status.textContent = `Pairs found: ${res.found} of ${session.puzzle.pairs}. Moves: ${res.moves}`;
          if (res.complete) { done((await post(`/sessions/${session.id}/finish`)).session); return; }
        }
        render();
      } catch (error) { Aira.reportError(error); } finally { busy = false; }
    };
    render();
    area.append(status, board);
  }

  // Word scramble and quick maths: every answer at once, checked on the server.
  function answersGame(area, session, done) {
    const isMaths = session.game === "maths";
    const items = isMaths ? session.puzzle.questions : session.puzzle.rounds.map((r) => r.letters.split("").join(" "));
    const inputs = items.map((_, i) => h("input", { type: "text", inputmode: isMaths ? "numeric" : "text", autocomplete: "off", autocapitalize: isMaths ? "off" : "none", spellcheck: "false", "aria-label": `Answer ${i + 1}`, class: "play-answer", style: big }));
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const rows = h("ol", { class: "play-sums" }, ...items.map((text, i) => h("li", {},
      h("div", { class: "play-sum" },
        h("span", { class: `play-sum-text${isMaths ? "" : " letters"}`, text: isMaths ? `${text} =` : text }), inputs[i]))));
    inputs.forEach((input, i) => input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); (inputs[i + 1] || submit).focus(); } }));
    const submit = h("button", { type: "button", class: "btn", style: big, text: "Check my answers", onclick: async (event) => {
      const blank = inputs.filter((input) => !input.value.trim()).length;
      if (blank === inputs.length && !(await Aira.confirm({ title: "No answers yet", message: "You have not typed any answers. Check anyway?", confirmLabel: "Check anyway" }))) return;
      await Aira.withBusy(event.currentTarget, async () => {
        try { done((await post(`/sessions/${session.id}/finish`, { answers: inputs.map((input) => input.value.trim()) })).session); } catch (err) { showError(error, err); }
      });
    } });
    area.append(h("p", { class: "muted", text: isMaths ? "Type each answer, then check." : "Unscramble each word, then check." }), rows, error, h("div", { class: "btn-row" }, submit));
    if (inputs[0]) inputs[0].focus();
  }

  // Trivia and family quizzes: one question at a time, all answers checked at the end.
  function triviaGame(area, session, done) {
    const questions = session.puzzle.questions;
    const answers = [];
    const render = (index) => {
      const q = questions[index];
      fill(area,
        h("p", { class: "muted", text: `Question ${index + 1} of ${questions.length}${q.category && session.game !== "quiz" ? ` · ${q.category}` : ""}` }),
        h("p", { class: "play-question", text: q.text }),
        h("div", { class: "play-options" }, ...q.choices.map((choice, c) => h("button", { type: "button", class: `btn ${answers[index] === c ? "" : "secondary"} play-option`, "aria-pressed": String(answers[index] === c), text: `${"ABCD"[c]}. ${choice}`, onclick: async (event) => {
          answers[index] = c;
          if (index + 1 < questions.length) { render(index + 1); return; }
          await Aira.withBusy(event.currentTarget, async () => { try { done((await post(`/sessions/${session.id}/finish`, { answers })).session); } catch (error) { Aira.reportError(error); } });
        } }))),
        index > 0 ? h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost sm", text: "← Previous question", onclick: () => render(index - 1) })) : null);
      const first = area.querySelector(".play-option");
      if (first) first.focus();
    };
    render(0);
  }

  // Sudoku: fill the grid; a hint fills one square (and costs points); the server checks the grid.
  function sudokuGame(area, session, done) {
    const size = session.puzzle.size;
    const box = size === 4 ? 2 : 3;
    const given = session.puzzle.grid;
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const cells = given.map((value, i) => {
      const row = Math.floor(i / size);
      const col = i % size;
      const input = h("input", { type: "text", inputmode: "numeric", maxlength: "1", autocomplete: "off", value: value ? String(value) : "", readonly: !!value, class: `play-cell${value ? " given" : ""}${size === 4 ? " small" : ""}`, "aria-label": `Row ${row + 1}, column ${col + 1}${value ? `, given ${value}` : ""}`, style: { borderRight: (col + 1) % box === 0 && col + 1 < size ? "3px solid var(--line-strong)" : undefined, borderBottom: (row + 1) % box === 0 && row + 1 < size ? "3px solid var(--line-strong)" : undefined } });
      input.addEventListener("input", () => { const v = input.value.replace(/[^0-9]/g, "").slice(-1); input.value = v && Number(v) >= 1 && Number(v) <= size ? v : ""; error.hidden = true; });
      input.addEventListener("keydown", (event) => {
        const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: size, ArrowUp: -size };
        if (moves[event.key] !== undefined) { const next = cells[i + moves[event.key]]; if (next) { event.preventDefault(); next.focus(); } }
      });
      return input;
    });
    const grid = () => cells.map((input) => Number(input.value) || 0);
    area.append(
      h("p", { class: "muted", text: `Fill every square with 1 to ${size} so no number repeats in a row, a column or a ${box} by ${box} box.` }),
      h("div", { class: "play-sudoku", style: { gridTemplateColumns: `repeat(${size}, 1fr)`, maxWidth: size === 4 ? "280px" : "460px" } }, ...cells),
      error,
      h("div", { class: "btn-row" },
        S.button("Check", async () => { try { done((await post(`/sessions/${session.id}/finish`, { grid: grid() })).session); } catch (err) { showError(error, err); } }),
        S.button("Hint (costs 50 points)", async () => { const res = await post(`/sessions/${session.id}/move`, { action: "hint", grid: grid() }); if (res.hint) { cells[res.hint.index].value = String(res.hint.value); cells[res.hint.index].focus(); } else showError(error, new Error("Every square is right. Press Check!")); }, { kind: "secondary" })));
  }

  // Word of the day: guesses checked on the server; the colours also come with words for screen readers.
  function wordGame(area, session, done) {
    const tries = session.puzzle.tries;
    const guesses = [...(session.progress.guesses || [])];
    const words = { correct: "right place", present: "in the word", absent: "not in the word" };
    const board = h("div", { class: "play-word" });
    const input = h("input", { type: "text", maxlength: "5", autocomplete: "off", autocapitalize: "characters", spellcheck: "false", "aria-label": "Your five-letter guess", class: "play-guess", style: big });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const left = h("p", { class: "muted", "aria-live": "polite" });
    const render = () => {
      board.replaceChildren(...Array.from({ length: tries }, (_, row) => {
        const guess = guesses[row];
        return h("div", { role: "row", "aria-label": guess ? `${guess.word.toUpperCase()}: ${guess.feedback.map((f, i) => `${guess.word[i].toUpperCase()} ${words[f]}`).join(", ")}` : `Try ${row + 1}`, class: "play-word-row" },
          ...Array.from({ length: 5 }, (_, col) => h("span", { "aria-hidden": "true", class: `play-letter${guess ? ` ${guess.feedback[col]}` : ""}`, text: guess ? guess.word[col].toUpperCase() : "" })));
      }));
      left.textContent = `${plural(tries - guesses.length, "try", "tries")} left.`;
    };
    const send = async () => {
      error.hidden = true;
      try {
        const res = await post(`/sessions/${session.id}/move`, { action: "guess", word: input.value.trim() });
        guesses.push({ word: res.word, feedback: res.feedback });
        input.value = "";
        render();
        if (res.complete) done(res.session);
      } catch (err) { showError(error, err); }
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); send(); } });
    render();
    area.append(h("p", { class: "muted", text: "Green: right letter, right place. Yellow: in the word, wrong place. Grey: not in the word." }), board, left, h("div", { class: "btn-row" }, input, S.button("Guess", send)), error);
    input.focus();
  }

  // ---------------------------------------------------------------------------
  // Daily challenge
  // ---------------------------------------------------------------------------
  async function dailyTab(panel) {
    const data = await api("/daily");
    const mine = data.mine;
    const finished = mine && mine.status === "finished";
    panel.replaceChildren(
      S.kpis([{ label: "Today's game", value: `${GAME_ICONS[data.game] || ""} ${data.name}` }, { label: "My score today", value: finished ? mine.score : mine ? "Started" : "Not played", tone: finished ? "good" : "" }, { label: "Play streak", value: plural(data.streak, "day") }]),
      S.section("Today's challenge", h("p", { class: "muted", text: `${data.tagline}. Everyone gets the same puzzle today, with no clock, and one try each.` }),
        finished ? h("p", { style: { fontSize: "18px" }, text: `You scored ${mine.score}. Come back tomorrow for a new one!` }) : h("div", { class: "btn-row" }, S.button(mine ? "Carry on" : "Play today's challenge", async () => { play(panel, (await api("/sessions", { method: "POST", body: { daily: true } })).session); }))),
      S.section("Family board today", S.table({ columns: [{ key: "rank", label: "#", num: true }, { key: "name", label: "Who", render: (r) => `${r.emoji} ${r.name}` }, { key: "score", label: "Score", num: true }], rows: data.board, empty: "Nobody has played yet today. Be the first!" })));
  }

  // ---------------------------------------------------------------------------
  // Quizzes people write
  // ---------------------------------------------------------------------------
  async function quizzesTab(panel, ask) {
    stopTimers();
    const [{ quizzes }, catalog] = await Promise.all([api("/quizzes"), api("/games")]);
    if (ask && ask.new === "quiz") { quizEditor(panel, null); return; }
    if (ask && ask.quiz) {
      const quiz = quizzes.find((item) => item.id === ask.quiz);
      if (quiz) { playQuiz(panel, catalog, quiz); return; }
      Aira.toast("That quiz is not here any more.", "info");
    }
    const card = (quiz) => h("article", { class: "card play-quiz" },
      h("div", { class: "play-quiz-title" }, h("strong", { text: quiz.title }), S.pill(quiz.shared ? "family" : "only me", { family: "good", "only me": "" })),
      quiz.about ? h("span", { text: quiz.about }) : null,
      h("span", { class: "muted", text: [plural(quiz.count, "question"), `by ${quiz.owner}`, quiz.plays ? `played ${plural(quiz.plays, "time")}` : "not played yet", quiz.my_best !== null ? `my best ${quiz.my_best}` : ""].filter(Boolean).join(" · ") }),
      h("div", { class: "btn-row" },
        S.button("Play", () => playQuiz(panel, catalog, quiz)),
        quiz.shared ? S.button("Host it live", () => hostForm(panel, catalog, [quiz], quiz.id), { kind: "secondary" }) : null,
        S.button("Family board", () => quizBoard(quiz), { kind: "ghost" }),
        quiz.can_change ? S.button("Edit", async () => quizEditor(panel, (await api(`/quizzes/${quiz.id}`)).quiz), { kind: "ghost" }) : null,
        quiz.can_change ? S.button("Delete", async () => { if (await S.confirmDelete(`the quiz "${quiz.title}"`)) { await api(`/quizzes/${quiz.id}`, { method: "DELETE" }); quizzesTab(panel); } }, { kind: "ghost" }) : null));
    panel.replaceChildren(
      h("div", { class: "play-toolbar" }, h("p", { class: "muted", text: quizzes.length ? plural(quizzes.length, "quiz", "quizzes") : "Nothing written yet." }), S.button("Write a quiz", () => quizEditor(panel, null), { icon: "play-pen" })),
      quizzes.length ? h("div", { class: "play-cards" }, ...quizzes.map(card)) : Aira.ui.empty("No quizzes yet", "Write one: three questions is enough to start. \"Suggest questions\" can help with ideas.",
        h("div", { class: "btn-row" }, S.button("Write the first quiz", () => quizEditor(panel, null), { kind: "secondary" }))));
  }

  async function quizBoard(quiz) {
    const { board } = await api(`/quizzes/${quiz.id}/board`);
    Aira.modal({ title: `${quiz.title}: family board`, body: S.table({ columns: [{ key: "rank", label: "#", num: true }, { key: "name", label: "Who", render: (r) => `${r.emoji} ${r.name}` }, { key: "best", label: "Best", num: true }, { key: "plays", label: "Played", num: true }], rows: board, empty: "Nobody has played it yet (the writer's own games do not count)." }), actions: [{ label: "Close", kind: "secondary" }] });
  }

  function playQuiz(panel, catalog, quiz) {
    stopTimers();
    let mode = catalog.default_mode;
    panel.replaceChildren(S.section(`${GAME_ICONS.quiz} ${quiz.title}`,
      h("p", { class: "muted", text: `${quiz.about ? `${quiz.about} · ` : ""}${plural(quiz.count, "question")} by ${quiz.owner}.` }),
      quiz.mine ? h("p", { class: "muted", text: "You wrote this one, so your games do not count on its family board." }) : null,
      choiceRow("How to play", [{ id: "relaxed", label: "Relaxed (no clock)" }, { id: "timed", label: "Timed (bonus for speed)" }], mode, (value) => { mode = value; }),
      h("div", { class: "btn-row" }, S.button("Start", async () => play(panel, (await post("/sessions", { quiz: quiz.id, mode })).session)), h("button", { type: "button", class: "btn ghost", text: "Back to quizzes", onclick: () => quizzesTab(panel) }))));
  }

  // The editor: a question, up to four answers and which one is right; suggested questions to start from.
  function quizEditor(panel, quiz) {
    stopTimers();
    const blank = () => ({ text: "", choices: ["", "", "", ""], answer: 0 });
    const rows = quiz && quiz.questions ? quiz.questions.map((q) => ({ text: q.text, choices: [...q.choices, "", "", "", ""].slice(0, 4), answer: q.answer })) : [blank(), blank(), blank()];
    const title = h("input", { type: "text", maxlength: "80", value: quiz ? quiz.title : "", placeholder: "Our family quiz" });
    const about = h("input", { type: "text", maxlength: "200", value: quiz ? quiz.about : "", placeholder: "Birthdays, holidays and funny moments" });
    const shared = h("input", { type: "checkbox", checked: quiz ? quiz.shared : true });
    const holder = h("div", { class: "play-questions" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const note = h("p", { class: "muted", "aria-live": "polite" });
    const read = () => holder.querySelectorAll("fieldset").forEach((set, i) => {
      rows[i].text = set.querySelector("textarea").value;
      rows[i].choices = [...set.querySelectorAll("input[type=text]")].map((input) => input.value);
      const picked = set.querySelector("input[type=radio]:checked");
      rows[i].answer = picked ? Number(picked.value) : 0;
    });
    const render = () => {
      holder.replaceChildren(...rows.map((row, i) => h("fieldset", { class: "play-question-edit" },
        h("legend", { text: `Question ${i + 1}` }),
        Aira.ui.field("Question", h("textarea", { rows: 2, maxlength: "200", text: row.text, placeholder: "What is Grandma's favourite song?" })),
        h("div", { class: "play-answers", role: "radiogroup", "aria-label": `Answers for question ${i + 1}: choose the right one` }, ...row.choices.map((choice, c) => h("div", { class: "play-answer-edit" },
          h("input", { type: "radio", name: `right-${i}`, value: String(c), checked: row.answer === c, "aria-label": `Answer ${"ABCD"[c]} is the right one` }),
          h("input", { type: "text", maxlength: "80", value: choice, "aria-label": `Answer ${"ABCD"[c]} for question ${i + 1}`, placeholder: c === 0 ? "The right answer (tick it)" : c === 1 ? "A wrong answer" : "Another answer (optional)" })))),
        h("div", { class: "btn-row" }, rows.length > 1 ? h("button", { type: "button", class: "btn ghost sm", text: "Remove this question", onclick: () => { read(); rows.splice(i, 1); render(); } }) : null))));
    };
    const topic = h("input", { type: "text", maxlength: "80", placeholder: "Space, football, the 1980s…" });
    const count = Aira.ui.select([["3", "3"], ["5", "5"], ["10", "10"]], "5", { "aria-label": "How many questions to suggest" });
    const suggest = S.button("Suggest questions", async () => {
      error.hidden = true;
      try {
        const data = await post("/quizzes/draft", { topic: topic.value.trim(), count: Number(count.value) });
        read();
        const filled = rows.filter((r) => r.text.trim() || r.choices.some((c) => c.trim()));
        rows.splice(0, rows.length, ...filled, ...data.questions.map((q) => ({ text: q.text, choices: [...q.choices, "", "", "", ""].slice(0, 4), answer: q.answer })));
        render();
        note.textContent = data.source === "model" ? `Added ${plural(data.questions.length, "suggested question")}. Check the facts before you save.` : `Added ${plural(data.questions.length, "question")} from the quiz book. Change anything you like.`;
      } catch (err) { showError(error, err); }
    }, { kind: "secondary" });
    const save = S.button(quiz ? "Save the quiz" : "Save quiz", async () => {
      read();
      error.hidden = true;
      const body = { title: title.value.trim(), about: about.value.trim(), shared: shared.checked, questions: rows.map((r) => ({ text: r.text, choices: r.choices, answer: r.answer })) };
      try {
        if (quiz) await api(`/quizzes/${quiz.id}`, { method: "PATCH", body });
        else await post("/quizzes", body);
        Aira.toast("Quiz saved.", "success");
        quizzesTab(panel);
      } catch (err) { showError(error, err); error.scrollIntoView({ block: "center" }); }
    });
    render();
    panel.replaceChildren(S.section(quiz ? `Edit "${quiz.title}"` : "Write a quiz",
      Aira.ui.field("Name of the quiz", title),
      Aira.ui.field("What it is about (optional)", about),
      h("label", { class: "suite-check" }, shared, h("span", { text: "Share it with the family (they can play it and see its board)" })),
      h("div", { class: "play-suggest" }, h("strong", { text: "Need ideas?" }), h("div", { class: "play-suggest-row" }, Aira.ui.field("Topic", topic), Aira.ui.field("How many", count), suggest), note),
      h("p", { class: "muted", text: "For each question, type two to four answers and tick the right one." }),
      holder,
      h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn secondary", text: "Add a question", onclick: () => { read(); rows.push(blank()); render(); const sets = holder.querySelectorAll("textarea"); sets[sets.length - 1].focus(); } })),
      error,
      h("div", { class: "btn-row" }, save, h("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => quizzesTab(panel) }))));
    title.focus();
  }

  // ---------------------------------------------------------------------------
  // Live trivia and quiz nights
  // ---------------------------------------------------------------------------
  // night: opened as "Run a paid quiz night" ("+ New"), with the entry fee to fill in first.
  function hostForm(panel, catalog, quizzes, quizId = "", { night = false } = {}) {
    const adult = isAdult();
    if (night && adult) setTimeout(() => { const fee = document.querySelector(".modal [name=entry_cents]"); if (fee) fee.focus(); }, 60);
    const fields = [
      { name: "title", label: "Name of the game", placeholder: "Sunday quiz" },
      quizzes.length ? { name: "quiz", label: "Questions from", type: "select", options: [["", "The quiz book"], ...quizzes.filter((q) => q.shared).map((q) => [q.id, `${q.title} (${plural(q.count, "question")})`])] } : null,
      { name: "category", label: "Quiz book questions about", type: "select", options: [["", "A bit of everything"], ...catalog.categories.map((c) => [c.id, c.label])], hint: "Used when the questions come from the quiz book." },
      { name: "questions", label: "How many questions", type: "number", min: 3, max: 30, placeholder: "Leave empty", hint: "Leave it empty to play every question of the quiz you chose, or 8 from the quiz book (the quiz book gives up to 15)." },
      { name: "seconds", label: "Seconds for each question", type: "number", min: 10, max: 120, hint: "Give everyone time to read. 45 is relaxed, 20 is quick." },
      { name: "auto_advance", label: "Move on by itself (otherwise the host presses Next)", type: "checkbox" },
      adult ? { name: "entry_cents", label: "Entry fee for guests (leave empty for a free game)", type: "money", hint: "For a quiz night at a club, café or fundraiser. Guests pay you; you mark who has paid. Family plays free." } : null,
      adult ? { name: "prize", label: "Prize (optional)", placeholder: "A bottle of wine, a cinema voucher…" } : null,
    ].filter(Boolean);
    return S.edit({
      title: night && adult ? "Run a paid quiz night" : "Host live trivia",
      fields,
      values: { questions: null, seconds: catalog.default_mode === "relaxed" ? 45 : 30, auto_advance: true, quiz: quizId },
      submitLabel: "Open the room",
      onSave: async (values) => {
        const body = { ...values };
        if (!body.quiz) delete body.quiz;
        if (body.questions === null) delete body.questions;
        if (!body.quiz && body.questions > 15) throw new Error("The quiz book gives up to 15 questions in one game. Choose 15 or fewer.");
        if (!body.entry_cents) delete body.entry_cents;
        const room = (await post("/rooms", body)).room;
        showRoom(room.id);
      },
    });
  }

  // A room has its own address (#/play?tab=live&room=<id>), so it can be reopened and linked.
  const showRoom = (roomId) => Aira.router.go(["play"], { tab: "live", room: roomId });

  async function liveTab(panel, route, ask) {
    stopTimers();
    if (route && route.query.room) { openRoom(panel, route.query.room); return; }
    const [{ rooms }, catalog, { quizzes }, nights] = await Promise.all([api("/rooms"), api("/games"), api("/quizzes"), api("/nights")]);
    await S.business();
    const code = h("input", { type: "text", maxlength: "4", autocomplete: "off", "aria-label": "Room code", placeholder: "ABCD", class: "play-code", style: big });
    const joinError = h("p", { class: "form-error", role: "alert", hidden: true });
    const joinCode = async () => {
      joinError.hidden = true;
      if (!/^[A-Za-z]{4}$/.test(code.value.trim())) { showError(joinError, new Error("Type the four letters of the room code.")); return; }
      try { const room = (await post("/rooms/join", { code: code.value.trim() })).room; showRoom(room.id); } catch (err) { showError(joinError, err); }
    };
    code.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); joinCode(); } });
    const nightsSection = nights.can_charge ? S.section("Quiz nights",
      h("p", { class: "muted", text: "Run a quiz for a club, a café or a fundraiser: set an entry fee when you host, share the link, and mark guests as paid. What you take shows here and in the Business hub." }),
      nights.payment_ready ? null : h("p", { class: "cloud-banner info", text: "Tip: add your payment link or payment instructions to your business profile (Business hub) so guests see how to pay you." }),
      nights.nights.length ? S.kpis([{ label: "Taken from quiz nights", value: S.money(nights.total_cents, nights.currency), tone: nights.total_cents ? "good" : "" }, { label: "Quiz nights", value: nights.nights.length }, { label: "Paying guests", value: nights.nights.reduce((sum, n) => sum + n.paid, 0) }]) : null,
      S.table({ columns: [{ key: "title", label: "Quiz night" }, { key: "created_at", label: "When", render: (n) => Aira.fmt.ago(n.created_at) }, { key: "entry_cents", label: "Entry", num: true, render: (n) => S.money(n.entry_cents, n.currency) }, { key: "paid", label: "Paid", num: true, render: (n) => `${n.paid} of ${n.guests}` }, { key: "takings_cents", label: "Taken", num: true, render: (n) => S.money(n.takings_cents, n.currency) }], rows: nights.nights, onRow: (n) => showRoom(n.id), empty: "No quiz nights yet. Host live trivia and set an entry fee to run one." })) : null;
    fill(panel,
      S.grid(
        S.section("Host a game", h("p", { class: "muted", text: "Everyone at home plays on their own phone or computer. Relatives can join with a link." }), h("div", { class: "btn-row" }, S.button("Host live trivia", () => hostForm(panel, catalog, quizzes)))),
        S.section("Join with a code", h("div", { class: "btn-row" }, code, S.button("Join", joinCode)), joinError)),
      S.section("Games happening now", rooms.length ? list(...rooms.map((room) => h("li", { class: "play-row" },
        h("div", { class: "play-row-main" }, h("strong", { text: room.title }), h("div", { class: "muted", text: `Hosted by ${room.host} · code ${room.code} · ${room.players} playing · ${room.phase === "lobby" ? "waiting to start" : "under way"}` })),
        S.button(room.joined ? "Open" : "Join", async () => { if (!room.joined) await post(`/rooms/${room.id}/join`); showRoom(room.id); })))) : Aira.ui.empty("No games right now", "Host one and the family will be invited.",
        h("div", { class: "btn-row" }, S.button("Host live trivia", () => hostForm(panel, catalog, quizzes), { kind: "secondary" })))),
      nightsSection);
    if (ask && (ask.new === "room" || ask.new === "night")) hostForm(panel, catalog, quizzes, "", { night: ask.new === "night" });
  }

  function openRoom(panel, roomId) {
    stopTimers();
    let version = "";
    let state = null;
    let localLeft = 0;
    const holder = h("div", { class: "play-area" });
    panel.replaceChildren(h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost", text: "← All games", onclick: () => Aira.router.go(["play"], { tab: "live" }) })), holder);
    const answer = async (choice) => { try { state = (await post(`/rooms/${roomId}/answer`, { choice })).room; version = ""; draw(); } catch (error) { Aira.reportError(error); } };
    const join = async () => { try { state = (await post(`/rooms/${roomId}/join`)).room; version = ""; draw(); } catch (error) { Aira.reportError(error); } };
    const markPaid = async (player, paid) => { state = (await post(`/rooms/${roomId}/players/${player.id}/paid`, { paid })).room; version = state.version; draw(); };
    const draw = () => {
      if (!state) return;
      const fee = state.entry_cents ? S.money(state.entry_cents, state.currency || undefined) : "";
      const players = h("ol", { class: "play-players" }, ...state.players.map((p) => h("li", {},
        h("span", { text: p.quizmaster ? `${p.emoji} ${p.name} (quizmaster, wrote the quiz)` : `${p.emoji} ${p.name}${p.guest ? " (guest)" : ""}: ${p.score}${state.phase === "question" && p.answered ? " ✓" : ""}` }),
        state.is_host && p.guest && state.entry_cents ? (p.paid
          ? h("span", { class: "play-paid" }, S.pill("paid", { paid: "good" }), S.button("Undo", () => markPaid(p, false), { kind: "ghost sm" }))
          : S.button(`Mark ${fee} paid`, () => markPaid(p, true), { kind: "secondary sm" })) : null)));
      const parts = [h("div", { class: "play-head" }, h("h2", { class: "suite-h2", style: { fontSize: "22px" }, text: state.title }), h("span", { class: "muted", text: `Code ${state.code} · host ${state.host}` }))];
      if (state.entry_cents || state.prize) parts.push(h("p", { class: "play-banner", text: [state.entry_cents ? `Entry ${fee} for guests (family plays free)` : "", state.prize ? `Prize: ${state.prize}` : ""].filter(Boolean).join(" · ") }));
      const open = state.phase === "lobby" || state.phase === "question" || state.phase === "reveal";
      if (!state.me && open) parts.push(h("div", { class: "play-banner play-join" }, h("p", { text: "You are watching this game. Join to play along." }), S.button("Join this game", join)));
      const quizmaster = !!(state.me && state.me.can_answer === false);
      if (quizmaster && open) parts.push(h("p", { class: "play-banner", text: "You wrote this quiz, so you know the answers. You are the quizmaster: read the questions out and let the others answer." }));
      if (state.is_host && state.entry_cents) parts.push(h("div", { class: "play-head" }, h("strong", { text: `Taken so far: ${S.money(state.takings_cents || 0, state.currency || undefined)} from ${plural(state.paid_count || 0, "guest")}` }), S.button("Download the entries", () => Aira.download(`/api/play/rooms/${roomId}/entries?format=csv`, "quiz-night-entries.csv"), { kind: "ghost sm" })));
      if (state.phase === "lobby") {
        parts.push(h("p", { style: { fontSize: "20px" }, text: `Waiting for players. Tell them the code: ${state.code}` }));
        if (state.is_host) {
          parts.push(h("div", { class: "btn-row" }, S.button("Start the game", async () => { state = (await post(`/rooms/${roomId}/start`)).room; draw(); }), S.button("Cancel the game", async () => { state = (await post(`/rooms/${roomId}/end`)).room; draw(); }, { kind: "ghost" })));
          if (!isChild()) parts.push(S.share({ title: state.entry_cents ? "Invite guests" : "Invite relatives", text: state.entry_cents ? `Anyone with this link can join with their name. They see the ${fee} entry fee and how to pay you.` : "Anyone with this link can join with their name, without an account.", link: state.link, onCreate: async () => (await post(`/rooms/${roomId}/link`)).link, onRevoke: async () => { await api(`/rooms/${roomId}/link`, { method: "DELETE" }); } }));
        }
      } else if (state.phase === "question" || state.phase === "reveal") {
        const q = state.question;
        const mine = state.me ? state.me.choice : null;
        localLeft = state.seconds_left;
        const left = h("strong", { "aria-live": "off", style: { fontSize: "22px" }, text: state.phase === "question" ? `${localLeft} s` : "Time's up" });
        parts.push(h("p", { class: "muted", text: `Question ${state.index + 1} of ${state.total}${q.category ? ` · ${q.category}` : ""}` }), h("p", { class: "play-question", text: q.text }), left);
        parts.push(h("div", { class: "play-options" }, ...q.choices.map((choice, c) => {
          const isRight = state.phase === "reveal" && state.answer === c;
          const isMine = mine === c;
          return h("button", { type: "button", class: `btn ${isMine ? "" : "secondary"} play-option${isRight ? " right" : ""}`, disabled: state.phase !== "question" || mine !== null || !state.me || quizmaster, text: `${"ABCD"[c]}. ${choice}${isRight ? "  ✓ right answer" : ""}${isMine && state.phase === "reveal" && !isRight ? "  (your answer)" : ""}`, onclick: () => answer(c) });
        })));
        if (mine !== null && state.phase === "question") parts.push(h("p", { class: "muted", text: "Answer locked in. Waiting for the others…" }));
        if (state.is_host) parts.push(h("div", { class: "btn-row" }, S.button(state.index + 1 >= state.total ? "Finish" : "Next question", async () => { state = (await post(`/rooms/${roomId}/next`)).room; draw(); }, { kind: "secondary" }), S.button("End the game", async () => { state = (await post(`/rooms/${roomId}/end`)).room; draw(); }, { kind: "ghost" })));
        if (stopClock) stopClock();
        if (state.phase === "question") {
          const timer = setInterval(() => { localLeft = Math.max(0, localLeft - 1); left.textContent = `${localLeft} s`; }, 1000);
          stopClock = () => clearInterval(timer);
        }
      } else {
        const winner = state.players.find((p) => !p.quizmaster);
        parts.push(h("p", { class: "play-winner", text: state.phase === "expired" ? "This game has closed." : winner && winner.score > 0 ? `🏆 ${winner.name} wins with ${winner.score} points!` : "The game is over." }));
        if (state.prize && winner && winner.score > 0 && state.phase !== "expired") parts.push(h("p", { text: `${winner.name} wins the prize: ${state.prize}` }));
      }
      parts.push(S.section("Scores", players));
      holder.replaceChildren(...parts);
    };
    const refresh = async () => {
      const data = (await api(`/rooms/${roomId}?since=${encodeURIComponent(version)}`, { silent: true })).room;
      if (!data.changed && data.phase === (state && state.phase)) return;
      state = data;
      version = data.version;
      draw();
      if (data.phase === "finished" || data.phase === "expired") { if (stopLive) stopLive(); stopLive = null; }
    };
    refresh().catch((error) => holder.replaceChildren(Aira.ui.error(error, () => openRoom(panel, roomId))));
    stopLive = S.live(refresh, 1500);
  }

  // ---------------------------------------------------------------------------
  // Scores
  // ---------------------------------------------------------------------------
  async function scoresTab(panel, route) {
    const game = route.query.game || "";
    const [data, catalog] = await Promise.all([api(`/scores${game ? `?game=${encodeURIComponent(game)}` : ""}`), api("/games")]);
    const select = Aira.ui.select([["", "All games"], ...catalog.games.map((g) => [g.id, g.name]), ["quiz", "Family quizzes"]], game, { "aria-label": "Game" });
    select.addEventListener("change", () => Aira.router.go(["play"], { tab: "scores", game: select.value }));
    const played = data.bests.reduce((sum, b) => sum + b.plays, 0);
    fill(panel,
      S.kpis([{ label: "Play streak", value: plural(data.streak, "day"), hint: "Play any game each day", tone: data.streak ? "good" : "" }, { label: "Games played", value: played }]),
      S.grid(
        S.section("My best scores", S.table({ columns: [{ key: "name", label: "Game", render: (b) => `${GAME_ICONS[b.game] || ""} ${b.name}` }, { key: "level_label", label: "Level" }, { key: "best", label: "Best", num: true }, { key: "plays", label: "Played", num: true }], rows: data.bests, empty: Aira.ui.empty("No scores yet", "Play any game and your best scores show here.", h("div", { class: "btn-row" }, h("a", { class: "btn secondary", href: "#/play?tab=games", text: "Pick a game" }))) })),
        S.section("Family leaderboard", Aira.ui.field("Game", select), S.table({ columns: [{ key: "rank", label: "#", num: true }, { key: "name", label: "Who", render: (r) => `${r.emoji} ${r.name}` }, { key: "best", label: "Best", num: true }, { key: "plays", label: "Played", num: true }], rows: data.family, empty: "Nobody in the family has a score for this yet." }))),
      // Nothing played yet: "My best scores" already says how to start, so the empty list of games is left out.
      data.recent.length ? S.section("My recent games",
        S.table({ columns: [{ key: "finished_at", label: "When", render: (r) => Aira.fmt.ago(r.finished_at) }, { key: "name", label: "Game", render: (r) => `${r.name}${r.daily ? " (daily)" : ""}` }, { key: "level_label", label: "Level" }, { key: "score", label: "Score", num: true, render: (r) => `${r.score}${r.best ? " ★" : ""}` }], rows: data.recent, empty: "No games yet." }),
        played ? h("div", { class: "btn-row" }, S.button("Download all my games (CSV)", () => Aira.download("/api/play/scores?format=csv", "my-games.csv"), { kind: "secondary" })) : null) : null);
  }

  // ---------------------------------------------------------------------------
  // Watch and read list
  // ---------------------------------------------------------------------------
  async function watchTab(panel, route, ask) {
    const status = route.query.status || "";
    const kind = route.query.kind || "";
    const q = route.query.q || "";
    const query = new URLSearchParams();
    if (status) query.set("status", status);
    if (kind) query.set("kind", kind);
    if (q) query.set("q", q);
    const data = await api(`/watch${query.toString() ? `?${query}` : ""}`);
    const people = data.people;
    const itemFields = [
      { name: "kind", label: "What is it", type: "select", options: data.kinds.map((k) => [k, KIND_LABELS[k]]) },
      { name: "title", label: "Title", required: true },
      { name: "creator", label: "By (director, author, host)" },
      { name: "year", label: "Year", type: "number", min: 1800, max: 2100 },
      { name: "link", label: "Link (where to watch, read or listen)", type: "url", placeholder: "https://" },
      { name: "recommended_by", label: "Recommended by", type: "select", options: [["", "Nobody in particular"], ...people.map((p) => [p.id, p.name])] },
    ];
    const go = (changes) => Aira.router.go(["play"], { tab: "watch", status, kind, q, ...changes });
    const rerender = () => watchTab(panel, route);
    const add = () => S.edit({ title: "Add to the list", fields: itemFields, values: { kind: kind || "film" }, submitLabel: "Add", onSave: async (values) => { await post("/watch", values); rerender(); } });
    const edit = (item) => S.edit({ title: `Change ${item.title}`, fields: itemFields, values: { kind: item.kind, title: item.title, creator: item.creator, year: item.year || "", link: item.link, recommended_by: item.recommended_by_id || "" }, submitLabel: "Save", onSave: async (values) => { await api(`/watch/${item.id}`, { method: "PATCH", body: { ...values, year: values.year === "" ? null : values.year } }); rerender(); } });
    const filters = h("div", { class: "btn-row", role: "group", "aria-label": "Show" }, ...[["", "Everything"], ["want", "Want to"], ["doing", "In progress"], ["done", "Finished"]].map(([value, label]) => h("button", { type: "button", class: `btn sm ${value === status ? "" : "secondary"}`, "aria-pressed": String(value === status), text: label, onclick: () => go({ status: value }) })));
    const kindSelect = Aira.ui.select([["", "Every kind"], ...data.kinds.map((k) => [k, `${KIND_LABELS[k]}s`])], kind, { "aria-label": "Kind" });
    kindSelect.addEventListener("change", () => go({ kind: kindSelect.value }));
    const search = h("input", { type: "search", value: q, placeholder: "Search titles and makers", "aria-label": "Search the list", class: "play-search" });
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); go({ q: search.value.trim() }); } });
    search.addEventListener("search", () => { if (!search.value) go({ q: "" }); });
    const pickOne = async () => {
      const { item } = await api(`/watch/pick?together=1${kind ? `&kind=${kind}` : ""}`);
      if (!item) { Aira.toast("Nothing left to pick: add something you want to enjoy."); return; }
      Aira.modal({ title: "How about this?", body: h("div", { class: "play-pick" }, h("strong", { style: { fontSize: "22px" }, text: item.title }), h("span", { class: "muted", text: [KIND_LABELS[item.kind], item.creator, item.year, item.average_rating ? `family rating ★ ${item.average_rating}` : ""].filter(Boolean).join(" · ") })), actions: [{ label: "Pick another", kind: "secondary", onClick: () => { setTimeout(pickOne, 0); } }, { label: "Plan it together", kind: "", onClick: () => { setTimeout(() => plan(item), 0); } }, { label: "Close", kind: "ghost" }] });
    };
    const plan = (item) => S.edit({
      title: `Enjoy ${item.title} together`,
      fields: [{ name: "planned_for", label: "When", type: "datetime-local", required: true }, ...people.filter((p) => !me() || p.id !== me().id).map((p) => ({ name: `who_${p.id}`, label: `${p.emoji} ${p.name}`, type: "checkbox" })), { name: "note", label: "Note", placeholder: "Popcorn at 7!" }],
      submitLabel: "Plan it",
      onSave: async (values) => {
        const when = new Date(values.planned_for);
        if (Number.isNaN(when.getTime())) throw new Error("Choose a date and time.");
        const who = people.filter((p) => values[`who_${p.id}`]).map((p) => p.id);
        await post(`/watch/${item.id}/plans`, { planned_for: when.toISOString(), who, note: values.note });
        rerender();
      },
    });
    const card = (item) => {
      const mine = item.mine || {};
      const stars = h("div", { role: "radiogroup", "aria-label": `My rating for ${item.title}`, class: "play-stars" }, ...[1, 2, 3, 4, 5].map((n) => h("button", { type: "button", role: "radio", "aria-checked": String(mine.rating === n), "aria-label": `${n} star${n === 1 ? "" : "s"}`, class: `play-star${(mine.rating || 0) >= n ? " on" : ""}`, text: "★", onclick: async () => { await post(`/watch/${item.id}/status`, { status: mine.status || "done", rating: n }); rerender(); } })));
      const statusButtons = h("div", { class: "btn-row" }, ...Object.entries(STATUS_LABELS).map(([value, label]) => h("button", { type: "button", class: `btn sm ${mine.status === value ? "" : "secondary"}`, "aria-pressed": String(mine.status === value), text: label, onclick: async () => { await post(`/watch/${item.id}/status`, { status: value }); rerender(); } })));
      return h("article", { class: "card play-watch", dataset: { playItem: item.id } },
        h("div", { class: "play-quiz-title" }, h("strong", { style: { fontSize: "18px" }, text: item.title }), S.pill(KIND_LABELS[item.kind] || item.kind), item.average_rating ? h("span", { class: "muted", text: `★ ${item.average_rating}` }) : null),
        h("span", { class: "muted", text: [item.creator, item.year, item.recommended_by ? `recommended by ${item.recommended_by}` : "", `added by ${item.added_by}`].filter(Boolean).join(" · ") }),
        item.others.length ? h("span", { class: "muted small", text: item.others.map((o) => `${o.name}: ${STATUS_LABELS[o.status] || o.status}${o.rating ? ` ★${o.rating}` : ""}`).join(" · ") }) : null,
        statusButtons, stars,
        ...item.plans.map((p) => h("div", { class: "cloud-banner info play-plan" }, h("strong", { text: `Together: ${p.local}` }), h("span", { text: `${p.who.join(", ")}${p.note ? ` · ${p.note}` : ""}` }), h("button", { type: "button", class: "btn ghost sm", text: "Cancel plan", onclick: async () => { await api(`/watch/plans/${p.id}`, { method: "DELETE" }); rerender(); } }))),
        h("div", { class: "btn-row" },
          S.button("Plan to enjoy it together", () => plan(item), { kind: "secondary sm" }),
          item.link ? h("a", { class: "btn ghost sm", href: item.link, target: "_blank", rel: "noopener noreferrer", text: "Open link" }) : null,
          item.can_change ? S.button("Edit", () => edit(item), { kind: "ghost sm" }) : null,
          item.can_change ? S.button("Remove", async () => { if (await S.confirmDelete(item.title)) { await api(`/watch/${item.id}`, { method: "DELETE" }); rerender(); } }, { kind: "ghost sm" }) : null));
    };
    const filtered = status || kind || q;
    panel.replaceChildren(
      h("div", { class: "play-toolbar" }, filters, h("div", { class: "btn-row" }, S.button("Add something", add), S.button("Pick for tonight", pickOne, { kind: "secondary" }))),
      h("div", { class: "play-toolbar" }, h("div", { class: "btn-row play-filters" }, search, kindSelect, filtered ? h("button", { type: "button", class: "btn ghost sm", text: "Clear", onclick: () => Aira.router.go(["play"], { tab: "watch" }) }) : null), data.items.length ? S.button("Download the list (CSV)", () => Aira.download(`/api/play/watch?format=csv${query.toString() ? `&${query}` : ""}`, "watch-list.csv"), { kind: "ghost sm" }) : null),
      data.items.length ? h("div", { class: "play-cards" }, ...data.items.map(card)) : filtered ? Aira.ui.empty("Nothing matches", "Try another search or show everything.") : Aira.ui.empty("Nothing on the list yet", "Add a film, a show, a book, a podcast or a game the family wants to enjoy.",
        h("div", { class: "btn-row" }, S.button("Add the first one", add, { kind: "secondary" }))));
    if (ask && ask.new === "watch") add();
    else if (ask && ask.open) found(panel.querySelector(`[data-play-item="${CSS.escape(ask.open)}"]`));
  }

  // The thing the search box chose: outlined, in view and focused, until focus moves on.
  function found(node) {
    if (!node) { Aira.toast("That is not on the list any more.", "info"); return; }
    node.classList.add("play-found");
    node.tabIndex = -1;
    node.scrollIntoView({ block: "center" });
    node.focus({ preventScroll: true });
    node.addEventListener("blur", () => node.classList.remove("play-found"), { once: true });
  }

  // ---------------------------------------------------------------------------
  // Search (the box across the top): suggestions as you type, results on Enter
  // ---------------------------------------------------------------------------
  const searchCache = new Map(); // "query|limit" -> { at, promise }: a few seconds, so typing on does not ask again
  function findAll(query, limit) {
    const key = `${query.toLowerCase()}|${limit}`;
    const hit = searchCache.get(key);
    if (hit && Date.now() - hit.at < 5000) return hit.promise;
    const promise = api(`/search?q=${encodeURIComponent(query)}&limit=${limit}`, { silent: true });
    promise.catch(() => searchCache.delete(key));
    searchCache.set(key, { at: Date.now(), promise });
    return promise;
  }
  const dot = (...parts) => parts.filter((part) => part !== undefined && part !== null && part !== "").join(" · ");
  // Each kind of thing found, as rows the search list and the results page both draw.
  const FOUND = [
    ["games", "Games", (g) => ({ label: g.name, sub: dot("Game", g.tagline), icon: "play-dice", href: `#/play?tab=games&game=${encodeURIComponent(g.id)}` })],
    ["quizzes", "Quizzes", (z) => ({ label: z.title, sub: dot("Quiz", plural(z.count, "question"), `by ${z.owner}`, z.shared ? "" : "only me"), icon: "play-quiz", href: `#/play?tab=quizzes&quiz=${encodeURIComponent(z.id)}` })],
    ["watch", "Watch and read", (w) => ({ label: w.title, sub: dot(KIND_LABELS[w.kind] || w.kind, w.creator, w.year), icon: KIND_ICONS[w.kind] || "play-film", href: `#/play?tab=watch&open=${encodeURIComponent(w.id)}` })],
    ["rooms", "Live games", (r) => ({ label: r.title, sub: dot("Live game", `code ${r.code}`, `hosted by ${r.host}`, r.phase === "lobby" ? "waiting to start" : "under way"), icon: "play-live", href: `#/play?tab=live&room=${encodeURIComponent(r.id)}` })],
  ];

  async function searchTab(panel, q) {
    if (!q) { panel.replaceChildren(S.section("", Aira.ui.empty("Type in the search box above", "Find a game, a quiz, something on the watch list or a live game by its room code."))); return; }
    const data = await findAll(q, 25);
    const groups = FOUND.filter(([key]) => (data[key] || []).length);
    if (!groups.length) {
      panel.replaceChildren(S.section("", Aira.ui.empty(`Nothing matches “${q}”`, "Try the name of a game, a quiz or a film, or a four-letter room code.",
        h("div", { class: "btn-row" }, h("a", { class: "btn secondary", href: "#/play?tab=quizzes&new=quiz", text: "Write a quiz" }), h("a", { class: "btn secondary", href: "#/play?tab=watch&new=watch", text: "Add to the watch list" })))));
      return;
    }
    panel.replaceChildren(...groups.map(([key, title, row]) => S.section(`${title} (${data[key].length})`,
      h("ul", { class: "play-results" }, ...data[key].map((item) => {
        const r = row(item);
        return h("li", {}, h("a", { class: "play-result", href: r.href }, h("span", { class: "play-result-icon", "aria-hidden": "true" }, Aira.icon(r.icon)), h("span", { class: "play-result-text" }, h("strong", { text: r.label }), h("span", { class: "muted small", text: r.sub }))));
      })),
      key === "watch" ? h("p", { class: "small" }, h("a", { href: `#/play?tab=watch&q=${encodeURIComponent(q)}`, text: "See these on the watch list" })) : null)));
  }

  Aira.appSearch = Aira.appSearch || {};
  Aira.appSearch.play = {
    placeholder: "Search games, quizzes, films and books",
    // Up to six, one of each kind in turn, so a long watch list does not hide the quizzes.
    async suggest(query) {
      const data = await findAll(query, 4);
      const lanes = FOUND.map(([key, , row]) => (data[key] || []).map(row));
      const out = [];
      for (let round = 0; out.length < 6 && lanes.some((lane) => lane.length > round); round += 1) for (const lane of lanes) if (lane[round] && out.length < 6) out.push(lane[round]);
      return out;
    },
    run(query) { Aira.router.go(["play"], { tab: "search", q: query }); },
  };

  // ---------------------------------------------------------------------------
  // "+ New" (above the left menu): each opens the same form as the button on its tab
  // ---------------------------------------------------------------------------
  Aira.appNew = Aira.appNew || {};
  Aira.appNew.play = () => [
    { label: "Write a quiz", icon: "play-pen", href: "#/play?tab=quizzes&new=quiz" },
    { label: "Host live trivia", icon: "play-live", href: "#/play?tab=live&new=room" },
    // Quiz nights take money from guests: grown-ups only, like the entry fee on the form (before the profile has
    // loaded it is offered; the form then shows the fee only to a grown-up).
    !me() || isAdult() ? { label: "Run a paid quiz night", icon: "play-trophy", href: "#/play?tab=live&new=night" } : null,
    { label: "Add to the watch list", icon: "play-film", href: "#/play?tab=watch&new=watch" },
  ].filter(Boolean);

  // ---------------------------------------------------------------------------
  // What "+ New" or the search box asked for (?new=, ?quiz=, ?open=, ?game= on Games): taken off the address first,
  // so Back or a reload does not ask again, and answered by the tab drawn next.
  const NEW_TAB = { quiz: "quizzes", room: "live", night: "live", watch: "watch" };
  let pending = null;

  // Pressing the folder you are on in the left menu (Games in the middle of a game) goes back to its start, like
  // the tab bar below.
  document.addEventListener("click", (event) => {
    const link = event.target.closest && event.target.closest('.shell-subnav[data-section="play"] a.shell-sublink');
    if (link && link.getAttribute("href") === location.hash) { event.preventDefault(); stopTimers(); Aira.router.render(); }
  });

  Aira.views.play = async (main, route) => {
    stopTimers();
    const query = route.query;
    const onGames = !query.tab || query.tab === "games";
    if (query.new || query.quiz || query.open || (onGames && query.game)) {
      pending = { new: String(query.new || ""), quiz: String(query.quiz || ""), open: String(query.open || ""), game: onGames ? String(query.game || "") : "" };
      const rest = { ...query };
      delete rest.new;
      delete rest.quiz;
      delete rest.open;
      if (onGames) delete rest.game;
      if (pending.new && !rest.tab) rest.tab = NEW_TAB[pending.new] || "";
      Aira.router.go(["play"], rest, { replace: true });
      return;
    }
    let ask = pending;
    pending = null;
    const searching = query.tab === "search";
    const q = String(query.q || "").trim();
    const meta = TABS.find((t) => t.id === query.tab) || TABS[0];
    const title = searching ? (q ? `Results for “${q}”` : "Search") : meta.label;
    const body = S.page(main, { title, subtitle: searching ? "Games, quizzes, the watch list and live games that match." : meta.about });
    document.title = `${title} · Play · Aira`;
    body.classList.add("play-app");
    const tab = S.tabs(body, route, "play", TABS, searching ? "search" : meta.id);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      const asked = ask;
      ask = null;
      panel.replaceChildren(Aira.ui.skeleton(3));
      try {
        if (tab === "search") await searchTab(panel, q);
        else if (tab === "daily") await dailyTab(panel);
        else if (tab === "quizzes") await quizzesTab(panel, asked);
        else if (tab === "live") await liveTab(panel, route, asked);
        else if (tab === "scores") await scoresTab(panel, route);
        else if (tab === "watch") await watchTab(panel, route, asked);
        else await gamesTab(panel, asked);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    // Pressing the tab you are on (for example Games, in the middle of a game) goes back to its start.
    const bar = body.querySelector(".tabs");
    if (bar) bar.addEventListener("click", (event) => { const button = event.target.closest("button[role=tab]"); if (button && button.getAttribute("aria-selected") === "true") { stopTimers(); render(); } });
    await render();
  };
})();

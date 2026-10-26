/* Aira — Play (#/play): games, the daily challenge, live family trivia, scores and the watch list.
 *
 * Tabs (?tab=): games (big tiles; choose a level and relaxed or timed, then play), daily (today's challenge for
 * everyone and the family board), live (host or join live trivia; the room updates every 1.5 s), scores
 * (personal bests, streak, family leaderboard) and watch (films, shows, books, podcasts and games to enjoy
 * together). Everything runs through /api/play/* (aira/suite/play). The browser never sends a score: it sends moves
 * and answers, and the server works the score out. DOM is built with h() and text only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("play", path, opts);
  const post = (path, body = {}) => api(path, { method: "POST", body });

  const TABS = [
    { id: "games", label: "Games" },
    { id: "daily", label: "Daily challenge" },
    { id: "live", label: "Live trivia" },
    { id: "scores", label: "Scores" },
    { id: "watch", label: "Watch and read" },
  ];
  const GAME_ICONS = { memory: "🃏", scramble: "🔤", maths: "➕", trivia: "❓", sudoku: "🔢", word: "🟩" };
  const KIND_LABELS = { film: "Film", show: "Show", book: "Book", podcast: "Podcast", game: "Game" };
  const STATUS_LABELS = { want: "Want to", doing: "Watching / reading", done: "Finished" };
  const reduceMotion = () => document.body.classList.contains("reduce-motion") || (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  let stopLive = null;
  let stopClock = null;
  function stopTimers() {
    if (stopLive) { stopLive(); stopLive = null; }
    if (stopClock) { stopClock(); stopClock = null; }
  }

  const big = { minHeight: "52px", fontSize: "17px" };
  const tileStyle = { display: "grid", gap: "6px", justifyItems: "start", padding: "18px", border: "1px solid var(--line)", borderRadius: "18px", background: "var(--surface)", color: "var(--ink)", textAlign: "left", cursor: "pointer", font: "inherit" };

  // ---------------------------------------------------------------------------
  // Games: choose, play, result
  // ---------------------------------------------------------------------------
  async function gamesTab(panel) {
    const catalog = await api("/games");
    const tiles = catalog.games.map((game) => h("button", { type: "button", class: "play-tile", style: tileStyle, onclick: () => setup(panel, catalog, game) },
      h("span", { style: { fontSize: "40px" }, "aria-hidden": "true", text: GAME_ICONS[game.id] || "🎲" }),
      h("strong", { style: { fontSize: "18px" }, text: game.name }),
      h("span", { class: "muted", text: game.tagline })));
    panel.replaceChildren(
      h("p", { class: "muted", text: "Pick a game. Every game can be played without a clock." }),
      h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: "12px" } }, ...tiles));
  }

  function choiceRow(label, options, selected, onPick) {
    const group = h("div", { role: "radiogroup", "aria-label": label, style: { display: "flex", flexWrap: "wrap", gap: "8px" } });
    const render = (value) => group.replaceChildren(...options.map((option) => h("button", { type: "button", role: "radio", "aria-checked": String(option.id === value), class: `btn ${option.id === value ? "" : "secondary"}`, style: big, onclick: () => { onPick(option.id); render(option.id); }, text: option.label })));
    render(selected);
    return h("div", { style: { display: "grid", gap: "6px" } }, h("strong", { text: label }), group);
  }

  function setup(panel, catalog, game) {
    stopTimers();
    let level = game.levels.some((l) => l.id === catalog.suggested_level) ? catalog.suggested_level : game.levels[0].id;
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
    return h("div", { style: { display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "baseline", justifyContent: "space-between" } },
      h("h2", { class: "suite-h2", style: { fontSize: "20px" }, text: `${GAME_ICONS[session.game] || ""} ${session.name}${session.daily_date ? " · today's challenge" : ""}` }),
      h("span", {}, S.pill(session.mode === "timed" ? "timed" : "relaxed", { timed: "warn", relaxed: "good" }), " ", clock));
  }

  function play(panel, session) {
    stopTimers();
    const area = h("div", { style: { display: "grid", gap: "14px" } });
    panel.replaceChildren(S.section("", header(session), area));
    const done = (finished) => result(panel, finished);
    const renderers = { memory: memoryGame, scramble: answersGame, maths: answersGame, trivia: triviaGame, sudoku: sudokuGame, word: wordGame };
    (renderers[session.game] || answersGame)(area, session, done);
  }

  function result(panel, session) {
    stopTimers();
    const details = [];
    const r = session.result || {};
    if (r.total) details.push(`${r.correct} of ${r.total} right`);
    if (r.moves) details.push(`${r.moves} moves for ${r.pairs} pairs${r.perfect ? " (perfect!)" : ""}`);
    if (r.tries) details.push(r.solved ? `Found in ${r.tries} ${r.tries === 1 ? "try" : "tries"}` : `The word was ${String(r.answer || "").toUpperCase()}`);
    if (r.hints) details.push(`${r.hints} hint${r.hints === 1 ? "" : "s"} used`);
    if (session.mode === "timed" && r.seconds !== undefined) details.push(`${r.seconds} seconds`);
    const review = [];
    if (Array.isArray(r.marks) && (r.words || r.answers)) {
      const shown = session.game === "trivia" ? session.puzzle.questions.map((q, i) => `${q.text} → ${q.choices[r.answers[i]]}`) : (r.words || r.answers).map((a, i) => `${session.game === "maths" ? session.puzzle.questions[i] : session.puzzle.rounds[i].letters} → ${String(a).toUpperCase()}`);
      review.push(h("ol", { style: { margin: 0, paddingLeft: "22px", display: "grid", gap: "4px" } }, ...shown.map((text, i) => h("li", { text: `${r.marks[i] ? "✓" : "✗"} ${text}` }))));
    }
    panel.replaceChildren(S.section(session.best ? "New personal best!" : "Well played!",
      h("p", { style: { fontSize: "44px", fontWeight: "800", margin: "0" }, text: `${session.score} points` }),
      details.length ? h("p", { class: "muted", style: { fontSize: "17px" }, text: details.join(" · ") }) : null,
      ...review,
      h("div", { class: "btn-row" },
        session.daily_date ? h("a", { class: "btn", href: "#/play?tab=daily", text: "See the family board" }) : S.button("Play again", async () => { play(panel, (await api("/sessions", { method: "POST", body: { game: session.game, level: session.level, mode: session.mode } })).session); }),
        h("button", { type: "button", class: "btn secondary", text: "Back to games", onclick: () => gamesTab(panel) }),
        h("a", { class: "btn ghost", href: "#/play?tab=scores", text: "My scores" }))));
    if (Aira.life && Aira.life.prefs && (Aira.life.prefs.read_aloud || Aira.life.prefs.easy_mode)) Aira.life.speak(`${session.best ? "New personal best! " : ""}${session.score} points.`);
  }

  const showError = (node, error) => { node.textContent = error && error.message ? error.message : String(error); node.hidden = false; };

  // Memory: the server shows each card as it is turned (the layout never reaches the browser).
  function memoryGame(area, session, done) {
    const cards = session.puzzle.cards;
    const known = { ...(session.progress.matched || {}) };
    let open = session.progress.pending ? { [session.progress.pending.index]: session.progress.pending.card } : {};
    let busy = false;
    const status = h("p", { class: "muted", "aria-live": "polite", text: `Find the ${session.puzzle.pairs} pairs. Moves: ${session.progress.moves || 0}` });
    const columns = cards <= 16 || window.innerWidth < 640 ? 4 : 6; // four across on a phone
    const board = h("div", { role: "grid", "aria-label": "Cards", style: { display: "grid", gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: "10px", maxWidth: "620px" } });
    const render = () => {
      board.replaceChildren(...Array.from({ length: cards }, (_, index) => {
        const face = known[index] || open[index];
        return h("button", { type: "button", "aria-label": face ? `Card ${index + 1}: ${face}` : `Card ${index + 1}, face down`, disabled: !!known[index], style: { aspectRatio: "1", width: "100%", minWidth: "0", minHeight: "56px", padding: "0", fontSize: "clamp(24px, 6vw, 44px)", borderRadius: "14px", border: `2px solid ${known[index] ? "var(--good)" : "var(--line-strong)"}`, background: face ? "var(--surface)" : "var(--brand-soft)", color: "var(--ink)", cursor: known[index] ? "default" : "pointer" }, text: face || "?", onclick: () => flip(index) });
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
    const inputs = items.map((_, i) => h("input", { type: isMaths ? "text" : "text", inputmode: isMaths ? "numeric" : "text", autocomplete: "off", "aria-label": `Answer ${i + 1}`, style: { ...big, maxWidth: "220px" } }));
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const list = h("ol", { style: { display: "grid", gap: "12px", paddingLeft: "22px", margin: 0 } }, ...items.map((text, i) => h("li", {},
      h("div", { style: { display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center" } },
        h("span", { style: { fontSize: "22px", fontWeight: "700", letterSpacing: isMaths ? "0" : "0.08em", minWidth: "170px" }, text: isMaths ? `${text} =` : text }), inputs[i]))));
    inputs.forEach((input, i) => input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); (inputs[i + 1] || submit).focus(); } }));
    const submit = h("button", { type: "button", class: "btn", style: big, text: "Check my answers", onclick: async (event) => {
      await Aira.withBusy(event.currentTarget, async () => {
        try { done((await post(`/sessions/${session.id}/finish`, { answers: inputs.map((input) => input.value.trim()) })).session); } catch (err) { showError(error, err); }
      });
    } });
    area.append(h("p", { class: "muted", text: isMaths ? "Type each answer, then check." : "Unscramble each word, then check." }), list, error, h("div", { class: "btn-row" }, submit));
    if (inputs[0]) inputs[0].focus();
  }

  // Trivia: one question at a time, all answers checked at the end.
  function triviaGame(area, session, done) {
    const questions = session.puzzle.questions;
    const answers = [];
    const render = (index) => {
      const q = questions[index];
      area.replaceChildren(
        h("p", { class: "muted", text: `Question ${index + 1} of ${questions.length} · ${q.category}` }),
        h("p", { style: { fontSize: "22px", fontWeight: "700", margin: 0 }, text: q.text }),
        h("div", { style: { display: "grid", gap: "10px", maxWidth: "640px" } }, ...q.choices.map((choice, c) => h("button", { type: "button", class: "btn secondary", style: { ...big, justifyContent: "flex-start", whiteSpace: "normal", textAlign: "left" }, text: `${"ABCD"[c]}. ${choice}`, onclick: async (event) => {
          answers[index] = c;
          if (index + 1 < questions.length) { render(index + 1); return; }
          await Aira.withBusy(event.currentTarget, async () => { try { done((await post(`/sessions/${session.id}/finish`, { answers })).session); } catch (error) { Aira.reportError(error); } });
        } }))));
      const first = area.querySelector("button");
      if (first) first.focus();
    };
    render(0);
  }

  // Sudoku: fill the grid; a hint fills one square (and costs a few points); the server checks the grid.
  function sudokuGame(area, session, done) {
    const size = session.puzzle.size;
    const box = size === 4 ? 2 : 3;
    const given = session.puzzle.grid;
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const cells = given.map((value, i) => {
      const row = Math.floor(i / size);
      const col = i % size;
      const input = h("input", { type: "text", inputmode: "numeric", maxlength: "1", value: value ? String(value) : "", readonly: !!value, "aria-label": `Row ${row + 1}, column ${col + 1}${value ? `, given ${value}` : ""}`, style: { width: "100%", aspectRatio: "1", minHeight: "0", padding: "0", textAlign: "center", fontSize: size === 4 ? "28px" : "clamp(16px, 4vw, 24px)", fontWeight: value ? "800" : "500", border: "1px solid var(--line)", borderRadius: "0", background: value ? "var(--surface-2)" : "var(--surface)", color: "var(--ink)", borderRight: (col + 1) % box === 0 && col + 1 < size ? "3px solid var(--line-strong)" : undefined, borderBottom: (row + 1) % box === 0 && row + 1 < size ? "3px solid var(--line-strong)" : undefined } });
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
      h("div", { style: { display: "grid", gridTemplateColumns: `repeat(${size}, 1fr)`, maxWidth: size === 4 ? "280px" : "460px", border: "3px solid var(--line-strong)" } }, ...cells),
      error,
      h("div", { class: "btn-row" },
        S.button("Check", async () => { try { done((await post(`/sessions/${session.id}/finish`, { grid: grid() })).session); } catch (err) { showError(error, err); } }),
        S.button("Hint", async () => { const res = await post(`/sessions/${session.id}/move`, { action: "hint", grid: grid() }); if (res.hint) { cells[res.hint.index].value = String(res.hint.value); cells[res.hint.index].focus(); } }, { kind: "secondary" })));
  }

  // Word of the day: guesses checked on the server; the colours also come with words for screen readers.
  function wordGame(area, session, done) {
    const tries = session.puzzle.tries;
    const guesses = [...(session.progress.guesses || [])];
    const colours = { correct: "var(--good)", present: "var(--warning)", absent: "var(--muted)" };
    const words = { correct: "right place", present: "in the word", absent: "not in the word" };
    const board = h("div", { style: { display: "grid", gap: "8px" } });
    const input = h("input", { type: "text", maxlength: "5", autocomplete: "off", autocapitalize: "characters", "aria-label": "Your five-letter guess", style: { ...big, maxWidth: "200px", textTransform: "uppercase", letterSpacing: "0.2em", fontSize: "22px" } });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const render = () => {
      board.replaceChildren(...Array.from({ length: tries }, (_, row) => {
        const guess = guesses[row];
        return h("div", { role: "row", "aria-label": guess ? `${guess.word.toUpperCase()}: ${guess.feedback.map((f, i) => `${guess.word[i].toUpperCase()} ${words[f]}`).join(", ")}` : `Try ${row + 1}`, style: { display: "flex", gap: "6px" } },
          ...Array.from({ length: 5 }, (_, col) => h("span", { "aria-hidden": "true", style: { display: "grid", placeItems: "center", width: "52px", height: "52px", borderRadius: "8px", border: "2px solid var(--line-strong)", fontSize: "24px", fontWeight: "800", color: guess ? "#fff" : "var(--ink)", background: guess ? colours[guess.feedback[col]] : "var(--surface)" }, text: guess ? guess.word[col].toUpperCase() : "" })));
      }));
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
    area.append(h("p", { class: "muted", text: "Green: right letter, right place. Yellow: in the word, wrong place. Grey: not in the word." }), board, h("div", { class: "btn-row" }, input, S.button("Guess", send)), error);
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
      S.kpis([{ label: "Today's game", value: `${GAME_ICONS[data.game] || ""} ${data.name}` }, { label: "My score today", value: finished ? mine.score : "Not played", tone: finished ? "good" : "" }, { label: "Play streak", value: `${data.streak} day${data.streak === 1 ? "" : "s"}` }]),
      S.section("Today's challenge", h("p", { class: "muted", text: `${data.tagline}. Everyone gets the same puzzle today, with no clock, and one try each.` }),
        finished ? h("p", { style: { fontSize: "18px" }, text: `You scored ${mine.score}. Come back tomorrow for a new one!` }) : h("div", { class: "btn-row" }, S.button(mine ? "Carry on" : "Play today's challenge", async () => { play(panel, (await api("/sessions", { method: "POST", body: { daily: true } })).session); }))),
      S.section("Family board today", S.table({ columns: [{ key: "rank", label: "#", num: true }, { key: "name", label: "Who", render: (r) => `${r.emoji} ${r.name}` }, { key: "score", label: "Score", num: true }], rows: data.board, empty: "Nobody has played yet today. Be the first!" })));
  }

  // ---------------------------------------------------------------------------
  // Live trivia
  // ---------------------------------------------------------------------------
  async function liveTab(panel) {
    stopTimers();
    const [{ rooms }, catalog] = await Promise.all([api("/rooms"), api("/games")]);
    const code = h("input", { type: "text", maxlength: "4", autocomplete: "off", "aria-label": "Room code", placeholder: "ABCD", style: { ...big, maxWidth: "140px", textTransform: "uppercase", letterSpacing: "0.2em" } });
    const joinError = h("p", { class: "form-error", role: "alert", hidden: true });
    const joinCode = async () => { try { const room = (await post("/rooms/join", { code: code.value.trim() })).room; openRoom(panel, room.id); } catch (err) { showError(joinError, err); } };
    code.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); joinCode(); } });
    const host = () => S.edit({
      title: "Host live trivia",
      fields: [
        { name: "title", label: "Name of the game", placeholder: "Sunday quiz" },
        { name: "category", label: "Questions about", type: "select", options: [["", "A bit of everything"], ...catalog.categories.map((c) => [c.id, c.label])] },
        { name: "questions", label: "How many questions", type: "number", min: 3, max: 15 },
        { name: "seconds", label: "Seconds for each question", type: "number", min: 10, max: 120, hint: "Give everyone time to read. 45 is relaxed, 20 is quick." },
        { name: "auto_advance", label: "Move on by itself (otherwise the host presses Next)", type: "checkbox" },
      ],
      values: { questions: 8, seconds: catalog.default_mode === "relaxed" ? 45 : 30, auto_advance: true },
      submitLabel: "Open the room",
      onSave: async (values) => { const room = (await post("/rooms", values)).room; openRoom(panel, room.id); },
    });
    panel.replaceChildren(
      S.grid(
        S.section("Host a game", h("p", { class: "muted", text: "Everyone at home plays on their own phone or computer. Relatives can join with a link." }), h("div", { class: "btn-row" }, S.button("Host live trivia", host))),
        S.section("Join with a code", h("div", { class: "btn-row" }, code, S.button("Join", joinCode)), joinError)),
      S.section("Games happening now", rooms.length ? h("ul", { style: { listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "10px" } }, ...rooms.map((room) => h("li", { style: { display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center" } },
        h("div", { style: { flex: "1", minWidth: "180px" } }, h("strong", { text: room.title }), h("div", { class: "muted", text: `Hosted by ${room.host} · code ${room.code} · ${room.players} playing · ${room.phase === "lobby" ? "waiting to start" : "under way"}` })),
        S.button(room.joined ? "Open" : "Join", async () => { if (!room.joined) await post(`/rooms/${room.id}/join`); openRoom(panel, room.id); })))) : Aira.ui.empty("No games right now", "Host one and the family will be invited.")));
  }

  function openRoom(panel, roomId) {
    stopTimers();
    let version = "";
    let state = null;
    let localLeft = 0;
    const holder = h("div", { style: { display: "grid", gap: "14px" } });
    panel.replaceChildren(h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn ghost", text: "← All games", onclick: () => liveTab(panel) })), holder);
    const answer = async (choice) => { try { state = (await post(`/rooms/${roomId}/answer`, { choice })).room; version = ""; draw(); } catch (error) { Aira.reportError(error); } };
    const draw = () => {
      if (!state) return;
      const players = h("ol", { style: { margin: 0, paddingLeft: "22px", display: "grid", gap: "4px", fontSize: "17px" } }, ...state.players.map((p) => h("li", { text: `${p.emoji} ${p.name}${p.guest ? " (guest)" : ""}: ${p.score}${state.phase === "question" && p.answered ? " ✓" : ""}` })));
      const parts = [h("div", { style: { display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "baseline" } }, h("h2", { class: "suite-h2", style: { fontSize: "22px" }, text: state.title }), h("span", { class: "muted", text: `Code ${state.code} · host ${state.host}` }))];
      if (state.phase === "lobby") {
        parts.push(h("p", { style: { fontSize: "20px" }, text: `Waiting for players. Tell them the code: ${state.code}` }));
        if (state.is_host) {
          parts.push(h("div", { class: "btn-row" }, S.button("Start the game", async () => { state = (await post(`/rooms/${roomId}/start`)).room; draw(); })));
          const child = Aira.life && Aira.life.me && Aira.life.me.age_group === "child";
          if (!child) parts.push(S.share({ title: "Invite relatives", text: "Anyone with this link can join with their name, without an account.", link: state.link, onCreate: async () => (await post(`/rooms/${roomId}/link`)).link, onRevoke: async () => { await api(`/rooms/${roomId}/link`, { method: "DELETE" }); } }));
        }
      } else if (state.phase === "question" || state.phase === "reveal") {
        const q = state.question;
        const mine = state.me ? state.me.choice : null;
        localLeft = state.seconds_left;
        const left = h("strong", { "aria-live": "off", style: { fontSize: "22px" }, text: state.phase === "question" ? `${localLeft} s` : "Time's up" });
        parts.push(h("p", { class: "muted", text: `Question ${state.index + 1} of ${state.total} · ${q.category}` }), h("p", { style: { fontSize: "24px", fontWeight: "700", margin: 0 }, text: q.text }), left);
        parts.push(h("div", { style: { display: "grid", gap: "10px", maxWidth: "640px" } }, ...q.choices.map((choice, c) => {
          const isRight = state.phase === "reveal" && state.answer === c;
          const isMine = mine === c;
          return h("button", { type: "button", class: `btn ${isMine ? "" : "secondary"}`, disabled: state.phase !== "question" || mine !== null || !state.me, style: { ...big, justifyContent: "flex-start", whiteSpace: "normal", textAlign: "left", outline: isRight ? "3px solid var(--good)" : undefined }, text: `${"ABCD"[c]}. ${choice}${isRight ? "  ✓ right answer" : ""}${isMine && state.phase === "reveal" && !isRight ? "  (your answer)" : ""}`, onclick: () => answer(c) });
        })));
        if (mine !== null && state.phase === "question") parts.push(h("p", { class: "muted", text: "Answer locked in. Waiting for the others…" }));
        if (state.is_host) parts.push(h("div", { class: "btn-row" }, S.button(state.index + 1 >= state.total ? "Finish" : "Next question", async () => { state = (await post(`/rooms/${roomId}/next`)).room; draw(); }, { kind: "secondary" }), S.button("End the game", async () => { state = (await post(`/rooms/${roomId}/end`)).room; draw(); }, { kind: "ghost" })));
        if (stopClock) stopClock();
        if (state.phase === "question") {
          const timer = setInterval(() => { localLeft = Math.max(0, localLeft - 1); left.textContent = `${localLeft} s`; }, 1000);
          stopClock = () => clearInterval(timer);
        }
      } else {
        const winner = state.players[0];
        parts.push(h("p", { style: { fontSize: "26px", fontWeight: "800" }, text: state.phase === "expired" ? "This game has closed." : winner ? `🏆 ${winner.name} wins with ${winner.score} points!` : "The game is over." }));
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
    refresh().catch(Aira.reportError);
    stopLive = S.live(refresh, 1500);
  }

  // ---------------------------------------------------------------------------
  // Scores
  // ---------------------------------------------------------------------------
  async function scoresTab(panel, route) {
    const game = route.query.game || "";
    const [data, catalog] = await Promise.all([api(`/scores${game ? `?game=${encodeURIComponent(game)}` : ""}`), api("/games")]);
    const select = Aira.ui.select([["", "All games"], ...catalog.games.map((g) => [g.id, g.name])], game, { "aria-label": "Game" });
    select.addEventListener("change", () => Aira.router.go(["play"], { tab: "scores", game: select.value }));
    panel.replaceChildren(
      S.kpis([{ label: "Play streak", value: `${data.streak} day${data.streak === 1 ? "" : "s"}`, hint: "Play any game each day", tone: data.streak ? "good" : "" }, { label: "Games played", value: data.bests.reduce((sum, b) => sum + b.plays, 0) }]),
      S.grid(
        S.section("My best scores", S.table({ columns: [{ key: "name", label: "Game", render: (b) => `${GAME_ICONS[b.game] || ""} ${b.name}` }, { key: "level", label: "Level" }, { key: "best", label: "Best", num: true }, { key: "plays", label: "Played", num: true }], rows: data.bests, empty: "Play a game to see your scores here." })),
        S.section("Family leaderboard", Aira.ui.field("Game", select), S.table({ columns: [{ key: "rank", label: "#", num: true }, { key: "name", label: "Who", render: (r) => `${r.emoji} ${r.name}` }, { key: "best", label: "Best", num: true }, { key: "plays", label: "Played", num: true }], rows: data.family, empty: "No scores yet." }))),
      S.section("My recent games", S.table({ columns: [{ key: "finished_at", label: "When", render: (r) => Aira.fmt.ago(r.finished_at) }, { key: "name", label: "Game", render: (r) => `${r.name}${r.daily ? " (daily)" : ""}` }, { key: "score", label: "Score", num: true, render: (r) => `${r.score}${r.best ? " ★" : ""}` }], rows: data.recent, empty: "No games yet." })));
  }

  // ---------------------------------------------------------------------------
  // Watch and read list
  // ---------------------------------------------------------------------------
  async function watchTab(panel, route) {
    const status = route.query.status || "";
    const data = await api(`/watch${status ? `?status=${status}` : ""}`);
    const people = data.people;
    const itemFields = [
      { name: "kind", label: "What is it", type: "select", options: data.kinds.map((k) => [k, KIND_LABELS[k]]) },
      { name: "title", label: "Title", required: true },
      { name: "creator", label: "By (director, author, host)" },
      { name: "year", label: "Year", type: "number", min: 1800, max: 2100 },
      { name: "link", label: "Link (where to watch, read or listen)", type: "url", placeholder: "https://" },
      { name: "recommended_by", label: "Recommended by", type: "select", options: [["", "Nobody in particular"], ...people.map((p) => [p.id, p.name])] },
    ];
    const add = () => S.edit({ title: "Add to the list", fields: itemFields, values: { kind: "film" }, submitLabel: "Add", onSave: async (values) => { await post("/watch", values); watchTab(panel, route); } });
    const filters = h("div", { class: "btn-row", role: "group", "aria-label": "Show" }, ...[["", "Everything"], ["want", "Want to"], ["doing", "In progress"], ["done", "Finished"]].map(([value, label]) => h("a", { class: `btn sm ${value === status ? "" : "secondary"}`, href: `#/play?tab=watch${value ? `&status=${value}` : ""}`, text: label })));
    const card = (item) => {
      const mine = item.mine || {};
      const stars = h("div", { role: "radiogroup", "aria-label": `My rating for ${item.title}`, style: { display: "flex", gap: "4px" } }, ...[1, 2, 3, 4, 5].map((n) => h("button", { type: "button", role: "radio", "aria-checked": String(mine.rating === n), "aria-label": `${n} star${n === 1 ? "" : "s"}`, style: { minWidth: "44px", minHeight: "44px", fontSize: "24px", border: "none", background: "transparent", cursor: "pointer", color: (mine.rating || 0) >= n ? "var(--warning)" : "var(--line-strong)" }, text: "★", onclick: async () => { await post(`/watch/${item.id}/status`, { status: mine.status || "done", rating: n }); watchTab(panel, route); } })));
      const statusButtons = h("div", { class: "btn-row" }, ...Object.entries(STATUS_LABELS).map(([value, label]) => h("button", { type: "button", class: `btn sm ${mine.status === value ? "" : "secondary"}`, "aria-pressed": String(mine.status === value), text: label, onclick: async () => { await post(`/watch/${item.id}/status`, { status: value }); watchTab(panel, route); } })));
      const plan = () => S.edit({
        title: `Enjoy ${item.title} together`,
        fields: [{ name: "planned_for", label: "When", type: "datetime-local", required: true }, ...people.filter((p) => !Aira.life || !Aira.life.me || p.id !== Aira.life.me.id).map((p) => ({ name: `who_${p.id}`, label: `${p.emoji} ${p.name}`, type: "checkbox" })), { name: "note", label: "Note", placeholder: "Popcorn at 7!" }],
        submitLabel: "Plan it",
        onSave: async (values) => {
          const who = people.filter((p) => values[`who_${p.id}`]).map((p) => p.id);
          await post(`/watch/${item.id}/plans`, { planned_for: new Date(values.planned_for).toISOString(), who, note: values.note });
          watchTab(panel, route);
        },
      });
      return h("article", { class: "card", style: { display: "grid", gap: "8px" } },
        h("div", { style: { display: "flex", gap: "10px", alignItems: "baseline", flexWrap: "wrap" } }, h("strong", { style: { fontSize: "18px" }, text: item.title }), S.pill(KIND_LABELS[item.kind] || item.kind), item.average_rating ? h("span", { class: "muted", text: `★ ${item.average_rating}` }) : null),
        h("span", { class: "muted", text: [item.creator, item.year, item.recommended_by ? `recommended by ${item.recommended_by}` : "", `added by ${item.added_by}`].filter(Boolean).join(" · ") }),
        item.others.length ? h("span", { class: "muted small", text: item.others.map((o) => `${o.name}: ${STATUS_LABELS[o.status] || o.status}${o.rating ? ` ★${o.rating}` : ""}`).join(" · ") }) : null,
        statusButtons, stars,
        ...item.plans.map((p) => h("div", { class: "cloud-banner info" }, h("strong", { text: `Together: ${p.local}` }), h("span", { text: `${p.who.join(", ")}${p.note ? ` · ${p.note}` : ""}` }), h("button", { type: "button", class: "btn ghost sm", text: "Cancel plan", onclick: async () => { await api(`/watch/plans/${p.id}`, { method: "DELETE" }); watchTab(panel, route); } }))),
        h("div", { class: "btn-row" },
          S.button("Plan to enjoy it together", plan, { kind: "secondary sm" }),
          item.link ? h("a", { class: "btn ghost sm", href: item.link, target: "_blank", rel: "noopener noreferrer", text: "Open link" }) : null,
          item.can_change ? S.button("Remove", async () => { if (await S.confirmDelete(item.title)) { await api(`/watch/${item.id}`, { method: "DELETE" }); watchTab(panel, route); } }, { kind: "ghost sm" }) : null));
    };
    panel.replaceChildren(
      h("div", { style: { display: "flex", flexWrap: "wrap", gap: "10px", justifyContent: "space-between", alignItems: "center" } }, filters, S.button("Add something", add)),
      data.items.length ? h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: "12px" } }, ...data.items.map(card)) : Aira.ui.empty("Nothing on the list yet", "Add a film, a show, a book, a podcast or a game the family wants to enjoy."));
  }

  // ---------------------------------------------------------------------------
  Aira.views.play = async (main, route) => {
    stopTimers();
    const body = S.page(main, { title: "Play", subtitle: "Games, quizzes and things to enjoy together." });
    const tab = S.tabs(body, route, "play", TABS);
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const render = async () => {
      panel.replaceChildren(Aira.ui.skeleton(3));
      try {
        if (tab === "daily") await dailyTab(panel);
        else if (tab === "live") await liveTab(panel);
        else if (tab === "scores") await scoresTab(panel, route);
        else if (tab === "watch") await watchTab(panel, route);
        else await gamesTab(panel);
      } catch (error) { panel.replaceChildren(Aira.ui.error(error, render)); }
    };
    await render();
  };
})();

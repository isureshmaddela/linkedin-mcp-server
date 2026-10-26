/* Aira — Play: the live trivia page relatives open from a link (aira/suite/play, kind "trivia_room").
 *
 * A visitor types their name to join (the server gives this browser a token that lets it answer), then the page
 * asks for the room every 2.5 seconds: waiting room, the question with big answer buttons, the right answer, and
 * the final scores. Answers after the time are refused by the server. Text only, no HTML from the server.
 */
"use strict";
(() => {
  if (!window.AiraPublic) return;
  const keyFor = (token) => `aira-play-room:${token}`;
  const remember = (token, value) => { try { if (value) sessionStorage.setItem(keyFor(token), value); else sessionStorage.removeItem(keyFor(token)); } catch { /* private window */ } };
  const recall = (token) => { try { return sessionStorage.getItem(keyFor(token)) || ""; } catch { return ""; } };

  window.AiraPublic.register("play", "trivia_room", (root, data, ctx) => {
    const { h } = ctx;
    let playerToken = recall(ctx.token);
    let state = data.page.room;
    let timer = 0;
    let version = "";
    const big = { minHeight: "54px", fontSize: "18px", width: "100%", justifyContent: "flex-start", textAlign: "left", whiteSpace: "normal" };
    const box = h("div", { class: "pub-card", "aria-live": "polite" });
    root.append(h("h1", { text: state.title || "Live trivia" }), h("p", { class: "pub-muted", text: `Hosted by ${state.host}. Room code ${state.code}.` }), box);

    const answer = async (choice) => {
      try {
        const result = await ctx.action("answer", { token: playerToken, choice });
        state = result.room;
        draw();
      } catch (error) { ctx.notice(error.message, "bad"); }
    };

    const scores = () => h("ol", {}, ...(state.players || []).map((p) => h("li", { text: `${p.name}${p.guest ? " (guest)" : ""}: ${p.score}` })));

    function draw() {
      version = state.version || "";
      const me = state.me;
      if (!playerToken) {
        if (!(state.phase === "lobby" || state.phase === "question" || state.phase === "reveal")) {
          box.replaceChildren(h("p", { text: "This game has finished." }), h("h2", { text: "Scores" }), scores());
          return;
        }
        const name = h("input", { type: "text", maxlength: "30", autocomplete: "given-name", placeholder: "Your name" });
        const join = ctx.button("Join the game", async () => {
          const result = await ctx.action("join", { name: name.value });
          playerToken = result.token;
          remember(ctx.token, playerToken);
          state = result.room;
          draw();
          start();
        });
        box.replaceChildren(h("h2", { text: "Join the family quiz" }), h("div", { class: "pub-form" }, ctx.field("Your name", name, "So everyone knows who is playing."), join));
        name.focus();
        return;
      }
      if (state.phase === "lobby") {
        box.replaceChildren(h("h2", { text: "You're in!" }), h("p", { text: "Waiting for the host to start…" }), h("h3", { text: "Players" }), scores());
        return;
      }
      if (state.phase === "question" || state.phase === "reveal") {
        const q = state.question;
        const mine = me ? me.choice : null;
        box.replaceChildren(...[
          h("p", { class: "pub-muted", text: `Question ${state.index + 1} of ${state.total} · ${q.category}` }),
          h("h2", { style: { fontSize: "22px" }, text: q.text }),
          h("p", { class: "pub-muted", text: state.phase === "question" ? `${state.seconds_left} seconds left` : "Time's up" }),
          h("div", { class: "pub-form" }, ...q.choices.map((choice, c) => {
            const right = state.phase === "reveal" && state.answer === c;
            const node = h("button", { type: "button", class: `pub-btn ${mine === c ? "" : "secondary"}`, style: { ...big, outline: right ? "3px solid var(--pub-good)" : "" }, disabled: state.phase !== "question" || mine !== null, text: `${"ABCD"[c]}. ${choice}${right ? "  ✓" : ""}` });
            node.addEventListener("click", () => answer(c));
            return node;
          })),
          mine !== null && state.phase === "question" ? h("p", { class: "pub-muted", text: "Answer locked in." }) : null,
          me ? h("p", { class: "pub-badge", text: `Your score: ${me.score}` }) : null,
        ].filter(Boolean)); // replaceChildren would show a null as the text "null"
        return;
      }
      const winner = (state.players || [])[0];
      box.replaceChildren(h("h2", { text: state.phase === "expired" ? "This game has closed." : winner ? `🏆 ${winner.name} wins!` : "The game is over." }), h("h3", { text: "Scores" }), scores());
      if (timer) { clearInterval(timer); timer = 0; }
    }

    async function refresh() {
      if (!playerToken || document.hidden) return;
      try {
        const result = await ctx.action("state", { token: playerToken });
        if (result.room.version !== version || result.room.phase === "question") { state = result.room; draw(); }
      } catch (error) {
        if (error.status === 403) { playerToken = ""; remember(ctx.token, ""); draw(); }
      }
    }
    function start() {
      if (timer) return;
      timer = setInterval(refresh, 2500);
    }

    draw();
    if (playerToken) { refresh(); start(); }
  });
})();

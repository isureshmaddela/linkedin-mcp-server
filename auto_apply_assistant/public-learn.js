/* Aira — Learn: a shared deck as a page anyone can study in their browser (aira/suite/learn, kind "deck").
 * Nothing is saved: the cards are shuffled here and the page keeps count of what the visitor knew.
 */
"use strict";
(() => {
  window.AiraPublic.register("learn", "deck", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const cards = page.cards.slice();
    for (let i = cards.length - 1; i > 0; i -= 1) { const j = Math.floor(Math.random() * (i + 1)); [cards[i], cards[j]] = [cards[j], cards[i]]; }
    let index = 0;
    let known = 0;
    const stage = h("section", { class: "pub-card", style: { textAlign: "center", gap: "16px" } });
    const show = () => {
      if (index >= cards.length) {
        stage.replaceChildren(h("h2", { text: "All done! 🎉" }), h("p", { class: "pub-total", text: `You knew ${known} of ${cards.length}.` }),
          h("div", { class: "pub-btn-row", style: { justifyContent: "center" } }, ctx.button("Go again", () => { index = 0; known = 0; show(); })));
        return;
      }
      const card = cards[index];
      const back = h("div", { hidden: true }, h("p", { style: { fontSize: "26px", fontWeight: "800", margin: "0" }, text: card.back }), card.hint ? h("p", { class: "pub-muted", text: card.hint }) : null);
      // public.css has no [hidden] rule, so flex rows and buttons are hidden with display.
      const answers = h("div", { class: "pub-btn-row", style: { justifyContent: "center", display: "none" } },
        ctx.button("I didn't know", () => { index += 1; show(); }, "secondary"),
        ctx.button("I knew it", () => { known += 1; index += 1; show(); }));
      const flip = ctx.button("Show answer", () => { back.hidden = false; answers.style.display = ""; flip.style.display = "none"; });
      stage.replaceChildren(
        h("div", { class: "pub-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(cards.length), "aria-valuenow": String(index) }, h("span", { style: { width: `${Math.round((index * 100) / cards.length)}%` } })),
        h("p", { class: "pub-muted", text: `Card ${index + 1} of ${cards.length}` }),
        h("p", { style: { fontSize: "28px", fontWeight: "800", margin: "8px 0" }, text: card.front }),
        back, flip, answers);
    };
    root.append(...[
      h("h1", {}, h("span", { "aria-hidden": "true", text: `${page.emoji} ` }), page.title),
      page.description ? h("p", { class: "pub-muted", text: page.description }) : null,
      cards.length ? stage : h("p", { class: "pub-muted", text: "This deck has no cards yet." }),
    ].filter(Boolean));
    if (cards.length) show();
  });
})();

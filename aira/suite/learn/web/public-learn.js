/* Aira — Learn: a deck as a page anyone can study in their browser (aira/suite/learn, kind "deck").
 * Nothing is saved: the cards are shuffled here and the page keeps count of what the visitor knew.
 * A deck on sale shows a few free cards, the price and a "buy" form: the visitor leaves a name and email, pays the
 * seller through their own payment link with a reference, and gets an access code by email that opens every card
 * here (the code goes in the Aira-Secret header of the "cards" query, and this browser remembers it).
 * The way to pay is the seller's own (page.payment), never the business block: hosted, a member's deck is paid to
 * the member, not to their organization. Form errors show next to the form, where the person is looking.
 */
"use strict";
(() => {
  const remember = (token, code) => { try { if (code) localStorage.setItem(`learnDeck:${token}`, code); else localStorage.removeItem(`learnDeck:${token}`); } catch { /* private mode */ } };
  const recall = (token) => { try { return localStorage.getItem(`learnDeck:${token}`) || ""; } catch { return ""; } };

  window.AiraPublic.register("learn", "deck", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    let cards = [];
    let unlocked = !page.for_sale;
    let index = 0;
    let known = 0;
    const shuffle = (list) => { const copy = list.slice(); for (let i = copy.length - 1; i > 0; i -= 1) { const j = Math.floor(Math.random() * (i + 1)); [copy[i], copy[j]] = [copy[j], copy[i]]; } return copy; };
    const stage = h("section", { class: "pub-card", style: { textAlign: "center", gap: "16px" } });
    const shop = h("div", { style: { display: "grid", gap: "16px", marginTop: "16px" } });
    const teaser = page.for_sale ? h("p", { class: "pub-muted", text: `Try ${page.cards.length} of the ${page.total} cards for free.` }) : null;
    const start = (list) => { cards = shuffle(list); index = 0; known = 0; show(); };
    const show = () => {
      if (!cards.length) { stage.replaceChildren(h("p", { class: "pub-muted", text: "This deck has no cards yet." })); return; }
      if (index >= cards.length) {
        const preview = !unlocked;
        stage.replaceChildren(h("h2", { text: preview ? "That was the free part! 🎉" : "All done! 🎉" }), h("p", { class: "pub-total", text: `You knew ${known} of ${cards.length}.` }),
          preview ? h("p", { class: "pub-muted", text: `The whole deck has ${page.total} cards. Buy it below to study them all.` }) : null,
          h("div", { class: "pub-btn-row", style: { justifyContent: "center" } }, ctx.button("Go again", () => start(cards))));
        return;
      }
      const card = cards[index];
      const back = h("div", { hidden: true }, h("p", { style: { fontSize: "26px", fontWeight: "800", margin: "0", overflowWrap: "anywhere" }, text: card.back }), card.hint ? h("p", { class: "pub-muted", text: card.hint }) : null);
      // public.css has no [hidden] rule, so flex rows and buttons are hidden with display.
      const answers = h("div", { class: "pub-btn-row", style: { justifyContent: "center", display: "none" } },
        ctx.button("I didn't know", () => { index += 1; show(); }, "secondary"),
        ctx.button("I knew it", () => { known += 1; index += 1; show(); }));
      const flip = ctx.button("Show answer", () => { back.hidden = false; answers.style.display = ""; flip.style.display = "none"; });
      stage.replaceChildren(
        h("div", { class: "pub-progress", role: "progressbar", "aria-label": "Cards done", "aria-valuemin": "0", "aria-valuemax": String(cards.length), "aria-valuenow": String(index) }, h("span", { style: { width: `${Math.round((index * 100) / cards.length)}%` } })),
        h("p", { class: "pub-muted", text: `Card ${index + 1} of ${cards.length}${unlocked ? "" : " (free preview)"}` }),
        h("p", { style: { fontSize: "28px", fontWeight: "800", margin: "8px 0", overflowWrap: "anywhere" }, text: card.front }),
        back, flip, answers);
    };

    const unlock = async (code, quiet) => {
      try {
        const result = await ctx.query("cards", { secret: code });
        unlocked = true;
        remember(ctx.token, code);
        if (teaser) teaser.remove();
        for (const node of document.querySelectorAll(".pub-notice.bad")) node.remove();
        shop.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: `Every card is open: ${result.cards.length} cards. Enjoy!` }));
        start(result.cards);
      } catch (error) {
        remember(ctx.token, "");
        if (!quiet) throw error;
      }
    };

    // An error shown under the button that caused it (the shared notice goes to the top of the page, out of view on
    // a phone), cleared on the next try.
    const formButton = (label, run, kind, where) => ctx.button(label, async () => {
      where.replaceChildren();
      try { await run(); } catch (error) {
        where.replaceChildren(h("p", { class: "pub-notice bad", role: "alert", text: error.message }));
        where.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }, kind);
    const howToPay = (made) => {
      const pay = made.payment || page.payment || {};
      const parts = [];
      if (pay.link && /^https?:\/\//i.test(pay.link)) parts.push(h("a", { class: "pub-btn", href: pay.link, target: "_blank", rel: "noopener noreferrer", text: `Pay ${ctx.money(made.amount_cents, made.currency)}` }));
      if (pay.instructions) parts.push(h("p", { class: "pub-pre", text: pay.instructions }));
      parts.push(h("p", { class: "pub-muted", text: parts.length ? `Use the reference ${made.reference} with your payment.` : `${page.seller} will tell you how to pay. Keep your reference: ${made.reference}.` }));
      return h("section", { class: "pub-card pub-pay" }, h("h2", { text: "How to pay" }), ...parts);
    };

    const buyBox = () => {
      const name = h("input", { type: "text", autocomplete: "name", maxlength: "80", required: true });
      const email = h("input", { type: "email", autocomplete: "email", maxlength: "254", required: true });
      const result = h("div");
      const buyError = h("div");
      const codeError = h("div");
      const code = h("input", { type: "text", autocomplete: "off", autocapitalize: "characters", spellcheck: false, maxlength: "20", placeholder: "ABCDE-FGHJK" });
      return h("div", { style: { display: "grid", gap: "16px" } },
        h("section", { class: "pub-card" },
          h("h2", { text: `Study all ${page.total} cards for ${ctx.money(page.price_cents, page.currency)}` }),
          h("p", { class: "pub-muted", text: `Made by ${page.seller}. Pay once and keep it: you get an access code by email that opens every card on this page.` }),
          page.buyable === false ? h("p", { class: "pub-notice", role: "status", text: "This deck cannot be bought right now. Please come back later." }) : h("form", { class: "pub-form", onsubmit: (event) => event.preventDefault() },
            ctx.field("Your name", name), ctx.field("Your email", email, "Your access code is sent here."),
            h("div", { class: "pub-btn-row" }, formButton("Buy this deck", async () => {
              if (!name.value.trim() || !email.value.trim()) throw new Error("Enter your name and email.");
              const made = (await ctx.action("buy", { name: name.value, email: email.value })).request;
              result.replaceChildren(
                h("p", { class: "pub-notice good", role: "status", text: `Thank you! Pay ${ctx.money(made.amount_cents, made.currency)} with the reference ${made.reference}. When ${page.seller} sees your payment, your access code comes by email.` }),
                howToPay(made));
              result.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }, "", buyError)), buyError),
          result),
        h("section", { class: "pub-card" },
          h("h2", { text: "Already bought it?" }),
          h("form", { class: "pub-form", onsubmit: (event) => event.preventDefault() },
            ctx.field("Your access code", code, "It is in the email you were sent."),
            h("div", { class: "pub-btn-row" }, formButton("Open every card", async () => {
              if (!code.value.trim()) throw new Error("Enter your access code.");
              await unlock(code.value.trim());
            }, "secondary", codeError)), codeError)));
    };

    root.append(...[
      h("h1", {}, h("span", { "aria-hidden": "true", text: `${page.emoji} ` }), page.title),
      page.description ? h("p", { class: "pub-muted", text: page.description }) : null,
      teaser,
      stage,
      page.for_sale ? shop : null,
    ].filter(Boolean));
    if (page.for_sale) shop.replaceChildren(buyBox());
    start(page.cards);
    const saved = page.for_sale ? recall(ctx.token) : "";
    if (saved) unlock(saved, true);
  });
})();

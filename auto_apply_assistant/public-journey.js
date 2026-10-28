/* Aira — Life's public page (aira/suite/journey).
 *
 * goal_page: one goal someone shared with family and friends ("Cheer me on"). Shows the goal, why it matters, its
 *   progress (a percentage; for a number goal the amounts too, never the amounts of a money goal), its steps and
 *   the target date, and lets a visitor leave a cheer (a name and a short message) that appears on the page and
 *   reaches the owner. When the owner takes support, a visitor can also back the goal with an amount: they pay the
 *   owner through the owner's own link or details (Aira never takes the money) and appear as a supporter (name and
 *   message, never the amount). Never shown: check-in notes, other goals, documents, amounts of money.
 */
"use strict";
(() => {
  const CATEGORY = { career: "Career", health: "Health", money: "Money", learning: "Learning", family: "Family", relationships: "Relationships", home: "Home", travel: "Travel", creative: "Creative", personal: "Personal" };
  const STATE = { on_track: "On track", behind: "Behind for now", overdue: "Past its target date", completed: "Reached!", paused: "Paused", dropped: "Stopped" };
  const remember = (key, value) => { try { localStorage.setItem(`aira.pub.${key}`, value); } catch { /* storage unavailable */ } };
  const recall = (key) => { try { return localStorage.getItem(`aira.pub.${key}`) || ""; } catch { return ""; } };

  window.AiraPublic.register("journey", "goal_page", (root, data, ctx) => {
    const { h } = ctx;
    const goal = data.page;
    const who = goal.name || "Someone";
    const ago = (iso) => {
      const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
      if (minutes < 1) return "just now";
      if (minutes < 60) return `${minutes} min ago`;
      if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
      return ctx.date(iso);
    };
    const cheers = h("ul", { class: "jr-pub-cheers", style: { listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "10px" } });
    const drawCheers = (list) => {
      cheers.replaceChildren(...(list.length ? list.map((cheer) => h("li", { class: "pub-item", style: { display: "grid", gap: "2px" } },
        h("strong", { style: { overflowWrap: "anywhere" }, text: `👏 ${cheer.message}` }),
        h("span", { class: "pub-muted", text: `${cheer.name} · ${ago(cheer.at)}` }))) : [h("li", { class: "pub-muted", text: `No cheers yet. Be the first to cheer ${who} on!` })]));
    };
    let list = [...(goal.cheers || [])];
    drawCheers(list);

    const nameInput = h("input", { type: "text", placeholder: "Your name", value: recall("name"), maxlength: "40", autocomplete: "name" });
    const messageInput = h("textarea", { rows: 3, maxlength: "280", placeholder: `Keep going, ${who}!` });
    let shown = null;
    // A new message replaces the old ones, including an error the page showed for an earlier try.
    const say = (message, tone) => { if (shown) shown.remove(); root.querySelectorAll(":scope > .pub-notice").forEach((node) => node.remove()); shown = ctx.notice(message, tone); };
    const send = async () => {
      const message = messageInput.value.trim();
      if (!message) { messageInput.focus(); say("Write a short message first.", "bad"); return; }
      remember("name", nameInput.value.trim());
      const result = await ctx.action("cheer", { name: nameInput.value.trim(), message });
      list = [result.cheer, ...list];
      drawCheers(list);
      messageInput.value = "";
      say(`Thank you! ${who} will see your cheer.`, "good");
    };
    messageInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); send(); } });

    // Supporters: back the goal with an amount, paid to the owner directly.
    const support = goal.support;
    let supportCard = null;
    if (support) {
      const backers = h("ul", { style: { listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "10px" } });
      let backerList = [...(support.backers || [])];
      let count = support.supporters || 0;
      const countLine = h("p", { class: "pub-muted" });
      const drawBackers = () => {
        countLine.textContent = count ? `${count} ${count === 1 ? "person has" : "people have"} backed this goal.` : `Be the first to back ${who}'s goal.`;
        backers.replaceChildren(...backerList.map((backer) => h("li", { class: "pub-item", style: { display: "grid", gap: "2px" } },
          h("strong", { style: { overflowWrap: "anywhere" }, text: `🤝 ${backer.name}` }),
          backer.message ? h("span", { style: { overflowWrap: "anywhere" }, text: backer.message }) : null,
          h("span", { class: "pub-muted", text: ago(backer.at) }))));
      };
      drawBackers();
      const payLink = /^https?:\/\//i.test(support.pay_link || "") ? support.pay_link : "";
      const howToPay = payLink || support.pay_note ? h("div", { class: "pub-card pub-pay", style: { margin: "0" } },
        h("h3", { style: { margin: "0 0 8px" }, text: `How to pay ${who}` }),
        payLink ? h("a", { class: "pub-btn", href: payLink, target: "_blank", rel: "noopener noreferrer", text: `Pay ${who}` }) : null,
        support.pay_note ? h("p", { class: "pub-pre", style: { marginTop: "8px", overflowWrap: "anywhere" }, text: support.pay_note }) : null)
        : h("p", { class: "pub-muted", text: `${who} will tell you how to pay.` });
      const amountInput = h("input", { type: "text", inputmode: "decimal", placeholder: "20", maxlength: "12", autocomplete: "off" });
      const backerName = h("input", { type: "text", placeholder: "Your name", value: recall("name"), maxlength: "40", autocomplete: "name" });
      const backerMessage = h("textarea", { rows: 2, maxlength: "280", placeholder: "Good luck!" });
      const thanks = h("div", { role: "status" });
      const back = async () => {
        const amount = amountInput.value.trim();
        if (!amount) { amountInput.focus(); say("Enter the amount you want to give.", "bad"); return; }
        remember("name", backerName.value.trim());
        const result = await ctx.action("pledge", { amount, name: backerName.value.trim(), message: backerMessage.value.trim() });
        backerList = [{ name: result.pledge.name, message: result.pledge.message, at: result.pledge.at }, ...backerList];
        count += 1;
        drawBackers();
        amountInput.value = "";
        backerMessage.value = "";
        thanks.replaceChildren(h("p", { class: "pub-notice good", text: `Thank you! You promised ${result.pledge.amount_label}. Pay ${who} when you are ready; ${who} sees your name when it arrives.` }));
        say(`Thank you for backing ${who}!`, "good");
      };
      supportCard = h("section", { class: "pub-card pub-form" },
        h("h2", { text: `Back ${who}'s goal` }),
        h("p", { class: "pub-muted", text: `Help ${who} reach it with an amount you choose. You pay ${who} directly; your name and message appear here, never the amount.` }),
        ctx.field(`Amount (${support.currency})`, amountInput),
        ctx.field("Your name (optional)", backerName),
        ctx.field("A message (optional)", backerMessage),
        h("div", { class: "pub-btn-row" }, ctx.button("Back this goal", back)),
        thanks,
        howToPay,
        h("h3", { style: { margin: "12px 0 0" }, text: "Supporters" }),
        countLine,
        backers);
    }

    const facts = [CATEGORY[goal.category] || "", goal.target_date ? `Target: ${ctx.date(goal.target_date)}` : "", goal.status === "completed" && goal.completed_at ? `Reached on ${ctx.date(goal.completed_at)}` : STATE[goal.state] || ""].filter(Boolean);
    // append() writes a null as the text "null": leave the missing parts out first.
    root.append(...[
      h("p", { class: "pub-muted", text: `${who}'s goal` }),
      h("h1", { style: { overflowWrap: "anywhere" }, text: goal.title }),
      goal.why ? h("p", { style: { fontSize: "17px", overflowWrap: "anywhere" }, text: goal.why }) : null,
      h("section", { class: "pub-card" },
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "10px" } }, h("h2", { style: { margin: "0" }, text: goal.status === "completed" ? "Done! 🎉" : "Progress" }), h("strong", { style: { fontSize: "28px" }, text: `${goal.progress}%` })),
        h("div", { class: "pub-progress", role: "progressbar", "aria-label": "Progress", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(goal.progress) }, h("span", { style: { width: `${Math.max(0, Math.min(100, goal.progress))}%` } })),
        goal.so_far ? h("p", { style: { margin: "8px 0 0" }, text: `${goal.so_far} of ${goal.target}` }) : null,
        h("p", { class: "pub-muted", style: { margin: "8px 0 0" }, text: facts.join(" · ") })),
      goal.steps.length ? h("section", { class: "pub-card" }, h("h2", { text: `Steps (${goal.steps.filter((s) => s.done).length} of ${goal.steps.length})` }),
        h("ul", { style: { listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "6px" } }, ...goal.steps.map((step) => h("li", { style: { display: "flex", gap: "10px", alignItems: "baseline", fontSize: "17px" } },
          h("span", { "aria-hidden": "true", text: step.done ? "✅" : "⬜" }),
          h("span", { style: { textDecoration: step.done ? "line-through" : "none", overflowWrap: "anywhere" }, text: `${step.title}${step.done ? " (done)" : ""}` }))))) : null,
      supportCard,
      h("section", { class: "pub-card pub-form" }, h("h2", { text: `Cheer ${who} on` }),
        ctx.field("Your name (optional)", nameInput),
        ctx.field("Your message", messageInput, "Up to 280 characters. It appears on this page."),
        h("div", { class: "pub-btn-row" }, ctx.button("Send a cheer", send))),
      h("section", { class: "pub-card" }, h("h2", { text: "Cheers" }), cheers),
    ].filter(Boolean));
  });
})();

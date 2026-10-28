/* Aira — Service: the pages customers use (aira/suite/service/service_routes.py).
 *
 * help_center  search and read the business's answers, say whether one helped, ask for a paid service, start a
 *              live chat when it is on, or send a message that becomes a case (with a link to follow it). While a
 *              visitor writes, answers that might already help appear above the Send button.
 * chat         live chat: start with a name, email and first message, then talk; new messages arrive every two
 *              seconds. When chat is off the visitor leaves a message instead. The chat id and its secret stay in
 *              this browser so a reload keeps the conversation.
 * case         one request: the conversation with the business, what is due and how to pay, a reply box, and a
 *              1 to 5 rating once solved.
 * Everything a customer or the business wrote is shown as text, never parsed as HTML.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  if (!P) return;

  // Element.append() writes "null" for a missing part: keep only real nodes and text.
  const parts = (...items) => items.flat().filter((item) => item !== null && item !== undefined && item !== false);
  // An error notice from an earlier try stays on the page; clear it once something works.
  const clearErrors = () => document.querySelectorAll("#pubMain > .pub-notice.bad").forEach((node) => node.remove());

  const bubble = (ctx, mine, name, body, at) => {
    const { h } = ctx;
    const node = h("div", { class: "pub-card" }, h("p", { class: "pub-muted pub-hint", text: [name, at ? ctx.datetime(at) : ""].filter(Boolean).join(" · ") }), h("p", { class: "pub-pre", text: body }));
    Object.assign(node.style, { margin: mine ? "0 0 0 12%" : "0 12% 0 0", padding: "10px 14px", overflowWrap: "anywhere" });
    if (mine) node.style.background = "color-mix(in srgb, var(--pub-accent) 10%, var(--pub-surface))";
    return node;
  };

  const trapField = (ctx) => {
    // A hidden "website" field catches form-filling bots.
    const trap = ctx.h("input", { type: "text", name: "website", tabindex: "-1", autocomplete: "off" });
    const box = ctx.h("div", { "aria-hidden": "true" }, trap);
    Object.assign(box.style, { position: "absolute", left: "-10000px", width: "1px", height: "1px", overflow: "hidden" });
    return { trap, box };
  };

  const thanks = (ctx, box, sent, extra = "") => {
    const { h } = ctx;
    clearErrors();
    box.replaceChildren(...parts(
      h("h2", { text: "Thank you, we have your message" }),
      h("p", { text: sent.reference ? `Your request number is ${sent.reference}. We sent a copy to your email.${extra}` : "We will reply by email." }),
      sent.path ? h("p", {}, h("a", { class: "pub-btn secondary", href: sent.path, text: "Follow your request" })) : null,
    ));
  };

  // A message to the business that becomes a case. ``suggest`` (help centre only) shows answers that might help
  // while the visitor writes; ``onOpen`` opens one of them.
  function contactForm(ctx, { withSubject = true, intro = "", suggest = false, onOpen = null } = {}) {
    const { h } = ctx;
    const name = h("input", { type: "text", autocomplete: "name", maxlength: "80" });
    const email = h("input", { type: "email", autocomplete: "email", required: true, maxlength: "200" });
    const subject = h("input", { type: "text", maxlength: "200" });
    const message = h("textarea", { rows: 5, required: true, maxlength: "10000" });
    const { trap, box: trapBox } = trapField(ctx);
    const hints = h("div", { class: "pub-form", "aria-live": "polite" });
    let timer = 0;
    let asked = "";
    const lookUp = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = `${subject.value} ${message.value}`.trim();
        if (q.length < 8 || q === asked) return;
        asked = q;
        try {
          const found = await ctx.query("answers", { q: q.slice(0, 500) });
          hints.replaceChildren(...(found.articles.length ? [
            h("p", { class: "pub-muted", text: "These answers might help:" }),
            ...found.articles.map((article) => h("div", { class: "pub-item" }, h("a", { href: `#${article.id}`, text: article.title, onclick: (event) => { event.preventDefault(); if (onOpen) onOpen(article.id); } }))),
          ] : []));
        } catch { /* suggestions are a help, never a blocker */ }
      }, 600);
    };
    if (suggest && ctx.query) { subject.addEventListener("input", lookUp); message.addEventListener("input", lookUp); }
    const box = h("section", { class: "pub-card pub-form" });
    box.append(...parts(
      h("h2", { text: "Contact us" }),
      intro ? h("p", { class: "pub-muted", text: intro }) : null,
      ctx.field("Your name", name),
      ctx.field("Your email", email, "We reply to this address."),
      withSubject ? ctx.field("Subject", subject) : null,
      ctx.field("How can we help?", message),
      hints,
      trapBox,
      h("div", { class: "pub-btn-row" }, ctx.button("Send message", async () => {
        if (!email.value.trim()) throw new Error("Enter your email so we can reply.");
        if (!message.value.trim()) throw new Error("Enter your message.");
        const sent = await ctx.action("contact", { name: name.value, email: email.value, subject: subject.value, message: message.value, website: trap.value });
        thanks(ctx, box, sent);
      })),
    ));
    return box;
  }

  // ---------------------------------------------------------------- paid help
  function offersSection(ctx, offers) {
    const { h } = ctx;
    if (!offers || !offers.length) return null;
    const section = h("section", { class: "pub-card" }, h("h2", { text: "Paid help" }), h("p", { class: "pub-muted", text: "Want us to do it for you, or need help fast? Ask for one of these and we will be in touch." }));
    for (const offer of offers) {
      const item = h("div", { class: "pub-item" });
      const ask = ctx.button("Ask for this", () => {
        const name = h("input", { type: "text", autocomplete: "name", maxlength: "80" });
        const email = h("input", { type: "email", autocomplete: "email", required: true, maxlength: "200" });
        const phone = h("input", { type: "tel", autocomplete: "tel", maxlength: "40" });
        const message = h("textarea", { rows: 3, maxlength: "5000" });
        const { trap, box: trapBox } = trapField(ctx);
        const form = h("div", { class: "pub-form" });
        form.append(...parts(
          ctx.field("Your name", name),
          ctx.field("Your email", email, "We send your request number and how to pay here."),
          ctx.field("Your phone (optional)", phone),
          ctx.field("Anything we should know? (optional)", message),
          trapBox,
          h("div", { class: "pub-btn-row" },
            ctx.button(`Ask for it (${ctx.money(offer.price_cents)})`, async () => {
              if (!email.value.trim()) throw new Error("Enter your email so we can reply.");
              const sent = await ctx.action("request", { offer_id: offer.id, name: name.value, email: email.value, phone: phone.value, message: message.value, website: trap.value });
              thanks(ctx, item, sent, " Your request page shows how to pay.");
            }),
            ctx.button("Cancel", () => { form.remove(); ask.hidden = false; }, "ghost")),
        ));
        ask.hidden = true;
        item.append(form);
        name.focus();
      }, "secondary");
      item.append(...parts(
        h("h3", { text: offer.title }),
        h("p", {}, h("strong", { text: ctx.money(offer.price_cents) })),
        offer.description ? h("p", { class: "pub-pre pub-muted", text: offer.description }) : null,
        h("div", { class: "pub-btn-row" }, ask),
      ));
      section.append(item);
    }
    return section;
  }

  // ---------------------------------------------------------------- help centre
  P.register("service", "help_center", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const names = Object.fromEntries(page.categories.map((c) => [c.id, c.name]));
    const results = h("div", { class: "pub-form", "aria-live": "polite" });
    const reader = h("div", {});

    const list = (articles, heading) => {
      if (!articles.length) return [h("p", { class: "pub-muted", text: heading ? "No answers match. Try other words or contact us below." : "There are no answers here yet. Contact us below." })];
      const groups = {};
      for (const article of articles) (groups[article.category_id] = groups[article.category_id] || []).push(article);
      const order = [...page.categories.map((c) => c.id), ""].filter((id) => groups[id]);
      return parts(heading ? h("h2", { text: heading }) : null, ...order.map((id) => h("section", { class: "pub-card" },
        h("h2", { text: names[id] || "More answers" }),
        ...groups[id].map((article) => h("div", { class: "pub-item" },
          h("a", { href: `#${article.id}`, text: article.title, onclick: (event) => { event.preventDefault(); open(article.id).catch((error) => ctx.notice(error.message, "bad")); } }),
          article.excerpt ? h("p", { class: "pub-muted", text: article.excerpt }) : null)))));
    };

    async function open(articleId) {
      const { article } = await ctx.action("article", { id: articleId });
      clearErrors();
      const said = h("p", { class: "pub-muted", "aria-live": "polite" });
      const vote = (helpful) => ctx.button(helpful ? "Yes" : "No", async () => {
        await ctx.action("vote", { id: article.id, helpful });
        said.textContent = helpful ? "Thank you for letting us know." : "Thank you. Contact us below and we will help.";
      }, "ghost");
      reader.replaceChildren(h("article", { class: "pub-card" },
        h("p", {}, h("a", { href: "#", text: "Back to all answers", onclick: (event) => { event.preventDefault(); reader.replaceChildren(); results.hidden = false; history.replaceState(null, "", location.pathname + location.search); } })),
        h("h1", { text: article.title }),
        h("p", { class: "pub-pre", text: article.body }),
        h("p", { class: "pub-muted pub-hint", text: `Updated ${ctx.date(article.updated_at)}` }),
        h("p", {}, h("strong", { text: "Did this answer your question?" })),
        h("div", { class: "pub-btn-row", role: "group", "aria-label": "Was this helpful?" }, vote(true), vote(false)),
        said));
      results.hidden = true;
      reader.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    const search = h("input", { type: "search", placeholder: "Search for an answer", "aria-label": "Search for an answer" });
    Object.assign(search.style, { width: "100%", boxSizing: "border-box" });
    const run = async () => {
      const q = search.value.trim();
      reader.replaceChildren();
      results.hidden = false;
      if (!q) { results.replaceChildren(...list(page.articles, "")); return; }
      const found = await ctx.action("search", { q });
      clearErrors();
      results.replaceChildren(...list(found.articles, `Answers for "${q}"`));
    };
    search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run().catch((error) => ctx.notice(error.message, "bad")); } });

    root.append(...parts(
      h("h1", { text: data.title || "How can we help?" }),
      h("div", { class: "pub-card pub-form", role: "search" }, ctx.field("Search our answers", search), h("div", { class: "pub-btn-row" }, ctx.button("Search", run))),
      reader,
      results,
      offersSection(ctx, page.offers),
      page.chat_path ? h("section", { class: "pub-card" }, h("h2", { text: "Talk to us now" }), h("p", { class: "pub-muted", text: "We are online. Start a live chat and get an answer straight away." }), h("a", { class: "pub-btn", href: page.chat_path, text: "Start a live chat" })) : null,
      contactForm(ctx, { intro: "Could not find your answer? Send us a message and we will reply by email.", suggest: (data.queries || []).includes("answers"), onOpen: (id) => open(id).catch((error) => ctx.notice(error.message, "bad")) }),
    ));
    results.replaceChildren(...list(page.articles, ""));
    const wanted = location.hash.replace(/^#/, "");
    if (wanted && page.articles.some((a) => a.id === wanted)) open(wanted).catch(() => {});
  });

  // ---------------------------------------------------------------- live chat
  P.register("service", "chat", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const key = `aira-chat-${ctx.token}`;
    const remember = (value) => { try { if (value) localStorage.setItem(key, JSON.stringify(value)); else localStorage.removeItem(key); } catch { /* private browsing */ } };
    const recall = () => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } };
    root.append(h("h1", { text: data.title || "Live chat" }));
    const area = h("div", {});
    root.append(area);

    const offline = () => area.replaceChildren(h("p", { class: "pub-notice", text: page.offline_message || "We are away right now. Leave a message and we will reply by email." }), contactForm(ctx, { withSubject: false }));

    function talk(session, first) {
      let cursor = 0;
      let timer = 0;
      const thread = h("div", { class: "pub-form", role: "log", "aria-live": "polite", "aria-label": "Conversation" });
      const status = h("p", { class: "pub-muted pub-hint" });
      const text = h("textarea", { rows: 2, maxlength: "2000", "aria-label": "Your message" });
      const add = (rows) => {
        for (const m of rows) thread.append(bubble(ctx, m.from === "you", m.from === "you" ? "You" : m.name || ctx.business.name || "Support", m.body, m.at));
        if (rows.length) thread.lastElementChild.scrollIntoView({ block: "nearest" });
      };
      const apply = (result) => {
        add(result.messages || []);
        cursor = Math.max(cursor, result.cursor || 0);
        if (result.status === "ended") {
          clearInterval(timer);
          remember(null);
          composer.replaceChildren(h("p", { class: "pub-muted", text: "This chat has ended. Thank you for talking to us." }), ctx.button("Start a new chat", () => ctx.reload(), "secondary"));
        } else status.textContent = result.status === "waiting" ? "Waiting for someone to answer. You can keep writing." : "Connected.";
      };
      const send = async () => {
        if (!text.value.trim()) return;
        const result = await ctx.action("send", { ...session, body: text.value, after: cursor });
        text.value = "";
        clearErrors();
        apply(result);
      };
      text.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send().catch((error) => ctx.notice(error.message, "bad")); } });
      const composer = h("div", { class: "pub-form" }, ctx.field("Your message", text, "Press Enter to send."), h("div", { class: "pub-btn-row" }, ctx.button("Send", send)));
      area.replaceChildren(h("section", { class: "pub-card pub-form" }, thread, status), composer);
      apply(first);
      timer = setInterval(async () => {
        if (!thread.isConnected) { clearInterval(timer); return; }
        if (document.hidden) return;
        try { apply(await (ctx.query ? ctx.query("messages", { ...session, after: cursor }) : ctx.action("poll", { ...session, after: cursor }))); } catch (error) {
          if (error.status === 404) { clearInterval(timer); remember(null); ctx.reload(); }
        }
      }, 2000);
    }

    function startForm() {
      const name = h("input", { type: "text", autocomplete: "name", required: true, maxlength: "80" });
      const email = h("input", { type: "email", autocomplete: "email", required: true, maxlength: "200" });
      const message = h("textarea", { rows: 3, required: true, maxlength: "2000" });
      area.replaceChildren(h("section", { class: "pub-card pub-form" },
        h("p", { class: "pub-muted", text: "Tell us who you are and what you need. Someone from the team will answer here." }),
        ctx.field("Your name", name),
        ctx.field("Your email", email, "If we get cut off we will reply by email."),
        ctx.field("Your question", message),
        h("div", { class: "pub-btn-row" }, ctx.button("Start chat", async () => {
          if (!name.value.trim() || !email.value.trim() || !message.value.trim()) throw new Error("Enter your name, email and question.");
          const started = await ctx.action("start", { name: name.value, email: email.value, message: message.value });
          clearErrors();
          const session = { chat_id: started.chat_id, secret: started.secret };
          remember(session);
          talk(session, { status: "waiting", messages: started.messages, cursor: started.cursor });
        }))));
    }

    const saved = recall();
    if (saved && saved.chat_id && saved.secret) {
      ctx.action("poll", { ...saved, after: 0 }).then((first) => {
        if (first.status === "ended") { remember(null); if (page.online) startForm(); else offline(); return; }
        talk(saved, first);
      }).catch(() => { remember(null); if (page.online) startForm(); else offline(); });
      return;
    }
    if (page.online) startForm();
    else offline();
  });

  // ---------------------------------------------------------------- one request
  const STATUS = { new: "Received", open: "We are on it", waiting: "Waiting for your reply", solved: "Solved", closed: "Closed" };
  P.register("service", "case", (root, data, ctx) => {
    const { h } = ctx;
    const render = (page) => {
      const tone = page.status === "solved" || page.status === "closed" ? "good" : "";
      const thread = h("div", { class: "pub-form", "aria-label": "Conversation" }, ...page.thread.map((m) => {
        const node = bubble(ctx, m.from === "you", m.from === "you" ? "You" : m.name || "Support", m.body, m.at);
        for (const link of m.attachments || []) if (/^https:\/\//i.test(link)) node.append(h("p", {}, h("a", { href: link, target: "_blank", rel: "noopener noreferrer", text: link })));
        return node;
      }));
      const out = [
        h("h1", { text: page.subject }),
        h("p", {}, h("span", { class: `pub-badge ${tone}`, text: STATUS[page.status] || page.status }), h("span", { class: "pub-muted", text: ` Request ${page.reference} · opened ${ctx.date(page.created_at)}` })),
      ];
      const charges = page.charges || [];
      if (charges.length) {
        out.push(h("section", { class: "pub-card" },
          h("h2", { text: page.due_cents ? "To pay" : "Payments" }),
          h("table", { class: "pub-table" }, h("tbody", {}, ...charges.map((c) => h("tr", {},
            h("td", { text: c.label }),
            h("td", { text: ctx.money(c.amount_cents) }),
            h("td", {}, h("span", { class: `pub-badge ${c.status === "paid" ? "good" : ""}`, text: c.status === "paid" ? "Paid" : "Due" })))))),
          page.due_cents ? h("p", { class: "pub-total", text: `Total due: ${ctx.money(page.due_cents)}` }) : h("p", { class: "pub-muted", text: "Everything is paid. Thank you." })));
        if (page.due_cents) {
          const pay = ctx.paymentBox(page.due_cents, page.reference);
          out.push(pay || h("p", { class: "pub-muted", text: "We will tell you how to pay. Reply below if you have a question." }));
        }
      }
      if (page.thread.length) out.push(thread);
      if (page.can_rate) {
        let score = 0;
        const starRow = h("div", { class: "pub-slots", role: "radiogroup", "aria-label": "Your rating from 1 to 5" });
        for (let n = 1; n <= 5; n += 1) {
          const star = h("button", { type: "button", class: "pub-slot", role: "radio", "aria-checked": "false", "aria-label": `${n} out of 5`, text: "★".repeat(n) });
          star.addEventListener("click", () => { score = n; starRow.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-checked", String(i === n - 1))); });
          starRow.append(star);
        }
        const comment = h("textarea", { rows: 3, maxlength: "1000" });
        out.push(h("section", { class: "pub-card pub-form" },
          h("h2", { text: "How did we do?" }),
          starRow,
          ctx.field("Anything to add? (optional)", comment),
          h("div", { class: "pub-btn-row" }, ctx.button("Send rating", async () => {
            if (!score) throw new Error("Choose a rating from 1 to 5.");
            render(await ctx.action("rate", { score, comment: comment.value }));
            clearErrors();
            ctx.notice("Thank you for your rating.", "good");
          }))));
      } else if (page.rated) out.push(h("p", { class: "pub-notice good", text: `Thank you, you rated us ${page.rated} out of 5.` }));
      if (page.can_reply) {
        const text = h("textarea", { rows: 4, maxlength: "5000" });
        out.push(h("section", { class: "pub-card pub-form" },
          h("h2", { text: page.status === "solved" ? "Still need help?" : "Reply" }),
          ctx.field("Your message", text, page.status === "solved" ? "Replying opens your request again." : ""),
          h("div", { class: "pub-btn-row" }, ctx.button("Send reply", async () => {
            if (!text.value.trim()) throw new Error("Enter your message.");
            render(await ctx.action("reply", { body: text.value }));
            clearErrors();
            ctx.notice("Sent. We will reply soon.", "good");
          }))));
      } else out.push(h("p", { class: "pub-muted", text: page.note || "This request is closed. If you need more help, please contact us again." }));
      root.replaceChildren(...parts(out));
    };
    render(data.page);
  });
})();

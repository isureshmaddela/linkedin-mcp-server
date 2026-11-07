/* Aira — News: a newsletter's public page (aira/suite/news, kind "newsletter").
 * It shows the newsletter, who writes it and its latest issues: every story of a free newsletter, the first story of
 * each issue of a paid one. A visitor subscribes with their name and email: free, they are in (a welcome email
 * carries their code); paid, they get the amount and a reference to pay the writer through the writer's own payment
 * link, and an access code by email once the writer sees the money. The code opens every story here (it goes in the
 * Aira-Secret header of the "issues" query, and this browser remembers it) and, with the email, stops the newsletter.
 * Someone still waiting to pay withdraws their request with their email and payment reference.
 * Story links open the publisher's own page; everything is text, never HTML. Each form shows its own message under
 * its buttons (the newest one only, so an old error never stays on screen after a success).
 */
"use strict";
(() => {
  const KEY = (token) => `newsLetter:${token}`;
  const remember = (token, code) => { try { if (code) localStorage.setItem(KEY(token), code); else localStorage.removeItem(KEY(token)); } catch { /* private mode */ } };
  const recall = (token) => { try { return localStorage.getItem(KEY(token)) || ""; } catch { return ""; } };
  const safeLink = (link) => (/^https?:\/\//i.test(link || "") ? link : "");

  window.AiraPublic.register("news", "newsletter", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const issuesBox = h("div", { style: { display: "grid", gap: "16px" } });

    // One message per form, replaced by the next one: errors do not pile up at the top of the page.
    const say = (box, text, tone = "") => {
      if (!text) { box.replaceChildren(); return; }
      box.replaceChildren(h("p", { class: `pub-notice ${tone}`.trim(), role: tone === "bad" ? "alert" : "status", text }));
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    const act = (label, box, onClick, kind) => ctx.button(label, async () => {
      say(box, "");
      try { await onClick(); } catch (error) { say(box, error && error.message ? error.message : String(error), "bad"); }
    }, kind);

    const story = (item) => {
      const link = safeLink(item.link);
      return h("li", { class: "pub-item", style: { display: "grid", gap: "4px" } },
        link ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer", style: { fontWeight: "700", overflowWrap: "anywhere" }, text: item.title }) : h("strong", { text: item.title }),
        item.source ? h("span", { class: "pub-muted", text: item.source }) : null,
        item.note ? h("p", { style: { margin: "0" }, text: item.note }) : null);
    };

    const drawIssues = (issues, open) => {
      if (!issues.length) {
        issuesBox.replaceChildren(h("section", { class: "pub-card" }, h("p", { class: "pub-muted", text: "The first issue is on its way. Subscribe below to get it by email." })));
        return;
      }
      issuesBox.replaceChildren(...issues.map((issue) => h("section", { class: "pub-card" },
        h("p", { class: "pub-muted", text: `Issue ${issue.number} · ${ctx.date(issue.published_at)}` }),
        h("h2", { style: { margin: "0", overflowWrap: "anywhere" }, text: issue.title }),
        issue.intro ? h("p", { style: { whiteSpace: "pre-line" }, text: issue.intro }) : null,
        h("ol", { style: { display: "grid", gap: "12px", paddingLeft: "20px" } }, ...issue.stories.map(story)),
        issue.more && !open ? h("p", { class: "pub-muted", text: issue.more === 1 ? "1 more story for subscribers." : `${issue.more} more stories for subscribers.` }) : null)));
    };

    const unlock = async (code, quiet, box) => {
      try {
        const result = await ctx.query("issues", { secret: code });
        remember(ctx.token, code);
        drawIssues(result.issues, true);
        if (!quiet && box) say(box, "Every story is open. Enjoy your reading!", "good");
        return true;
      } catch (error) {
        remember(ctx.token, "");
        if (!quiet) throw error;
        return false;
      }
    };

    const subscribeBox = () => {
      const name = h("input", { type: "text", autocomplete: "name", maxlength: "80", required: true });
      const email = h("input", { type: "email", autocomplete: "email", maxlength: "254", required: true });
      const result = h("div");
      return h("section", { class: "pub-card" },
        h("h2", { text: page.paid ? `Subscribe for ${ctx.money(page.price_cents, page.currency)} a month` : "Subscribe for free" }),
        h("p", { class: "pub-muted", text: page.paid
          ? `Every issue by email, and every story on this page. Pay ${page.writer} directly; your access code comes by email when they see your payment.`
          : "Every new issue comes to your email. You can stop whenever you like." }),
        h("form", { class: "pub-form", onsubmit: (event) => event.preventDefault() },
          ctx.field("Your name", name), ctx.field("Your email", email),
          h("div", { class: "pub-btn-row" }, act(page.paid ? "Subscribe" : "Subscribe for free", result, async () => {
            if (!name.value.trim() || !email.value.trim()) throw new Error("Enter your name and email.");
            const made = (await ctx.action("subscribe", { name: name.value, email: email.value })).subscription;
            if (made.status === "subscribed") {
              result.replaceChildren(h("p", { class: "pub-notice good", role: "status", text: made.already ? "You are already subscribed. New issues come to your email." : "You are subscribed! Look for a welcome email with your code." }));
            } else {
              result.replaceChildren(
                h("p", { class: "pub-notice good", role: "status", text: `Thank you! Pay ${ctx.money(made.amount_cents, made.currency)} with the reference ${made.reference}. When ${page.writer} sees your payment, your access code comes by email.` }),
                ctx.paymentBox(made.amount_cents, made.reference) || h("p", { class: "pub-muted", text: `${page.writer} will tell you how to pay. Keep your reference: ${made.reference}.` }));
            }
            result.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }))),
        result);
    };

    const codeBox = () => {
      const code = h("input", { type: "text", autocomplete: "off", autocapitalize: "characters", spellcheck: false, maxlength: "20", placeholder: "ABCDE-FGHJK" });
      const email = h("input", { type: "email", autocomplete: "email", maxlength: "254" });
      const result = h("div");
      return h("section", { class: "pub-card" },
        h("h2", { text: page.paid ? "Already a subscriber?" : "Stop the newsletter" }),
        h("form", { class: "pub-form", onsubmit: (event) => event.preventDefault() },
          ctx.field("Your code", code, page.paid ? "It is in the email you were sent. Not paid yet? Your payment reference (NW-...) stops your request." : "It is in the email you were sent."),
          page.paid ? h("div", { class: "pub-btn-row" }, act("Open every story", result, async () => {
            if (!code.value.trim()) throw new Error("Enter the access code from your email.");
            await unlock(code.value.trim(), false, result);
          }, "secondary")) : null,
          ctx.field("Your email", email, "To stop the newsletter, enter your email and code."),
          h("div", { class: "pub-btn-row" }, act("Stop the newsletter", result, async () => {
            if (!email.value.trim() || !code.value.trim()) throw new Error("Enter your email and your code.");
            await ctx.action("unsubscribe", { email: email.value, code: code.value });
            remember(ctx.token, "");
            say(result, "Done: you will get no more issues.", "good");
          }, "secondary"))),
        result);
    };

    root.append(...[
      h("h1", { style: { overflowWrap: "anywhere" }, text: page.name }),
      h("p", { class: "pub-muted", text: `A newsletter by ${page.writer}${page.paid ? ` · ${ctx.money(page.price_cents, page.currency)} a month` : " · free"}` }),
      page.about ? h("p", { style: { whiteSpace: "pre-line" }, text: page.about }) : null,
      subscribeBox(),
      h("h2", { text: "Latest issues" }),
      issuesBox,
      codeBox(),
    ].filter(Boolean));
    drawIssues(page.issues, !page.paid);
    const saved = page.paid ? recall(ctx.token) : "";
    if (saved) unlock(saved, true);
  });
})();

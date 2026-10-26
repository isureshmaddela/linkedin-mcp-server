/* Aira — Family: the family board relatives open without an account (aira/suite/family/family_routes.py).
 *
 *   board   the family's feed (never their chats); visitors react and write a message, which the family approves
 *           first unless they chose to publish at once.
 *
 * DOM is built with ctx.h and text only; photos load without sending a referrer.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  const NAME_KEY = "aira.familyBoard.name";
  const remembered = () => { try { return localStorage.getItem(NAME_KEY) || ""; } catch { return ""; } };
  const remember = (name) => { try { localStorage.setItem(NAME_KEY, name); } catch { /* storage unavailable */ } };
  const when = (iso) => { const date = new Date(iso); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); };

  P.register("family", "board", (root, data, ctx) => {
    const { h } = ctx;
    const page = data.page;
    const face = (person, size = 40) => h("span", { "aria-hidden": "true", style: { display: "inline-grid", placeItems: "center", width: `${size}px`, height: `${size}px`, borderRadius: `${Math.round(size / 3)}px`, background: person.color || "#64748b", fontSize: `${Math.round(size * 0.55)}px`, flex: "none" }, text: person.emoji || "🙂" });

    root.append(h("h1", { text: page.title || "Our family" }));
    if (page.people.length) root.append(h("div", { class: "pub-btn-row", "aria-label": "The family" }, ...page.people.map((person) => h("span", { style: { display: "inline-flex", alignItems: "center", gap: "6px" } }, face(person, 32), h("span", { text: person.name })))));

    // Writing a message
    const name = h("input", { type: "text", maxlength: "40", autocomplete: "name", value: remembered() });
    const text = h("textarea", { maxlength: "1000", rows: "3" });
    const send = ctx.button("Send to the family", async () => {
      const result = await ctx.action("post", { name: name.value, text: text.value });
      remember(name.value.trim());
      text.value = "";
      if (result.status === "live") { ctx.notice("Thank you! Your message is on the board.", "good"); await ctx.reload(); }
      else ctx.notice("Thank you! The family will see your message once they approve it.", "good");
    });
    root.append(h("section", { class: "pub-card" }, h("h2", { text: "Write to the family" }), h("div", { class: "pub-form" }, ctx.field("Your name", name), ctx.field("Your message", text, page.auto_publish ? "" : "The family approves messages before they appear here."), h("div", { class: "pub-btn-row" }, send))));

    // The feed
    if (!page.posts.length) { root.append(h("p", { class: "pub-muted", text: "Nothing shared yet. Write the first message!" })); return; }
    for (const post of page.posts) {
      const reactions = h("div", { class: "pub-btn-row", role: "group", "aria-label": "Reactions" }, ...page.reactions.map((emoji) => {
        const count = post.reactions[emoji] || 0;
        return h("button", { type: "button", class: "pub-btn ghost", "aria-label": `React ${emoji}${count ? ` (${count})` : ""}`, style: { minHeight: "38px", padding: "4px 10px" }, onclick: async (event) => {
          event.currentTarget.disabled = true;
          try { const result = await ctx.action("react", { post_id: post.id, emoji }); post.reactions = result.reactions; await ctx.reload(); } catch (error) { ctx.notice(error.message, "bad"); }
        } }, h("span", { text: emoji }), count ? h("span", { text: ` ${count}` }) : null);
      }));
      root.append(h("article", { class: "pub-card" },
        h("div", { style: { display: "flex", gap: "10px", alignItems: "center" } }, face(post.author, 40), h("div", {}, h("strong", { text: post.author.name }), h("div", { class: "pub-muted pub-hint", text: when(post.created_at) })), post.pinned ? h("span", { class: "pub-badge", text: "Pinned" }) : null),
        post.text ? h("p", { class: "pub-pre", text: post.text }) : null,
        post.photos.length ? h("div", { class: "pub-grid" }, ...post.photos.filter((src) => /^https:\/\//i.test(src)).map((src, index) => h("img", { src, alt: `Photo ${index + 1} from ${post.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", borderRadius: "10px" } }))) : null,
        reactions,
        post.comments.length ? h("div", {}, ...post.comments.map((comment) => h("p", { style: { margin: "4px 0" } }, h("strong", { text: `${comment.author.emoji || ""} ${comment.author.name}: ` }), h("span", { text: comment.text })))) : null));
    }
  });
})();

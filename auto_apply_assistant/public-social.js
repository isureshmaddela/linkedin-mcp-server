/* Aira — Social: the public profile page an adult with a public profile shares (aira/suite/social/social_routes.py).
 *
 *   profile   their name, picture, about, work profile and public posts; anyone can open it without an account.
 *
 * DOM is built with ctx.h and text only; photos load without sending a referrer.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  const when = (iso) => { const date = new Date(iso); return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }); };

  P.register("social", "profile", (root, response, ctx) => {
    const { h } = ctx;
    const data = response.page || {};
    if (!data.available) {
      root.append(h("section", { class: "pub-card" }, h("h1", { text: data.title || "This profile is private." }), h("p", { class: "pub-muted", text: "Ask the person to share their profile again." })));
      return;
    }
    const person = data.person;
    const avatar = /^https:\/\//i.test(person.avatar || "")
      ? h("img", { src: person.avatar, alt: "", referrerpolicy: "no-referrer", style: { width: "88px", height: "88px", borderRadius: "50%", objectFit: "cover" } })
      : h("span", { "aria-hidden": "true", style: { display: "inline-grid", placeItems: "center", width: "88px", height: "88px", borderRadius: "50%", background: "#e2e8f0", fontSize: "46px" }, text: person.avatar || "🙂" });
    root.append(h("section", { class: "pub-card" },
      h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, avatar, h("div", {}, h("h1", { style: { margin: 0 }, text: person.name }), h("p", { class: "pub-muted", style: { margin: 0 }, text: `@${person.handle} on Aira Social` }))),
      person.headline ? h("p", { style: { fontSize: "18px" }, text: person.headline }) : null,
      person.open_to_work ? h("p", {}, h("span", { class: "pub-badge", text: "Open to work" })) : null,
      person.bio ? h("p", { class: "pub-pre", text: person.bio }) : null));
    if ((person.experience && person.experience.length) || (person.skills && person.skills.length)) {
      root.append(h("section", { class: "pub-card" }, h("h2", { text: "Work" }),
        ...(person.experience || []).map((item) => h("div", { class: "pub-item" }, h("strong", { text: `${item.title}${item.company ? ` at ${item.company}` : ""}` }), h("div", { class: "pub-muted pub-hint", text: [item.start, item.end || (item.start ? "now" : "")].filter(Boolean).join(" to ") }), item.about ? h("p", { class: "pub-pre", text: item.about }) : null)),
        person.skills && person.skills.length ? h("p", {}, h("strong", { text: "Skills: " }), h("span", { text: person.skills.join(", ") })) : null));
    }
    if (!data.posts.length) { root.append(h("p", { class: "pub-muted", text: "No public posts yet." })); return; }
    root.append(h("h2", { text: "Posts" }));
    for (const post of data.posts) {
      root.append(h("article", { class: "pub-card" },
        h("div", { class: "pub-muted pub-hint", text: `${when(post.created_at)}${post.reactions ? ` · ${post.reactions} reaction${post.reactions === 1 ? "" : "s"}` : ""}` }),
        post.text ? h("p", { class: "pub-pre", text: post.text }) : null,
        post.photos.length ? h("div", { class: "pub-grid" }, ...post.photos.filter((src) => /^https:\/\//i.test(src)).map((src, index) => h("img", { src, alt: `Photo ${index + 1} from ${person.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", borderRadius: "10px" } }))) : null));
    }
  });
})();

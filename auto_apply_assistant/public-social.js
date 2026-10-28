/* Aira — Social: the public pages people share outside Aira (aira/suite/social/social_routes.py).
 *
 *   profile   an adult's name, picture, about, work profile and public posts; anyone can open it without an account.
 *   listing   a Marketplace listing: photos, price, where to pick it up, a button for the seller's own payment
 *             link, and "I'm interested" (the seller gets a notification with how to reach the visitor).
 *
 * DOM is built with ctx.h and text only; photos load without sending a referrer.
 */
"use strict";
(() => {
  const P = window.AiraPublic;
  const when = (iso) => { const date = new Date(iso); return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }); };
  const https = (src) => /^https:\/\//i.test(src || "");
  // A person's own page (their profile, a listing they sell as themselves) is not the account's business: the
  // header carries the person's name and none of the business's name, logo or contact details.
  const asPerson = (name, title) => {
    const set = (id, fn) => { const node = document.getElementById(id); if (node) fn(node); };
    set("pubName", (node) => { node.textContent = name || ""; });
    set("pubTagline", (node) => { node.textContent = "on Monosage Social"; });
    set("pubLogo", (node) => { node.hidden = true; node.removeAttribute("src"); });
    set("pubContact", (node) => node.replaceChildren());
    document.title = title && name ? `${title} · ${name}` : title || name || "Monosage";
  };
  // Clear what an earlier try left on the page ("Enter your name.") before showing a new message.
  const clearNotices = (root) => { for (const node of root.querySelectorAll(".pub-notice")) node.remove(); };

  P.register("social", "profile", (root, response, ctx) => {
    const { h } = ctx;
    const data = response.page || {};
    if (!data.available) {
      root.append(h("section", { class: "pub-card" }, h("h1", { text: data.title || "This profile is private." }), h("p", { class: "pub-muted", text: "Ask the person to share their profile again." })));
      return;
    }
    const person = data.person;
    asPerson(person.name, "");
    const avatar = https(person.avatar)
      ? h("img", { src: person.avatar, alt: "", referrerpolicy: "no-referrer", style: { width: "88px", height: "88px", borderRadius: "50%", objectFit: "cover" } })
      : h("span", { "aria-hidden": "true", style: { display: "inline-grid", placeItems: "center", width: "88px", height: "88px", borderRadius: "50%", background: "#e2e8f0", fontSize: "46px" }, text: person.avatar || "🙂" });
    root.append(h("section", { class: "pub-card" },
      h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, avatar, h("div", {}, h("h1", { style: { margin: 0 }, text: person.name }), h("p", { class: "pub-muted", style: { margin: 0 }, text: `@${person.handle} on Monosage Social` }))),
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
        post.photos.length ? h("div", { class: "pub-grid" }, ...post.photos.filter(https).map((src, index) => h("img", { src, alt: `Photo ${index + 1} from ${person.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", borderRadius: "10px" } }))) : null));
    }
  });

  const CONDITIONS = { new: "New", like_new: "Like new", good: "Good", used: "Used", for_parts: "For parts" };
  P.register("social", "listing", (root, response, ctx) => {
    const { h } = ctx;
    const data = response.page || {};
    if (!data.available) {
      root.append(h("section", { class: "pub-card" }, h("h1", { text: data.title || "This listing is no longer shared." }), h("p", { class: "pub-muted", text: "It may have been sold or removed." })));
      return;
    }
    const item = data.listing;
    if (!item.for_business) asPerson(item.seller.name, item.title);
    const sold = item.status === "sold";
    const photos = item.photos.filter(https);
    root.append(h("section", { class: "pub-card" },
      photos.length ? h("div", { class: "pub-grid" }, ...photos.map((src, index) => h("img", { src, alt: `Photo ${index + 1} of ${item.title}`, loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", borderRadius: "10px" } }))) : null,
      h("h1", { style: { overflowWrap: "anywhere" }, text: item.title }),
      h("p", { class: "pub-total", text: item.price }),
      sold || item.status === "reserved" ? h("p", {}, h("span", { class: "pub-badge", text: sold ? "Sold" : "Reserved" })) : null,
      h("p", { class: "pub-muted", text: [CONDITIONS[item.condition] || "", item.place ? `Pick up in ${item.place}` : "", `Sold by ${item.seller.name}`].filter(Boolean).join(" · ") }),
      item.about ? h("p", { class: "pub-pre", text: item.about }) : null,
      !sold && https(item.payment_link) ? h("div", { class: "pub-btn-row" }, h("a", { class: "pub-btn", href: item.payment_link, target: "_blank", rel: "noopener noreferrer", text: item.price_cents ? `Pay ${item.price}` : "Pay the seller" })) : null));
    if (sold) return;
    const name = h("input", { type: "text", maxlength: "60", autocomplete: "name" });
    const contact = h("input", { type: "text", maxlength: "120", autocomplete: "email" });
    const note = h("textarea", { maxlength: "500", rows: "3" });
    const send = ctx.button("I'm interested", async () => {
      clearNotices(root);
      await ctx.action("interested", { name: name.value, contact: contact.value, message: note.value });
      note.value = "";
      clearNotices(root);
      ctx.notice(`Thank you! ${item.seller.name} will get in touch with you.`, "good");
    });
    // the bottom padding keeps the end of the form clear of the floating language button on a phone
    root.append(h("section", { class: "pub-card", style: { marginBottom: "72px" } }, h("h2", { text: "Interested?" }), h("div", { class: "pub-form" },
      ctx.field("Your name", name), ctx.field("How to reach you (email or phone)", contact), ctx.field("Message (optional)", note), h("div", { class: "pub-btn-row" }, send))));
  });
})();

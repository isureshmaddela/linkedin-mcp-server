/* Aira — Social (#/social): Aira's own social network and messenger, safe for every age.
 *
 * Tabs (?tab=): feed (stories, posts, reactions, comments, shares; ?post=, ?tag=), chats (one-to-one and group chats,
 * two panes on a wide screen; ?chat=), friends (search, requests, suggestions, blocked people), groups (?group=),
 * pages (?page=), market (the Marketplace: buy and sell; ?listing=, ?view=selling|saved), profile (yours, or
 * ?person=), notifications and, for the household's owner, moderation. A grown-up also says yes or no to the
 * household's children's new friends (Friends, and a line on Today).
 * Everything runs through /api/social/* (aira/suite/social/social_routes.py) as the person using Aira (life.js).
 * Live pages poll with Aira.suite.live and a since= time. DOM is built with h() and textContent only.
 *
 * In the Aira frame (shell.js): "+ New" (Aira.appNew.social) asks with ?new=post|story|chat|group|listing and the
 * page opens that form once, then drops new= from the address; the search box across the top (Aira.appSearch.social)
 * finds people, chats, groups, pages, Marketplace listings and family members and events, and Enter lists them on
 * ?tab=search&q=. The tabs are the left menu's folders.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("social", path, opts);
  const go = (query) => Aira.router.go(["social"], query);
  // Children of a node without the empty ones (append(null) would print "null").
  const kids = (...nodes) => nodes.flat().filter((node) => node !== null && node !== undefined && node !== false && node !== "");

  const REACTIONS = ["❤️", "👍", "😂", "😮", "😢", "🎉"];
  const REACTION_NAMES = { "❤️": "love", "👍": "like", "😂": "laugh", "😮": "wow", "😢": "sad", "🎉": "celebrate" };
  const AUDIENCE_LABELS = { public: "Everyone", friends: "Friends", only_me: "Only me", group: "Group", page: "Page" };
  const REPORT_LABELS = { post: "post", comment: "comment", message: "message", account: "person", story: "story", listing: "listing" };
  const PAGE_SIZE = 30;
  const CATEGORY_LABELS = { home: "Home and furniture", clothes: "Clothes and shoes", kids: "Kids and baby", electronics: "Phones and electronics", vehicles: "Bikes and cars", sports: "Sports and outdoors", books: "Books, films and music", garden: "Garden and DIY", services: "Services and lessons", other: "Other" };
  const CONDITION_LABELS = { new: "New", like_new: "Like new", good: "Good", used: "Used", for_parts: "For parts" };
  const STATUS_LABELS = { available: "For sale", reserved: "Reserved", sold: "Sold" };
  const young = (me) => me.age_group === "child" || me.age_group === "teen";

  // A few glyphs the shared set lacks, for the menu's folders, "+ New" and the search list (added once, never replaced).
  const GLYPHS = {
    soPhoto: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5L6 20"/></svg>',
    soPen: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>',
    soStory: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke-dasharray="4 2.2"/><path d="M12 8v8M8 12h8"/></svg>',
    soGroup: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3"/><circle cx="5" cy="10" r="2.2"/><circle cx="19" cy="10" r="2.2"/><path d="M6.5 20a5.5 5.5 0 0 1 11 0M1.5 19a3.6 3.6 0 0 1 4.5-3.4M22.5 19a3.6 3.6 0 0 0-4.5-3.4"/></svg>',
    soFlag: '<svg viewBox="0 0 24 24"><path d="M5 21V4"/><path d="M5 4h12l-2 4 2 4H5"/></svg>',
    soTag: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.5"/></svg>',
    soPerson: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    soBell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/></svg>',
    soShield: '<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
  };
  for (const [name, svg] of Object.entries(GLYPHS)) if (Aira.ICONS && !Aira.ICONS[name]) Aira.ICONS[name] = svg;
  // Each tab is a folder in the frame's left menu; its label names the page and `about` is the line under it.
  const TAB_META = {
    feed: { label: "Feed", icon: "news", about: "Stories, posts and news from your friends, groups and pages." },
    chats: { label: "Chats", icon: "social", about: "Private chats with one friend or a group." },
    friends: { label: "Friends", icon: "recruiters", about: "Find people you know, answer requests and see who you may know." },
    groups: { label: "Groups", icon: "soGroup", about: "Groups you belong to, and public groups to join." },
    pages: { label: "Pages", icon: "soFlag", about: "Shops, clubs and services you follow, and the pages you run." },
    market: { label: "Marketplace", icon: "soTag", about: "Buy from people near you, and sell what you no longer need." },
    profile: { label: "Profile", icon: "soPerson", about: "How others see you on Social." },
    notifications: { label: "Notifications", icon: "soBell", about: "Comments, reactions, mentions and friend requests." },
    moderation: { label: "Moderation", icon: "soShield", about: "Reports from your household and the words Social hides." },
  };
  // What "+ New" asked for (?new=), answered once the page is drawn.
  let pending = "";
  // The Social account of the person using Aira, kept from the page's last GET /me ("+ New" and search read it).
  let known = null;
  // Children, then teens: what "+ New" and the search box leave out, like the pages do.
  const ageGroup = () => (Aira.life && Aira.life.me && Aira.life.me.age_group) || (known && known.age_group) || "adult";
  const familyOpen = () => (Aira.visibleNav ? Aira.visibleNav() : []).some((item) => item.id === "family") && !(Aira.apps && Aira.apps.blocked && Aira.apps.blocked("family"));
  // Poll while this screen is on the page. (Aira.suite.live alone keeps going while the person moves between tabs
  // of Social, because tabs live in the query: every visit would add another loop.)
  const live = (node, fn, ms) => {
    const stop = S.live(async () => { if (!node.isConnected) { stop(); return; } await fn(); }, ms);
    return stop;
  };

  const face = (person, size = 40) => {
    const avatar = (person && person.avatar) || "🙂";
    const box = { display: "inline-grid", placeItems: "center", width: `${size}px`, height: `${size}px`, borderRadius: "50%", background: "var(--surface-2)", fontSize: `${Math.round(size * 0.55)}px`, flex: "none", overflow: "hidden" };
    if (/^https:\/\//i.test(avatar)) return h("img", { src: avatar, alt: "", referrerpolicy: "no-referrer", loading: "lazy", style: { ...box, objectFit: "cover" } });
    return h("span", { "aria-hidden": "true", style: box, text: avatar });
  };
  const when = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
    if (date.toDateString() === new Date().toDateString()) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };
  const personLink = (person) => h("a", { href: `#/social?tab=profile&person=${person.id}`, text: person.name, style: { fontWeight: "600" } });
  const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  // Text with #tags and @mentions as links (built as text nodes, never HTML).
  const richText = (text) => {
    const node = h("p", { style: { whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: "16px", margin: "0" } });
    const pattern = /(#[\p{L}\p{N}_]{2,40}|@[a-z][a-z0-9_.]{2,29})/giu;
    let last = 0;
    for (const match of String(text || "").matchAll(pattern)) {
      if (match.index > last) node.append(document.createTextNode(text.slice(last, match.index)));
      const word = match[0];
      node.append(word.startsWith("#")
        ? h("a", { href: `#/social?tab=feed&tag=${encodeURIComponent(word.slice(1).toLowerCase())}`, text: word })
        : h("a", { href: `#/social?tab=friends&q=${encodeURIComponent(word.slice(1))}`, text: word }));
      last = match.index + word.length;
    }
    if (last < String(text || "").length) node.append(document.createTextNode(text.slice(last)));
    return node;
  };
  const photoGrid = (photos, owner) => (photos && photos.length ? h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: "8px" } }, ...photos.filter((src) => /^https:\/\//i.test(src)).map((src, index) => h("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, h("img", { src, alt: `Photo ${index + 1} from ${owner}`, loading: "lazy", referrerpolicy: "no-referrer", style: { width: "100%", aspectRatio: "4 / 3", objectFit: "cover", borderRadius: "10px", background: "var(--surface-2)" } })))) : null);
  const photoList = (value) => String(value || "").split(/\s+/).filter(Boolean);

  const report = (targetType, targetId) => S.edit({
    title: `Report this ${REPORT_LABELS[targetType]}`,
    fields: [{ name: "reason", label: "What is wrong?", type: "textarea", rows: 3, placeholder: "It is unkind, unsafe, a scam or not true." }],
    submitLabel: "Send report",
    onSave: async (values) => { await api("/reports", { method: "POST", body: { target_type: targetType, target_id: targetId, reason: values.reason } }); Aira.toast("Thank you. Your report was sent to the moderators.", "success"); },
  });
  const block = async (person) => {
    if (!(await Aira.confirm({ title: `Block ${person.name}?`, message: `${person.name} cannot see your profile or posts, message you or find you. You stop being friends.`, confirmLabel: "Block", danger: true }))) return false;
    await api(`/people/${person.id}/block`, { method: "POST", body: {} });
    Aira.toast(`${person.name} is blocked.`, "success");
    return true;
  };
  const message = async (person) => {
    const created = await api("/chats", { method: "POST", body: { person_id: person.id } });
    go({ tab: "chats", chat: created.chat.id });
  };

  // ---------------------------------------------------------------------------
  // Posts
  // ---------------------------------------------------------------------------
  function postNode(post, ctx) {
    const reactionRow = h("div", { class: "btn-row", role: "group", "aria-label": "Reactions" }, ...REACTIONS.map((emoji) => {
      const n = post.reactions[emoji] || 0;
      const mine = post.my_reaction === emoji;
      return h("button", { type: "button", class: `btn sm ${mine ? "secondary" : "ghost"}`, "aria-pressed": String(mine), "aria-label": `${REACTION_NAMES[emoji]}${n ? ` (${n})` : ""}`, onclick: async () => { const result = await api(`/posts/${post.id}/react`, { method: "POST", body: { emoji: mine ? null : emoji } }); Object.assign(post, result.post); ctx.redraw(); } }, h("span", { text: emoji }), n ? h("span", { text: String(n) }) : null);
    }));
    // Open comments and half-typed comments survive the live feed redrawing this post.
    const comments = h("div", { hidden: !(ctx.open || ctx.openIds.has(post.id)), class: "suite-body" });
    const loadComments = async () => {
      const data = await api(`/posts/${post.id}/comments`);
      const input = h("input", { type: "text", placeholder: "Write a comment", "aria-label": `Comment on ${post.author.name}'s post`, maxlength: "1000", "data-comment-for": post.id, value: ctx.drafts.get(post.id) || "" });
      input.addEventListener("input", () => { if (input.value) ctx.drafts.set(post.id, input.value); else ctx.drafts.delete(post.id); });
      const send = async () => { if (!input.value.trim()) return; await api(`/posts/${post.id}/comments`, { method: "POST", body: { text: input.value } }); ctx.drafts.delete(post.id); post.comments += 1; await loadComments(); input.focus(); };
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); send(); } });
      if (ctx.refocus === post.id) { ctx.refocus = ""; setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 0); }
      comments.replaceChildren(
        data.comments.length ? h("ul", { class: "suite-activity" }, ...data.comments.map((item) => h("li", {}, face(item.author, 28),
          h("div", { style: { flex: "1", minWidth: "0" } }, personLink(item.author), h("span", { text: ` ${item.text}` }), h("div", { class: "muted small", text: when(item.created_at) })),
          item.can_delete ? h("button", { type: "button", class: "btn ghost sm", text: "Delete", "aria-label": "Delete comment", onclick: async () => { await api(`/comments/${item.id}`, { method: "DELETE" }); post.comments = Math.max(0, post.comments - 1); await loadComments(); } }) : null,
          item.mine ? null : h("button", { type: "button", class: "btn ghost sm", text: "Report", "aria-label": "Report comment", onclick: () => report("comment", item.id) })))) : h("p", { class: "muted", text: "No comments yet. Be the first." }),
        h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1", minWidth: "0" } }, input), S.button("Comment", send, { kind: "secondary sm" })));
    };
    if (!comments.hidden) loadComments();
    const share = async () => {
      await S.edit({ title: "Share this post", fields: [{ name: "text", label: "Say something about it (optional)", type: "textarea", rows: 3 }], submitLabel: "Share", onSave: async (values) => { await api("/posts", { method: "POST", body: { shared_post_id: post.id, text: values.text, audience: ctx.young ? "friends" : "public" } }); Aira.toast("Shared on your profile.", "success"); await ctx.reload(); } });
    };
    const where = post.group ? h("a", { href: `#/social?tab=groups&group=${post.group.id}`, text: ` in ${post.group.name}` }) : post.page ? h("a", { href: `#/social?tab=pages&page=${post.page.id}`, text: ` · ${post.page.name}` }) : null;
    const shared = post.shared ? (post.shared.gone ? h("p", { class: "muted", text: "This post is no longer available." }) : h("div", { class: "card", style: { padding: "12px", display: "grid", gap: "8px" } }, h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } }, face(post.shared.author, 28), personLink(post.shared.author), h("span", { class: "muted small", text: when(post.shared.created_at) })), post.shared.text ? richText(post.shared.text) : null, photoGrid(post.shared.photos, post.shared.author.name))) : null;
    return h("article", { class: "card suite-section", "aria-label": `Post by ${post.author.name}` },
      h("header", { style: { display: "flex", gap: "12px", alignItems: "center" } }, face(post.author, 44),
        h("div", { style: { flex: "1", minWidth: "0" } }, personLink(post.author), where, h("div", { class: "muted small", text: `${when(post.created_at)} · ${AUDIENCE_LABELS[post.audience] || ""}${post.edited ? " · edited" : ""}` }))),
      post.text ? richText(post.text) : null,
      photoGrid(post.photos, post.author.name),
      shared,
      reactionRow,
      h("div", { class: "btn-row" },
        S.button(post.comments ? count(post.comments, "comment", "comments") : "Comment", async () => { comments.hidden = !comments.hidden; if (comments.hidden) ctx.openIds.delete(post.id); else { ctx.openIds.add(post.id); await loadComments(); } }, { kind: "ghost sm" }),
        post.can_share ? S.button("Share", share, { kind: "ghost sm" }) : null,
        post.mine ? S.button("Edit", () => S.edit({ title: "Edit your post", fields: [{ name: "text", label: "Your post", type: "textarea", rows: 5 }], values: post, onSave: async (values) => { const result = await api(`/posts/${post.id}`, { method: "PATCH", body: { text: values.text } }); Object.assign(post, result.post); ctx.redraw(); } }), { kind: "ghost sm" }) : null,
        post.mine ? S.button("Delete", async () => { if (await S.confirmDelete("this post")) { await api(`/posts/${post.id}`, { method: "DELETE" }); ctx.remove(post.id); } }, { kind: "ghost sm" }) : null,
        post.mine ? null : S.button("Report", () => report("post", post.id), { kind: "ghost sm" })),
      comments);
  }

  // A list of posts that redraws in place; ``load`` returns the posts. Only posts that changed are rebuilt, and
  // nodes that stay are not moved, so a comment being typed keeps its text and focus while the feed updates.
  function postList(container, me, load, { open = false, empty } = {}) {
    const state = { posts: [] };
    const list = h("div", { class: "suite-body" });
    const built = new Map();
    const emptyNode = empty || Aira.ui.empty("Nothing here yet", "Posts appear here.");
    const ctx = {
      me, open, young: young(me), openIds: new Set(), drafts: new Map(), refocus: "",
      redraw: () => {
        const active = document.activeElement;
        const typing = active && active.getAttribute ? active.getAttribute("data-comment-for") : "";
        const wanted = state.posts.length ? state.posts.map((post) => {
          const key = JSON.stringify(post);
          const known = built.get(post.id);
          if (known && known.key === key) return known.node;
          if (typing === post.id) ctx.refocus = post.id;
          const node = postNode(post, ctx);
          built.set(post.id, { key, node });
          return node;
        }) : [emptyNode];
        for (const id of [...built.keys()]) if (!state.posts.some((post) => post.id === id)) built.delete(id);
        wanted.forEach((node, index) => { if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
        while (list.children.length > wanted.length) list.lastChild.remove();
      },
      reload: async () => { state.posts = await load(); built.clear(); ctx.redraw(); },
      remove: (id) => { state.posts = state.posts.filter((post) => post.id !== id); ctx.redraw(); },
    };
    container.append(list);
    return { state, ctx };
  }

  function composer(me, { groupId = "", pageId = "", placeholder, onPosted }) {
    const young = me.age_group === "child" || me.age_group === "teen";
    const text = h("textarea", { rows: "3", placeholder: placeholder || `What's on your mind, ${me.display_name}?`, "aria-label": "Your post", maxlength: "2000", "data-so-composer": "", style: { fontSize: "16px" } });
    const photos = h("textarea", { rows: "2", placeholder: "Photo links (https://…), one per line, up to 6", "aria-label": "Photo links" });
    const audience = Aira.ui.select(young ? [["friends", "Friends"], ["only_me", "Only me"]] : [["friends", "Friends"], ["public", "Everyone"], ["only_me", "Only me"]], "friends", { "aria-label": "Who can see it" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    // The photo links wait behind a button: most posts are words, and the box stays short.
    const photoField = Aira.ui.field("Photos (optional)", photos);
    photoField.hidden = true;
    const addPhotos = h("button", { type: "button", class: "btn ghost sm", "aria-expanded": "false", onclick: () => { photoField.hidden = false; addPhotos.hidden = true; addPhotos.setAttribute("aria-expanded", "true"); photos.focus(); } }, Aira.icon("soPhoto"), h("span", { text: "Add photos" }));
    const post = async () => {
      error.hidden = true;
      try {
        await api("/posts", { method: "POST", body: { text: text.value, photos: photoList(photos.value), audience: audience.value, group_id: groupId, page_id: pageId } });
        text.value = ""; photos.value = "";
        photoField.hidden = true; addPhotos.hidden = false; addPhotos.setAttribute("aria-expanded", "false");
        Aira.toast("Posted.", "success");
        await onPosted();
      } catch (err) { error.textContent = err.message; error.hidden = false; }
    };
    const who = groupId || pageId ? null : h("label", { class: "so-audience" }, h("span", { class: "muted small", text: "Who can see it" }), audience);
    return h("section", { class: "card suite-section so-composer" },
      h("h2", { class: "suite-h2", text: groupId ? "Post in the group" : pageId ? "Post as the page" : "Share something" }),
      h("div", { class: "so-composer-row" }, face(me, 40), h("div", { class: "so-composer-text" }, text)),
      photoField, error,
      h("div", { class: "so-composer-actions" }, addPhotos, who, h("span", { class: "so-grow" }), S.button("Post", post)),
      young && !groupId && !pageId ? h("p", { class: "muted small", style: { margin: 0 }, text: "Young people share with friends only." }) : null);
  }

  // ---------------------------------------------------------------------------
  // Stories
  // ---------------------------------------------------------------------------
  function showStories(person, onDone) {
    let index = Math.max(0, person.stories.findIndex((story) => !story.seen));
    const view = h("div", { style: { display: "grid", gap: "10px" } });
    const draw = () => {
      const story = person.stories[index];
      if (!person.mine) api(`/stories/${story.id}/seen`, { method: "POST", body: {}, silent: true }).catch(() => {});
      story.seen = true;
      view.replaceChildren(
        h("div", { style: { minHeight: "240px", borderRadius: "16px", padding: "20px", display: "grid", placeItems: "center", background: story.color, color: "#fff", fontSize: "22px", textAlign: "center", whiteSpace: "pre-wrap" } },
          story.photo ? h("img", { src: story.photo, alt: `Story photo from ${person.author.name}`, referrerpolicy: "no-referrer", style: { maxWidth: "100%", maxHeight: "50vh", borderRadius: "12px" } }) : null,
          story.text ? h("div", { text: story.text }) : null),
        h("p", { class: "muted small", text: `${index + 1} of ${person.stories.length} · ${when(story.created_at)}${person.mine && story.views !== null ? ` · seen by ${count(story.views, "person", "people")}` : ""}` }),
        h("div", { class: "btn-row" },
          index > 0 ? S.button("Back", () => { index -= 1; draw(); }, { kind: "ghost" }) : null,
          index < person.stories.length - 1 ? S.button("Next", () => { index += 1; draw(); }) : null,
          person.mine ? S.button("Delete this story", async () => { await api(`/stories/${story.id}`, { method: "DELETE" }); dialog.close(); }, { kind: "ghost" }) : S.button("Report", () => report("story", story.id), { kind: "ghost" })));
    };
    const dialog = Aira.modal({ title: `${person.author.name}'s story`, body: view, actions: [{ label: "Close", kind: "secondary" }], onClose: onDone });
    draw();
  }

  const addStory = (onDone) => S.edit({ title: "Add a story (24 hours)", fields: [{ name: "text", label: "Text", type: "textarea", rows: 3 }, { name: "photo", label: "Photo link (https://…, optional)", type: "url" }, { name: "color", label: "Background colour", type: "color" }], values: { color: "#2563eb" }, submitLabel: "Share the story", onSave: async (values) => { await api("/stories", { method: "POST", body: values }); Aira.toast("Your story is up for 24 hours.", "success"); await onDone(); } });

  async function storiesStrip(me) {
    const strip = h("div", { role: "list", "aria-label": "Stories", class: "so-stories" });
    const draw = async () => {
      const data = await api("/stories", { silent: true });
      strip.replaceChildren(...kids(
        h("div", { role: "listitem" }, h("button", { type: "button", class: "so-story so-story-add", onclick: () => addStory(draw) },
          h("span", { class: "so-story-ring" }, face(me, 52), h("span", { class: "so-story-plus", "aria-hidden": "true" }, Aira.icon("plus"))),
          h("span", { class: "small", text: "Add a story" }))),
        ...data.people.map((person) => h("div", { role: "listitem" }, h("button", { type: "button", class: "so-story", "aria-label": `${person.author.name}'s story${person.unseen ? ` (${person.unseen} new)` : ""}`, onclick: () => showStories(person, draw) },
          h("span", { class: `so-story-ring ${person.unseen ? "so-unseen" : ""}` }, face(person.author, 52)),
          h("span", { class: "small", text: person.mine ? "Your story" : person.author.name })))),
        data.people.length ? null : h("p", { class: "muted small so-stories-hint", text: "Stories last a day: a photo or a few words for your friends." })));
    };
    strip.redraw = draw;
    await draw();
    return strip;
  }

  // ---------------------------------------------------------------------------
  // Feed
  // ---------------------------------------------------------------------------
  async function feedTab(body, me, route) {
    if (route.query.post) {
      body.append(h("div", { class: "btn-row" }, S.button("Back to the feed", () => go({ tab: "feed" }), { kind: "ghost" })));
      const { ctx } = postList(body, me, async () => [(await api(`/posts/${route.query.post}`)).post], { open: true });
      await ctx.reload();
      return;
    }
    if (route.query.tag) {
      body.append(h("div", { class: "btn-row" }, h("h2", { class: "suite-h2", text: `#${route.query.tag}` }), S.button("Back to the feed", () => go({ tab: "feed" }), { kind: "ghost" })));
      const { ctx } = postList(body, me, async () => (await api(`/tags?tag=${encodeURIComponent(route.query.tag)}`)).posts, { empty: Aira.ui.empty("No posts with this tag", "Posts you can see with this tag appear here.") });
      await ctx.reload();
      return;
    }
    body.append(S.section("Stories", await storiesStrip(me)));
    let now = "";
    const listHost = h("div", { class: "suite-body" });
    const wrap = postList(listHost, me, async () => { const data = await api("/feed"); now = data.now; return data.posts; }, { empty: Aira.ui.empty("Your feed is empty", "Add friends, follow pages or join groups: their posts appear here.", h("div", { class: "btn-row", style: { justifyContent: "center" } }, S.button("Find friends", () => go({ tab: "friends" }), { kind: "secondary sm" }), S.button("Discover groups", () => go({ tab: "groups" }), { kind: "ghost sm" }))) });
    body.append(composer(me, { onPosted: () => wrap.ctx.reload() }), listHost);
    await wrap.ctx.reload();
    // Older posts exist only when the first page came back full.
    const older = S.button("Show older posts", async () => {
      const oldest = wrap.state.posts[wrap.state.posts.length - 1];
      if (!oldest) return;
      const data = await api(`/feed?before=${encodeURIComponent(oldest.created_at)}`);
      wrap.state.posts.push(...data.posts.filter((post) => !wrap.state.posts.some((item) => item.id === post.id)));
      wrap.ctx.redraw();
      if (data.posts.length < PAGE_SIZE) older.hidden = true;
    }, { kind: "ghost" });
    older.hidden = wrap.state.posts.length < PAGE_SIZE;
    body.append(h("div", { class: "btn-row" }, older));
    live(listHost, async () => {
      const changed = await api(`/feed?since=${encodeURIComponent(now)}`, { silent: true });
      now = changed.now;
      if (!changed.posts.length && !changed.removed.length) return;
      for (const id of changed.removed) wrap.state.posts = wrap.state.posts.filter((post) => post.id !== id);
      for (const post of changed.posts) {
        const index = wrap.state.posts.findIndex((item) => item.id === post.id);
        if (index >= 0) wrap.state.posts[index] = post; else wrap.state.posts.unshift(post);
      }
      wrap.state.posts.sort((a, b) => (b.created_at > a.created_at ? 1 : -1));
      wrap.ctx.redraw();
    }, 5000);
  }

  // ---------------------------------------------------------------------------
  // Chats
  // ---------------------------------------------------------------------------
  async function newChat() {
    const friends = (await api("/friends")).friends;
    if (!friends.length) { Aira.toast("Add friends first: you can message your friends.", "info"); go({ tab: "friends" }); return; }
    await S.edit({
      title: "New chat",
      fields: [
        { name: "title", label: "Group name (only for a group chat)", placeholder: "Weekend plans" },
        { type: "heading", label: "Who (tick one for a private chat, more for a group)" },
        ...friends.map((friend) => ({ name: `with_${friend.id}`, label: `${/^https:/i.test(friend.avatar) ? "" : friend.avatar} ${friend.name} (@${friend.handle})`, type: "checkbox" })),
      ],
      submitLabel: "Start chatting",
      onSave: async (values) => {
        const chosen = friends.filter((friend) => values[`with_${friend.id}`]).map((friend) => friend.id);
        if (!chosen.length) throw new Error("Tick at least one friend.");
        const created = chosen.length === 1 && !String(values.title || "").trim()
          ? await api("/chats", { method: "POST", body: { person_id: chosen[0] } })
          : await api("/chats", { method: "POST", body: { title: values.title || "Group chat", member_ids: chosen } });
        go({ tab: "chats", chat: created.chat.id });
      },
    });
  }

  async function chatsTab(body, me, route) {
    const narrow = window.matchMedia && window.matchMedia("(max-width: 900px)").matches;
    const chatId = route.query.chat || "";
    const listBox = h("nav", { "aria-label": "Chats", style: { display: "grid", gap: "6px", alignContent: "start" } });
    const drawList = (data) => listBox.replaceChildren(
      h("div", { class: "so-list-head" }, S.button("New chat", newChat, { icon: "plus" })),
      ...(data.chats.length ? data.chats.map((chat) => h("a", { href: `#/social?tab=chats&chat=${chat.id}`, class: `btn ${chat.id === chatId ? "secondary" : "ghost"}`, "aria-current": chat.id === chatId ? "true" : "false", style: { justifyContent: "flex-start", gap: "10px", minHeight: "56px" } },
        chat.kind === "group" ? h("span", { "aria-hidden": "true", style: { fontSize: "24px" }, text: "👥" }) : face({ avatar: chat.avatar }, 36),
        h("span", { style: { flex: "1", textAlign: "left", minWidth: "0" } }, h("strong", { text: chat.title }), chat.last ? h("span", { class: "muted small", style: { display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: `${chat.last.mine ? "You: " : ""}${chat.last.deleted ? "Message deleted" : chat.last.text}` }) : null),
        chat.muted ? h("span", { class: "muted small", text: "Muted" }) : null,
        chat.unread ? h("span", { "aria-label": `${chat.unread} unread`, text: String(chat.unread), style: { background: "var(--brand)", color: "var(--on-brand)", borderRadius: "999px", padding: "1px 8px", fontSize: "13px" } }) : null)) : [narrow ? Aira.ui.empty("No chats yet", "Chat with one friend, or start a group chat for the family or a club.") : h("p", { class: "muted", text: "No chats yet." })]));
    let listData = await api("/chats");
    drawList(listData);
    if (!chatId) {
      // A wide screen keeps the conversation's place beside the list, like a messenger.
      body.append(narrow ? S.section("", listBox) : h("div", { class: "so-chat-grid" }, S.section("", listBox),
        h("section", { class: "card suite-section so-chat-pick" }, Aira.ui.empty(listData.chats.length ? "Choose a chat" : "Start a conversation", listData.chats.length ? "Pick a chat on the left to read and reply." : "Message a friend, or start a group chat.", S.button("New chat", newChat, { kind: "secondary sm", icon: "plus" })))));
      live(listBox, async () => { listData = await api("/chats", { silent: true }); drawList(listData); }, 4000);
      return;
    }

    const first = await api(`/chats/${chatId}/messages`);
    const chat = first.chat;
    const state = { messages: first.messages, now: first.now, readUpto: first.read_upto || "" };
    const thread = h("div", { role: "log", "aria-live": "polite", "aria-label": `Messages with ${chat.title}`, style: { display: "grid", gap: "10px", alignContent: "start", maxHeight: "60vh", minHeight: "240px", overflowY: "auto", padding: "6px" } });
    // Redrawn only when something changed; it follows new messages only when you are at the bottom, so you can
    // scroll up and read older ones in peace.
    const draw = (follow = false) => {
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
      for (const item of state.messages) if (item.mine) item.seen = !!state.readUpto && item.created_at <= state.readUpto;
      thread.replaceChildren(...(state.messages.length ? state.messages.map((item) => {
        const bubble = h("div", { style: { maxWidth: "100%", padding: "10px 14px", borderRadius: "18px", background: item.mine ? "var(--brand)" : "var(--surface-2)", color: item.mine ? "var(--on-brand)" : "var(--ink)", fontSize: "16px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } },
          item.deleted ? h("em", { text: "This message was deleted." }) : null,
          item.text ? h("div", { text: item.text }) : null,
          item.photo ? h("a", { href: item.photo, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: item.photo, alt: `Photo from ${item.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" } })) : null);
        return h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", justifyContent: item.mine ? "flex-end" : "flex-start" } },
          item.mine ? null : face(item.author, 30),
          h("div", { style: { display: "grid", gap: "2px", maxWidth: "min(80%, 560px)", minWidth: "0", justifyItems: item.mine ? "end" : "start" } },
            item.mine || chat.kind === "direct" ? null : h("span", { class: "muted small", text: item.author.name }),
            bubble,
            h("span", { class: "muted small" }, h("span", { text: `${when(item.created_at)}${item.mine && !item.deleted ? (item.seen ? " · Seen" : " · Sent") : ""}` }),
              item.can_delete ? h("button", { type: "button", class: "btn ghost sm", text: "Delete for everyone", onclick: async () => { await api(`/messages/${item.id}`, { method: "DELETE" }); await poll(); } }) : null,
              !item.mine && !item.deleted ? h("button", { type: "button", class: "btn ghost sm", text: "Report", "aria-label": "Report this message", onclick: () => report("message", item.id) }) : null)));
      }) : [Aira.ui.empty("No messages yet", "Say hello!")]));
      if (follow || atBottom) thread.scrollTop = thread.scrollHeight;
    };
    const markRead = () => api(`/chats/${chatId}/read`, { method: "POST", body: {}, silent: true }).catch(() => {});
    let ticks = 0;
    const poll = async (follow = false) => {
      const data = await api(`/chats/${chatId}/messages?since=${encodeURIComponent(state.now)}`, { silent: true });
      state.now = data.now;
      let changed = follow;
      for (const item of data.messages) {
        const index = state.messages.findIndex((m) => m.id === item.id);
        if (item.removed) { if (index >= 0) state.messages.splice(index, 1); changed = true; continue; }
        if (index >= 0) state.messages[index] = item; else state.messages.push(item);
        changed = true;
      }
      if (data.messages.length) markRead();
      // "Seen" moves when the others read, without new messages
      if ((data.read_upto || "") !== state.readUpto) { state.readUpto = data.read_upto || ""; changed = true; }
      // the list beside the chat (wide screens) shows the other chats' new messages
      ticks += 1;
      if (!narrow && ticks % 5 === 0) { listData = await api("/chats", { silent: true }); drawList(listData); }
      if (changed) draw(follow);
    };
    draw();
    markRead();

    const closed = !chat.can_send;
    const input = h("textarea", { rows: "2", placeholder: chat.blocked ? "You cannot message this person." : closed ? "You can message friends, and people who accept messages from everyone." : `Message ${chat.title}`, "aria-label": "Your message", maxlength: "4000", disabled: closed, style: { fontSize: "16px" } });
    const send = async () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      await api(`/chats/${chatId}/messages`, { method: "POST", body: { text } });
      input.value = "";
      await poll(true);
      input.focus();
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } });
    const other = chat.kind === "direct" ? chat.members[0] : null;
    const head = h("div", { style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } },
      narrow ? S.button("All chats", () => go({ tab: "chats" }), { kind: "ghost sm" }) : null,
      chat.kind === "group" ? h("span", { "aria-hidden": "true", style: { fontSize: "26px" }, text: "👥" }) : face({ avatar: chat.avatar }, 36),
      h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: chat.title }), chat.kind === "group" ? h("span", { class: "muted small", text: chat.members.map((m) => m.name).join(", ") }) : null),
      h("div", { class: "btn-row" },
        S.button(chat.muted ? "Unmute" : "Mute", async () => { await api(`/chats/${chatId}/mute`, { method: "POST", body: { muted: !chat.muted } }); Aira.router.render(); }, { kind: "ghost sm" }),
        chat.kind === "group" && chat.admin ? S.button("Add people", async () => {
          const friends = (await api("/friends")).friends.filter((f) => !chat.members.some((m) => m.id === f.id));
          if (!friends.length) { Aira.toast("All your friends are already here.", "info"); return; }
          await S.edit({ title: "Add people", fields: [{ name: "person_id", label: "Friend", type: "select", options: friends.map((f) => [f.id, `${f.name} (@${f.handle})`]) }], values: { person_id: friends[0].id }, submitLabel: "Add", onSave: async (values) => { await api(`/chats/${chatId}/members`, { method: "POST", body: { person_id: values.person_id } }); Aira.router.render(); } });
        }, { kind: "ghost sm" }) : null,
        chat.kind === "group" && chat.admin && chat.members.length ? S.button("Remove someone", () => S.edit({ title: "Remove someone from the chat", fields: [{ name: "person_id", label: "Person", type: "select", options: chat.members.map((m) => [m.id, m.name]) }], values: { person_id: chat.members[0].id }, submitLabel: "Remove", onSave: async (values) => { await api(`/chats/${chatId}/members/${values.person_id}`, { method: "DELETE" }); Aira.router.render(); } }), { kind: "ghost sm" }) : null,
        other ? S.button("Profile", () => go({ tab: "profile", person: other.id }), { kind: "ghost sm" }) : null,
        other && !chat.blocked ? S.button("Block", async () => { if (await block(other)) Aira.router.render(); }, { kind: "ghost sm" }) : null,
        S.button(chat.kind === "group" ? "Leave group" : "Hide chat", async () => { if (await Aira.confirm({ title: chat.kind === "group" ? "Leave this group chat?" : "Hide this chat?", message: chat.kind === "group" ? "You stop getting its messages." : "It comes back when a new message arrives.", confirmLabel: chat.kind === "group" ? "Leave" : "Hide", danger: chat.kind === "group" })) { await api(`/chats/${chatId}/leave`, { method: "POST", body: {} }); go({ tab: "chats" }); } }, { kind: "ghost sm" })));
    const photoButton = S.button("Send a photo", () => S.edit({ title: "Send a photo", fields: [{ name: "photo", label: "Photo link (https://…)", type: "url", required: true }], onSave: async (values) => { await api(`/chats/${chatId}/messages`, { method: "POST", body: { photo: values.photo } }); await poll(true); } }), { kind: "ghost" });
    const sendButton = h("button", { type: "button", class: "btn", disabled: closed, style: { minHeight: "52px", minWidth: "110px", fontSize: "17px" }, text: "Send", onclick: (event) => Aira.withBusy(event.currentTarget, send) });
    const about = chat.listing_id ? h("p", { class: "muted small", style: { margin: 0 } }, h("span", { text: "This chat is about something on the Marketplace. " }), h("a", { href: `#/social?tab=market&listing=${chat.listing_id}`, text: "See the listing" })) : null;
    const threadCard = h("section", { class: "card suite-section", style: { minWidth: "0" } }, ...kids(head, about, thread,
      h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", flexWrap: "wrap" } }, h("div", { style: { flex: "1 1 240px" } }, input), closed ? null : photoButton, sendButton)));
    body.append(narrow ? threadCard : h("div", { class: "so-chat-grid" }, S.section("", listBox), threadCard));
    thread.scrollTop = thread.scrollHeight;
    live(thread, poll, 2000);
  }

  // ---------------------------------------------------------------------------
  // Friends
  // ---------------------------------------------------------------------------
  const relationButtons = (person, relation, after) => {
    if (relation === "friends") return [S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" })];
    if (relation === "request_received") return [S.button("Accept", async () => { await api(`/friends/${person.id}`, { method: "POST", body: {} }); Aira.toast(`You and ${person.name} are friends now.`, "success"); await after(); }, { kind: "sm" }), S.button("Decline", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "request_sent") return [S.button("Cancel request", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "awaiting_parent") return [h("span", { class: "muted small", text: "Waiting for a grown-up to say yes" }), S.button("Cancel", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "self") return [];
    return [S.button("Add friend", async () => {
      const result = await api(`/friends/${person.id}`, { method: "POST", body: {} });
      Aira.toast(result.relation === "awaiting_parent" ? "A grown-up in your family will say yes or no first." : "Friend request sent.", "success");
      await after();
    }, { kind: "secondary sm" })];
  };
  // A grown-up's answer to a child's new friend.
  const guardianRow = (item, after) => h("li", { class: "life-person", style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } },
    face(item.other, 40),
    h("div", { style: { flex: "1 1 220px", minWidth: "0" } },
      h("strong", { text: `${item.child.name} ${item.action === "accept" ? "wants to accept a request from" : "wants to be friends with"} ` }), personLink(item.other), h("span", { class: "muted small", text: ` @${item.other.handle}` }),
      h("div", { class: "muted small", text: [item.other.young ? "Also young" : "An adult", item.other.friends_with_you ? "your friend" : "", item.other.mutual_friends ? count(item.other.mutual_friends, "friend in common with you", "friends in common with you") : ""].filter(Boolean).join(" · ") })),
    S.button("Say yes", async () => { await api(`/guardian/${item.child.id}/${item.other.id}`, { method: "POST", body: { approve: true } }); Aira.toast(`${item.child.name} can be friends with ${item.other.name}.`, "success"); await after(); }, { kind: "sm" }),
    S.button("Say no", async () => { await api(`/guardian/${item.child.id}/${item.other.id}`, { method: "POST", body: { approve: false } }); await after(); }, { kind: "ghost sm" }));
  const personRow = (person, ...buttons) => h("li", { class: "life-person", style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } }, face(person, 40), h("div", { style: { flex: "1", minWidth: "0" } }, personLink(person), h("span", { class: "muted small", text: ` @${person.handle}` }), person.headline ? h("div", { class: "muted small", text: person.headline }) : null), ...buttons.flat().filter(Boolean));

  async function friendsTab(body, me, route) {
    const q = h("input", { type: "search", value: route.query.q || "", placeholder: "Search by name or @name", "aria-label": "Search people, groups and pages" });
    const results = h("div", { class: "suite-body" });
    const runSearch = async () => {
      const query = q.value.trim();
      if (query.length < 2) { results.replaceChildren(); return; }
      const data = await api(`/search?q=${encodeURIComponent(query)}`);
      results.replaceChildren(...kids(
        data.people.length ? h("ul", { class: "life-people" }, ...data.people.map((person) => personRow(person, relationButtons(person, person.relation, runSearch)))) : h("p", { class: "muted", text: "Nobody found. Check the spelling, or ask them for their @name." }),
        data.groups.length ? h("p", {}, h("strong", { text: "Groups: " }), ...data.groups.map((g) => h("a", { href: `#/social?tab=groups&group=${g.id}`, text: `${g.name}  ` }))) : null,
        data.pages.length ? h("p", {}, h("strong", { text: "Pages: " }), ...data.pages.map((p) => h("a", { href: `#/social?tab=pages&page=${p.id}`, text: `${p.name}  ` }))) : null));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); runSearch(); } });
    body.append(S.section("Find people", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", runSearch)), results));
    if (q.value) await runSearch();
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const draw = async () => {
      const data = await api("/friends");
      const blocked = (await api("/blocks")).blocked;
      panel.replaceChildren(...kids(
        data.children_asking && data.children_asking.length ? S.section(`Your children's new friends (${data.children_asking.length})`, h("p", { class: "muted", text: "A child's new friend from outside your family waits for a grown-up to say yes." }), h("ul", { class: "life-people" }, ...data.children_asking.map((item) => guardianRow(item, draw)))) : null,
        data.incoming.length ? S.section(`Friend requests (${data.incoming.length})`, h("ul", { class: "life-people" }, ...data.incoming.map((person) => personRow(person, relationButtons(person, "request_received", draw))))) : null,
        data.awaiting_parent && data.awaiting_parent.length ? S.section("Waiting for a grown-up", h("ul", { class: "life-people" }, ...data.awaiting_parent.map((person) => personRow(person, relationButtons(person, "awaiting_parent", draw))))) : null,
        S.section(`Your friends (${data.friends.length})`, data.friends.length ? h("ul", { class: "life-people" }, ...data.friends.map((person) => personRow(person, S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" }), S.button("Unfriend", async () => { if (await Aira.confirm({ title: `Unfriend ${person.name}?`, message: "You can send a new request later.", confirmLabel: "Unfriend" })) { await api(`/friends/${person.id}`, { method: "DELETE" }); await draw(); } }, { kind: "ghost sm" })))) : Aira.ui.empty("No friends yet", "Search for people you know above, or accept a request.")),
        data.suggestions.length ? S.section("People you may know", h("ul", { class: "life-people" }, ...data.suggestions.map((person) => personRow(person, relationButtons(person, "none", draw))))) : null,
        data.outgoing.length ? S.section("Requests you sent", h("ul", { class: "life-people" }, ...data.outgoing.map((person) => personRow(person, relationButtons(person, "request_sent", draw))))) : null,
        blocked.length ? S.section("Blocked people", h("ul", { class: "life-people" }, ...blocked.map((person) => personRow(person, S.button("Unblock", async () => { await api(`/people/${person.id}/block`, { method: "DELETE" }); await draw(); }, { kind: "ghost sm" }))))) : null));
    };
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Groups and pages
  // ---------------------------------------------------------------------------
  const GROUP_FIELDS = [{ name: "name", label: "Group name", required: true, placeholder: "Neighbourhood walkers" }, { name: "about", label: "What it is about", type: "textarea", rows: 3 }, { name: "visibility", label: "Who can see it", type: "select", options: [["public", "Public: anyone can find and join"], ["private", "Private: people ask to join"]] }];
  const PAGE_FIELDS = [{ name: "name", label: "Page name", required: true, placeholder: "Asha's Bakery" }, { name: "category", label: "Category", placeholder: "Bakery" }, { name: "about", label: "About", type: "textarea", rows: 3 }, { name: "link", label: "Your website or Aira public page (https://…)", type: "url" }];
  const startGroup = () => S.edit({ title: "Start a group", fields: GROUP_FIELDS, values: { visibility: "public" }, submitLabel: "Start the group", onSave: async (values) => { const created = await api("/groups", { method: "POST", body: values }); go({ tab: "groups", group: created.group.id }); } });
  const createPage = () => S.edit({ title: "Create a page", fields: PAGE_FIELDS, submitLabel: "Create the page", onSave: async (values) => { const created = await api("/pages", { method: "POST", body: values }); go({ tab: "pages", page: created.page.id }); } });
  async function groupsTab(body, me, route) {
    if (route.query.group) {
      const draw = async () => {
        const group = (await api(`/groups/${route.query.group}`)).group;
        const editGroup = () => S.edit({ title: "Change the group", fields: GROUP_FIELDS, values: group, onSave: async (values) => { await api(`/groups/${group.id}`, { method: "PATCH", body: values }); await draw(); } });
        const deleteGroup = async () => { if (await Aira.confirm({ title: `Delete ${group.name}?`, message: "The group and its posts disappear for everyone. This cannot be undone.", confirmLabel: "Delete group", danger: true })) { await api(`/groups/${group.id}`, { method: "DELETE" }); Aira.toast("The group was deleted.", "success"); go({ tab: "groups" }); } };
        body.replaceChildren(...kids(
          h("div", { class: "btn-row" }, S.button("All groups", () => go({ tab: "groups" }), { kind: "ghost" })),
          S.section(group.name, h("p", { class: "muted", text: `${group.visibility === "public" ? "Public group" : "Private group"} · ${count(group.members, "member", "members")}` }), group.about ? h("p", { style: { whiteSpace: "pre-wrap" }, text: group.about }) : null,
            h("div", { class: "btn-row" },
              group.status === "none" ? S.button(group.visibility === "public" ? "Join group" : "Ask to join", async () => { await api(`/groups/${group.id}/join`, { method: "POST", body: {} }); await draw(); }) : null,
              group.status === "pending" ? h("span", { class: "muted", text: "Your request is waiting for an admin." }) : null,
              group.status !== "none" ? S.button(group.status === "pending" ? "Cancel request" : "Leave group", async () => { await api(`/groups/${group.id}/leave`, { method: "POST", body: {} }); await draw(); }, { kind: "ghost" }) : null,
              group.admin ? S.button("Change group", editGroup, { kind: "ghost" }) : null,
              group.admin ? S.button("Delete group", deleteGroup, { kind: "ghost" }) : null)),
          group.admin && group.requests && group.requests.length ? S.section("Asking to join", h("ul", { class: "life-people" }, ...group.requests.map((person) => personRow(person, S.button("Let in", async () => { await api(`/groups/${group.id}/requests/${person.id}`, { method: "POST", body: { approve: true } }); await draw(); }, { kind: "sm" }), S.button("Decline", async () => { await api(`/groups/${group.id}/requests/${person.id}`, { method: "POST", body: { approve: false } }); await draw(); }, { kind: "ghost sm" }))))) : null,
          group.status === "member" ? composer(me, { groupId: group.id, placeholder: "Write something to the group", onPosted: draw }) : null));
        if (group.posts) { const { state, ctx } = postList(body, me, async () => group.posts, { empty: Aira.ui.empty("No posts yet", "Members' posts appear here.") }); state.posts = group.posts; ctx.reload = draw; ctx.redraw(); }
        else body.append(Aira.ui.empty("This group is private", "Ask to join to see its posts."));
      };
      await draw();
      return;
    }
    const q = h("input", { type: "search", placeholder: "Find a group", "aria-label": "Find a group" });
    const panel = h("div", { class: "suite-body" });
    const draw = async () => {
      const data = await api(`/groups?q=${encodeURIComponent(q.value.trim())}`);
      const row = (group) => h("li", { class: "life-line" }, h("span", { "aria-hidden": "true", style: { fontSize: "26px" }, text: "👥" }), h("div", { style: { flex: "1", minWidth: "0" } }, h("a", { href: `#/social?tab=groups&group=${group.id}`, class: "life-line-text", text: group.name }), h("span", { class: "muted life-line-sub", text: `${group.visibility === "public" ? "Public" : "Private"} · ${count(group.members, "member", "members")}${group.status === "pending" ? " · request sent" : ""}` })), S.button("Open", () => go({ tab: "groups", group: group.id }), { kind: "secondary sm" }));
      panel.replaceChildren(
        S.section("Your groups", data.mine.length ? h("ul", { class: "life-lines" }, ...data.mine.map(row)) : Aira.ui.empty("No groups yet", "Join a group below or start your own.")),
        S.section("Discover groups", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", draw, { kind: "secondary" })), data.discover.length ? h("ul", { class: "life-lines" }, ...data.discover.map(row)) : h("p", { class: "muted", text: "No public groups found." })));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); draw(); } });
    body.append(h("div", { class: "btn-row" }, me.age_group === "child" ? null : S.button("Start a group", startGroup, { icon: "plus" })), panel);
    await draw();
  }

  async function pagesTab(body, me, route) {
    if (route.query.page) {
      const draw = async () => {
        const page = (await api(`/pages/${route.query.page}`)).page;
        const editPage = () => S.edit({ title: "Change the page", fields: PAGE_FIELDS, values: page, onSave: async (values) => { await api(`/pages/${page.id}`, { method: "PATCH", body: values }); await draw(); } });
        const deletePage = async () => { if (await Aira.confirm({ title: `Delete ${page.name}?`, message: "The page and its posts disappear for everyone, and its followers stop following it.", confirmLabel: "Delete page", danger: true })) { await api(`/pages/${page.id}`, { method: "DELETE" }); Aira.toast("The page was deleted.", "success"); go({ tab: "pages" }); } };
        body.replaceChildren(...kids(
          h("div", { class: "btn-row" }, S.button("All pages", () => go({ tab: "pages" }), { kind: "ghost" })),
          S.section(page.name, h("p", { class: "muted", text: `${page.category ? `${page.category} · ` : ""}${count(page.followers, "follower", "followers")}` }), page.about ? h("p", { style: { whiteSpace: "pre-wrap" }, text: page.about }) : null,
            h("div", { class: "btn-row" },
              S.button(page.following ? "Following" : "Follow", async () => { await api(`/pages/${page.id}/follow`, { method: page.following ? "DELETE" : "POST", body: {} }); await draw(); }, { kind: page.following ? "secondary" : "" }),
              /^https:\/\//i.test(page.link || "") ? h("a", { class: "btn ghost", href: page.link, target: "_blank", rel: "noopener noreferrer", text: "Visit their page" }) : null,
              page.admin ? S.button("Change page", editPage, { kind: "ghost" }) : null,
              page.admin ? S.button("Delete page", deletePage, { kind: "ghost" }) : null)),
          page.admin ? composer(me, { pageId: page.id, placeholder: `Write as ${page.name}`, onPosted: draw }) : null));
        const { state, ctx } = postList(body, me, async () => page.posts, { empty: Aira.ui.empty("No posts yet", "The page's news appears here.") });
        state.posts = page.posts; ctx.reload = draw; ctx.redraw();
      };
      await draw();
      return;
    }
    const q = h("input", { type: "search", placeholder: "Find a page", "aria-label": "Find a page" });
    const panel = h("div", { class: "suite-body" });
    const draw = async () => {
      const data = await api(`/pages?q=${encodeURIComponent(q.value.trim())}`);
      const row = (page) => h("li", { class: "life-line" }, h("span", { "aria-hidden": "true", style: { fontSize: "26px" }, text: "🏷️" }), h("div", { style: { flex: "1", minWidth: "0" } }, h("a", { href: `#/social?tab=pages&page=${page.id}`, class: "life-line-text", text: page.name }), h("span", { class: "muted life-line-sub", text: `${page.category ? `${page.category} · ` : ""}${count(page.followers, "follower", "followers")}${page.admin ? " · you manage it" : ""}` })), S.button("Open", () => go({ tab: "pages", page: page.id }), { kind: "secondary sm" }));
      panel.replaceChildren(
        S.section("Pages you follow", data.mine.length ? h("ul", { class: "life-lines" }, ...data.mine.map(row)) : Aira.ui.empty("No pages yet", "Follow shops, clubs and services to see their news in your feed.")),
        S.section("Discover pages", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", draw, { kind: "secondary" })), data.discover.length ? h("ul", { class: "life-lines" }, ...data.discover.map(row)) : h("p", { class: "muted", text: "No pages found." })));
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); draw(); } });
    const adult = !(me.age_group === "child" || me.age_group === "teen");
    body.append(h("div", { class: "btn-row" }, adult ? S.button("Create a page", createPage, { icon: "plus" }) : null), panel);
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Marketplace: buy and sell with friends and neighbours (aira/suite/social/market.py)
  // ---------------------------------------------------------------------------
  // setup (GET /market/setup): the currencies, and the business only for someone who runs it. A listing is the
  // seller's own unless they tick "Sell it for <business>". The price is sent as typed: "ten" is an error.
  const listingFields = (setup, creating) => kids(
    { name: "title", label: "What are you selling?", required: true, placeholder: "Child's bike, 16 inch" },
    { name: "price", label: "Price (leave empty or 0 to give it away)", placeholder: "12.50" },
    creating && setup.currencies.length > 1 ? { name: "currency", label: "Currency", type: "select", options: setup.currencies.map((code) => [code, code]) } : null,
    { name: "category", label: "Category", type: "select", options: Object.entries(CATEGORY_LABELS) },
    { name: "condition", label: "Condition", type: "select", options: Object.entries(CONDITION_LABELS) },
    { name: "about", label: "Description", type: "textarea", rows: 4, placeholder: "Size, colour, what is included, why you are selling" },
    { name: "place", label: "Where to pick it up (town or area)", placeholder: "Riverside" },
    { name: "photos", label: "Photo links (https://…), one per line, up to 6", type: "textarea", rows: 3 },
    { name: "audience", label: "Who can see it", type: "select", options: [["public", "Everyone on Social"], ["friends", "Only my friends"]] },
    { name: "payment_link", label: "Your payment link (optional, https://…)", type: "url", hint: "Buyers pay you there or in person. Aira never handles the money." },
    setup.business ? { name: "for_business", label: `Sell it for ${setup.business.name}`, type: "checkbox", hint: `The sale counts in the Business hub.${setup.business.has_payment_link ? " Buyers see the business's payment link if you leave yours empty." : ""}` } : null,
  );
  const listingBody = (values) => ({ ...values, photos: photoList(values.photos) });
  const newListing = async () => {
    const setup = await api("/market/setup");
    return S.edit({
      title: "Sell something", fields: listingFields(setup, true), values: { category: "other", condition: "good", audience: "public", currency: setup.currency, for_business: false }, submitLabel: "Put it up for sale",
      onSave: async (values) => { const created = await api("/market", { method: "POST", body: listingBody(values) }); Aira.toast("It is on the Marketplace.", "success"); go({ tab: "market", listing: created.listing.id }); },
    });
  };
  const listingCard = (listing) => h("a", { class: `so-listing ${listing.status === "sold" ? "so-sold" : ""}`, href: `#/social?tab=market&listing=${listing.id}`, "aria-label": `${listing.title}, ${listing.price}${listing.status !== "available" ? `, ${STATUS_LABELS[listing.status]}` : ""}` },
    listing.photos.length && /^https:\/\//i.test(listing.photos[0])
      ? h("img", { class: "so-listing-photo", src: listing.photos[0], alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
      : h("span", { class: "so-listing-photo", "aria-hidden": "true", text: "🏷️" }),
    h("span", { class: "so-listing-price", text: listing.price }),
    h("span", { class: "so-listing-title", text: listing.title }),
    h("span", { class: "muted small", text: [listing.place, CONDITION_LABELS[listing.condition]].filter(Boolean).join(" · ") }),
    listing.status !== "available" ? h("span", {}, S.pill(STATUS_LABELS[listing.status], { Reserved: "warn", Sold: "info" })) : null);
  const listingGrid = (listings, empty) => (listings.length ? h("div", { class: "so-market-grid" }, ...listings.map(listingCard)) : empty);

  async function listingDetail(body, me, id) {
    const draw = async () => {
      const listing = (await api(`/market/${id}`)).listing;
      const setStatus = async (status, extra = {}) => { await api(`/market/${listing.id}/status`, { method: "POST", body: { status, ...extra } }); await draw(); };
      const markSold = () => S.edit({
        title: "Mark as sold", submitLabel: "Mark as sold",
        fields: [{ name: "sold_price", label: "How much you got (0 if you gave it away)", required: true, placeholder: "12.50" }, { name: "buyer", label: "Who bought it (optional)", placeholder: "Ana" }],
        values: { sold_price: S.fromCents(listing.price_cents) },
        onSave: async (values) => { await setStatus("sold", { sold_price: values.sold_price, buyer: values.buyer }); Aira.toast(listing.for_business ? "Sold! It is added to the Business hub." : "Sold! It counts in your earnings from sales.", "success"); },
      });
      const ask = () => S.edit({
        title: `Message ${listing.seller.name}`, submitLabel: "Send",
        fields: [{ name: "text", label: "Your message", type: "textarea", rows: 3, required: true }],
        values: { text: `Hi! Is “${listing.title}” still available?` },
        onSave: async (values) => { const result = await api(`/market/${listing.id}/ask`, { method: "POST", body: { text: values.text } }); go({ tab: "chats", chat: result.chat.id }); },
      });
      const edit = async () => {
        const setup = await api("/market/setup");
        return S.edit({ title: "Change your listing", fields: listingFields(setup, false), values: { ...listing, price: listing.price_cents ? S.fromCents(listing.price_cents) : "", payment_link: listing.own_payment_link || "", photos: (listing.photos || []).join("\n") }, onSave: async (values) => { await api(`/market/${listing.id}`, { method: "PATCH", body: listingBody(values) }); await draw(); } });
      };
      const remove = async () => { if (await S.confirmDelete(`“${listing.title}”`)) { await api(`/market/${listing.id}`, { method: "DELETE" }); Aira.toast("Your listing was removed.", "success"); go({ tab: "market", view: "selling" }); } };
      const link = listing.mine && listing.audience === "public" ? (await api(`/market/${listing.id}/share`)).link : null;
      body.replaceChildren(...kids(
        h("div", { class: "btn-row" }, S.button(listing.mine ? "Your listings" : "Back to the Marketplace", () => go(listing.mine ? { tab: "market", view: "selling" } : { tab: "market" }), { kind: "ghost" })),
        S.section("", ...kids(
          photoGrid(listing.photos, listing.seller.name),
          h("div", { style: { display: "flex", gap: "10px", alignItems: "baseline", flexWrap: "wrap" } }, h("h2", { class: "suite-h2", style: { margin: 0, overflowWrap: "anywhere" }, text: listing.title }), S.pill(STATUS_LABELS[listing.status], { "For sale": "good", Reserved: "warn", Sold: "info" })),
          h("p", { class: "so-listing-price", style: { margin: 0 }, text: listing.price }),
          h("p", { class: "muted", style: { margin: 0 }, text: [CATEGORY_LABELS[listing.category], CONDITION_LABELS[listing.condition], listing.place ? `Pick up in ${listing.place}` : "", `Listed ${when(listing.created_at).replace(/^Just now$/, "just now")}`, listing.audience === "friends" ? "Friends only" : ""].filter(Boolean).join(" · ") }),
          listing.about ? richText(listing.about) : null,
          h("div", { style: { display: "flex", gap: "10px", alignItems: "center" } }, face(listing.seller, 36), h("span", {}, h("span", { class: "muted", text: "Sold by " }), personLink(listing.seller))),
          listing.mine
            ? h("div", { class: "btn-row" },
              listing.status !== "sold" ? S.button("Mark as sold", markSold) : null,
              listing.status === "available" ? S.button("Mark as reserved", () => setStatus("reserved"), { kind: "secondary" }) : null,
              listing.status !== "available" ? S.button("Available again", () => setStatus("available"), { kind: "secondary" }) : null,
              S.button("Change", edit, { kind: "ghost" }),
              S.button("Remove", remove, { kind: "ghost" }))
            : h("div", { class: "btn-row" },
              listing.can_message && listing.status !== "sold" ? S.button("Message the seller", ask) : null,
              /^https:\/\//i.test(listing.payment_link || "") && listing.status !== "sold" ? h("a", { class: "btn secondary", href: listing.payment_link, target: "_blank", rel: "noopener noreferrer", text: "Pay the seller" }) : null,
              S.button(listing.saved ? "Saved" : "Save", async () => { await api(`/market/${listing.id}/save`, { method: listing.saved ? "DELETE" : "POST", body: {} }); await draw(); }, { kind: listing.saved ? "secondary" : "ghost" }),
              S.button("Report", () => report("listing", listing.id), { kind: "ghost" })),
          !listing.mine && !listing.can_message && listing.status !== "sold" ? h("p", { class: "muted small", text: "You can message sellers who are your friends." }) : null)),
        listing.mine ? S.section("How it is going", h("p", { style: { margin: 0 }, text: `${count(listing.views || 0, "view", "views")} · saved by ${count(listing.saves || 0, "person", "people")}` }),
          listing.status === "sold" ? h("p", { style: { margin: 0 }, text: `${listing.sold_cents ? `Sold for ${listing.sold}` : "Given away"}${listing.buyer ? ` to ${listing.buyer}` : ""}.${listing.sold_cents ? (listing.for_business ? " It counts in the Business hub." : " It counts in your earnings from sales.") : ""}` }) : null,
          listing.for_business ? h("p", { class: "muted small", style: { margin: 0 }, text: "You are selling this for the business." }) : null) : null,
        listing.mine && listing.audience === "public" && listing.status !== "sold" ? S.section("Share it outside Aira", S.share({ title: "Listing link", text: "A page anyone can open, even without Aira: the photos, the price and a button to pay you. They can tell you they are interested.", link, onCreate: async () => (await api(`/market/${listing.id}/share`, { method: "POST", body: {} })).link, onRevoke: async () => { await api(`/market/${listing.id}/share`, { method: "DELETE" }); } })) : null));
    };
    await draw();
  }

  async function marketTab(body, me, route) {
    if (me.age_group === "child") { body.append(Aira.ui.empty("Ask a grown-up", "The Marketplace is for older people.")); return; }
    if (route.query.listing) { await listingDetail(body, me, route.query.listing); return; }
    const adult = !young(me);
    const views = [["browse", "Browse"], ["saved", "Saved"], adult ? ["selling", "Your listings"] : null].filter(Boolean);
    const view = views.some(([id]) => id === route.query.view) ? route.query.view : "browse";
    body.append(h("div", { class: "btn-row", role: "group", "aria-label": "Marketplace" },
      ...views.map(([id, label]) => h("button", { type: "button", class: `btn sm ${id === view ? "secondary" : "ghost"}`, "aria-pressed": String(id === view), style: id === view ? { textDecoration: "underline", textUnderlineOffset: "4px" } : null, text: label, onclick: () => go(id === "browse" ? { tab: "market" } : { tab: "market", view: id }) })),
      adult ? h("span", { style: { flex: "1" } }) : null,
      adult ? S.button("Sell something", newListing, { kind: "sm", icon: "plus" }) : null));
    if (view === "saved") {
      const data = await api("/market/saved");
      body.append(S.section("Saved", listingGrid(data.listings, Aira.ui.empty("Nothing saved yet", "Tap Save on a listing to keep it here."))));
      return;
    }
    if (view === "selling") {
      const data = await api("/market/mine");
      const summary = data.summary;
      body.append(
        S.kpis([
          { label: "For sale", value: summary.for_sale },
          { label: "Reserved", value: summary.reserved },
          { label: "Sold", value: summary.sold, tone: summary.sold ? "good" : "" },
          { label: "Earned from sales", value: summary.earned.length ? summary.earned.map((item) => item.text).join(" + ") : "Nothing yet", tone: summary.earned.length ? "good" : "" },
        ]),
        h("p", { class: "muted", text: "Earned from sales adds up what you marked as sold, also listings you removed afterwards. Things you sell for a business are added to the Business hub too. Buyers pay you through your payment link or in person." }),
        S.section("Your listings", listingGrid(data.listings, Aira.ui.empty("You are not selling anything yet", "Things you no longer need can earn you money: tap Sell something."))));
      return;
    }
    const q = h("input", { type: "search", placeholder: "Search: bike, sofa, piano lessons", "aria-label": "Search the Marketplace", value: route.query.q || "" });
    const category = Aira.ui.select([["", "All categories"], ...Object.entries(CATEGORY_LABELS)], route.query.category || "", { "aria-label": "Category" });
    const maxPrice = h("input", { type: "text", inputmode: "decimal", placeholder: "Highest price", "aria-label": "Highest price", value: route.query.max_price || "" });
    const sort = Aira.ui.select([["newest", "Newest first"], ["cheapest", "Cheapest first"], ["dearest", "Most expensive first"]], route.query.sort || "newest", { "aria-label": "Order" });
    const results = h("div", { class: "suite-body" });
    const search = async () => {
      const params = new URLSearchParams({ q: q.value.trim(), category: category.value, max_price: maxPrice.value.trim(), sort: sort.value });
      let data;
      try { data = await api(`/market?${params}`); } catch (error) {
        results.replaceChildren(h("p", { class: "form-error", role: "alert", text: error.status === 400 ? error.message : `The Marketplace could not be loaded. ${error.message}` }));
        if (error.status === 400 && maxPrice.value.trim()) maxPrice.focus();
        return;
      }
      const filtered = q.value.trim() || category.value || maxPrice.value.trim();
      results.replaceChildren(listingGrid(data.listings, filtered ? Aira.ui.empty("Nothing matches", "Try other words, another category or a higher price.") : Aira.ui.empty("Nothing for sale yet", adult ? "Be the first: tap Sell something." : "Things your friends and neighbours sell appear here.")));
    };
    for (const control of [q, maxPrice]) control.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); search(); } });
    for (const control of [category, sort]) control.addEventListener("change", search);
    body.append(S.section("For sale near you", h("div", { class: "so-filters" }, q, category, maxPrice, sort, S.button("Search", search, { kind: "secondary" })), results));
    await search();
  }

  // ---------------------------------------------------------------------------
  // Profile
  // ---------------------------------------------------------------------------
  const experienceText = (items) => (items || []).map((item) => [item.title, item.company, item.start, item.end].join(" | ")).join("\n");
  const parseExperience = (text) => String(text || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => { const [title, company = "", start = "", end = ""] = line.split("|").map((part) => part.trim()); return { title, company, start, end }; });
  const professional = (person) => (person.headline || (person.skills && person.skills.length) || (person.experience && person.experience.length) ? S.section("Work",
    person.headline ? h("p", { style: { fontSize: "17px", margin: 0 }, text: person.headline }) : null,
    person.open_to_work ? h("p", {}, S.pill("Open to work", { "Open to work": "good" })) : null,
    person.experience && person.experience.length ? h("ul", { class: "life-lines" }, ...person.experience.map((item) => h("li", { class: "life-line" }, h("div", {}, h("span", { class: "life-line-text", text: `${item.title}${item.company ? ` at ${item.company}` : ""}` }), h("span", { class: "muted life-line-sub", text: [item.start, item.end || (item.start ? "now" : "")].filter(Boolean).join(" to ") }))))) : null,
    person.skills && person.skills.length ? h("p", {}, h("strong", { text: "Skills: " }), h("span", { text: person.skills.join(", ") })) : null) : null);

  async function profileTab(body, me, route) {
    if (route.query.person && route.query.person !== me.id) {
      const data = await api(`/people/${route.query.person}`);
      const person = data.person;
      const after = () => Aira.router.render();
      body.append(...kids(
        S.section("", h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, face(person, 84), h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: person.name }), h("p", { class: "muted", style: { margin: 0 }, text: `@${person.handle} · ${count(person.friends, "friend", "friends")} · ${count(person.followers, "follower", "followers")}` }))),
          person.bio ? h("p", { style: { whiteSpace: "pre-wrap" }, text: person.bio }) : null,
          person.relation !== "friends" && person.visibility === "private" ? h("p", { class: "muted", text: "This profile is private. Become friends to see their posts." }) : null,
          h("div", { class: "btn-row" }, ...relationButtons(person, person.relation, after),
            person.can_message && person.relation !== "friends" ? S.button("Message", () => message(person), { kind: "secondary sm" }) : null,
            person.visibility === "public" && person.relation !== "friends" ? S.button(person.following ? "Unfollow" : "Follow", async () => { await api(`/people/${person.id}/follow`, { method: person.following ? "DELETE" : "POST", body: {} }); after(); }, { kind: "ghost sm" }) : null,
            S.button("Block", async () => { if (await block(person)) go({ tab: "friends" }); }, { kind: "ghost sm" }),
            S.button("Report", () => report("account", person.id), { kind: "ghost sm" }))),
        professional(person)));
      const { state, ctx } = postList(body, me, async () => data.posts, { empty: Aira.ui.empty("No posts to show", "Posts you are allowed to see appear here.") });
      state.posts = data.posts; ctx.reload = after; ctx.redraw();
      return;
    }
    const young = me.age_group === "child" || me.age_group === "teen";
    const edit = () => S.edit({
      title: "Edit your profile",
      fields: [
        { name: "display_name", label: "Your name", required: true },
        { name: "handle", label: "Your @name", required: true, hint: "3 to 30 letters, numbers, dots or underscores." },
        { name: "avatar", label: "Picture: an emoji or an https:// photo link" },
        { name: "bio", label: "About you", type: "textarea", rows: 3 },
        young ? null : { name: "visibility", label: "Who can see your profile and public posts", type: "select", options: [["public", "Everyone"], ["private", "Only my friends"]] },
        young ? null : { name: "open_messages", label: "Let people who are not my friends message me", type: "checkbox" },
      ].filter(Boolean),
      values: me,
      onSave: async (values) => { await api("/me", { method: "PATCH", body: values }); Aira.router.render(); },
    });
    const editWork = () => S.edit({
      title: "Your work profile",
      fields: [
        { name: "headline", label: "Headline", placeholder: "Nurse at City Hospital" },
        { name: "open_to_work", label: "I am open to work", type: "checkbox" },
        { name: "skills", label: "Skills (separate with commas)", placeholder: "Patient care, Spanish" },
        { name: "experience", label: "Experience: one job per line as Title | Company | Start | End", type: "textarea", rows: 5, placeholder: "Nurse | City Hospital | 2019 | " },
      ],
      values: { ...me, skills: (me.skills || []).join(", "), experience: experienceText(me.experience) },
      onSave: async (values) => { await api("/me", { method: "PATCH", body: { headline: values.headline, open_to_work: !!values.open_to_work, skills: String(values.skills || "").split(","), experience: parseExperience(values.experience) } }); Aira.router.render(); },
    });
    const data = await api(`/people/${me.id}`);
    body.append(...kids(
      S.section("", h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, face(me, 84), h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: me.display_name }), h("p", { class: "muted", style: { margin: 0 }, text: `@${me.handle} · ${me.visibility === "public" ? "Public profile" : "Private profile"} · ${count(data.person.friends, "friend", "friends")}` }))),
        me.bio ? h("p", { style: { whiteSpace: "pre-wrap" }, text: me.bio }) : h("p", { class: "muted", text: "Add a few words about yourself." }),
        young ? h("p", { class: "muted", text: "Your profile is private: only your friends see it, and only friends can message you." }) : null,
        h("div", { class: "btn-row" }, S.button("Edit profile", edit), S.button(young ? "Work and school" : "Work profile", editWork, { kind: "secondary" }),
          S.button("Download my data", () => Aira.download("/api/social/me/export", `social-${me.handle}.json`), { kind: "ghost" }))),
      professional(me)));
    if (!young && me.visibility === "public") {
      const link = (await api("/me/share")).link;
      body.append(S.section("Share your profile", S.share({ title: "Public profile link", text: "A page anyone can open, even without Aira: your name, about you, your work and your public posts.", link, onCreate: async () => (await api("/me/share", { method: "POST", body: {} })).link, onRevoke: async () => { await api("/me/share", { method: "DELETE" }); } })));
    }
    body.append(h("h2", { class: "suite-h2", text: "Your posts" }));
    const { state, ctx } = postList(body, me, async () => (await api(`/people/${me.id}`)).posts, { empty: Aira.ui.empty("You have not posted yet", "Share something from the Feed.") });
    state.posts = data.posts; ctx.redraw();
  }

  // ---------------------------------------------------------------------------
  // Notifications and moderation
  // ---------------------------------------------------------------------------
  const notificationLink = (item) => {
    if (item.ref_type === "post") return `#/social?tab=feed&post=${item.ref_id}`;
    if (item.ref_type === "chat") return `#/social?tab=chats&chat=${item.ref_id}`;
    if (item.ref_type === "group") return `#/social?tab=groups&group=${item.ref_id}`;
    if (item.ref_type === "listing") return `#/social?tab=market&listing=${item.ref_id}`;
    if (item.kind === "friend_request" || item.ref_type === "guardian") return "#/social?tab=friends";
    return item.actor ? `#/social?tab=profile&person=${item.actor.id}` : "#/social";
  };
  async function notificationsTab(body) {
    const data = await api("/notifications");
    body.append(S.section(data.notifications.length ? "Recent" : "", data.notifications.length ? h("ul", { class: "life-lines" }, ...data.notifications.map((item) => h("li", { class: "life-line", style: { fontWeight: item.read ? "400" : "600" } },
      item.actor ? face(item.actor, 36) : h("span", { "aria-hidden": "true", text: "🔔" }),
      h("div", { style: { flex: "1", minWidth: "0" } }, h("a", { href: notificationLink(item), class: "life-line-text", text: item.text }), h("span", { class: "muted life-line-sub", text: `${when(item.created_at)}${item.read ? "" : " · new"}` }))))) : Aira.ui.empty("No notifications", "When someone comments, reacts, mentions you or sends a friend request, it shows here.", S.button("Find friends", () => go({ tab: "friends" }), { kind: "secondary sm" }))));
    if (data.unread) api("/notifications/read", { method: "POST", body: {}, silent: true }).catch(() => {});
  }

  async function moderationTab(body) {
    const draw = async () => {
      const data = await api("/moderation");
      const words = h("textarea", { rows: "3", "aria-label": "Blocked words" });
      words.value = data.words.join(", ");
      body.replaceChildren(
        S.section(`Reports to review (${data.reports.length})`, data.reports.length ? h("ul", { class: "life-lines" }, ...data.reports.map((item) => h("li", { class: "life-line", style: { flexWrap: "wrap" } },
          h("div", { style: { flex: "1 1 260px", minWidth: "0" } },
            h("span", { class: "life-line-text", text: `A ${REPORT_LABELS[item.target_type]}${item.author ? ` by ${item.author.name} (@${item.author.handle})` : ""}` }),
            item.preview ? h("span", { class: "life-line-sub", style: { whiteSpace: "pre-wrap" }, text: `“${item.preview}”` }) : null,
            h("span", { class: "muted life-line-sub", text: `Reported by ${item.reporter ? item.reporter.name : "someone"} ${when(item.created_at)}: ${item.reason}` })),
          item.target_type === "account" ? null : S.button("Hide it", async () => { await api(`/moderation/${item.id}`, { method: "POST", body: { action: "hide" } }); await draw(); }, { kind: "sm" }),
          item.can_suspend ? S.button("Suspend the person", async () => { if (await Aira.confirm({ title: "Suspend this person?", message: "Their posts, comments and stories disappear and they cannot post or message.", confirmLabel: "Suspend", danger: true })) { await api(`/moderation/${item.id}`, { method: "POST", body: { action: "suspend" } }); await draw(); } }, { kind: "ghost sm" }) : null,
          S.button("Nothing wrong", async () => { await api(`/moderation/${item.id}`, { method: "POST", body: { action: "dismiss" } }); await draw(); }, { kind: "ghost sm" })))) : Aira.ui.empty("Nothing to review", "Reports from your household, or about your household's people, appear here.")),
        S.section("Blocked words", h("p", { class: "muted", text: "These words are hidden with stars in posts, comments and messages. Separate them with commas." }), words,
          h("div", { class: "btn-row" }, S.button("Save words", async () => { await api("/moderation/words", { method: "PUT", body: { words: words.value.split(",").map((w) => w.trim()).filter(Boolean) } }); Aira.toast("Saved.", "success"); }))));
    };
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Search across the top: people, chats, groups, pages, listings and the family
  // ---------------------------------------------------------------------------
  // Lists are small: the chats and the family are fetched whole and filtered here, kept a few seconds so typing
  // does not ask again; people, groups, pages and listings come from the server's own searches.
  const remembered = new Map();
  const cached = async (key, ms, load) => {
    const hit = remembered.get(key);
    if (hit && Date.now() - hit.at < ms) return hit.value;
    const value = await load();
    remembered.set(key, { at: Date.now(), value });
    return value;
  };
  const norm = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  const NOTHING = { people: [], chats: [], groups: [], pages: [], listings: [], family: [], events: [], familyMe: "" };
  async function find(query) {
    const q = String(query || "").trim();
    const words = norm(q.replace(/^@/, ""));
    if (words.length < 2) return NOTHING;
    const quiet = (promise, fallback) => promise.catch(() => fallback);
    const [found, chats, market, family] = await Promise.all([
      quiet(cached(`search:${q}`, 5000, () => api(`/search?q=${encodeURIComponent(q)}`, { silent: true })), { people: [], groups: [], pages: [] }),
      quiet(cached("chats", 8000, () => api("/chats", { silent: true })), { chats: [] }),
      ageGroup() === "child" ? { listings: [] } : quiet(cached(`market:${q}`, 5000, () => api(`/market?q=${encodeURIComponent(q)}`, { silent: true })), { listings: [] }),
      familyOpen() ? quiet(cached("family", 30000, async () => {
        const [overview, events] = await Promise.all([S.api("family", "/overview", { silent: true }), S.api("family", "/events", { silent: true })]);
        return { me: overview.me, people: overview.people, events: events.events };
      }), null) : null,
    ]);
    return {
      people: found.people || [],
      groups: found.groups || [],
      pages: found.pages || [],
      chats: (chats.chats || []).filter((chat) => norm(chat.title).includes(words) || (chat.last && !chat.last.deleted && norm(chat.last.text).includes(words))),
      listings: market.listings || [],
      family: family ? family.people.filter((person) => person.id !== family.me.id && norm(person.name).includes(words)) : [],
      events: family ? family.events.filter((event) => norm(`${event.title} ${event.place || ""}`).includes(words)) : [],
      familyMe: family ? family.me.id : "",
    };
  }
  const RELATION_TEXT = { friends: "Friend", request_received: "Wants to be friends", request_sent: "Request sent", awaiting_parent: "Waiting for a grown-up" };
  const eventDay = (event) => new Date(event.all_day ? `${String(event.starts_at).slice(0, 10)}T00:00:00` : event.starts_at).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  const familyChat = async (person) => {
    const created = await S.api("family", "/rooms/direct", { method: "POST", body: { profile_id: person.id } });
    Aira.router.go(["family"], { tab: "chat", room: created.room.id });
  };
  // One row per record for the list under the search box, each opening its record.
  const HITS = {
    people: (person) => ({ label: person.name, sub: [`@${person.handle}`, RELATION_TEXT[person.relation] || "Person"].join(" · "), icon: "soPerson", href: `#/social?tab=profile&person=${person.id}` }),
    chats: (chat) => ({ label: chat.title, sub: `Chat${chat.last && !chat.last.deleted ? ` · ${chat.last.mine ? "You: " : ""}${chat.last.text}` : ""}`, icon: "social", href: `#/social?tab=chats&chat=${chat.id}` }),
    groups: (group) => ({ label: group.name, sub: `Group · ${group.visibility === "public" ? "Public" : "Private"}`, icon: "soGroup", href: `#/social?tab=groups&group=${group.id}` }),
    pages: (page) => ({ label: page.name, sub: `Page${page.category ? ` · ${page.category}` : ""}`, icon: "soFlag", href: `#/social?tab=pages&page=${page.id}` }),
    listings: (listing) => ({ label: listing.title, sub: ["Marketplace", listing.price, listing.place].filter(Boolean).join(" · "), icon: "soTag", href: `#/social?tab=market&listing=${listing.id}` }),
    family: (person) => ({ label: person.name, sub: "Family · send a message", icon: "family", run: () => familyChat(person) }),
    events: (event) => ({ label: event.title, sub: `Family calendar · ${eventDay(event)}`, icon: "interviews", href: `#/family?tab=calendar&event=${event.id}` }),
  };
  const LANES = Object.keys(HITS);

  // Enter in the search box: everything that matches, by kind, each opening its record.
  async function searchTab(body, me, route) {
    const q = String(route.query.q || "").trim();
    if (q.length < 2) { body.append(Aira.ui.empty("Type what you are looking for", "In the search box at the top: a name, an @name, a group, a page or something for sale.")); return; }
    const found = await find(q);
    const sections = [];
    const add = (title, items, node) => { if (items.length) sections.push(S.section(`${title} (${items.length})`, node)); };
    const rerun = () => Aira.router.render();
    add("People", found.people, h("ul", { class: "life-people" }, ...found.people.map((person) => personRow(person, relationButtons(person, person.relation, rerun)))));
    const line = (hit, extra) => h("li", { class: "life-line" }, h("span", { class: "so-hit-icon", "aria-hidden": "true" }, Aira.icon(hit.icon)),
      h("div", { style: { flex: "1", minWidth: "0" } }, hit.href ? h("a", { href: hit.href, class: "life-line-text", text: hit.label }) : h("button", { type: "button", class: "so-linkish life-line-text", text: hit.label, onclick: hit.run }), h("span", { class: "muted life-line-sub", text: hit.sub })), extra || null);
    add("Chats", found.chats, h("ul", { class: "life-lines" }, ...found.chats.map((chat) => line(HITS.chats(chat)))));
    add("Groups", found.groups, h("ul", { class: "life-lines" }, ...found.groups.map((group) => line(HITS.groups(group)))));
    add("Pages", found.pages, h("ul", { class: "life-lines" }, ...found.pages.map((page) => line(HITS.pages(page)))));
    add("Marketplace", found.listings, listingGrid(found.listings, null));
    add("Family", found.family, h("ul", { class: "life-lines" }, ...found.family.map((person) => line(HITS.family(person)))));
    add("Family calendar", found.events, h("ul", { class: "life-lines" }, ...found.events.map((event) => line(HITS.events(event)))));
    if (!sections.length) {
      body.append(S.section("", Aira.ui.empty(`Nothing in Social matches “${q}”`, "Try part of a name, or ask them for their @name.",
        h("div", { class: "btn-row", style: { justifyContent: "center" } }, S.button("Find people", () => go({ tab: "friends", q }), { kind: "secondary sm" }), ageGroup() === "child" ? null : S.button("Search the Marketplace", () => go({ tab: "market", q }), { kind: "secondary sm" })))));
      return;
    }
    body.append(...sections);
  }

  // ---------------------------------------------------------------------------
  // First visit: choose a Social name
  // ---------------------------------------------------------------------------
  function setup(main, info) {
    const young = info.age_group === "child" || info.age_group === "teen";
    const body = S.page(main, { title: "Social", subtitle: "Chat with friends and family, share photos and news." });
    const form = S.form([
      { name: "display_name", label: "Your name", required: true },
      { name: "handle", label: "Your @name (people find you by it)", required: true, hint: "3 to 30 letters, numbers, dots or underscores." },
      { name: "avatar", label: "Picture: an emoji or an https:// photo link" },
      { name: "bio", label: "About you (optional)", type: "textarea", rows: 2 },
      young ? null : { name: "visibility", label: "Who can see your profile", type: "select", options: [["public", "Everyone"], ["private", "Only my friends"]] },
    ].filter(Boolean), { display_name: info.name, handle: info.suggested_handle, avatar: info.avatar, visibility: "public" });
    body.append(S.section("Join Social", h("p", { class: "muted", text: young ? "Your profile stays private: only friends see it and only friends can message you." : "You can change all of this later." }), form.node,
      h("div", { class: "btn-row" }, S.button("Join Social", async () => {
        try { await api("/me", { method: "POST", body: form.read() }); Aira.router.render(); } catch (error) { form.setError(error.message); }
      }))));
  }

  // "+ New" asked for something: open the same form as the button on its tab, if this person may make it.
  function fulfil(ask, me, main) {
    const opened = (() => {
      if (ask === "post") {
        const box = main.querySelector("[data-so-composer]");
        if (box) { box.scrollIntoView({ block: "center" }); box.focus(); }
        return null;
      }
      if (ask === "story") { const strip = main.querySelector(".so-stories"); return addStory(strip && strip.redraw ? strip.redraw : async () => {}); }
      if (ask === "chat") return newChat();
      if (ask === "group" && me.age_group !== "child") return startGroup();
      if (ask === "page" && !young(me)) return createPage();
      if (ask === "listing" && !young(me)) return newListing();
      return null;
    })();
    if (opened && opened.catch) opened.catch((error) => Aira.toast(error.message, "error"));
  }
  const NEW_TAB = { post: "feed", story: "feed", chat: "chats", group: "groups", page: "pages", listing: "market" };

  Aira.views.social = async (main, route) => {
    // "+ New" asks with ?new=<kind>: remember it, take it out of the address (Back or a refresh must not ask again)
    // and draw the page; the drawn page then opens the form.
    if (route.query.new) {
      pending = String(route.query.new);
      const rest = { ...route.query };
      delete rest.new;
      if (!rest.tab) rest.tab = NEW_TAB[pending] || "";
      Aira.router.go(["social"], rest, { replace: true });
      return;
    }
    let info;
    try { info = await api("/me"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    if (!info.account) { pending = ""; setup(main, info); return; }
    const me = info.account;
    known = me;
    const unread = info.badge || { messages: 0, notifications: 0 };
    const tabs = [
      { id: "feed" },
      { id: "chats", count: unread.messages },
      { id: "friends" },
      { id: "groups" },
      { id: "pages" },
      me.age_group === "child" ? null : { id: "market" },
      { id: "profile" },
      { id: "notifications", count: unread.notifications },
      info.moderator ? { id: "moderation" } : null,
    ].filter(Boolean).map((item) => ({ ...item, label: TAB_META[item.id].label, icon: TAB_META[item.id].icon }));
    const searching = route.query.tab === "search";
    const tabId = searching ? "search" : tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "feed";
    const query = String(route.query.q || "").trim();
    // The page is named after the open tab (the app's name is in the bar above), like a mail app's folder.
    const news = [unread.messages ? count(unread.messages, "new message", "new messages") : "", unread.notifications ? count(unread.notifications, "notification", "notifications") : ""].filter(Boolean);
    const title = searching ? (query ? `Results for “${query}”` : "Search") : TAB_META[tabId].label;
    const subtitle = searching ? "People, chats, groups, pages, things for sale and your family." : tabId === "feed" && news.length ? news.join(" · ") : TAB_META[tabId].about;
    const body = S.page(main, { title, subtitle });
    body.classList.add("so-page");
    document.title = `${title} · Social · Aira`;
    // The tab bar (on a phone) and the menu's folders (shell.js draws them from the same items) carry the unread
    // counts in their labels.
    S.tabs(body, route, "social", tabs.map((item) => ({ id: item.id, icon: item.icon, label: item.count ? `${item.label} (${item.count})` : item.label })), searching ? "" : tabId);
    const panel = h("div", { class: `suite-body ${["feed", "friends", "groups", "pages", "profile", "notifications", "search"].includes(tabId) ? "so-column" : ""}`.trim() });
    body.append(panel);
    try {
      if (tabId === "chats") await chatsTab(panel, me, route);
      else if (tabId === "friends") await friendsTab(panel, me, route);
      else if (tabId === "groups") await groupsTab(panel, me, route);
      else if (tabId === "pages") await pagesTab(panel, me, route);
      else if (tabId === "market") await marketTab(panel, me, route);
      else if (tabId === "profile") await profileTab(panel, me, route);
      else if (tabId === "notifications") await notificationsTab(panel);
      else if (tabId === "moderation") await moderationTab(panel);
      else if (tabId === "search") await searchTab(panel, me, route);
      else await feedTab(panel, me, route);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
    const ask = pending;
    pending = "";
    if (ask && main.isConnected) fulfil(ask, me, main);
  };

  Aira.appNew = Aira.appNew || {};
  Aira.appSearch = Aira.appSearch || {};
  // "+ New": what people make most here, each opening the same form as the button on its tab. Children have no
  // Marketplace; children and teens do not sell.
  Aira.appNew.social = () => {
    const age = ageGroup();
    return [
      { label: "Post", icon: "soPen", href: "#/social?tab=feed&new=post" },
      { label: "Story", icon: "soStory", href: "#/social?tab=feed&new=story" },
      { label: "Chat", icon: "social", href: "#/social?tab=chats&new=chat" },
      age === "child" || age === "teen" ? null : { label: "Marketplace listing", icon: "soTag", href: "#/social?tab=market&new=listing" },
      familyOpen() ? { label: "Family event", icon: "interviews", href: "#/family?tab=calendar&new=event" } : null,
    ].filter(Boolean);
  };
  Aira.appSearch.social = {
    placeholder: "Search people, chats, groups and the Marketplace",
    // Up to six records, one of each kind in turn so one busy kind does not hide the others. A #tag lists its posts.
    async suggest(query) {
      const q = String(query || "").trim();
      if (/^#[\p{L}\p{N}_]{2,40}$/u.test(q)) return [{ label: `Posts tagged ${q}`, sub: "Feed", icon: "news", href: `#/social?tab=feed&tag=${encodeURIComponent(q.slice(1).toLowerCase())}` }];
      const found = await find(q);
      const out = [];
      for (let round = 0; out.length < 6 && LANES.some((lane) => found[lane].length > round); round += 1) {
        for (const lane of LANES) if (found[lane][round] && out.length < 6) out.push(HITS[lane](found[lane][round]));
      }
      return out;
    },
    run(query) {
      const q = String(query || "").trim();
      if (/^#[\p{L}\p{N}_]{2,40}$/u.test(q)) go({ tab: "feed", tag: q.slice(1).toLowerCase() });
      else go({ tab: "search", q });
    },
  };
})();

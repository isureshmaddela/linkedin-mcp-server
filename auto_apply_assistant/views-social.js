/* Aira — Social (#/social): Aira's own social network and messenger, safe for every age.
 *
 * Tabs (?tab=): feed (stories, posts, reactions, comments, shares; ?post=, ?tag=), chats (one-to-one and group chats,
 * two panes on a wide screen; ?chat=), friends (search, requests, suggestions, blocked people), groups (?group=),
 * pages (?page=), profile (yours, or ?person=), notifications and, for the household's owner, moderation.
 * Everything runs through /api/social/* (aira/suite/social/social_routes.py) as the person using Aira (life.js).
 * Live pages poll with Aira.suite.live and a since= time. DOM is built with h() and textContent only.
 */
"use strict";
(() => {
  const Aira = window.Aira;
  const h = window.h;
  const S = Aira.suite;
  const api = (path, opts) => S.api("social", path, opts);
  const go = (query) => Aira.router.go(["social"], query);

  const REACTIONS = ["❤️", "👍", "😂", "😮", "😢", "🎉"];
  const REACTION_NAMES = { "❤️": "love", "👍": "like", "😂": "laugh", "😮": "wow", "😢": "sad", "🎉": "celebrate" };
  const AUDIENCE_LABELS = { public: "Everyone", friends: "Friends", only_me: "Only me", group: "Group", page: "Page" };
  const REPORT_LABELS = { post: "post", comment: "comment", message: "message", account: "person", story: "story" };

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
    const comments = h("div", { hidden: !ctx.open, class: "suite-body" });
    const loadComments = async () => {
      const data = await api(`/posts/${post.id}/comments`);
      const input = h("input", { type: "text", placeholder: "Write a comment", "aria-label": `Comment on ${post.author.name}'s post`, maxlength: "1000" });
      const send = async () => { if (!input.value.trim()) return; await api(`/posts/${post.id}/comments`, { method: "POST", body: { text: input.value } }); post.comments += 1; await loadComments(); };
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); send(); } });
      comments.replaceChildren(
        data.comments.length ? h("ul", { class: "suite-activity" }, ...data.comments.map((item) => h("li", {}, face(item.author, 28),
          h("div", { style: { flex: "1", minWidth: "0" } }, personLink(item.author), h("span", { text: ` ${item.text}` }), h("div", { class: "muted small", text: when(item.created_at) })),
          item.can_delete ? h("button", { type: "button", class: "btn ghost sm", text: "Delete", "aria-label": "Delete comment", onclick: async () => { await api(`/comments/${item.id}`, { method: "DELETE" }); post.comments = Math.max(0, post.comments - 1); await loadComments(); } }) : null,
          item.mine ? null : h("button", { type: "button", class: "btn ghost sm", text: "Report", "aria-label": "Report comment", onclick: () => report("comment", item.id) })))) : h("p", { class: "muted", text: "No comments yet. Be the first." }),
        h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, input), S.button("Comment", send, { kind: "secondary sm" })));
    };
    if (ctx.open) loadComments();
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
        S.button(post.comments ? count(post.comments, "comment", "comments") : "Comment", async () => { comments.hidden = !comments.hidden; if (!comments.hidden) await loadComments(); }, { kind: "ghost sm" }),
        post.can_share ? S.button("Share", share, { kind: "ghost sm" }) : null,
        post.mine ? S.button("Edit", () => S.edit({ title: "Edit your post", fields: [{ name: "text", label: "Your post", type: "textarea", rows: 5 }], values: post, onSave: async (values) => { const result = await api(`/posts/${post.id}`, { method: "PATCH", body: { text: values.text } }); Object.assign(post, result.post); ctx.redraw(); } }), { kind: "ghost sm" }) : null,
        post.mine ? S.button("Delete", async () => { if (await S.confirmDelete("this post")) { await api(`/posts/${post.id}`, { method: "DELETE" }); ctx.remove(post.id); } }, { kind: "ghost sm" }) : null,
        post.mine ? null : S.button("Report", () => report("post", post.id), { kind: "ghost sm" })),
      comments);
  }

  // A list of posts that redraws in place; ``load`` returns the posts.
  function postList(container, me, load, { open = false, empty } = {}) {
    const state = { posts: [] };
    const list = h("div", { class: "suite-body" });
    const ctx = {
      me, open, young: me.age_group === "child" || me.age_group === "teen",
      redraw: () => list.replaceChildren(...(state.posts.length ? state.posts.map((post) => postNode(post, ctx)) : [empty || Aira.ui.empty("Nothing here yet", "Posts appear here.")])),
      reload: async () => { state.posts = await load(); ctx.redraw(); },
      remove: (id) => { state.posts = state.posts.filter((post) => post.id !== id); ctx.redraw(); },
    };
    container.append(list);
    return { state, ctx };
  }

  function composer(me, { groupId = "", pageId = "", placeholder, onPosted }) {
    const young = me.age_group === "child" || me.age_group === "teen";
    const text = h("textarea", { rows: "3", placeholder: placeholder || `What's on your mind, ${me.display_name}?`, "aria-label": "Your post", maxlength: "2000", style: { fontSize: "16px" } });
    const photos = h("textarea", { rows: "2", placeholder: "Photo links (https://…), one per line, up to 6", "aria-label": "Photo links" });
    const audience = Aira.ui.select(young ? [["friends", "Friends"], ["only_me", "Only me"]] : [["friends", "Friends"], ["public", "Everyone"], ["only_me", "Only me"]], "friends", { "aria-label": "Who can see it" });
    const error = h("p", { class: "form-error", role: "alert", hidden: true });
    const post = async () => {
      error.hidden = true;
      try {
        await api("/posts", { method: "POST", body: { text: text.value, photos: photoList(photos.value), audience: audience.value, group_id: groupId, page_id: pageId } });
        text.value = ""; photos.value = "";
        Aira.toast("Posted.", "success");
        await onPosted();
      } catch (err) { error.textContent = err.message; error.hidden = false; }
    };
    return S.section(groupId ? "Post in the group" : pageId ? "Post as the page" : "Share something", text, Aira.ui.field("Photos (optional)", photos), groupId || pageId ? null : Aira.ui.field("Who can see it", audience, young ? "Young people share with friends only." : ""), error, h("div", { class: "btn-row" }, S.button("Post", post)));
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

  async function storiesStrip(me) {
    const strip = h("div", { role: "list", "aria-label": "Stories", style: { display: "flex", gap: "12px", overflowX: "auto", padding: "4px 2px" } });
    const draw = async () => {
      const data = await api("/stories", { silent: true });
      strip.replaceChildren(
        h("div", { role: "listitem" }, h("button", { type: "button", class: "btn secondary", style: { minHeight: "72px" }, text: "Add a story", onclick: () => S.edit({ title: "Add a story (24 hours)", fields: [{ name: "text", label: "Text", type: "textarea", rows: 3 }, { name: "photo", label: "Photo link (https://…, optional)", type: "url" }, { name: "color", label: "Background colour", type: "color" }], values: { color: "#2563eb" }, onSave: async (values) => { await api("/stories", { method: "POST", body: values }); await draw(); } }) })),
        ...data.people.map((person) => h("div", { role: "listitem" }, h("button", { type: "button", class: "btn ghost", "aria-label": `${person.author.name}'s story${person.unseen ? ` (${person.unseen} new)` : ""}`, style: { display: "grid", justifyItems: "center", gap: "4px", minHeight: "72px", padding: "4px 8px" }, onclick: () => showStories(person, draw) },
          h("span", { style: { borderRadius: "50%", padding: "2px", border: `3px solid ${person.unseen ? "var(--brand)" : "var(--line)"}` } }, face(person.author, 44)),
          h("span", { class: "small", text: person.mine ? "Your story" : person.author.name })))));
    };
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
    const wrap = postList(listHost, me, async () => { const data = await api("/feed"); now = data.now; return data.posts; }, { empty: Aira.ui.empty("Your feed is empty", "Find friends under Friends, follow pages or join groups, then their posts appear here.") });
    body.append(composer(me, { onPosted: () => wrap.ctx.reload() }), listHost);
    await wrap.ctx.reload();
    const older = S.button("Show older posts", async () => {
      const oldest = wrap.state.posts[wrap.state.posts.length - 1];
      if (!oldest) return;
      const data = await api(`/feed?before=${encodeURIComponent(oldest.created_at)}`);
      wrap.state.posts.push(...data.posts.filter((post) => !wrap.state.posts.some((item) => item.id === post.id)));
      wrap.ctx.redraw();
      if (!data.posts.length) older.hidden = true;
    }, { kind: "ghost" });
    body.append(h("div", { class: "btn-row" }, older));
    S.live(async () => {
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
    const narrow = window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
    const chatId = route.query.chat || "";
    const listBox = h("nav", { "aria-label": "Chats", style: { display: "grid", gap: "6px", alignContent: "start" } });
    const drawList = (data) => listBox.replaceChildren(
      S.button("New chat", newChat),
      ...(data.chats.length ? data.chats.map((chat) => h("a", { href: `#/social?tab=chats&chat=${chat.id}`, class: `btn ${chat.id === chatId ? "secondary" : "ghost"}`, "aria-current": chat.id === chatId ? "true" : "false", style: { justifyContent: "flex-start", gap: "10px", minHeight: "56px" } },
        chat.kind === "group" ? h("span", { "aria-hidden": "true", style: { fontSize: "24px" }, text: "👥" }) : face({ avatar: chat.avatar }, 36),
        h("span", { style: { flex: "1", textAlign: "left", minWidth: "0" } }, h("strong", { text: chat.title }), chat.last ? h("span", { class: "muted small", style: { display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, text: `${chat.last.mine ? "You: " : ""}${chat.last.deleted ? "Message deleted" : chat.last.text}` }) : null),
        chat.muted ? h("span", { class: "muted small", text: "Muted" }) : null,
        chat.unread ? h("span", { "aria-label": `${chat.unread} unread`, text: String(chat.unread), style: { background: "var(--brand)", color: "var(--on-brand)", borderRadius: "999px", padding: "1px 8px", fontSize: "13px" } }) : null)) : [h("p", { class: "muted", text: "No chats yet. Start one with a friend." })]));
    let listData = await api("/chats");
    drawList(listData);
    if (!chatId) {
      body.append(S.section("Chats", listBox));
      S.live(async () => { listData = await api("/chats", { silent: true }); drawList(listData); }, 4000);
      return;
    }

    const first = await api(`/chats/${chatId}/messages`);
    const chat = first.chat;
    const state = { messages: first.messages, now: first.now };
    const thread = h("div", { role: "log", "aria-live": "polite", "aria-label": `Messages with ${chat.title}`, style: { display: "grid", gap: "10px", alignContent: "start", maxHeight: "60vh", minHeight: "240px", overflowY: "auto", padding: "6px" } });
    const draw = () => {
      thread.replaceChildren(...(state.messages.length ? state.messages.map((item) => {
        const bubble = h("div", { style: { maxWidth: "min(80%, 520px)", padding: "10px 14px", borderRadius: "18px", background: item.mine ? "var(--brand)" : "var(--surface-2)", color: item.mine ? "var(--on-brand)" : "var(--ink)", fontSize: "16px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } },
          item.deleted ? h("em", { text: "This message was deleted." }) : null,
          item.text ? h("div", { text: item.text }) : null,
          item.photo ? h("a", { href: item.photo, target: "_blank", rel: "noopener noreferrer" }, h("img", { src: item.photo, alt: `Photo from ${item.author.name}`, loading: "lazy", referrerpolicy: "no-referrer", style: { maxWidth: "100%", borderRadius: "10px", marginTop: "6px" } })) : null);
        return h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", justifyContent: item.mine ? "flex-end" : "flex-start" } },
          item.mine ? null : face(item.author, 30),
          h("div", { style: { display: "grid", gap: "2px", justifyItems: item.mine ? "end" : "start" } },
            item.mine || chat.kind === "direct" ? null : h("span", { class: "muted small", text: item.author.name }),
            bubble,
            h("span", { class: "muted small" }, h("span", { text: `${when(item.created_at)}${item.mine && !item.deleted ? (item.seen ? " · Seen" : " · Sent") : ""}` }),
              item.can_delete ? h("button", { type: "button", class: "btn ghost sm", text: "Delete for everyone", onclick: async () => { await api(`/messages/${item.id}`, { method: "DELETE" }); await poll(); } }) : null,
              !item.mine && !item.deleted ? h("button", { type: "button", class: "btn ghost sm", text: "Report", "aria-label": "Report this message", onclick: () => report("message", item.id) }) : null)));
      }) : [Aira.ui.empty("No messages yet", "Say hello!")]));
      thread.scrollTop = thread.scrollHeight;
    };
    const markRead = () => api(`/chats/${chatId}/read`, { method: "POST", body: {}, silent: true }).catch(() => {});
    let ticks = 0;
    const poll = async () => {
      const data = await api(`/chats/${chatId}/messages?since=${encodeURIComponent(state.now)}`, { silent: true });
      state.now = data.now;
      if (data.messages.length) {
        for (const item of data.messages) {
          const index = state.messages.findIndex((m) => m.id === item.id);
          if (item.removed) { if (index >= 0) state.messages.splice(index, 1); continue; }
          if (index >= 0) state.messages[index] = item; else state.messages.push(item);
        }
        markRead();
      }
      // read receipts change without new messages: refresh mine every few rounds
      ticks += 1;
      if (ticks % 3 === 0) {
        const full = await api(`/chats/${chatId}/messages`, { silent: true });
        state.messages = full.messages;
        listData = await api("/chats", { silent: true });
        drawList(listData);
      }
      draw();
    };
    draw();
    markRead();

    const input = h("textarea", { rows: "2", placeholder: chat.blocked ? "You cannot message this person." : `Message ${chat.title}`, "aria-label": "Your message", maxlength: "4000", disabled: chat.blocked, style: { fontSize: "16px" } });
    const send = async () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      await api(`/chats/${chatId}/messages`, { method: "POST", body: { text } });
      input.value = "";
      await poll();
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
    const photoButton = S.button("Send a photo", () => S.edit({ title: "Send a photo", fields: [{ name: "photo", label: "Photo link (https://…)", type: "url", required: true }], onSave: async (values) => { await api(`/chats/${chatId}/messages`, { method: "POST", body: { photo: values.photo } }); await poll(); } }), { kind: "ghost" });
    const sendButton = h("button", { type: "button", class: "btn", disabled: chat.blocked, style: { minHeight: "52px", minWidth: "110px", fontSize: "17px" }, text: "Send", onclick: (event) => Aira.withBusy(event.currentTarget, send) });
    const threadCard = h("section", { class: "card suite-section", style: { gridColumn: narrow ? "auto" : "span 2" } }, head, thread,
      h("div", { style: { display: "flex", gap: "8px", alignItems: "flex-end", flexWrap: "wrap" } }, h("div", { style: { flex: "1 1 240px" } }, input), chat.blocked ? null : photoButton, sendButton));
    body.append(narrow ? threadCard : h("div", { class: "suite-grid", style: { gridTemplateColumns: "minmax(220px, 1fr) 2fr" } }, S.section("Chats", listBox), threadCard));
    S.live(poll, 2000);
  }

  // ---------------------------------------------------------------------------
  // Friends
  // ---------------------------------------------------------------------------
  const relationButtons = (person, relation, after) => {
    if (relation === "friends") return [S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" })];
    if (relation === "request_received") return [S.button("Accept", async () => { await api(`/friends/${person.id}`, { method: "POST", body: {} }); Aira.toast(`You and ${person.name} are friends now.`, "success"); await after(); }, { kind: "sm" }), S.button("Decline", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "request_sent") return [S.button("Cancel request", async () => { await api(`/friends/${person.id}`, { method: "DELETE" }); await after(); }, { kind: "ghost sm" })];
    if (relation === "self") return [];
    return [S.button("Add friend", async () => { await api(`/friends/${person.id}`, { method: "POST", body: {} }); Aira.toast("Friend request sent.", "success"); await after(); }, { kind: "secondary sm" })];
  };
  const personRow = (person, ...buttons) => h("li", { class: "life-person", style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } }, face(person, 40), h("div", { style: { flex: "1", minWidth: "0" } }, personLink(person), h("span", { class: "muted small", text: ` @${person.handle}` }), person.headline ? h("div", { class: "muted small", text: person.headline }) : null), ...buttons.flat().filter(Boolean));

  async function friendsTab(body, me, route) {
    const q = h("input", { type: "search", value: route.query.q || "", placeholder: "Search by name or @name", "aria-label": "Search people, groups and pages" });
    const results = h("div", { class: "suite-body" });
    const runSearch = async () => {
      const query = q.value.trim();
      if (query.length < 2) { results.replaceChildren(); return; }
      const data = await api(`/search?q=${encodeURIComponent(query)}`);
      results.replaceChildren(
        data.people.length ? h("ul", { class: "life-people" }, ...data.people.map((person) => personRow(person, relationButtons(person, person.relation, runSearch)))) : h("p", { class: "muted", text: "Nobody found. Check the spelling, or ask them for their @name." }),
        data.groups.length ? h("p", {}, h("strong", { text: "Groups: " }), ...data.groups.map((g) => h("a", { href: `#/social?tab=groups&group=${g.id}`, text: `${g.name}  ` }))) : null,
        data.pages.length ? h("p", {}, h("strong", { text: "Pages: " }), ...data.pages.map((p) => h("a", { href: `#/social?tab=pages&page=${p.id}`, text: `${p.name}  ` }))) : null);
    };
    q.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); runSearch(); } });
    body.append(S.section("Find people", h("div", { style: { display: "flex", gap: "8px" } }, h("div", { style: { flex: "1" } }, q), S.button("Search", runSearch)), results));
    if (q.value) await runSearch();
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    const draw = async () => {
      const data = await api("/friends");
      const blocked = (await api("/blocks")).blocked;
      panel.replaceChildren(
        data.incoming.length ? S.section(`Friend requests (${data.incoming.length})`, h("ul", { class: "life-people" }, ...data.incoming.map((person) => personRow(person, relationButtons(person, "request_received", draw))))) : null,
        S.section(`Your friends (${data.friends.length})`, data.friends.length ? h("ul", { class: "life-people" }, ...data.friends.map((person) => personRow(person, S.button(`Message ${person.name}`, () => message(person), { kind: "secondary sm" }), S.button("Unfriend", async () => { if (await Aira.confirm({ title: `Unfriend ${person.name}?`, message: "You can send a new request later.", confirmLabel: "Unfriend" })) { await api(`/friends/${person.id}`, { method: "DELETE" }); await draw(); } }, { kind: "ghost sm" })))) : Aira.ui.empty("No friends yet", "Search for people you know above, or accept a request.")),
        data.suggestions.length ? S.section("People you may know", h("ul", { class: "life-people" }, ...data.suggestions.map((person) => personRow(person, relationButtons(person, "none", draw))))) : null,
        data.outgoing.length ? S.section("Requests you sent", h("ul", { class: "life-people" }, ...data.outgoing.map((person) => personRow(person, relationButtons(person, "request_sent", draw))))) : null,
        blocked.length ? S.section("Blocked people", h("ul", { class: "life-people" }, ...blocked.map((person) => personRow(person, S.button("Unblock", async () => { await api(`/people/${person.id}/block`, { method: "DELETE" }); await draw(); }, { kind: "ghost sm" }))))) : null);
    };
    await draw();
  }

  // ---------------------------------------------------------------------------
  // Groups and pages
  // ---------------------------------------------------------------------------
  async function groupsTab(body, me, route) {
    if (route.query.group) {
      const draw = async () => {
        const group = (await api(`/groups/${route.query.group}`)).group;
        body.replaceChildren(
          h("div", { class: "btn-row" }, S.button("All groups", () => go({ tab: "groups" }), { kind: "ghost" })),
          S.section(group.name, h("p", { class: "muted", text: `${group.visibility === "public" ? "Public group" : "Private group"} · ${count(group.members, "member", "members")}` }), group.about ? h("p", { style: { whiteSpace: "pre-wrap" }, text: group.about }) : null,
            h("div", { class: "btn-row" },
              group.status === "none" ? S.button(group.visibility === "public" ? "Join group" : "Ask to join", async () => { await api(`/groups/${group.id}/join`, { method: "POST", body: {} }); await draw(); }) : null,
              group.status === "pending" ? h("span", { class: "muted", text: "Your request is waiting for an admin." }) : null,
              group.status !== "none" ? S.button(group.status === "pending" ? "Cancel request" : "Leave group", async () => { await api(`/groups/${group.id}/leave`, { method: "POST", body: {} }); await draw(); }, { kind: "ghost" }) : null)),
          group.admin && group.requests && group.requests.length ? S.section("Asking to join", h("ul", { class: "life-people" }, ...group.requests.map((person) => personRow(person, S.button("Let in", async () => { await api(`/groups/${group.id}/requests/${person.id}`, { method: "POST", body: { approve: true } }); await draw(); }, { kind: "sm" }), S.button("Decline", async () => { await api(`/groups/${group.id}/requests/${person.id}`, { method: "POST", body: { approve: false } }); await draw(); }, { kind: "ghost sm" }))))) : null,
          group.status === "member" ? composer(me, { groupId: group.id, placeholder: "Write something to the group", onPosted: draw }) : null);
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
    body.append(h("div", { class: "btn-row" }, me.age_group === "child" ? null : S.button("Start a group", () => S.edit({ title: "Start a group", fields: [{ name: "name", label: "Group name", required: true, placeholder: "Neighbourhood walkers" }, { name: "about", label: "What it is about", type: "textarea", rows: 3 }, { name: "visibility", label: "Who can see it", type: "select", options: [["public", "Public: anyone can find and join"], ["private", "Private: people ask to join"]] }], values: { visibility: "public" }, onSave: async (values) => { const created = await api("/groups", { method: "POST", body: values }); go({ tab: "groups", group: created.group.id }); } }))), panel);
    await draw();
  }

  async function pagesTab(body, me, route) {
    if (route.query.page) {
      const draw = async () => {
        const page = (await api(`/pages/${route.query.page}`)).page;
        body.replaceChildren(
          h("div", { class: "btn-row" }, S.button("All pages", () => go({ tab: "pages" }), { kind: "ghost" })),
          S.section(page.name, h("p", { class: "muted", text: `${page.category ? `${page.category} · ` : ""}${count(page.followers, "follower", "followers")}` }), page.about ? h("p", { style: { whiteSpace: "pre-wrap" }, text: page.about }) : null,
            h("div", { class: "btn-row" },
              S.button(page.following ? "Following" : "Follow", async () => { await api(`/pages/${page.id}/follow`, { method: page.following ? "DELETE" : "POST", body: {} }); await draw(); }, { kind: page.following ? "secondary" : "" }),
              /^https:\/\//i.test(page.link || "") ? h("a", { class: "btn ghost", href: page.link, target: "_blank", rel: "noopener noreferrer", text: "Visit their page" }) : null)),
          page.admin ? composer(me, { pageId: page.id, placeholder: `Write as ${page.name}`, onPosted: draw }) : null);
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
    body.append(h("div", { class: "btn-row" }, adult ? S.button("Create a page", () => S.edit({ title: "Create a page", fields: [{ name: "name", label: "Page name", required: true, placeholder: "Asha's Bakery" }, { name: "category", label: "Category", placeholder: "Bakery" }, { name: "about", label: "About", type: "textarea", rows: 3 }, { name: "link", label: "Your website or Aira public page (https://…)", type: "url" }], onSave: async (values) => { const created = await api("/pages", { method: "POST", body: values }); go({ tab: "pages", page: created.page.id }); } })) : null), panel);
    await draw();
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
      body.append(
        S.section("", h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, face(person, 84), h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: person.name }), h("p", { class: "muted", style: { margin: 0 }, text: `@${person.handle} · ${count(person.friends, "friend", "friends")} · ${count(person.followers, "follower", "followers")}` }))),
          person.bio ? h("p", { style: { whiteSpace: "pre-wrap" }, text: person.bio }) : null,
          person.relation !== "friends" && person.visibility === "private" ? h("p", { class: "muted", text: "This profile is private. Become friends to see their posts." }) : null,
          h("div", { class: "btn-row" }, ...relationButtons(person, person.relation, after),
            person.can_message && person.relation !== "friends" ? S.button("Message", () => message(person), { kind: "secondary sm" }) : null,
            person.visibility === "public" && person.relation !== "friends" ? S.button(person.following ? "Unfollow" : "Follow", async () => { await api(`/people/${person.id}/follow`, { method: person.following ? "DELETE" : "POST", body: {} }); after(); }, { kind: "ghost sm" }) : null,
            S.button("Block", async () => { if (await block(person)) go({ tab: "friends" }); }, { kind: "ghost sm" }),
            S.button("Report", () => report("account", person.id), { kind: "ghost sm" }))),
        professional(person));
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
    body.append(
      S.section("", h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, face(me, 84), h("div", { style: { flex: "1", minWidth: "0" } }, h("h2", { class: "suite-h2", style: { margin: 0 }, text: me.display_name }), h("p", { class: "muted", style: { margin: 0 }, text: `@${me.handle} · ${me.visibility === "public" ? "Public profile" : "Private profile"} · ${count(data.person.friends, "friend", "friends")}` }))),
        me.bio ? h("p", { style: { whiteSpace: "pre-wrap" }, text: me.bio }) : h("p", { class: "muted", text: "Add a few words about yourself." }),
        young ? h("p", { class: "muted", text: "Your profile is private: only your friends see it, and only friends can message you." }) : null,
        h("div", { class: "btn-row" }, S.button("Edit profile", edit), S.button(young ? "Work and school" : "Work profile", editWork, { kind: "secondary" }))),
      professional(me));
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
    if (item.kind === "friend_request") return "#/social?tab=friends";
    return item.actor ? `#/social?tab=profile&person=${item.actor.id}` : "#/social";
  };
  async function notificationsTab(body) {
    const data = await api("/notifications");
    body.append(S.section("Notifications", data.notifications.length ? h("ul", { class: "life-lines" }, ...data.notifications.map((item) => h("li", { class: "life-line", style: { fontWeight: item.read ? "400" : "600" } },
      item.actor ? face(item.actor, 36) : h("span", { "aria-hidden": "true", text: "🔔" }),
      h("div", { style: { flex: "1", minWidth: "0" } }, h("a", { href: notificationLink(item), class: "life-line-text", text: item.text }), h("span", { class: "muted life-line-sub", text: `${when(item.created_at)}${item.read ? "" : " · new"}` }))))) : Aira.ui.empty("No notifications", "When someone comments, reacts, mentions you or sends a friend request, it shows here.")));
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

  Aira.views.social = async (main, route) => {
    let info;
    try { info = await api("/me"); } catch (error) { main.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); return; }
    if (!info.account) { setup(main, info); return; }
    const me = info.account;
    const unread = info.badge || { messages: 0, notifications: 0 };
    const tabs = [
      { id: "feed", label: "Feed" },
      { id: "chats", label: unread.messages ? `Chats (${unread.messages})` : "Chats" },
      { id: "friends", label: "Friends" },
      { id: "groups", label: "Groups" },
      { id: "pages", label: "Pages" },
      { id: "profile", label: "Profile" },
      { id: "notifications", label: unread.notifications ? `Notifications (${unread.notifications})` : "Notifications" },
      info.moderator ? { id: "moderation", label: "Moderation" } : null,
    ].filter(Boolean);
    const body = S.page(main, { title: "Social", subtitle: unread.messages || unread.notifications ? `${count(unread.messages, "new message", "new messages")} · ${count(unread.notifications, "notification", "notifications")}` : `Signed in as @${me.handle}` });
    const tab = S.tabs(body, route, "social", tabs, tabs.some((item) => item.id === route.query.tab) ? route.query.tab : "feed");
    const panel = h("div", { class: "suite-body" });
    body.append(panel);
    try {
      if (tab === "chats") await chatsTab(panel, me, route);
      else if (tab === "friends") await friendsTab(panel, me, route);
      else if (tab === "groups") await groupsTab(panel, me, route);
      else if (tab === "pages") await pagesTab(panel, me, route);
      else if (tab === "profile") await profileTab(panel, me, route);
      else if (tab === "notifications") await notificationsTab(panel);
      else if (tab === "moderation") await moderationTab(panel);
      else await feedTab(panel, me, route);
    } catch (error) { panel.replaceChildren(Aira.ui.error(error, () => Aira.router.render())); }
  };
})();
